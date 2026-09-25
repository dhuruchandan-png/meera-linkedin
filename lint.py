"""Deterministic checks for the hard rules in the voice guide (Sections 4, 5.3, 7, 11).

These run on every draft in plain code, so the model cannot talk its way past them.
"""
import re

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿️‍←-⇿✅✨]"
)
WELLNESS = [
    "glow", "glowing", "radiant", "radiance", "nourish", "nourishing", "pamper", "self-love", "self care",
    "self-care", "skin-loving", "magic", "magical", "miracle", "holy grail", "game-changer", "game changer",
    "clean girl", "dewy", "luxurious",
]
HYPE = ["absolutely", "incredible", "obsessed", "you need this", "must-have"]
BAIT = [
    "agree?", "thoughts?", "comment below", "follow for more", "here's the thing", "nobody is talking about",
    "let that sink in", "unpopular opinion", "link in bio", "shop now", "discount code", "use code",
    "drop your", "tag someone", "dm me",
]
# American spellings she never uses. -ize words are handled by a pattern below.
US_WORDS = {
    "color": "colour", "colors": "colours", "behavior": "behaviour", "behaviors": "behaviours",
    "favorite": "favourite", "flavor": "flavour", "center": "centre", "centimeter": "centimetre",
    "centimeters": "centimetres", "fiber": "fibre", "gray": "grey", "labor": "labour", "odor": "odour",
    "humor": "humour", "honor": "honour", "program": "programme", "catalog": "catalogue",
    "defense": "defence", "aging": "ageing", "moisturizer": "moisturiser",
    "moisturizers": "moisturisers", "analyze": "analyse", "analyzed": "analysed", "paralyze": "paralyse",
}
IZE_OK = {"size", "sizes", "sized", "seize", "seized", "prize", "prizes", "capsize", "citizen", "citizens",
          "maize", "baize", "bizarre", "sizeable", "oversized", "downsize", "downsized"}
IZE = re.compile(r"\b([a-z]+)iz(e|es|ed|ing|ation|ations|er|ers)\b", re.I)


def _quoted(text, start, end):
    """Is text[start:end] inside double quotes (her way of holding marketing words at arm's length)?"""
    before = text[:start]
    return before.count('"') % 2 == 1 or (before.count("“") > before.count("”"))


def _phrase_hits(text, phrases):
    low = text.lower()
    hits = []
    for p in phrases:
        pat = r"(?<![a-z])" + re.escape(p) + (r"(?![a-z])" if p[-1].isalpha() else "")
        for m in re.finditer(pat, low):
            if not _quoted(text, m.start(), m.end()):
                hits.append(p)
                break
    return hits


def check(text, fmt="linkedin"):
    """Return (hard_failures, soft_failures) as lists of short strings."""
    hard, soft = [], []
    body = text.strip()

    if EMOJI.search(body):
        hard.append("contains emoji or pictographic symbols")
    if "—" in body or "–" in body:
        hard.append("contains em/en dashes (use a spaced hyphen ' - ')")
    if re.search(r"(^|\s)#[A-Za-z]\w*", body):
        hard.append("contains hashtags")
    if "!" in body:
        hard.append("contains exclamation marks")
    if re.search(r"^\s*([-*•▪●]|\d+[.)])\s+", body, re.M):
        hard.append("contains bullet or numbered list lines")
    if "**" in body or "__" in body or re.search(r"^\s*#{1,6}\s", body, re.M):
        hard.append("contains markdown bold or headers")

    w = _phrase_hits(body, WELLNESS)
    if w:
        hard.append("wellness words used unquoted: " + ", ".join(w))
    h = _phrase_hits(body, HYPE)
    if h:
        hard.append("hype words: " + ", ".join(h))
    b = _phrase_hits(body, BAIT)
    if b:
        hard.append("engagement bait or selling: " + ", ".join(b))

    us = sorted({m.group(0) for m in IZE.finditer(body) if m.group(0).lower() not in IZE_OK})
    us += sorted({wd for wd in re.findall(r"[A-Za-z]+", body) if wd.lower() in US_WORDS})
    if us:
        hard.append("American spelling: " + ", ".join(us[:8]))

    first = body.split("\n", 1)[0].strip().lower()
    if re.match(r"^(hi|hello|hey|dear|let's talk|let us talk|ever wondered|did you know)\b", first) and fmt == "linkedin":
        hard.append("greeting-style or bait opener")

    words = len(re.findall(r"\b[\w'%.-]+\b", body))
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    lo, hi = (380, 600) if fmt == "linkedin" else (430, 700)
    if not lo <= words <= hi:
        soft.append(f"length {words} words (target {lo + 20}-{hi - 50})")
    if fmt == "linkedin" and not 5 <= len(paras) <= 10:
        soft.append(f"{len(paras)} paragraphs (target 6-9)")
    one_line = sum(1 for p in paras if "\n" in p.strip())
    if one_line:
        soft.append("single line breaks inside paragraphs (use plain paragraphs)")
    if not re.search(r"\b(not saying|not making a case|not claiming|not trying to|i want to be (precise|careful|clear)|that doesn't mean|this is not)\b", body, re.I):
        soft.append("no explicit 'what I'm not saying' fence detected")
    return hard, soft
