from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from adaos.services import named_entities as _service


def list_entities(*, kind: str | None = None, webspace_id: str | None = None) -> list[dict[str, Any]]:
    return _service.list_entities(kind=kind, webspace_id=webspace_id)


def resolve_text(
    text: str,
    *,
    kind: str | None = None,
    include_fallback: bool = False,
    webspace_id: str | None = None,
    request_locale: str | None = None,
    preferred_locales: Iterable[str] | None = None,
) -> dict[str, Any]:
    return _service.resolve_text(
        text,
        kind=kind,
        include_fallback=include_fallback,
        webspace_id=webspace_id,
        request_locale=request_locale,
        preferred_locales=preferred_locales,
    )


__all__ = ["list_entities", "resolve_text"]
