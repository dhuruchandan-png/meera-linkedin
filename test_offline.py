"""Offline end-to-end test: fake Telegram, fake Gemini, in-memory Redis."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(TELEGRAM_BOT_TOKEN="t", GEMINI_API_KEY="g", SETUP_CODE="abc123")

from lib import bot, gemini, lint, pipeline, store, telegram  # noqa: E402

store.use(store.MemoryStore())
sent = []


def fake_call(method, **p):
    sent.append((method, p))
    return {"message_id": len(sent), "file_path": "voice/file.oga"} if method != "getFile" else {"file_path": "voice/a.oga"}


telegram.call = fake_call
telegram.download = lambda fid: (b"OggS", "voice/a.oga")

GOOD = ("A customer wrote to me last month asking whether she could use our serum with her Vitamin C product. "
        "The honest answer is that it depends on the pH of both products.\n\n" + ("Niacinamide starts converting to niacin below pH 4. " * 12) +
        "\n\nI'm not saying you shouldn't combine the two. What I'm saying is that it depends on numbers.\n\n"
        + ("Our serum is pH 5.5-5.8 and we document it on every batch. " * 10) + "\n\n" +
        ("If you email a brand asking for the pH and they can't answer, that is useful information. " * 6))
calls = []


def fake_generate(model, parts, system=None, schema=None, search=False, **kw):
    calls.append((model, bool(schema), search))
    text = parts if isinstance(parts, str) else json.dumps(parts)
    if schema is pipeline.SCREEN_SCHEMA:
        found = __import__("re").findall(r"--- NOTE (\d+) ---\n(.*?)(?=\n\n--- NOTE|$)", text, __import__("re").S)
        return json.dumps({"results": [{"note_id": int(i), "develop": "venting" not in body,
                                         "criteria": [4], "why": "A concrete customer question that generalises.",
                                         "reject_reason": "", "category": "Formulation Science",
                                         "core_claim": "pH decides compatibility", "strength": 7,
                                         "search_query": "vitamin c niacinamide ph"} for i, body in found]}), []
    if search:
        return ("FOUND: yes\nSOURCE: Journal of Cosmetic Dermatology\nDATE: 2025\nURL: https://example.org/x\n"
                "FINDING: Ascorbic acid degrades above pH 3.5.\nHOW IT SUPPORTS: backs the pH point"), [{"title": "x", "uri": "https://example.org/x"}]
    if schema is pipeline.DRAFT_SCHEMA:
        post = GOOD if "REVISE" in text else GOOD + "\n\nThis will make your skin glow!"
        return json.dumps({"post": post, "flags": ["verify Vitamin C figure"], "kept_verbatim": ["nobody publishes pH"], "self_check_failures": []}), []
    if schema is pipeline.REVIEW_SCHEMA:
        return json.dumps({"hard_failures": [], "soft_failures": [], "unsupported_claims": []}), []
    return "transcribed voice note about pH testing", []


gemini.generate = fake_generate


def texts():
    return [p.get("text", "") for m, p in sent if m == "sendMessage"]


def upd(i, **kw):
    return dict(update_id=i, **kw)


def pm(text, cid=42, **extra):
    return {"message_id": 1, "chat": {"id": cid, "type": "private"}, "text": text, **extra}


# setup: stranger can't claim, owner claims with code
bot.handle_update(upd(1, message=pm("/start wrong")))
assert texts()[-1] == "This is a private bot."
bot.handle_update(upd(2, message=pm("/start abc123")))
assert "owner" in texts()[-1] and bot.owner() == 42
bot.handle_update(upd(3, message=pm("hello", cid=99)))
assert texts()[-1] == "This is a private bot."

# dedupe
bot.handle_update(upd(3, message=pm("hello", cid=99)))
assert len([t for t in texts() if t == "This is a private bot."]) == 2

# capture a note -> screened, queued
bot.handle_update(upd(4, message=pm("customer asked if our serum works with her vit c. nobody publishes pH. ours is 5.5-5.8")))
assert "note #1" in texts()[-1] and "Worth developing" in texts()[-1], texts()[-1]
assert store.queue_ids() == [1]

# voice note
bot.handle_update(upd(5, message={"message_id": 2, "chat": {"id": 42, "type": "private"}, "voice": {"file_id": "f", "mime_type": "audio/ogg"}}))
assert any("Transcript" in t for t in texts()) and store.get_note(2)["source"] == "voice note"

# /draft -> 3 messages, lint catches "glow"/"!" and triggers one revision
sent.clear()
bot.handle_update(upd(6, message=pm("/draft 1")))
t = texts()
assert t[0].startswith("Drafting note #1"), t[0]
head, body, tail = t[1], t[2], t[3]
assert "SOURCE NOTE: note #1" in head and "CATEGORY: Formulation Science" in head and "WHY THIS NOTE" in head
assert "glow" not in body and "!" not in body, "revision should have removed lint failures"
assert "CURRENT ANGLE: Journal of Cosmetic Dermatology, 2025, https://example.org/x" in tail
assert "kept verbatim" in tail
d = store.get_draft(1)
assert d["result"]["revised"] is True and store.get_note(1)["status"] == "drafted" and 1 not in store.queue_ids()
kb = [p for m, p in sent if m == "sendMessage"][-1]["reply_markup"]["inline_keyboard"][0]
assert [b["callback_data"] for b in kb] == ["k:1", "r:1", "x:1"]

# reply to draft with feedback -> redo with same angle
msg_id = d["messages"][1]
sent.clear()
bot.handle_update(upd(7, message=pm("shorter please, and mention Chennai", reply_to_message={"message_id": msg_id})))
assert texts()[0].startswith("Drafting note #1") and store.get_draft(2)["note_id"] == 1

# buttons
cq = {"id": "c", "data": "k:2", "message": {"chat": {"id": 42}, "message_id": 9}}
bot.handle_update(upd(8, callback_query=cq))
assert store.get_draft(2)["status"] == "kept"
bot.handle_update(upd(9, callback_query=dict(cq, data="x:2", id="d")))
assert store.get_draft(2)["status"] == "rejected"

# import
sent.clear()
bot.handle_update(upd(10, message=pm("/import first old note about CoA mid-batch sampling results\n---\nsecond old note about humid city returns\n---\njust venting about a supplier today")))
assert "Imported 3 notes" in texts()[0], texts()
assert store.get_note(5)["status"] == "rejected" and store.get_note(3)["status"] == "queued"

# channel linking + channel note
bot.handle_update(upd(11, channel_post={"chat": {"id": -100, "title": "notes"}, "text": "/connect"}))
assert store.cfg_get("channel") == "-100"
bot.handle_update(upd(12, channel_post={"chat": {"id": -100, "title": "notes"}, "text": "saw a CLINICALLY TESTED banner again at the Mumbai fair"}))
assert store.get_note(6)["source"] == "channel"
bot.handle_update(upd(13, channel_post={"chat": {"id": -555, "title": "other"}, "text": "ignored post"}))
assert store.get_note(7) is None

# listings + scheduled
assert "notes waiting" in bot.queue_text()
assert bot.scheduled().startswith("drafted note")

# lint unit checks
hard, _ = lint.check("Hi friends! Let's glow — #skincare\n- bullet\nWe optimized the color.")
joined = " | ".join(hard)
for needle in ["exclamation", "dashes", "hashtags", "bullet", "wellness", "American", "opener"]:
    assert needle in joined, (needle, joined)
hard, _ = lint.check('"Glow" is not a formulation claim. We stabilised the serum at pH 5.5-5.8 and it is sized for travel.')
assert hard == [], hard

print("ALL OFFLINE TESTS PASSED;", len(calls), "fake Gemini calls")
