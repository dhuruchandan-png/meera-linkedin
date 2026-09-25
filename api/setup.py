"""One-time setup: open GET /api/setup in a browser after deploying.

Registers the Telegram webhook using this deployment's own settings and reports
what is missing. Safe to open again at any time. Add ?test=1 to also send Meera
a test message.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import config  # noqa: E402
from lib import telegram as tg  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        required = {
            "TELEGRAM_BOT_TOKEN": config.telegram_token(),
            "GEMINI_API_KEY": config.gemini_key(),
            "TELEGRAM_USER_ID": config.owner_id(),
            "CHAT_ID": config.chat_id(),
        }
        missing = [k for k, v in required.items() if not v]
        out = {"ok": True, "settings": {k: ("set" if v else "MISSING") for k, v in required.items()}}
        if not config.telegram_token():
            out.update(ok=False, fix="Add TELEGRAM_BOT_TOKEN in Vercel > Settings > Environment Variables, then Redeploy.")
            return self._json(500, out)
        try:
            me = tg.call("getMe")
            out["bot"] = "@" + me["username"]
            url = f"https://{self.headers.get('Host')}/api/telegram"
            tg.call("setWebhook", url=url, secret_token=config.webhook_secret(),
                    allowed_updates=["message", "channel_post", "callback_query"],
                    drop_pending_updates=True, max_connections=10)
            tg.call("setMyCommands", commands=[
                {"command": "help", "description": "How this works"},
                {"command": "import", "description": "Screen a batch of old notes"},
            ])
            out["webhook"] = tg.call("getWebhookInfo").get("url")
            if config.chat_id():
                try:
                    member = tg.call("getChatMember", chat_id=config.chat_id(), user_id=me["id"])
                    out["channel"] = ("bot is an admin: ok" if member.get("status") in ("administrator", "creator")
                                      else "the bot must be an ADMIN of the channel to read its posts")
                except tg.TelegramError as e:
                    out["channel"] = f"can't reach CHAT_ID ({e}). Add the bot to the channel as an admin."
            if config.owner_id():
                if query.get("test"):
                    try:
                        tg.send(config.owner_id(), "Connected. Send me a note or post one in your channel.")
                        out["test_message"] = "sent"
                    except tg.TelegramError as e:
                        out["test_message"] = (f"failed ({e}). Meera needs to open @{me['username']} "
                                               "in Telegram and tap Start once.")
                else:
                    out["next_step"] = (f"Meera opens https://t.me/{me['username']} and taps Start once "
                                        "(bots can't message someone first). Then open this page with ?test=1.")
        except tg.TelegramError as e:
            out.update(ok=False, error=str(e)[:300])
        if missing:
            out.update(ok=False, fix=f"Add {', '.join(missing)} in Vercel > Settings > Environment Variables, then Redeploy.")
        self._json(200 if out["ok"] else 500, out)

    def _json(self, code, body):
        data = json.dumps(body, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)
