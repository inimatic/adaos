"""Post-readiness and registry-event runtime for Application auto updates."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.domain import Event
from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.workspace_sync import sync_workspace_sparse_to_registry


_LOG = logging.getLogger("adaos.applications.auto_update")
_TASK: asyncio.Task[Any] | None = None
_PENDING_TRIGGER: str | None = None


def _runtime_selections_for_context(ctx: Any) -> tuple[Any, ...]:
    from adaos.services.applications.store import ApplicationStore

    return ApplicationStore(Path(ctx.paths.state_dir())).list_runtime_selections()


def _updated_application_ids(result: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(result, Mapping):
        return set()
    application_ids: set[str] = set()
    for outcome in result.get("outcomes") or ():
        if not isinstance(outcome, Mapping) or outcome.get("status") != "succeeded":
            continue
        application_id = str(outcome.get("application_id") or "").strip()
        if application_id:
            application_ids.add(application_id)
        operation_result = outcome.get("result")
        if not isinstance(operation_result, Mapping):
            continue
        for rebound in operation_result.get("shared_dependency_rebindings") or ():
            if not isinstance(rebound, Mapping):
                continue
            application_id = str(rebound.get("application_id") or "").strip()
            if application_id:
                application_ids.add(application_id)
    return application_ids


def _materialization_refresh_webspaces(
    ctx: Any,
    result: Mapping[str, Any] | None,
    *,
    recover_all_stable: bool,
) -> list[str]:
    """Return stable Webspaces whose persisted Application identity may be stale."""

    affected_applications = _updated_application_ids(result)
    if not recover_all_stable and not affected_applications:
        return []
    try:
        selections = _runtime_selections_for_context(ctx)
    except Exception:
        _LOG.warning("failed to inspect Application channels for materialization refresh", exc_info=True)
        return []
    return sorted(
        {
            str(selection.webspace_id).strip()
            for selection in selections
            if str(getattr(selection, "webspace_id", "") or "").strip()
            and str(getattr(selection, "source", "") or "").strip()
            == "stable_installation"
            and str(getattr(selection, "runtime_root_ref", "") or "").strip()
            == "workspace"
            and (
                recover_all_stable
                or str(getattr(selection, "application_id", "") or "").strip()
                in affected_applications
            )
        }
    )


async def _refresh_webspace_materialization(webspace_id: str) -> dict[str, Any]:
    from adaos.services.scenario.webspace_runtime import rebuild_webspace_from_sources

    return await rebuild_webspace_from_sources(
        webspace_id,
        action="runtime_selection_refresh",
        source_of_truth="application_auto_update",
        reseed_from_scenario=False,
    )


async def _reconcile_application_materializations(
    ctx: Any,
    result: Mapping[str, Any] | None,
    *,
    trigger: str,
) -> dict[str, Any]:
    """Make Yjs page authority follow committed stable Application channels.

    A core restart can happen after a runtime-channel cutover but before the
    affected Yjs document is rematerialized. On startup, reconcile every
    selected stable Webspace; for later registry events, limit the work to
    Applications advanced by that update run.
    """

    webspace_ids = await asyncio.to_thread(
        _materialization_refresh_webspaces,
        ctx,
        result,
        recover_all_stable=trigger == "sys.ready",
    )
    items: list[dict[str, Any]] = []
    for webspace_id in webspace_ids:
        try:
            refreshed = await _refresh_webspace_materialization(webspace_id)
            items.append(
                {
                    "webspace_id": webspace_id,
                    "ok": bool(refreshed.get("ok")),
                    "accepted": bool(refreshed.get("accepted")),
                    "scenario_id": str(refreshed.get("scenario_id") or "").strip()
                    or None,
                    "error": str(refreshed.get("error") or "").strip() or None,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning(
                "Application materialization refresh failed webspace=%s",
                webspace_id,
                exc_info=True,
            )
            items.append(
                {
                    "webspace_id": webspace_id,
                    "ok": False,
                    "accepted": False,
                    "scenario_id": None,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
    failed = sum(1 for item in items if not item["ok"] or not item["accepted"])
    return {
        "status": "failed" if failed else "completed" if items else "not_required",
        "trigger": trigger,
        "webspace_count": len(items),
        "refreshed_count": len(items) - failed,
        "failed_count": failed,
        "webspaces": items,
    }


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


def _development_workspace_preflight(ctx: Any) -> dict[str, Any] | None:
    """Keep automatic registry pulls away from active dev authoring trees."""

    environment = str(
        os.getenv("ENV_TYPE") or os.getenv("ADAOS_ENV_TYPE") or "prod"
    ).strip().lower()
    if environment != "dev":
        return None
    try:
        workspace_root = str(ctx.paths.workspace_dir())
        changed = sorted(
            {
                str(path).replace("\\", "/")
                for path in ctx.git.changed_files(workspace_root)
                if str(path).strip()
            }
        )
    except Exception:
        _LOG.debug("failed to inspect development workspace", exc_info=True)
        return None
    if not changed:
        return None
    return {
        "ok": True,
        "skipped": True,
        "reason": "dirty_development_workspace",
        "workspace_root": workspace_root,
        "changed_count": len(changed),
        "application_auto_update": {
            "schema": "adaos.application.auto_update_run.v1",
            "status": "skipped",
            "reason": "dirty_development_workspace",
            "changed_count": len(changed),
        },
    }


async def _run_updates(initial_trigger: str) -> None:
    global _PENDING_TRIGGER, _TASK
    trigger = initial_trigger
    if trigger == "sys.ready":
        await asyncio.sleep(_startup_delay_s())
    try:
        while True:
            _PENDING_TRIGGER = None
            ctx = get_ctx()
            result = await asyncio.to_thread(_development_workspace_preflight, ctx)
            if result is None:
                result = await asyncio.to_thread(sync_workspace_sparse_to_registry, ctx)
            auto_update = (
                result.get("application_auto_update")
                if isinstance(result, dict)
                else None
            )
            if isinstance(auto_update, dict):
                materialization_reconcile = await _reconcile_application_materializations(
                    ctx,
                    auto_update,
                    trigger=trigger,
                )
                auto_update["materialization_reconcile"] = materialization_reconcile
                if isinstance(result, dict):
                    result["post_application_materialization"] = materialization_reconcile
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
