from __future__ import annotations

from typing import Any

from adaos.services import named_entities as _service


def list_entities(*, kind: str | None = None) -> list[dict[str, Any]]:
    return _service.list_entities(kind=kind)


def resolve_text(text: str, *, kind: str | None = None, include_fallback: bool = False) -> dict[str, Any]:
    return _service.resolve_text(
        text,
        kind=kind,
        include_fallback=include_fallback,
    )


__all__ = ["list_entities", "resolve_text"]
