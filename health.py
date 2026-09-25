"""GET /api/health - configuration status (never prints secrets)."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import config, store  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = {
            "telegram_token": bool(config.telegram_token()),
            "gemini_key": bool(config.gemini_key()),
            "screen_model": config.screen_model(),
            "draft_model": config.draft_model(),
            "webhook_secret": bool(config.get("TELEGRAM_WEBHOOK_SECRET")),
            "cron_secret": bool(config.get("CRON_SECRET")),
        }
        try:
            status["redis"] = store.ping() == "PONG"
            status["owner_linked"] = bool(config.get("OWNER_CHAT_ID") or store.cfg_get("owner"))
            status["channel_linked"] = bool(config.get("CHANNEL_ID") or store.cfg_get("channel"))
            status["queue"] = store.queue_size()
        except Exception as e:
            status["redis"] = f"error: {e}"
        body = json.dumps(status, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
