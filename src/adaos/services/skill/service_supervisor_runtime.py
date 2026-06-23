from __future__ import annotations

import logging
from typing import Any, Dict

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.pending_actions import list_pending_actions_async, publish_pending_action_async
from adaos.services.skill.service_supervisor import get_service_supervisor

_log = logging.getLogger("adaos.skill.service.runtime")
_RUNTIME_RECOVERY_KIND = "runtime.recovery.service_supervisor_failure"
_RUNTIME_RECOVERY_RESPONSE_TOPIC = "runtime.recovery.service_supervisor.response"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _payload(evt: Any) -> dict[str, Any]:
    payload = getattr(evt, "payload", None)
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(evt, dict):
        return dict(evt)
    return {}


def _operation_label(operation: str) -> str:
    return {
        "restart_service": "restart service",
        "stop_service": "stop service",
        "stop_all_services": "stop all services",
    }.get(operation, operation.replace("_", " "))


def _recovery_domain_ref(*, operation: str, skill_name: str | None, reason: str) -> dict[str, Any]:
    return {
        "operation": _text(operation),
        "skill_name": _text(skill_name),
        "reason": _text(reason),
    }


async def _find_active_recovery_action(
    *,
    operation: str,
    skill_name: str | None,
    reason: str,
) -> str:
    try:
        snapshot = await list_pending_actions_async(include_terminal=False)
    except Exception:
        return ""
    expected = _recovery_domain_ref(operation=operation, skill_name=skill_name, reason=reason)
    for item in snapshot.get("active_items") or []:
        if not isinstance(item, dict):
            continue
        if _text(item.get("kind")) != _RUNTIME_RECOVERY_KIND:
            continue
        domain_ref = item.get("domain_ref") if isinstance(item.get("domain_ref"), dict) else {}
        if all(_text(domain_ref.get(key)) == _text(value) for key, value in expected.items()):
            return _text(item.get("id"))
    return ""


async def _publish_service_recovery_action(
    *,
    operation: str,
    skill_name: str | None,
    reason: str,
    exc: BaseException,
    details: dict[str, Any] | None = None,
) -> str:
    existing = await _find_active_recovery_action(operation=operation, skill_name=skill_name, reason=reason)
    if existing:
        return existing
    error_type = type(exc).__name__
    error_text = str(exc)
    target = _text(skill_name) or "service supervisor"
    operation_text = _operation_label(operation)
    summary = f"Failed to {operation_text} {target}: {error_type}: {error_text}"
    domain_ref = _recovery_domain_ref(operation=operation, skill_name=skill_name, reason=reason)
    try:
        action = await publish_pending_action_async(
            ctx=get_ctx(),
            kind=_RUNTIME_RECOVERY_KIND,
            title="Service recovery needs attention",
            title_i18n={"key": "pending_actions.runtime.service_recovery_title"},
            summary=summary,
            summary_i18n={
                "key": "pending_actions.runtime.service_recovery_summary",
                "params": {"operation": operation_text, "target": target, "error_type": error_type},
            },
            producer={"type": "system", "system_id": "runtime_recovery"},
            owner_scope={},
            domain_ref=domain_ref,
            allowed_actions=[
                {
                    "id": "retry",
                    "label": "Retry",
                    "label_i18n": {"key": "pending_actions.action.retry"},
                    "terminal": True,
                },
                {
                    "id": "open_diagnostics",
                    "label": "Diagnostics",
                    "label_i18n": {"key": "pending_actions.action.open_diagnostics"},
                    "terminal": False,
                },
                {
                    "id": "postpone",
                    "label": "Later",
                    "label_i18n": {"key": "pending_actions.action.postpone"},
                    "terminal": False,
                },
                {
                    "id": "dismiss",
                    "label": "Dismiss",
                    "label_i18n": {"key": "pending_actions.action.dismiss"},
                    "terminal": True,
                },
            ],
            default_text_binding=False,
            response_route={
                "type": "event",
                "topic": _RUNTIME_RECOVERY_RESPONSE_TOPIC,
                "target": {"type": "system", "system_id": "runtime_recovery"},
            },
            metadata={
                "source": "service_supervisor_runtime",
                "error_type": error_type,
                "error": error_text,
                "details": details or {},
            },
        )
        return _text(action.get("id"))
    except Exception:
        _log.warning("failed to publish service recovery pending action operation=%s skill=%s", operation, skill_name, exc_info=True)
        return ""


def _emit_runtime_recovery(topic: str, payload: dict[str, Any]) -> None:
    try:
        bus_emit(get_ctx().bus, topic, payload, source="runtime.recovery")
    except Exception:
        _log.debug("failed to emit runtime recovery event topic=%s", topic, exc_info=True)


async def _restart_if_service(skill_name: str | None, *, reason: str) -> bool:
    if not skill_name:
        return False
    try:
        supervisor = get_service_supervisor()
        supervisor.ensure_discovered()
        if skill_name not in supervisor.list():
            return False
        await supervisor.restart(skill_name)
        _log.info("service restarted skill=%s reason=%s", skill_name, reason)
        return True
    except Exception as exc:
        _log.warning("failed to restart service skill=%s reason=%s", skill_name, reason, exc_info=True)
        await _publish_service_recovery_action(
            operation="restart_service",
            skill_name=skill_name,
            reason=reason,
            exc=exc,
        )
        return False


async def _stop_if_service(skill_name: str | None, *, reason: str) -> bool:
    if not skill_name:
        return False
    try:
        supervisor = get_service_supervisor()
        supervisor.ensure_discovered()
        if skill_name not in supervisor.list():
            return False
        await supervisor.stop(skill_name)
        _log.info("service stopped skill=%s reason=%s", skill_name, reason)
        return True
    except Exception as exc:
        _log.warning("failed to stop service skill=%s reason=%s", skill_name, reason, exc_info=True)
        await _publish_service_recovery_action(
            operation="stop_service",
            skill_name=skill_name,
            reason=reason,
            exc=exc,
        )
        return False


async def _stop_all_services(*, reason: str) -> bool:
    supervisor = get_service_supervisor()
    try:
        supervisor.ensure_discovered()
    except Exception as exc:
        _log.warning("failed to discover service supervisor before shutdown reason=%s", reason, exc_info=True)
        await _publish_service_recovery_action(
            operation="stop_all_services",
            skill_name=None,
            reason=reason,
            exc=exc,
        )
        return False
    shutdown = getattr(supervisor, "shutdown", None)
    shutdown_exc: BaseException | None = None
    if callable(shutdown):
        try:
            await shutdown()
            _log.info("service supervisor shutdown reason=%s", reason)
            return True
        except Exception as exc:
            shutdown_exc = exc
            _log.warning("failed to shutdown service supervisor reason=%s", reason, exc_info=True)
    ok = True
    try:
        service_names = list(supervisor.list())
    except Exception as exc:
        _log.warning("failed to list services during supervisor shutdown reason=%s", reason, exc_info=True)
        await _publish_service_recovery_action(
            operation="stop_all_services",
            skill_name=None,
            reason=reason,
            exc=exc,
        )
        return False
    for skill_name in service_names:
        try:
            await supervisor.stop(skill_name)
            _log.info("service stopped skill=%s reason=%s", skill_name, reason)
        except Exception as exc:
            ok = False
            _log.warning("failed to stop service skill=%s reason=%s", skill_name, reason, exc_info=True)
            await _publish_service_recovery_action(
                operation="stop_service",
                skill_name=skill_name,
                reason=reason,
                exc=exc,
            )
    if not ok and shutdown_exc is not None:
        await _publish_service_recovery_action(
            operation="stop_all_services",
            skill_name=None,
            reason=reason,
            exc=shutdown_exc,
            details={"fallback_stop_failed": True},
        )
    return ok


async def _open_service_diagnostics(*, skill_name: str | None, operation: str, pending_action_id: str) -> None:
    supervisor = get_service_supervisor()
    diagnostics: dict[str, Any] = {
        "pending_action_id": pending_action_id,
        "operation": operation,
        "skill_name": _text(skill_name),
    }
    if skill_name:
        try:
            diagnostics["status"] = supervisor.status(skill_name, check_health=True)
        except Exception as exc:
            diagnostics["status_error"] = f"{type(exc).__name__}: {exc}"
        try:
            diagnostics["issues"] = supervisor.issues(skill_name)
        except Exception as exc:
            diagnostics["issues_error"] = f"{type(exc).__name__}: {exc}"
    _emit_runtime_recovery("runtime.recovery.diagnostics.requested", diagnostics)


@subscribe(_RUNTIME_RECOVERY_RESPONSE_TOPIC)
async def _on_runtime_recovery_response(evt: Any) -> None:
    payload = _payload(evt)
    action = payload.get("pending_action") if isinstance(payload.get("pending_action"), dict) else {}
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    response_action_id = _text(payload.get("response_action_id") or response.get("response_action_id"))
    domain_ref = payload.get("domain_ref") if isinstance(payload.get("domain_ref"), dict) else {}
    pending_action_id = _text(payload.get("pending_action_id") or action.get("id"))
    operation = _text(domain_ref.get("operation"))
    skill_name = _text(domain_ref.get("skill_name")) or None
    if response_action_id == "open_diagnostics":
        await _open_service_diagnostics(skill_name=skill_name, operation=operation, pending_action_id=pending_action_id)
        return
    if response_action_id == "postpone":
        _emit_runtime_recovery(
            "runtime.recovery.postponed",
            {"pending_action_id": pending_action_id, "operation": operation, "skill_name": skill_name},
        )
        return
    if response_action_id == "dismiss":
        _emit_runtime_recovery(
            "runtime.recovery.dismissed",
            {"pending_action_id": pending_action_id, "operation": operation, "skill_name": skill_name},
        )
        return
    if response_action_id != "retry":
        _emit_runtime_recovery(
            "runtime.recovery.response_ignored",
            {
                "pending_action_id": pending_action_id,
                "operation": operation,
                "skill_name": skill_name,
                "response_action_id": response_action_id,
                "reason": "unsupported_response_action",
            },
        )
        return
    retry_reason = f"pending_action.retry:{pending_action_id or 'unknown'}"
    _emit_runtime_recovery(
        "runtime.recovery.retry.started",
        {"pending_action_id": pending_action_id, "operation": operation, "skill_name": skill_name},
    )
    if operation == "restart_service":
        ok = await _restart_if_service(skill_name, reason=retry_reason)
    elif operation == "stop_service":
        ok = await _stop_if_service(skill_name, reason=retry_reason)
    elif operation == "stop_all_services":
        ok = await _stop_all_services(reason=retry_reason)
    else:
        ok = False
    _emit_runtime_recovery(
        "runtime.recovery.retry.completed",
        {"pending_action_id": pending_action_id, "operation": operation, "skill_name": skill_name, "ok": ok},
    )


@subscribe("skills.activated")
async def _on_skill_activated(payload: Dict[str, Any]) -> None:
    await _restart_if_service(payload.get("skill_name"), reason="skills.activated")


@subscribe("skills.rolledback")
async def _on_skill_rolledback(payload: Dict[str, Any]) -> None:
    await _restart_if_service(payload.get("skill_name"), reason="skills.rolledback")


@subscribe("skills.deactivated")
async def _on_skill_deactivated(payload: Dict[str, Any]) -> None:
    await _stop_if_service(payload.get("name") or payload.get("skill_name"), reason="skills.deactivated")


@subscribe("subnet.stopping")
async def _on_subnet_stopping(payload: Dict[str, Any]) -> None:
    reason = str((payload or {}).get("reason") or "subnet.stopping").strip() or "subnet.stopping"
    await _stop_all_services(reason=reason)

