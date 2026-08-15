from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
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
_DEFAULT_STALE_AFTER_S = 300.0
_STAGE_STALE_AFTER_S: dict[str, float] = {
    "schedule": 60.0,
    "sync": 180.0,
    "select": 60.0,
    "migrate": 300.0,
    "disable": 60.0,
    "refresh_runtime": 600.0,
    "tests": 600.0,
    "background": 120.0,
}


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


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _round_age(now: float, ts: Any) -> float | None:
    stamp = _float_or_none(ts)
    if stamp is None or stamp <= 0:
        return None
    return round(max(0.0, now - stamp), 3)


def _current_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    current_skill = _clean_text(current.get("skill")) if current else ""
    for item in list(payload.get("skills") or []):
        if not isinstance(item, dict):
            continue
        if current_skill and _clean_text(item.get("skill")) == current_skill:
            return dict(item)
    return {}


def _io_pressure_snapshot(ctx: AgentContext, payload: dict[str, Any]) -> dict[str, Any]:
    disks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for getter_name in ("base_dir", "workspace_dir"):
        try:
            getter = getattr(ctx.paths, getter_name)
            raw = getter() if callable(getter) else getter
            path = Path(raw).expanduser().resolve()
        except Exception:
            continue
        token = str(path)
        if token in seen:
            continue
        seen.add(token)
        try:
            usage = shutil.disk_usage(path)
            total = max(1, int(usage.total))
            disks.append(
                {
                    "path": token,
                    "total_bytes": int(usage.total),
                    "used_bytes": int(usage.used),
                    "free_bytes": int(usage.free),
                    "used_pct": round((int(usage.used) / total) * 100.0, 3),
                }
            )
        except Exception:
            continue
    psi: dict[str, Any] = {"available": False}
    try:
        raw = Path("/proc/pressure/io").read_text(encoding="utf-8").splitlines()
        parsed: dict[str, dict[str, float]] = {}
        for line in raw:
            parts = [part for part in line.split() if part]
            if not parts:
                continue
            row: dict[str, float] = {}
            for part in parts[1:]:
                key, _, value = part.partition("=")
                if key and value:
                    with contextlib.suppress(Exception):
                        row[key] = float(value)
            parsed[parts[0]] = row
        psi = {"available": bool(parsed), **parsed}
    except Exception:
        pass
    pressure = any(float(item.get("used_pct") or 0.0) >= 95.0 for item in disks)
    try:
        pressure = pressure or float(((psi.get("full") or {}) if isinstance(psi.get("full"), dict) else {}).get("avg10") or 0.0) >= 10.0
    except Exception:
        pass
    return {
        "available": bool(disks) or bool(psi.get("available")),
        "pressure": bool(pressure),
        "disks": disks,
        "psi_io": psi,
    }


def _classify_status_blocker(payload: dict[str, Any], *, stale: bool, stage: str, candidate: dict[str, Any]) -> str | None:
    text_parts: list[str] = []
    for key in ("message", "error", "reason"):
        value = payload.get(key)
        if value:
            text_parts.append(str(value))
    for key in ("error", "disable_error"):
        value = candidate.get(key)
        if value:
            text_parts.append(str(value))
    deactivation = candidate.get("deactivation") if isinstance(candidate.get("deactivation"), dict) else {}
    if deactivation:
        text_parts.append(str(deactivation.get("comment") or ""))
        text_parts.append(str(deactivation.get("reason") or ""))
    text = " ".join(text_parts).lower()
    if "database is locked" in text or ("sqlite" in text and "locked" in text):
        return "sqlite_lock"
    if "no space left" in text or "disk full" in text:
        return "disk_full"
    if "pip" in text or "dependency" in text or "install" in text or "torch" in text:
        return "dependency_install_failed"
    if stale:
        if stage in {"refresh_runtime", "prepare", "install"}:
            return "dependency_install_or_runtime_prepare_stalled"
        if stage == "tests":
            return "skill_tests_stalled"
        if stage == "sync":
            return "workspace_sync_or_git_stalled"
        if stage in {"disable", "migrate"}:
            return "skill_runtime_migration_stalled"
        return "skill_runtime_migration_status_stale"
    return None


def _status_diagnostics(ctx: AgentContext, payload: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    ts = _now() if now is None else float(now)
    state = _clean_text(payload.get("state")) or "unknown"
    phase = _clean_text(payload.get("phase")) or state
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    stage = _clean_text(current.get("stage") if current else "") or phase
    candidate = _current_candidate(payload)
    updated_age_s = _round_age(ts, payload.get("updated_at"))
    elapsed_s = _round_age(ts, payload.get("started_at") or payload.get("scheduled_at"))
    stale_after_s = float(_STAGE_STALE_AFTER_S.get(stage, _STAGE_STALE_AFTER_S.get(phase, _DEFAULT_STALE_AFTER_S)))
    pending = bool(payload.get("pending"))
    stale = bool(pending and updated_age_s is not None and updated_age_s >= stale_after_s)
    host_pressure = _io_pressure_snapshot(ctx, payload)
    suspected_blocker = _classify_status_blocker(payload, stale=stale, stage=stage, candidate=candidate)
    if suspected_blocker is None and stale and bool(host_pressure.get("pressure")):
        suspected_blocker = "host_io_or_disk_pressure"
    recommendations: list[str] = []
    if suspected_blocker in {"dependency_install_or_runtime_prepare_stalled", "dependency_install_failed"}:
        recommendations.append("inspect runtime prepare/install logs for the current skill")
    if suspected_blocker in {"sqlite_lock", "host_io_or_disk_pressure", "disk_full"}:
        recommendations.append("inspect disk usage, /proc/pressure/io, and SQLite lock holders on the stand")
    if stale:
        recommendations.append("inspect state/skill_runtime_migration/status.json and running migration process wait channels")
    return {
        "schema": "adaos.skill_runtime_migration.diagnostics.v1",
        "state": "stalled" if stale else ("failed" if state == "failed" else "ok"),
        "pending": pending,
        "stale": stale,
        "stale_after_s": stale_after_s if pending else None,
        "updated_age_s": updated_age_s,
        "elapsed_s": elapsed_s,
        "current_skill": _clean_text(current.get("skill") if current else "") or None,
        "current_stage": stage or None,
        "current_index": current.get("index") if current else None,
        "suspected_blocker": suspected_blocker,
        "host_pressure": host_pressure,
        "recommendations": recommendations,
    }


def _with_diagnostics(ctx: AgentContext, payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["diagnostics"] = _status_diagnostics(ctx, result)
    return result


def read_status(ctx: AgentContext) -> dict[str, Any]:
    path = status_path(ctx)
    if not path.exists():
        return _with_diagnostics(ctx, {
            "ok": True,
            "state": "idle",
            "phase": "idle",
            "message": "no active skill runtime migration",
            "pending": False,
            "updated_at": None,
        })
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _with_diagnostics(ctx, {
            "ok": False,
            "state": "unknown",
            "phase": "read_status",
            "message": "skill runtime migration status is unreadable",
            "pending": False,
            "status_path": str(path),
        })
    return _with_diagnostics(ctx, payload if isinstance(payload, dict) else {"ok": False, "state": "unknown", "pending": False})


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
    # Workspace registry entries describe materialized artifacts, not install
    # intent.  Only the installed registry may enroll a skill in an automatic
    # runtime migration; an explicit request may still recover one by name.
    names = {requested_name} if requested_name else set(_registered_skill_names(ctx))
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
        precommit_migration_failure = (
            str(deactivation.get("reason") or "").strip() == "runtime_migration_failed"
            and not bool(deactivation.get("committed_core_switch"))
        )
        attempted_version = _clean_text(deactivation.get("attempted_version"))
        reason = "force" if force else ""
        if not force:
            explicitly_recovering = bool(requested_name and runtime_state.get("deactivated"))
            if explicitly_recovering:
                reason = "explicit_quarantine_recovery"
            elif bool(runtime_state.get("deactivated")):
                if precommit_migration_failure and _runtime_is_behind(workspace_version, runtime_version):
                    reason = "recover_precommit_migration_failure"
                else:
                    # Post-commit quarantine remains fail-closed. A pre-commit
                    # failure is retried only when a newer candidate exists.
                    continue
            elif precommit_migration_failure and attempted_version == workspace_version:
                # The active fallback remains live. Do not retry the same
                # rejected candidate on every background discovery cycle.
                continue
            elif not _runtime_is_behind(workspace_version, runtime_version):
                continue
            else:
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


def failed_candidate_runtimes(ctx: AgentContext, mgr: SkillManager) -> list[dict[str, Any]]:
    """Report rejected candidates that did not deactivate their fallback."""

    names = set(_registered_skill_names(ctx)) | set(_registry_versions(ctx))
    items: list[dict[str, Any]] = []
    for skill_name in sorted(names):
        try:
            status = mgr.runtime_status(skill_name)
        except Exception:
            continue
        marker = status.get("deactivation") if isinstance(status.get("deactivation"), dict) else {}
        if bool(status.get("deactivated")) or str(marker.get("status") or "") != "candidate_quarantined":
            continue
        items.append(
            {
                "skill": skill_name,
                "active_version": str(status.get("version") or ""),
                "active_slot": str(status.get("active_slot") or ""),
                "attempted_version": str(marker.get("attempted_version") or ""),
                "failed_stage": str(marker.get("failed_stage") or ""),
                "comment": str(marker.get("comment") or ""),
                "operation_id": str(marker.get("operation_id") or ""),
                "fallback_preserved": bool(marker.get("fallback_preserved")),
                "rollback_performed": bool(marker.get("rollback_performed")),
            }
        )
    return items


def quarantined_runtimes(
    ctx: AgentContext,
    mgr: SkillManager,
) -> list[dict[str, Any]]:
    """Report quarantined runtimes without turning discovery into a retry."""

    names = set(_registered_skill_names(ctx)) | set(_registry_versions(ctx))
    items: list[dict[str, Any]] = []
    for skill_name in sorted(names):
        try:
            status = mgr.runtime_status(skill_name)
        except Exception:
            continue
        if not bool(status.get("deactivated")):
            continue
        deactivation = status.get("deactivation") if isinstance(status.get("deactivation"), dict) else {}
        items.append(
            {
                "skill": skill_name,
                "version": str(status.get("version") or ""),
                "active_slot": str(status.get("active_slot") or ""),
                "reason": str(deactivation.get("reason") or "deactivated"),
                "failed_stage": str(deactivation.get("failed_stage") or ""),
                "failure_kind": str(deactivation.get("failure_kind") or ""),
                "comment": str(deactivation.get("comment") or ""),
                "operation_id": str(deactivation.get("operation_id") or ""),
            }
        )
    return items


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


def _runtime_selection(status: dict[str, Any] | None) -> tuple[str, str]:
    payload = status if isinstance(status, dict) else {}
    return (
        str(payload.get("version") or "").strip(),
        str(payload.get("active_slot") or "").strip().upper(),
    )


def _preserve_runtime_after_candidate_failure(
    ctx: AgentContext,
    mgr: SkillManager,
    *,
    name: str,
    candidate: dict[str, Any],
    entry: dict[str, Any],
    before: dict[str, Any],
    operation_id: str,
    error: BaseException,
) -> dict[str, Any]:
    before_version, before_slot = _runtime_selection(before)
    try:
        current = mgr.runtime_status(name)
    except Exception:
        current = {}
    current_version, current_slot = _runtime_selection(current)
    previous_marker = before.get("deactivation") if isinstance(before.get("deactivation"), dict) else {}
    previous_failed_active = (
        bool(before.get("deactivated"))
        and not bool(previous_marker.get("committed_core_switch"))
        and str(previous_marker.get("failed_stage") or "").strip().lower()
        not in {"", "prepare", "runtime_update"}
    )
    selection_changed = bool(
        before_version
        and (current_version != before_version or current_slot != before_slot)
    )
    rollback_required = bool(selection_changed or previous_failed_active)
    rollback_performed = False
    rollback_error = ""
    if rollback_required:
        try:
            mgr.rollback_runtime(name)
            rollback_performed = True
            current = mgr.runtime_status(name)
            current_version, current_slot = _runtime_selection(current)
        except Exception as rollback_exc:
            rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"

    fallback_available = bool(current_version and current_slot and (not rollback_required or rollback_performed))
    if fallback_available:
        marker = mgr.record_runtime_migration_failure(
            name,
            attempted_version=str(candidate.get("workspace_version") or ""),
            failed_stage=str(entry.get("stage") or "unknown"),
            comment=str(error),
            operation_id=operation_id,
            active_version_before=before_version,
            active_slot_before=before_slot,
            rollback_performed=rollback_performed,
            source="skill_runtime_migration_worker",
        )
        entry["candidate_quarantine"] = marker
        entry["deactivation"] = marker
        entry["fallback_preserved"] = True
        entry["rollback_performed"] = rollback_performed
        entry["fallback_version"] = str(marker.get("fallback_version") or current_version)
        entry["fallback_slot"] = str(marker.get("fallback_slot") or current_slot)
        entry["handler_reload"] = _reload_live_skill_handlers_sync(ctx, name)
        return marker

    if rollback_error:
        entry["rollback_error"] = rollback_error
    deactivation = mgr.deactivate_runtime(
        name,
        reason="runtime_migration_failed",
        failure_kind="migration",
        failed_stage=str(entry.get("stage") or "unknown"),
        source="skill_runtime_migration_worker",
        committed_core_switch=False,
        status="quarantined",
        comment=str(error),
        operation_id=operation_id,
        transient=False,
    )
    entry["deactivation"] = deactivation
    entry["fallback_preserved"] = False
    return deactivation


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
    quarantine_before = quarantined_runtimes(ctx, mgr)
    failed_candidates_before = failed_candidate_runtimes(ctx, mgr)
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
        "quarantined_total": len(quarantine_before),
        "quarantined_skills": quarantine_before,
        "failed_candidate_total": len(failed_candidates_before),
        "failed_candidates": failed_candidates_before,
        "skills": candidates,
    }
    _write_status(ctx, payload)
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        name = str(candidate.get("skill") or "").strip()
        if not name:
            continue
        entry = dict(candidate)
        entry["stage"] = "refresh_runtime"
        entry["ok"] = False
        try:
            before = mgr.runtime_status(name)
        except Exception:
            before = {}
        entry["active_version_before"] = str(before.get("version") or "")
        entry["active_slot_before"] = str(before.get("active_slot") or "")
        payload["current"] = {"skill": name, "index": index, "stage": "refresh_runtime"}
        _write_status(ctx, payload)
        try:
            # A/B preparation is safe while the old slot remains active.  Do
            # not create an outage before prepare/tests have succeeded; an
            # explicit recovery keeps its existing quarantine until the new
            # slot activates and clears it atomically.
            entry["disabled_for_migration"] = False
            refresh = refresh_skill_runtime(
                mgr,
                name,
                webspace_id=webspace_id,
                source_version=str(candidate.get("workspace_version") or ""),
                migrate_runtime=True,
                # With sync_workspace=False the authoritative source has
                # already been materialized (for example by WorkspaceLock).
                # Calling the legacy ensure-installed path would start a git
                # pull/rebase inside that release-owned Workspace before the
                # runtime can be refreshed.
                ensure_installed=bool(sync_workspace),
                require_active_version=bool(candidate.get("workspace_version")),
                disable_during_migration=False,
                retry_deactivated=bool(candidate.get("deactivated")),
                # A core migration can activate dozens of skills. Rebuilding
                # the same webspace after every activation creates an update
                # storm and races YStore compaction. The worker performs one
                # authoritative rebuild after the complete batch below.
                defer_webspace_rebuild=True,
                run_candidate_tests=bool(run_tests),
            )
            entry["runtime_refresh"] = refresh
            if bool(refresh.get("skipped")) or bool(refresh.get("deactivated")):
                raise RuntimeError("runtime refresh left the skill deactivated")
            entry["tests"] = dict(refresh.get("tests") or {})
            entry["stage"] = "completed"
            entry["ok"] = True
            entry["deactivation_cleared"] = True
            entry["handler_reload"] = _reload_live_skill_handlers_sync(ctx, name)
        except RuntimeRefreshError as exc:
            entry["stage"] = str(exc.payload.get("failed_stage") or entry.get("stage") or "refresh_runtime")
            entry["ok"] = False
            entry["error"] = str(exc)
            entry["runtime_refresh"] = exc.payload
            try:
                _preserve_runtime_after_candidate_failure(
                    ctx,
                    mgr,
                    name=name,
                    candidate=candidate,
                    entry=entry,
                    before=before,
                    operation_id=operation_id,
                    error=exc,
                )
            except Exception as preserve_exc:
                entry["preservation_error"] = f"{type(preserve_exc).__name__}: {preserve_exc}"
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
            try:
                _preserve_runtime_after_candidate_failure(
                    ctx,
                    mgr,
                    name=name,
                    candidate=candidate,
                    entry=entry,
                    before=before,
                    operation_id=operation_id,
                    error=exc,
                )
            except Exception as preserve_exc:
                entry["preservation_error"] = f"{type(preserve_exc).__name__}: {preserve_exc}"
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
    quarantine_after = quarantined_runtimes(ctx, mgr)
    failed_candidates_after = failed_candidate_runtimes(ctx, mgr)
    final["quarantined_total"] = len(quarantine_after)
    final["quarantined_skills"] = quarantine_after
    final["failed_candidate_total"] = len(failed_candidates_after)
    final["failed_candidates"] = failed_candidates_after
    if not failed and quarantine_after:
        final["message"] = (
            "skill runtime migration completed; "
            f"{len(quarantine_after)} skill runtime(s) remain quarantined"
        )
    if not results:
        final["webspace_rebuild"] = {
            "ok": True,
            "skipped": True,
            "reason": "no_runtime_changes",
            "webspace_id": webspace_id,
        }
    else:
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
