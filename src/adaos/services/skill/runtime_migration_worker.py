from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from adaos.adapters.db import SqliteSkillRegistry
from adaos.services.agent_context import AgentContext
from adaos.services.runtime_refresh import RuntimeRefreshError, rebuild_webspace_projection_sync, refresh_skill_runtime
from adaos.services.skill.manager import SkillManager
from adaos.services.workspace_registry import build_registry_entry, list_workspace_registry_entries


_LOG = logging.getLogger("adaos.skill.runtime_migration")
_TASK: asyncio.Task[Any] | None = None
_LOCK = asyncio.Lock()


def _status_dir(ctx: AgentContext) -> Path:
    return Path(ctx.paths.base_dir()) / "state" / "skill_runtime_migration"


def status_path(ctx: AgentContext) -> Path:
    return _status_dir(ctx) / "status.json"


def _now() -> float:
    return time.time()


def _default_webspace_id() -> str:
    try:
        from adaos.services.yjs.webspace import default_webspace_id

        return default_webspace_id()
    except Exception:
        return "default"


def _write_status(ctx: AgentContext, payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    body["updated_at"] = _now()
    path = status_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return body


def read_status(ctx: AgentContext) -> dict[str, Any]:
    path = status_path(ctx)
    if not path.exists():
        return {
            "ok": True,
            "state": "idle",
            "phase": "idle",
            "message": "no active skill runtime migration",
            "pending": False,
            "updated_at": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "ok": False,
            "state": "unknown",
            "phase": "read_status",
            "message": "skill runtime migration status is unreadable",
            "pending": False,
            "status_path": str(path),
        }
    return payload if isinstance(payload, dict) else {"ok": False, "state": "unknown", "pending": False}


def _version(value: Any) -> Version | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return Version(token)
    except InvalidVersion:
        return None


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _read_local_artifact_version(artifact_dir: Path) -> str:
    try:
        entry = build_registry_entry("skills", artifact_dir)
    except Exception:
        entry = None
    if not isinstance(entry, dict):
        return ""
    return _clean_text(entry.get("version"))


def _workspace_skill_source(ctx: AgentContext, name: str) -> Path:
    workspace_root = Path(ctx.paths.workspace_dir())
    skills_root = Path(ctx.paths.skills_workspace_dir())
    candidate = (skills_root / name).resolve()
    if candidate.exists():
        return candidate
    repo_root_attr = getattr(ctx.paths, "repo_root", None)
    repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
    if repo_root:
        fallback = (Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "skills" / name).resolve()
        if fallback.exists():
            return fallback
    return (workspace_root / "skills" / name).resolve()


def _registry_versions(ctx: AgentContext) -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        items = list_workspace_registry_entries(Path(ctx.paths.workspace_dir()), kind="skills", fallback_to_scan=True)
    except Exception:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name") or item.get("id"))
        version = _clean_text(item.get("version"))
        if name and version:
            versions[name] = version
    return versions


def _registered_skill_names(ctx: AgentContext) -> list[str]:
    names: set[str] = set()
    try:
        for row in SqliteSkillRegistry(ctx.sql).list():
            if not bool(getattr(row, "installed", True)):
                continue
            name = getattr(row, "name", None) or getattr(row, "id", None)
            if name:
                names.add(str(name))
    except Exception:
        pass
    return sorted(names)


def _runtime_is_behind(workspace_version: str, runtime_version: str) -> bool:
    if workspace_version and not runtime_version:
        return True
    if not workspace_version or not runtime_version:
        return False
    left = _version(workspace_version)
    right = _version(runtime_version)
    if left is not None and right is not None:
        return left > right
    return workspace_version != runtime_version


def migration_candidates(
    ctx: AgentContext,
    mgr: SkillManager,
    *,
    force: bool = False,
    name: str | None = None,
) -> list[dict[str, Any]]:
    registry_versions = _registry_versions(ctx)
    requested_name = str(name or "").strip()
    names = {requested_name} if requested_name else (set(_registered_skill_names(ctx)) | set(registry_versions))
    candidates: list[dict[str, Any]] = []
    for name in sorted(names):
        source = _workspace_skill_source(ctx, name)
        workspace_version = _read_local_artifact_version(source) if source.exists() else registry_versions.get(name, "")
        try:
            runtime_state = mgr.runtime_status(name)
        except Exception:
            runtime_state = {}
        runtime_version = _clean_text(runtime_state.get("version") if isinstance(runtime_state, dict) else "")
        deactivation = runtime_state.get("deactivation") if isinstance(runtime_state, dict) and isinstance(runtime_state.get("deactivation"), dict) else {}
        reason = "force" if force else ""
        if not force:
            if not _runtime_is_behind(workspace_version, runtime_version):
                continue
            reason = "runtime_version_behind"
        candidates.append(
            {
                "skill": name,
                "workspace_version": workspace_version,
                "runtime_version": runtime_version,
                "source_path": str(source),
                "reason": reason,
                "deactivated": bool(runtime_state.get("deactivated")) if isinstance(runtime_state, dict) else False,
                "deactivation": deactivation,
            }
        )
    return candidates


def _manager(ctx: AgentContext) -> SkillManager:
    return SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )


def _reload_live_skill_handlers_sync(ctx: AgentContext, name: str) -> dict[str, Any]:
    try:
        from adaos.services.skills_loader_importlib import ImportlibSkillsLoader

        return asyncio.run(ImportlibSkillsLoader().reload_skill_handlers(ctx.paths.skills_dir(), name))
    except Exception as exc:
        return {"ok": False, "reason": "reload_failed", "error": str(exc)}


def _run_migration_sync(
    ctx: AgentContext,
    *,
    operation_id: str,
    webspace_id: str,
    force: bool,
    run_tests: bool,
    name: str | None,
    sync_workspace: bool,
) -> dict[str, Any]:
    mgr = _manager(ctx)
    started_at = _now()
    initial: dict[str, Any] = {
        "ok": True,
        "state": "running",
        "phase": "sync" if sync_workspace else "select",
        "message": "syncing workspace artifacts before skill runtime migration" if sync_workspace else "selecting skill runtime migration candidates",
        "pending": True,
        "operation_id": operation_id,
        "started_at": started_at,
        "webspace_id": webspace_id,
        "name": str(name or "").strip() or None,
        "force": bool(force),
        "run_tests": bool(run_tests),
        "sync_workspace": bool(sync_workspace),
    }
    _write_status(ctx, initial)
    if sync_workspace:
        mgr.sync(force=True if force else None)
    candidates = migration_candidates(ctx, mgr, force=force, name=name)
    payload: dict[str, Any] = {
        "ok": True,
        "state": "running",
        "phase": "migrate",
        "message": "skill runtime migration is running",
        "pending": True,
        "operation_id": operation_id,
        "started_at": started_at,
        "webspace_id": webspace_id,
        "name": str(name or "").strip() or None,
        "force": bool(force),
        "run_tests": bool(run_tests),
        "sync_workspace": bool(sync_workspace),
        "total": len(candidates),
        "completed_total": 0,
        "failed_total": 0,
        "deactivated_total": 0,
        "skills": candidates,
    }
    _write_status(ctx, payload)
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        name = str(candidate.get("skill") or "").strip()
        if not name:
            continue
        entry = dict(candidate)
        entry["stage"] = "disable"
        entry["ok"] = False
        payload["current"] = {"skill": name, "index": index, "stage": "disable"}
        _write_status(ctx, payload)
        try:
            try:
                deactivation = mgr.deactivate_runtime(
                    name,
                    reason="runtime_migration_in_progress",
                    failure_kind="migration",
                    failed_stage="disable",
                    source="skill_runtime_migration_worker",
                    committed_core_switch=False,
                    status="disabled",
                    comment="Skill runtime is disabled while AdaOS prepares and activates its updated runtime slot.",
                    operation_id=operation_id,
                    transient=True,
                )
                entry["disabled_for_migration"] = True
                entry["transient_deactivation"] = deactivation
            except Exception as exc:
                entry["disabled_for_migration"] = False
                entry["disable_error"] = str(exc)
            entry["stage"] = "refresh_runtime"
            payload["current"] = {"skill": name, "index": index, "stage": "refresh_runtime"}
            _write_status(ctx, payload)
            refresh = refresh_skill_runtime(
                mgr,
                name,
                webspace_id=webspace_id,
                source_version=str(candidate.get("workspace_version") or ""),
                migrate_runtime=True,
                ensure_installed=True,
                require_active_version=bool(candidate.get("workspace_version")),
                disable_during_migration=False,
            )
            entry["runtime_refresh"] = refresh
            if run_tests:
                entry["stage"] = "tests"
                payload["current"] = {"skill": name, "index": index, "stage": "tests"}
                _write_status(ctx, payload)
                tests = mgr.run_skill_tests(name, source="installed")
                entry["tests"] = {str(test): str(getattr(result, "status", result) or "") for test, result in tests.items()}
                failed_tests = {test: status for test, status in entry["tests"].items() if status != "passed"}
                if failed_tests:
                    raise RuntimeError(f"skill tests failed: {failed_tests}")
            entry["stage"] = "completed"
            entry["ok"] = True
            entry["deactivation_cleared"] = True
            entry["handler_reload"] = _reload_live_skill_handlers_sync(ctx, name)
        except RuntimeRefreshError as exc:
            entry["stage"] = str(exc.payload.get("failed_stage") or entry.get("stage") or "refresh_runtime")
            entry["ok"] = False
            entry["error"] = str(exc)
            entry["runtime_refresh"] = exc.payload
            with contextlib.suppress(Exception):
                deactivation = mgr.deactivate_runtime(
                    name,
                    reason="runtime_migration_failed",
                    failure_kind="migration",
                    failed_stage=str(entry["stage"] or "refresh_runtime"),
                    source="skill_runtime_migration_worker",
                    committed_core_switch=False,
                    status="quarantined",
                    comment=str(exc),
                    operation_id=operation_id,
                    transient=False,
                )
                entry["deactivation"] = deactivation
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
            with contextlib.suppress(Exception):
                deactivation = mgr.deactivate_runtime(
                    name,
                    reason="runtime_migration_failed",
                    failure_kind="migration",
                    failed_stage=str(entry.get("stage") or "unknown"),
                    source="skill_runtime_migration_worker",
                    committed_core_switch=False,
                    status="quarantined",
                    comment=str(exc),
                    operation_id=operation_id,
                    transient=False,
                )
                entry["deactivation"] = deactivation
        results.append(entry)
        payload["completed_total"] = len(results)
        payload["failed_total"] = sum(1 for item in results if not bool(item.get("ok")))
        payload["deactivated_total"] = sum(
            1
            for item in results
            if not bool(item.get("ok")) and bool((item.get("deactivation") or {}).get("deactivated"))
        )
        payload["skills"] = [*results, *candidates[index:]]
        payload["current"] = None
        _write_status(ctx, payload)
    failed = [item for item in results if not bool(item.get("ok"))]
    final = dict(payload)
    final["ok"] = not failed
    final["state"] = "failed" if failed else "succeeded"
    final["phase"] = "complete"
    final["message"] = (
        f"skill runtime migration finished with {len(failed)} failure(s)"
        if failed
        else "skill runtime migration completed"
    )
    final["pending"] = False
    final["finished_at"] = _now()
    final["elapsed_s"] = round(final["finished_at"] - started_at, 3)
    final["skills"] = results
    try:
        final["webspace_rebuild"] = rebuild_webspace_projection_sync(
            webspace_id=webspace_id,
            action="skill_runtime_migration_sync",
            source_of_truth="skill_runtime",
        )
    except Exception as exc:
        final["webspace_rebuild"] = {"ok": False, "error": str(exc), "webspace_id": webspace_id}
        if not failed:
            final["ok"] = False
            final["state"] = "failed"
            final["message"] = f"skill runtime migration completed, but webspace rebuild failed: {exc}"
    return _write_status(ctx, final)


async def _run_background(
    ctx: AgentContext,
    *,
    operation_id: str,
    webspace_id: str,
    force: bool,
    run_tests: bool,
    name: str | None,
    sync_workspace: bool,
) -> None:
    try:
        await asyncio.to_thread(
            _run_migration_sync,
            ctx,
            operation_id=operation_id,
            webspace_id=webspace_id,
            force=force,
            run_tests=run_tests,
            name=name,
            sync_workspace=sync_workspace,
        )
    except Exception as exc:
        _LOG.exception("background skill runtime migration failed")
        _write_status(
            ctx,
            {
                "ok": False,
                "state": "failed",
                "phase": "background",
                "message": f"skill runtime migration worker failed: {exc}",
                "pending": False,
                "operation_id": operation_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "finished_at": _now(),
            },
        )


async def start_background_migration(
    ctx: AgentContext,
    *,
    reason: str,
    webspace_id: str | None = None,
    name: str | None = None,
    force: bool = False,
    run_tests: bool = True,
    sync_workspace: bool = True,
) -> dict[str, Any]:
    global _TASK
    async with _LOCK:
        if _TASK is not None and not _TASK.done():
            status = read_status(ctx)
            return {"ok": True, "accepted": False, "reason": "already_running", "status": status}
        operation_id = f"skill-migrate-{uuid.uuid4().hex[:10]}"
        target_webspace = str(webspace_id or "").strip() or _default_webspace_id()
        initial = _write_status(
            ctx,
            {
                "ok": True,
                "state": "scheduled",
                "phase": "schedule",
                "message": "skill runtime migration scheduled",
                "pending": True,
                "operation_id": operation_id,
                "reason": str(reason or "manual"),
                "webspace_id": target_webspace,
                "name": str(name or "").strip() or None,
                "force": bool(force),
                "run_tests": bool(run_tests),
                "sync_workspace": bool(sync_workspace),
                "scheduled_at": _now(),
            },
        )
        _TASK = asyncio.create_task(
            _run_background(
                ctx,
                operation_id=operation_id,
                webspace_id=target_webspace,
                force=force,
                run_tests=run_tests,
                name=name,
                sync_workspace=sync_workspace,
            ),
            name=f"skill-runtime-migration:{operation_id}",
        )
        return {"ok": True, "accepted": True, "status": initial}


async def cancel_background_migration() -> None:
    global _TASK
    task = _TASK
    if task is None:
        return
    if not task.done():
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
    _TASK = None
