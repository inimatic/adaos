from __future__ import annotations

import asyncio
import logging
from mimetypes import guess_type
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse
from starlette.requests import ClientDisconnect

from adaos.adapters.db import SqliteSkillRegistry
from adaos.apps.api.auth import require_token
from adaos.domain.node_identity import node_identities_match
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.skill.manager import SkillCoreCompatibilityError, SkillDependencyIsolationError, SkillManager
from adaos.services.skill.artifacts import (
    request_upload_metadata,
    resolve_skill_file_path,
    skill_upload_max_bytes,
    store_skill_upload,
)
from adaos.services.skill.update import SkillUpdateService
from adaos.services.artifact_subscription_update import (
    ArtifactSubscriptionUpdateCoordinator,
    ArtifactSubscriptionUpdateError,
)
from adaos.services.eventbus import emit as bus_emit
from adaos.services.operations import submit_install_operation
from adaos.services.runtime_refresh import RuntimeRefreshError, rebuild_webspace_projection, refresh_skill_runtime
from adaos.services.runtime_activation_observations import (
    emit_runtime_activation_failure,
    emit_runtime_activation_success,
)
from adaos.services.skill.runtime_migration_worker import (
    read_status as read_skill_runtime_migration_status,
    start_background_migration,
)
from adaos.services.capacity import invalidate_local_capacity_cache
from adaos.services.scenario.webspace_runtime import invalidate_webspace_materialization_cache
from adaos.services.skills_loader_importlib import ImportlibSkillsLoader
from adaos.services.workspace_registry import build_registry_entry, find_workspace_registry_entry, list_workspace_registry_entries
from adaos.services.yjs.webspace import default_webspace_id

from packaging.version import Version, InvalidVersion


router = APIRouter(tags=["skills"], dependencies=[Depends(require_token)])
log = logging.getLogger(__name__)
_BACKGROUND_REBUILD_TASKS: set[asyncio.Task[Any]] = set()


def _get_manager(ctx: AgentContext = Depends(get_ctx)) -> SkillManager:
    repo = ctx.skills_repo
    registry = SqliteSkillRegistry(ctx.sql)
    return SkillManager(
        repo=repo,
        registry=registry,
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )


def _to_mapping(obj: Any) -> Dict[str, Any]:
    try:
        return dict(obj)
    except Exception:
        pass
    try:
        return obj._asdict()  # type: ignore[attr-defined]
    except Exception:
        pass
    data: Dict[str, Any] = {}
    for key in ("name", "pin", "last_updated", "id", "path", "version", "active_version"):
        if hasattr(obj, key):
            value = getattr(obj, key)
            if key == "id" and hasattr(value, "value"):
                value = getattr(value, "value")
            data[key] = value
    return data or {"repr": repr(obj)}


def _schedule_webspace_rebuild(
    *,
    webspace_id: str,
    action: str,
    source_of_truth: str,
    reason: str,
) -> dict[str, Any]:
    try:
        from adaos.services.scenario.webspace_runtime import schedule_skill_runtime_rebuild

        return schedule_skill_runtime_rebuild(
            webspace_id=webspace_id,
            action=action,
            source_of_truth=source_of_truth,
            reason=reason,
        )
    except Exception:
        log.exception("failed to schedule coalesced webspace rebuild reason=%s webspace=%s", reason, webspace_id)

    async def _runner() -> None:
        try:
            await rebuild_webspace_projection(
                webspace_id=webspace_id,
                action=action,
                source_of_truth=source_of_truth,
            )
        except Exception:
            log.exception("background webspace rebuild failed reason=%s webspace=%s", reason, webspace_id)

    try:
        task = asyncio.create_task(_runner(), name=f"adaos-webspace-rebuild:{action}:{webspace_id}")
        _BACKGROUND_REBUILD_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_REBUILD_TASKS.discard)
        return {
            "scheduled": True,
            "mode": "background",
            "webspace_id": webspace_id,
            "action": action,
            "source_of_truth": source_of_truth,
            "task": task.get_name(),
        }
    except Exception as exc:
        log.exception("failed to schedule webspace rebuild reason=%s webspace=%s", reason, webspace_id)
        return {
            "scheduled": False,
            "mode": "background",
            "webspace_id": webspace_id,
            "action": action,
            "source_of_truth": source_of_truth,
            "error": f"{type(exc).__name__}: {exc}",
        }


class UpdateReq(BaseModel):
    name: str
    dry_run: bool = False
    webspace_id: str | None = None
    defer_webspace_rebuild: bool = False
    force: bool | None = None
    expected_plan_digest: str | None = None
    permission_decision: dict[str, Any] | None = None
    idempotency_key: str | None = None


class UninstallReq(BaseModel):
    name: str
    webspace_id: str | None = None
    force: bool = False


class SyncReq(BaseModel):
    force: bool | None = None


class RuntimeMigrationStartReq(BaseModel):
    name: str | None = None
    webspace_id: str | None = None
    force: bool = False
    run_tests: bool = True
    sync_workspace: bool = True
    reason: str = "api"


def _safe_version(v: Any) -> Version | None:
    if v is None:
        return None
    raw = str(v).strip()
    if not raw:
        return None
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _read_registry_catalog_version(ctx: AgentContext, *, skill_id: str) -> str | None:
    entry = find_workspace_registry_entry(
        Path(ctx.paths.workspace_dir()),
        kind="skills",
        name_or_id=skill_id,
        fallback_to_scan=False,
    )
    if not isinstance(entry, dict):
        return None
    version = entry.get("version")
    if version is None:
        return None
    token = str(version).strip()
    return token or None


async def _reload_live_skill_handlers(ctx: AgentContext, skill_name: str) -> dict[str, Any]:
    try:
        return await ImportlibSkillsLoader().reload_skill_handlers(ctx.paths.skills_dir(), skill_name)
    except Exception as exc:
        log.debug("live skill handler reload failed skill=%s: %s", skill_name, exc, exc_info=True)
        return {"ok": False, "reason": "reload_failed", "error": str(exc)}


def _skill_activation_payload(
    skill_name: str,
    *,
    space: str,
    webspace_id: str,
    defer_webspace_rebuild: bool,
) -> Dict[str, Any]:
    return {
        "skill_name": skill_name,
        "space": space,
        "webspace_id": webspace_id,
        "defer_webspace_rebuild": bool(defer_webspace_rebuild),
    }


def _emit_live_skill_activation(
    ctx: AgentContext,
    skill_name: str,
    *,
    space: str,
    webspace_id: str,
    defer_webspace_rebuild: bool,
) -> None:
    bus = getattr(ctx, "bus", None)
    if bus is None:
        raise HTTPException(status_code=503, detail="skill activation event bus is unavailable")
    bus_emit(
        bus,
        "skills.activated",
        _skill_activation_payload(
            skill_name,
            space=space,
            webspace_id=webspace_id,
            defer_webspace_rebuild=defer_webspace_rebuild,
        ),
        "api.skills",
    )


async def _finalize_live_skill_activation(
    ctx: AgentContext,
    skill_name: str,
    *,
    space: str,
    webspace_id: str,
    defer_webspace_rebuild: bool,
    cache_reason: str,
    cache_action: str,
    emit_activation: bool = True,
    observation_source: str = "api.skills.runtime_activation",
    observation_policy: str = "project_inbox",
) -> dict[str, Any]:
    reload_result = await _reload_live_skill_handlers(ctx, skill_name)
    if not bool(reload_result.get("ok")):
        emit_runtime_activation_failure(
            getattr(ctx, "bus", None),
            component_type="skill",
            component_id=skill_name,
            stage="handler_reload",
            error=str(reload_result.get("error") or reload_result.get("reason") or "reload failed"),
            source=observation_source,
            report_policy=observation_policy,
            space=space,
            webspace_id=webspace_id,
            operation_id=cache_reason,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "skill_handler_reload_failed",
                "message": f"active runtime handlers did not reload for {skill_name}",
                "skill_name": skill_name,
                "handler_reload": reload_result,
            },
        )
    try:
        materialization_cache = await asyncio.to_thread(
            invalidate_webspace_materialization_cache,
            webspace_id,
            reason=cache_reason,
            action=cache_action,
            source_of_truth="skill_runtime",
        )
    except Exception as exc:
        emit_runtime_activation_failure(
            getattr(ctx, "bus", None),
            component_type="skill",
            component_id=skill_name,
            stage="materialization",
            error=f"{type(exc).__name__}: {exc}",
            source=observation_source,
            report_policy=observation_policy,
            space=space,
            webspace_id=webspace_id,
            operation_id=cache_reason,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "skill_materialization_invalidation_failed",
                "message": f"runtime materialization did not invalidate for {skill_name}",
                "skill_name": skill_name,
                "error": str(exc),
            },
        ) from exc
    if emit_activation:
        _emit_live_skill_activation(
            ctx,
            skill_name,
            space=space,
            webspace_id=webspace_id,
            defer_webspace_rebuild=defer_webspace_rebuild,
        )
    return {
        "handler_reload": reload_result,
        "materialization_cache": materialization_cache,
    }


def _clean_version_text(value: object | None) -> str | None:
    token = str(value or "").strip()
    return token or None


def _repo_workspace_skills_root(ctx: AgentContext) -> Path | None:
    try:
        repo_root_attr = getattr(ctx.paths, "repo_root", None)
        repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
        if not repo_root:
            return None
        candidate = Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "skills"
        if candidate.exists():
            return candidate
    except Exception:
        return None
    return None


def _ctx_path(ctx: AgentContext, attr_name: str) -> Path | None:
    try:
        attr = getattr(ctx.paths, attr_name, None)
        value = attr() if callable(attr) else attr
        if value:
            return Path(value).expanduser().resolve()
    except Exception:
        return None
    return None


def _workspace_skill_manifest_exists(ctx: AgentContext, skill_name: str) -> bool:
    token = str(skill_name or "").strip()
    if not token:
        return False

    roots: list[Path] = []
    for attr_name in ("skills_workspace_dir", "skills_dir"):
        root = _ctx_path(ctx, attr_name)
        if root is not None and root not in roots:
            roots.append(root)

    repo_root = _repo_workspace_skills_root(ctx)
    if repo_root is not None and repo_root not in roots:
        roots.append(repo_root)

    if not roots:
        return True
    return any((root / token / "skill.yaml").is_file() for root in roots)


def _resolve_workspace_skill_source(ctx: AgentContext, skill_name: str, workspace_root: Path, workspace_skills_root: Path) -> Path:
    local_path = (workspace_skills_root / skill_name).resolve()
    if local_path.exists():
        return local_path
    repo_root = _repo_workspace_skills_root(ctx)
    if repo_root is not None:
        repo_path = (repo_root / skill_name).resolve()
        if repo_path.exists():
            return repo_path
    return local_path


def _read_local_artifact_version(kind: str, artifact_dir: Path) -> str | None:
    try:
        entry = build_registry_entry(kind, artifact_dir)
    except Exception:
        entry = None
    if not isinstance(entry, dict):
        return None
    return _clean_version_text(entry.get("version"))


def _resolve_list_skill_version(
    *,
    ctx: AgentContext,
    skill_name: str,
    row_version: object | None,
    registry_meta: dict[str, Any] | None,
) -> str:
    workspace_root = Path(ctx.paths.workspace_dir())
    workspace_skills_root = Path(ctx.paths.skills_workspace_dir())
    source_path = _resolve_workspace_skill_source(ctx, skill_name, workspace_root, workspace_skills_root)
    workspace_version = _read_local_artifact_version("skills", source_path)
    if not workspace_version and isinstance(registry_meta, dict):
        workspace_version = _clean_version_text(registry_meta.get("version"))
    return workspace_version or _clean_version_text(row_version) or "unknown"


class InstallReq(BaseModel):
    name: str
    pin: Optional[str] = None
    perform_validation: bool = True
    strict: bool = True
    probe_tools: bool = False
    async_operation: bool = False
    webspace_id: str | None = None


class PushReq(BaseModel):
    name: str
    message: str
    signoff: bool = False


# --- Runtime management API ---
class RuntimePrepareReq(BaseModel):
    name: str
    run_tests: bool = False
    slot: str | None = None
    allow_deactivated: bool = False


class RuntimeActivateReq(BaseModel):
    name: str
    slot: str | None = None
    version: str | None = None
    auto_prepare: bool = True
    webspace_id: str | None = "default"


class RuntimeNotifyActivatedReq(BaseModel):
    name: str
    space: str | None = "default"
    webspace_id: str | None = None
    defer_webspace_rebuild: bool = False


class RuntimeRebuildWebspaceReq(BaseModel):
    webspace_id: str | None = None


class RuntimeSetupReq(BaseModel):
    name: str


@router.get("/list")
async def list_skills(
    fs: bool = False,
    mgr: SkillManager = Depends(_get_manager),
    ctx: AgentContext = Depends(get_ctx),
):
    return await asyncio.to_thread(_list_skills_sync, fs=fs, mgr=mgr, ctx=ctx)


def _list_skills_sync(*, fs: bool, mgr: SkillManager, ctx: AgentContext) -> Dict[str, Any]:
    rows = mgr.list_installed()
    workspace_registry_by_name: dict[str, dict[str, Any]] = {}
    try:
        registry_items = list_workspace_registry_entries(Path(ctx.paths.workspace_dir()), kind="skills", fallback_to_scan=True)
    except Exception:
        registry_items = []
    for item in registry_items:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("name") or item.get("id") or "").strip()
        if item_name:
            workspace_registry_by_name[item_name] = item

    items = []
    for row in (rows or []):
        if not bool(getattr(row, "installed", True)):
            continue
        item = _to_mapping(row)
        name = str(item.get("name") or item.get("id") or item.get("repr") or "").strip()
        if name and not _workspace_skill_manifest_exists(ctx, name):
            log.error(
                "installed skill hidden: required declaration is missing name=%s required=skill.yaml",
                name,
            )
            continue
        if name:
            item["version"] = _resolve_list_skill_version(
                ctx=ctx,
                skill_name=name,
                row_version=item.get("active_version") or item.get("version"),
                registry_meta=workspace_registry_by_name.get(name),
            )
        items.append(item)
    result: Dict[str, Any] = {"items": items}
    if fs:
        present = {m.id.value for m in mgr.list_present()}
        desired = {(i.get("name") or i.get("id") or i.get("repr")) for i in items}
        missing = sorted(desired - present)
        extra = sorted(present - desired)
        result["fs"] = {
            "present": sorted(present),
            "missing": missing,
            "extra": extra,
        }
    return result


@router.get("/installed-status")
async def installed_status(mgr: SkillManager = Depends(_get_manager), ctx: AgentContext = Depends(get_ctx)):
    """
    Installed skills with runtime slot and update hint (remote version > local version).
    """
    return await asyncio.to_thread(_installed_status_sync, mgr=mgr, ctx=ctx)


def _installed_status_sync(*, mgr: SkillManager, ctx: AgentContext) -> dict[str, Any]:
    rows = mgr.list_installed()
    items: list[dict[str, Any]] = []

    for row in (rows or []):
        if not bool(getattr(row, "installed", True)):
            continue
        name = str(getattr(row, "name", "") or "").strip()
        if not name:
            continue
        if not _workspace_skill_manifest_exists(ctx, name):
            log.error(
                "installed skill status hidden: required declaration is missing name=%s required=skill.yaml",
                name,
            )
            continue

        meta = mgr.get(name)
        local_version = (getattr(meta, "version", None) if meta else None) or getattr(row, "active_version", None)
        local_version_s = str(local_version).strip() if local_version is not None else ""

        slot = ""
        try:
            st = mgr.runtime_status(name)
            slot = str(st.get("active_slot") or "").strip()
        except Exception:
            slot = ""

        remote_version_s = _read_registry_catalog_version(ctx, skill_id=name) or ""

        update_available = False
        lv = _safe_version(local_version_s)
        rv = _safe_version(remote_version_s)
        if lv is not None and rv is not None and rv > lv:
            update_available = True

        items.append(
            {
                "name": name,
                "version": local_version_s,
                "slot": slot,
                "remote_version": remote_version_s,
                "update_available": update_available,
            }
        )

    return {"ok": True, "items": items}


@router.post("/sync")
async def sync(body: SyncReq | None = None, mgr: SkillManager = Depends(_get_manager)):
    try:
        await asyncio.to_thread(mgr.sync, force=(body.force if body is not None else None))
    except Exception as exc:
        # Surface the failure as a structured client error instead of a 500.
        # Common causes: dirty workspace, git remote/upstream misconfiguration, or merge conflicts.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


def _install_skill_sync(body: InstallReq, mgr: SkillManager, webspace_id: str) -> Dict[str, Any]:
    preflight = getattr(mgr, "ensure_standalone_mutation_allowed", None)
    if callable(preflight):
        try:
            preflight(body.name, operation="skill install")
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Best-effort sync to ensure monorepo workspace exists
    sync_error: Exception | None = None
    try:
        mgr.sync()
    except Exception as exc:
        # We may still be able to install if the skill is already materialized locally.
        # Keep the error to surface it if we later discover that the skill is missing.
        sync_error = exc
    try:
        result = mgr.install(
            body.name,
            pin=body.pin,
            validate=body.perform_validation,
            strict=body.strict,
            probe_tools=body.probe_tools,
        )
    except FileNotFoundError:
        # Retry once after an explicit sync in case the repo was missing.
        # If the best-effort sync already failed, surface that as a client error.
        if sync_error is not None:
            raise HTTPException(status_code=409, detail=str(sync_error)) from sync_error
        try:
            mgr.sync()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        result = mgr.install(
            body.name,
            pin=body.pin,
            validate=body.perform_validation,
            strict=body.strict,
            probe_tools=body.probe_tools,
        )
    if isinstance(result, tuple):
        meta, report = result
    else:
        meta, report = result, None
    if body.strict and report is not None and hasattr(report, "ok") and not report.ok:
        detail = report.to_dict() if hasattr(report, "to_dict") else repr(report)
        raise HTTPException(status_code=409, detail={"error": "skill_validation_failed", "report": detail})
    payload: Dict[str, Any] = {
        "ok": True,
        "skill": {
            "id": getattr(meta, "id", None).value if getattr(meta, "id", None) else body.name,
            "version": getattr(meta, "version", None),
            "path": str(getattr(meta, "path", "")),
        },
    }
    skill_name = str(payload["skill"].get("id") or body.name)
    try:
        prep = mgr.prepare_runtime(skill_name, run_tests=False)
    except Exception as exc:
        emit_runtime_activation_failure(
            getattr(mgr, "bus", None),
            component_type="skill",
            component_id=skill_name,
            stage="prepare",
            error=f"{type(exc).__name__}: {exc}",
            source="api.skills.install",
            report_policy="project_inbox",
            space="default",
            webspace_id=webspace_id,
            operation_id=f"skill-install:{skill_name}",
        )
        log.exception("runtime preparation failed after skill install: %s", skill_name)
        raise HTTPException(
            status_code=409,
            detail=f"runtime preparation failed for {skill_name}: {type(exc).__name__}: {exc}",
        ) from exc
    emit_runtime_activation_success(
        getattr(mgr, "bus", None),
        component_type="skill",
        component_id=skill_name,
        stage="prepare",
        source="api.skills.install",
        report_policy="project_inbox",
        space="default",
        webspace_id=webspace_id,
        version=str(getattr(prep, "version", "") or "") or None,
        slot=str(getattr(prep, "slot", "") or "") or None,
        operation_id=f"skill-install:{skill_name}",
    )
    try:
        slot = mgr.activate_for_space(
            skill_name,
            version=getattr(prep, "version", None),
            slot=getattr(prep, "slot", None),
            space="default",
            webspace_id=webspace_id,
            emit_activation=False,
            observation_source="api.skills.install",
            observation_policy="project_inbox",
            operation_id=f"skill-install:{skill_name}",
        )
    except Exception as exc:
        log.exception("runtime activation failed after skill install: %s", skill_name)
        raise HTTPException(
            status_code=409,
            detail=f"runtime activation failed for {skill_name}: {type(exc).__name__}: {exc}",
        ) from exc
    payload["runtime"] = {
        "version": getattr(prep, "version", None),
        "slot": slot,
        "prepared": getattr(prep, "slot", None),
        "webspace_id": webspace_id,
    }
    if report is not None:
        if hasattr(report, "to_dict"):
            payload["report"] = report.to_dict()  # type: ignore[call-arg]
        else:
            payload["report"] = repr(report)
    return payload


@router.post("/install")
async def install(body: InstallReq, mgr: SkillManager = Depends(_get_manager), ctx: AgentContext = Depends(get_ctx)):
    webspace_id = body.webspace_id or default_webspace_id()
    if body.async_operation:
        operation = submit_install_operation(
            target_kind="skill",
            target_id=body.name,
            webspace_id=webspace_id,
        )
        return {
            "ok": True,
            "accepted": True,
            "operation_id": operation["operation_id"],
            "operation": operation,
        }
    payload = await asyncio.to_thread(_install_skill_sync, body, mgr, webspace_id)
    skill_name = str(((payload.get("skill") or {}).get("id")) or body.name)
    payload.update(
        await _finalize_live_skill_activation(
            ctx,
            skill_name,
            space="default",
            webspace_id=webspace_id,
            defer_webspace_rebuild=False,
            cache_reason=f"skill_install:{skill_name}",
            cache_action="skill_install_sync",
        )
    )
    try:
        await rebuild_webspace_projection(
            webspace_id=webspace_id,
            action="skill_install_sync",
            source_of_truth="skill_runtime",
        )
    except Exception:
        log.exception("webspace rebuild failed after skill install: %s", body.name)
    return payload


@router.post("/uninstall")
async def uninstall(body: UninstallReq, mgr: SkillManager = Depends(_get_manager)):
    webspace_id = body.webspace_id or default_webspace_id()
    await asyncio.to_thread(
        mgr.uninstall,
        body.name,
        force=bool(body.force),
    )
    await asyncio.to_thread(
        invalidate_webspace_materialization_cache,
        webspace_id,
        reason=f"skill_uninstall:{body.name}",
        action="skill_uninstall_sync",
        source_of_truth="skill_runtime",
    )
    try:
        await rebuild_webspace_projection(
            webspace_id=webspace_id,
            action="skill_uninstall_sync",
            source_of_truth="skill_runtime",
        )
    except Exception:
        log.exception("webspace rebuild failed after skill uninstall: %s", body.name)
    return {"ok": True}


@router.get("/{name}")
async def get_skill(name: str, mgr: SkillManager = Depends(_get_manager)):
    meta = await asyncio.to_thread(mgr.get, name)
    if not meta:
        return {"ok": False, "reason": "not-found"}
    return {"ok": True, "skill": _to_mapping(meta)}


@router.delete("/{name}")
async def remove(name: str, mgr: SkillManager = Depends(_get_manager)):
    await asyncio.to_thread(mgr.uninstall, name)
    await asyncio.to_thread(
        invalidate_webspace_materialization_cache,
        default_webspace_id(),
        reason=f"skill_delete:{name}",
        action="skill_uninstall_sync",
        source_of_truth="skill_runtime",
    )
    try:
        await rebuild_webspace_projection(
            webspace_id=default_webspace_id(),
            action="skill_uninstall_sync",
            source_of_truth="skill_runtime",
        )
    except Exception:
        log.exception("webspace rebuild failed after skill delete: %s", name)
    return {"ok": True}


@router.put("/{name}/files/{filename:path}")
async def upload_skill_file(
    name: str,
    filename: str,
    request: Request,
    purpose: str | None = None,
    expected_size_bytes: int | None = None,
    ctx: AgentContext = Depends(get_ctx),
):
    metadata = request_upload_metadata(request.headers)
    content_length = metadata.get("content_length")
    max_bytes = skill_upload_max_bytes()
    if isinstance(content_length, int) and max_bytes > 0 and content_length > max_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds max size: {max_bytes} bytes")
    try:
        return await store_skill_upload(
            skills_root=Path(ctx.paths.skills_workspace_dir()),
            skill_name=name,
            filename=filename,
            chunks=request.stream(),
            purpose=purpose,
            content_type=metadata.get("content_type"),
            max_bytes=max_bytes,
            expected_size_bytes=expected_size_bytes,
        )
    except ClientDisconnect as exc:
        raise HTTPException(status_code=499, detail="upload client disconnected") from exc
    except ValueError as exc:
        raise HTTPException(status_code=413 if "max size" in str(exc) else 400, detail=str(exc)) from exc


@router.get("/{name}/files/content/{relative_path:path}")
async def get_skill_file_content(
    name: str,
    relative_path: str,
    download: bool = False,
    ctx: AgentContext = Depends(get_ctx),
):
    try:
        target = resolve_skill_file_path(
            skills_root=Path(ctx.paths.skills_workspace_dir()),
            skill_name=name,
            relative_path=relative_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="skill file not found")
    media_type = guess_type(target.name)[0] or "application/octet-stream"
    if download:
        return FileResponse(
            target,
            media_type=media_type,
            filename=target.name,
            content_disposition_type="attachment",
        )
    return FileResponse(target, media_type=media_type)


@router.post("/push")
async def push(body: PushReq, mgr: SkillManager = Depends(_get_manager)):
    revision = await asyncio.to_thread(mgr.push, body.name, body.message, signoff=body.signoff)
    return {"ok": True, "revision": revision}


# --- Runtime management endpoints ---


@router.post("/runtime/prepare")
async def runtime_prepare(body: RuntimePrepareReq, mgr: SkillManager = Depends(_get_manager)):
    if body.allow_deactivated and not body.run_tests:
        raise HTTPException(status_code=400, detail="deactivated runtime recovery requires run_tests=true")
    try:
        result = await asyncio.to_thread(
            mgr.prepare_runtime,
            body.name,
            run_tests=body.run_tests,
            preferred_slot=body.slot,
            allow_deactivated=body.allow_deactivated,
        )
    except (SkillCoreCompatibilityError, SkillDependencyIsolationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        stage = "tests" if "test" in str(exc).lower() else "prepare"
        emit_runtime_activation_failure(
            getattr(mgr, "bus", None),
            component_type="skill",
            component_id=body.name,
            stage=stage,
            error=f"{type(exc).__name__}: {exc}",
            source="api.skills.runtime_prepare",
            report_policy="project_inbox",
            space="default",
            operation_id=f"skill-prepare:{body.name}",
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = {
        "ok": True,
        "name": result.name,
        "version": result.version,
        "slot": result.slot,
        "resolved_manifest": str(result.resolved_manifest),
        "tests": {k: v.status for k, v in (result.tests or {}).items()},
    }
    emit_runtime_activation_success(
        getattr(mgr, "bus", None),
        component_type="skill",
        component_id=body.name,
        stage="tests" if body.run_tests else "prepare",
        source="api.skills.runtime_prepare",
        report_policy="project_inbox",
        space="default",
        version=str(result.version or "") or None,
        slot=str(result.slot or "") or None,
        operation_id=f"skill-prepare:{body.name}",
    )
    return payload


@router.post("/runtime/activate")
async def runtime_activate(
    body: RuntimeActivateReq,
    mgr: SkillManager = Depends(_get_manager),
    ctx: AgentContext = Depends(get_ctx),
):
    webspace_id = body.webspace_id or "default"
    invalidate_local_capacity_cache()
    try:
        slot = await asyncio.to_thread(
            mgr.activate_for_space,
            body.name,
            version=body.version,
            slot=body.slot,
            space="default",
            webspace_id=webspace_id,
            emit_activation=False,
            observation_source="api.skills.runtime_activate",
            observation_policy="project_inbox",
            operation_id=f"skill-activate:{body.name}",
        )
        activation = await _finalize_live_skill_activation(
            ctx,
            body.name,
            space="default",
            webspace_id=webspace_id,
            defer_webspace_rebuild=False,
            cache_reason=f"skill_activate:{body.name}",
            cache_action="skill_activation_sync",
        )
        return {"ok": True, "slot": slot, **activation}
    except (SkillCoreCompatibilityError, SkillDependencyIsolationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        msg = str(exc).lower()
        if not body.auto_prepare or ("is not prepared" not in msg and "no installed versions" not in msg):
            # expose as 422 Unprocessable if activation cannot proceed
            raise HTTPException(status_code=422, detail=str(exc))
        # auto-prepare then retry
        pref_slot = body.slot
        try:
            prep = await asyncio.to_thread(
                mgr.prepare_runtime,
                body.name,
                run_tests=False,
                preferred_slot=pref_slot,
            )
        except (SkillCoreCompatibilityError, SkillDependencyIsolationError) as compat_exc:
            raise HTTPException(status_code=409, detail=str(compat_exc)) from compat_exc
        except Exception as prep_exc:
            emit_runtime_activation_failure(
                getattr(mgr, "bus", None),
                component_type="skill",
                component_id=body.name,
                stage="prepare",
                error=f"{type(prep_exc).__name__}: {prep_exc}",
                source="api.skills.runtime_activate",
                report_policy="project_inbox",
                space="default",
                webspace_id=webspace_id,
                operation_id=f"skill-activate:{body.name}",
            )
            raise HTTPException(status_code=422, detail=str(prep_exc)) from prep_exc
        try:
            slot = await asyncio.to_thread(
                mgr.activate_for_space,
                body.name,
                version=prep.version,
                slot=prep.slot,
                space="default",
                webspace_id=webspace_id,
                emit_activation=False,
                observation_source="api.skills.runtime_activate",
                observation_policy="project_inbox",
                operation_id=f"skill-activate:{body.name}",
            )
        except (SkillCoreCompatibilityError, SkillDependencyIsolationError) as compat_exc:
            raise HTTPException(status_code=409, detail=str(compat_exc)) from compat_exc
        except RuntimeError as activation_exc:
            raise HTTPException(status_code=422, detail=str(activation_exc)) from activation_exc
        activation = await _finalize_live_skill_activation(
            ctx,
            body.name,
            space="default",
            webspace_id=webspace_id,
            defer_webspace_rebuild=False,
            cache_reason=f"skill_activate:{body.name}",
            cache_action="skill_activation_sync",
        )
        return {"ok": True, "slot": slot, "prepared": prep.slot, **activation}


@router.post("/runtime/notify-activated")
async def runtime_notify_activated(body: RuntimeNotifyActivatedReq):
    """
    Lightweight hook to broadcast a skills.activated event on the hub bus
    without touching runtime slots (used by CLI after local activation).
    """
    ctx = get_ctx()
    bus = getattr(ctx, "bus", None)
    if bus is None:
        return {"ok": False, "reason": "bus-unavailable"}
    space = (body.space or "default").strip() or "default"
    webspace_id = body.webspace_id or default_webspace_id()
    invalidate_local_capacity_cache()
    activation = await _finalize_live_skill_activation(
        ctx,
        body.name,
        space=space,
        webspace_id=webspace_id,
        defer_webspace_rebuild=bool(body.defer_webspace_rebuild),
        cache_reason=f"skills_activated:{body.name}",
        cache_action="skill_activation_sync",
    )
    return {"ok": True, **activation}


@router.post("/runtime/rebuild-webspace")
async def runtime_rebuild_webspace(body: RuntimeRebuildWebspaceReq):
    webspace_id = body.webspace_id or default_webspace_id()
    invalidate_local_capacity_cache()
    await rebuild_webspace_projection(
        webspace_id=webspace_id,
        action="skill_batch_runtime_sync",
        source_of_truth="skill_runtime",
    )
    return {"ok": True, "accepted": True, "webspace_id": webspace_id}


@router.get("/runtime/status/{name}")
async def runtime_status(
    name: str,
    target_node_id: str | None = None,
    mgr: SkillManager = Depends(_get_manager),
    ctx: AgentContext = Depends(get_ctx),
):
    target = str(target_node_id or "").strip()
    config = getattr(ctx, "config", None)
    local_node_id = str(getattr(config, "node_id", "") or "").strip()
    if target and not node_identities_match(target, local_node_id):
        if str(getattr(config, "role", "") or "").strip().lower() != "hub":
            raise HTTPException(status_code=409, detail="remote_runtime_status_requires_hub")
        from adaos.services.subnet.link_manager import get_hub_link_manager

        try:
            state = await get_hub_link_manager().rpc_call(
                target,
                method="skills.runtime.status",
                params={"name": name},
                timeout=30.0,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"remote_runtime_status_failed:{type(exc).__name__}",
            ) from exc
        if not isinstance(state, dict):
            raise HTTPException(status_code=502, detail="remote_runtime_status_contract_invalid")
        return {"ok": True, "state": state, "node_id": target, "remote": True}
    state = await asyncio.to_thread(mgr.runtime_status, name)
    return {"ok": True, "state": state, "node_id": local_node_id, "remote": False}


@router.post("/runtime/setup")
async def runtime_setup(body: RuntimeSetupReq, mgr: SkillManager = Depends(_get_manager)):
    try:
        result = await asyncio.to_thread(mgr.setup_skill, body.name)
        if isinstance(result, dict):
            return {"ok": bool(result.get("ok", True)), **result}
        return {"ok": True, "result": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("runtime setup failed: %s", body.name)
        raise HTTPException(status_code=500, detail=str(exc) or "runtime setup failed") from exc


@router.post("/update")
async def update_skill(body: UpdateReq, ctx: AgentContext = Depends(get_ctx)):
    coordinator = ArtifactSubscriptionUpdateCoordinator(ctx)
    try:
        update_route = coordinator.select_route(body.name)
    except ArtifactSubscriptionUpdateError as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    if update_route.package_required:
        try:
            return await coordinator.update(
                "skill",
                body.name,
                dry_run=body.dry_run,
                expected_plan_digest=body.expected_plan_digest,
                permission_decision=body.permission_decision,
                idempotency_key=body.idempotency_key,
                webspace_id=body.webspace_id,
                defer_webspace_rebuild=body.defer_webspace_rebuild,
            )
        except ArtifactSubscriptionUpdateError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    service = SkillUpdateService(ctx)
    try:
        kwargs: dict[str, Any] = {"dry_run": body.dry_run}
        if body.force is not None:
            kwargs["force"] = body.force
        result = await asyncio.to_thread(service.request_update, body.name, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("skill update failed: %s", body.name)
        raise HTTPException(status_code=500, detail=str(exc) or "skill update failed") from exc
    webspace_id = body.webspace_id or default_webspace_id()
    runtime_refresh: dict[str, Any] = {}
    handler_reload: dict[str, Any] = {}
    webspace_rebuild: dict[str, Any] = {
        "scheduled": False,
        "mode": "deferred" if bool(body.defer_webspace_rebuild) else "not_requested",
        "webspace_id": webspace_id,
    }
    if not body.dry_run:
        mgr = _get_manager(ctx)
        source_version = str(result.version or "").strip()
        try:
            runtime_refresh = await asyncio.to_thread(
                refresh_skill_runtime,
                mgr,
                body.name,
                webspace_id=webspace_id,
                source_version=source_version,
                migrate_runtime=True,
                ensure_installed=False,
                require_active_version=True,
                disable_during_migration=True,
                operation_id=f"skill-update:{body.name}",
                emit_activation=False,
            )
        except RuntimeRefreshError as exc:
            emit_runtime_activation_failure(
                getattr(ctx, "bus", None),
                component_type="skill",
                component_id=body.name,
                stage=str(exc.payload.get("failed_stage") or "runtime_refresh"),
                error=str(exc.payload.get("error") or exc),
                source="api.skills.update",
                report_policy="project_inbox",
                space="default",
                webspace_id=webspace_id,
                version=str(
                    exc.payload.get("prepared_version")
                    or exc.payload.get("source_version")
                    or ""
                )
                or None,
                slot=str(exc.payload.get("prepared_slot") or "") or None,
                operation_id=f"skill-update:{body.name}",
            )
            log.exception("runtime refresh failed after skill update: %s", body.name)
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"runtime refresh failed after skill update: {exc}",
                    "runtime_refresh": exc.payload,
                },
            ) from exc
        except Exception as exc:
            log.exception("runtime refresh failed after skill update: %s", body.name)
            raise HTTPException(status_code=409, detail=f"runtime refresh failed after skill update: {exc}") from exc
        activation = await _finalize_live_skill_activation(
            ctx,
            body.name,
            space="default",
            webspace_id=webspace_id,
            defer_webspace_rebuild=bool(body.defer_webspace_rebuild),
            cache_reason=f"skill_update:{body.name}",
            cache_action="skill_update_sync",
            emit_activation=False,
        )
        handler_reload = activation["handler_reload"]
        materialization_cache = activation["materialization_cache"]
        bus = getattr(ctx, "bus", None)
        if bus is not None:
            bus_emit(
                bus,
                "skills.updated",
                {
                    "name": body.name,
                    "webspace_id": webspace_id,
                    "defer_webspace_rebuild": bool(body.defer_webspace_rebuild),
                },
                "api.skills",
            )
            _emit_live_skill_activation(
                ctx,
                body.name,
                space="default",
                webspace_id=webspace_id,
                defer_webspace_rebuild=bool(body.defer_webspace_rebuild),
            )
        if not body.defer_webspace_rebuild:
            webspace_rebuild = _schedule_webspace_rebuild(
                webspace_id=webspace_id,
                action="skill_update_sync",
                source_of_truth="skill_runtime",
                reason=f"skill_update:{body.name}",
            )
    return {
        "ok": True,
        "updated": result.updated,
        "version": result.version,
        "mode": "legacy_source_pull",
        "update_route": update_route.to_dict(),
        "legacy_materialization": True,
        "warning": "no stable package subscription; compatibility git pull was used",
        "runtime_refresh": runtime_refresh,
        "handler_reload": handler_reload,
        "materialization_cache": materialization_cache if not body.dry_run else {},
        "webspace_rebuild": webspace_rebuild,
    }


@router.post("/runtime/migration/start")
async def runtime_migration_start(body: RuntimeMigrationStartReq, ctx: AgentContext = Depends(get_ctx)):
    return await start_background_migration(
        ctx,
        reason=body.reason or "api",
        webspace_id=body.webspace_id or default_webspace_id(),
        name=body.name,
        force=bool(body.force),
        run_tests=bool(body.run_tests),
        sync_workspace=bool(body.sync_workspace),
    )


@router.get("/runtime/migration/status")
async def runtime_migration_status(ctx: AgentContext = Depends(get_ctx)):
    status = await asyncio.to_thread(read_skill_runtime_migration_status, ctx)
    return {"ok": True, "status": status}
