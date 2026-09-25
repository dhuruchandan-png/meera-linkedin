"""Telegram update handling: capture notes, screen them, draft on demand or on schedule."""
import re
import time
import traceback

from . import config, gemini, pipeline, store
from . import telegram as tg

HELP = (
    "I turn your notes into LinkedIn drafts for you to review. I never post anything.\n\n"
    "How to use me:\n"
    "Send me a note (text or a voice note), or post it in your linked channel. I save it and tell you if it's "
    "worth developing.\n"
    "Every Monday, Wednesday and Friday morning I draft the strongest note in the queue.\n"
    "Reply to any draft with your comments and I'll revise it.\n\n"
    "Commands\n"
    "/draft - draft the best queued note now (or /draft 12 for note #12)\n"
    "/queue - notes waiting to be drafted\n"
    "/notes - recent notes and their status\n"
    "/import - bulk-add old notes: /import then the notes separated by lines of ---, or send a .txt file "
    "with the caption /import\n"
    "/skip 12 - take note #12 out of the queue\n"
    "/channel - how to link your Telegram notes channel\n"
    "/help - this message"
)


def linked_channel():
    v = config.get("CHANNEL_ID") or store.cfg_get("channel")
    return int(v) if v else None


def owner():
    v = config.get("OWNER_CHAT_ID") or store.cfg_get("owner")
    return int(v) if v else None


# ---- entry point ---------------------------------------------------------

def handle_update(u):
    uid = u.get("update_id")
    if uid is not None and not store.first_time(uid):
        return
    if "callback_query" in u:
        return on_callback(u["callback_query"])
    if "channel_post" in u:
        return on_channel_post(u["channel_post"])
    if "message" in u:
        return on_message(u["message"])


# ---- private chat --------------------------------------------------------

def on_message(msg):
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return
    cid = chat["id"]
    text = (msg.get("text") or msg.get("caption") or "").strip()
    me = owner()

    if me is None:
        code = config.get("SETUP_CODE")
        parts = text.split()
        if parts and parts[0].startswith("/start") and code and len(parts) > 1 and parts[1] == code:
            store.cfg_set("owner", cid)
            tg.send(cid, "You're set up as the owner of this bot.\n\n" + HELP)
        else:
            tg.send(cid, "This is a private bot.")
        return
    if cid != me:
        tg.send(cid, "This is a private bot.")
        return

    cmd, arg = _command(text)
    if cmd in ("/start", "/help"):
        return tg.send(cid, HELP)
    if cmd == "/queue":
        return tg.send(cid, queue_text())
    if cmd == "/notes":
        return tg.send(cid, notes_text())
    if cmd == "/channel":
        return tg.send(cid, channel_text())
    if cmd == "/skip":
        return skip(cid, arg)
    if cmd == "/draft":
        return draft_command(cid, arg)
    if cmd == "/import":
        return import_notes(cid, msg, arg)
    if cmd:
        return tg.send(cid, "I don't know that command.\n\n" + HELP)

    # A reply to one of my draft messages is feedback on that draft.
    rt = msg.get("reply_to_message")
    if rt and text:
        did = store.draft_for_message(rt.get("message_id"))
        if did:
            return redo(cid, did, feedback=text)

    note_text, source = _extract_note(cid, msg)
    if note_text:
        capture(note_text, source, reply_chat=cid, reply_to=msg.get("message_id"))


def _command(text):
    m = re.match(r"^(/[a-z_]+)(?:@\w+)?\s*(.*)$", text, re.S | re.I)
    return (m.group(1).lower(), m.group(2).strip()) if m else (None, text)


def _extract_note(cid, msg):
    """Returns (text, source) for text, captions, voice notes and audio."""
    media = msg.get("voice") or msg.get("audio")
    if media:
        tg.typing(cid)
        try:
            text = transcribe(media)
        except Exception as e:
            tg.send(cid, f"I couldn't transcribe that voice note ({e}). Could you send it as text?")
            return None, None
        tg.send(cid, f"Transcript:\n\n{text}")
        return text, "voice note"
    doc = msg.get("document")
    if doc and (doc.get("file_name", "").lower().endswith(".txt")):
        return None, None  # .txt files are only read via /import
    text = (msg.get("text") or msg.get("caption") or "").strip()
    src = "forwarded" if (msg.get("forward_origin") or msg.get("forward_date")) else "telegram"
    return (text, src) if text else (None, None)


def transcribe(media):
    data, path = tg.download(media["file_id"])
    mime = media.get("mime_type") or ("audio/ogg" if path.endswith(".oga") or path.endswith(".ogg") else "audio/mpeg")
    text, _ = gemini.generate(
        config.screen_model(),
        [gemini.audio_part(data, mime), {"text": (
            "Transcribe this voice note from Meera Pillai, an Indian skincare founder, verbatim in British "
            "English. Keep her words and phrasing; remove only filler sounds (um, uh). Skincare and chemistry "
            "terms (niacinamide, CoA, INCI, pH, stratum corneum, CDSCO) should be spelled correctly. "
            "Return the transcript only.")}],
        temperature=0.0,
    )
    return text.strip()


# ---- channel -------------------------------------------------------------

def on_channel_post(post):
    chat_id = post["chat"]["id"]
    title = post["chat"].get("title", "your channel")
    text = (post.get("text") or post.get("caption") or "").strip()
    me = owner()
    linked = linked_channel()

    if text.lower().startswith("/connect"):
        if not me:
            return
        if linked and linked != chat_id:
            tg.send(me, f"Someone posted /connect in \"{title}\", but a different channel is already linked. Ignored.")
            return
        store.cfg_set("channel", chat_id)
        tg.send(me, f"Linked \"{title}\". Anything you post there now comes to me as a note.")
        return
    if linked != chat_id or not me:
        return

    media = post.get("voice") or post.get("audio")
    source = "channel"
    if media:
        try:
            text = transcribe(media)
            source = "channel voice note"
        except Exception as e:
            tg.send(me, f"I couldn't transcribe a voice note from your channel ({e}).")
            return
    if text:
        capture(text, source, reply_chat=me, reply_to=None, quote=True)


# ---- capture + screening -------------------------------------------------

def capture(text, source, reply_chat, reply_to=None, quote=False):
    note = store.new_note(text, source)
    try:
        res = pipeline.screen([note]).get(note["id"])
    except gemini.GeminiError as e:
        tg.send(reply_chat, f"Saved as note #{note['id']}. I couldn't screen it yet ({e}); I'll retry before the next scheduled draft.", reply_to=reply_to)
        return note
    apply_screen(note, res)
    prefix = f"\"{_short(text, 80)}\"\n\n" if quote else ""
    tg.send(reply_chat, prefix + screen_text(note), buttons=screen_buttons(note), reply_to=reply_to)
    return note


def apply_screen(note, res):
    if not res:
        return
    note["screen"] = res
    note["status"] = "queued" if res.get("develop") else "rejected"
    store.save_note(note)
    if res.get("develop"):
        store.queue_add(note["id"], res.get("strength", 5))
    else:
        store.queue_remove(note["id"])


def screen_text(note):
    s = note.get("screen") or {}
    if note["status"] == "queued":
        return (f"Saved as note #{note['id']}. Worth developing ({s.get('category')}, strength {s.get('strength')}/10).\n"
                f"{s.get('why')}\n\nIt's in the queue ({store.queue_size()} waiting).")
    if note["status"] == "rejected":
        return (f"Saved as note #{note['id']}. Not drafting it: {s.get('reject_reason') or s.get('why')}\n\n"
                "It stays saved. Tap below if you want a draft anyway.")
    return f"Saved as note #{note['id']}."


def screen_buttons(note):
    if note["status"] == "queued":
        return [[("Draft this now", f"dn:{note['id']}")]]
    if note["status"] == "rejected":
        return [[("Draft anyway", f"dn:{note['id']}")]]
    return None


def screen_pending(budget=90):
    """Screen notes saved without a screening result (imports, earlier failures)."""
    t0 = time.time()
    pending = []
    for nid in store.recent_note_ids():
        n = store.get_note(nid)
        if n and n["status"] == "new":
            pending.append(n)
    done = 0
    for i in range(0, len(pending), 12):
        if time.time() - t0 > budget:
            break
        batch = pending[i: i + 12]
        res = pipeline.screen(batch)
        for n in batch:
            if n["id"] in res:
                apply_screen(n, res[n["id"]])
                done += 1
    return done, len(pending)


def import_notes(cid, msg, arg):
    text = arg
    doc = msg.get("document") or (msg.get("reply_to_message") or {}).get("document")
    if doc:
        data, _ = tg.download(doc["file_id"])
        text = data.decode("utf-8", "replace")
    if not text.strip():
        return tg.send(cid, "Send /import followed by your notes, separated by lines containing only ---. "
                            "Or send a .txt file with the caption /import.")
    if re.search(r"^\s*-{3,}\s*$", text, re.M):
        chunks = re.split(r"^\s*-{3,}\s*$", text, flags=re.M)
    else:
        chunks = re.split(r"\n\s*\n", text)
    chunks = [c.strip() for c in chunks if len(c.strip()) > 15]
    if not chunks:
        return tg.send(cid, "I didn't find any notes in that.")
    for c in chunks:
        store.new_note(c, "import")
    tg.send(cid, f"Imported {len(chunks)} notes. Screening them now.")
    tg.typing(cid)
    done, total = screen_pending(budget=200)
    msg_ = f"Screened {done} of {total}. {store.queue_size()} notes are in the queue.\n\n" + queue_text(limit=10)
    if done < total:
        msg_ += "\n\nThe rest will be screened before the next scheduled draft, or when you send /draft."
    tg.send(cid, msg_)


# ---- drafting ------------------------------------------------------------

def draft_command(cid, arg):
    if arg:
        m = re.search(r"\d+", arg)
        if not m:
            return tg.send(cid, "Use /draft or /draft <note number>.")
        return draft_note(cid, int(m.group()))
    ids = store.queue_ids(1)
    if not ids:
        done, _ = screen_pending(budget=60)
        ids = store.queue_ids(1)
    if not ids:
        return tg.send(cid, "The queue is empty. Send me a note first, or use /draft <note number> for a specific one.")
    draft_note(cid, ids[0])


def draft_note(cid, nid, feedback=None, previous=None, angle=None):
    note = store.get_note(nid)
    if not note:
        return tg.send(cid, f"There's no note #{nid}.")
    if not store.acquire(f"draft:{nid}"):
        return tg.send(cid, f"Note #{nid} is already being drafted.")
    try:
        tg.send(cid, f"Drafting note #{nid}. This usually takes one to three minutes.")
        tg.typing(cid)
        scr = note.get("screen")
        if not scr or not scr.get("category"):
            scr = pipeline.screen([note]).get(nid) or {}
            note["screen"] = scr
        result = pipeline.run(note, scr, angle=angle, feedback=feedback, previous=previous)
        d = store.new_draft({"note_id": nid, "result": result, "status": "pending"})
        head, body, tail = pipeline.render(note, result, d["id"])
        m1 = tg.send(cid, head)
        m2 = tg.send(cid, body)
        m3 = tg.send(cid, tail, buttons=draft_buttons(d["id"]))
        for m in (m1, m2, m3):
            if m:
                store.link_message(m["message_id"], d["id"])
        d["messages"] = [m["message_id"] for m in (m1, m2, m3) if m]
        store.save_draft(d)
        note["status"] = "drafted"
        note.setdefault("drafts", []).append(d["id"])
        store.save_note(note)
        store.queue_remove(nid)
    except Exception as e:
        traceback.print_exc()
        tg.send(cid, f"Drafting note #{nid} failed: {e}\nIt stays in the queue. Try /draft {nid} again in a few minutes.")
    finally:
        store.release(f"draft:{nid}")


def draft_buttons(did):
    return [[("Keep", f"k:{did}"), ("Redo", f"r:{did}"), ("Reject", f"x:{did}")]]


def redo(cid, did, feedback=None):
    d = store.get_draft(did)
    if not d:
        return tg.send(cid, "I can't find that draft any more.")
    prev = d["result"]["post"]
    if feedback:
        fb = "Meera's comments on this draft (follow them; they override the guide where they conflict):\n" + feedback
        # Keep the same angle when she is editing wording; she can press Redo for a fresh one.
        return draft_note(cid, d["note_id"], feedback=fb, previous=prev, angle=d["result"].get("angle"))
    fb = ("Meera pressed Redo: she did not want this version. Write a clearly different take on the same note: "
          "a different hook pattern and a different structure for the mechanism. Do not reuse its sentences.")
    return draft_note(cid, d["note_id"], feedback=fb, previous=prev)


# ---- buttons -------------------------------------------------------------

def on_callback(cq):
    cid = cq.get("message", {}).get("chat", {}).get("id")
    mid = cq.get("message", {}).get("message_id")
    data = cq.get("data", "")
    if cid != owner():
        return tg.answer_callback(cq["id"], "Private bot.")
    kind, _, val = data.partition(":")
    if kind == "noop":
        return tg.answer_callback(cq["id"])
    try:
        n = int(val)
    except ValueError:
        return tg.answer_callback(cq["id"])

    if kind == "dn":
        tg.answer_callback(cq["id"], "Drafting")
        tg.set_buttons(cid, mid, [[("Drafting...", "noop")]])
        return draft_note(cid, n)
    if kind in ("k", "x", "r"):
        d = store.get_draft(n)
        if not d:
            return tg.answer_callback(cq["id"], "Draft not found")
        if kind == "k":
            d["status"] = "kept"
            store.save_draft(d)
            tg.answer_callback(cq["id"], "Kept")
            tg.set_buttons(cid, mid, [[("Kept - post it yourself when ready", "noop")], [("Redo anyway", f"r:{n}")]])
            return
        if kind == "x":
            d["status"] = "rejected"
            store.save_draft(d)
            tg.answer_callback(cq["id"], "Rejected")
            tg.set_buttons(cid, mid, [[("Rejected", "noop")], [("Try again", f"r:{n}")]])
            return
        d["status"] = "redone"
        store.save_draft(d)
        tg.answer_callback(cq["id"], "Redoing")
        tg.set_buttons(cid, mid, [[("Redone - see below", "noop")]])
        return redo(cid, n)
    tg.answer_callback(cq["id"])


# ---- listings ------------------------------------------------------------

def _short(t, n=70):
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 3] + "..."


def queue_text(limit=15):
    ids = store.queue_ids(limit)
    if not ids:
        return "The queue is empty."
    lines = [f"{store.queue_size()} notes waiting. Strongest first:"]
    for nid in ids:
        n = store.get_note(nid)
        if n:
            s = n.get("screen") or {}
            lines.append(f"#{nid} [{s.get('strength', '?')}/10, {s.get('category', '?')}] {_short(n['text'])}")
    lines.append("\n/draft drafts the top one. /draft <number> drafts a specific one.")
    return "\n".join(lines)


def notes_text(limit=12):
    ids = store.recent_note_ids(limit)
    if not ids:
        return "No notes yet."
    lines = []
    for nid in ids:
        n = store.get_note(nid)
        if n:
            lines.append(f"#{nid} {n['status']}: {_short(n['text'], 60)}")
    return "Recent notes:\n" + "\n".join(lines)


def channel_text():
    linked = linked_channel()
    status = f"A channel is linked (id {linked})." if linked else "No channel is linked yet."
    return (status + "\n\nTo link your notes channel: open the channel, add this bot as an administrator "
            "(it only needs to read posts), then post /connect in the channel. After that, everything you post "
            "there reaches me as a note. You can also just message me directly.")


def skip(cid, arg):
    m = re.search(r"\d+", arg or "")
    if not m:
        return tg.send(cid, "Use /skip <note number>.")
    nid = int(m.group())
    n = store.get_note(nid)
    if not n:
        return tg.send(cid, f"There's no note #{nid}.")
    store.queue_remove(nid)
    n["status"] = "skipped"
    store.save_note(n)
    tg.send(cid, f"Took note #{nid} out of the queue.")


# ---- scheduled run -------------------------------------------------------

def scheduled():
    me = owner()
    if not me:
        return "no owner yet"
    try:
        screen_pending(budget=60)
    except gemini.GeminiError:
        pass
    ids = store.queue_ids(1)
    if not ids:
        tg.send(me, "Scheduled draft: the queue is empty, so there's nothing to draft today. Send me a note whenever something comes up.")
        return "queue empty"
    tg.send(me, f"Scheduled draft ({store.queue_size()} notes in the queue).")
    draft_note(me, ids[0])
    return f"drafted note {ids[0]}"
