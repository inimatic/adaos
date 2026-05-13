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
    base_fingerprint: str | None = None,
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
        base_fingerprint=base_fingerprint,
    )


def apply_alias_add(proposal: dict[str, Any]) -> dict[str, Any]:
    return _service.apply_alias_add(proposal)


def add_device_alias(
    device_ref: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    from adaos.services import device_access as _device_access

    return _device_access.add_device_alias(
        device_ref,
        alias,
        locale=locale,
        actor=actor,
        request_id=request_id,
        base_fingerprint=base_fingerprint,
    )


__all__ = [
    "add_device_alias",
    "apply_alias_add",
    "list_entities",
    "propose_alias_add",
    "resolve_text",
]
