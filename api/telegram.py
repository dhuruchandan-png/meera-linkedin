"""Telegram webhook: POST /api/telegram"""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import bot, config  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.webhook_secret():
            return self._reply(401, "unauthorised")
        bot.BASE_URL = f"https://{self.headers.get('Host')}"
        try:
            n = int(self.headers.get("Content-Length") or 0)
            bot.handle_update(json.loads(self.rfile.read(n) or b"{}"))
        except Exception:
            traceback.print_exc()
        # Always 200 so Telegram doesn't resend the update; errors are logged and reported in chat.
        self._reply(200, "ok")

    def do_GET(self):
        self._reply(200, "Skinstinct drafts bot: webhook endpoint. Open /api/setup to connect Telegram.")

    def _reply(self, code, text):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(text.encode())
