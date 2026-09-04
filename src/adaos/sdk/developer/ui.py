"""Typed WebUI capability discovery and deterministic acceptance checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.ui_capabilities import (
    evaluate_ui_request as _evaluate_ui_request,
    get_ui_capability as _get_ui_capability,
    qualify_ui_request as _qualify_ui_request,
    search_ui_capabilities as _search_ui_capabilities,
    selected_ui_capabilities as _selected_ui_capabilities,
    validate_webui_capabilities as _validate_webui_capabilities,
)


def search(query: str, *, kinds: Sequence[str] | None = None, limit: int = 8) -> dict[str, Any]:
    return _search_ui_capabilities(query, kinds=kinds, limit=limit)


def get(item_id: str) -> dict[str, Any]:
    return _get_ui_capability(item_id)


def qualify(request: str) -> dict[str, Any]:
    return _qualify_ui_request(request)


def select(request: str, *, limit: int = 8) -> dict[str, Any]:
    return _selected_ui_capabilities(request, limit=limit)


def validate(webui: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_webui_capabilities(webui)


def evaluate(
    request: str,
    webui: Mapping[str, Any],
    *,
    prototype_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return _evaluate_ui_request(request, webui, prototype_records=prototype_records)


__all__ = ["evaluate", "get", "qualify", "search", "select", "validate"]
