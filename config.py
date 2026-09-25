"""Environment configuration. Read at call time so tests can override."""
import os

DEFAULT_SCREEN_MODEL = "gemini-2.5-flash"
DEFAULT_DRAFT_MODEL = "gemini-2.5-pro"


def get(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def telegram_token():
    return get("TELEGRAM_BOT_TOKEN")


def gemini_key():
    return get("GEMINI_API_KEY")


def screen_model():
    return get("GEMINI_SCREEN_MODEL", DEFAULT_SCREEN_MODEL)


def draft_model():
    return get("GEMINI_DRAFT_MODEL", DEFAULT_DRAFT_MODEL)


def redis_url():
    return get("UPSTASH_REDIS_REST_URL") or get("KV_REST_API_URL")


def redis_token():
    return get("UPSTASH_REDIS_REST_TOKEN") or get("KV_REST_API_TOKEN")


def fallback_model():
    return get("GEMINI_FALLBACK_MODEL", screen_model())
