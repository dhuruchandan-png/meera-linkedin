"""Minimal Telegram Bot API client."""
from . import config, net

API = "https://api.telegram.org"
LIMIT = 4000  # Telegram caps messages at 4096 characters


class TelegramError(Exception):
    pass


def call(method, **params):
    params = {k: v for k, v in params.items() if v is not None}
    try:
        r = net.request("POST", f"{API}/bot{config.telegram_token()}/{method}", params, timeout=30)
    except net.HTTPError as e:
        raise TelegramError(f"{method}: {e.body[:300]}") from None
    if not r.get("ok"):
        raise TelegramError(f"{method}: {r}")
    return r["result"]


def keyboard(rows):
    """rows: [[(label, callback_data), ...], ...]"""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def _chunks(text):
    if len(text) <= LIMIT:
        return [text]
    out, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > LIMIT:
            out.append(para[:LIMIT])
            para = para[LIMIT:]
        if cur and len(cur) + 2 + len(para) > LIMIT:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def send(chat_id, text, buttons=None, reply_to=None):
    """Send plain text (no parse mode, so nothing needs escaping). Returns the last message."""
    msg = None
    parts = _chunks(text or "(empty)")
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        msg = call(
            "sendMessage", chat_id=chat_id, text=part,
            reply_markup=keyboard(buttons) if (buttons and last) else None,
            reply_parameters={"message_id": reply_to, "allow_sending_without_reply": True} if (reply_to and i == 0) else None,
            link_preview_options={"is_disabled": True},
        )
    return msg


def set_buttons(chat_id, message_id, buttons):
    try:
        call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
             reply_markup=keyboard(buttons) if buttons else {"inline_keyboard": []})
    except TelegramError:
        pass


def answer_callback(callback_id, text=None):
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except TelegramError:
        pass


def typing(chat_id):
    try:
        call("sendChatAction", chat_id=chat_id, action="typing")
    except TelegramError:
        pass


def download(file_id):
    path = call("getFile", file_id=file_id)["file_path"]
    return net.request("GET", f"{API}/file/bot{config.telegram_token()}/{path}", raw=True, timeout=60), path
