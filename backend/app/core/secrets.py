"""Secrets handling: API keys live only in the environment / .env, are never stored in the database or returned by the API,
and are scrubbed from every error text we persist or return (provider errors sometimes echo keys or URLs with ?key=...)."""
import os
import re

_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "VERTEX_API_KEY", "PV_API_TOKEN")
_PATTERNS = [re.compile(p) for p in (r"AIza[0-9A-Za-z_\-]{20,}", r"sk-[A-Za-z0-9_\-]{16,}", r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}")]
_QUERY = re.compile(r"(?i)([?&](?:key|api[_-]?key|token)=)[^&\s\"']+")
# `api_key=...`, `'api_key': '...'`, `token: ...`, `password=...` anywhere in an error text (a bare word 'key' is left alone)
_NAMED = re.compile(r"(?i)(\b(?:api[_-]?key|apikey|token|secret|password|authorization)\b[\"']?\s*[:=]\s*[\"']?)(?!\[REDACTED\])[^\s\"'&,;)}]+")


def redact(text: str) -> str:
    if not text:
        return text
    for name in _KEY_ENVS:
        v = os.environ.get(name, "")
        if len(v) >= 8:
            text = text.replace(v, "[REDACTED]")
    for p in _PATTERNS:  # token shapes first (so 'Authorization: Bearer <tok>' loses the token, not just the word 'Bearer')
        text = p.sub("[REDACTED]", text)
    text = _QUERY.sub(r"\1[REDACTED]", text)
    return _NAMED.sub(r"\1[REDACTED]", text)


def configured_keys() -> dict[str, bool]:
    """Which providers have a key configured (booleans only, never the values)."""
    has = lambda *names: any(len(os.environ.get(n, "")) >= 8 for n in names)
    return {"gemini": has("GEMINI_API_KEY", "GOOGLE_API_KEY"), "openai": has("OPENAI_API_KEY"), "anthropic": has("ANTHROPIC_API_KEY")}
