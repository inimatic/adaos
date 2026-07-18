"""SDK facade for Builder workbench and scenario preview lifecycle."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.workbench import BuilderWorkbenchService

    return BuilderWorkbenchService.from_context()


def _plain(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _complete(awaitable: Any) -> tuple[Any, bool]:
    """Complete an awaitable synchronously or schedule it on the active loop."""

    if not inspect.isawaitable(awaitable):
        return awaitable, False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable), False
    loop.create_task(awaitable)
    return None, True


def dev_webspace_id(source_webspace_id: str | None = None) -> str:
    """Return the canonical development webspace for a source webspace."""

    from adaos.services.builder.workbench import dev_webspace_id_for_source

    return dev_webspace_id_for_source(source_webspace_id)


def get_binding(source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(_service().get_workspace_binding(source_webspace_id))


def set_active_draft(
    *,
    source_webspace_id: str = "desktop",
    active_draft_id: str | None = None,
    runtime_scenario_id: str | None = None,
    persist_projection: bool = True,
) -> dict[str, Any]:
    return _plain(
        _service().set_active_draft(
            source_webspace_id=source_webspace_id,
            active_draft_id=active_draft_id,
            runtime_scenario_id=runtime_scenario_id,
            persist_projection=persist_projection,
        )
    )


def ensure(
    source_webspace_id: str | None = None,
    *,
    active_draft_id: str | None = None,
    runtime_scenario_id: str | None = None,
    preview_state: Mapping[str, Any] | None = None,
    wait_for_rebuild: bool = False,
) -> dict[str, Any]:
    service = _service()
    result, scheduled = _complete(
        service.ensure_dev_webspace(
            source_webspace_id or "desktop",
            active_draft_id=active_draft_id,
            runtime_scenario_id=runtime_scenario_id,
            preview_state=preview_state,
            wait_for_rebuild=wait_for_rebuild,
        )
    )
    if scheduled:
        return {
            "ok": True,
            "scheduled": True,
            "source_webspace_id": source_webspace_id or "desktop",
            "dev_webspace_id": dev_webspace_id(source_webspace_id),
        }
    return _plain(result)


def select_project(
    object_type: str,
    object_id: str,
    *,
    source_webspace_id: str = "desktop",
    ensure_ready: bool = True,
    wait_for_rebuild: bool = False,
    publish_event: bool = True,
) -> dict[str, Any]:
    """Select a scenario for preview and optionally ensure its dev webspace."""

    kind = str(object_type or "").strip().lower().rstrip("s")
    project_id = str(object_id or "").strip()
    source = str(source_webspace_id or "").strip() or "desktop"
    if kind not in {"skill", "scenario"}:
        raise ValueError("object_type must be skill or scenario")
    if not project_id:
        raise ValueError("object_id is required")
    if kind != "scenario":
        return {
            "ok": True,
            "selected": False,
            "object_type": kind,
            "object_id": project_id,
            "source_webspace_id": source,
        }

    service = _service()
    binding = _plain(
        service.set_active_draft(
            source_webspace_id=source,
            active_draft_id=None,
            runtime_scenario_id=project_id,
            persist_projection=True,
        )
    )
    ensured: dict[str, Any] | None = None
    if ensure_ready:
        result, scheduled = _complete(
            service.ensure_dev_webspace(
                source,
                active_draft_id=None,
                runtime_scenario_id=project_id,
                wait_for_rebuild=wait_for_rebuild,
            )
        )
        ensured = (
            {"ok": True, "scheduled": True, "dev_webspace_id": dev_webspace_id(source)}
            if scheduled
            else _plain(result)
        )
    if publish_event:
        from adaos.sdk.data.events import publish

        publish(
            "builder.preview.selected",
            {
                "source_webspace_id": source,
                "object_type": "scenario",
                "object_id": project_id,
                "scenario_id": project_id,
            },
            source="sdk.builder.preview",
        )
    return {
        "ok": bool(binding.get("ok", True)),
        "selected": True,
        "object_type": "scenario",
        "object_id": project_id,
        "source_webspace_id": source,
        "dev_webspace_id": dev_webspace_id(source),
        "binding": binding,
        "ensure": ensured,
    }


def open_workspace(source_webspace_id: str | None = None, *, base_url: str | None = None) -> dict[str, Any]:
    return _plain(_service().open_dev_webspace(source_webspace_id, base_url=base_url))


# Compatibility operation names used by the existing Builder tool surface.
get_workspace_binding = get_binding
ensure_dev_webspace = ensure
open_dev_webspace = open_workspace


def snapshot(
    source_webspace_id: str | None = None,
    *,
    preview_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _plain(_service().snapshot(source_webspace_id, preview_state=preview_state))


def dialog_widget_config(source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(_service().dialog_widget_config(source_webspace_id))


def list_development_skills(source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(_service().list_development_skills(source_webspace_id))


def delete_development_skill(draft_id: str, source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(_service().delete_development_skill(draft_id, source_webspace_id))


def reload(
    webspace_id: str,
    scenario_id: str,
    *,
    action: str = "sdk.builder.preview.reload",
) -> dict[str, Any]:
    from adaos.services.scenario.webspace_runtime import reload_webspace_from_scenario

    result, scheduled = _complete(
        reload_webspace_from_scenario(webspace_id=webspace_id, scenario_id=scenario_id, action=action)
    )
    return {"ok": True, "scheduled": True} if scheduled else _plain(result)


async def reload_async(
    webspace_id: str,
    scenario_id: str,
    *,
    action: str = "sdk.builder.preview.reload",
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from adaos.services.scenario.webspace_runtime import reload_webspace_from_scenario

    return _plain(
        await reload_webspace_from_scenario(
            webspace_id,
            scenario_id=scenario_id,
            action=action,
            event_payload=event_payload,
        )
    )


def materialize_revision(**kwargs: Any) -> dict[str, Any]:
    """Apply one validated Builder revision through the core materializer."""

    from adaos.services.scenario.webspace_runtime import apply_builder_revision_materialization

    result, scheduled = _complete(apply_builder_revision_materialization(**kwargs))
    return {"ok": True, "scheduled": True} if scheduled else _plain(result)


async def materialize_revision_async(webspace_id: str, **kwargs: Any) -> dict[str, Any]:
    from adaos.services.scenario.webspace_runtime import apply_builder_revision_materialization

    return _plain(await apply_builder_revision_materialization(webspace_id, **kwargs))


def invalidate_scenario_caches(scenario_id: str, *, reason: str = "sdk.builder.preview") -> None:
    """Invalidate loader and resolved-webspace caches after DEV file writes."""

    from adaos.services.scenarios import loader as scenarios_loader

    scenarios_loader.invalidate_cache(scenario_id=scenario_id, space="dev")
    scenarios_loader.invalidate_cache(scenario_id=scenario_id, space="workspace")
    from adaos.services.scenario import webspace_runtime

    invalidate = getattr(webspace_runtime, "_invalidate_resolved_webspace_cache", None)
    if callable(invalidate):
        invalidate(scenario_id=scenario_id, reason=reason)


__all__ = [
    "dev_webspace_id",
    "delete_development_skill",
    "dialog_widget_config",
    "ensure",
    "ensure_dev_webspace",
    "get_binding",
    "get_workspace_binding",
    "invalidate_scenario_caches",
    "list_development_skills",
    "materialize_revision",
    "materialize_revision_async",
    "open_workspace",
    "open_dev_webspace",
    "reload",
    "reload_async",
    "select_project",
    "set_active_draft",
    "snapshot",
]
