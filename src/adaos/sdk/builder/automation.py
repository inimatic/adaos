"""Stable SDK operations for the Builder Automation loop."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.automation import BuilderAutomationService

    return BuilderAutomationService.from_context()


def start(
    *,
    object_type: str,
    object_id: str,
    implementation_brief: str,
    webspace_id: str = "desktop",
    conversation_id: str | None = None,
    brief_path: str | None = None,
) -> dict[str, Any]:
    """Start or resume implementation from an approved brief."""

    return dict(
        _service().start_from_execute(
            object_type=object_type,
            object_id=object_id,
            implementation_brief=implementation_brief,
            webspace_id=webspace_id,
            conversation_id=conversation_id,
            brief_path=brief_path,
        )
        or {}
    )


def submit(
    text: str,
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Submit one follow-up instruction and include the current projection."""

    service = _service()
    result = dict(
        service.submit_turn(
            text=text,
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
        )
        or {}
    )
    if not isinstance(result.get("automation"), Mapping):
        state = service.projection(
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
        )
        if isinstance(state, Mapping):
            result["automation"] = dict(state.get("automation") or {})
    return result


def get_state(
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Return the compact render-safe automation projection."""

    return dict(
        _service().projection(
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
        )
        or {}
    )


__all__ = ["get_state", "start", "submit"]
