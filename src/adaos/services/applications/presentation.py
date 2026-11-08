"""Catalog presentation fallbacks; never infer product artwork from identity."""
from __future__ import annotations


def icon_fallback(title: str) -> dict[str, str]:
    words = str(title or "").split()
    text = "".join(word[0] for word in words[:2]).upper()
    return {"iconText": text or "?"}


def catalog_icon(title: str, *declared_icons: str | None) -> dict[str, str]:
    icon = next((value.strip() for value in declared_icons if isinstance(value, str) and value.strip()), '')
    return {'icon': icon or 'apps-outline', **({} if icon else icon_fallback(title))}
