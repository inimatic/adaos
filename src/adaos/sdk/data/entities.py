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


def propose_alias_add(
    *,
    canonical_ref: str,
    alias: str,
    locale: str | None = None,
    kind: str | None = None,
    webspace_id: str | None = None,
    actor: str | None = None,
    source: str = "sdk.data.entities",
    request_id: str | None = None,
) -> dict[str, Any]:
    return _service.propose_alias_add(
        canonical_ref=canonical_ref,
        alias=alias,
        locale=locale,
        kind=kind,
        webspace_id=webspace_id,
        actor=actor,
        source=source,
        request_id=request_id,
    )


def apply_alias_add(proposal: dict[str, Any]) -> dict[str, Any]:
    return _service.apply_alias_add(proposal)


__all__ = ["apply_alias_add", "list_entities", "propose_alias_add", "resolve_text"]
