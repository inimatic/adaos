"""Budgeted development scratch maintenance; runtime storage has other owners."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from adaos.services import storage_hygiene as hygiene
from adaos.services.mutation_lock import mutation_lock

GIB = 1024**3
DEFAULT_POLICY = {
    "scratch_max_bytes": 20 * GIB,
    "min_free_bytes": 20 * GIB,
    "max_worktrees": 12,
    "worktree_retention_seconds": 3 * 86400,
    "scan_max_entries": 250_000,
    "scan_max_seconds": 10,
}


def repository_guard(path: Path, *, max_entries: int = 20_000, max_seconds: float = 0.5, intent_base: Path | None = None) -> str | None:
    """Fail closed before generic deletion can detach a nested Git checkout."""
    if intent_base is not None:
        try:
            intents = _read_intents(intent_base)
        except ValueError:
            return "invalid_git_removal_state"
        if any(Path(key).is_relative_to(path) or path.is_relative_to(Path(key)) for key in intents):
            return "unfinished_git_removal_requires_review"
    pending = [path]
    deadline = time.monotonic() + max_seconds
    entries = 0
    while pending:
        current = pending.pop()
        try:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return "linked_scratch_requires_owner"
            if not stat.S_ISDIR(info.st_mode):
                continue
            # Check directly, even when enumeration order places .git last.
            if os.path.lexists(current / ".git") or ((current / "HEAD").is_file() and (current / "objects").is_dir() and (current / "refs").is_dir()):
                return "git_checkout_requires_owner"
            if current != path:
                if hygiene._active_lease(current, now=time.time()):
                    return "nested_active_lease"
                manifest = hygiene._read_json(hygiene._manifest_path(current))
                if manifest and (manifest.get("pinned") or manifest.get("status") in {"active", "running", "working", "queued", "pending"}):
                    return "nested_owner_protected"
            with os.scandir(current) as children:
                for child in children:
                    entries += 1
                    if entries > max_entries or time.monotonic() > deadline:
                        return "repository_scan_incomplete"
                    if child.is_dir(follow_symlinks=False) or child.is_symlink():
                        pending.append(Path(child.path))
        except OSError:
            return "repository_scan_error"
    return None


def _policy(workspace: Path) -> dict[str, int]:
    path = workspace / ".github" / "workspace-storage.json"
    result = dict(DEFAULT_POLICY)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != "adaos.workspace.storage_policy.v1":
            raise ValueError("unsupported workspace storage policy")
        for key in result:
            value = raw.get(key, result[key])
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid workspace storage policy: {key}")
            result[key] = value
    return result


def _git(repository: Path, *args: str, timeout: float = 5) -> str:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repository), *args],
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])} failed ({result.returncode})")
    return result.stdout


def _repositories(workspace: Path) -> list[Path]:
    paths = [workspace, workspace / "docs-development", workspace / "docs-stable"]
    modules = workspace / ".gitmodules"
    if modules.is_file():
        import configparser

        config = configparser.ConfigParser(interpolation=None)
        config.read(modules, encoding="utf-8")
        paths.extend(workspace / config[section]["path"] for section in config.sections() if "path" in config[section])
    return sorted({path.resolve() for path in paths if (path / ".git").exists() and path.resolve().is_relative_to(workspace)})


def _worktrees(workspace: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records, errors = [], []
    scratch = workspace / ".tmp"
    deadline = time.monotonic() + 10
    for repository in _repositories(workspace):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append("worktree_inventory_time_budget")
                break
            output = _git(repository, "worktree", "list", "--porcelain", "-z", timeout=min(5, remaining))
            record: dict[str, Any] = {}
            for token in output.split("\0"):
                if not token:
                    if record:
                        path = Path(record["path"]).resolve()
                        if path.is_relative_to(scratch) and path != scratch:
                            records.append({**record, "path": str(path), "repository": str(repository)})
                    record = {}
                    continue
                key, _, value = token.partition(" ")
                record["path" if key == "worktree" else key] = value or True
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{repository.relative_to(workspace).as_posix()}: {type(exc).__name__}")
    return records, errors


def _usage(root: Path, *, max_entries: int, max_seconds: float) -> dict[str, Any]:
    """One bounded, link-free pass; partial bytes are explicitly a lower bound."""
    deadline = time.monotonic() + max_seconds
    pending = [(root, "")]
    buckets: dict[str, int] = {}
    entries, links, errors = 0, 0, 0
    while pending:
        if entries >= max_entries or time.monotonic() >= deadline:
            break
        current, bucket = pending.pop()
        try:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                links += 1
                continue
            if not stat.S_ISDIR(info.st_mode):
                buckets[bucket] = buckets.get(bucket, 0) + info.st_size
                continue
            with os.scandir(current) as children:
                for child in children:
                    entries += 1
                    if entries > max_entries or time.monotonic() >= deadline:
                        pending.append((current, bucket))
                        break
                    key = bucket or child.name
                    info = child.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        links += 1
                    elif stat.S_ISDIR(info.st_mode):
                        pending.append((Path(child.path), key))
                    else:
                        buckets[key] = buckets.get(key, 0) + info.st_size
        except FileNotFoundError:
            if current != root:
                errors += 1
        except OSError:
            errors += 1
    return {
        "bytes_lower_bound": sum(buckets.values()), "complete": not pending and not errors,
        "entries": entries, "skipped_links": links, "errors": errors,
        "largest_entries": [{"name": key, "bytes_lower_bound": value} for key, value in sorted(buckets.items(), key=lambda item: item[1], reverse=True)[:20]],
    }


def inspect_workspace_storage(workspace_root: Path, *, reserve_bytes: int = 0) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    policy = _policy(workspace)
    usage = _usage(workspace / ".tmp", max_entries=policy["scan_max_entries"], max_seconds=policy["scan_max_seconds"])
    worktrees, errors = _worktrees(workspace)
    present_worktrees = sum(Path(item["path"]).exists() for item in worktrees)
    disk = shutil.disk_usage(workspace)
    reserve = max(0, int(reserve_bytes))
    reasons = []
    if disk.free - reserve < policy["min_free_bytes"]:
        reasons.append("free_space_reserve")
    if usage["bytes_lower_bound"] + reserve > policy["scratch_max_bytes"]:
        reasons.append("scratch_budget_exceeded")
    if present_worktrees >= policy["max_worktrees"]:
        reasons.append("worktree_count_limit")
    if not usage["complete"]:
        reasons.append("scratch_usage_incomplete")
    if errors:
        reasons.append("worktree_inventory_incomplete")
    return {
        "schema": "adaos.workspace.storage_report.v1", "ok": not reasons,
        "workspace_root": str(workspace), "generated_at": hygiene._utc_now(),
        "policy": policy, "disk_free_bytes": disk.free, "reserved_bytes": reserve,
        "admission": "blocked" if reasons else "allowed", "reasons": reasons,
        "scratch": usage, "worktree_count": present_worktrees, "registration_count": len(worktrees), "worktrees": worktrees, "inventory_errors": errors,
    }


def _worktree_decision(record: dict[str, Any], workspace: Path, base: Path, policy: dict[str, int], now: float) -> str:
    path = Path(record["path"])
    scratch = workspace / ".tmp"
    if not path.is_relative_to(scratch) or path == scratch or path.resolve() != path:
        return "unsafe_worktree_path"
    if not path.exists():
        return "missing_registration_requires_review"
    if "locked" in record:
        return "git_worktree_locked"
    if _read_intents(base).get(str(path)):
        return "unfinished_git_removal_requires_review"
    pins = hygiene._load_pins(base)
    manifest = None
    # Worktree ownership is declared on its task directory, outside the checkout.
    # A manifest inside a checkout would itself be an untracked source file.
    for parent in (path, *path.parents):
        if parent == scratch:
            break
        if hygiene._pin_for(parent, workspace=workspace, pins=pins):
            return "pinned"
        if hygiene._active_lease(parent, now=now):
            return "active_lease"
        entry = hygiene._read_json(hygiene._manifest_path(parent))
        if entry:
            if entry.get("pinned") or entry.get("status") != "complete":
                return "owner_not_complete"
            if entry.get("schema") != hygiene.MANIFEST_SCHEMA:
                return "invalid_manifest"
            if manifest is None and parent != path:
                manifest = entry
    if manifest is None:
        return "unmanaged_worktree"
    finished = hygiene._parse_timestamp(manifest.get("completed_at") or manifest.get("updated_at"))
    try:
        retention = max(policy["worktree_retention_seconds"], int(manifest.get("retain_seconds") or 0))
    except (ValueError, TypeError):
        return "invalid_retention"
    if finished is None or now - finished < retention:
        return "within_retention"
    try:
        if _git(path, "rev-parse", "HEAD").strip() != record["HEAD"]:
            return "head_changed"
        if _git(path, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none").strip():
            return "dirty_worktree"
        # No fetch and no trust in an arbitrary feature remote: only admitted main.
        _git(path, "merge-base", "--is-ancestor", record["HEAD"], "refs/remotes/origin/main")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return "main_reachability_unconfirmed"
    return "eligible"


def _read_intents(base: Path) -> dict[str, Any]:
    path = base / "state/maintenance/worktree-removal-intents.json"
    if not path.exists():
        return {}
    payload = hygiene._read_json(path)
    if not payload or payload.get("schema") != "adaos.workspace.worktree_intents.v1" or not isinstance(payload.get("intents"), dict):
        raise ValueError("invalid worktree removal intent state; operator review required")
    return payload["intents"]


def _write_intent(base: Path, record: dict[str, Any], *, clear: bool = False) -> None:
    intents = _read_intents(base)
    if clear:
        intents.pop(record["path"], None)
    else:
        intents[record["path"]] = {"repository": record["repository"], "HEAD": record["HEAD"], "started_at": hygiene._utc_now()}
    hygiene._atomic_write_json(base / "state/maintenance/worktree-removal-intents.json", {"schema": "adaos.workspace.worktree_intents.v1", "intents": intents})


def plan_workspace_worktrees(workspace_root: Path, *, base_dir: Path | None = None, now: float | None = None) -> dict[str, Any]:
    workspace, base = hygiene._resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    records, errors = _worktrees(workspace)
    policy = _policy(workspace)
    current = time.time() if now is None else now
    deadline = time.monotonic() + 10
    for record in records:
        record["reason"] = _worktree_decision(record, workspace, base, policy, current) if time.monotonic() < deadline else "inspection_budget_exhausted"
    return {"ok": not errors, "worktrees": records, "inventory_errors": errors, "eligible_count": sum(record["reason"] == "eligible" for record in records)}


def maintain_workspace_storage(workspace_root: Path, *, apply: bool = False, base_dir: Path | None = None) -> dict[str, Any]:
    """Periodic, managed-only cleanup. Legacy directories always require adoption."""
    workspace, base = hygiene._resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    if apply:
        with mutation_lock(base / "state/maintenance/workspace.lock", timeout_s=0.2):
            return _maintain_workspace_storage(workspace, apply=True, base_dir=base)
    return _maintain_workspace_storage(workspace, base_dir=base)


def _maintain_workspace_storage(workspace_root: Path, *, apply: bool = False, base_dir: Path | None = None) -> dict[str, Any]:
    workspace, base = hygiene._resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    plan = plan_workspace_worktrees(workspace, base_dir=base)
    removed = []
    if apply and plan["ok"]:
        policy = _policy(workspace)
        deadline = time.monotonic() + 10
        for record in plan["worktrees"]:
            if record["reason"] != "eligible" or time.monotonic() >= deadline:
                continue
            # Check the registration again as well as HEAD, dirty state and leases.
            fresh, errors = _worktrees(workspace)
            current = next((item for item in fresh if item["path"] == record["path"] and item["repository"] == record["repository"]), None)
            if errors or current is None or current.get("HEAD") != record["HEAD"]:
                removed.append({"path": record["path"], "status": "registration_changed"})
                continue
            reason = _worktree_decision(current, workspace, base, policy, time.time())
            if reason != "eligible":
                removed.append({"path": record["path"], "status": reason})
                continue
            try:
                # Persist before mutation: a timeout/crash may leave a partial removal.
                # Such an attempt is held for review rather than blindly repeated.
                _write_intent(base, record)
                _git(Path(record["repository"]), "worktree", "remove", "--", record["path"], timeout=30)
                _write_intent(base, record, clear=True)
                removed.append({"path": record["path"], "status": "removed"})
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                removed.append({"path": record["path"], "status": "blocked", "error": type(exc).__name__})
    scratch_plan = hygiene.plan_storage_cleanup(workspace_root=workspace, base_dir=base, persist=False, spec_ids={"repository_temp"})
    # This routine owns only repository scratch, never runtime, evidence or recovery.
    candidates = [item for item in scratch_plan["candidates"] if item["spec_id"] == "repository_temp"]
    cleanup = None
    resumed = []
    if apply and candidates:
        exact = hygiene.create_storage_cleanup_plan(candidates, workspace_root=workspace, base_dir=base, source="workspace_storage")
        cleanup = hygiene.apply_storage_cleanup_plan(exact["plan_digest"], workspace_root=workspace, base_dir=base, max_entries=10_000, max_seconds=2)
    # Resume only jobs issued by this routine; unrelated owners have their own budgets.
    if apply:
        jobs = hygiene.list_storage_cleanup_jobs(workspace_root=workspace, base_dir=base)
        deadline = time.monotonic() + 2
        for job in jobs["jobs"]:
            if job["status"] not in {"pending", "running"} or time.monotonic() >= deadline:
                continue
            stored = hygiene._read_json(base / "state/maintenance/plans" / f"{job['plan_digest']}.json") or {}
            if stored.get("source") == "workspace_storage":
                result = hygiene.resume_storage_cleanup(job["job_id"], workspace_root=workspace, base_dir=base, max_entries=10_000, max_seconds=max(0.05, deadline - time.monotonic()))
                resumed.append({"job_id": result["job_id"], "status": result["status"]})
    report = inspect_workspace_storage(workspace)
    unfinished = _read_intents(base)
    result = {
        "schema": "adaos.workspace.maintenance.v1", "ok": plan["ok"] and not unfinished and not any(item["status"] in {"blocked", "error"} for item in [*removed, *resumed, *([cleanup] if cleanup else [])]),
        "dry_run": not apply, "report": report, "worktree_plan": plan, "worktree_results": removed,
        "scratch_candidates": candidates, "scratch_skipped": scratch_plan["skipped_counts"], "cleanup_job": cleanup, "resumed_jobs": resumed, "unfinished_git_removals": unfinished,
    }
    if apply:
        # Bounded receipt storage: replace latest, keep no ever-growing log history.
        hygiene._atomic_write_json(base / "state/maintenance/workspace-latest.json", result)
    return result
