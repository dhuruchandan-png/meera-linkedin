"""Gemini REST client (generateContent), with Google Search grounding and JSON mode."""
import base64
import json
import re
import time

from . import config, net

BASE = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE = {429, 500, 502, 503, 504}


class GeminiError(Exception):
    pass


_available = {}


def available_models(key):
    """Model ids this key can call with generateContent (cached per instance)."""
    if key not in _available:
        names = []
        try:
            r = net.request("GET", f"{BASE}/models?pageSize=1000", headers={"x-goog-api-key": key}, timeout=20)
            for m in r.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    names.append(m["name"].split("/", 1)[-1])
        except net.HTTPError:
            pass
        _available[key] = names
    return _available[key]


def _version(name, family):
    m = re.fullmatch(rf"gemini-(\d+(?:\.\d+)?)-{family}(-latest)?", name)
    return float(m.group(1)) if m else None


def _newest(family, key):
    ranked = sorted(((v, n) for n in available_models(key) if (v := _version(n, family)) is not None), reverse=True)
    return ranked[0] if ranked else (None, None)


def pick(family, key):
    """Newest stable gemini-X.Y-<family> (no preview/lite/exp builds)."""
    return _newest(family, key)[1]


def resolve(name, key):
    if name == "auto-pro":
        pv, pro = _newest("pro", key)
        fv, flash = _newest("flash", key)
        # An older-generation pro is retired or weaker than a current flash.
        if pro and (fv is None or pv >= fv):
            return pro
        return flash or name
    if name == "auto-flash":
        return pick("flash", key) or name
    return name


def _models(primary, key):
    first = resolve(primary, key)
    fb = resolve(config.fallback_model(), key)
    out = [first] if fb == first else [first, fb]
    # If a pinned model gets retired (404), fall back to whatever flash is current.
    auto = pick("flash", key)
    if auto and auto not in out:
        out.append(auto)
    return out


def generate(model, parts, system=None, schema=None, search=False, temperature=0.7, timeout=170):
    """Returns (text, sources). `parts` is a string or a list of Gemini parts."""
    if isinstance(parts, str):
        parts = [{"text": parts}]
    gen = {"temperature": temperature}
    if schema:
        gen["responseMimeType"] = "application/json"
        gen["responseSchema"] = schema
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if search:
        body["tools"] = [{"google_search": {}}]

    # Try the requested model (one retry on transient errors), then the fallback model.
    keys = config.gemini_keys()
    if not keys:
        raise GeminiError("GEMINI_API_KEY is not set")
    last_err = None
    for ki, key in enumerate(keys):
        try:
            return _generate_with_key(key, model, body, timeout)
        except GeminiError as e:
            last_err = e
            if ki < len(keys) - 1 and getattr(e, "retry_next_key", False):
                continue
            raise
    raise last_err


def _generate_with_key(key, model, body, timeout):
    last_err = None
    models = _models(model, key)
    for mi, m in enumerate(models):
        has_fallback = mi < len(models) - 1
        for attempt in range(2):
            try:
                r = net.request(
                    "POST", f"{BASE}/models/{m}:generateContent", body,
                    headers={"x-goog-api-key": key}, timeout=timeout,
                )
                return _parse(r)
            except net.HTTPError as e:
                last_err = e
                if (e.status == 404 or e.status == 429) and has_fallback:
                    break  # model missing or rate-limited: move to the fallback
                if e.status in RETRYABLE and attempt == 0:
                    time.sleep(4)
                    continue
                err = GeminiError(config.redact(e))
                err.retry_next_key = e.status in (0, 400, 401, 403, 429)
                raise err from None
            except (TimeoutError, OSError) as e:
                last_err = e
                break
    err = GeminiError(config.redact(last_err))
    err.retry_next_key = isinstance(last_err, net.HTTPError) and last_err.status == 429
    raise err


def _parse(r):
    cands = r.get("candidates") or []
    if not cands:
        raise GeminiError(f"No candidates returned: {json.dumps(r.get('promptFeedback', r))[:300]}")
    c = cands[0]
    text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))
    if not text.strip():
        raise GeminiError(f"Empty response (finishReason={c.get('finishReason')})")
    sources, seen = [], set()
    for ch in (c.get("groundingMetadata") or {}).get("groundingChunks", []):
        w = ch.get("web") or {}
        if w.get("uri") and w["uri"] not in seen:
            seen.add(w["uri"])
            sources.append({"title": w.get("title", ""), "uri": w["uri"]})
    return text, sources


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            return json.loads(t[i: j + 1])
        raise


def generate_json(model, parts, schema, **kw):
    text, _ = generate(model, parts, schema=schema, **kw)
    return parse_json(text)


def audio_part(data, mime="audio/ogg"):
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
