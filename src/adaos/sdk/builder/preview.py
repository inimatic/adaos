"""SDK facade for Builder workbench and scenario preview lifecycle."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any

from adaos.domain.project_events import BUILDER_CONTEXT_SELECTED, BUILDER_PREVIEW_DESIRED


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


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def dev_webspace_id(source_webspace_id: str | None = None) -> str:
    """Return the explicitly paired preview webspace for a Builder host."""

    binding = _plain(_service().get_workspace_binding(canonical_source_webspace_id(source_webspace_id)))
    return str(binding.get("preview_webspace_id") or binding.get("dev_webspace_id") or "").strip()


def canonical_source_webspace_id(webspace_id: str | None = None) -> str:
    """Resolve the Builder host using explicit relation topology."""

    service = _service()
    resolve = getattr(service, "resolve_source_webspace_id", None)
    if callable(resolve):
        return str(resolve(webspace_id)).strip()
    return str(webspace_id or "desktop").strip() or "desktop"


def action_source_webspace_id(
    webspace_id: str | None = None,
    *,
    current_scenario_id: str | None = None,
) -> str:
    """Resolve the host for a UI action, including bounded Builder self-hosting."""

    service = _service()
    resolve = getattr(service, "resolve_action_source_webspace_id", None)
    if callable(resolve):
        return str(
            resolve(
                webspace_id,
                current_scenario_id=current_scenario_id,
            )
        ).strip()
    return canonical_source_webspace_id(webspace_id)


def get_binding(source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(_service().get_workspace_binding(canonical_source_webspace_id(source_webspace_id)))


def list_builder_hosts() -> list[dict[str, Any]]:
    """List active Builder Webspaces without provisioning Preview topology."""

    return [dict(item) for item in _service().list_builder_hosts()]


def resolve_builder_context(
    builder_webspace_id: str,
    *,
    require_ready: bool = True,
) -> dict[str, Any]:
    """Resolve an explicit Builder host and its unique Preview Webspace."""

    return _plain(
        _service().resolve_builder_context(
            builder_webspace_id,
            require_ready=require_ready,
        )
    )


def set_active_draft(
    *,
    source_webspace_id: str = "desktop",
    active_draft_id: str | None = None,
    runtime_scenario_id: str | None = None,
    persist_projection: bool = True,
) -> dict[str, Any]:
    return _plain(
        _service().set_active_draft(
            source_webspace_id=canonical_source_webspace_id(source_webspace_id),
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
    source = canonical_source_webspace_id(source_webspace_id)
    service = _service()
    effective_wait = bool(wait_for_rebuild or not _has_running_loop())
    result, scheduled = _complete(
        service.ensure_dev_webspace(
            source,
            active_draft_id=active_draft_id,
            runtime_scenario_id=runtime_scenario_id,
            preview_state=preview_state,
            wait_for_rebuild=effective_wait,
        )
    )
    if scheduled:
        return {
            "ok": True,
            "scheduled": True,
            "source_webspace_id": source,
            "dev_webspace_id": dev_webspace_id(source),
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
    """Persist Builder context and request scenario preview reconciliation."""

    kind = str(object_type or "").strip().lower().rstrip("s")
    project_id = str(object_id or "").strip()
    source = canonical_source_webspace_id(source_webspace_id)
    if kind not in {"skill", "scenario"}:
        raise ValueError("object_type must be skill or scenario")
    if not project_id:
        raise ValueError("object_id is required")
    try:
        from adaos.sdk.developer import projects

        metadata = _plain(projects.describe(kind, project_id))
    except Exception:
        metadata = {}
    title = str(metadata.get("title") or metadata.get("name") or project_id).strip() or project_id
    description = str(metadata.get("description") or "").strip()
    service = _service()
    if kind != "scenario":
        binding = _plain(
            service.set_selected_project(
                source_webspace_id=source,
                object_type=kind,
                object_id=project_id,
                title=title,
                description=description,
                persist_projection=False,
            )
        )
        result = {
            "ok": True,
            "selected": True,
            "object_type": kind,
            "object_id": project_id,
            "source_webspace_id": source,
            "binding": binding,
        }
        if publish_event:
            from adaos.sdk.data.events import publish

            publish(
                BUILDER_CONTEXT_SELECTED,
                {
                    "source_webspace_id": source,
                    "project_kind": kind,
                    "project_id": project_id,
                    "object_type": kind,
                    "object_id": project_id,
                    "title": title,
                    "description": description,
                },
                source="sdk.builder.preview",
            )
        return result

    binding = _plain(
        service.set_active_draft(
            source_webspace_id=source,
            active_draft_id=None,
            runtime_scenario_id=project_id,
            persist_projection=not ensure_ready,
        )
    )
    binding = _plain(
        service.set_selected_project(
            source_webspace_id=source,
            object_type="scenario",
            object_id=project_id,
            title=title,
            description=description,
            persist_projection=False,
        )
    )
    ensured: dict[str, Any] | None = None
    deferred_to_event = bool(ensure_ready and publish_event)
    if ensure_ready and not deferred_to_event:
        effective_wait = bool(wait_for_rebuild or not _has_running_loop())
        result, scheduled = _complete(
            service.ensure_dev_webspace(
                source,
                active_draft_id=None,
                runtime_scenario_id=project_id,
                wait_for_rebuild=effective_wait,
            )
        )
        ensured = (
            {"ok": True, "scheduled": True, "dev_webspace_id": dev_webspace_id(source)}
            if scheduled
            else _plain(result)
        )
    elif deferred_to_event:
        ensured = {
            "ok": True,
            "scheduled": True,
            "via": BUILDER_PREVIEW_DESIRED,
            "runtime_scenario_id": project_id,
            "dev_webspace_id": str(
                binding.get("preview_webspace_id") or binding.get("dev_webspace_id") or ""
            ).strip(),
        }
    preview_id = str(
        (ensured or {}).get("preview_webspace_id")
        or (ensured or {}).get("dev_webspace_id")
        or binding.get("preview_webspace_id")
        or binding.get("dev_webspace_id")
        or dev_webspace_id(source)
        or ""
    ).strip()
    if publish_event:
        from adaos.sdk.data.events import publish

        publish(
            BUILDER_CONTEXT_SELECTED,
            {
                "source_webspace_id": source,
                "project_kind": "scenario",
                "project_id": project_id,
                "object_type": "scenario",
                "object_id": project_id,
                "title": title,
                "description": description,
            },
            source="sdk.builder.preview",
        )
        publish(
            BUILDER_PREVIEW_DESIRED,
            {
                "source_webspace_id": source,
                "preview_webspace_id": preview_id,
                "object_type": "scenario",
                "object_id": project_id,
                "scenario_id": project_id,
                "reconciled": False,
                "wait_for_rebuild": False,
            },
            source="sdk.builder.preview",
        )
    return {
        "ok": bool(binding.get("ok", True)),
        "selected": True,
        "object_type": "scenario",
        "object_id": project_id,
        "source_webspace_id": source,
        "dev_webspace_id": preview_id,
        "preview_webspace_id": preview_id,
        "binding": binding,
        "ensure": ensured,
    }


def select_target(
    object_type: str,
    object_id: str,
    *,
    stage: str,
    revision: str | None = None,
    source_webspace_id: str = "desktop",
    follow_active: bool = False,
) -> dict[str, Any]:
    """Materialize an explicit Lifecycle snapshot without changing its active phase."""

    kind = str(object_type or "").strip().lower().rstrip("s")
    project_id = str(object_id or "").strip()
    if kind != "scenario":
        raise ValueError("only scenario Lifecycle nodes can be shown in Preview")
    if not project_id:
        raise ValueError("object_id is required")
    source = canonical_source_webspace_id(source_webspace_id)

    from adaos.services.builder.workflow import BuilderWorkflowService

    workflow = BuilderWorkflowService.from_context().describe(kind, project_id)
    stage_token = str(stage or "").strip().lower()
    if follow_active:
        stage_token = str(workflow.get("active_phase") or "prototype")
    if stage_token not in {"prototype", "automation", "publication"}:
        raise ValueError("stage must be prototype, automation, or publication")
    capability = {
        "prototype": "can_preview_prototype",
        "automation": "can_preview_automation",
        "publication": "can_preview_publication",
    }[stage_token]
    if not bool(_plain(workflow.get("capabilities")).get(capability)):
        raise ValueError(f"{stage_token} Preview is not available for this project")

    prototype = _plain(workflow.get("prototype"))
    automation_state = _plain(workflow.get("automation"))
    publication = _plain(workflow.get("publication"))
    target_revision = str(revision or "").strip()
    display_revision = ""
    if stage_token == "prototype" and not target_revision:
        target_revision = str(prototype.get("head_revision") or "").strip()
        display_revision = f"UI {target_revision}" if target_revision else "current"
    elif stage_token == "prototype":
        display_revision = f"UI {target_revision}"
    elif stage_token == "automation":
        current_automation = str(
            automation_state.get("snapshot_task_id") or automation_state.get("head_task_id") or "current"
        ).strip() or "current"
        current_automation_version = str(automation_state.get("result_version") or "").strip()
        accepted_automation_revisions = {
            value
            for value in (current_automation, current_automation_version)
            if value
        }
        if target_revision and target_revision not in accepted_automation_revisions:
            raise ValueError("only the current Automation result can be shown in Preview")
        target_revision = current_automation
        display_revision = current_automation_version or "current"
    elif stage_token == "publication":
        current_publication = str(publication.get("current_version") or "current").strip() or "current"
        if target_revision and target_revision != current_publication:
            raise ValueError("only the current Publication can be shown in Preview")
        target_revision = current_publication
        display_revision = current_publication

    selected = select_project(
        kind,
        project_id,
        source_webspace_id=source,
        ensure_ready=True,
        wait_for_rebuild=True,
        publish_event=False,
    )
    # ``select_target`` materializes the requested snapshot itself, so it must
    # not publish ``builder.preview.desired`` and schedule a second reconcile.
    # Project selection is a separate projection, however: Builder hosts
    # consume ``data/builder/selection`` through Yjs and need the context event
    # even when preview materialization is synchronous.
    selected_binding = _plain(selected.get("binding"))
    selected_context = _plain(selected_binding.get("selection"))
    from adaos.sdk.data.events import publish

    publish(
        BUILDER_CONTEXT_SELECTED,
        {
            "source_webspace_id": source,
            "project_kind": kind,
            "project_id": project_id,
            "object_type": kind,
            "object_id": project_id,
            "title": str(selected_context.get("title") or project_id).strip() or project_id,
            "description": str(selected_context.get("description") or "").strip(),
        },
        source="sdk.builder.preview.select_target",
    )
    preview_id = str(selected.get("preview_webspace_id") or selected.get("dev_webspace_id") or "").strip()
    if not preview_id:
        raise RuntimeError("Builder preview relation is missing")
    prefix = {"prototype": "proto", "automation": "active", "publication": "public"}[stage_token]
    label = f"{prefix}: {project_id} · {display_revision or target_revision or 'current'}"
    materialized = materialize_revision(
        webspace_id=preview_id,
        scenario_id=project_id,
        revision=target_revision or None,
        preview_stage=stage_token,
        preview_label=label,
        event_payload={
            "source": "sdk.builder.preview.select_target",
            "source_webspace_id": source,
            "preview_stage": stage_token,
            "preview_revision": target_revision or None,
        },
    )
    target = {
        "schema": "adaos.builder.preview_target.v1",
        "object_type": kind,
        "object_id": project_id,
        "stage": stage_token,
        "revision": target_revision or None,
        "label": label,
        "follow_active": bool(follow_active),
    }
    binding = _plain(_service().set_preview_target(source_webspace_id=source, target=target))
    return {
        "ok": bool(materialized.get("ok", True)),
        "target": target,
        "materialization": materialized,
        "binding": binding,
        "preview_webspace_id": preview_id,
    }


def refresh_follow_active_target(
    object_type: str,
    object_id: str,
    *,
    revision: str,
    source_webspace_id: str = "desktop",
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Advance a follow-active Prototype target without rebuilding Preview inline."""

    kind = str(object_type or "").strip().lower().rstrip("s")
    project_id = str(object_id or "").strip()
    revision_token = str(revision or "").strip()
    if kind != "scenario":
        raise ValueError("only scenario Lifecycle nodes can be shown in Preview")
    if not project_id or not revision_token:
        raise ValueError("object_id and revision are required")
    source = canonical_source_webspace_id(source_webspace_id)
    service = _service()
    current_binding = _plain(service.get_workspace_binding(source))
    current_target = _plain(current_binding.get("preview_target"))
    if not bool(current_target.get("follow_active")):
        return {"ok": True, "skipped": "preview_target_not_following_active", "binding": current_binding}
    if (
        str(current_target.get("object_type") or "").strip().lower().rstrip("s") != kind
        or str(current_target.get("object_id") or "").strip() != project_id
    ):
        return {"ok": True, "skipped": "preview_target_project_mismatch", "binding": current_binding}
    if str(current_target.get("stage") or "").strip().lower() != "prototype":
        return {"ok": True, "skipped": "follow_active_target_is_not_prototype", "binding": current_binding}

    current_selection = _plain(current_binding.get("selection"))
    selected_title = str(title or current_selection.get("title") or project_id).strip() or project_id
    selected_description = str(
        description if description is not None else current_selection.get("description") or ""
    ).strip()
    selected_binding = _plain(
        service.set_selected_project(
            source_webspace_id=source,
            object_type=kind,
            object_id=project_id,
            title=selected_title,
            description=selected_description,
            persist_projection=False,
        )
    )
    target = {
        **current_target,
        "schema": "adaos.builder.preview_target.v1",
        "object_type": kind,
        "object_id": project_id,
        "stage": "prototype",
        "revision": revision_token,
        "label": f"proto: {project_id} · UI {revision_token}",
        "follow_active": True,
    }
    binding = _plain(service.set_preview_target(source_webspace_id=source, target=target))
    return {
        "ok": bool(binding.get("ok", True)),
        "target": target,
        "binding": binding,
        "selection": _plain(selected_binding.get("selection")),
        "materialization": "deferred",
    }


def open_workspace(source_webspace_id: str | None = None, *, base_url: str | None = None) -> dict[str, Any]:
    source = canonical_source_webspace_id(source_webspace_id)
    return _plain(_service().open_dev_webspace(source, base_url=base_url))


def public_app_base() -> str:
    """Return the configured public AdaOS application origin for deep links."""

    try:
        from adaos.services.agent_context import get_ctx

        return str(get_ctx().settings.app_base or "https://inimatic.com").strip().rstrip("/")
    except Exception:
        return "https://inimatic.com"


def navigation_link(
    source_webspace_id: str | None = None,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Build a topology-aware Preview destination through Navigation SDK."""

    from adaos.sdk import navigation

    source = canonical_source_webspace_id(source_webspace_id)
    binding = get_binding(source)
    preview_webspace_id = str(
        binding.get("preview_webspace_id") or binding.get("dev_webspace_id") or ""
    ).strip()
    if not preview_webspace_id:
        raise RuntimeError("Builder preview relation is missing")
    target = _plain(binding.get("preview_target"))
    scope = navigation.runtime_scope()
    zone = str(scope.get("zone") or "").strip()
    subnet_id = str(scope.get("subnet_id") or "").strip()
    if not zone or not subnet_id:
        raise RuntimeError("Builder Preview navigation requires zone and subnet identity")
    object_type = str(target.get("object_type") or "").strip().lower().rstrip("s")
    destination = navigation.webspace_destination(
        zone=zone,
        subnet_id=subnet_id,
        webspace_id=preview_webspace_id,
        space_kind="development",
        expected_scenario_id=(
            str(target.get("object_id") or "").strip() or None
            if object_type == "scenario"
            else None
        ),
        expected_revision=str(target.get("revision") or "").strip() or None,
        preview_stage=str(target.get("stage") or "").strip() or None,
    )
    return {
        "schema": "adaos.builder.preview_navigation.v1",
        "url": navigation.build_url(destination, base_url=base_url or public_app_base()),
        "destination": destination,
        "preview_webspace_id": preview_webspace_id,
        "source_webspace_id": source,
        "target": target,
        "label": str(target.get("label") or "").strip() or f"preview: {preview_webspace_id}",
    }


# Compatibility operation names used by the existing Builder tool surface.
get_workspace_binding = get_binding
ensure_dev_webspace = ensure
open_dev_webspace = open_workspace


def snapshot(
    source_webspace_id: str | None = None,
    *,
    preview_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = canonical_source_webspace_id(source_webspace_id)
    return _plain(_service().snapshot(source, preview_state=preview_state))


def dialog_widget_config(source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(_service().dialog_widget_config(canonical_source_webspace_id(source_webspace_id)))


def list_development_skills(source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(_service().list_development_skills(canonical_source_webspace_id(source_webspace_id)))


def delete_development_skill(draft_id: str, source_webspace_id: str | None = None) -> dict[str, Any]:
    return _plain(
        _service().delete_development_skill(draft_id, canonical_source_webspace_id(source_webspace_id))
    )


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
    "list_builder_hosts",
    "list_development_skills",
    "materialize_revision",
    "materialize_revision_async",
    "navigation_link",
    "open_workspace",
    "public_app_base",
    "open_dev_webspace",
    "reload",
    "reload_async",
    "resolve_builder_context",
    "refresh_follow_active_target",
    "select_project",
    "select_target",
    "set_active_draft",
    "snapshot",
    "canonical_source_webspace_id",
    "action_source_webspace_id",
]
