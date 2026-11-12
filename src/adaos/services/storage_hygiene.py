from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CATALOG_VERSION = 1
MANIFEST_SCHEMA = "adaos.storage.entry.v1"
LEASE_SCHEMA = "adaos.storage.lease.v1"
PLAN_SCHEMA = "adaos.storage.cleanup_plan.v1"
JOB_SCHEMA = "adaos.storage.cleanup_job.v1"
MANIFEST_NAME = ".adaos-storage.json"
LEASE_NAME = ".adaos-lease.json"


@dataclass(frozen=True)
class StorageSpec:
    id: str
    relative_path: str
    scope: str
    data_class: str
    owner: str
    cleanup: str = "never"
    retention_seconds: int | None = None
    pressure_retention_seconds: int | None = None
    manifest_required: bool = False
    legacy_cleanup_allowed: bool = False
    selector: str = "all"
    description: str = ""


STORAGE_CATALOG: tuple[StorageSpec, ...] = (
    StorageSpec("adaos_base", ".", "base", "runtime-root", "adaos"),
    StorageSpec("git_metadata", ".git", "workspace", "protected-source", "git"),
    StorageSpec("github_configuration", ".github", "workspace", "source", "repository"),
    StorageSpec("vscode_configuration", ".vscode", "workspace", "source", "repository"),
    StorageSpec("python_environment", ".venv", "workspace", "managed-environment", "python-tooling"),
    StorageSpec("local_credentials", ".ssh", "workspace", "protected-data", "security"),
    StorageSpec("tracked_runtime_fixtures", ".runtime", "workspace", "source-fixtures", "repository"),
    StorageSpec("curated_artifacts", "artifacts", "workspace", "mixed-curated-source", "repository"),
    StorageSpec("public_docs", "docs-stable", "workspace", "source", "documentation"),
    StorageSpec("private_docs", "docs-development", "workspace", "nested-source", "documentation"),
    StorageSpec("source_tree", "src", "workspace", "source", "repository"),
    StorageSpec("test_tree", "tests", "workspace", "source", "repository"),
    StorageSpec("script_tree", "scripts", "workspace", "source", "repository"),
    StorageSpec("tool_tree", "tools", "workspace", "source", "repository"),
    StorageSpec("e2e_tree", "e2e", "workspace", "source-and-evidence", "e2e"),
    StorageSpec("example_tree", "examples", "workspace", "source", "repository"),
    StorageSpec("vendor_tree", "vendor", "workspace", "vendored-source", "repository"),
    StorageSpec("repository_i18n", "i18n", "workspace", "source", "repository"),
    StorageSpec("internal_sources", "internals", "workspace", "nested-source", "repository"),
    StorageSpec(
        "repository_temp",
        ".tmp",
        "workspace",
        "ephemeral",
        "development-tools",
        cleanup="children",
        retention_seconds=3 * 86400,
        pressure_retention_seconds=6 * 3600,
        manifest_required=True,
        legacy_cleanup_allowed=True,
        description="Agent, test, and one-off development scratch data.",
    ),
    StorageSpec(
        "legacy_codex_temp",
        ".codex-tmp",
        "workspace",
        "legacy-ephemeral",
        "codex",
        cleanup="children",
        retention_seconds=7 * 86400,
        pressure_retention_seconds=86400,
        manifest_required=True,
        legacy_cleanup_allowed=True,
        description="Legacy Codex scratch root; new writes belong under .tmp/codex.",
    ),
    StorageSpec("pytest_cache", ".pytest_cache", "workspace", "cache", "pytest", cleanup="self", retention_seconds=7 * 86400, pressure_retention_seconds=86400),
    StorageSpec("ruff_cache", ".ruff_cache", "workspace", "cache", "ruff", cleanup="self", retention_seconds=7 * 86400, pressure_retention_seconds=86400),
    StorageSpec("generated_build", "build", "workspace", "derived", "build-system", cleanup="self", retention_seconds=7 * 86400, pressure_retention_seconds=86400),
    StorageSpec("generated_site", "site", "workspace", "derived", "docs-build", cleanup="self", retention_seconds=7 * 86400, pressure_retention_seconds=86400),
    StorageSpec("generated_reports", "reports", "workspace", "derived", "test-tools", cleanup="children", retention_seconds=14 * 86400, pressure_retention_seconds=3 * 86400, manifest_required=True, legacy_cleanup_allowed=True),
    StorageSpec("e2e_evidence", "e2e/artifacts", "workspace", "evidence", "e2e", cleanup="children", retention_seconds=30 * 86400, pressure_retention_seconds=7 * 86400, manifest_required=True),
    StorageSpec("adaos_tmp", "tmp", "base", "ephemeral", "adaos-runtime", cleanup="children", retention_seconds=86400, pressure_retention_seconds=6 * 3600),
    StorageSpec("adaos_cache", "cache", "base", "cache", "adaos-runtime", cleanup="children", retention_seconds=7 * 86400, pressure_retention_seconds=86400),
    StorageSpec("adaos_build", "build", "base", "derived", "adaos-build", cleanup="children", retention_seconds=7 * 86400, pressure_retention_seconds=86400),
    StorageSpec(
        "adaos_logs",
        "logs",
        "base",
        "logs",
        "logging",
        cleanup="children",
        retention_seconds=7 * 86400,
        pressure_retention_seconds=2 * 86400,
        selector="rotated_logs",
        description="Only rotated logs are eligible; active log names remain owner-managed.",
    ),
    StorageSpec("adaos_diagnostics", "diagnostics", "base", "evidence", "diagnostics", cleanup="children", retention_seconds=14 * 86400, pressure_retention_seconds=3 * 86400, manifest_required=True),
    StorageSpec("adaos_test_runs", "test_runs", "base", "evidence", "test-tools", cleanup="children", retention_seconds=7 * 86400, pressure_retention_seconds=86400, manifest_required=True),
    StorageSpec("adaos_test_artifacts", "test-artifacts", "base", "evidence", "test-tools", cleanup="children", retention_seconds=7 * 86400, pressure_retention_seconds=86400, manifest_required=True),
    StorageSpec("adaos_trials", "trials", "base", "candidate-workspaces", "applications", description="Cleaned only from terminal candidate receipts."),
    StorageSpec("adaos_state", "state", "base", "durable-state", "component-owners"),
    StorageSpec("adaos_data", "data", "base", "durable-data", "component-owners"),
    StorageSpec("adaos_private", "private", "base", "protected-data", "security"),
    StorageSpec("adaos_keys", "keys", "base", "protected-data", "security"),
    StorageSpec("adaos_workspace", "workspace", "base", "stable-runtime", "applications"),
    StorageSpec("adaos_dev", "dev", "base", "development-runtime", "builder"),
    StorageSpec("adaos_recovery", "recovery", "base", "recovery", "recovery"),
    StorageSpec("adaos_archive", "archive", "base", "archive", "component-owners"),
    StorageSpec(
        "adaos_gc",
        "gc",
        "base",
        "cleanup-work",
        "maintenance",
        description="Quarantine and resumable cleanup work owned by storage hygiene.",
    ),
    StorageSpec("adaos_root_logs", "root_logs", "base", "logs", "root-operations", cleanup="children", retention_seconds=14 * 86400, pressure_retention_seconds=3 * 86400, selector="rotated_logs"),
    StorageSpec("adaos_root_log_snapshots", "root_log_snapshots", "base", "evidence", "root-operations", cleanup="children", retention_seconds=30 * 86400, pressure_retention_seconds=7 * 86400, manifest_required=True),
    StorageSpec("adaos_chrome_profile", "chrome-headless-profile", "base", "cache", "browser-runtime", cleanup="self", retention_seconds=7 * 86400, pressure_retention_seconds=86400, manifest_required=True, legacy_cleanup_allowed=True),
    StorageSpec("adaos_runtime", "runtime", "base", "operational", "runtime"),
    StorageSpec("adaos_models", "models", "base", "models", "model-runtime"),
    StorageSpec("adaos_assets", "assets", "base", "assets", "applications"),
    StorageSpec("adaos_mcp", "mcp", "base", "operational", "mcp"),
    StorageSpec("adaos_bin", "bin", "base", "operational", "runtime"),
    StorageSpec("adaos_release_infrastate", "release-infrastate", "base", "release-state", "release"),
    StorageSpec("adaos_stand_hotfix", "stand-hotfix", "base", "release-state", "release"),
    StorageSpec("adaos_i18n", "i18n", "base", "assets", "localization"),
)


def _utc_now(now: float | None = None) -> str:
    value = time.time() if now is None else float(now)
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _resolve_roots(
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> tuple[Path, Path]:
    workspace = Path(workspace_root or Path.cwd()).expanduser().resolve()
    base = Path(base_dir).expanduser().resolve() if base_dir is not None else (workspace / ".adaos").resolve()
    return workspace, base


def _spec_path(spec: StorageSpec, workspace: Path, base: Path) -> Path:
    root = workspace if spec.scope == "workspace" else base
    return (root / spec.relative_path).resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _workspace_key(path: Path, workspace: Path) -> str:
    resolved = path.expanduser().resolve()
    if not _is_relative_to(resolved, workspace):
        raise ValueError(f"path is outside workspace: {resolved}")
    return resolved.relative_to(workspace).as_posix()


def _maintenance_state(base: Path) -> Path:
    return base / "state" / "maintenance"


def _manifest_path(path: Path) -> Path:
    return path / MANIFEST_NAME if path.is_dir() else path.with_name(path.name + f".{MANIFEST_NAME}")


def _lease_path(path: Path) -> Path:
    return path / LEASE_NAME if path.is_dir() else path.with_name(path.name + f".{LEASE_NAME}")


def write_storage_manifest(
    path: str | os.PathLike[str],
    *,
    owner: str,
    status: str = "active",
    retention_class: str = "temporary",
    retain_seconds: int | None = None,
    created_at: float | str | None = None,
    pinned: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if target.exists() and not target.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target.mkdir(parents=True, exist_ok=True)
    created = _parse_timestamp(created_at)
    payload = {
        "schema": MANIFEST_SCHEMA,
        "owner": str(owner),
        "status": str(status),
        "retention_class": str(retention_class),
        "created_at": _utc_now(created),
        "updated_at": _utc_now(),
        "retain_seconds": None if retain_seconds is None else max(0, int(retain_seconds)),
        "pinned": bool(pinned),
        "metadata": dict(metadata or {}),
    }
    _atomic_write_json(_manifest_path(target), payload)
    return payload


def update_storage_manifest(path: str | os.PathLike[str], **changes: Any) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    manifest_path = _manifest_path(target)
    payload = _read_json(manifest_path)
    if not payload or payload.get("schema") != MANIFEST_SCHEMA:
        raise FileNotFoundError(f"storage manifest not found: {manifest_path}")
    allowed = {"status", "retention_class", "retain_seconds", "pinned", "metadata"}
    for key, value in changes.items():
        if key in allowed:
            payload[key] = value
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload


def acquire_storage_lease(
    path: str | os.PathLike[str],
    *,
    owner: str,
    ttl_seconds: int = 300,
    lease_id: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if target.exists() and not target.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target.mkdir(parents=True, exist_ok=True)
    now = time.time()
    payload = {
        "schema": LEASE_SCHEMA,
        "lease_id": str(lease_id or uuid.uuid4().hex),
        "owner": str(owner),
        "pid": os.getpid(),
        "heartbeat_at": _utc_now(now),
        "expires_at": _utc_now(now + max(1, int(ttl_seconds))),
    }
    _atomic_write_json(_lease_path(target), payload)
    return payload


def refresh_storage_lease(path: str | os.PathLike[str], *, lease_id: str, ttl_seconds: int = 300) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    lease_path = _lease_path(target)
    payload = _read_json(lease_path)
    if not payload or payload.get("schema") != LEASE_SCHEMA or payload.get("lease_id") != lease_id:
        raise RuntimeError("storage lease does not match")
    now = time.time()
    payload["heartbeat_at"] = _utc_now(now)
    payload["expires_at"] = _utc_now(now + max(1, int(ttl_seconds)))
    _atomic_write_json(lease_path, payload)
    return payload


def release_storage_lease(path: str | os.PathLike[str], *, lease_id: str) -> bool:
    target = Path(path).expanduser().resolve()
    lease_path = _lease_path(target)
    payload = _read_json(lease_path)
    if not payload or payload.get("lease_id") != lease_id:
        return False
    try:
        lease_path.unlink()
    except FileNotFoundError:
        return False
    return True


@contextmanager
def managed_storage_run(
    path: str | os.PathLike[str],
    *,
    owner: str,
    retention_class: str,
    success_retain_seconds: int = 0,
    failure_retain_seconds: int = 3 * 86400,
    lease_ttl_seconds: int = 24 * 3600,
    metadata: dict[str, Any] | None = None,
):
    """Mark a producer-owned run active and record its terminal retention state."""

    target = Path(path).expanduser().resolve()
    write_storage_manifest(
        target,
        owner=owner,
        status="active",
        retention_class=retention_class,
        retain_seconds=failure_retain_seconds,
        metadata=metadata,
    )
    lease = acquire_storage_lease(target, owner=owner, ttl_seconds=lease_ttl_seconds)
    try:
        yield target
    except BaseException:
        release_storage_lease(target, lease_id=lease["lease_id"])
        update_storage_manifest(
            target,
            status="failed",
            retain_seconds=failure_retain_seconds,
            metadata=metadata or {},
        )
        raise
    else:
        release_storage_lease(target, lease_id=lease["lease_id"])
        update_storage_manifest(
            target,
            status="complete",
            retain_seconds=success_retain_seconds,
            metadata=metadata or {},
        )


def _active_lease(path: Path, *, now: float) -> dict[str, Any] | None:
    payload = _read_json(_lease_path(path))
    if not payload or payload.get("schema") != LEASE_SCHEMA:
        return None
    expires = _parse_timestamp(payload.get("expires_at"))
    if expires is None or expires <= now:
        return None
    return payload


def _load_pins(base: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(_maintenance_state(base) / "pins.json") or {}
    pins = payload.get("pins")
    return pins if isinstance(pins, dict) else {}


def pin_storage_path(
    path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    reason: str = "manual",
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    key = _workspace_key(target, workspace)
    pins = _load_pins(base)
    pins[key] = {"created_at": _utc_now(), "reason": str(reason)}
    payload = {"schema": "adaos.storage.pins.v1", "pins": pins}
    _atomic_write_json(_maintenance_state(base) / "pins.json", payload)
    return {"ok": True, "path": key, "pin": pins[key]}


def unpin_storage_path(
    path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    key = _workspace_key(target, workspace)
    pins = _load_pins(base)
    removed = pins.pop(key, None)
    _atomic_write_json(_maintenance_state(base) / "pins.json", {"schema": "adaos.storage.pins.v1", "pins": pins})
    return {"ok": True, "path": key, "removed": removed is not None}


def _pin_for(path: Path, *, workspace: Path, pins: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    key = _workspace_key(path, workspace)
    for pinned_key, metadata in pins.items():
        normalized = str(pinned_key).strip("/")
        if key == normalized or key.startswith(normalized + "/"):
            return normalized, metadata
    return None


def _immediate_count(path: Path, *, limit: int) -> tuple[int, bool]:
    if not path.is_dir():
        return 0, False
    count = 0
    try:
        with os.scandir(path) as entries:
            for _entry in entries:
                count += 1
                if count > limit:
                    return limit, True
    except OSError:
        return 0, False
    return count, False


def inspect_storage(
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    max_entries_per_root: int = 1000,
    now: float | None = None,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    current_time = time.time() if now is None else float(now)
    pins = _load_pins(base)
    roots: list[dict[str, Any]] = []
    catalog_paths: set[Path] = set()
    for spec in STORAGE_CATALOG:
        path = _spec_path(spec, workspace, base)
        catalog_paths.add(path)
        exists = path.exists()
        count, truncated = _immediate_count(path, limit=max(1, int(max_entries_per_root))) if exists else (0, False)
        manifest = _read_json(_manifest_path(path)) if exists else None
        lease = _active_lease(path, now=current_time) if exists else None
        try:
            modified_at = _utc_now(path.stat().st_mtime) if exists else None
        except OSError:
            modified_at = None
        roots.append(
            {
                **asdict(spec),
                "path": str(path),
                "workspace_path": _workspace_key(path, workspace),
                "exists": exists,
                "immediate_entries": count,
                "scan_truncated": truncated,
                "modified_at": modified_at,
                "manifest": manifest,
                "active_lease": lease,
                "pin": _pin_for(path, workspace=workspace, pins=pins),
            }
        )
    unclassified: list[dict[str, Any]] = []
    for scope, parent in (("workspace", workspace), ("base", base)):
        if not parent.is_dir():
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                resolved = child.resolve()
                is_directory = child.is_dir()
            except OSError:
                continue
            if not is_directory or resolved in catalog_paths:
                continue
            unclassified.append(
                {
                    "scope": scope,
                    "path": str(resolved),
                    "workspace_path": _workspace_key(resolved, workspace),
                    "automatic_cleanup": False,
                    "reason": "unclassified_directory",
                }
            )
    return {
        "ok": True,
        "schema": "adaos.storage.inspect.v1",
        "catalog_version": CATALOG_VERSION,
        "workspace_root": str(workspace),
        "base_dir": str(base),
        "generated_at": _utc_now(current_time),
        "roots": roots,
        "unclassified_directories": sorted(unclassified, key=lambda item: (item["scope"], item["workspace_path"])),
    }


def _candidate_timestamp(path: Path, manifest: dict[str, Any] | None) -> float | None:
    if manifest:
        created = _parse_timestamp(manifest.get("created_at"))
        if created is not None:
            return created
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return None


def _candidate_decision(
    path: Path,
    *,
    spec: StorageSpec,
    workspace: Path,
    pins: dict[str, dict[str, Any]],
    now: float,
    profile: str,
    include_legacy: bool,
) -> tuple[dict[str, Any] | None, str]:
    if spec.selector == "rotated_logs":
        name = path.name.lower()
        rotated = bool(
            path.is_file()
            and (
                name.endswith((".gz", ".zip", ".old"))
                or re.search(r"\.(?:log|jsonl)\.\d+$", name)
            )
        )
        if not rotated:
            return None, "selector_excluded"
    pin = _pin_for(path, workspace=workspace, pins=pins)
    if pin:
        return None, "pinned"
    if _active_lease(path, now=now):
        return None, "active_lease"
    manifest = _read_json(_manifest_path(path))
    if manifest and bool(manifest.get("pinned")):
        return None, "manifest_pinned"
    status = str((manifest or {}).get("status") or "").strip().lower()
    if status in {"active", "running", "working", "queued", "pending"}:
        return None, f"active_status:{status}"
    if spec.manifest_required and not manifest:
        if not include_legacy:
            return None, "legacy_missing_manifest"
        if not spec.legacy_cleanup_allowed:
            return None, "legacy_evidence_requires_adoption"
    retention = spec.retention_seconds
    if profile == "pressure" and spec.pressure_retention_seconds is not None:
        retention = spec.pressure_retention_seconds
    manifest_retention = (manifest or {}).get("retain_seconds")
    if manifest_retention is not None:
        try:
            retention = max(0, int(manifest_retention))
        except (TypeError, ValueError):
            return None, "invalid_manifest_retention"
    created = _candidate_timestamp(path, manifest)
    if retention is None or created is None:
        return None, "retention_not_available"
    try:
        expected_mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None, "scan_error"
    age = max(0.0, now - created)
    if age < retention:
        return None, "within_retention"
    key = _workspace_key(path, workspace)
    return (
        {
            "spec_id": spec.id,
            "path": str(path),
            "workspace_path": key,
            "data_class": spec.data_class,
            "owner": spec.owner,
            "age_seconds": round(age, 3),
            "retention_seconds": int(retention),
            "expected_mtime_ns": expected_mtime_ns,
            "manifest_schema": (manifest or {}).get("schema"),
            "legacy": manifest is None,
            "reason": "retention_expired",
        },
        "candidate",
    )


def _plan_digest(payload: dict[str, Any]) -> str:
    canonical = {
        "schema": payload.get("schema"),
        "catalog_version": payload.get("catalog_version"),
        "workspace_root": payload.get("workspace_root"),
        "base_dir": payload.get("base_dir"),
        "profile": payload.get("profile"),
        "include_legacy": payload.get("include_legacy"),
        "candidates": payload.get("candidates", []),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iter_immediate_paths(root: Path) -> Iterable[Path]:
    with os.scandir(root) as entries:
        for entry in entries:
            yield Path(entry.path)


def plan_storage_cleanup(
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    profile: str = "normal",
    include_legacy: bool = False,
    max_candidates: int = 2000,
    now: float | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    if profile not in {"normal", "pressure"}:
        raise ValueError("profile must be normal or pressure")
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    current_time = time.time() if now is None else float(now)
    pins = _load_pins(base)
    candidates: list[dict[str, Any]] = []
    skipped_counts: dict[str, int] = {}
    scan_truncated = False
    for spec in STORAGE_CATALOG:
        if spec.cleanup not in {"self", "children"}:
            continue
        root = _spec_path(spec, workspace, base)
        if not root.exists():
            continue
        paths: Iterable[Path]
        if spec.cleanup == "self":
            paths = (root,)
        else:
            try:
                paths = _iter_immediate_paths(root)
            except OSError:
                skipped_counts["scan_error"] = skipped_counts.get("scan_error", 0) + 1
                continue
        try:
            for path in paths:
                if len(candidates) >= max(1, int(max_candidates)):
                    scan_truncated = True
                    break
                candidate, reason = _candidate_decision(
                    path,
                    spec=spec,
                    workspace=workspace,
                    pins=pins,
                    now=current_time,
                    profile=profile,
                    include_legacy=include_legacy,
                )
                if candidate:
                    candidates.append(candidate)
                else:
                    skipped_counts[reason] = skipped_counts.get(reason, 0) + 1
        except OSError:
            skipped_counts["scan_error"] = skipped_counts.get("scan_error", 0) + 1
        finally:
            close = getattr(paths, "close", None)
            if callable(close):
                close()
        if scan_truncated:
            break
    candidates.sort(key=lambda item: (str(item["spec_id"]), str(item["workspace_path"])))
    payload: dict[str, Any] = {
        "ok": True,
        "schema": PLAN_SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "workspace_root": str(workspace),
        "base_dir": str(base),
        "profile": profile,
        "include_legacy": bool(include_legacy),
        "generated_at": _utc_now(current_time),
        "candidates": candidates,
        "candidate_total": len(candidates),
        "skipped_counts": skipped_counts,
        "scan_truncated": scan_truncated,
    }
    payload["plan_digest"] = _plan_digest(payload)
    if persist:
        plan_path = _maintenance_state(base) / "plans" / f"{payload['plan_digest']}.json"
        _atomic_write_json(plan_path, payload)
        payload["plan_path"] = str(plan_path)
    return payload


def create_storage_cleanup_plan(
    candidates: Iterable[dict[str, Any]],
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    source: str,
    profile: str = "component",
    now: float | None = None,
) -> dict[str, Any]:
    """Persist a component-owned cleanup plan for the shared quarantine worker."""

    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    current_time = time.time() if now is None else float(now)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        path = Path(str(raw.get("path") or "")).expanduser().resolve()
        key = _workspace_key(path, workspace)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                **dict(raw),
                "spec_id": str(raw.get("spec_id") or source),
                "path": str(path),
                "workspace_path": key,
                "owner": str(raw.get("owner") or source),
                "reason": str(raw.get("reason") or "component_retention"),
            }
        )
    normalized.sort(key=lambda item: (str(item["spec_id"]), str(item["workspace_path"])))
    payload: dict[str, Any] = {
        "ok": True,
        "schema": PLAN_SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "workspace_root": str(workspace),
        "base_dir": str(base),
        "source": str(source),
        "profile": str(profile),
        "include_legacy": False,
        "generated_at": _utc_now(current_time),
        "candidates": normalized,
        "candidate_total": len(normalized),
        "skipped_counts": {},
        "scan_truncated": False,
    }
    payload["plan_digest"] = _plan_digest(payload)
    plan_path = _maintenance_state(base) / "plans" / f"{payload['plan_digest']}.json"
    _atomic_write_json(plan_path, payload)
    payload["plan_path"] = str(plan_path)
    return payload


def _validate_plan(payload: dict[str, Any], *, digest: str) -> None:
    if payload.get("schema") != PLAN_SCHEMA:
        raise ValueError("unsupported cleanup plan schema")
    actual = _plan_digest(payload)
    if actual != digest or payload.get("plan_digest") != digest:
        raise ValueError("cleanup plan digest does not match")


def _safe_destination_name(candidate: dict[str, Any]) -> str:
    source_name = Path(str(candidate["path"])).name or "root"
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in source_name)[:80]
    suffix = hashlib.sha256(str(candidate["workspace_path"]).encode("utf-8")).hexdigest()[:10]
    return f"{normalized}-{suffix}"


def _write_job(base: Path, job: dict[str, Any]) -> Path:
    path = _maintenance_state(base) / "jobs" / f"{job['job_id']}.json"
    _atomic_write_json(path, job)
    return path


def _native_path(path: Path) -> str:
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text.lstrip("\\")
    return "\\\\?\\" + text


def _make_writable(path: Path, *, directory: bool) -> None:
    mode = stat.S_IREAD | stat.S_IWRITE
    if directory:
        mode |= stat.S_IEXEC
    try:
        os.chmod(_native_path(path), mode)
    except OSError:
        pass


@dataclass
class _DeleteBudget:
    max_entries: int
    deadline: float
    entries: int = 0
    bytes: int = 0
    errors: int = 0

    def exhausted(self) -> bool:
        return self.entries >= self.max_entries or time.monotonic() >= self.deadline


def _unlink(path: Path, *, budget: _DeleteBudget) -> None:
    try:
        size = os.stat(_native_path(path), follow_symlinks=False).st_size
    except OSError:
        size = 0
    for attempt in range(2):
        try:
            os.unlink(_native_path(path))
            budget.entries += 1
            budget.bytes += int(size)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 0:
                _make_writable(path, directory=False)
                continue
            budget.errors += 1
            return
        except OSError:
            budget.errors += 1
            return


def _rmdir(path: Path, *, budget: _DeleteBudget) -> bool:
    for attempt in range(2):
        try:
            os.rmdir(_native_path(path))
            budget.entries += 1
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt == 0:
                _make_writable(path, directory=True)
                continue
            budget.errors += 1
            return False
        except OSError:
            return False
    return False


def _delete_tree_slice(path: Path, *, budget: _DeleteBudget) -> None:
    if budget.exhausted():
        return
    native = _native_path(path)
    try:
        metadata = os.lstat(native)
        # Windows junctions are directories, but os.path.islink() is false
        # for them. Never enumerate a reparse target while deleting a Trial.
        if getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            if not stat.S_ISLNK(metadata.st_mode) and stat.S_ISDIR(metadata.st_mode):
                _rmdir(path, budget=budget)
            else:
                _unlink(path, budget=budget)
            return
        if os.path.islink(native) or not os.path.isdir(native):
            _unlink(path, budget=budget)
            return
    except OSError:
        _unlink(path, budget=budget)
        return
    try:
        with os.scandir(native) as entries:
            for entry in entries:
                if budget.exhausted():
                    return
                _delete_tree_slice(Path(entry.path), budget=budget)
    except FileNotFoundError:
        return
    except PermissionError:
        _make_writable(path, directory=True)
        budget.errors += 1
        return
    if not budget.exhausted():
        _rmdir(path, budget=budget)


def _job_status(job: dict[str, Any]) -> str:
    states = {str(item.get("status")) for item in job.get("items", [])}
    if states <= {"complete", "missing", "skipped"}:
        return "complete"
    if "quarantined" in states or "deleting" in states:
        return "running"
    if "error" in states:
        return "blocked"
    return "pending"


def resume_storage_cleanup(
    job_id: str,
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    max_seconds: float = 2.0,
    max_entries: int = 500,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    job_path = _maintenance_state(base) / "jobs" / f"{job_id}.json"
    job = _read_json(job_path)
    if not job or job.get("schema") != JOB_SCHEMA:
        raise FileNotFoundError(f"cleanup job not found: {job_id}")
    if Path(str(job.get("workspace_root"))).resolve() != workspace:
        raise ValueError("cleanup job belongs to another workspace")
    budget = _DeleteBudget(max_entries=max(1, int(max_entries)), deadline=time.monotonic() + max(0.05, float(max_seconds)))
    for item in job.get("items", []):
        if budget.exhausted():
            break
        if item.get("status") not in {"quarantined", "deleting"}:
            continue
        quarantine = Path(str(item["quarantine_path"]))
        item["status"] = "deleting"
        before_entries = budget.entries
        before_bytes = budget.bytes
        before_errors = budget.errors
        _delete_tree_slice(quarantine, budget=budget)
        item["deleted_entries"] = int(item.get("deleted_entries") or 0) + (budget.entries - before_entries)
        item["deleted_bytes"] = int(item.get("deleted_bytes") or 0) + (budget.bytes - before_bytes)
        item["delete_errors"] = int(item.get("delete_errors") or 0) + (budget.errors - before_errors)
        item["updated_at"] = _utc_now()
        if not quarantine.exists():
            item["status"] = "complete"
            item["completed_at"] = _utc_now()
            item["stalled_slices"] = 0
        elif budget.entries == before_entries:
            item["stalled_slices"] = int(item.get("stalled_slices") or 0) + 1
            if int(item["stalled_slices"]) >= 3:
                item["status"] = "error"
                item["error"] = "deletion_stalled_after_three_slices"
        else:
            item["stalled_slices"] = 0
        _write_job(base, job)
    job["status"] = _job_status(job)
    job["updated_at"] = _utc_now()
    job["last_slice"] = {"entries": budget.entries, "bytes": budget.bytes, "errors": budget.errors}
    _write_job(base, job)
    return job


def list_storage_cleanup_jobs(
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    jobs_root = _maintenance_state(base) / "jobs"
    jobs: list[dict[str, Any]] = []
    if jobs_root.is_dir():
        paths = sorted(jobs_root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in paths[: max(1, int(limit))]:
            payload = _read_json(path)
            if not payload or payload.get("schema") != JOB_SCHEMA:
                continue
            jobs.append(
                {
                    "job_id": payload.get("job_id"),
                    "status": payload.get("status"),
                    "plan_digest": payload.get("plan_digest"),
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                    "item_count": len(payload.get("items") or []),
                    "path": str(path),
                }
            )
    return {
        "ok": True,
        "workspace_root": str(workspace),
        "base_dir": str(base),
        "jobs": jobs,
        "job_count": len(jobs),
        "active_count": sum(1 for item in jobs if item.get("status") in {"pending", "preparing", "running"}),
    }


def resume_pending_storage_jobs(
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    max_jobs: int = 3,
    max_seconds: float = 2.0,
    max_entries: int = 500,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    inventory = list_storage_cleanup_jobs(workspace_root=workspace, base_dir=base, limit=1000)
    active = [item for item in inventory["jobs"] if item.get("status") in {"pending", "preparing", "running"}]
    results = []
    deadline = time.monotonic() + max(0.05, float(max_seconds))
    remaining_entries = max(1, int(max_entries))
    for item in active[: max(1, int(max_jobs))]:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0 or remaining_entries <= 0:
            break
        result = resume_storage_cleanup(
            str(item["job_id"]),
            workspace_root=workspace,
            base_dir=base,
            max_seconds=remaining_seconds,
            max_entries=remaining_entries,
        )
        last_slice = result.get("last_slice") if isinstance(result.get("last_slice"), dict) else {}
        removed = int(last_slice.get("entries") or 0)
        remaining_entries -= removed
        results.append(
            {
                "job_id": result["job_id"],
                "status": result["status"],
                "last_slice": result.get("last_slice"),
            }
        )
    return {
        "ok": True,
        "workspace_root": str(workspace),
        "base_dir": str(base),
        "active_before": len(active),
        "resumed": results,
        "resumed_count": len(results),
    }


def apply_storage_cleanup_plan(
    plan_digest: str,
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    max_seconds: float = 2.0,
    max_entries: int = 500,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    plan_path = _maintenance_state(base) / "plans" / f"{plan_digest}.json"
    plan = _read_json(plan_path)
    if not plan:
        raise FileNotFoundError(f"cleanup plan not found: {plan_digest}")
    _validate_plan(plan, digest=plan_digest)
    if Path(str(plan.get("workspace_root"))).resolve() != workspace or Path(str(plan.get("base_dir"))).resolve() != base:
        raise ValueError("cleanup plan belongs to another workspace")
    job_id = uuid.uuid4().hex
    quarantine_root = base / "gc" / "quarantine" / job_id
    job: dict[str, Any] = {
        "schema": JOB_SCHEMA,
        "job_id": job_id,
        "plan_digest": plan_digest,
        "workspace_root": str(workspace),
        "base_dir": str(base),
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "preparing",
        "items": [],
    }
    _write_job(base, job)
    pins = _load_pins(base)
    now = time.time()
    for candidate in plan.get("candidates", []):
        source = Path(str(candidate["path"])).expanduser().resolve()
        item = {**candidate, "source_path": str(source), "status": "pending", "updated_at": _utc_now()}
        job["items"].append(item)
        _write_job(base, job)
        if not _is_relative_to(source, workspace) or source in {workspace, base}:
            item["status"] = "error"
            item["error"] = "unsafe_source_path"
            _write_job(base, job)
            continue
        if not source.exists():
            item["status"] = "missing"
            _write_job(base, job)
            continue
        if _pin_for(source, workspace=workspace, pins=pins):
            item["status"] = "skipped"
            item["reason"] = "pinned_after_plan"
            _write_job(base, job)
            continue
        current_manifest = _read_json(_manifest_path(source))
        current_status = str((current_manifest or {}).get("status") or "").strip().lower()
        if current_manifest and bool(current_manifest.get("pinned")):
            item["status"] = "skipped"
            item["reason"] = "manifest_pinned_after_plan"
            _write_job(base, job)
            continue
        if current_status in {"active", "running", "working", "queued", "pending"}:
            item["status"] = "skipped"
            item["reason"] = f"active_status_after_plan:{current_status}"
            _write_job(base, job)
            continue
        if _active_lease(source, now=now):
            item["status"] = "skipped"
            item["reason"] = "active_lease_after_plan"
            _write_job(base, job)
            continue
        expected_mtime_ns = candidate.get("expected_mtime_ns", candidate.get("mtime_ns"))
        if expected_mtime_ns is not None:
            try:
                changed = source.stat().st_mtime_ns != int(expected_mtime_ns)
            except (OSError, TypeError, ValueError):
                changed = True
            if changed:
                item["status"] = "skipped"
                item["reason"] = "changed_after_plan"
                _write_job(base, job)
                continue
        destination = quarantine_root / str(candidate["spec_id"]) / _safe_destination_name(candidate)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(_native_path(source), _native_path(destination))
        except OSError as exc:
            item["status"] = "error"
            item["error"] = str(exc)
            _write_job(base, job)
            continue
        item["quarantine_path"] = str(destination)
        item["status"] = "quarantined"
        item["quarantined_at"] = _utc_now()
        _write_job(base, job)
    job["status"] = _job_status(job)
    _write_job(base, job)
    return resume_storage_cleanup(
        job_id,
        workspace_root=workspace,
        base_dir=base,
        max_seconds=max_seconds,
        max_entries=max_entries,
    )


def explain_storage_path(
    path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    target = target.expanduser().resolve()
    key = _workspace_key(target, workspace)
    matches = []
    for spec in STORAGE_CATALOG:
        root = _spec_path(spec, workspace, base)
        if target == root or _is_relative_to(target, root):
            matches.append((len(root.parts), spec, root))
    matches.sort(key=lambda value: value[0], reverse=True)
    selected = matches[0] if matches else None
    pins = _load_pins(base)
    current_time = time.time() if now is None else float(now)
    return {
        "ok": True,
        "path": str(target),
        "workspace_path": key,
        "exists": target.exists(),
        "spec": asdict(selected[1]) if selected else None,
        "managed_root": str(selected[2]) if selected else None,
        "manifest": _read_json(_manifest_path(target)) if target.exists() else None,
        "active_lease": _active_lease(target, now=current_time) if target.exists() else None,
        "pin": _pin_for(target, workspace=workspace, pins=pins),
        "automatic_cleanup": bool(selected and selected[1].cleanup in {"self", "children"}),
    }


def adopt_legacy_storage_path(
    path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    owner: str | None = None,
    retention_class: str | None = None,
    retain_seconds: int | None = None,
    pinned: bool = False,
) -> dict[str, Any]:
    workspace, base = _resolve_roots(workspace_root=workspace_root, base_dir=base_dir)
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    target = target.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(target)
    explanation = explain_storage_path(target, workspace_root=workspace, base_dir=base)
    raw_spec = explanation.get("spec")
    if not isinstance(raw_spec, dict) or raw_spec.get("cleanup") not in {"self", "children"}:
        raise ValueError("path is not inside an automatically managed storage root")
    if retain_seconds is None:
        retain_seconds = raw_spec.get("retention_seconds")
    try:
        created_at = target.stat().st_mtime
    except OSError:
        created_at = time.time()
    manifest = write_storage_manifest(
        target,
        owner=str(owner or raw_spec.get("owner") or "legacy-adoption"),
        status="complete",
        retention_class=str(retention_class or raw_spec.get("data_class") or "legacy"),
        retain_seconds=None if retain_seconds is None else int(retain_seconds),
        created_at=created_at,
        pinned=pinned,
        metadata={"adopted_legacy": True, "adopted_at": _utc_now()},
    )
    return {
        "ok": True,
        "path": str(target),
        "workspace_path": _workspace_key(target, workspace),
        "spec_id": raw_spec.get("id"),
        "manifest": manifest,
    }


__all__ = [
    "CATALOG_VERSION",
    "LEASE_NAME",
    "MANIFEST_NAME",
    "STORAGE_CATALOG",
    "StorageSpec",
    "acquire_storage_lease",
    "adopt_legacy_storage_path",
    "apply_storage_cleanup_plan",
    "create_storage_cleanup_plan",
    "explain_storage_path",
    "inspect_storage",
    "list_storage_cleanup_jobs",
    "managed_storage_run",
    "pin_storage_path",
    "plan_storage_cleanup",
    "refresh_storage_lease",
    "release_storage_lease",
    "resume_pending_storage_jobs",
    "resume_storage_cleanup",
    "unpin_storage_path",
    "update_storage_manifest",
    "write_storage_manifest",
]
