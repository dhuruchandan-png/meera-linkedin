"""Configuration. Only four environment variables are needed:

  TELEGRAM_BOT_TOKEN   from @BotFather
  GEMINI_API_KEY       from aistudio.google.com
  TELEGRAM_USER_ID     Meera's Telegram user id: drafts go to her private chat with the bot
  CHAT_ID              her notes channel (e.g. -100...): posts there become notes

Names match case-insensitively; Telegram_Target_CHAT_ID is also accepted for CHAT_ID.

Optional: GEMINI_SCREEN_MODEL, GEMINI_DRAFT_MODEL.
"""
import hashlib
import os
import re

# "auto" = ask the API which models this key can use and pick the newest stable
# flash (screening, search, transcription) and pro (drafting). Set
# GEMINI_SCREEN_MODEL / GEMINI_DRAFT_MODEL to pin a specific model instead.
DEFAULT_SCREEN_MODEL = "auto-flash"
DEFAULT_DRAFT_MODEL = "auto-pro"


def get(*keys, default=None):
    """First non-empty value among `keys`. Names match case-insensitively
    (Telegram_Bot_Token works as well as TELEGRAM_BOT_TOKEN)."""
    lower = {k.lower(): v for k, v in os.environ.items()}
    for key in keys:
        v = os.environ.get(key) or lower.get(key.lower())
        if v not in (None, "") and v.strip():
            return v.strip()
    return default


def _int(*keys):
    v = get(*keys)
    try:
        return int(v) if v else None
    except ValueError:
        return None


def telegram_token():
    t = get("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
    if not t:
        return None
    t = t.strip().strip('"').strip("'").strip()
    if t.lower().startswith("bot") and ":" in t:
        t = t[3:]  # "bot123:ABC" -> "123:ABC"
    return t


def token_shape():
    """Describe the token's format without revealing it (for the setup page)."""
    t = telegram_token()
    if not t:
        return "missing"
    import re
    if re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", t):
        return f"looks valid ({len(t)} characters)"
    hints = []
    if ":" not in t:
        hints.append("has no ':' - a BotFather token looks like 1234567890:AAH...")
    if " " in t:
        hints.append("contains spaces")
    if t.startswith("@") or t.lower().endswith("bot"):
        hints.append("looks like a bot username, not a token")
    return f"does not look like a bot token ({len(t)} characters): " + ("; ".join(hints) or "unexpected format")


def gemini_keys():
    """One or more keys (separated by newlines, spaces or commas); tried in order."""
    raw = get("GEMINI_API_KEY", "GEMINI_API") or ""
    return [k.strip().strip('"').strip("'") for k in re.split(r"[\s,;]+", raw) if k.strip().strip('"').strip("'")]


def gemini_key():
    keys = gemini_keys()
    return keys[0] if keys else None


def redact(text):
    """Remove every secret value from text before it is shown anywhere."""
    text = str(text)
    secrets = list(gemini_keys())
    if telegram_token():
        secrets.append(telegram_token())
    raw = get("GEMINI_API_KEY", "GEMINI_API")
    if raw:
        secrets.append(raw)
    for sec in sorted(set(secrets), key=len, reverse=True):
        if len(sec) >= 8:
            text = text.replace(sec, "[redacted]")
    return re.sub(r"(AIza|AQ\.)[A-Za-z0-9_.\-]{16,}", "[redacted]", text)


def owner_id():
    return _int("TELEGRAM_USER_ID", "USER_ID")


def chat_id():
    return _int("CHAT_ID", "TELEGRAM_TARGET_CHAT_ID", "TELEGRAM_CHAT_ID", "CHANNEL_ID")


def screen_model():
    return get("GEMINI_SCREEN_MODEL", default=DEFAULT_SCREEN_MODEL)


def draft_model():
    return get("GEMINI_DRAFT_MODEL", default=DEFAULT_DRAFT_MODEL)


def fallback_model():
    return get("GEMINI_FALLBACK_MODEL", default=screen_model())


def _derived(purpose):
    # Secrets derived from the bot token, so they need no settings of their own.
    return hashlib.sha256(f"{purpose}:{telegram_token() or ''}".encode()).hexdigest()[:48]


def webhook_secret():
    return _derived("telegram-webhook")


def worker_secret():
    return _derived("draft-worker")
