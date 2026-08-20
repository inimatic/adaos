from __future__ import annotations

import asyncio
import contextlib
import json
import os
import requests
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from string import Formatter
from typing import Any

from adaos.domain import Event as DomainEvent
from adaos.services.agent_context import get_ctx
from adaos.services.bootstrap_update import BOOTSTRAP_CRITICAL_PATHS
from adaos.services.core_slots import (
    activate_slot,
    active_slot,
    choose_inactive_slot,
    previous_slot,
    read_slot_manifest,
    remove_inactive_slot,
    rollback_to_previous_slot,
    slot_dir,
)
from adaos.services.env_policy import env_bool
from adaos.services.runtime_paths import current_base_dir, current_control_python, current_repo_root, is_core_slot_path
from adaos.services.runtime_topology import supervisor_base_candidates_from_env


def _base_dir() -> Path:
    return current_base_dir()


def _state_root() -> Path:
    root = _base_dir() / "state" / "core_update"
    root.mkdir(parents=True, exist_ok=True)
    return root


def plan_path() -> Path:
    return _state_root() / "plan.json"


def status_path() -> Path:
    return _state_root() / "status.json"


def last_result_path() -> Path:
    return _state_root() / "last_result.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                transient = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {
                    5,
                    32,
                    33,
                }
                if not transient or attempt == 7:
                    raise
                time.sleep(min(0.005 * (2**attempt), 0.1))
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _target_version_matches(left: Any, right: Any) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a))


def _manifest_matches_target_version(manifest: dict[str, Any] | None, target_version: Any) -> bool:
    expected = str(target_version or "").strip()
    if not expected:
        return True
    data = manifest if isinstance(manifest, dict) else {}
    for key in ("target_version", "build_version", "git_commit", "git_short_commit"):
        if _target_version_matches(expected, data.get(key)):
            return True
    return False


def _runtime_boot_target_mismatch_status(
    current: dict[str, Any],
    *,
    slot: str,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    now = time.time()
    failed = dict(current)
    failed.update(
        {
            "state": "failed",
            "phase": "validate",
            "message": "runtime boot validation refused because active slot does not match requested target",
            "target_slot": slot or str(current.get("target_slot") or ""),
            "manifest": manifest if isinstance(manifest, dict) else {},
            "active_slot_target_mismatch": True,
            "active_slot_target_mismatch_reason": "active_slot_target_mismatch",
            "finished_at": now,
            "validated_at": None,
            "scheduled_for": None,
            "candidate_prewarm_state": None,
            "candidate_prewarm_message": None,
            "candidate_prewarm_ready_at": None,
        }
    )
    finalized = write_status(failed)
    clear_plan()
    return finalized


def _update_command_output_tail_chars() -> int:
    try:
        return max(1, int(str(os.getenv("ADAOS_CORE_UPDATE_OUTPUT_TAIL_CHARS") or "8000").strip() or "8000"))
    except Exception:
        return 8000


def _root_promotion_preflight_timeout_sec() -> float:
    raw = str(os.getenv("ADAOS_CORE_ROOT_PROMOTION_PREFLIGHT_TIMEOUT_SEC") or "300").strip()
    try:
        return max(45.0, min(float(raw), 900.0))
    except (TypeError, ValueError):
        return 300.0


class _OutputTailBuffer:
    def __init__(self, max_chars: int) -> None:
        self._max_chars = max(1, int(max_chars))
        self._parts: deque[str] = deque()
        self._total_chars = 0
        self._lock = threading.Lock()

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            self._parts.append(chunk)
            self._total_chars += len(chunk)
            while self._total_chars > self._max_chars and self._parts:
                overflow = self._total_chars - self._max_chars
                head = self._parts[0]
                if len(head) <= overflow:
                    self._parts.popleft()
                    self._total_chars -= len(head)
                    continue
                self._parts[0] = head[overflow:]
                self._total_chars -= overflow
                break

    def text(self) -> str:
        with self._lock:
            return "".join(self._parts)


def _stream_subprocess_output(
    stream: Any | None,
    *,
    buffer: _OutputTailBuffer,
) -> threading.Thread | None:
    if stream is None:
        return None

    def _worker() -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                buffer.append(str(chunk))
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    thread = threading.Thread(target=_worker, name="adaos-core-update-output", daemon=True)
    thread.start()
    return thread


def _run_command_with_bounded_output(command: str) -> subprocess.CompletedProcess[str]:
    max_chars = _update_command_output_tail_chars()
    stdout_buffer = _OutputTailBuffer(max_chars=max_chars)
    stderr_buffer = _OutputTailBuffer(max_chars=max_chars)
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    threads = [
        _stream_subprocess_output(proc.stdout, buffer=stdout_buffer),
        _stream_subprocess_output(proc.stderr, buffer=stderr_buffer),
    ]
    returncode = proc.wait()
    for thread in threads:
        if thread is not None:
            thread.join(timeout=1.0)
    return subprocess.CompletedProcess(
        command,
        int(returncode),
        stdout=stdout_buffer.text(),
        stderr=stderr_buffer.text(),
    )


def read_plan() -> dict[str, Any] | None:
    plan = _read_json(plan_path())
    if not isinstance(plan, dict):
        return None
    try:
        expires_at = float(plan.get("expires_at") or 0.0)
    except Exception:
        expires_at = 0.0
    if expires_at and time.time() > expires_at:
        clear_plan()
        write_status(
            {
                "state": "expired",
                "message": "pending update expired before autostart runner picked it up",
                "updated_at": time.time(),
            }
        )
        return None
    return plan


def write_plan(payload: dict[str, Any]) -> None:
    _write_json(plan_path(), payload)


def clear_plan() -> None:
    try:
        plan_path().unlink(missing_ok=True)
    except Exception:
        pass


def read_status() -> dict[str, Any]:
    return _read_json(status_path()) or {"state": "idle", "updated_at": time.time()}


def read_last_result() -> dict[str, Any] | None:
    payload = _read_json(last_result_path())
    return payload if isinstance(payload, dict) else None


def _supervisor_public_status_fields(status: dict[str, Any] | None) -> dict[str, Any]:
    payload = status if isinstance(status, dict) else {}
    return {
        "action": str(payload.get("action") or "").strip().lower() or None,
        "state": str(payload.get("state") or "").strip().lower() or "unknown",
        "phase": str(payload.get("phase") or "").strip().lower() or "",
        "message": str(payload.get("message") or "").strip(),
        "target_rev": str(payload.get("target_rev") or "").strip(),
        "target_version": str(payload.get("target_version") or "").strip(),
        "planned_reason": str(payload.get("planned_reason") or "").strip() or None,
        "min_update_period_sec": payload.get("min_update_period_sec"),
        "scheduled_for": payload.get("scheduled_for"),
        "subsequent_transition": bool(payload.get("subsequent_transition")),
        "subsequent_transition_requested_at": payload.get("subsequent_transition_requested_at"),
        "candidate_prewarm_state": str(payload.get("candidate_prewarm_state") or "").strip() or None,
        "candidate_prewarm_message": str(payload.get("candidate_prewarm_message") or "").strip() or None,
        "candidate_prewarm_ready_at": payload.get("candidate_prewarm_ready_at"),
        "restart_mode": str(payload.get("restart_mode") or "").strip() or None,
        "restart_requested_at": payload.get("restart_requested_at"),
        "updated_at": payload.get("updated_at"),
    }


def _supervisor_public_attempt_fields(status: dict[str, Any] | None) -> dict[str, Any]:
    payload = status if isinstance(status, dict) else {}
    state = str(payload.get("state") or "").strip().lower()
    return {
        "contract_version": "runtime_fallback.v1",
        "authority": "supervisor",
        "action": str(payload.get("action") or "").strip().lower() or None,
        "state": state or None,
        "awaiting_restart": bool(
            state in {"restarting", "succeeded"} and str(payload.get("phase") or "").strip().lower() in {"shutdown", "root_promoted"}
        ),
        "planned_reason": str(payload.get("planned_reason") or "").strip() or None,
        "scheduled_for": payload.get("scheduled_for"),
        "subsequent_transition": bool(payload.get("subsequent_transition")),
        "subsequent_transition_requested_at": payload.get("subsequent_transition_requested_at"),
        "candidate_prewarm_state": str(payload.get("candidate_prewarm_state") or "").strip() or None,
        "candidate_prewarm_message": str(payload.get("candidate_prewarm_message") or "").strip() or None,
        "restart_mode": str(payload.get("restart_mode") or "").strip() or None,
        "restart_requested_at": payload.get("restart_requested_at"),
        "updated_at": payload.get("updated_at"),
    }


def build_public_update_status_payload(
    status: dict[str, Any] | None,
    *,
    served_by: str = "runtime_fallback",
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": _supervisor_public_status_fields(status),
        "attempt": _supervisor_public_attempt_fields(status),
        "runtime": {},
        "_served_by": str(served_by or "runtime_fallback").strip() or "runtime_fallback",
    }


def _supervisor_public_base_candidates() -> list[str]:
    return supervisor_base_candidates_from_env(include_localhost=True, include_default_loopback=False)


def read_public_update_status(*, timeout_sec: float = 0.75) -> dict[str, Any]:
    fallback = build_public_update_status_payload(read_status(), served_by="runtime_fallback")
    if not (
        env_bool("ADAOS_SUPERVISOR_ENABLED")
        or str(os.getenv("ADAOS_SUPERVISOR_URL") or "").strip()
    ):
        return fallback
    headers = {"Accept": "application/json"}
    token = str(os.getenv("ADAOS_TOKEN") or "").strip()
    if token:
        headers["X-AdaOS-Token"] = token
    for base in _supervisor_public_base_candidates():
        session = requests.Session()
        try:
            try:
                session.trust_env = False
            except Exception:
                pass
            response = session.get(
                f"{base}/api/supervisor/public/update-status",
                headers=headers,
                timeout=max(0.1, float(timeout_sec)),
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
        finally:
            with contextlib.suppress(Exception):
                session.close()
    return fallback


_ROLLOUT_STATUS_KEYS = (
    "action",
    "target_rev",
    "target_version",
    "planned_reason",
    "min_update_period_sec",
    "scheduled_for",
    "subsequent_transition",
    "subsequent_transition_requested_at",
    "candidate_prewarm_state",
    "candidate_prewarm_message",
    "candidate_prewarm_ready_at",
)


def _rollout_status_identity_matches(payload: dict[str, Any], current: dict[str, Any]) -> bool:
    payload_action = str(payload.get("action") or "").strip().lower()
    current_action = str(current.get("action") or "").strip().lower()
    if payload_action and current_action and payload_action != current_action:
        return False

    payload_version = str(payload.get("target_version") or "").strip()
    current_version = str(current.get("target_version") or "").strip()
    payload_rev = str(payload.get("target_rev") or "").strip()
    current_rev = str(current.get("target_rev") or "").strip()
    payload_has_target = bool(payload_version or payload_rev)
    current_has_target = bool(current_version or current_rev)
    if not payload_has_target:
        return True
    if not current_has_target:
        return False
    if payload_version or current_version:
        return bool(
            payload_version
            and current_version
            and _target_version_matches(payload_version, current_version)
        )
    return bool(payload_rev and current_rev and payload_rev == current_rev)


def _hydrate_rollout_status_fields(payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    plan = merged.get("plan") if isinstance(merged.get("plan"), dict) else {}
    manifest = merged.get("manifest") if isinstance(merged.get("manifest"), dict) else {}
    state = str(merged.get("state") or "").strip().lower()
    if state == "idle" and not plan and not manifest:
        return merged

    if not str(merged.get("action") or "").strip():
        action = str(plan.get("action") or "").strip()
        if action:
            merged["action"] = action

    if not str(merged.get("target_rev") or "").strip():
        target_rev = str(plan.get("target_rev") or manifest.get("target_rev") or "").strip()
        if target_rev:
            merged["target_rev"] = target_rev

    if not str(merged.get("target_version") or "").strip():
        target_version = str(plan.get("target_version") or manifest.get("target_version") or "").strip()
        if target_version:
            merged["target_version"] = target_version

    current = read_status()
    if _rollout_status_identity_matches(merged, current):
        for key in _ROLLOUT_STATUS_KEYS:
            if key not in merged and key in current:
                merged[key] = current[key]

    if not str(merged.get("planned_reason") or "").strip():
        planned_reason = str(plan.get("reason") or "").strip()
        if planned_reason:
            merged["planned_reason"] = planned_reason

    return merged


def _set_status_default(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    if key not in payload or payload.get(key) is None or payload.get(key) == "":
        payload[key] = value


def _hydrate_install_status_fields_from_manifest(payload: dict[str, Any], manifest: dict[str, Any] | None) -> None:
    source = manifest if isinstance(manifest, dict) else {}
    install = source.get("install") if isinstance(source.get("install"), dict) else {}
    seed = source.get("venv_seed") if isinstance(source.get("venv_seed"), dict) else {}
    repair = seed.get("repair") if isinstance(seed.get("repair"), dict) else {}

    _set_status_default(payload, "install_elapsed_s", install.get("elapsed_s"))
    installer = str(install.get("installer") or "").strip()
    _set_status_default(payload, "install_installer", installer or None)

    seed_source = str(seed.get("source") or "").strip()
    _set_status_default(payload, "venv_seed_source", seed_source or None)
    if "venv_seeded" not in payload:
        payload["venv_seeded"] = bool(seed.get("seeded"))
    _set_status_default(payload, "venv_seed_copy_method", str(seed.get("copy_method") or "").strip() or None)
    _set_status_default(payload, "venv_seed_copy_elapsed_s", seed.get("copy_elapsed_s"))
    _set_status_default(payload, "venv_seed_elapsed_s", seed.get("elapsed_s"))
    _set_status_default(payload, "venv_seed_repair_elapsed_s", repair.get("elapsed_s"))
    _set_status_default(payload, "venv_repair_files_total", repair.get("repaired_files_total"))


def _is_terminal_status(payload: dict[str, Any]) -> bool:
    state = str(payload.get("state") or "").strip().lower()
    phase = str(payload.get("phase") or "").strip().lower()
    if state in {"failed", "validated", "succeeded", "rolled_back", "expired", "cancelled"}:
        return True
    return bool(state == "idle" and phase == "validate")


def manifest_requires_root_promotion(manifest: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    payload = manifest if isinstance(manifest, dict) else {}
    bootstrap = payload.get("bootstrap_update") if isinstance(payload.get("bootstrap_update"), dict) else {}
    required = bool(bootstrap.get("required"))
    return required, dict(bootstrap)


def _paths_equivalent(source: Path, target: Path) -> bool:
    if source.exists() != target.exists():
        return False
    if not source.exists():
        return True
    if source.is_dir() != target.is_dir():
        return False
    if source.is_dir():
        try:
            source_names = sorted(child.name for child in source.iterdir())
            target_names = sorted(child.name for child in target.iterdir())
        except Exception:
            return False
        if source_names != target_names:
            return False
        return all(_paths_equivalent(source / name, target / name) for name in source_names)
    try:
        source_bytes = source.read_bytes()
        target_bytes = target.read_bytes()
    except Exception:
        return False
    if source_bytes == target_bytes:
        return True
    try:
        source_text = source_bytes.decode("utf-8")
        target_text = target_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return source_text.replace("\r\n", "\n") == target_text.replace("\r\n", "\n")


def _root_checkout_contains_candidate_commit(
    manifest: dict[str, Any],
    root_dir: Path,
    checked_paths: list[str],
) -> tuple[bool, dict[str, Any]]:
    candidate_commit = str(
        manifest.get("git_commit")
        or manifest.get("resolved_target_version")
        or manifest.get("target_version")
        or ""
    ).strip()
    if not (7 <= len(candidate_commit) <= 40) or any(ch not in "0123456789abcdefABCDEF" for ch in candidate_commit):
        return False, {"effective_root_commit_relation": "candidate_commit_unavailable"}
    git = shutil.which("git")
    if not git or not (root_dir / ".git").exists():
        return False, {"effective_root_commit_relation": "root_git_unavailable"}
    try:
        ancestor = subprocess.run(
            [git, "merge-base", "--is-ancestor", candidate_commit, "HEAD"],
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        if ancestor.returncode != 0:
            return False, {"effective_root_commit_relation": "candidate_not_in_root_history"}
        clean = subprocess.run(
            [git, "diff", "--quiet", "HEAD", "--", *checked_paths],
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        if clean.returncode != 0:
            return False, {"effective_root_commit_relation": "root_bootstrap_paths_dirty"}
        head = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except Exception as exc:
        return False, {
            "effective_root_commit_relation": "root_git_check_failed",
            "effective_root_commit_relation_error": f"{type(exc).__name__}: {exc}",
        }
    root_commit = str(head.stdout or "").strip() if head.returncode == 0 else ""
    return True, {
        "effective_root_commit_relation": "contains_candidate",
        "effective_root_commit": root_commit,
        "effective_candidate_commit": candidate_commit,
    }


def resolved_root_promotion_requirement(manifest: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    required, bootstrap = manifest_requires_root_promotion(manifest)
    payload = manifest if isinstance(manifest, dict) else {}
    resolved = dict(bootstrap)
    changed_paths = bootstrap.get("changed_paths") if isinstance(bootstrap.get("changed_paths"), list) else []
    declared_paths = [str(item) for item in changed_paths if str(item).strip()]
    effective_paths = list(dict.fromkeys((*declared_paths, *BOOTSTRAP_CRITICAL_PATHS)))
    resolved["effective_changed_paths"] = list(effective_paths)
    resolved["effective_basis"] = "root_checkout_compare"
    resolved["declared_required"] = bool(required)
    resolved["effective_required"] = False

    repo_dir_raw = str(payload.get("repo_dir") or "").strip()
    source_repo_dir = Path(repo_dir_raw).expanduser().resolve() if repo_dir_raw else None
    root_dir, root_basis = _resolve_root_promotion_target(manifest)
    resolved["effective_target_root"] = str(root_dir) if root_dir is not None else ""
    resolved["effective_target_root_basis"] = root_basis

    if source_repo_dir is None or not source_repo_dir.exists():
        resolved["effective_unavailable_reason"] = "slot repo_dir is unavailable for root promotion comparison"
        return bool(required), resolved
    if root_dir is None or not root_dir.exists():
        resolved["effective_unavailable_reason"] = "root checkout is unavailable for root promotion comparison"
        return bool(required), resolved

    root_contains_candidate, root_relation = _root_checkout_contains_candidate_commit(
        payload,
        root_dir,
        effective_paths,
    )
    resolved.update(root_relation)
    if root_contains_candidate:
        resolved["effective_basis"] = "root_checkout_contains_candidate"
        resolved["effective_mismatched_paths"] = []
        return False, resolved

    mismatched_paths: list[str] = []
    for rel_path in effective_paths:
        source_path = (source_repo_dir / rel_path).resolve()
        target_path = (root_dir / rel_path).resolve()
        if not _paths_equivalent(source_path, target_path):
            mismatched_paths.append(rel_path)
    resolved["effective_mismatched_paths"] = mismatched_paths
    resolved["effective_required"] = bool(mismatched_paths)
    return bool(mismatched_paths), resolved


def _root_promotion_state_dir() -> Path:
    path = _base_dir() / "state" / "root_promotion"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _root_promotion_metadata_path(backup_dir: Path) -> Path:
    return (backup_dir / "metadata.json").resolve()


def _write_promotion_metadata_best_effort(backup_dir: Path, payload: dict[str, Any]) -> None:
    try:
        _write_json(_root_promotion_metadata_path(backup_dir), payload)
    except Exception:
        pass


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def _copy_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
        )
    else:
        shutil.copy2(source, target)


def _promotion_stage_path(target: Path, *, token: str) -> Path:
    return target.with_name(f".{target.name}.adaos-stage-{token}")


def _replace_promotion_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(8):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            transient = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {
                5,
                32,
                33,
            }
            if not transient or attempt == 7:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.25))


def _preflight_copy_file(source: str, target: str) -> str:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def _promotion_relative_paths(changed_paths: list[Any]) -> list[str]:
    normalized: list[str] = []
    for item in changed_paths:
        raw = str(item or "").strip().replace("\\", "/")
        if not raw:
            continue
        relative = Path(raw)
        if relative.is_absolute() or raw.startswith("/") or any(part in {"", ".", ".."} for part in relative.parts):
            raise RuntimeError(f"invalid root promotion path: {raw}")
        normalized.append(relative.as_posix())
    deduplicated = list(dict.fromkeys(normalized))
    if not any(path == "src/adaos" or path.startswith("src/adaos/") for path in deduplicated):
        return deduplicated

    # Root-launched control code is one import graph. Promoting only the files
    # that triggered the bootstrap comparison can leave their newly introduced
    # transitive modules behind, producing a hybrid candidate/root package.
    # Keep non-package metadata granular, but replace src/adaos atomically.
    collapsed: list[str] = []
    package_added = False
    for rel_path in deduplicated:
        if rel_path == "src/adaos" or rel_path.startswith("src/adaos/"):
            if not package_added:
                collapsed.append("src/adaos")
                package_added = True
            continue
        collapsed.append(rel_path)
    return collapsed


def _promotion_path(root: Path, rel_path: str) -> Path:
    resolved_root = root.resolve()
    resolved = (resolved_root / rel_path).resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise RuntimeError(f"root promotion path escapes its root: {rel_path}")
    return resolved


def _preflight_root_promotion(
    *,
    source_repo_dir: Path,
    root_dir: Path,
    changed_paths: list[str],
) -> dict[str, Any]:
    """Prove that the exact post-promotion Python package imports before mutation."""
    source_package = source_repo_dir / "src" / "adaos"
    root_package = root_dir / "src" / "adaos"
    # Small unit-test fixtures intentionally do not model a complete checkout.
    # AdaOS is a namespace package, so pyproject plus the supervisor entrypoint
    # are the stable markers of a real prepared slot.
    if not (source_repo_dir / "pyproject.toml").is_file() or not (
        source_package / "apps" / "supervisor.py"
    ).is_file():
        return {
            "ok": True,
            "skipped": True,
            "reason": "source_slot_checkout_markers_missing",
        }
    if not root_package.is_dir():
        raise RuntimeError(f"root package is unavailable for promotion preflight: {root_package}")

    state_dir = _root_promotion_state_dir()
    with tempfile.TemporaryDirectory(prefix="preflight-", dir=str(state_dir)) as temporary:
        candidate_root = Path(temporary).resolve()
        candidate_package = candidate_root / "src" / "adaos"
        candidate_package.mkdir(parents=True)
        projection_paths = {"apps"}
        for rel_path in changed_paths:
            parts = Path(rel_path).parts
            if len(parts) < 3 or parts[:2] != ("src", "adaos"):
                continue
            projection_paths.add(parts[2])
        for projection_path in sorted(projection_paths):
            source_projection = root_package / projection_path
            candidate_projection = candidate_package / projection_path
            if source_projection.is_dir():
                shutil.copytree(
                    source_projection,
                    candidate_projection,
                    copy_function=_preflight_copy_file,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "node_modules"),
                )
            elif source_projection.is_file():
                _preflight_copy_file(str(source_projection), str(candidate_projection))
        for rel_path in changed_paths:
            if rel_path != "src/adaos" and not rel_path.startswith("src/adaos/"):
                continue
            source_path = _promotion_path(source_repo_dir, rel_path)
            candidate_path = _promotion_path(candidate_root, rel_path)
            _remove_path(candidate_path)
            if source_path.exists():
                _copy_path(source_path, candidate_path)

        control_python = current_control_python(root_dir)
        if not control_python.exists():
            raise RuntimeError(f"root control Python is unavailable for promotion preflight: {control_python}")
        modules = (
            "adaos.apps.supervisor",
            "adaos.apps.cli.app",
            "adaos.apps.autostart_runner",
        )
        script = (
            "import importlib,json,pathlib,sys\n"
            "candidate=pathlib.Path(sys.argv[1]).resolve()\n"
            "loaded={}\n"
            "for name in sys.argv[2:]:\n"
            " module=importlib.import_module(name)\n"
            " loaded[name]=str(pathlib.Path(module.__file__).resolve())\n"
            "supervisor=pathlib.Path(loaded['adaos.apps.supervisor'])\n"
            "assert candidate in supervisor.parents, (candidate, supervisor)\n"
            "print(json.dumps(loaded, sort_keys=True))\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            (str((candidate_root / "src").resolve()), str((root_dir / "src").resolve()))
        )
        env["PYTHONNOUSERSITE"] = "1"
        timeout_sec = _root_promotion_preflight_timeout_sec()
        try:
            completed = subprocess.run(
                [str(control_python), "-c", script, str(candidate_root), *modules],
                cwd=str(candidate_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "root promotion import preflight timed out before root mutation "
                f"after {timeout_sec:.1f} seconds"
            ) from exc
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "import preflight failed").strip()[-4000:]
            raise RuntimeError(f"root promotion import preflight failed: {detail}")
        try:
            imported = json.loads(str(completed.stdout or "").strip().splitlines()[-1])
        except Exception:
            imported = {}
        return {
            "ok": True,
            "skipped": False,
            "control_python": str(control_python),
            "modules": list(modules),
            "imported": imported if isinstance(imported, dict) else {},
        }


def promote_root_from_slot(*, slot: str | None = None) -> dict[str, Any]:
    slot_name = str(slot or active_slot() or "").strip().upper()
    if not slot_name:
        raise RuntimeError("no active slot available for root promotion")
    manifest = read_slot_manifest(slot_name)
    if not isinstance(manifest, dict):
        raise RuntimeError(f"slot {slot_name} manifest is missing")
    root_promotion_required, bootstrap_update = resolved_root_promotion_requirement(manifest)
    if not root_promotion_required:
        resolved_root_dir, resolved_root_basis = _resolve_root_promotion_target(manifest)
        return {
            "ok": True,
            "slot": slot_name,
            "required": False,
            "target_root": str(resolved_root_dir or ""),
            "target_root_basis": resolved_root_basis,
            "changed_paths": [],
            "backup_dir": "",
            "promoted_paths": [],
            "removed_paths": [],
            "restart_required": False,
        }
    source_repo_dir = Path(str(manifest.get("repo_dir") or "")).expanduser().resolve()
    if not source_repo_dir.exists():
        raise RuntimeError(f"slot {slot_name} repo_dir is missing: {source_repo_dir}")
    root_dir, root_basis = _resolve_root_promotion_target(manifest)
    if root_dir is None or not root_dir.exists():
        raise RuntimeError("root checkout is unavailable for promotion")
    changed_paths = (
        bootstrap_update.get("effective_mismatched_paths")
        if isinstance(bootstrap_update.get("effective_mismatched_paths"), list)
        else []
    )
    if not changed_paths:
        changed_paths = (
            bootstrap_update.get("changed_paths") if isinstance(bootstrap_update.get("changed_paths"), list) else []
        )
    if not changed_paths:
        changed_paths = list(BOOTSTRAP_CRITICAL_PATHS)
    normalized_paths = _promotion_relative_paths(list(changed_paths))
    preflight = _preflight_root_promotion(
        source_repo_dir=source_repo_dir,
        root_dir=root_dir,
        changed_paths=normalized_paths,
    )
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup_dir = (
        _root_promotion_state_dir() / f"{stamp}-{time.time_ns()}-{slot_name.lower()}"
    ).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    promoted_paths: list[str] = []
    removed_paths: list[str] = []
    stage_token = f"{os.getpid()}-{time.time_ns()}"
    staged_paths: dict[str, Path] = {}
    mutated_paths: list[str] = []
    payload = {
        "ok": False,
        "slot": slot_name,
        "required": True,
        "target_root": str(root_dir),
        "target_root_basis": root_basis,
        "changed_paths": normalized_paths,
        "backup_dir": str(backup_dir),
        "backup_metadata_path": str(_root_promotion_metadata_path(backup_dir)),
        "promoted_paths": [],
        "removed_paths": [],
        "transaction_state": "staging",
        "backup_mode": "atomic_rename",
        "preflight": preflight,
        "restart_required": True,
    }
    _write_json(_root_promotion_metadata_path(backup_dir), payload)
    staging_started_at = time.monotonic()
    try:
        for rel_path in normalized_paths:
            source_path = _promotion_path(source_repo_dir, rel_path)
            if not source_path.exists():
                continue
            target_path = _promotion_path(root_dir, rel_path)
            stage_path = _promotion_stage_path(target_path, token=stage_token)
            _remove_path(stage_path)
            staged_paths[rel_path] = stage_path
            _copy_path(source_path, stage_path)
    except Exception as exc:
        payload["transaction_state"] = "staging_failed"
        payload["error"] = str(exc)
        _write_promotion_metadata_best_effort(backup_dir, payload)
        for stage_path in staged_paths.values():
            _remove_path(stage_path)
        raise RuntimeError(f"root promotion staging failed before cutover: {exc}") from exc
    payload["transaction_state"] = "staged"
    payload["staging_elapsed_s"] = round(time.monotonic() - staging_started_at, 3)
    payload["staged_paths"] = sorted(staged_paths)
    _write_json(_root_promotion_metadata_path(backup_dir), payload)
    cutover_started_at = time.monotonic()
    try:
        for rel_path in normalized_paths:
            source_path = _promotion_path(source_repo_dir, rel_path)
            target_path = _promotion_path(root_dir, rel_path)
            backup_path = _promotion_path(backup_dir, rel_path)
            if target_path.exists():
                _replace_promotion_path(target_path, backup_path)
                mutated_paths.append(rel_path)
            if source_path.exists():
                stage_path = staged_paths[rel_path]
                _replace_promotion_path(stage_path, target_path)
                if rel_path not in mutated_paths:
                    mutated_paths.append(rel_path)
                promoted_paths.append(rel_path)
            else:
                removed_paths.append(rel_path)
        payload["ok"] = True
        payload["transaction_state"] = "committed"
        payload["promoted_paths"] = promoted_paths
        payload["removed_paths"] = removed_paths
        payload["cutover_elapsed_ms"] = round((time.monotonic() - cutover_started_at) * 1000.0, 3)
        _write_json(_root_promotion_metadata_path(backup_dir), payload)
    except Exception as exc:
        payload["ok"] = False
        payload["transaction_state"] = "apply_failed"
        payload["error"] = str(exc)
        _write_promotion_metadata_best_effort(backup_dir, payload)
        try:
            restored_paths: list[str] = []
            for rel_path in reversed(mutated_paths):
                target_path = _promotion_path(root_dir, rel_path)
                backup_path = _promotion_path(backup_dir, rel_path)
                _remove_path(target_path)
                if backup_path.exists():
                    _replace_promotion_path(backup_path, target_path)
                restored_paths.append(rel_path)
            rollback = {
                "ok": True,
                "restored_paths": list(reversed(restored_paths)),
                "mode": "atomic_rename",
            }
        except Exception as rollback_exc:
            payload["transaction_state"] = "rollback_failed"
            payload["rollback_error"] = str(rollback_exc)
            _write_promotion_metadata_best_effort(backup_dir, payload)
            raise RuntimeError(
                f"root promotion failed and rollback failed: apply={exc}; rollback={rollback_exc}"
            ) from exc
        payload["transaction_state"] = "rolled_back"
        payload["rollback"] = rollback
        _write_promotion_metadata_best_effort(backup_dir, payload)
        raise RuntimeError(f"root promotion failed and was rolled back: {exc}") from exc
    finally:
        for stage_path in staged_paths.values():
            _remove_path(stage_path)
    return payload


def _publish_status_events(merged: dict[str, Any]) -> None:
    try:
        public_payload = build_public_update_status_payload(merged, served_by="runtime_fallback")
        get_ctx().bus.publish(
            DomainEvent(
                type="core.update.status",
                payload=dict(merged),
                source="core.update",
                ts=float(merged.get("updated_at") or time.time()),
            )
        )
        get_ctx().bus.publish(
            DomainEvent(
                type="supervisor.update.status.raw",
                payload=public_payload,
                source="core.update",
                ts=float(merged.get("updated_at") or time.time()),
            )
        )
    except Exception:
        pass


def write_status(payload: dict[str, Any], *, publish_events: bool = True) -> dict[str, Any]:
    merged = _hydrate_rollout_status_fields(payload)
    manifest = merged.get("manifest") if isinstance(merged.get("manifest"), dict) else None
    if manifest is not None:
        _hydrate_install_status_fields_from_manifest(merged, manifest)
    merged.setdefault("updated_at", time.time())
    _write_json(status_path(), merged)
    if _is_terminal_status(merged):
        _write_json(last_result_path(), merged)
    if publish_events:
        _publish_status_events(merged)
    return merged


async def write_status_async(payload: dict[str, Any]) -> dict[str, Any]:
    merged = await asyncio.to_thread(write_status, payload, publish_events=False)
    _publish_status_events(merged)
    return merged


def finalize_runtime_boot_status(
    *,
    supervisor_authorized: bool = False,
    publish_events: bool = True,
) -> dict[str, Any] | None:
    current = read_status()
    state = str(current.get("state") or "").strip().lower()
    phase = str(current.get("phase") or "").strip().lower()
    explicit_target_slot = str(current.get("target_slot") or "").strip().upper()
    slot = str(explicit_target_slot or active_slot() or "").strip().upper()
    manifest = read_slot_manifest(slot) if slot else None
    if state == "succeeded" and phase == "validate":
        return current
    root_restart_pending = state == "succeeded" and phase == "root_promoted"
    # A slot runtime can prove that the application booted, but it cannot prove
    # that the root-launched supervisor survived its own restart. Only the
    # supervisor control plane may commit that handoff.
    if root_restart_pending and not supervisor_authorized:
        return None
    if state not in {"restarting", "applying", "validated"} and not (
        state == "succeeded" and phase in {"", "apply", "launch", "shutdown", "root_promoted"}
    ):
        return None
    target_version = str(current.get("target_version") or "").strip()
    if target_version and not _manifest_matches_target_version(manifest, target_version):
        if not explicit_target_slot:
            return None
        return _runtime_boot_target_mismatch_status(current, slot=slot, manifest=manifest)

    now = time.time()
    payload = dict(current)
    payload["state"] = "succeeded"
    payload["phase"] = "validate"
    payload["message"] = (
        f"runtime boot validated on slot {slot}" if slot else "runtime boot validated"
    )
    if root_restart_pending:
        payload["message"] = (
            f"root promotion restart completed; runtime boot validated on slot {slot}"
            if slot
            else "root promotion restart completed; runtime boot validated"
        )
        payload["root_restart_completed_at"] = now
        payload["candidate_prewarm_state"] = None
        payload["candidate_prewarm_message"] = None
        payload["candidate_prewarm_ready_at"] = None
    payload["validated_at"] = now
    payload["finished_at"] = float(payload.get("finished_at") or now)
    payload["scheduled_for"] = None
    if slot:
        payload["target_slot"] = slot
    if isinstance(manifest, dict) and manifest:
        payload["manifest"] = manifest
        _hydrate_install_status_fields_from_manifest(payload, manifest)
    root_promotion_required, bootstrap_update = resolved_root_promotion_requirement(manifest)
    root_promotion_unavailable = str(bootstrap_update.get("effective_unavailable_reason") or "").strip()
    if root_promotion_required and (not root_restart_pending or not root_promotion_unavailable):
        payload["state"] = "validated"
        payload["phase"] = "root_promotion_pending"
        if root_restart_pending:
            payload["message"] = (
                f"root promotion restart completed on slot {slot}, but root source parity is still pending"
                if slot
                else "root promotion restart completed, but root source parity is still pending"
            )
        else:
            payload["message"] = (
                f"runtime boot validated on slot {slot}; root promotion pending"
                if slot
                else "runtime boot validated; root promotion pending"
            )
        payload["root_promotion_required"] = True
        payload["bootstrap_update"] = bootstrap_update
        payload["candidate_prewarm_state"] = None
        payload["candidate_prewarm_message"] = None
        payload["candidate_prewarm_ready_at"] = None
    elif root_restart_pending:
        payload["root_promotion_required"] = False
    else:
        payload["candidate_prewarm_state"] = None
        payload["candidate_prewarm_message"] = None
        payload["candidate_prewarm_ready_at"] = None
    finalized = write_status(payload, publish_events=publish_events)
    clear_plan()
    return finalized


async def finalize_runtime_boot_status_async(
    *, supervisor_authorized: bool = False
) -> dict[str, Any] | None:
    finalized = await asyncio.to_thread(
        finalize_runtime_boot_status,
        supervisor_authorized=supervisor_authorized,
        publish_events=False,
    )
    if finalized is not None:
        _publish_status_events(finalized)
    return finalized


def _repo_root() -> Path | None:
    return current_repo_root()


def _resolve_root_promotion_target(manifest: dict[str, Any] | None) -> tuple[Path | None, str]:
    payload = manifest if isinstance(manifest, dict) else {}
    explicit = str(payload.get("root_repo_root") or "").strip()
    if explicit:
        explicit_path = Path(explicit).expanduser().resolve()
        if is_core_slot_path(explicit_path):
            stable_root = _repo_root()
            if stable_root is not None and not is_core_slot_path(stable_root):
                return stable_root, "runtime_context.stable_root_over_manifest_slot"
        return explicit_path, "manifest.root_repo_root"
    resolved = _repo_root()
    if resolved is not None:
        if str(os.getenv("ADAOS_ROOT_REPO_ROOT") or os.getenv("ADAOS_REPO_ROOT") or "").strip():
            return resolved, "env.ADAOS_ROOT_REPO_ROOT"
        return resolved, "runtime_context"
    return None, "unavailable"


def restore_root_from_backup(
    *,
    backup_dir: str | Path,
    target_root: str | Path | None = None,
) -> dict[str, Any]:
    backup_path = Path(str(backup_dir)).expanduser().resolve()
    state_root = _root_promotion_state_dir().resolve()
    if backup_path != state_root and state_root not in backup_path.parents:
        raise RuntimeError("root promotion backup must live under state/root_promotion")
    if not backup_path.exists() or not backup_path.is_dir():
        raise RuntimeError(f"root promotion backup does not exist: {backup_path}")

    metadata = _read_json(_root_promotion_metadata_path(backup_path)) or {}
    metadata_target_root = str(metadata.get("target_root") or "").strip()
    changed_paths = [str(item) for item in metadata.get("changed_paths") or [] if str(item).strip()]
    if not changed_paths:
        changed_paths = []
        for child in backup_path.rglob("*"):
            if child.is_dir():
                continue
            if child == _root_promotion_metadata_path(backup_path):
                continue
            changed_paths.append(str(child.relative_to(backup_path)).replace("\\", "/"))

    explicit_target = str(target_root or "").strip()
    resolved_target_root = explicit_target or metadata_target_root
    target_basis = "argument.target_root" if explicit_target else "backup.metadata.target_root"
    if not resolved_target_root:
        fallback = _repo_root()
        if fallback is None:
            raise RuntimeError("target root is unavailable for root promotion restore")
        resolved_target_root = str(fallback)
        target_basis = "runtime_context"
    root_dir = Path(resolved_target_root).expanduser().resolve()
    root_dir.mkdir(parents=True, exist_ok=True)

    restored_paths: list[str] = []
    removed_paths: list[str] = []
    for rel_path in changed_paths:
        normalized_path = _promotion_relative_paths([rel_path])[0]
        source_path = _promotion_path(backup_path, normalized_path)
        target_path = _promotion_path(root_dir, normalized_path)
        if source_path.exists():
            _remove_path(target_path)
            _copy_path(source_path, target_path)
            restored_paths.append(normalized_path)
        else:
            _remove_path(target_path)
            removed_paths.append(normalized_path)

    return {
        "ok": True,
        "backup_dir": str(backup_path),
        "target_root": str(root_dir),
        "target_root_basis": target_basis,
        "changed_paths": changed_paths,
        "restored_paths": restored_paths,
        "removed_paths": removed_paths,
        "restart_required": True,
        "metadata": metadata,
    }


def rollback_installed_skill_runtimes() -> dict[str, Any]:
    try:
        from adaos.adapters.db import SqliteSkillRegistry
        from adaos.services.skill.manager import SkillManager
    except Exception as exc:
        return {
            "ok": False,
            "total": 0,
            "failed_total": 1,
            "rollback_total": 0,
            "skipped_total": 0,
            "skills": [],
            "error": f"skill rollback helpers unavailable: {exc}",
        }

    try:
        ctx = get_ctx()
        mgr = SkillManager(
            repo=ctx.skills_repo,
            registry=SqliteSkillRegistry(ctx.sql),
            git=ctx.git,
            paths=ctx.paths,
            bus=getattr(ctx, "bus", None),
            caps=ctx.caps,
        )
        reg = SqliteSkillRegistry(ctx.sql)
    except Exception as exc:
        return {
            "ok": False,
            "total": 0,
            "failed_total": 1,
            "rollback_total": 0,
            "skipped_total": 0,
            "skills": [],
            "error": f"skill rollback init failed: {exc}",
        }

    items: list[dict[str, Any]] = []
    for row in reg.list():
        name = getattr(row, "name", None) or getattr(row, "id", None)
        if not name or not bool(getattr(row, "installed", True)):
            continue
        skill_name = str(name)
        entry: dict[str, Any] = {
            "skill": skill_name,
            "ok": True,
            "skipped": False,
        }
        try:
            entry["restored_slot"] = str(mgr.rollback_runtime(skill_name) or "")
        except Exception as exc:
            error_text = str(exc)
            lowered = error_text.lower()
            if (
                "no previous slot recorded" in lowered
                or "previous slot matches current" in lowered
                or "no active version" in lowered
            ):
                entry["skipped"] = True
                entry["reason"] = error_text
            else:
                entry["ok"] = False
                entry["error"] = error_text
        items.append(entry)

    failed_total = sum(1 for item in items if not bool(item.get("ok")))
    rollback_total = sum(1 for item in items if bool(item.get("restored_slot")))
    skipped_total = sum(1 for item in items if bool(item.get("skipped")))
    return {
        "ok": failed_total == 0,
        "total": len(items),
        "failed_total": failed_total,
        "rollback_total": rollback_total,
        "skipped_total": skipped_total,
        "skills": items,
    }


def _repo_current_branch(repo_root: Path | None = None) -> str:
    root = repo_root or _repo_root()
    if root is None:
        return ""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        return ""
    branch = str(completed.stdout or "").strip()
    return "" if branch.upper() == "HEAD" else branch


def _shared_dotenv_path() -> str:
    raw = str(os.getenv("ADAOS_SHARED_DOTENV_PATH") or "").strip()
    if raw:
        return raw
    slot = active_slot()
    manifest = read_slot_manifest(slot) if slot else None
    env = manifest.get("env") if isinstance(manifest, dict) else None
    if not isinstance(env, dict):
        return ""
    return str(env.get("ADAOS_SHARED_DOTENV_PATH") or "").strip()


def _format_update_command(template: str, plan: dict[str, Any]) -> str:
    repo_root = _repo_root()
    control_python = current_control_python(repo_root)
    values = {
        "target_rev": str(plan.get("target_rev") or ""),
        "target_version": str(plan.get("target_version") or ""),
        "target_slot": str(plan.get("target_slot") or ""),
        "inactive_slot": str(plan.get("inactive_slot") or ""),
        "inactive_slot_dir": str(plan.get("inactive_slot_dir") or ""),
        "active_slot": str(plan.get("active_slot") or ""),
        "active_slot_dir": str(plan.get("active_slot_dir") or ""),
        "reason": str(plan.get("reason") or ""),
        "base_dir": str(_base_dir()),
        "python": str(control_python),
        "repo_root": str(repo_root or ""),
        "source_repo_root": str(repo_root or ""),
        "shared_dotenv_path": _shared_dotenv_path(),
    }
    fields = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    for field in fields:
        values.setdefault(field, "")
    return template.format(**values)


def _default_update_command_template() -> str:
    return (
        '"{python}" -m adaos.apps.core_update_apply'
        ' --target-rev "{target_rev}"'
        ' --target-version "{target_version}"'
        ' --slot "{target_slot}"'
        ' --slot-dir "{inactive_slot_dir}"'
        ' --base-dir "{base_dir}"'
        ' --repo-root "{repo_root}"'
        ' --source-repo-root "{source_repo_root}"'
        ' --shared-dotenv-path "{shared_dotenv_path}"'
        ' --prepare-lease-path "{prepare_lease_path}"'
        ' --prepare-lease-token "{prepare_lease_token}"'
    )


def configured_update_command(plan: dict[str, Any]) -> str | None:
    cmd = str(os.getenv("ADAOS_CORE_UPDATE_CMD") or "").strip()
    if not cmd:
        cmd = _default_update_command_template()
    try:
        return _format_update_command(cmd, plan)
    except Exception:
        return cmd


def _plan_with_slot_context(plan: dict[str, Any]) -> dict[str, Any]:
    payload = dict(plan)
    payload["active_slot"] = active_slot() or ""
    payload["previous_slot"] = previous_slot() or ""
    payload["target_slot"] = str(plan.get("target_slot") or choose_inactive_slot())
    payload["inactive_slot"] = payload["target_slot"]
    payload["inactive_slot_dir"] = str(slot_dir(payload["target_slot"]))
    payload.setdefault("prepare_lease_path", "")
    payload.setdefault("prepare_lease_token", "")
    if payload["active_slot"]:
        payload["active_slot_dir"] = str(slot_dir(payload["active_slot"]))
    else:
        payload["active_slot_dir"] = ""
    if not str(payload.get("target_rev") or "").strip():
        active_manifest = read_slot_manifest(payload["active_slot"]) if payload["active_slot"] else None
        resolved_rev = str(
            (active_manifest or {}).get("target_rev")
            or os.getenv("ADAOS_REV")
            or os.getenv("ADAOS_INIT_REV")
            or _repo_current_branch()
            or ""
        ).strip()
        payload["target_rev"] = resolved_rev
    return payload


def prepare_pending_update(plan: dict[str, Any]) -> dict[str, Any]:
    slot_plan = _plan_with_slot_context(plan)
    started_at = time.time()
    target_slot = str(slot_plan.get("target_slot") or "").strip().upper()
    if not target_slot:
        return {
            "state": "failed",
            "phase": "prepare",
            "message": "target slot is unavailable for preparation",
            "started_at": started_at,
            "finished_at": time.time(),
            "plan": slot_plan,
        }
    try:
        from adaos.apps.core_update_apply import prepare_slot

        repo_root = _repo_root()
        manifest = prepare_slot(
            slot=target_slot,
            slot_dir_path=str(slot_plan.get("inactive_slot_dir") or ""),
            base_dir=str(_base_dir()),
            repo_root=str(repo_root or ""),
            source_repo_root=str(repo_root or ""),
            shared_dotenv_path=_shared_dotenv_path(),
            target_rev=str(slot_plan.get("target_rev") or ""),
            target_version=str(slot_plan.get("target_version") or ""),
            migrate_skill_runtimes=False,
            prepare_lease_path=str(slot_plan.get("prepare_lease_path") or ""),
            prepare_lease_token=str(slot_plan.get("prepare_lease_token") or ""),
        )
    except Exception as exc:
        finished_at = time.time()
        return {
            "state": "failed",
            "phase": "prepare",
            "message": f"core update slot preparation failed: {exc}",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "target_slot": target_slot,
            "started_at": started_at,
            "finished_at": finished_at,
            "prepare_elapsed_s": round(finished_at - started_at, 3),
            "plan": slot_plan,
        }
    finished_at = time.time()
    install = manifest.get("install") if isinstance(manifest, dict) and isinstance(manifest.get("install"), dict) else {}
    seed = manifest.get("venv_seed") if isinstance(manifest, dict) and isinstance(manifest.get("venv_seed"), dict) else {}
    return {
        "state": "prepared",
        "phase": "prepare",
        "message": f"prepared target slot {target_slot} for restart",
        "target_slot": target_slot,
        "manifest": manifest,
        "started_at": started_at,
        "finished_at": finished_at,
        "prepare_elapsed_s": round(finished_at - started_at, 3),
        "install_elapsed_s": install.get("elapsed_s"),
        "install_installer": str(install.get("installer") or "").strip() or None,
        "venv_seed_source": str(seed.get("source") or "").strip() or None,
        "venv_seeded": bool(seed.get("seeded")),
        "venv_seed_copy_method": str(seed.get("copy_method") or "").strip() or None,
        "venv_seed_copy_elapsed_s": seed.get("copy_elapsed_s"),
        "venv_seed_elapsed_s": seed.get("elapsed_s"),
        "venv_seed_repair_elapsed_s": (
            seed.get("repair", {}).get("elapsed_s") if isinstance(seed.get("repair"), dict) else None
        ),
        "venv_repair_files_total": (
            seed.get("repair", {}).get("repaired_files_total") if isinstance(seed.get("repair"), dict) else None
        ),
        "plan": slot_plan,
    }


def execute_pending_update(plan: dict[str, Any]) -> dict[str, Any]:
    action = str(plan.get("action") or "update").strip().lower()
    if action == "rollback":
        restored = rollback_to_previous_slot()
        skill_runtime_rollback = rollback_installed_skill_runtimes() if restored else {}
        if restored:
            payload = {
                "state": "rolled_back",
                "phase": "rollback",
                "message": f"rolled back to slot {restored}",
                "restored_slot": restored,
                "finished_at": time.time(),
                "plan": plan,
            }
            if skill_runtime_rollback:
                payload["skill_runtime_rollback"] = skill_runtime_rollback
                if not bool(skill_runtime_rollback.get("ok")):
                    payload["message"] += " | some skill runtime rollbacks failed"
            return write_status(payload)
        return write_status(
            {
                "state": "failed",
                "phase": "rollback",
                "message": "no previous slot available for rollback",
                "finished_at": time.time(),
                "plan": plan,
            }
        )

    slot_plan = _plan_with_slot_context(plan)
    command = configured_update_command(slot_plan)
    started_at = time.time()
    if not command:
        return write_status(
            {
                "state": "failed",
                "phase": "apply",
                "message": "ADAOS_CORE_UPDATE_CMD is not configured",
                "started_at": started_at,
                "finished_at": time.time(),
                "plan": slot_plan,
            }
        )

    write_status(
        {
            "state": "applying",
            "phase": "apply",
            "message": "running core update command",
            "command": command,
            "started_at": started_at,
            "plan": slot_plan,
        }
    )
    completed = _run_command_with_bounded_output(command)
    target_slot = str(slot_plan.get("target_slot") or "")
    manifest = read_slot_manifest(target_slot) if target_slot else None
    manifest_ready = isinstance(manifest, dict) and (
        isinstance(manifest.get("argv"), list) or str(manifest.get("command") or "").strip()
    )
    ok = completed.returncode == 0 and manifest_ready
    if ok and target_slot:
        activate_slot(target_slot)
    payload = {
        "state": "succeeded" if ok else "failed",
        "phase": "apply",
        "message": (
            f"core update command completed; activated slot {target_slot}"
            if ok
            else (
                "core update command completed but slot manifest is missing or incomplete"
                if completed.returncode == 0
                else "core update command failed"
            )
        ),
        "command": command,
        "started_at": started_at,
        "finished_at": time.time(),
        "returncode": int(completed.returncode),
        "stdout": (completed.stdout or "")[-8000:],
        "stderr": (completed.stderr or "")[-8000:],
        "target_slot": target_slot,
        "manifest": manifest,
        "plan": slot_plan,
    }
    if not ok and action == "update" and target_slot:
        payload["slot_cleanup"] = remove_inactive_slot(
            target_slot,
            reason="core_update.apply_failed",
        )
    return write_status(payload)
