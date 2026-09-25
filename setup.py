"""One-time setup: GET /api/setup?code=<SETUP_CODE>

Registers the Telegram webhook and command list using this deployment's own
environment variables, then returns Meera's owner-link. Safe to call again.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import config, store  # noqa: E402
from lib import telegram as tg  # noqa: E402

COMMANDS = [
    {"command": "draft", "description": "Draft the best queued note now (or /draft 12)"},
    {"command": "queue", "description": "Notes waiting to be drafted"},
    {"command": "notes", "description": "Recent notes and their status"},
    {"command": "import", "description": "Bulk-add old notes"},
    {"command": "skip", "description": "Take a note out of the queue"},
    {"command": "channel", "description": "Link your notes channel"},
    {"command": "help", "description": "How this works"},
]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = parse_qs(urlparse(self.path).query).get("code", [""])[0]
        if not config.get("SETUP_CODE") or code != config.get("SETUP_CODE"):
            return self._json(401, {"ok": False, "error": "add ?code=<SETUP_CODE> from your env file"})
        out = {"ok": True, "checks": {}}
        missing = [k for k in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "TELEGRAM_WEBHOOK_SECRET", "CRON_SECRET")
                   if not config.get(k)]
        if not (config.redis_url() and config.redis_token()):
            missing.append("UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN")
        if missing:
            return self._json(500, {"ok": False, "missing_env_vars": missing,
                                    "fix": "Add them in Vercel > Settings > Environment Variables, then Redeploy."})
        try:
            out["checks"]["redis"] = store.ping() == "PONG"
            me = tg.call("getMe")
            host = config.get("VERCEL_PROJECT_PRODUCTION_URL") or self.headers.get("Host")
            url = f"https://{host}/api/telegram"
            tg.call("setWebhook", url=url, secret_token=config.get("TELEGRAM_WEBHOOK_SECRET"),
                    allowed_updates=["message", "channel_post", "callback_query"],
                    drop_pending_updates=True, max_connections=10)
            tg.call("setMyCommands", commands=COMMANDS)
            info = tg.call("getWebhookInfo")
            out["bot"] = "@" + me["username"]
            out["webhook"] = info.get("url")
            owner = config.get("OWNER_CHAT_ID") or store.cfg_get("owner")
            out["owner_linked"] = bool(owner)
            if not owner:
                out["next_step"] = ("Open this link on Meera's phone and tap Start: "
                                    f"https://t.me/{me['username']}?start={config.get('SETUP_CODE')}")
            out["channel"] = config.get("CHANNEL_ID") or store.cfg_get("channel") or "not linked"
            out["channel_note"] = "The bot must be an administrator of that channel to read its posts."
        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)[:400]
        self._json(200 if out["ok"] else 500, out)

    def _json(self, code, body):
        data = json.dumps(body, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)
