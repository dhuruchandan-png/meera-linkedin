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

DEFAULT_SCREEN_MODEL = "gemini-2.5-flash"
DEFAULT_DRAFT_MODEL = "gemini-2.5-pro"


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
    return get("TELEGRAM_BOT_TOKEN")


def gemini_key():
    return get("GEMINI_API_KEY", "GEMINI_API")


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
