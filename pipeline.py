"""Screen -> current angle -> draft -> check -> (revise once) -> Section 12 output.

Every step is grounded in the voice guide and Meera's 15 published pieces.
Nothing here posts anywhere: the result goes back to Meera for review.
"""
import datetime
import os
import re
import time

from . import config, gemini, lint

HERE = os.path.dirname(os.path.abspath(__file__))
CATEGORIES = [
    "Ingredient Deep-Dive", "Industry Transparency", "Formulation Science", "Founder Story",
    "Brand Philosophy", "India-Specific Context", "Consumer Education",
]

_cache = {}


def _data(name):
    if name not in _cache:
        with open(os.path.join(HERE, "data", name), encoding="utf-8") as f:
            _cache[name] = f.read()
    return _cache[name]


def voice_guide():
    return _data("voice_guide.md")


def published():
    return _data("published.txt")


def today():
    # IST date; she works in Mumbai.
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).date().isoformat()


def system_screen():
    return (
        "You are the editorial screener for Meera Pillai, founder of Skinstinct. You decide which of her raw "
        "Telegram notes are worth developing into a LinkedIn post, strictly following Section 8.1 of her voice "
        "guide below. Be discerning: a thin or risky note should be rejected with a one-line reason.\n\n"
        "=== VOICE GUIDE ===\n" + voice_guide()
    )


def system_draft():
    return (
        "You draft LinkedIn posts in the voice of Meera Pillai, founder of Skinstinct, for her to review and edit. "
        "Her voice guide and all 15 pieces she has published are below. The guide's MUST/NEVER rules are hard "
        "constraints. The published pieces are the source of truth for tone: when unsure, imitate the closest one. "
        "The bar is not correctness; it is sounding like her. A previous content writer produced clean, accurate "
        "posts that she rewrote anyway.\n\n"
        "=== VOICE GUIDE ===\n" + voice_guide() + "\n\n=== PUBLISHED PIECES (her own writing) ===\n" + published()
    )


# ---- 1. screen -----------------------------------------------------------

SCREEN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "results": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "note_id": {"type": "INTEGER"},
                    "develop": {"type": "BOOLEAN"},
                    "criteria": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "why": {"type": "STRING"},
                    "reject_reason": {"type": "STRING"},
                    "category": {"type": "STRING", "enum": CATEGORIES},
                    "core_claim": {"type": "STRING"},
                    "strength": {"type": "INTEGER"},
                    "search_query": {"type": "STRING"},
                },
                "required": ["note_id", "develop", "why", "category", "core_claim", "strength"],
            },
        }
    },
    "required": ["results"],
}


def screen(notes):
    """notes: list of {id, text}. Returns {note_id: result}."""
    listing = "\n\n".join(f'--- NOTE {n["id"]} ---\n{n["text"]}' for n in notes)
    prompt = (
        f"Today is {today()}. Screen each note below against Section 8.1.\n\n"
        "For each note return:\n"
        "- develop: true only if it meets at least one of the five criteria AND none of the reject conditions "
        "(venting, complaint about a named person/brand, product idea not ready to discuss, unverifiable gossip, "
        "medical-advice request, too thin to reach ~400 words without padding).\n"
        "- criteria: which of criteria 1-5 it meets.\n"
        "- why: if develop, ONE sentence naming the criterion it meets (this becomes WHY THIS NOTE). "
        "If not, a short restatement of the reject reason.\n"
        "- reject_reason: one line if rejected, else empty.\n"
        "- category: best-fitting category from Section 4.3.\n"
        "- core_claim: the single core claim a post would make.\n"
        "- strength: 1-10. Highest for her own specific data, a clear label-vs-reality gap, or a candid lesson; "
        "lowest for vague observations.\n"
        "- search_query: a Google search query that could find a current, credible reference (Indian regulation "
        "such as CDSCO or BIS, peer-reviewed research, supplier data, market data, recalls) supporting the claim.\n\n"
        + listing
    )
    out = gemini.generate_json(config.screen_model(), prompt, SCREEN_SCHEMA, system=system_screen(), temperature=0.2)
    res = {}
    for r in out.get("results", []):
        try:
            res[int(r["note_id"])] = r
        except (KeyError, ValueError, TypeError):
            continue
    return res


# ---- 2. current angle ----------------------------------------------------

def find_angle(note_text, scr):
    prompt = (
        f"Today is {today()}. Meera Pillai (Indian D2C skincare founder, ex-pharma formulator) is writing a "
        "LinkedIn post. Use Google Search to find ONE real, verifiable, recent reference that supports the "
        "argument below. Prefer, in order: Indian regulatory updates (CDSCO, BIS cosmetic labelling, the "
        "Cosmetics Rules 2020), peer-reviewed dermatology or cosmetic-science research, ingredient supplier data, "
        "industry market data, recalls or enforcement actions. Prefer the last 18 months. Avoid celebrity or "
        "influencer news, viral trends without data, and scandals about specific competitor brands.\n\n"
        f"Core claim: {scr.get('core_claim')}\n"
        f"Category: {scr.get('category')}\n"
        f"Suggested query: {scr.get('search_query') or '(your choice)'}\n"
        f"Her raw note: {note_text}\n\n"
        "Only report something you actually found in the search results, with its real publisher and date. "
        "If nothing credible fits, say so. Reply in exactly this format and nothing else:\n"
        "FOUND: yes|no\nSOURCE: <publisher / regulator / journal and title>\nDATE: <date or year>\n"
        "URL: <url>\nFINDING: <the specific fact or number, one or two sentences>\n"
        "HOW IT SUPPORTS: <one sentence>"
    )
    try:
        text, sources = gemini.generate(config.screen_model(), prompt, search=True, temperature=0.2, timeout=90)
    except gemini.GeminiError as e:
        return {"found": False, "error": str(e)}
    fields = {}
    for line in text.splitlines():
        m = re.match(r"\s*\**\s*(FOUND|SOURCE|DATE|URL|FINDING|HOW IT SUPPORTS)\s*\**\s*:\s*(.*)", line, re.I)
        if m:
            fields[m.group(1).upper()] = m.group(2).strip()
    found = fields.get("FOUND", "").lower().startswith("y") and bool(fields.get("FINDING"))
    url = fields.get("URL", "")
    if not url.startswith("http") and sources:
        url = sources[0]["uri"]
    return {
        "found": found,
        "source": fields.get("SOURCE", ""),
        "date": fields.get("DATE", ""),
        "url": url,
        "finding": fields.get("FINDING", ""),
        "support": fields.get("HOW IT SUPPORTS", ""),
        "search_sources": sources[:5],
    }


def angle_line(angle):
    if not angle or not angle.get("found"):
        return "none found"
    return f'{angle.get("source")}, {angle.get("date")}, {angle.get("url")}'


# ---- 3. draft ------------------------------------------------------------

DRAFT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "post": {"type": "STRING"},
        "flags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "kept_verbatim": {"type": "ARRAY", "items": {"type": "STRING"}},
        "self_check_failures": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["post", "flags", "kept_verbatim", "self_check_failures"],
}


def _angle_block(angle):
    if angle and angle.get("found"):
        return (
            "CURRENT ANGLE (verified by search; cite it in the body the way she cites studies, naming the source "
            "and year, e.g. \"A 2022 study from the University of Liverpool ... found ...\". It supports the "
            "argument; it is not the hook for its own sake. Do NOT put the URL in the post):\n"
            f"Source: {angle['source']}\nDate: {angle['date']}\nFinding: {angle['finding']}\n"
            f"How it supports: {angle['support']}"
        )
    return (
        "CURRENT ANGLE: none found. Do NOT reference any news, study or data point that is not in her note or "
        "published pieces. Do not invent one."
    )


def draft(note, scr, angle, feedback=None, previous=None):
    prompt = (
        f"Today is {today()}.\n\n"
        f"RAW NOTE (note #{note['id']}, captured via Telegram). If it contains phrases in her own voice that fit, "
        "keep them verbatim; do not polish them:\n"
        f'"""\n{note["text"]}\n"""\n\n'
        f"SCREEN: category = {scr.get('category')}; core claim = {scr.get('core_claim')}; "
        f"why this note = {scr.get('why')}\n\n"
        + _angle_block(angle) + "\n\n"
        "TASK: Write ONE LinkedIn post for Meera to review, following Section 8.2 step by step:\n"
        "- Five-beat arc: HOOK (specific fact, number, scene or customer question; no greeting or bait) -> "
        "MECHANISM (the chemistry or regulation, explained plainly, technical terms explained in the same or next "
        "sentence) -> FENCE (what she is NOT saying; credit the other side) -> OUR PRACTICE (only facts known from "
        "the note or published pieces; include trade-offs or mistakes; never a pitch) -> TAKEAWAY (a question the "
        "reader can ask any brand, or a flat statement of practice).\n"
        "- 6-9 plain paragraphs, 400-550 words. Paragraphs separated by one blank line. No line breaks inside a "
        "paragraph.\n"
        "- Use 2-4 phrase-bank moves, not more. At least one short flat sentence landing a point after a long one.\n"
        "- British spelling. Spaced hyphen ' - ' for asides; never em or en dashes. No emojis, hashtags, bullets, "
        "bold, exclamation marks, wellness words, hype, selling or engagement bait. No sign-off.\n"
        "- Ground in India where relevant.\n"
        "- FACTS: use only facts from the note, her published pieces, or the current angle above. Never invent "
        "numbers, studies, test results, customer quotes or product specs. Where a point needs a number you do not "
        "have, write [NEEDS DATA: what is needed] in the post and list it in flags.\n\n"
        "Return JSON: post (the post text only), flags (every [NEEDS DATA] gap and every claim Meera should "
        "verify), kept_verbatim (phrases kept from her note), self_check_failures (Section 11 rubric items this "
        "draft does not pass; empty if all pass)."
    )
    if previous:
        prompt += "\n\nPREVIOUS VERSION:\n\"\"\"\n" + previous + "\n\"\"\""
    if feedback:
        prompt += (
            "\n\nREVISE the previous version to address the following. Keep everything that already works; "
            "change only what is needed:\n" + feedback
        )
    return gemini.generate_json(config.draft_model(), prompt, DRAFT_SCHEMA, system=system_draft(), temperature=0.7)


# ---- 4. review -----------------------------------------------------------

REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hard_failures": {"type": "ARRAY", "items": {"type": "STRING"}},
        "soft_failures": {"type": "ARRAY", "items": {"type": "STRING"}},
        "unsupported_claims": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["hard_failures", "soft_failures", "unsupported_claims"],
}


def review(note, post, angle):
    prompt = (
        "Act as Meera's strict editor. Score the DRAFT against every item of the Section 11 self-check rubric.\n"
        "- hard_failures: (hard) rubric items the draft fails, each with a short specific reason.\n"
        "- soft_failures: other rubric items it fails.\n"
        "- unsupported_claims: any number, study, test result, customer quote or product spec in the draft that "
        "does not appear in the RAW NOTE, her PUBLISHED PIECES, or the CURRENT ANGLE. Quote each. Placeholders "
        "written as [NEEDS DATA: ...] are fine.\n"
        "Be precise. Do not invent problems; an empty list is a valid answer.\n\n"
        f'RAW NOTE:\n"""\n{note["text"]}\n"""\n\n'
        + _angle_block(angle) + "\n\n"
        f'DRAFT:\n"""\n{post}\n"""'
    )
    return gemini.generate_json(config.screen_model(), prompt, REVIEW_SCHEMA, system=system_draft(), temperature=0.1)


# ---- full run ------------------------------------------------------------

def run(note, scr, angle=None, feedback=None, previous=None, budget=240):
    """Draft a note end to end. Returns a dict ready for render()."""
    t0 = time.time()
    if angle is None:
        angle = find_angle(note["text"], scr)
    d = draft(note, scr, angle, feedback=feedback, previous=previous)
    post = _clean(d.get("post", ""))
    hard, soft = lint.check(post)
    rv = {}
    try:
        rv = review(note, post, angle)
    except gemini.GeminiError:
        pass
    issues = hard + list(rv.get("hard_failures", [])) + [f"unsupported claim: {c}" for c in rv.get("unsupported_claims", [])]
    revised = False
    if issues and time.time() - t0 < budget - 70:
        fix = "\n".join(f"- {i}" for i in issues)
        fix += ("\nFor unsupported claims: remove them, soften to what is supportable, or replace with "
                "[NEEDS DATA: ...].")
        d2 = draft(note, scr, angle, feedback=fix, previous=post)
        post2 = _clean(d2.get("post", ""))
        if post2:
            d, post, revised = d2, post2, True
            hard, soft = lint.check(post)
            rv = {"hard_failures": [], "soft_failures": rv.get("soft_failures", [])}
    failures = hard + [f"(hard) {x}" for x in rv.get("hard_failures", [])] + soft + list(rv.get("soft_failures", []))
    failures += [f for f in d.get("self_check_failures", []) if f not in failures]
    return {
        "post": post,
        "category": scr.get("category"),
        "why": scr.get("why"),
        "angle": angle,
        "flags": list(dict.fromkeys(d.get("flags", []) + [f"kept verbatim: \"{k}\"" for k in d.get("kept_verbatim", [])])),
        "self_check": _dedupe(failures),
        "revised": revised,
        "seconds": int(time.time() - t0),
    }


def _clean(post):
    post = post.strip().replace("—", " - ").replace("–", "-")
    post = re.sub(r"[ \t]+\n", "\n", post)
    post = re.sub(r"\n{3,}", "\n\n", post)
    return post


def _dedupe(items):
    out, seen = [], set()
    for i in items:
        k = i.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(i)
    return out


def render(note, result, draft_id=None):
    """Section 12 structure, split into three Telegram messages so the post itself is copy-ready."""
    created = datetime.datetime.utcfromtimestamp(note["created"] + 19800).strftime("%d %b %Y")
    head = (
        f"SOURCE NOTE: note #{note['id']} ({note.get('source', 'telegram')}, {created})\n"
        f"CATEGORY: {result['category']}\n"
        "FORMAT: LinkedIn\n"
        f"WHY THIS NOTE: {result['why']}\n\n"
        f"--- DRAFT{' #' + str(draft_id) if draft_id else ''} (next message) ---"
    )
    body = result["post"]
    angle = result["angle"] or {}
    tail = "--- END DRAFT ---\n\n"
    tail += f"CURRENT ANGLE: {angle_line(angle)}\n"
    if angle.get("found") and angle.get("finding"):
        tail += f"Sources: {angle.get('finding')} ({angle.get('url')})\n"
    tail += "FLAGS: " + ("\n- " + "\n- ".join(result["flags"]) if result["flags"] else "none") + "\n"
    tail += "SELF-CHECK: " + ("\n- " + "\n- ".join(result["self_check"]) if result["self_check"] else "all pass")
    tail += "\n\nNothing has been posted. Copy it into LinkedIn yourself when it is right. Reply to the draft with notes to get a revision."
    return head, body, tail
