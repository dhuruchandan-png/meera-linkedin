"""Scheduled draft (Mon/Wed/Fri, see vercel.json): GET /api/cron"""
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import bot, config  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        secret = config.get("CRON_SECRET")
        if not secret or self.headers.get("Authorization") != f"Bearer {secret}":
            return self._reply(401, "unauthorised")
        try:
            result = bot.scheduled()
        except Exception as e:
            traceback.print_exc()
            result = f"error: {e}"
        self._reply(200, result)

    def _reply(self, code, text):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(str(text).encode())
