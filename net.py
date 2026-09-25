"""Tiny JSON-over-HTTP helper built on the standard library (no dependencies)."""
import json
import urllib.error
import urllib.request


class HTTPError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def request(method, url, data=None, headers=None, timeout=60, raw=False):
    body = None
    h = dict(headers or {})
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        else:
            body = json.dumps(data).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        raise HTTPError(e.code, e.read().decode("utf-8", "replace")) from None
    if raw:
        return payload
    return json.loads(payload) if payload else None
