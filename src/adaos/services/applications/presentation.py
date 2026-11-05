"""Catalog presentation fallbacks; never infer product artwork from identity."""
from __future__ import annotations


def icon_fallback(title: str) -> dict[str, str]:
    words = str(title or "").split()
    text = "".join(word[0] for word in words[:2]).upper()
    return {"iconText": text or "?"}
