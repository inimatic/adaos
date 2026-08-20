from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from adaos.adapters.db import SqliteSkillRegistry
from adaos.build_info import BUILD_INFO
from adaos.services.agent_context import AgentContext
from adaos.services.eventbus import emit as bus_emit
from adaos.services.runtime_refresh import (
    RuntimeRefreshError,
    rebuild_webspace_projection,
    rebuild_webspace_projection_sync,
    refresh_skill_runtime,
)
from adaos.services.skill.manager import SkillManager
from adaos.services.workspace_sync import selected_runtime_skill_names
from adaos.services.workspace_registry import build_registry_entry, list_workspace_registry_entries


_LOG = logging.getLogger("adaos.skill.runtime_migration")
_TASK: asyncio.Task[Any] | None = None
_PROCESS: asyncio.subprocess.Process | None = None
_LEASE_HANDLE: Any | None = None
_CANCELLING = False
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


def _lease_path(ctx: AgentContext) -> Path:
    return _status_dir(ctx) / "worker.lock"


def _try_acquire_global_lease(ctx: AgentContext, *, operation_id: str) -> Any | None:
    path = _lease_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return None
    metadata = {
        "schema": "adaos.skill_runtime_migration.lease.v1",
        "operation_id": str(operation_id or ""),
        "owner_pid": os.getpid(),
        "acquired_at": _now(),
    }
    try:
        handle.seek(1)
        handle.truncate()
        handle.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
        handle.flush()
    except Exception:
        _release_global_lease(handle)
        raise
    return handle


def _release_global_lease(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    with contextlib.suppress(Exception):
        handle.close()


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
    if str(os.getenv("ADAOS_SKILL_MIGRATION_WORKER_PROCESS") or "").strip() == "1":
        body.setdefault("worker_pid", os.getpid())
        body.setdefault("worker_mode", "subprocess")
        body.setdefault(
            "worker_priority",
            str(os.getenv("ADAOS_SKILL_MIGRATION_WORKER_PRIORITY") or "").strip() or "normal",
        )
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


def _workload_kind(command: list[str]) -> str:
    text = " ".join(command).lower()
    if "pytest" in text:
        return "skill_tests"
    if " pip " in f" {text} " and "install" in text:
        return "dependency_install"
    if "uv" in text and "install" in text:
        return "dependency_install"
    if "git" in text and any(token in text for token in ("clone", "fetch", "pull", "checkout")):
        return "workspace_sync"
    if "handlers.service" in text:
        return "skill_service"
    return "skill_worker_child"


def _redacted_command(command: list[str]) -> list[str]:
    result: list[str] = []
    redact_next = False
    for raw in command[:24]:
        value = str(raw)
        lowered = value.lower()
        if redact_next:
            result.append("***")
            redact_next = False
            continue
        if lowered in {"--token", "--password", "--secret", "--api-key", "--key"}:
            result.append(value)
            redact_next = True
            continue
        if any(marker in lowered for marker in ("token=", "password=", "secret=", "api_key=", "apikey=")):
            key, sep, _value = value.partition("=")
            result.append(f"{key}{sep}***")
            continue
        if "://" in value and "@" in value:
            scheme, _, remainder = value.partition("://")
            result.append(f"{scheme}://***@{remainder.rsplit('@', 1)[-1]}")
            continue
        result.append(value[:500])
    return result


def _worker_process_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        worker_pid = int(payload.get("worker_pid") or 0)
    except Exception:
        worker_pid = 0
    if worker_pid <= 0:
        return {"available": False, "worker_pid": None, "active_workloads": []}
    try:
        import psutil

        worker = psutil.Process(worker_pid)
        processes = [worker, *worker.children(recursive=True)]
    except Exception as exc:
        return {
            "available": False,
            "worker_pid": worker_pid,
            "alive": False,
            "error": f"{type(exc).__name__}: {exc}",
            "active_workloads": [],
        }
    rows: list[dict[str, Any]] = []
    for proc in processes[:24]:
        try:
            with proc.oneshot():
                command = list(proc.cmdline() or [])
                memory = proc.memory_info()
                cpu = proc.cpu_times()
                try:
                    io = proc.io_counters()
                except Exception:
                    io = None
                row = {
                    "pid": int(proc.pid),
                    "ppid": int(proc.ppid()),
                    "name": str(proc.name() or ""),
                    "kind": "migration_worker" if int(proc.pid) == worker_pid else _workload_kind(command),
                    "age_s": round(max(0.0, _now() - float(proc.create_time())), 3),
                    "cpu_total_s": round(float(cpu.user) + float(cpu.system), 3),
                    "rss_bytes": int(memory.rss),
                    "read_bytes": int(getattr(io, "read_bytes", 0) or 0) if io is not None else None,
                    "write_bytes": int(getattr(io, "write_bytes", 0) or 0) if io is not None else None,
                    "command": _redacted_command(command),
                }
        except Exception:
            continue
        rows.append(row)
    return {
        "available": True,
        "worker_pid": worker_pid,
        "alive": bool(rows),
        "worker_mode": str(payload.get("worker_mode") or "").strip() or None,
        "active_workloads": rows,
    }


def _core_update_migration_blocker(*, reason: str) -> dict[str, Any] | None:
    if str(reason or "").strip() == "core_update_post_promotion":
        return None
    try:
        from adaos.services.core_update import read_core_update_status

        status = read_core_update_status()
    except Exception:
        return None
    state = str(status.get("state") or "").strip().lower()
    if state not in {"planned", "countdown", "preparing", "restarting"}:
        return None
    return {
        "state": state,
        "phase": str(status.get("phase") or "").strip() or None,
        "target_version": str(status.get("target_version") or "").strip() or None,
    }


def _background_worker_command(command: list[str]) -> tuple[list[str], str]:
    result = [str(item) for item in command]
    mode = str(os.getenv("ADAOS_SKILL_MIGRATION_RESOURCE_PRIORITY", "below_normal") or "").strip().lower()
    if mode in {"", "0", "off", "none", "disabled", "normal"}:
        return result, "normal"
    if os.name == "nt":
        return result, "below_normal"
    nice = shutil.which("nice")
    if nice:
        result = [nice, "-n", "10", *result]
    if sys.platform.startswith("linux"):
        ionice = shutil.which("ionice")
        if ionice:
            result = [ionice, "-c", "2", "-n", "7", "--", *result]
    return result, "below_normal" if result != command else "normal"


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
    process_snapshot = _worker_process_snapshot(payload)
    suspected_blocker = _classify_status_blocker(payload, stale=stale, stage=stage, candidate=candidate)
    workload_kinds = {
        str(item.get("kind") or "")
        for item in list(process_snapshot.get("active_workloads") or [])
        if isinstance(item, dict)
    }
    if suspected_blocker is None and "dependency_install" in workload_kinds:
        suspected_blocker = "dependency_install_in_progress"
    if suspected_blocker is None and "skill_tests" in workload_kinds:
        suspected_blocker = "skill_tests_in_progress"
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
        "worker_process": process_snapshot,
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


def _core_runtime_identity() -> str:
    return _clean_text(BUILD_INFO.git_commit) or _clean_text(BUILD_INFO.version)


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
    names: set[str] = set(selected_runtime_skill_names(ctx))
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


def _installed_runtime_version_records(
    ctx: AgentContext,
    mgr: SkillManager,
    *,
    name: str | None = None,
) -> list[dict[str, Any]]:
    registry_versions = _registry_versions(ctx)
    requested_name = str(name or "").strip()
    names = {requested_name} if requested_name else set(_registered_skill_names(ctx))
    records: list[dict[str, Any]] = []
    for skill_name in sorted(names):
        source = _workspace_skill_source(ctx, skill_name)
        local_version = _read_local_artifact_version(source) if source.exists() else ""
        workspace_version = local_version or registry_versions.get(skill_name, "")
        try:
            runtime_state = mgr.runtime_status(skill_name)
        except Exception:
            runtime_state = {}
        runtime_version = _clean_text(runtime_state.get("version") if isinstance(runtime_state, dict) else "")
        deactivation = (
            runtime_state.get("deactivation")
            if isinstance(runtime_state, dict) and isinstance(runtime_state.get("deactivation"), dict)
            else {}
        )
        records.append(
            {
                "skill": skill_name,
                "workspace_version": workspace_version,
                "runtime_version": runtime_version,
                "source_path": str(source),
                "source_materialized": bool(local_version),
                "version_source": "workspace_manifest" if local_version else "workspace_registry",
                "runtime_behind": _runtime_is_behind(workspace_version, runtime_version),
                "deactivated": bool(runtime_state.get("deactivated")) if isinstance(runtime_state, dict) else False,
                "deactivation": deactivation,
            }
        )
    return records


def migration_candidates(
    ctx: AgentContext,
    mgr: SkillManager,
    *,
    force: bool = False,
    name: str | None = None,
) -> list[dict[str, Any]]:
    requested_name = str(name or "").strip()
    core_identity = _core_runtime_identity()
    # Workspace registry entries describe materialized artifacts, not install
    # intent.  Only the installed registry may enroll a skill in an automatic
    # runtime migration; an explicit request may still recover one by name.
    candidates: list[dict[str, Any]] = []
    for record in _installed_runtime_version_records(ctx, mgr, name=requested_name or None):
        name = str(record["skill"])
        workspace_version = str(record["workspace_version"])
        runtime_version = str(record["runtime_version"])
        deactivation = dict(record["deactivation"])
        precommit_migration_failure = (
            str(deactivation.get("reason") or "").strip() == "runtime_migration_failed"
            and not bool(deactivation.get("committed_core_switch"))
        )
        attempted_version = _clean_text(deactivation.get("attempted_version"))
        attempted_core_identity = _clean_text(deactivation.get("attempted_core_identity"))
        reason = "force" if force else ""
        if not force:
            explicitly_recovering = bool(requested_name and record["deactivated"])
            if explicitly_recovering:
                reason = "explicit_quarantine_recovery"
            elif bool(record["deactivated"]):
                candidate_changed = attempted_version != workspace_version
                core_changed = bool(core_identity and attempted_core_identity != core_identity)
                if precommit_migration_failure and (candidate_changed or core_changed):
                    reason = "recover_precommit_migration_failure" if candidate_changed else "recover_after_core_update"
                else:
                    # Post-commit quarantine remains fail-closed. A pre-commit
                    # failure is retried for a newer candidate or core build.
                    continue
            elif precommit_migration_failure and attempted_version == workspace_version:
                # The active fallback remains live. Do not retry the same
                # rejected candidate on every background discovery cycle.
                continue
            elif not bool(record["runtime_behind"]):
                continue
            else:
                reason = "runtime_version_behind"
        candidates.append(
            {
                "skill": name,
                "workspace_version": workspace_version,
                "runtime_version": runtime_version,
                "source_path": record["source_path"],
                "source_materialized": bool(record["source_materialized"]),
                "version_source": record["version_source"],
                "reason": reason,
                "deactivated": bool(record["deactivated"]),
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


async def _reload_owner_skill_handlers(ctx: AgentContext, name: str) -> dict[str, Any]:
    """Reload a selected runtime in the long-lived process that owns the bus."""

    try:
        from adaos.services.skills_loader_importlib import ImportlibSkillsLoader

        return await ImportlibSkillsLoader().reload_skill_handlers(ctx.paths.skills_dir(), name)
    except Exception as exc:
        return {"ok": False, "reason": "reload_failed", "error": str(exc)}


def _invalidate_owner_materialization(webspace_id: str, *, operation_id: str) -> dict[str, Any]:
    from adaos.services.scenario.webspace_runtime import invalidate_webspace_materialization_cache

    return invalidate_webspace_materialization_cache(
        webspace_id,
        reason=f"skill_runtime_migration:{operation_id}",
        action="skill_runtime_migration_live_finalize",
        source_of_truth="skill_runtime",
    )


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
    reload_fallback_handlers: bool = True,
) -> dict[str, Any]:
    before_version, before_slot = _runtime_selection(before)
    try:
        current = mgr.runtime_status(name)
    except Exception:
        current = {}
    current_version, current_slot = _runtime_selection(current)
    selection_changed = bool(
        before_version
        and (current_version != before_version or current_slot != before_slot)
    )
    rollback_performed = False
    rollback_error = ""
    if selection_changed:
        try:
            mgr.rollback_runtime(name)
            current = mgr.runtime_status(name)
            current_version, current_slot = _runtime_selection(current)
            if (current_version, current_slot) != (before_version, before_slot):
                mgr.restore_runtime_selection_exact(name, version=before_version, slot=before_slot)
                current = mgr.runtime_status(name)
                current_version, current_slot = _runtime_selection(current)
            rollback_performed = (current_version, current_slot) == (before_version, before_slot)
            if not rollback_performed:
                rollback_error = (
                    "runtime rollback did not restore exact fallback: "
                    f"expected={before_version}/{before_slot} actual={current_version or '-'}/{current_slot or '-'}"
                )
        except Exception as rollback_exc:
            rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"

    fallback_available = bool(
        before_version
        and before_slot
        and (current_version, current_slot) == (before_version, before_slot)
        and (not selection_changed or rollback_performed)
    )
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
            attempted_core_identity=_core_runtime_identity(),
        )
        entry["candidate_quarantine"] = marker
        entry["deactivation"] = marker
        entry["fallback_preserved"] = True
        entry["rollback_performed"] = rollback_performed
        entry["fallback_version"] = str(marker.get("fallback_version") or current_version)
        entry["fallback_slot"] = str(marker.get("fallback_slot") or current_slot)
        if reload_fallback_handlers:
            entry["worker_fallback_handler_validation"] = _reload_live_skill_handlers_sync(ctx, name)
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
        attempted_version=str(candidate.get("workspace_version") or ""),
        attempted_core_identity=_core_runtime_identity(),
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
    requested_name = str(name or "").strip() or None
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
            entry["stage"] = "handler_validation"
            handler_validation = _reload_live_skill_handlers_sync(ctx, name)
            entry["worker_handler_validation"] = handler_validation
            if not bool(handler_validation.get("ok")):
                raise RuntimeError(
                    "selected skill runtime handlers failed isolated validation: "
                    f"{handler_validation.get('error') or handler_validation.get('reason') or 'unknown failure'}"
                )
            entry["stage"] = "completed"
            entry["ok"] = True
            entry["deactivation_cleared"] = True
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
    remaining_runtime_drift = [
        item
        for item in _installed_runtime_version_records(ctx, mgr, name=requested_name)
        if bool(item.get("runtime_behind"))
    ]
    final["remaining_runtime_drift_total"] = len(remaining_runtime_drift)
    final["remaining_runtime_drift"] = remaining_runtime_drift
    if remaining_runtime_drift:
        final["ok"] = False
        final["state"] = "failed"
        final["message"] = (
            "skill runtime migration left "
            f"{len(remaining_runtime_drift)} installed runtime(s) behind workspace"
        )
    if not failed and quarantine_after:
        quarantine_message = (
            "skill runtime migration completed; "
            f"{len(quarantine_after)} skill runtime(s) remain quarantined"
        )
        if not remaining_runtime_drift:
            final["message"] = quarantine_message
    if not results:
        final["webspace_rebuild"] = {
            "ok": True,
            "skipped": True,
            "reason": "no_runtime_changes",
            "webspace_id": webspace_id,
        }
    elif str(os.getenv("ADAOS_SKILL_MIGRATION_WORKER_PROCESS") or "").strip() == "1":
        final["webspace_rebuild"] = {
            "ok": True,
            "deferred": True,
            "reason": "owner_live_finalization_required",
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


async def _finalize_owner_runtime_migration(
    ctx: AgentContext,
    status: dict[str, Any],
    *,
    operation_id: str,
    webspace_id: str,
) -> dict[str, Any]:
    """Apply subprocess runtime selections to the long-lived handler registry."""

    if str(status.get("operation_id") or "") != operation_id:
        raise RuntimeError("skill migration worker status belongs to a different operation")
    skills = [dict(item) for item in status.get("skills") or () if isinstance(item, dict)]
    successful = [item for item in skills if bool(item.get("ok"))]
    if not successful:
        return status

    live_status = {
        **status,
        "ok": False,
        "state": "running",
        "phase": "live_finalize",
        "message": "loading migrated skill handlers in the owning runtime",
        "pending": True,
        "skills": skills,
    }
    _write_status(ctx, live_status)
    mgr = _manager(ctx)
    finalized: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in successful:
        skill_name = str(item.get("skill") or "").strip()
        if not skill_name:
            continue
        item["stage"] = "live_handler_reload"
        reload_result = await _reload_owner_skill_handlers(ctx, skill_name)
        item["handler_reload"] = reload_result
        if bool(reload_result.get("ok")):
            item["stage"] = "live_handler_ready"
            finalized.append(item)
            continue

        error = RuntimeError(
            "selected skill runtime handlers failed live reload: "
            f"{reload_result.get('error') or reload_result.get('reason') or 'unknown failure'}"
        )
        item["ok"] = False
        item["error"] = str(error)
        try:
            before = {
                "version": str(item.get("active_version_before") or ""),
                "active_slot": str(item.get("active_slot_before") or ""),
            }
            await asyncio.to_thread(
                _preserve_runtime_after_candidate_failure,
                ctx,
                mgr,
                name=skill_name,
                candidate=item,
                entry=item,
                before=before,
                operation_id=operation_id,
                error=error,
                reload_fallback_handlers=False,
            )
            item["fallback_handler_reload"] = await _reload_owner_skill_handlers(ctx, skill_name)
        except Exception as exc:
            item["preservation_error"] = f"{type(exc).__name__}: {exc}"
        failed.append(item)

    materialization_cache: dict[str, Any] = {
        "ok": True,
        "skipped": True,
        "reason": "no_live_handlers_finalized",
    }
    webspace_rebuild: dict[str, Any] = {
        "ok": True,
        "skipped": True,
        "reason": "no_live_handlers_finalized",
        "webspace_id": webspace_id,
    }
    if finalized:
        materialization_cache = await asyncio.to_thread(
            _invalidate_owner_materialization,
            webspace_id,
            operation_id=operation_id,
        )
        if not bool(materialization_cache.get("ok", True)):
            raise RuntimeError(
                "webspace materialization invalidation failed after skill migration: "
                f"{materialization_cache.get('error') or materialization_cache.get('reason') or 'unknown failure'}"
            )
        bus = getattr(ctx, "bus", None)
        if bus is None:
            raise RuntimeError("skill migration owner event bus is unavailable")
        for item in finalized:
            skill_name = str(item.get("skill") or "").strip()
            bus_emit(
                bus,
                "skills.activated",
                {
                    "skill_name": skill_name,
                    "space": "default",
                    "webspace_id": webspace_id,
                    "defer_webspace_rebuild": True,
                    "source": "skill_runtime_migration",
                    "operation_id": operation_id,
                },
                "skill.runtime_migration",
            )
            item["activation_emitted"] = True
            item["stage"] = "completed"
        webspace_rebuild = await rebuild_webspace_projection(
            webspace_id=webspace_id,
            action="skill_runtime_migration_live_finalize",
            source_of_truth="skill_runtime",
        )

    remaining_runtime_drift = await asyncio.to_thread(
        _installed_runtime_version_records,
        ctx,
        mgr,
        name=str(status.get("name") or "").strip() or None,
    )
    remaining_runtime_drift = [item for item in remaining_runtime_drift if bool(item.get("runtime_behind"))]
    all_failed = [item for item in skills if not bool(item.get("ok"))]
    final = {
        **status,
        "ok": not all_failed and not remaining_runtime_drift,
        "state": "succeeded" if not all_failed and not remaining_runtime_drift else "failed",
        "phase": "complete",
        "pending": False,
        "skills": skills,
        "completed_total": len(skills),
        "failed_total": len(all_failed),
        "handler_reload_failed_total": len(failed),
        "live_finalized_total": len(finalized),
        "materialization_cache": materialization_cache,
        "webspace_rebuild": webspace_rebuild,
        "remaining_runtime_drift_total": len(remaining_runtime_drift),
        "remaining_runtime_drift": remaining_runtime_drift,
        "finished_at": _now(),
    }
    if failed:
        final["message"] = f"skill runtime migration failed live handler reload for {len(failed)} skill(s)"
    elif remaining_runtime_drift:
        final["message"] = (
            "skill runtime migration left "
            f"{len(remaining_runtime_drift)} installed runtime(s) behind workspace"
        )
    else:
        final["message"] = "skill runtime migration completed and live handlers were reloaded"
    final["elapsed_s"] = round(float(final["finished_at"]) - float(status.get("started_at") or final["finished_at"]), 3)
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
    global _PROCESS, _LEASE_HANDLE, _CANCELLING
    command = [
        sys.executable,
        "-m",
        "adaos.services.skill.runtime_migration_worker",
        "--operation-id",
        operation_id,
        "--webspace-id",
        webspace_id,
    ]
    if force:
        command.append("--force")
    if run_tests:
        command.append("--run-tests")
    if name:
        command.extend(["--name", name])
    if sync_workspace:
        command.append("--sync-workspace")
    command, worker_priority = _background_worker_command(command)
    env = dict(os.environ)
    env["ADAOS_SKILL_MIGRATION_WORKER_PROCESS"] = "1"
    env["ADAOS_SKILL_MIGRATION_WORKER_PRIORITY"] = worker_priority
    env["PYTHONUNBUFFERED"] = "1"
    popen_kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "env": env,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            | (0x00004000 if worker_priority == "below_normal" else 0)  # BELOW_NORMAL_PRIORITY_CLASS
        )
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = await asyncio.create_subprocess_exec(*command, **popen_kwargs)
        _PROCESS = proc
        current = read_status(ctx)
        _write_status(
            ctx,
            {
                **current,
                "worker_pid": int(proc.pid),
                "worker_mode": "subprocess",
                "worker_priority": worker_priority,
            },
        )
        stdout, stderr = await proc.communicate()
        if int(proc.returncode or 0) != 0:
            if _CANCELLING:
                _write_status(
                    ctx,
                    {
                        "ok": False,
                        "state": "cancelled",
                        "phase": "cancel",
                        "message": "skill runtime migration worker cancelled",
                        "pending": False,
                        "operation_id": operation_id,
                        "worker_pid": int(proc.pid),
                        "returncode": int(proc.returncode or 0),
                        "finished_at": _now(),
                    },
                )
            else:
                details = (stderr or stdout or b"").decode("utf-8", errors="replace")[-4000:]
                raise RuntimeError(f"worker exited rc={int(proc.returncode or 0)}: {details}")
        else:
            completed = read_status(ctx)
            completed.pop("diagnostics", None)
            await _finalize_owner_runtime_migration(
                ctx,
                completed,
                operation_id=operation_id,
                webspace_id=webspace_id,
            )
    except asyncio.CancelledError:
        _CANCELLING = True
        if _PROCESS is not None and _PROCESS.returncode is None:
            await _terminate_worker_process(_PROCESS)
        _write_status(
            ctx,
            {
                "ok": False,
                "state": "cancelled",
                "phase": "shutdown",
                "message": "skill runtime migration stopped with the owning runtime",
                "pending": False,
                "operation_id": operation_id,
                "worker_pid": int(_PROCESS.pid) if _PROCESS is not None else None,
                "finished_at": _now(),
            },
        )
        raise
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
    finally:
        _PROCESS = None
        _release_global_lease(_LEASE_HANDLE)
        _LEASE_HANDLE = None
        _CANCELLING = False


async def _terminate_worker_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            await asyncio.wait_for(killer.wait(), timeout=10.0)
        except Exception:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(int(proc.pid), signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(int(proc.pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5.0)


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
    global _TASK, _LEASE_HANDLE
    async with _LOCK:
        if _TASK is not None and not _TASK.done():
            status = read_status(ctx)
            return {"ok": True, "accepted": False, "reason": "already_running", "status": status}
        update_blocker = _core_update_migration_blocker(reason=reason)
        if update_blocker is not None:
            return {
                "ok": True,
                "accepted": False,
                "retryable": True,
                "reason": "core_update_active",
                "core_update": update_blocker,
                "status": read_status(ctx),
            }
        operation_id = f"skill-migrate-{uuid.uuid4().hex[:10]}"
        lease = _try_acquire_global_lease(ctx, operation_id=operation_id)
        if lease is None:
            return {
                "ok": True,
                "accepted": False,
                "retryable": True,
                "reason": "global_migration_running",
                "status": read_status(ctx),
            }
        _LEASE_HANDLE = lease
        target_webspace = str(webspace_id or "").strip() or _default_webspace_id()
        try:
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
                    "worker_mode": "subprocess",
                    "lease_path": str(_lease_path(ctx)),
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
        except Exception:
            _release_global_lease(_LEASE_HANDLE)
            _LEASE_HANDLE = None
            raise
        return {"ok": True, "accepted": True, "status": initial}


async def cancel_background_migration() -> None:
    global _TASK, _CANCELLING
    task = _TASK
    if task is None:
        return
    if not task.done():
        _CANCELLING = True
        if _PROCESS is not None and _PROCESS.returncode is None:
            await _terminate_worker_process(_PROCESS)
        with contextlib.suppress(BaseException):
            await task
    _TASK = None


def _parse_worker_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an isolated AdaOS skill runtime migration")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--webspace-id", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--sync-workspace", action="store_true")
    return parser.parse_args(argv)


def _worker_main(argv: list[str] | None = None) -> int:
    args = _parse_worker_args(argv)
    from adaos.apps.bootstrap import init_ctx
    from adaos.services.agent_context import get_ctx

    init_ctx()
    ctx = get_ctx()
    try:
        _run_migration_sync(
            ctx,
            operation_id=str(args.operation_id),
            webspace_id=str(args.webspace_id),
            force=bool(args.force),
            run_tests=bool(args.run_tests),
            name=str(args.name or "").strip() or None,
            sync_workspace=bool(args.sync_workspace),
        )
    except Exception as exc:
        _LOG.exception("isolated skill runtime migration failed")
        _write_status(
            ctx,
            {
                "ok": False,
                "state": "failed",
                "phase": "worker",
                "message": f"isolated skill runtime migration failed: {exc}",
                "pending": False,
                "operation_id": str(args.operation_id),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "finished_at": _now(),
            },
        )
        return 1
    # Candidate failures are represented in the structured status and are not
    # worker-process failures. Keep the process exit code for infrastructure
    # failures so the parent does not overwrite per-skill diagnostics.
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
