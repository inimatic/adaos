"""Stable SDK operations for the Builder Automation loop."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any


def _service():
    from adaos.services.builder.automation import BuilderAutomationService

    execution_mode = str(os.getenv("ADAOS_DEV_TOOL_EXECUTION_MODE") or "").strip().lower()
    return BuilderAutomationService.from_context(background=execution_mode != "oneshot")


def _foreground_result(
    service: Any,
    result: Mapping[str, Any],
    *,
    object_type: str | None,
    object_id: str | None,
    webspace_id: str,
) -> dict[str, Any]:
    """Replace a queued acknowledgement with the durable final one-shot projection."""

    merged = dict(result or {})
    if bool(getattr(service, "background", True)):
        return merged
    final = service.projection(
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )
    if not isinstance(final, Mapping):
        return merged
    session = final.get("session") if isinstance(final.get("session"), Mapping) else None
    projection = final.get("automation") if isinstance(final.get("automation"), Mapping) else None
    if session is not None:
        merged["session"] = dict(session)
    if projection is not None:
        merged["automation"] = dict(projection)
    final_status = str((session or {}).get("status") or "").strip()
    if final_status:
        merged["status"] = f"automation_{final_status}"
    if final_status in {"completed", "failed", "cancelled", "expired"}:
        merged["ok"] = final_status == "completed"
    return merged


def start(
    *,
    object_type: str,
    object_id: str,
    implementation_brief: str,
    webspace_id: str = "desktop",
    conversation_id: str | None = None,
    brief_path: str | None = None,
    change_set_id: str | None = None,
) -> dict[str, Any]:
    """Start or resume implementation from an approved brief."""

    service = _service()
    result = service.start_from_execute(
        object_type=object_type,
        object_id=object_id,
        implementation_brief=implementation_brief,
        webspace_id=webspace_id,
        conversation_id=conversation_id,
        brief_path=brief_path,
        change_set_id=change_set_id,
    ) or {}
    return _foreground_result(
        service,
        result,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
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
    return _foreground_result(
        service,
        result,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )


def return_to_prototype(
    *,
    object_type: str,
    object_id: str,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Ask the built-in Automation LLM to derive a safe, disconnected prototype."""

    instruction = (
        "Workflow transition: derive a new safe Prototype revision from the current Automation result. "
        "Preserve the user-facing information architecture, layout, copy, and interaction intent, but remove "
        "all real service, credential, device, external-network, and production-data bindings. Replace them with "
        "typed contracts plus local mock or internal declarative data suitable for rapid prototyping. Do not "
        "publish or activate a release. Validate the resulting scenario and webui declarations and add or update "
        "tests that prove the prototype has no functional production bindings."
    )
    service = _service()
    result = service.submit_turn(
        text=instruction,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
        workflow_transition="return_to_prototype",
    ) or {}
    return _foreground_result(
        service,
        result,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )


def get_state(
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Return the compact render-safe automation projection."""

    result = dict(
        _service().projection(
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
        )
        or {}
    )
    if result.get("error") == "automation_session_not_found" and isinstance(
        result.get("automation"), Mapping
    ):
        result["ok"] = True
        result["session_present"] = False
        result.pop("error", None)
    elif result.get("ok"):
        result.setdefault("session_present", True)
    return result


def reconcile_checkpoint(*, object_type: str, object_id: str) -> dict[str, Any]:
    """Explicitly recover a failed post-Codex Forge checkpoint without rerunning Codex."""

    return dict(
        _service().reconcile_checkpoint(
            object_type=object_type,
            object_id=object_id,
        )
        or {}
    )


def recover_validated_result(*, object_type: str, object_id: str) -> dict[str, Any]:
    """Activate a preserved validated result without assigning Codex again."""

    return dict(
        _service().recover_validated_result(
            object_type=object_type,
            object_id=object_id,
        )
        or {}
    )


__all__ = [
    "get_state",
    "reconcile_checkpoint",
    "recover_validated_result",
    "return_to_prototype",
    "start",
    "submit",
]
