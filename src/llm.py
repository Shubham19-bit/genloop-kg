"""One tiny JSON-returning LLM client for Gemini / Groq / Ollama, with a disk cache.

Plain `requests` calls (no SDKs) so there is nothing extra to install.
Every response is cached under data/llm_cache/<sha256>.json, so re-running the
pipeline costs zero API calls and results are reproducible.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import requests

from . import settings as S


class LLMError(RuntimeError):
    pass


def _cache_path(provider: str, model: str, system: str, prompt: str):
    h = hashlib.sha256(f"{provider}|{model}|{system}|{prompt}".encode()).hexdigest()[:32]
    return S.CACHE / f"{h}.json"


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # strip code fences
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


def _gemini(system: str, prompt: str, model: str | None = None) -> str:
    if not S.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY not set (see .env.example)")
    # key goes in a header, never the URL, so it can't leak into error messages or logs
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model or S.GEMINI_MODEL}:generateContent")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    r = requests.post(url, json=body, headers={"x-goog-api-key": S.GEMINI_API_KEY}, timeout=180)
    if r.status_code in (429, 500, 502, 503, 504):
        raise LLMError("retryable")
    if r.status_code >= 400:
        raise LLMError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _groq(system: str, prompt: str) -> str:
    if not S.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY not set (see .env.example)")
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {S.GROQ_API_KEY}"},
        json={
            "model": S.GROQ_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if r.status_code in (429, 500, 502, 503, 504):
        raise LLMError("retryable")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _ollama(system: str, prompt: str) -> str:
    r = requests.post(
        f"{S.OLLAMA_URL}/api/chat",
        json={"model": S.OLLAMA_MODEL, "stream": False, "format": "json",
              "think": False,   # Qwen 3.x reasons at length by default; not needed for short JSON calls
              # num_ctx: Ollama's default window (2-4k tokens) would silently cut off the filing pages
              "options": {"temperature": 0, "num_ctx": 16384},
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": prompt}]},
        timeout=600,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


_PROVIDERS = {"gemini": (_gemini, lambda: S.GEMINI_MODEL),
              "groq": (_groq, lambda: S.GROQ_MODEL),
              "ollama": (_ollama, lambda: S.OLLAMA_MODEL)}


FALLBACK_GEMINI = "gemini-3.5-flash-lite"   # the other free-tier model


def complete_json(system: str, prompt: str, provider: str | None = None, retries: int = 6):
    """Return parsed JSON from the LLM. Cached; retries with backoff on rate limits and
    temporary server errors (429/5xx). For Gemini, after 3 failed tries on the main model it
    switches to the free fallback model. The cache key records the model that answered."""
    provider = provider or S.LLM_PROVIDER
    fn, model_fn = _PROVIDERS[provider]
    models = [model_fn()]
    if provider == "gemini" and model_fn() != FALLBACK_GEMINI:
        models.append(FALLBACK_GEMINI)
    for model in models:                                   # cached answer from either model?
        path = _cache_path(provider, model, system, prompt)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["parsed"]

    delay, last = 5.0, None
    for attempt in range(retries):
        model = models[0] if attempt < 3 or len(models) == 1 else models[1]
        try:
            raw = fn(system, prompt, model) if provider == "gemini" else fn(system, prompt)
            parsed = _parse_json(raw)
            _cache_path(provider, model, system, prompt).write_text(
                json.dumps({"provider": provider, "model": model, "raw": raw, "parsed": parsed},
                           indent=1), encoding="utf-8")
            return parsed
        except LLMError as e:
            if str(e) != "retryable":
                raise
            last = e
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            last = e
        print(f"   [llm] {model}: temporary failure, retrying in {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise LLMError(f"gave up after {retries} tries: {last}")
