"""Persistence on Upstash Redis (REST API), with an in-memory twin for tests.

Keys
  seq:note / seq:draft        counters
  note:{id}                   JSON note
  draft:{id}                  JSON draft
  notes:recent                list of note ids, newest first
  queue                       sorted set of note ids worth drafting (score = strength)
  msg:{message_id}            draft id a Telegram message belongs to (for reply-to-redo)
  upd:{update_id}             webhook de-duplication
  lock:{name}                 short-lived locks
  cfg:owner / cfg:channel     linked owner chat and capture channel
"""
import json
import time

from . import config, net


class RedisStore:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def cmd(self, *args):
        r = net.request(
            "POST", self.url, [str(a) for a in args],
            headers={"Authorization": f"Bearer {self.token}"}, timeout=15,
        )
        if isinstance(r, dict) and r.get("error"):
            raise RuntimeError(f"Redis error: {r['error']}")
        return r.get("result") if isinstance(r, dict) else r


class MemoryStore:
    """Implements the handful of Redis commands this app uses."""

    def __init__(self):
        self.kv, self.exp, self.z, self.lists = {}, {}, {}, {}

    def _alive(self, k):
        if k in self.exp and self.exp[k] < time.time():
            self.kv.pop(k, None)
            self.exp.pop(k, None)
        return k in self.kv

    def cmd(self, *args):
        c, a = args[0].upper(), [str(x) for x in args[1:]]
        if c == "PING":
            return "PONG"
        if c == "GET":
            return self.kv.get(a[0]) if self._alive(a[0]) else None
        if c == "SET":
            k, v, opts = a[0], a[1], [o.upper() for o in a[2:]]
            if "NX" in opts and self._alive(k):
                return None
            self.kv[k] = v
            self.exp.pop(k, None)
            if "EX" in opts:
                self.exp[k] = time.time() + int(a[2 + opts.index("EX") + 1])
            return "OK"
        if c == "DEL":
            n = sum(1 for k in a if self.kv.pop(k, None) is not None)
            return n
        if c == "INCR":
            v = int(self.kv.get(a[0], 0)) + 1
            self.kv[a[0]] = str(v)
            return v
        if c == "ZADD":
            z = self.z.setdefault(a[0], {})
            z[a[2]] = float(a[1])
            return 1
        if c == "ZREM":
            return 1 if self.z.get(a[0], {}).pop(a[1], None) is not None else 0
        if c == "ZCARD":
            return len(self.z.get(a[0], {}))
        if c == "ZSCORE":
            s = self.z.get(a[0], {}).get(a[1])
            return None if s is None else str(s)
        if c == "ZREVRANGE":
            items = sorted(self.z.get(a[0], {}).items(), key=lambda kv: -kv[1])
            start, stop = int(a[1]), int(a[2])
            items = items[start: None if stop == -1 else stop + 1]
            if len(a) > 3 and a[3].upper() == "WITHSCORES":
                out = []
                for m, s in items:
                    out += [m, str(s)]
                return out
            return [m for m, _ in items]
        if c == "LPUSH":
            l = self.lists.setdefault(a[0], [])
            for v in a[1:]:
                l.insert(0, v)
            return len(l)
        if c == "LRANGE":
            l = self.lists.get(a[0], [])
            stop = int(a[2])
            return l[int(a[1]): None if stop == -1 else stop + 1]
        if c == "LTRIM":
            l = self.lists.get(a[0], [])
            stop = int(a[2])
            self.lists[a[0]] = l[int(a[1]): None if stop == -1 else stop + 1]
            return "OK"
        raise NotImplementedError(c)


_store = None


def backend():
    global _store
    if _store is None:
        url, token = config.redis_url(), config.redis_token()
        if not (url and token):
            raise RuntimeError("Redis is not configured (UPSTASH_REDIS_REST_URL / KV_REST_API_URL).")
        _store = RedisStore(url, token)
    return _store


def use(store):
    """Swap the backend (tests)."""
    global _store
    _store = store


def _cmd(*a):
    return backend().cmd(*a)


def _get_json(key):
    v = _cmd("GET", key)
    return json.loads(v) if v else None


# ---- notes ---------------------------------------------------------------

def new_note(text, source):
    nid = int(_cmd("INCR", "seq:note"))
    note = {
        "id": nid, "text": text.strip(), "source": source,
        "created": int(time.time()), "status": "new", "screen": None,
    }
    save_note(note)
    _cmd("LPUSH", "notes:recent", nid)
    _cmd("LTRIM", "notes:recent", 0, 499)
    return note


def save_note(note):
    _cmd("SET", f"note:{note['id']}", json.dumps(note))


def get_note(nid):
    return _get_json(f"note:{int(nid)}")


def recent_note_ids(n=500):
    return [int(x) for x in (_cmd("LRANGE", "notes:recent", 0, n - 1) or [])]


def queue_add(nid, strength):
    # newer notes win ties
    _cmd("ZADD", "queue", float(strength) + int(nid) / 1e6, int(nid))


def queue_remove(nid):
    _cmd("ZREM", "queue", int(nid))


def queue_ids(n=50):
    return [int(x) for x in (_cmd("ZREVRANGE", "queue", 0, n - 1) or [])]


def queue_size():
    return int(_cmd("ZCARD", "queue") or 0)


# ---- drafts --------------------------------------------------------------

def new_draft(draft):
    did = int(_cmd("INCR", "seq:draft"))
    draft["id"] = did
    draft.setdefault("created", int(time.time()))
    save_draft(draft)
    return draft


def save_draft(draft):
    _cmd("SET", f"draft:{draft['id']}", json.dumps(draft))


def get_draft(did):
    return _get_json(f"draft:{int(did)}")


def link_message(message_id, did):
    _cmd("SET", f"msg:{message_id}", did, "EX", 60 * 60 * 24 * 60)


def draft_for_message(message_id):
    v = _cmd("GET", f"msg:{message_id}")
    return int(v) if v else None


# ---- misc ----------------------------------------------------------------

def first_time(update_id):
    """True the first time we see this Telegram update (webhook retries are dropped)."""
    return _cmd("SET", f"upd:{update_id}", 1, "NX", "EX", 60 * 60 * 48) == "OK"


def acquire(name, ttl=330):
    return _cmd("SET", f"lock:{name}", 1, "NX", "EX", ttl) == "OK"


def release(name):
    _cmd("DEL", f"lock:{name}")


def cfg_get(key):
    return _cmd("GET", f"cfg:{key}")


def cfg_set(key, value):
    _cmd("SET", f"cfg:{key}", value)


def ping():
    return _cmd("PING")
