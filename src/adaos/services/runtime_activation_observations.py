from __future__ import annotations

import logging
from typing import Any

from adaos.ports import EventBus
from adaos.services.eventbus import emit


ACTIVATION_REPORT_POLICIES = frozenset({"project_inbox", "diagnostic_only"})
ACTIVATION_OBSERVATION_STATUSES = frozenset({"failed", "passed"})
_log = logging.getLogger("adaos.runtime.activation_observations")


def _project_development_ticket(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("report_policy") != "project_inbox":
        return {"processed": False, "reason": "diagnostic_only"}
    try:
        from adaos.services.development_tickets import DevelopmentTicketService

        result = DevelopmentTicketService().report_runtime_activation_observation(payload)
    except Exception as exc:
        _log.warning("failed to durably project runtime activation observation", exc_info=True)
        return {
            "processed": False,
            "reason": "projection_failed",
            "error_type": type(exc).__name__,
        }
    ticket = result.get("ticket") if isinstance(result, dict) else None
    ticket_id = str(ticket.get("ticket_id") or "") or None if isinstance(ticket, dict) else None
    reason = str(result.get("reason") or "") or None if isinstance(result, dict) else None
    return {
        "processed": True,
        "reported": bool(result.get("reported")) if isinstance(result, dict) else False,
        "ticket_id": ticket_id,
        "reason": reason,
    }


def classify_runtime_activation_failure(error: object, *, default: str = "activation") -> str:
    """Map channel-specific failure text to a stable activation gate."""

    text = str(error or "").strip().lower()
    if "validation" in text or "schema" in text and "invalid" in text:
        return "validation"
    if any(token in text for token in ("skill tests failed", "pytest", "test suite", "tests failed")):
        return "tests"
    return str(default or "").strip().lower() or "activation"


def emit_runtime_activation_observation(
    bus: EventBus | None,
    *,
    status: str,
    component_type: str,
    component_id: str,
    stage: str,
    source: str,
    error: str | None = None,
    report_policy: str = "diagnostic_only",
    space: str = "default",
    webspace_id: str | None = None,
    version: str | None = None,
    slot: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Emit one stage-specific runtime activation observation."""

    kind = str(component_type or "").strip().lower().rstrip("s")
    identifier = str(component_id or "").strip()
    status_token = str(status or "").strip().lower()
    stage_token = str(stage or "").strip().lower() or "activation"
    if kind not in {"skill", "scenario"}:
        raise ValueError("activation observation component_type must be skill or scenario")
    if not identifier:
        raise ValueError("activation observation component_id is required")
    if status_token not in ACTIVATION_OBSERVATION_STATUSES:
        raise ValueError(f"unsupported activation observation status: {status_token}")
    policy = str(report_policy or "").strip().lower() or "diagnostic_only"
    if policy not in ACTIVATION_REPORT_POLICIES:
        raise ValueError(f"unsupported activation report policy: {policy}")
    source_token = str(source or "").strip() or "runtime.activation"
    error_text = str(error or "").strip()
    if status_token == "failed" and not error_text:
        error_text = "runtime activation failed"
    payload: dict[str, Any] = {
        "schema": "adaos.runtime.activation_observation.v1",
        "status": status_token,
        "component_type": kind,
        "component_id": identifier,
        f"{kind}_id": identifier,
        "stage": stage_token,
        "source": source_token,
        "report_policy": policy,
        "space": str(space or "").strip() or "default",
        "webspace_id": str(webspace_id or "").strip() or None,
        "attempted_version": str(version or "").strip() or None,
        "slot": str(slot or "").strip() or None,
        "operation_id": str(operation_id or "").strip() or None,
    }
    if status_token == "failed":
        payload["failed_stage"] = stage_token
        payload["error"] = error_text
    payload["development_ticket_projection"] = _project_development_ticket(payload)
    if bus is not None and callable(getattr(bus, "publish", None)):
        emit(
            bus,
            f"{kind}s.activation.{status_token}",
            payload,
            source_token,
            schema="adaos.runtime.activation_observation.v1",
            version="1",
            generate_event_id=True,
        )
    return payload


def emit_runtime_activation_failure(
    bus: EventBus | None,
    *,
    component_type: str,
    component_id: str,
    stage: str,
    error: str,
    source: str,
    report_policy: str = "diagnostic_only",
    space: str = "default",
    webspace_id: str | None = None,
    version: str | None = None,
    slot: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Emit one channel-neutral activation failure observation."""
    return emit_runtime_activation_observation(
        bus,
        status="failed",
        component_type=component_type,
        component_id=component_id,
        stage=stage,
        error=error,
        source=source,
        report_policy=report_policy,
        space=space,
        webspace_id=webspace_id,
        version=version,
        slot=slot,
        operation_id=operation_id,
    )


def emit_runtime_activation_success(
    bus: EventBus | None,
    *,
    component_type: str,
    component_id: str,
    stage: str,
    source: str,
    report_policy: str = "diagnostic_only",
    space: str = "default",
    webspace_id: str | None = None,
    version: str | None = None,
    slot: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Emit evidence that a previously failing activation gate now passes."""

    return emit_runtime_activation_observation(
        bus,
        status="passed",
        component_type=component_type,
        component_id=component_id,
        stage=stage,
        source=source,
        report_policy=report_policy,
        space=space,
        webspace_id=webspace_id,
        version=version,
        slot=slot,
        operation_id=operation_id,
    )


__all__ = [
    "ACTIVATION_OBSERVATION_STATUSES",
    "ACTIVATION_REPORT_POLICIES",
    "classify_runtime_activation_failure",
    "emit_runtime_activation_failure",
    "emit_runtime_activation_observation",
    "emit_runtime_activation_success",
]
