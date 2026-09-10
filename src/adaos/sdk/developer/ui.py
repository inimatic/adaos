"""Typed WebUI capability discovery and deterministic acceptance checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.builder_domain_packs import (
    domain_pack_ids_for_recipes as _domain_pack_ids_for_recipes,
    domain_pack_receipts as _domain_pack_receipts,
)
from adaos.services.ui_capabilities import (
    evaluate_ui_request as _evaluate_ui_request,
    get_ui_capability as _get_ui_capability,
    qualify_ui_request as _qualify_ui_request,
    search_ui_capabilities as _search_ui_capabilities,
    selected_ui_capabilities as _selected_ui_capabilities,
    validate_webui_capabilities as _validate_webui_capabilities,
)


def domain_packs_for_recipes(recipe_ids: Sequence[str]) -> list[str]:
    return _domain_pack_ids_for_recipes(recipe_ids)


def domain_pack_receipts(domain_packs: Sequence[str]) -> list[dict[str, Any]]:
    return _domain_pack_receipts(domain_packs)


def search(
    query: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int = 8,
    domain_packs: Sequence[str] | None = None,
) -> dict[str, Any]:
    return _search_ui_capabilities(
        query, kinds=kinds, limit=limit, domain_packs=domain_packs
    )


def get(item_id: str, *, domain_packs: Sequence[str] | None = None) -> dict[str, Any]:
    return _get_ui_capability(item_id, domain_packs=domain_packs)


def qualify(
    request: str, *, domain_packs: Sequence[str] | None = None
) -> dict[str, Any]:
    return _qualify_ui_request(request, domain_packs=domain_packs)


def select(
    request: str,
    *,
    limit: int = 8,
    domain_packs: Sequence[str] | None = None,
) -> dict[str, Any]:
    return _selected_ui_capabilities(request, limit=limit, domain_packs=domain_packs)


def validate(webui: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_webui_capabilities(webui)


def evaluate(
    request: str,
    webui: Mapping[str, Any],
    *,
    prototype_records: Sequence[Mapping[str, Any]] | None = None,
    locale_dictionaries: Mapping[str, Mapping[str, Any]] | None = None,
    domain_packs: Sequence[str] | None = None,
) -> dict[str, Any]:
    return _evaluate_ui_request(
        request,
        webui,
        prototype_records=prototype_records,
        locale_dictionaries=locale_dictionaries,
        domain_packs=domain_packs,
    )


__all__ = [
    "domain_pack_receipts",
    "domain_packs_for_recipes",
    "evaluate",
    "get",
    "qualify",
    "search",
    "select",
    "validate",
]
