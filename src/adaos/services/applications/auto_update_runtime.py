"""Post-readiness and registry-event runtime for Application auto updates."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import hashlib
from typing import Any

from adaos.domain import Event
from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.workspace_sync import sync_workspace_sparse_to_registry


_LOG = logging.getLogger("adaos.applications.auto_update")
_TASK: asyncio.Task[Any] | None = None
_PENDING_TRIGGER: str | None = None


def _publish_contribution_notification(result: dict[str, Any]) -> None:
    outcomes = [
        item
        for item in result.get("outcomes") or []
        if isinstance(item, dict) and item.get("status") == "succeeded"
    ]
    if not outcomes:
        return
    report_ids = {
        str(report_id).strip()
        for item in outcomes
        for report_id in item.get("addresses_report_ids") or []
        if str(report_id).strip()
    }
    local_report_ids: set[str] = set()
    if report_ids:
        try:
            from adaos.services.applications import get_development_report_service

            reports = get_development_report_service()
            reports.receive(limit=100)
            local_report_ids = {
                report_id for report_id in report_ids if reports.get_report(report_id)
            }
        except Exception:
            _LOG.debug("failed to reconcile addressed Development Reports", exc_info=True)
    app_ids = [str(item.get("application_id") or "").strip() for item in outcomes]
    app_ids = [item for item in app_ids if item]
    updated = ", ".join(app_ids[:4]) or str(len(outcomes))
    contribution_count = len(local_report_ids)
    message = f"Обновлено: {updated}."
    if contribution_count:
        message += (
            f" Учтено ваших пожеланий: {contribution_count}. "
            "Благодарим за вклад в развитие."
        )
    from adaos.services.platform_notifications import append_platform_notification

    fingerprint = hashlib.sha256(
        "\0".join(
            [
                str(result.get("run_id") or ""),
                *sorted(app_ids),
                *sorted(local_report_ids),
            ]
        ).encode("utf-8")
    ).hexdigest()[:20]
    ctx = get_ctx()
    append_platform_notification(
        webspace_id=str(result.get("webspace_id") or "desktop"),
        item={
            "id": f"notification:application-update:{fingerprint}",
            "level": "success",
            "message": message,
            "source": "applications.auto_update",
            "code": "applications_updated",
            "target_kind": "applications",
            "details": {
                "application_ids": app_ids,
                "addressed_report_ids": sorted(local_report_ids),
                "contribution_count": contribution_count,
            },
            "ts": result.get("updated_at") or time.time(),
        },
        bus=ctx.bus,
    )


def _startup_delay_s() -> float:
    try:
        value = float(os.getenv("ADAOS_APPLICATION_AUTO_UPDATE_DELAY_S", "2.0"))
    except Exception:
        value = 2.0
    return max(0.0, min(value, 300.0))


async def _run_updates(initial_trigger: str) -> None:
    global _PENDING_TRIGGER, _TASK
    trigger = initial_trigger
    if trigger == "sys.ready":
        await asyncio.sleep(_startup_delay_s())
    try:
        while True:
            _PENDING_TRIGGER = None
            result = await asyncio.to_thread(sync_workspace_sparse_to_registry, get_ctx())
            auto_update = (
                result.get("application_auto_update")
                if isinstance(result, dict)
                else None
            )
            if isinstance(auto_update, dict):
                _publish_contribution_notification(auto_update)
            try:
                get_ctx().bus.publish(
                    Event(
                        type="applications.auto_update.completed",
                        payload={
                            "trigger": trigger,
                            "workspace_sync_ok": bool(
                                isinstance(result, dict) and result.get("ok")
                            ),
                            "result": auto_update
                            if isinstance(auto_update, dict)
                            else {},
                        },
                        source="applications.auto_update_runtime",
                        ts=time.time(),
                    )
                )
            except Exception:
                _LOG.debug("failed to publish Application auto-update result", exc_info=True)
            pending = _PENDING_TRIGGER
            if not pending:
                break
            trigger = pending
    except Exception:
        _LOG.warning(
            "Application registry synchronization failed trigger=%s",
            trigger,
            exc_info=True,
        )
    finally:
        _TASK = None


def request_application_registry_sync(trigger: str) -> bool:
    """Coalesce update notices without delaying the event publisher."""

    global _PENDING_TRIGGER, _TASK
    token = str(trigger or "registry_event").strip() or "registry_event"
    _PENDING_TRIGGER = token
    if _TASK is not None and not _TASK.done():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _LOG.debug("Application registry sync ignored without a running event loop")
        return False
    _TASK = loop.create_task(
        _run_updates(token),
        name="application-registry-auto-update",
    )
    return True


@subscribe("sys.ready")
async def on_runtime_ready(_event: Any) -> None:
    request_application_registry_sync("sys.ready")


@subscribe("applications.registry.updated")
async def on_application_registry_updated(_event: Any) -> None:
    request_application_registry_sync("applications.registry.updated")


__all__ = [
    "on_application_registry_updated",
    "on_runtime_ready",
    "request_application_registry_sync",
]
