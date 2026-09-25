"""Telegram bot logic. No database: every bot message carries the note its buttons act on.

Flow: a note arrives (her channel CHAT_ID, or a private message from TELEGRAM_USER_ID)
-> screened -> if worth developing, drafted straight away -> draft sent to her private chat.
Drafting takes 1-3 minutes, so it runs in a separate call to /api/work and the
Telegram webhook can answer immediately.
"""
import re
import socket
import traceback
import urllib.error

from . import config, gemini, net, pipeline
from . import telegram as tg

HELP = (
    "I turn your notes into LinkedIn drafts for you to review. I never post anything.\n\n"
    "Post a note in your notes channel, or send it to me here (text or a voice note). If it's worth "
    "developing I draft it straight away and send it to you here. If not, I tell you why, and you can "
    "ask for a draft anyway.\n\n"
    "On each draft:\n"
    "Redo - a different take on the same note\n"
    "Keep - marks it as the one you're going with\n"
    "Reply to the draft with comments - I revise it\n\n"
    "/import - paste old notes after /import, separated by lines of ---, or send a .txt file with the "
    "caption /import. I screen them all and send you the ones worth drafting."
)

BASE_URL = None  # set by the webhook handler from the incoming Host header


def out_chat():
    """Where drafts and replies go: her private chat with the bot."""
    return config.owner_id() or config.chat_id()


# ---- entry point ---------------------------------------------------------

def handle_update(u):
    if "callback_query" in u:
        return on_callback(u["callback_query"])
    msg = u.get("message") or u.get("channel_post")
    if msg:
        return on_message(msg)


def _allowed(msg):
    chat = msg.get("chat", {})
    if config.chat_id() and chat.get("id") == config.chat_id():
        return True
    return chat.get("type") == "private" and (msg.get("from") or {}).get("id") == config.owner_id()


def on_message(msg):
    chat = msg.get("chat", {})
    if not _allowed(msg):
        if chat.get("type") == "private":
            uid = (msg.get("from") or {}).get("id")
            tg.send(chat["id"], f"This is a private bot.\n\nYour Telegram user ID is {uid}.")
        return
    text = (msg.get("text") or msg.get("caption") or "").strip()
    cmd, arg = _command(text)
    if cmd in ("/start", "/help"):
        return tg.send(chat["id"], HELP)
    if cmd == "/import":
        return start_import(msg, arg)
    if cmd:
        return tg.send(chat["id"], HELP) if chat.get("type") == "private" else None

    # A reply to one of my messages is feedback.
    rt = msg.get("reply_to_message") or {}
    if text and (rt.get("from") or {}).get("is_bot") and rt.get("text"):
        return feedback(rt["text"], text)

    note = _extract_note(msg)
    if note:
        capture(note)


def _command(text):
    m = re.match(r"^(/[a-z_]+)(?:@\w+)?\s*(.*)$", text, re.S | re.I)
    return (m.group(1).lower(), m.group(2).strip()) if m else (None, text)


def _extract_note(msg):
    source = "channel" if msg.get("chat", {}).get("type") == "channel" else "telegram"
    media = msg.get("voice") or msg.get("audio")
    if media:
        try:
            text = transcribe(media)
        except Exception as e:
            tg.send(out_chat(), f"I couldn't transcribe a voice note ({e}). Could you send it as text?")
            return None
        return {"text": text, "source": f"{source} voice note", "date": pipeline.today()}
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if msg.get("forward_origin") or msg.get("forward_date"):
        source = "forwarded"
    return {"text": text, "source": source, "date": pipeline.today()} if text else None


def transcribe(media):
    data, path = tg.download(media["file_id"])
    mime = media.get("mime_type") or ("audio/ogg" if path.endswith((".oga", ".ogg")) else "audio/mpeg")
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


# ---- capture -------------------------------------------------------------

def capture(note):
    try:
        res = pipeline.screen([{"id": 1, "text": note["text"]}]).get(1)
    except gemini.GeminiError as e:
        return tg.send(out_chat(), f"I couldn't screen this note yet ({e}).\n\n" + pipeline.note_block(note),
                       buttons=[[("Try again", "draft")]])
    if not res:
        return tg.send(out_chat(), "I couldn't screen this note.\n\n" + pipeline.note_block(note),
                       buttons=[[("Draft anyway", "draft")]])
    if res.get("develop"):
        tg.send(out_chat(), f"Worth developing ({res.get('category')}, strength {res.get('strength')}/10). "
                            f"{res.get('why')}\nDrafting it now. This takes one to three minutes.\n\n"
                            + pipeline.note_block(note))
        dispatch({"kind": "draft", "note": note, "screen": res})
    else:
        tg.send(out_chat(), f"Not drafting this one: {res.get('reject_reason') or res.get('why')}\n\n"
                            + pipeline.note_block(note), buttons=[[("Draft anyway", "draft")]])


# ---- background jobs -----------------------------------------------------

def dispatch(job):
    """Hand a slow job to /api/work and return without waiting. Runs inline if that isn't possible."""
    if not BASE_URL:
        return run_job(job)
    try:
        net.request("POST", f"{BASE_URL}/api/work", job,
                    headers={"X-Worker-Secret": config.worker_secret()}, timeout=2.5)
    except (TimeoutError, socket.timeout):
        pass  # expected: the worker keeps going after we stop waiting
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return
        traceback.print_exc()
        run_job(job)
    except net.HTTPError:
        traceback.print_exc()
        run_job(job)


def run_job(job):
    kind = job.get("kind")
    if kind == "draft":
        return draft_note(job["note"], job.get("screen"), feedback=job.get("feedback"))
    if kind == "revise":
        return revise_draft(job["previous"], job["feedback"])
    if kind == "import":
        return run_import(job["texts"])


def draft_note(note, scr=None, feedback=None):
    try:
        if not scr or not scr.get("category"):
            scr = pipeline.screen([{"id": 1, "text": note["text"]}]).get(1) or {}
        result = pipeline.run(note, scr, feedback=feedback)
        send_draft(note, result)
    except Exception as e:
        traceback.print_exc()
        tg.send(out_chat(), f"Drafting failed: {e}\n\n" + pipeline.note_block(note), buttons=[[("Try again", "draft")]])


def revise_draft(previous, comments):
    note = {"text": "(The original note is not available. The previous version's facts are the only facts you may use.)",
            "source": "revision", "date": pipeline.today()}
    scr = {"category": "same as the previous version", "core_claim": "same as the previous version",
           "why": "revision of an earlier draft"}
    fb = "Meera's comments on this draft (follow them; they override the guide where they conflict):\n" + comments
    try:
        result = pipeline.run(note, scr, angle={"keep": True}, feedback=fb, previous=previous)
        result["why"] = "Revision requested: " + _short(comments, 120)
        post = result["post"]
        meta = ("REVISED DRAFT (next message)\n"
                "FLAGS: " + ("\n- " + "\n- ".join(result["flags"]) if result["flags"] else "none") + "\n"
                "SELF-CHECK: " + ("\n- " + "\n- ".join(result["self_check"]) if result["self_check"] else "all pass")
                + "\n\nReply to the draft with more comments if needed.")
        m = tg.send(out_chat(), meta, buttons=[[("Keep", "keep")]])
        tg.send(out_chat(), post, reply_to=m["message_id"] if m else None)
    except Exception as e:
        traceback.print_exc()
        tg.send(out_chat(), f"Revising failed: {e}. Reply to the draft again to retry.")


def send_draft(note, result):
    meta, post = pipeline.render(note, result)
    m = tg.send(out_chat(), meta, buttons=[[("Redo", "redo"), ("Keep", "keep")]])
    tg.send(out_chat(), post, reply_to=m["message_id"] if m else None)


def feedback(replied_text, comments):
    note = pipeline.parse_note(replied_text)
    if note:
        # Reply to a message that carries the note: redraft the note with her comments.
        tg.send(out_chat(), "Redrafting with your comments. This takes one to three minutes.")
        return dispatch({"kind": "draft", "note": note, "feedback":
                         "Meera's comments (follow them; they override the guide where they conflict):\n" + comments})
    # Reply to the draft itself: revise that text.
    tg.send(out_chat(), "Revising the draft with your comments. This takes about a minute.")
    dispatch({"kind": "revise", "previous": replied_text, "feedback": comments})


# ---- buttons -------------------------------------------------------------

def on_callback(cq):
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    uid = (cq.get("from") or {}).get("id")
    if uid != config.owner_id() and chat != config.chat_id():
        return tg.answer_callback(cq["id"], "Private bot.")
    action = cq.get("data", "")
    if action == "noop":
        return tg.answer_callback(cq["id"])
    if action == "keep":
        tg.answer_callback(cq["id"], "Kept")
        return tg.set_buttons(chat, mid, [[("Kept - post it yourself when ready", "noop")], [("Redo", "redo")]])
    if action in ("redo", "draft"):
        note = pipeline.parse_note(msg.get("text", ""))
        if not note:
            return tg.answer_callback(cq["id"], "I can't find the note in this message.")
        tg.answer_callback(cq["id"], "On it")
        tg.set_buttons(chat, mid, [[("Drafting - new message coming", "noop")]])
        fb = None
        if action == "redo":
            fb = ("Meera pressed Redo: she did not want the previous version. Write a clearly different take on the "
                  "same note: a different hook pattern and a different structure for the mechanism.")
        return dispatch({"kind": "draft", "note": note, "feedback": fb})
    tg.answer_callback(cq["id"])


# ---- import --------------------------------------------------------------

def start_import(msg, arg):
    text = arg
    doc = msg.get("document") or (msg.get("reply_to_message") or {}).get("document")
    if doc:
        data, _ = tg.download(doc["file_id"])
        text = data.decode("utf-8", "replace")
    if re.search(r"^\s*-{3,}\s*$", text or "", re.M):
        chunks = re.split(r"^\s*-{3,}\s*$", text, flags=re.M)
    else:
        chunks = re.split(r"\n\s*\n", text or "")
    chunks = [c.strip() for c in chunks if len(c.strip()) > 15]
    if not chunks:
        return tg.send(out_chat(), "Send /import followed by your notes, separated by lines containing only ---. "
                                   "Or send a .txt file with the caption /import.")
    tg.send(out_chat(), f"Got {len(chunks)} notes. Screening them now.")
    dispatch({"kind": "import", "texts": chunks})


def run_import(texts):
    results = {}
    for i in range(0, len(texts), 12):
        batch = [{"id": j + 1, "text": texts[j]} for j in range(i, min(i + 12, len(texts)))]
        try:
            results.update(pipeline.screen(batch))
        except gemini.GeminiError as e:
            tg.send(out_chat(), f"Screening stopped at note {i + 1} ({e}). Send the rest with /import again.")
            break
    keep = sorted(((r.get("strength", 0), nid) for nid, r in results.items() if r.get("develop")), reverse=True)
    rejected = [(nid, r) for nid, r in results.items() if not r.get("develop")]
    tg.send(out_chat(), f"Screened {len(results)} notes: {len(keep)} worth developing, {len(rejected)} not.\n"
                        "Worth developing, strongest first. Tap Draft on the ones you want.")
    for strength, nid in keep:
        r = results[nid]
        note = {"text": texts[nid - 1], "source": "import", "date": pipeline.today()}
        tg.send(out_chat(), f"{r.get('category')}, strength {strength}/10. {r.get('why')}\n\n" + pipeline.note_block(note),
                buttons=[[("Draft", "draft")]])
    if rejected:
        lines = [f"- \"{_short(texts[nid - 1], 60)}\": {r.get('reject_reason') or r.get('why')}" for nid, r in rejected]
        tg.send(out_chat(), "Not worth drafting:\n" + "\n".join(lines))


def _short(t, n=70):
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 3] + "..."
