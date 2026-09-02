from __future__ import annotations

from typing import Any

from adaos.ports import EventBus
from adaos.services.eventbus import emit


ACTIVATION_REPORT_POLICIES = frozenset({"project_inbox", "diagnostic_only"})


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

    kind = str(component_type or "").strip().lower().rstrip("s")
    identifier = str(component_id or "").strip()
    if kind not in {"skill", "scenario"}:
        raise ValueError("activation observation component_type must be skill or scenario")
    if not identifier:
        raise ValueError("activation observation component_id is required")
    policy = str(report_policy or "").strip().lower() or "diagnostic_only"
    if policy not in ACTIVATION_REPORT_POLICIES:
        raise ValueError(f"unsupported activation report policy: {policy}")
    source_token = str(source or "").strip() or "runtime.activation"
    payload: dict[str, Any] = {
        "schema": "adaos.runtime.activation_observation.v1",
        "status": "failed",
        "component_type": kind,
        "component_id": identifier,
        f"{kind}_id": identifier,
        "failed_stage": str(stage or "").strip() or "activation",
        "error": str(error or "").strip() or "runtime activation failed",
        "source": source_token,
        "report_policy": policy,
        "space": str(space or "").strip() or "default",
        "webspace_id": str(webspace_id or "").strip() or None,
        "attempted_version": str(version or "").strip() or None,
        "slot": str(slot or "").strip() or None,
        "operation_id": str(operation_id or "").strip() or None,
    }
    if bus is not None and callable(getattr(bus, "publish", None)):
        emit(
            bus,
            "skills.activation.failed" if kind == "skill" else "scenarios.activation.failed",
            payload,
            source_token,
            schema="adaos.runtime.activation_observation.v1",
            version="1",
            generate_event_id=True,
        )
    return payload


__all__ = ["ACTIVATION_REPORT_POLICIES", "emit_runtime_activation_failure"]
