"""Offline end-to-end test with fake Telegram and fake Gemini. No network, no database."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(TELEGRAM_BOT_TOKEN="t", GEMINI_API_KEY="g", TELEGRAM_USER_ID="42", CHAT_ID="-1004429285345")

from lib import bot, config, gemini, lint, pipeline, telegram  # noqa: E402

sent = []


def fake_call(method, **p):
    sent.append((method, p))
    return {"message_id": len(sent)}


telegram.call = fake_call
telegram.download = lambda fid: (b"OggS", "voice/a.oga")

GOOD = ("A customer wrote to me last month asking whether she could use our serum with her Vitamin C product. "
        "The honest answer is that it depends on the pH of both products.\n\n" + ("Niacinamide starts converting to niacin below pH 4. " * 12) +
        "\n\nI'm not saying you shouldn't combine the two. What I'm saying is that it depends on numbers.\n\n"
        + ("Our serum is pH 5.5-5.8 and we document it on every batch. " * 10) + "\n\n" +
        ("If you email a brand asking for the pH and they can't answer, that is useful information. " * 6)).strip()
prompts = []


def fake_generate(model, parts, system=None, schema=None, search=False, **kw):
    text = parts if isinstance(parts, str) else json.dumps(parts)
    prompts.append(text)
    if schema is pipeline.SCREEN_SCHEMA:
        found = re.findall(r"--- NOTE (\d+) ---\n(.*?)(?=\n\n--- NOTE|$)", text, re.S)
        return json.dumps({"results": [{"note_id": int(i), "develop": "venting" not in body, "criteria": [4],
                                         "why": "A concrete customer question that generalises.",
                                         "reject_reason": "just venting", "category": "Formulation Science",
                                         "core_claim": "pH decides compatibility", "strength": 7 if "pH" in body else 5,
                                         "search_query": "q"} for i, body in found]}), []
    if search:
        return ("FOUND: yes\nSOURCE: Journal of Cosmetic Dermatology\nDATE: 2025\nURL: https://example.org/x\n"
                "FINDING: Ascorbic acid degrades above pH 3.5.\nHOW IT SUPPORTS: backs the pH point"), []
    if schema is pipeline.DRAFT_SCHEMA:
        post = GOOD if "REVISE" in text else GOOD + "\n\nThis will make your skin glow!"
        return json.dumps({"post": post, "flags": ["verify Vitamin C figure"], "kept_verbatim": ["nobody publishes pH"],
                           "self_check_failures": []}), []
    if schema is pipeline.REVIEW_SCHEMA:
        return json.dumps({"hard_failures": [], "soft_failures": [], "unsupported_claims": []}), []
    return "transcribed voice note about pH testing", []


gemini.generate = fake_generate


def msgs():
    return [p for m, p in sent if m == "sendMessage"]


def upd(**kw):
    return dict(update_id=1, **kw)


def pm(text, uid=42, **extra):
    return {"message_id": 1, "chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": text, **extra}


# strangers get their user id, nothing else
bot.handle_update(upd(message=pm("hi", uid=99)))
assert "private bot" in msgs()[-1]["text"] and "99" in msgs()[-1]["text"] and msgs()[-1]["chat_id"] == 99

# channel note -> screened -> drafted -> 2 messages to Meera's DM (chat 42)
sent.clear()
bot.handle_update(upd(channel_post={"message_id": 5, "chat": {"id": -1004429285345, "type": "channel"},
                                    "text": "customer asked if our serum works with her vit c. nobody publishes pH. ours is 5.5-5.8"}))
m = msgs()
assert all(x["chat_id"] == 42 for x in m), [x["chat_id"] for x in m]
assert m[0]["text"].startswith("Worth developing") and "NOTE (channel," in m[0]["text"]
meta, post = m[1], m[2]
assert "SOURCE NOTE: channel" in meta["text"] and "CATEGORY: Formulation Science" in meta["text"]
assert "CURRENT ANGLE: Journal of Cosmetic Dermatology, 2025, https://example.org/x" in meta["text"]
assert [b["callback_data"] for b in meta["reply_markup"]["inline_keyboard"][0]] == ["redo", "keep"]
assert post["text"] == pipeline._clean(GOOD), "lint should have forced a revision without 'glow' and '!'"
assert post["reply_parameters"]["message_id"] == 2
assert pipeline.parse_note(meta["text"])["text"].startswith("customer asked")

# other channels are ignored
sent.clear()
bot.handle_update(upd(channel_post={"message_id": 6, "chat": {"id": -555, "type": "channel"}, "text": "ignore me"}))
assert not msgs()

# Redo button reads the note back out of the message it's attached to
sent.clear()
bot.handle_update(upd(callback_query={"id": "c", "data": "redo", "from": {"id": 42},
                                      "message": {"chat": {"id": 42}, "message_id": 2, "text": meta["text"]}}))
assert "different take" in prompts[-3] or any("different take" in p for p in prompts[-4:])
assert msgs()[-1]["text"] == pipeline._clean(GOOD)

# Keep button
sent.clear()
bot.handle_update(upd(callback_query={"id": "c", "data": "keep", "from": {"id": 42},
                                      "message": {"chat": {"id": 42}, "message_id": 2, "text": meta["text"]}}))
assert any(m_ == "editMessageReplyMarkup" for m_, _ in sent)

# reply to the draft itself -> revise that text
sent.clear()
bot.handle_update(upd(message=pm("shorter please, and mention Chennai",
                                 reply_to_message={"message_id": 3, "from": {"id": 1, "is_bot": True}, "text": GOOD})))
assert msgs()[0]["text"].startswith("Revising") and msgs()[1]["text"].startswith("REVISED DRAFT")
assert any("shorter please" in p and "PREVIOUS VERSION" in p for p in prompts)

# rejected note in DM -> "Draft anyway" button -> draft
sent.clear()
bot.handle_update(upd(message=pm("just venting about a supplier today, so annoyed")))
assert msgs()[-1]["text"].startswith("Not drafting") and msgs()[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "draft"
rej = msgs()[-1]["text"]
sent.clear()
bot.handle_update(upd(callback_query={"id": "c", "data": "draft", "from": {"id": 42},
                                      "message": {"chat": {"id": 42}, "message_id": 9, "text": rej}}))
assert msgs()[-1]["text"] == pipeline._clean(GOOD)

# voice note
sent.clear()
bot.handle_update(upd(message={"message_id": 2, "chat": {"id": 42, "type": "private"}, "from": {"id": 42},
                               "voice": {"file_id": "f", "mime_type": "audio/ogg"}}))
assert "NOTE (telegram voice note," in msgs()[0]["text"]

# import
sent.clear()
bot.handle_update(upd(message=pm("/import old note about CoA mid-batch pH sampling results\n---\nsecond old note about humid city returns\n---\njust venting about a supplier today")))
t = [x["text"] for x in msgs()]
assert t[0].startswith("Got 3 notes") and "2 worth developing, 1 not" in t[1]
assert "strength 7/10" in t[2] and "strength 5/10" in t[3] and t[4].startswith("Not worth drafting")

# secrets derived from the token
assert config.webhook_secret() != config.worker_secret() and len(config.webhook_secret()) == 48

# lint unit checks
hard, _ = lint.check("Hi friends! Let's glow — #skincare\n- bullet\nWe optimized the color.")
joined = " | ".join(hard)
for needle in ["exclamation", "dashes", "hashtags", "bullet", "wellness", "American", "opener"]:
    assert needle in joined, (needle, joined)
hard, _ = lint.check('"Glow" is not a formulation claim. We stabilised the serum at pH 5.5-5.8 and it is sized for travel.')
assert hard == [], hard

print("ALL OFFLINE TESTS PASSED")
