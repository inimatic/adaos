"""Credential redaction at diagnostic boundaries; never for runtime payloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

REDACTED = "[redacted]"
_CREDENTIAL_KEYS = frozenset({
    "token", "accesstoken", "refreshtoken", "idtoken", "authtoken", "apitoken",
    "sessiontoken", "sessionjwt", "jwt", "auth", "authorization", "proxyauthorization",
    "password", "passwd", "secret", "clientsecret", "apikey", "xapikey",
    "cookie", "setcookie", "signature", "sig", "xamzsignature", "xgoogsignature",
    "privatekey", "credential", "credentials",
})
_QUERY = re.compile(r"([?&;])([^=\s&#;]+)=([^\s&#;\"'<>]*)")
_AUTH = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9_~+/.=-]+", re.I)
_USERINFO = re.compile(r"(\b(?:https?|wss?)://)[^/\s@]+@", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_ASSIGNMENT = re.compile(
    r"(?<![\w-])(?P<prefix>[\"']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|token|"
    r"password|passwd|client[_-]?secret|secret|authorization|cookie)[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|\[redacted\]|[^\s,;&}\]]+)", re.I
)


def is_credential_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", unquote(str(key)).lower())
    return normalized in _CREDENTIAL_KEYS


def redact_log_text(value: str) -> str:
    text = _QUERY.sub(
        lambda match: f"{match[1]}{match[2]}={REDACTED}" if is_credential_key(match[2]) else match[0], value
    )
    text = _USERINFO.sub(r"\1[redacted]@", text)
    text = _AUTH.sub(lambda match: f"{match[1]} {REDACTED}", text)
    text = _JWT.sub(REDACTED, text)

    def redact_assignment(match: re.Match) -> str:
        raw = match["value"]
        quote = raw[0] if raw.startswith(('"', "'")) else ""
        return f'{match["prefix"]}{quote}{REDACTED}{quote}'

    return _ASSIGNMENT.sub(redact_assignment, text)


def redact_log_value(value: Any, *, _depth: int = 0) -> Any:
    if isinstance(value, str):
        return redact_log_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if _depth >= 32:
        return "[depth]"
    if isinstance(value, dict):
        return {
            str(key): REDACTED if is_credential_key(str(key)) else redact_log_value(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_log_value(item, _depth=_depth + 1) for item in value]
    return redact_log_text(str(value))
