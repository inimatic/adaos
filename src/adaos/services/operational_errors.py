from __future__ import annotations

from typing import Any


SENSITIVE_ERROR_MARKERS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "password",
        "private",
        "secret",
        "token",
    }
)


def normalized_error_code(value: Any, *, fallback: str) -> str:
    candidate = str(value or "").strip().lower()
    if (
        candidate
        and len(candidate) <= 160
        and not any(marker in candidate for marker in SENSITIVE_ERROR_MARKERS)
        and all(
            char.isascii() and (char.isalnum() or char in "._:-")
            for char in candidate
        )
    ):
        return candidate
    return fallback
