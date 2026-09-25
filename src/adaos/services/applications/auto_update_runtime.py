"""Post-readiness and registry-event runtime for Application auto updates."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from adaos.domain import Event
from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.workspace_sync import sync_workspace_sparse_to_registry


_LOG = logging.getLogger("adaos.applications.auto_update")
_TASK: asyncio.Task[Any] | None = None
_PENDING_TRIGGER: str | None = None


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
