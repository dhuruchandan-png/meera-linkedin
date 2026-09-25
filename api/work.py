"""Background worker: POST /api/work (called by the webhook for drafting, which takes 1-3 minutes)."""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import bot, config  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("X-Worker-Secret") != config.worker_secret():
            return self._reply(401, "unauthorised")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            bot.run_job(json.loads(self.rfile.read(n) or b"{}"))
        except Exception:
            traceback.print_exc()
        self._reply(200, "done")

    def _reply(self, code, text):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(text.encode())
