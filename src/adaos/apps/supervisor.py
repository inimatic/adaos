from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import copy
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency in some environments
    psutil = None  # type: ignore

import requests
import uvicorn
from fastapi import HTTPException

from adaos.apps.bootstrap import init_ctx
from adaos.apps.supervisor_runtime import (
    AdoptedProcess,
    MemoryProfilingOperations,
    MemoryProfilingService,
    ProcessSupervisor,
    ProcessSupervisorOperations,
    RuntimeRecoveryFacts,
    RuntimeRecoveryOperations,
    RuntimeRecoveryPolicy,
    SupervisorApiAdapter,
    SupervisorMonitoringOperations,
    SupervisorMonitoringService,
    SupervisorRuntimeConfig,
    SupervisorStatusOperations,
    SupervisorStatusService,
    SupervisorUpdateExecution,
    SupervisorUpdateExecutionOperations,
    UpdateReconciliationOperations,
    UpdateReconciliationService,
    UpdateAttemptStore,
    UpdateStateMachine,
    WatchdogStatusCompactor,
    create_supervisor_app,
    create_supervisor_routes,
)
from adaos.apps.cli.commands.api import _uvicorn_loop_mode
from adaos.services.agent_context import get_ctx
from adaos.services.bootstrap_update import SIDECAR_CONTROLLED_PATHS
from adaos.services.bounded_io import bounded_jsonl_tail, bounded_text_tail_lines, path_size_snapshot
from adaos.services.core_slots import (
    activate_slot,
    active_slot,
    active_slot_manifest,
    choose_inactive_slot,
    read_slot_manifest,
    remove_inactive_slot,
    rollback_to_previous_slot,
    slot_dir,
    slot_status as core_slot_status,
    validate_slot_structure,
)
from adaos.services.core_update import clear_plan as clear_core_update_plan
from adaos.services.core_update import finalize_runtime_boot_status
from adaos.services.core_update import prepare_pending_update
from adaos.services.core_update import read_last_result as read_core_update_last_result
from adaos.services.core_update import read_plan as read_core_update_plan
from adaos.services.core_update import read_status as read_core_update_status
from adaos.services.core_update import resolved_root_promotion_requirement
from adaos.services.core_update import rollback_installed_skill_runtimes
from adaos.services.core_update import write_plan as write_core_update_plan
from adaos.services.core_update import write_status as write_core_update_status
from adaos.services.core_update_policy import (
    SKIP_PENDING_CORE_UPDATE_ENV,
    core_update_reactions_disabled_reason,
)
from adaos.services.node_config import load_config
from adaos.services.realtime_sidecar import (
    probe_realtime_sidecar_ready,
    realtime_sidecar_diag_path,
    realtime_sidecar_enabled,
    realtime_sidecar_listener_snapshot,
    realtime_sidecar_log_path,
    realtime_sidecar_local_url,
    restart_realtime_sidecar_subprocess,
    start_realtime_sidecar_subprocess,
    stop_realtime_sidecar_subprocess,
)
from adaos.services.root.memory_profile_sync import (
    memory_profile_artifact_published_ref,
    memory_profile_artifact_source_api_path,
    report_hub_memory_profile,
)
from adaos.services.env_policy import env_int, env_text
from adaos.services.runtime_paths import current_base_dir, current_repo_root
from adaos.services.runtime_topology import (
    DEFAULT_LOOPBACK_HOST,
    DEFAULT_RUNTIME_PORT,
    DEFAULT_SUPERVISOR_PORT,
    supervisor_base_from_env,
)
from adaos.services.zone_hosts import DEFAULT_PUBLIC_ROOT_BASE_URL
from adaos.services.supervisor_memory import (
    DEFAULT_PROFILER_ADAPTER,
    IMPLEMENTED_PROFILE_CONTROL_ACTIONS,
    IMPLEMENTED_PROFILE_CONTROL_MODE,
    MEMORY_OPERATION_CONTRACT_VERSION,
    PROFILE_LAUNCH_ENV_KEYS,
    TOP_LEVEL_OPERATION_EVENTS,
    append_memory_telemetry_sample,
    append_memory_session_operation,
    ensure_memory_store,
    read_memory_telemetry_tail,
    read_memory_runtime_state,
    read_memory_session_operations,
    read_memory_session_index,
    read_memory_session_summary,
    supervisor_memory_evidence_dir,
    supervisor_memory_runtime_state_path,
    supervisor_memory_session_artifacts_dir,
    supervisor_memory_session_operations_path,
    supervisor_memory_sessions_index_path,
    supervisor_memory_telemetry_path,
    write_memory_session_index,
    write_memory_session_summary,
    write_memory_runtime_state,
)


_LOG = logging.getLogger("adaos.supervisor")
_SUPERVISOR_INSTANCE_ID = uuid.uuid4().hex
_SUPERVISOR_INSTANCE_STARTED_AT = time.time()
_PROCESS_SUPERVISOR = ProcessSupervisor(psutil)
_UPDATE_STATE_MACHINE = UpdateStateMachine()
_UPDATE_ATTEMPTS = UpdateAttemptStore()
_UPDATE_RECONCILIATION = UpdateReconciliationService()
_UPDATE_TRANSITION_LOCKS: dict[str, threading.Lock] = {}
_UPDATE_TRANSITION_LOCKS_GUARD = threading.Lock()
_MEMORY_PROFILING = MemoryProfilingService()
_RUNTIME_CONFIG = SupervisorRuntimeConfig()
_WATCHDOG_STATUS = WatchdogStatusCompactor()


def _update_reconciliation_operations() -> UpdateReconciliationOperations:
    return UpdateReconciliationOperations(
        active_slot_target_mismatch_status=_active_slot_target_mismatch_status,
        attempt_transition_at=_attempt_transition_at,
        clear_core_update_plan=clear_core_update_plan,
        clear_orphaned_subsequent_transition_status=_clear_orphaned_subsequent_transition_status,
        compact_public_runtime_self_heal=_compact_public_runtime_self_heal,
        compact_watchdog_required_link=_compact_watchdog_required_link,
        complete_update_attempt=_complete_update_attempt,
        fail_root_restart_attempt=_fail_root_restart_attempt,
        finalize_runtime_boot_status_from_supervisor=_finalize_runtime_boot_status_from_supervisor,
        is_root_restart_completed_status=_is_root_restart_completed_status,
        is_root_restart_pending_attempt=_is_root_restart_pending_attempt,
        is_root_promotion_pending_status=_is_root_promotion_pending_status,
        is_terminal_update_status=_is_terminal_update_status,
        read_core_update_status=read_core_update_status,
        read_update_attempt=_read_update_attempt,
        reconcile_completed_attempt_after_runtime_failure=(
            _reconcile_completed_attempt_after_runtime_failure
        ),
        reconcile_failed_attempt_after_terminal_success=_reconcile_failed_attempt_after_terminal_success,
        reconcile_failed_root_restart_after_runtime_recovery=(
            _reconcile_failed_root_restart_after_runtime_recovery
        ),
        reconcile_failed_target_mismatch_after_active_switch=(
            _reconcile_failed_target_mismatch_after_active_switch
        ),
        recover_active_attempt_target_already_active=_recover_active_attempt_target_already_active,
        revoke_prepare_lease=_revoke_prepare_lease,
        rollback_installed_skill_runtimes=rollback_installed_skill_runtimes,
        rollback_to_previous_slot=rollback_to_previous_slot,
        runtime_ready_for_boot_status_finalize=_runtime_ready_for_boot_status_finalize,
        status_updated_at=_status_updated_at,
        terminal_status_belongs_to_attempt=_terminal_status_belongs_to_attempt,
        transition_snapshot_current=_update_transition_snapshot_current,
        update_attempt_contract_version=UPDATE_ATTEMPT_CONTRACT_VERSION,
        update_status_timeout_sec=_update_status_timeout_sec,
        update_transition_timed_out=_update_transition_timed_out,
        write_core_update_status=write_core_update_status,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AdaOS supervisor")
    parser.add_argument("--host", default=DEFAULT_LOOPBACK_HOST, help="Managed runtime host")
    parser.add_argument("--port", type=int, default=DEFAULT_RUNTIME_PORT, help="Managed runtime port")
    parser.add_argument("--token", default=None)
    return parser.parse_known_args()[0]


def _resolved_token(raw_token: str | None = None) -> str | None:
    token = str(raw_token or os.getenv("ADAOS_TOKEN") or "").strip()
    if token:
        return token
    try:
        return str(get_ctx().config.token or "").strip() or None
    except Exception:
        return None


def _supervisor_host() -> str:
    return env_text("ADAOS_SUPERVISOR_HOST", DEFAULT_LOOPBACK_HOST).strip() or DEFAULT_LOOPBACK_HOST


def _supervisor_port() -> int:
    return env_int("ADAOS_SUPERVISOR_PORT", DEFAULT_SUPERVISOR_PORT)


def _supervisor_base_url() -> str:
    return supervisor_base_from_env()


def _supervisor_state_dir() -> Path:
    path = (current_base_dir() / "state" / "supervisor").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _supervisor_runtime_state_path() -> Path:
    return (_supervisor_state_dir() / "runtime.json").resolve()


def _supervisor_hub_root_watchdog_log_path() -> Path:
    return (_supervisor_state_dir() / "hub_root_watchdog.jsonl").resolve()


def _supervisor_member_hub_watchdog_log_path() -> Path:
    return (_supervisor_state_dir() / "member_hub_watchdog.jsonl").resolve()


def _supervisor_update_attempt_path() -> Path:
    return (_supervisor_state_dir() / "update_attempt.json").resolve()


def _update_transition_lock_path() -> Path:
    return (_supervisor_state_dir() / "update_transition.lock").resolve()


def _active_skill_runtime_migration() -> dict[str, Any] | None:
    status_path = (current_base_dir() / "state" / "skill_runtime_migration" / "status.json").resolve()
    lease_path = status_path.with_name("worker.lock")
    status: dict[str, Any] = {}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            status = payload
    except Exception:
        pass
    handle = None
    locked_here = False
    try:
        lease_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lease_path.open("a+b")
        if lease_path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                locked_here = True
            except OSError:
                locked_here = False
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_here = True
            except OSError:
                locked_here = False
    except Exception:
        return status if bool(status.get("pending")) else None
    finally:
        if handle is not None:
            if locked_here:
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
            handle.close()
    if locked_here:
        return None
    return status or {"pending": True, "state": "running", "phase": "unknown"}


def _update_transition_thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _UPDATE_TRANSITION_LOCKS_GUARD:
        lock = _UPDATE_TRANSITION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _UPDATE_TRANSITION_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _try_update_transition_guard(*, operation: str):
    """Serialize destructive update transitions without blocking status reads."""
    path = _update_transition_lock_path()
    thread_lock = _update_transition_thread_lock(path)
    if not thread_lock.acquire(blocking=False):
        yield False
        return
    handle = None
    locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                locked = False
        else:
            try:
                import fcntl
            except ImportError:  # pragma: no cover - supported Unix targets provide fcntl
                _LOG.warning(
                    "cross-process update transition locking unavailable; "
                    "using process-local guard operation=%s path=%s",
                    operation,
                    path,
                )
                locked = True
            else:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError:
                    locked = False
        if not locked:
            yield False
            return
        _LOG.debug("acquired update transition guard operation=%s path=%s", operation, path)
        yield True
    finally:
        if handle is not None:
            if locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass
            handle.close()
        thread_lock.release()


def _supervisor_prepare_leases_dir() -> Path:
    path = (_supervisor_state_dir() / "prepare_leases").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prepare_lease_path(token: str) -> Path:
    safe_token = "".join(ch for ch in str(token or "").strip() if ch.isalnum() or ch in {"-", "_"})
    if not safe_token:
        safe_token = uuid.uuid4().hex
    return (_supervisor_prepare_leases_dir() / f"{safe_token}.json").resolve()


def _write_prepare_lease(path: Path, *, token: str, state: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "token": str(token or "").strip(),
        "state": str(state or "").strip().lower() or "unknown",
        "reason": str(reason or "").strip() or None,
        "updated_at": time.time(),
    }
    payload.update(extra)
    _write_json(path, payload)
    return payload


def _memory_profiler_adapter() -> str:
    return _MEMORY_PROFILING.profiler_adapter(DEFAULT_PROFILER_ADAPTER)


def _memory_telemetry_interval_sec() -> float:
    return _MEMORY_PROFILING.telemetry_interval_sec()


def _memory_telemetry_window_sec() -> float:
    return _MEMORY_PROFILING.telemetry_window_sec()


def _memory_baseline_warmup_sec() -> float:
    return _MEMORY_PROFILING.baseline_warmup_sec()


def _memory_baseline_maturity_slope_bytes_per_min() -> float:
    return _MEMORY_PROFILING.baseline_maturity_slope_bytes_per_min()


def _memory_suspicion_growth_threshold_bytes() -> int:
    return _MEMORY_PROFILING.suspicion_growth_threshold_bytes(psutil_module=psutil)


def _memory_suspicion_family_rss_threshold_bytes() -> int | None:
    return _MEMORY_PROFILING.suspicion_family_rss_threshold_bytes()


def _memory_suspicion_slope_threshold_bytes_per_min() -> float:
    return _MEMORY_PROFILING.suspicion_slope_threshold_bytes_per_min()


def _memory_auto_profile_cooldown_sec() -> float:
    return _MEMORY_PROFILING.auto_profile_cooldown_sec()


def _memory_policy_profile_restarts_enabled() -> bool:
    return _MEMORY_PROFILING.policy_profile_restarts_enabled()


def _memory_auto_profile_min_uptime_sec() -> float:
    return _MEMORY_PROFILING.auto_profile_min_uptime_sec()


def _memory_auto_profile_browser_live_ttl_sec() -> float:
    return _MEMORY_PROFILING.auto_profile_browser_live_ttl_sec()


def _memory_auto_profile_allow_browser_sessions() -> bool:
    return _MEMORY_PROFILING.auto_profile_allow_browser_sessions()


def _memory_auto_profile_circuit_window_sec() -> float:
    return _MEMORY_PROFILING.auto_profile_circuit_window_sec()


def _memory_auto_profile_circuit_limit() -> int:
    return _MEMORY_PROFILING.auto_profile_circuit_limit()


def _available_memory_bytes() -> int | None:
    return _MEMORY_PROFILING.available_memory_bytes(psutil_module=psutil)


def _total_memory_bytes() -> int | None:
    return _MEMORY_PROFILING.total_memory_bytes(psutil_module=psutil)


def _memory_critical_available_percent_threshold() -> float:
    return _MEMORY_PROFILING.critical_available_percent_threshold()


def _memory_critical_available_bytes_threshold() -> int:
    return _MEMORY_PROFILING.critical_available_bytes_threshold()


def _memory_critical_duration_sec() -> float:
    return _MEMORY_PROFILING.critical_duration_sec()


def _memory_critical_restart_cooldown_sec() -> float:
    return _MEMORY_PROFILING.critical_restart_cooldown_sec()


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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _compact_watchdog_required_link(value: Any) -> dict[str, Any]:
    return _WATCHDOG_STATUS.compact_required_link(value)


def _compact_watchdog_last_result(value: Any) -> dict[str, Any]:
    return _WATCHDOG_STATUS.compact_last_result(value)


def _compact_watchdog_event(value: Any) -> dict[str, Any]:
    return _WATCHDOG_STATUS.compact_event(value)


def _read_jsonl_tail(path: Path, *, limit: int = 20, max_bytes: int = 256 * 1024) -> list[dict[str, Any]]:
    return _WATCHDOG_STATUS.read_tail(path, limit=limit, max_bytes=max_bytes)


def _local_update_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "status": read_core_update_status(),
        "last_result": read_core_update_last_result(),
        "plan": read_core_update_plan(),
        "slots": core_slot_status(),
        "active_manifest": active_slot_manifest(),
        "_local_fallback": True,
    }


def _promote_root_with_validated_candidate(
    *,
    slot: str,
    manifest: dict[str, Any],
    runtime_host: str,
    runtime_port: int,
) -> dict[str, Any]:
    slot_name = str(slot or "").strip().upper()
    if slot_name not in {"A", "B"}:
        raise RuntimeError("validated candidate slot is unavailable for root promotion")
    expected_slot_dir = slot_dir(slot_name).resolve()
    repo_dir = Path(os.path.abspath(os.path.expanduser(str(manifest.get("repo_dir") or ""))))
    argv = manifest.get("argv") if isinstance(manifest.get("argv"), list) else []
    candidate_python = Path(os.path.abspath(os.path.expanduser(str(argv[0] if argv else ""))))
    expected_repo_dir = expected_slot_dir / "repo"
    expected_venv_dir = expected_slot_dir / "venv"
    if repo_dir != expected_repo_dir or expected_venv_dir not in candidate_python.parents:
        raise RuntimeError("root promotion candidate paths do not belong to the validated slot")
    if not (repo_dir / "src" / "adaos" / "apps" / "core_update_root_promote.py").is_file():
        raise RuntimeError("validated candidate does not provide the root promotion runner")
    if not candidate_python.is_file():
        raise RuntimeError(f"validated candidate Python is unavailable: {candidate_python}")
    root_dir_raw = str(manifest.get("root_repo_root") or "").strip()
    root_dir = Path(root_dir_raw).expanduser().resolve() if root_dir_raw else current_repo_root()
    if root_dir is None:
        raise RuntimeError("stable root checkout is unavailable for candidate-owned promotion")
    env = dict(os.environ)
    env["ADAOS_BASE_DIR"] = str(current_base_dir())
    env["ADAOS_ROOT_REPO_ROOT"] = str(root_dir)
    env["PYTHONPATH"] = str((repo_dir / "src").resolve())
    completed = subprocess.run(
        [
            str(candidate_python),
            "-m",
            "adaos.apps.core_update_root_promote",
            "--slot",
            slot_name,
            "--base-dir",
            str(current_base_dir()),
            "--root-repo-root",
            str(root_dir),
            "--runtime-host",
            str(runtime_host),
            "--runtime-port",
            str(int(runtime_port)),
        ],
        cwd=str(repo_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=180.0,
    )
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "candidate promotion failed").strip()[-4000:]
        raise RuntimeError(f"candidate-owned root promotion failed: {detail}")
    payload: dict[str, Any] | None = None
    for line in reversed(str(completed.stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
        except Exception:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if not isinstance(payload, dict) or not bool(payload.get("ok")):
        raise RuntimeError("candidate-owned root promotion returned no successful result")
    if str(payload.get("slot") or "").strip().upper() != slot_name:
        raise RuntimeError("candidate-owned root promotion returned the wrong slot")
    payload["execution_owner"] = "validated_candidate"
    payload["candidate_python"] = str(candidate_python)
    payload["candidate_repo_dir"] = str(repo_dir)
    return payload


def _update_attempt_timeout_sec() -> float:
    return _RUNTIME_CONFIG.update_attempt_timeout_sec()


def _update_prepare_timeout_sec() -> float:
    return _RUNTIME_CONFIG.update_prepare_timeout_sec()


def _update_status_timeout_sec(status: dict[str, Any] | None) -> float:
    payload = status if isinstance(status, dict) else {}
    state = str(payload.get("state") or "").strip().lower()
    phase = str(payload.get("phase") or "").strip().lower()
    if state == "preparing" or phase == "prepare":
        return _update_prepare_timeout_sec()
    return _update_attempt_timeout_sec()


def _min_update_period_sec() -> float:
    return _RUNTIME_CONFIG.min_update_period_sec()


def _live_media_guard_defer_sec() -> float:
    return _RUNTIME_CONFIG.live_media_guard_defer_sec()


def _auto_update_complete_enabled() -> bool:
    return _RUNTIME_CONFIG.auto_update_complete_enabled()


def _root_restart_delay_sec() -> float:
    return _RUNTIME_CONFIG.root_restart_delay_sec()


def _autostart_self_restart_supported() -> bool:
    return _RUNTIME_CONFIG.autostart_self_restart_supported()


def _slot_runtime_ports(primary_port: int) -> dict[str, int]:
    return _RUNTIME_CONFIG.slot_runtime_ports(primary_port)


def _slot_runtime_port(slot: str | None, primary_port: int) -> int:
    slot_name = str(slot or "").strip().upper()
    return int(_slot_runtime_ports(primary_port).get(slot_name, int(primary_port)))


def _warm_switch_enabled() -> bool:
    return _RUNTIME_CONFIG.warm_switch_enabled()


def _warm_switch_min_available_bytes() -> int:
    return _RUNTIME_CONFIG.warm_switch_min_available_bytes()


def _warm_switch_min_candidate_bytes() -> int:
    return _RUNTIME_CONFIG.warm_switch_min_candidate_bytes()


def _warm_switch_max_candidate_rss_bytes() -> int:
    return _RUNTIME_CONFIG.warm_switch_max_candidate_rss_bytes(
        total_memory_bytes=_total_memory_bytes()
    )


def _warm_switch_rss_multiplier() -> float:
    return _RUNTIME_CONFIG.warm_switch_rss_multiplier()


def _warm_switch_candidate_ready_timeout_sec() -> float:
    return _RUNTIME_CONFIG.warm_switch_candidate_ready_timeout_sec()


def _warm_switch_strict_cutover_enabled() -> bool:
    return _RUNTIME_CONFIG.warm_switch_strict_cutover_enabled()


def _warm_switch_cold_fallback_enabled() -> bool:
    return _RUNTIME_CONFIG.warm_switch_cold_fallback_enabled()


def _warm_switch_defer_sec() -> float:
    return _RUNTIME_CONFIG.warm_switch_defer_sec()


def _cutover_recovery_stable_sec() -> float:
    return _RUNTIME_CONFIG.cutover_recovery_stable_sec()


def _warm_switch_max_deferrals() -> int:
    return _RUNTIME_CONFIG.warm_switch_max_deferrals()


def _sidecar_code_change_debounce_sec() -> float:
    return _RUNTIME_CONFIG.sidecar_code_change_debounce_sec()


def _sidecar_recovery_settle_timeout_sec() -> float:
    return _RUNTIME_CONFIG.sidecar_recovery_settle_timeout_sec()


def _sidecar_restart_window_sec() -> float:
    return _RUNTIME_CONFIG.sidecar_restart_window_sec()


def _sidecar_restart_limit() -> int:
    return _RUNTIME_CONFIG.sidecar_restart_limit()


def _sidecar_restart_base_backoff_sec() -> float:
    return _RUNTIME_CONFIG.sidecar_restart_base_backoff_sec()


def _sidecar_restart_max_backoff_sec() -> float:
    return _RUNTIME_CONFIG.sidecar_restart_max_backoff_sec()


def _sidecar_restart_circuit_open_sec() -> float:
    return _RUNTIME_CONFIG.sidecar_restart_circuit_open_sec()


def _hub_root_watchdog_enabled() -> bool:
    return _RUNTIME_CONFIG.hub_root_watchdog_enabled()


def _required_upstream_watchdog_poll_interval_sec() -> float:
    return _RUNTIME_CONFIG.required_upstream_watchdog_poll_interval_sec()


def _runtime_reliability_probe_timeout_sec() -> float:
    return _RUNTIME_CONFIG.runtime_reliability_probe_timeout_sec()


def _hub_root_watchdog_cooldown_sec() -> float:
    return _RUNTIME_CONFIG.hub_root_watchdog_cooldown_sec()


def _hub_root_watchdog_reset_degraded_route_enabled() -> bool:
    return _RUNTIME_CONFIG.hub_root_watchdog_reset_degraded_route_enabled()


def _hub_root_watchdog_verify_timeout_sec() -> float:
    return _RUNTIME_CONFIG.hub_root_watchdog_verify_timeout_sec()


def _hub_root_watchdog_verify_interval_sec() -> float:
    return _RUNTIME_CONFIG.hub_root_watchdog_verify_interval_sec()


def _hub_root_root_probe_enabled() -> bool:
    return _RUNTIME_CONFIG.hub_root_root_probe_enabled()


def _hub_root_root_probe_interval_sec() -> float:
    return _RUNTIME_CONFIG.hub_root_root_probe_interval_sec()


def _hub_root_root_probe_timeout_sec() -> float:
    return _RUNTIME_CONFIG.hub_root_root_probe_timeout_sec()


def _hub_root_root_probe_ttl_sec() -> float:
    return _RUNTIME_CONFIG.hub_root_root_probe_ttl_sec()


def _parse_root_probe_time(value: Any) -> float | None:
    return _RUNTIME_CONFIG.parse_root_probe_time(value)


def _member_hub_watchdog_enabled() -> bool:
    return _RUNTIME_CONFIG.member_hub_watchdog_enabled()


def _member_hub_watchdog_cooldown_sec() -> float:
    return _RUNTIME_CONFIG.member_hub_watchdog_cooldown_sec()


def _member_hub_watchdog_verify_timeout_sec() -> float:
    return _RUNTIME_CONFIG.member_hub_watchdog_verify_timeout_sec()


def _member_hub_watchdog_verify_interval_sec() -> float:
    return _RUNTIME_CONFIG.member_hub_watchdog_verify_interval_sec()


def _post_recovery_core_update_reconcile_enabled() -> bool:
    return _RUNTIME_CONFIG.post_recovery_core_update_reconcile_enabled()


def _post_recovery_core_update_reconcile_cooldown_sec() -> float:
    return _RUNTIME_CONFIG.post_recovery_core_update_reconcile_cooldown_sec()


def _post_recovery_core_update_reconcile_countdown_sec() -> float:
    return _RUNTIME_CONFIG.post_recovery_core_update_reconcile_countdown_sec()


def _periodic_core_update_reconcile_enabled() -> bool:
    return _RUNTIME_CONFIG.periodic_core_update_reconcile_enabled()


def _periodic_core_update_reconcile_interval_sec() -> float:
    return _RUNTIME_CONFIG.periodic_core_update_reconcile_interval_sec()


def _post_recovery_member_hub_refresh_enabled() -> bool:
    return _RUNTIME_CONFIG.post_recovery_member_hub_refresh_enabled()


def _post_recovery_member_hub_refresh_cooldown_sec() -> float:
    return _RUNTIME_CONFIG.post_recovery_member_hub_refresh_cooldown_sec()


UPDATE_ATTEMPT_CONTRACT_VERSION = UpdateAttemptStore.CONTRACT_VERSION


def _new_runtime_instance_id(*, slot: str | None, transition_role: str) -> str:
    slot_token = str(slot or "x").strip().lower() or "x"
    role_token = str(transition_role or "active").strip().lower() or "active"
    return f"rt-{slot_token}-{role_token[:1]}-{uuid.uuid4().hex[:8]}"


def _read_update_attempt() -> dict[str, Any] | None:
    return _UPDATE_ATTEMPTS.read(
        _supervisor_update_attempt_path(),
        read_json=_read_json,
    )


def _write_update_attempt(payload: dict[str, Any]) -> dict[str, Any]:
    return _UPDATE_ATTEMPTS.write(
        _supervisor_update_attempt_path(),
        payload,
        write_json=_write_json,
    )


def _write_update_attempt_preserving_subsequent_transition(
    payload: dict[str, Any],
) -> dict[str, Any]:
    attempt_payload = dict(payload)
    current = _read_update_attempt()
    queued = _subsequent_transition_request(current)
    if queued is not None and _subsequent_transition_request(attempt_payload) is None:
        attempt_payload["subsequent_transition"] = True
        attempt_payload["subsequent_transition_requested_at"] = (
            (current or {}).get("subsequent_transition_requested_at")
            or queued.get("requested_at")
        )
        attempt_payload["subsequent_transition_request"] = queued
    return _write_update_attempt(attempt_payload)


def _replace_update_attempt(payload: dict[str, Any]) -> dict[str, Any]:
    return _write_update_attempt(payload)


def _observed_update_attempt(
    attempt: dict[str, Any] | None,
    status: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(attempt, dict):
        return None
    payload = dict(attempt)
    current = status if isinstance(status, dict) else {}
    last = attempt.get("last_status") if isinstance(attempt.get("last_status"), dict) else {}
    snapshot_keys = (
        "state",
        "phase",
        "action",
        "target_rev",
        "target_version",
        "target_slot",
    )
    observed = {key: current.get(key) for key in snapshot_keys}
    observed["updated_at"] = current.get("updated_at")
    payload["observed_status"] = observed
    payload["last_status_matches_current"] = all(
        str(last.get(key) or "").strip() == str(current.get(key) or "").strip()
        for key in snapshot_keys
    )
    payload["last_status_updated_at"] = last.get("updated_at")
    return payload


def _epoch(value: Any) -> float:
    return _UPDATE_ATTEMPTS.epoch(value)


def _status_updated_at(payload: dict[str, Any]) -> float:
    return _UPDATE_ATTEMPTS.status_updated_at(payload)


def _attempt_transition_at(payload: dict[str, Any]) -> float:
    return _UPDATE_ATTEMPTS.transition_at(payload)


def _update_transition_snapshot_current(
    *,
    status: dict[str, Any],
    attempt: dict[str, Any],
) -> bool:
    current_status = read_core_update_status()
    current_attempt = _read_update_attempt() or {}
    status_keys = ("state", "phase", "action", "target_version", "updated_at")
    attempt_keys = ("state", "action", "target_version", "updated_at", "transitioned_at")
    return all(current_status.get(key) == status.get(key) for key in status_keys) and all(
        current_attempt.get(key) == attempt.get(key) for key in attempt_keys
    )


def _update_transition_timed_out(*, status_age: float, transition_age: float, timeout_sec: float) -> bool:
    return _UPDATE_STATE_MACHINE.transition_timed_out(
        status_age=status_age,
        transition_age=transition_age,
        timeout_sec=timeout_sec,
    )


def _prepare_lease_ref_from_payloads(*payloads: dict[str, Any] | None) -> tuple[str, str]:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        path = str(payload.get("prepare_lease_path") or "").strip()
        token = str(payload.get("prepare_lease_token") or "").strip()
        if path or token:
            return path, token
        previous = payload.get("supervisor_previous_status")
        if isinstance(previous, dict):
            path = str(previous.get("prepare_lease_path") or "").strip()
            token = str(previous.get("prepare_lease_token") or "").strip()
            if path or token:
                return path, token
        last_status = payload.get("last_status")
        if isinstance(last_status, dict):
            path = str(last_status.get("prepare_lease_path") or "").strip()
            token = str(last_status.get("prepare_lease_token") or "").strip()
            if path or token:
                return path, token
    return "", ""


def _revoke_prepare_lease(
    *,
    status: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any] | None:
    path_raw, token = _prepare_lease_ref_from_payloads(status, attempt)
    if not path_raw:
        return None
    path = Path(path_raw).expanduser().resolve()
    payload = {
        "token": token,
        "state": "revoked",
        "reason": str(reason or "revoked").strip() or "revoked",
        "revoked_reason": str(reason or "revoked").strip() or "revoked",
        "revoked_at": time.time(),
    }
    try:
        existing = _read_json(path) or {}
        if token and str(existing.get("token") or "").strip() not in {"", token}:
            payload["token_mismatch"] = True
            payload["previous_token"] = str(existing.get("token") or "").strip()
        _write_json(path, {**existing, **payload, "updated_at": time.time()})
        return {"ok": True, "path": str(path), "token_present": bool(token)}
    except Exception as exc:
        return {"ok": False, "path": str(path), "error_type": type(exc).__name__, "error": str(exc)}


def _is_terminal_update_status(payload: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.is_terminal(payload)


def _is_root_restart_pending_attempt(payload: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.is_root_restart_pending_attempt(payload)


def _is_root_restart_completed_status(payload: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.is_root_restart_completed_status(payload)


def _is_root_promotion_pending_status(payload: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.is_root_promotion_pending_status(payload)


def _is_root_restart_pending_status(payload: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.is_root_restart_pending_status(payload)


def _root_restart_crossed_supervisor_generation(payload: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.crossed_supervisor_generation(
        payload,
        current_instance_id=_SUPERVISOR_INSTANCE_ID,
    )


def _is_transition_in_progress(status: dict[str, Any] | None, attempt: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.transition_in_progress(status, attempt)


def _runtime_ready_for_boot_status_finalize(status: dict[str, Any] | None, runtime: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.runtime_ready_for_boot_finalize(
        status,
        runtime,
        current_instance_id=_SUPERVISOR_INSTANCE_ID,
    )


def _transition_request_payload(
    *,
    action: str,
    target_rev: str,
    target_version: str,
    reason: str,
    countdown_sec: float,
    drain_timeout_sec: float,
    signal_delay_sec: float,
    requested_at: float | None = None,
) -> dict[str, Any]:
    return {
        "action": str(action or "update"),
        "target_rev": str(target_rev or ""),
        "target_version": str(target_version or ""),
        "reason": str(reason or ""),
        "countdown_sec": float(countdown_sec),
        "drain_timeout_sec": float(drain_timeout_sec),
        "signal_delay_sec": float(signal_delay_sec),
        "requested_at": float(requested_at or time.time()),
    }


def _request_from_attempt(attempt: dict[str, Any] | None) -> dict[str, Any]:
    data = attempt if isinstance(attempt, dict) else {}
    payload = _transition_request_payload(
        action=str(data.get("action") or "update"),
        target_rev=str(data.get("target_rev") or ""),
        target_version=str(data.get("target_version") or ""),
        reason=str(data.get("reason") or ""),
        countdown_sec=float(data.get("countdown_sec") or 0.0),
        drain_timeout_sec=float(data.get("drain_timeout_sec") or 10.0),
        signal_delay_sec=float(data.get("signal_delay_sec") or 0.25),
        requested_at=_epoch(data.get("requested_at")) or time.time(),
    )
    try:
        candidate_prewarm_deferral_count = max(
            0,
            int(data.get("candidate_prewarm_deferral_count") or 0),
        )
    except Exception:
        candidate_prewarm_deferral_count = 0
    if candidate_prewarm_deferral_count:
        payload["candidate_prewarm_deferral_count"] = candidate_prewarm_deferral_count
    return payload


def _subsequent_transition_request(attempt: dict[str, Any] | None) -> dict[str, Any] | None:
    data = attempt if isinstance(attempt, dict) else {}
    queued = data.get("subsequent_transition_request")
    return dict(queued) if isinstance(queued, dict) and queued else None


def _clear_orphaned_subsequent_transition_status(
    status: dict[str, Any],
    attempt: dict[str, Any] | None,
    *,
    updated_at: float | None = None,
) -> dict[str, Any]:
    status_payload = dict(status)
    if _subsequent_transition_request(attempt) is not None:
        return status_payload
    had_stale_marker = bool(status_payload.get("subsequent_transition")) or any(
        key in status_payload
        for key in (
            "subsequent_transition_action",
            "subsequent_transition_target_rev",
            "subsequent_transition_target_version",
        )
    )
    if not had_stale_marker:
        return status_payload
    status_payload["subsequent_transition"] = False
    status_payload["subsequent_transition_requested_at"] = None
    for key in (
        "subsequent_transition_action",
        "subsequent_transition_target_rev",
        "subsequent_transition_target_version",
    ):
        status_payload.pop(key, None)
    status_payload["updated_at"] = time.time() if updated_at is None else float(updated_at)
    return write_core_update_status(status_payload)


def _target_version_matches(left: Any, right: Any) -> bool:
    return _UPDATE_STATE_MACHINE.target_version_matches(left, right)


def _transition_request_has_resolved_target(request: dict[str, Any] | None) -> bool:
    return _UPDATE_STATE_MACHINE.transition_request_has_resolved_target(request)


def _manifest_matches_target_version(manifest: dict[str, Any] | None, target_version: Any) -> bool:
    return _UPDATE_STATE_MACHINE.manifest_matches_target_version(manifest, target_version)


def _terminal_status_belongs_to_attempt(status: dict[str, Any], attempt: dict[str, Any]) -> bool:
    attempt_action = str(attempt.get("action") or "").strip().lower()
    status_action = str(status.get("action") or "").strip().lower()
    if attempt_action and status_action and attempt_action != status_action:
        return False

    attempt_version = str(attempt.get("target_version") or "").strip()
    status_version = str(status.get("target_version") or "").strip()
    if attempt_version:
        if status_version:
            return _target_version_matches(attempt_version, status_version)
        status_at = _status_updated_at(status)
        transition_at = _attempt_transition_at(attempt)
        if not status_at or not transition_at or status_at < transition_at:
            return False
        try:
            manifest = active_slot_manifest()
        except Exception:
            manifest = None
        return _manifest_matches_target_version(manifest, attempt_version)

    attempt_rev = str(attempt.get("target_rev") or "").strip()
    status_rev = str(status.get("target_rev") or "").strip()
    if attempt_rev:
        return bool(status_rev and status_rev == attempt_rev)
    return True


def _transition_request_same_target(request: dict[str, Any] | None, other: dict[str, Any] | None) -> bool:
    req = request if isinstance(request, dict) else {}
    cur = other if isinstance(other, dict) else {}
    req_action = str(req.get("action") or "update").strip().lower()
    cur_action = str(cur.get("action") or "update").strip().lower()
    if req_action != cur_action:
        return False
    if _target_version_matches(req.get("target_version"), cur.get("target_version")):
        return True
    req_rev = str(req.get("target_rev") or "").strip()
    cur_rev = str(cur.get("target_rev") or "").strip()
    return bool(req_rev and cur_rev and req_rev == cur_rev and not req.get("target_version") and not cur.get("target_version"))


def _transition_request_matches_active_slot(request: dict[str, Any] | None) -> bool:
    req = request if isinstance(request, dict) else {}
    if str(req.get("action") or "update").strip().lower() != "update":
        return False
    requested_version = str(req.get("target_version") or "").strip()
    if not requested_version:
        return False
    try:
        manifest = active_slot_manifest()
    except Exception:
        manifest = None
    manifest = manifest if isinstance(manifest, dict) else {}
    for key in ("target_version", "build_version", "git_commit", "git_short_commit"):
        if _target_version_matches(requested_version, manifest.get(key)):
            return True
    return False


def _planned_transition_active(status: dict[str, Any] | None, attempt: dict[str, Any] | None) -> bool:
    status_map = status if isinstance(status, dict) else {}
    attempt_map = attempt if isinstance(attempt, dict) else {}
    return (
        str(attempt_map.get("state") or "").strip().lower() == "planned"
        or str(status_map.get("state") or "").strip().lower() == "planned"
    )


def _same_target_status_completion_time(status: dict[str, Any]) -> float:
    return max(
        _epoch(status.get("root_restart_completed_at")),
        _epoch(status.get("finished_at")),
        _epoch(status.get("validated_at")),
    )


def _clear_stale_deduplicated_status_fields(
    status: dict[str, Any],
    *,
    keep_completion_timestamps: bool,
) -> None:
    for key in (
        "error",
        "error_type",
        "plan",
        "supervisor_previous_status",
        "supervisor_timeout_sec",
        "supervisor_timeout_at",
        "active_slot_target_mismatch",
        "active_slot_target_mismatch_reason",
        "root_promotion_refused",
        "root_promotion_refused_reason",
    ):
        status.pop(key, None)
    if not keep_completion_timestamps:
        for key in (
            "started_at",
            "finished_at",
            "validated_at",
            "root_restart_completed_at",
        ):
            status.pop(key, None)


def _clear_same_target_subsequent_transition(
    *,
    status: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    queued: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    now = time.time()
    attempt_payload = dict(attempt or {})
    if attempt_payload:
        attempt_payload["subsequent_transition"] = False
        attempt_payload["subsequent_transition_requested_at"] = None
        attempt_payload.pop("subsequent_transition_request", None)
        attempt_payload["same_target_subsequent_deduped_at"] = now
        attempt_payload["same_target_subsequent_deduped_reason"] = reason
        attempt_payload["same_target_subsequent_target_version"] = str(queued.get("target_version") or "")
        attempt_payload["updated_at"] = now
        _replace_update_attempt(attempt_payload)

    status_payload = dict(status or read_core_update_status() or {})
    status_payload["subsequent_transition"] = False
    status_payload["subsequent_transition_requested_at"] = None
    status_payload["same_target_subsequent_deduped_at"] = now
    status_payload["same_target_subsequent_deduped_reason"] = reason
    status_payload["same_target_subsequent_target_version"] = str(queued.get("target_version") or "")
    for key in (
        "subsequent_transition_action",
        "subsequent_transition_target_rev",
        "subsequent_transition_target_version",
    ):
        status_payload.pop(key, None)
    status_payload["updated_at"] = now
    return write_core_update_status(status_payload)


def _last_update_completion_at(status: dict[str, Any] | None, attempt: dict[str, Any] | None) -> float:
    attempt_map = attempt if isinstance(attempt, dict) else {}
    if bool(attempt_map.get("same_target_deduped_at")):
        attempt_map = {}
    if str(attempt_map.get("action") or "").strip().lower() == "update":
        completed_at = _epoch(attempt_map.get("completed_at"))
        if completed_at > 0.0:
            return completed_at
        updated_at = _epoch(attempt_map.get("updated_at"))
        if updated_at > 0.0 and str(attempt_map.get("state") or "").strip().lower() in {"completed", "failed", "cancelled"}:
            return updated_at
    status_map = status if isinstance(status, dict) else {}
    if str(status_map.get("action") or "").strip().lower() != "update":
        return 0.0
    if not _is_terminal_update_status(status_map):
        return 0.0
    if bool(status_map.get("same_target_deduped_at")):
        return _same_target_status_completion_time(status_map)
    return max(
        _epoch(status_map.get("root_restart_completed_at")),
        _epoch(status_map.get("finished_at")),
        _status_updated_at(status_map),
    )


def _build_attempt_payload(*, action: str, request: dict[str, Any], status: dict[str, Any] | None, accepted: bool) -> dict[str, Any]:
    now = time.time()
    current_status = dict(status or {})
    countdown_sec = float(request.get("countdown_sec") or current_status.get("countdown_sec") or 0.0)
    scheduled_for = float(current_status.get("scheduled_for") or (now + countdown_sec))
    return {
        "state": "active" if accepted else "rejected",
        "action": str(action or current_status.get("action") or "update"),
        "requested_at": now,
        "transitioned_at": scheduled_for if accepted else now,
        "countdown_sec": countdown_sec,
        "drain_timeout_sec": float(request.get("drain_timeout_sec") or current_status.get("drain_timeout_sec") or 0.0),
        "signal_delay_sec": float(request.get("signal_delay_sec") or current_status.get("signal_delay_sec") or 0.0),
        "target_rev": str(request.get("target_rev") or current_status.get("target_rev") or ""),
        "target_version": str(request.get("target_version") or current_status.get("target_version") or ""),
        "reason": str(request.get("reason") or current_status.get("reason") or ""),
        "accepted": bool(accepted),
        "scheduled_for": scheduled_for if accepted else None,
        "min_update_period_sec": float(request.get("min_update_period_sec") or current_status.get("min_update_period_sec") or 0.0),
        "candidate_prewarm_state": str(
            request.get("candidate_prewarm_state") or current_status.get("candidate_prewarm_state") or ""
        ).strip()
        or None,
        "candidate_prewarm_message": str(
            request.get("candidate_prewarm_message") or current_status.get("candidate_prewarm_message") or ""
        ).strip()
        or None,
        "candidate_prewarm_deferral_count": max(
            0,
            int(
                request.get("candidate_prewarm_deferral_count")
                or current_status.get("candidate_prewarm_deferral_count")
                or 0
            ),
        ),
        "candidate_prewarm_max_deferrals": max(
            0,
            int(
                request.get("candidate_prewarm_max_deferrals")
                or current_status.get("candidate_prewarm_max_deferrals")
                or 0
            ),
        ),
        "last_status": current_status,
        "updated_at": now,
    }


def _complete_update_attempt(*, state: str, status: dict[str, Any] | None, reason: str | None = None) -> dict[str, Any]:
    now = time.time()
    current = _read_update_attempt() or {}
    payload = dict(current)
    payload["state"] = str(state or "completed")
    payload["completed_at"] = now
    payload["updated_at"] = now
    payload["awaiting_restart"] = False
    payload["restart_required"] = False
    payload["candidate_prewarm_state"] = None
    payload["candidate_prewarm_message"] = None
    payload["candidate_prewarm_ready_at"] = None
    if reason:
        payload["completion_reason"] = str(reason)
    if isinstance(status, dict):
        status_payload = dict(status)
        # Rollout status intentionally carries a queued transition through
        # prepare, promotion, and restart.  Once the attempt is terminal there
        # must also be an actual durable queued request, otherwise preserving
        # an old boolean makes read surfaces claim that another mutation is
        # pending even though the reconciler has nothing it can execute.
        status_payload = _clear_orphaned_subsequent_transition_status(
            status_payload,
            payload,
            updated_at=now,
        )
        payload["last_status"] = status_payload
    return _write_update_attempt(payload)


def _active_slot_target_mismatch_status(status: dict[str, Any], attempt: dict[str, Any] | None = None) -> dict[str, Any] | None:
    status_map = status if isinstance(status, dict) else {}
    if str(status_map.get("state") or "").strip().lower() != "succeeded":
        return None
    expected_target_version = str(status_map.get("target_version") or "").strip()
    if not expected_target_version:
        return None
    try:
        manifest = active_slot_manifest()
    except Exception:
        manifest = None
    if _manifest_matches_target_version(manifest, expected_target_version):
        return None
    now = time.time()
    return write_core_update_status(
        {
            "state": "failed",
            "phase": "validate",
            "action": str(status_map.get("action") or (attempt or {}).get("action") or "update"),
            "target_rev": str(status_map.get("target_rev") or (attempt or {}).get("target_rev") or ""),
            "target_version": expected_target_version,
            "target_slot": str((manifest or {}).get("slot") or active_slot() or ""),
            "message": "active slot does not match requested update target; terminal success rejected",
            "reason": "active_slot_target_mismatch",
            "manifest": manifest if isinstance(manifest, dict) else {},
            "supervisor_previous_status": dict(status_map),
            "active_slot_target_mismatch": True,
            "active_slot_target_mismatch_reason": "active_slot_target_mismatch",
            "finished_at": now,
            "updated_at": now,
        }
    )


def _runtime_payload_ready(runtime: dict[str, Any] | None) -> bool:
    payload = runtime if isinstance(runtime, dict) else {}
    runtime_state = str(payload.get("runtime_state") or "").strip().lower()
    return bool(
        runtime_state == "ready"
        or (bool(payload.get("listener_running")) and bool(payload.get("runtime_api_ready")))
    )


def _recover_active_attempt_target_already_active(
    *,
    status: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
) -> dict[str, Any] | None:
    attempt_map = attempt if isinstance(attempt, dict) else {}
    if str(attempt_map.get("state") or "").strip().lower() != "active":
        return None
    if str(attempt_map.get("action") or "update").strip().lower() != "update":
        return None
    target_version = str(attempt_map.get("target_version") or "").strip()
    if not target_version:
        return None
    if not _runtime_payload_ready(runtime):
        return None
    try:
        manifest = active_slot_manifest()
    except Exception:
        manifest = None
    if not _manifest_matches_target_version(manifest, target_version):
        return None

    status_map = status if isinstance(status, dict) else {}
    status_state = str(status_map.get("state") or "").strip().lower()
    status_phase = str(status_map.get("phase") or "").strip().lower()
    if status_state in {"failed", "cancelled", "expired", "rolled_back"} and not bool(status_map.get("supervisor_timeout_at")):
        return None
    if status_state == "validated" and status_phase == "root_promotion_pending":
        return None
    if status_state == "succeeded" and status_phase == "root_promoted":
        return None

    now = time.time()
    slot = str((manifest or {}).get("slot") or active_slot() or "").strip().upper()
    payload = dict(status_map)
    payload.update(
        {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": str(attempt_map.get("target_rev") or status_map.get("target_rev") or ""),
            "target_version": target_version,
            "reason": str(attempt_map.get("reason") or status_map.get("reason") or "supervisor.recovered_active_target"),
            "target_slot": slot,
            "message": (
                f"runtime boot validated on slot {slot}; supervisor recovered stale active attempt"
                if slot
                else "runtime boot validated; supervisor recovered stale active attempt"
            ),
            "manifest": manifest if isinstance(manifest, dict) else {},
            "validated_at": now,
            "finished_at": now,
            "scheduled_for": None,
            "candidate_prewarm_state": None,
            "candidate_prewarm_message": None,
            "candidate_prewarm_ready_at": None,
            "stale_active_attempt_recovered": True,
            "stale_active_attempt_recovered_at": now,
        }
    )
    recovered_status = write_core_update_status(payload)
    with contextlib.suppress(Exception):
        clear_core_update_plan()
    _complete_update_attempt(
        state="completed",
        status=recovered_status,
        reason="active slot target already active",
    )
    return recovered_status


def _reconcile_failed_attempt_after_terminal_success(
    *,
    status: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    status_map = status if isinstance(status, dict) else {}
    attempt_map = attempt if isinstance(attempt, dict) else {}
    if str(status_map.get("state") or "").strip().lower() != "succeeded":
        return None
    if str(status_map.get("phase") or "").strip().lower() != "validate":
        return None
    if str(attempt_map.get("state") or "").strip().lower() != "failed":
        return None
    if str(attempt_map.get("action") or "update").strip().lower() != "update":
        return None
    if not _terminal_status_belongs_to_attempt(status_map, attempt_map):
        return None
    return _complete_update_attempt(
        state="completed",
        status=status_map,
        reason="terminal core update success reconciled",
    )


def _reconcile_completed_attempt_after_runtime_failure(
    *,
    status: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
) -> dict[str, Any] | None:
    status_map = status if isinstance(status, dict) else {}
    attempt_map = attempt if isinstance(attempt, dict) else {}
    if str(attempt_map.get("state") or "").strip().lower() != "completed":
        return None
    if str(attempt_map.get("action") or "update").strip().lower() != "update":
        return None
    if str(status_map.get("state") or "").strip().lower() != "failed":
        return None
    if str(status_map.get("phase") or "").strip().lower() != "uvicorn.run":
        return None
    if not str(status_map.get("message") or "").strip().lower().startswith("autostart runner failed during"):
        return None
    if not _runtime_payload_ready(runtime):
        return None
    try:
        completed_at = float(attempt_map.get("completed_at") or 0.0)
        failed_at = float(status_map.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if completed_at <= 0.0 or failed_at < completed_at:
        return None

    last_status = attempt_map.get("last_status") if isinstance(attempt_map.get("last_status"), dict) else {}
    if str(last_status.get("state") or "").strip().lower() != "succeeded":
        return None
    if str(last_status.get("phase") or "").strip().lower() != "validate":
        return None
    target_version = str(attempt_map.get("target_version") or last_status.get("target_version") or "").strip()
    if not target_version:
        return None
    try:
        manifest = active_slot_manifest()
    except Exception:
        manifest = None
    if not _manifest_matches_target_version(manifest, target_version):
        return None

    now = time.time()
    recovered = dict(last_status)
    recovered.update(
        {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": str(attempt_map.get("target_rev") or last_status.get("target_rev") or ""),
            "target_version": target_version,
            "target_slot": str((manifest or {}).get("slot") or last_status.get("target_slot") or ""),
            "manifest": manifest if isinstance(manifest, dict) else last_status.get("manifest") or {},
            "message": str(last_status.get("message") or "runtime boot validated"),
            "post_update_runtime_failure_reconciled": True,
            "post_update_runtime_failure_reconciled_at": now,
            "post_update_runtime_failure": {
                "observed_at": failed_at,
                "phase": str(status_map.get("phase") or ""),
                "error_type": str(status_map.get("error_type") or ""),
                "error": str(status_map.get("error") or "")[:1000],
                "traceback": str(status_map.get("traceback") or "")[-8000:],
            },
            "updated_at": now,
        }
    )
    return write_core_update_status(recovered)


def _reconcile_failed_target_mismatch_after_active_switch(
    *,
    status: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
) -> dict[str, Any] | None:
    status_map = status if isinstance(status, dict) else {}
    attempt_map = attempt if isinstance(attempt, dict) else {}
    if str(attempt_map.get("state") or "").strip().lower() != "failed":
        return None
    if str(attempt_map.get("action") or "update").strip().lower() != "update":
        return None
    mismatch = bool(status_map.get("active_slot_target_mismatch")) or (
        str(attempt_map.get("completion_reason") or "").strip().lower() == "active slot target mismatch"
    )
    if not mismatch:
        return None
    if not _runtime_payload_ready(runtime):
        return None
    target_version = str(attempt_map.get("target_version") or status_map.get("target_version") or "").strip()
    if not target_version:
        return None
    try:
        manifest = active_slot_manifest()
    except Exception:
        manifest = None
    if not _manifest_matches_target_version(manifest, target_version):
        return None

    now = time.time()
    slot = str((manifest or {}).get("slot") or active_slot() or "").strip().upper()
    payload = dict(status_map)
    payload.update(
        {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": str(attempt_map.get("target_rev") or status_map.get("target_rev") or ""),
            "target_version": target_version,
            "target_slot": slot,
            "message": (
                f"runtime boot validated on slot {slot}; supervisor reconciled target mismatch after active switch"
                if slot
                else "runtime boot validated; supervisor reconciled target mismatch after active switch"
            ),
            "manifest": manifest if isinstance(manifest, dict) else {},
            "active_slot_target_mismatch": False,
            "active_slot_target_mismatch_reconciled": True,
            "active_slot_target_mismatch_reconciled_at": now,
            "validated_at": now,
            "finished_at": now,
            "scheduled_for": None,
            "candidate_prewarm_state": None,
            "candidate_prewarm_message": None,
            "candidate_prewarm_ready_at": None,
        }
    )
    recovered_status = write_core_update_status(payload)
    with contextlib.suppress(Exception):
        clear_core_update_plan()
    return _complete_update_attempt(
        state="completed",
        status=recovered_status,
        reason="active slot target mismatch reconciled",
    )


def _reconcile_failed_root_restart_after_runtime_recovery(
    *,
    status: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Commit a promoted target once its replacement runtime becomes ready.

    A root restart timeout is not proof that promotion failed: systemd may
    restart successfully while the promoted runtime is temporarily unhealthy.
    Root updates intentionally stay out of the fast slot-rollback path, so a
    later supervisor self-heal must be able to validate the already-active
    target and close the durable attempt.
    """
    status_map = status if isinstance(status, dict) else {}
    attempt_map = attempt if isinstance(attempt, dict) else {}
    if str(attempt_map.get("state") or "").strip().lower() != "failed":
        return None
    if str(attempt_map.get("action") or "update").strip().lower() != "update":
        return None
    timed_out = (
        str(attempt_map.get("completion_reason") or "").strip().lower() == "root restart timeout"
        or (
            str(status_map.get("phase") or "").strip().lower() == "root_restart_timeout"
            and bool(status_map.get("supervisor_timeout_at"))
        )
    )
    if not timed_out or not _runtime_payload_ready(runtime):
        return None
    target_version = str(attempt_map.get("target_version") or status_map.get("target_version") or "").strip()
    if not target_version:
        return None
    try:
        manifest = active_slot_manifest()
    except Exception:
        manifest = None
    if not _manifest_matches_target_version(manifest, target_version):
        return None

    now = time.time()
    slot = str((manifest or {}).get("slot") or active_slot() or "").strip().upper()
    recovered_status = write_core_update_status(
        {
            **status_map,
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": str(attempt_map.get("target_rev") or status_map.get("target_rev") or ""),
            "target_version": target_version,
            "target_slot": slot,
            "reason": "supervisor.root_restart_runtime_recovered",
            "message": (
                f"runtime boot validated on slot {slot} after root restart timeout"
                if slot
                else "runtime boot validated after root restart timeout"
            ),
            "manifest": manifest if isinstance(manifest, dict) else {},
            "root_restart_timeout_reconciled": True,
            "root_restart_timeout_reconciled_at": now,
            "validated_at": now,
            "finished_at": now,
            "scheduled_for": None,
            "candidate_prewarm_state": None,
            "candidate_prewarm_message": None,
            "candidate_prewarm_ready_at": None,
        }
    )
    with contextlib.suppress(Exception):
        clear_core_update_plan()
    return _complete_update_attempt(
        state="completed",
        status=recovered_status,
        reason="root restart timeout reconciled after runtime recovery",
    )


def _fail_root_restart_attempt(
    *,
    status: dict[str, Any],
    attempt: dict[str, Any],
    timeout_sec: float,
    now: float,
) -> dict[str, Any]:
    failed_status = write_core_update_status(
        {
            "state": "failed",
            "phase": "root_restart_timeout",
            "action": str(status.get("action") or attempt.get("action") or "update"),
            "target_rev": str(status.get("target_rev") or attempt.get("target_rev") or ""),
            "target_version": str(status.get("target_version") or attempt.get("target_version") or ""),
            "reason": str(status.get("reason") or attempt.get("reason") or "supervisor.root_restart_timeout"),
            "message": "supervisor timed out waiting for autostart service restart after root promotion",
            "supervisor_timeout_sec": timeout_sec,
            "supervisor_timeout_at": now,
            "supervisor_previous_status": dict(status),
        }
    )
    return _complete_update_attempt(
        state="failed",
        status=failed_status,
        reason="root restart timeout",
    )


def _finalize_runtime_boot_status_from_supervisor() -> dict[str, Any] | None:
    current = read_core_update_status()
    if _is_root_restart_pending_status(current) and not _root_restart_crossed_supervisor_generation(current):
        return None
    finalized = finalize_runtime_boot_status(supervisor_authorized=True)
    if not isinstance(finalized, dict):
        return None
    if float(finalized.get("root_restart_completed_at") or 0.0) > 0.0:
        finalized = dict(finalized)
        for key in (
            "root_promotion_supervisor_instance_id",
            "root_promotion_supervisor_pid",
            "root_promotion_supervisor_started_at",
            "restart_requested_by_instance_id",
            "restart_requested_by_pid",
            "restart_requested_by_started_at",
        ):
            if key not in finalized and current.get(key) is not None:
                finalized[key] = current[key]
        finalized["root_restart_completed_by_instance_id"] = _SUPERVISOR_INSTANCE_ID
        finalized["root_restart_completed_by_pid"] = os.getpid()
        finalized["root_restart_completed_by_started_at"] = _SUPERVISOR_INSTANCE_STARTED_AT
        finalized = write_core_update_status(finalized)
    return finalized


def _reconcile_update_status(payload: dict[str, Any]) -> dict[str, Any]:
    with _try_update_transition_guard(operation="update.reconcile") as acquired:
        if not acquired:
            deferred = dict(payload)
            deferred["status"] = read_core_update_status()
            deferred["attempt"] = _read_update_attempt() or {}
            deferred["reconciliation"] = {
                "deferred": True,
                "retryable": True,
                "reason": "update_transition_guard_busy",
            }
            deferred["_served_by"] = "supervisor_transition_busy"
            return deferred
        return _UPDATE_RECONCILIATION.reconcile(
            _update_reconciliation_operations(),
            payload,
        )


def _compact_public_runtime_self_heal(value: dict[str, Any] | None) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    decision = state.get("last_decision") if isinstance(state.get("last_decision"), dict) else {}
    last_evidence = state.get("last_evidence") if isinstance(state.get("last_evidence"), dict) else {}
    has_decision = any(
        decision.get(key) not in (None, "", {})
        for key in ("recorded_at", "reason", "message", "runtime_port", "runtime_url", "timeout_sec")
    )
    has_last_evidence = any(
        last_evidence.get(key) not in (None, "", {})
        for key in ("captured_at", "reason", "stage", "pid", "runtime_instance_id", "evidence_path", "evidence_error")
    )
    pre_restart_evidence = (
        decision.get("pre_restart_evidence")
        if isinstance(decision.get("pre_restart_evidence"), dict)
        else last_evidence
    )
    evidence = _compact_runtime_stop_evidence(pre_restart_evidence) if has_decision or has_last_evidence else {}
    public_decision = {}
    if has_decision:
        public_decision = {
            "recorded_at": decision.get("recorded_at"),
            "reason": str(decision.get("reason") or "").strip() or None,
            "message": str(decision.get("message") or "").strip() or None,
            "runtime_port": decision.get("runtime_port"),
            "runtime_url": str(decision.get("runtime_url") or "").strip() or None,
            "listener_running": decision.get("listener_running"),
            "runtime_api_ready": decision.get("runtime_api_ready"),
            "timeout_sec": decision.get("timeout_sec"),
            "pre_restart_evidence": evidence,
        }
    return {
        "unhealthy_since": state.get("unhealthy_since"),
        "unhealthy_kind": str(state.get("unhealthy_kind") or "").strip() or None,
        "last_decision": public_decision,
        "last_evidence": evidence,
    }


def _public_update_status_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return _UPDATE_RECONCILIATION.public_payload(
        _update_reconciliation_operations(),
        payload,
    )


def _listener_running(host: str, port: int, *, timeout: float = 0.35) -> bool:
    return _PROCESS_SUPERVISOR.listener_running(host, port, timeout=timeout)


def _runtime_api_probe(base_url: str, *, token: str | None, timeout: float = 0.75) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-AdaOS-Token"] = token
    try:
        with requests.get(f"{base_url}/api/ping", headers=headers, timeout=max(0.1, float(timeout))) as response:
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {
            "ready": False,
            "runtime": {},
            "error_type": type(exc).__name__,
        }
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return {
            "ready": False,
            "runtime": {},
            "error_type": "invalid_response",
        }
    readiness = payload.get("readiness")
    if isinstance(readiness, dict):
        ready = readiness.get("ready") is True and str(readiness.get("state") or "").strip().lower() == "ready"
    else:
        # Compatibility with a runtime from the generation immediately before
        # the explicit boot-readiness contract was introduced.
        ready = True
    runtime = payload.get("runtime")
    return {
        "ready": bool(ready),
        "runtime": dict(runtime) if isinstance(runtime, dict) else {},
        "error_type": None,
    }


def _runtime_api_ready(base_url: str, *, token: str | None, timeout: float = 0.75) -> bool:
    return bool(_runtime_api_probe(base_url, token=token, timeout=timeout).get("ready"))


def _runtime_beacon_ready(base_url: str, *, token: str | None, timeout: float = 1.25) -> bool:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-AdaOS-Token"] = token
    try:
        with requests.get(
            f"{base_url}/api/node/reliability/runtime?webspace_id=desktop",
            headers=headers,
            timeout=max(0.2, float(timeout)),
        ) as response:
            if int(response.status_code or 0) != 200:
                return False
            payload = response.json()
            stale = str(response.headers.get("X-AdaOS-Runtime-Stale") or "0").strip().lower()
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("ok") is True and stale in {"0", "1"}


def _runtime_listener_restart_timeout_sec() -> float:
    return _RUNTIME_CONFIG.runtime_listener_restart_timeout_sec()


def _runtime_listener_startup_grace_sec() -> float:
    return _RUNTIME_CONFIG.runtime_listener_startup_grace_sec(
        listener_timeout_sec=_runtime_listener_restart_timeout_sec()
    )


def _runtime_api_restart_timeout_sec() -> float:
    return _RUNTIME_CONFIG.runtime_api_restart_timeout_sec()


def _runtime_shutdown_request_timeout(*, drain_timeout_sec: float, signal_delay_sec: float) -> float:
    return max(5.0, float(drain_timeout_sec) + float(signal_delay_sec) + 2.0)


def _runtime_profile_graceful_shutdown_timeout_sec(profile_mode: str) -> tuple[float, float, float, float]:
    return _MEMORY_PROFILING.graceful_shutdown_timeouts(profile_mode)


def _signal_process_family(proc: subprocess.Popen[Any], sig: int) -> None:
    _PROCESS_SUPERVISOR.signal_family(proc, sig)


def _runtime_profile_finalize_wait_sec() -> float:
    return _MEMORY_PROFILING.finalize_wait_sec()


def _memory_profile_max_runtime_sec(profile_mode: str) -> float:
    return _MEMORY_PROFILING.max_runtime_sec(profile_mode)


def _proc_details(proc: subprocess.Popen[Any] | None, *, cwd_hint: str | None = None) -> dict[str, Any]:
    return _PROCESS_SUPERVISOR.describe(proc, cwd_hint=cwd_hint)


def _process_family_rss_bytes(pid: int | None) -> tuple[int | None, int | None]:
    return _MEMORY_PROFILING.family_rss_bytes(pid, psutil_module=psutil)


def _process_family_memory_snapshot(pid: int | None, *, max_children: int = 12) -> dict[str, Any]:
    return _MEMORY_PROFILING.process_family_snapshot(
        pid,
        psutil_module=psutil,
        max_children=max_children,
    )


def _system_process_memory_snapshot(pid: int | None, *, max_processes: int = 12) -> dict[str, Any]:
    return _MEMORY_PROFILING.system_process_memory_snapshot(
        pid,
        psutil_module=psutil,
        max_processes=max_processes,
    )


def _parse_linux_memory_stat(text: str) -> dict[str, int]:
    return _MEMORY_PROFILING.parse_linux_memory_stat(text)


def _linux_cgroup_memory_snapshot(pid: int | None) -> dict[str, Any]:
    if not pid or not sys.platform.startswith("linux"):
        return {"available": False, "reason": "linux_required"}
    try:
        cgroup_text = Path(f"/proc/{int(pid)}/cgroup").read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"available": False, "reason": f"cgroup_read_failed:{type(exc).__name__}"}
    cgroup_rel = ""
    for line in cgroup_text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers = parts[1]
        if controllers == "" or "memory" in controllers.split(","):
            cgroup_rel = parts[2].strip()
            break
    if not cgroup_rel:
        return {"available": False, "reason": "memory_cgroup_not_found"}
    cgroup_path = Path("/sys/fs/cgroup") / cgroup_rel.lstrip("/")
    try:
        current = int((cgroup_path / "memory.current").read_text(encoding="utf-8").strip())
    except Exception:
        current = None
    try:
        stat = _parse_linux_memory_stat((cgroup_path / "memory.stat").read_text(encoding="utf-8", errors="replace"))
    except Exception:
        stat = {}
    return {
        "available": bool(current is not None or stat),
        "path": str(cgroup_path),
        "current_bytes": current,
        "stat": stat,
        "anon_bytes": stat.get("anon"),
        "file_bytes": stat.get("file"),
        "kernel_bytes": stat.get("kernel"),
        "slab_bytes": stat.get("slab"),
    }


def _linux_smaps_rollup_snapshot(pid: int | None) -> dict[str, Any]:
    if not pid or not sys.platform.startswith("linux"):
        return {"available": False, "reason": "linux_required"}
    path = Path(f"/proc/{int(pid)}/smaps_rollup")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"available": False, "reason": f"smaps_rollup_read_failed:{type(exc).__name__}"}
    result: dict[str, Any] = {"available": True, "path": str(path)}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parts = value.strip().split()
        if not parts:
            continue
        try:
            amount = int(parts[0])
        except Exception:
            continue
        unit = parts[1].lower() if len(parts) > 1 else ""
        result[f"{key.strip().lower().replace(' ', '_')}_bytes"] = amount * 1024 if unit == "kb" else amount
    return result


def _linux_process_state_snapshot(pid: int | None, *, max_threads: int = 16) -> dict[str, Any]:
    if not pid or not sys.platform.startswith("linux"):
        return {"available": False, "reason": "linux_required"}
    normalized_pid = int(pid)
    proc_dir = Path(f"/proc/{normalized_pid}")
    status: dict[str, str] = {}
    try:
        status_text = (proc_dir / "status").read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"available": False, "reason": f"status_read_failed:{type(exc).__name__}"}
    for line in status_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        if normalized_key in {
            "state",
            "vmrss",
            "vmhwm",
            "threads",
            "voluntary_ctxt_switches",
            "nonvoluntary_ctxt_switches",
        }:
            status[normalized_key] = value.strip()
    try:
        wchan = (proc_dir / "wchan").read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        wchan = None
    threads: list[dict[str, Any]] = []
    try:
        task_dirs = sorted((proc_dir / "task").iterdir(), key=lambda item: int(item.name))
    except Exception:
        task_dirs = []
    for task_dir in task_dirs[: max(0, int(max_threads or 0))]:
        try:
            tid = int(task_dir.name)
        except Exception:
            continue
        try:
            task_wchan = (task_dir / "wchan").read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            task_wchan = None
        task_state = None
        with contextlib.suppress(Exception):
            for line in (task_dir / "status").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("State:"):
                    task_state = line.split(":", 1)[1].strip()
                    break
        threads.append({"tid": tid, "state": task_state, "wchan": task_wchan})
    return {
        "available": True,
        "pid": normalized_pid,
        "state": status.get("state"),
        "wchan": wchan,
        "vmrss": status.get("vmrss"),
        "vmhwm": status.get("vmhwm"),
        "threads_total": _positive_int_or_none(status.get("threads")),
        "voluntary_ctxt_switches": _positive_int_or_none(status.get("voluntary_ctxt_switches")),
        "nonvoluntary_ctxt_switches": _positive_int_or_none(status.get("nonvoluntary_ctxt_switches")),
        "threads_returned": len(threads),
        "threads": threads,
    }


def _compact_runtime_stop_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    memory = evidence.get("memory") if isinstance(evidence.get("memory"), dict) else {}
    process = evidence.get("process") if isinstance(evidence.get("process"), dict) else {}
    return {
        "captured_at": evidence.get("captured_at"),
        "reason": evidence.get("reason"),
        "stage": evidence.get("stage"),
        "pid": evidence.get("pid"),
        "runtime_instance_id": evidence.get("runtime_instance_id"),
        "transition_role": evidence.get("transition_role"),
        "evidence_path": evidence.get("evidence_path"),
        "evidence_error": evidence.get("evidence_error"),
        "memory": {
            "process_rss_bytes": memory.get("process_rss_bytes"),
            "family_rss_bytes": memory.get("family_rss_bytes"),
            "cgroup_memory_current_bytes": memory.get("cgroup_memory_current_bytes"),
            "cgroup_anon_bytes": memory.get("cgroup_anon_bytes"),
            "cgroup_file_bytes": memory.get("cgroup_file_bytes"),
            "cgroup_kernel_bytes": memory.get("cgroup_kernel_bytes"),
            "cgroup_slab_bytes": memory.get("cgroup_slab_bytes"),
        },
        "process": {
            "available": process.get("available"),
            "state": process.get("state"),
            "wchan": process.get("wchan"),
            "threads_total": process.get("threads_total"),
            "threads_returned": process.get("threads_returned"),
            "threads": process.get("threads") if isinstance(process.get("threads"), list) else [],
        },
    }


def _runtime_memory_attribution_snapshot(
    pid: int | None,
    *,
    process_rss_bytes: int | None = None,
    family_rss_bytes: int | None = None,
) -> dict[str, Any]:
    cgroup = _linux_cgroup_memory_snapshot(pid)
    process_tree = _process_family_memory_snapshot(pid)
    return {
        "process_rss_bytes": process_rss_bytes,
        "family_rss_bytes": family_rss_bytes,
        "process_tree": process_tree,
        "cgroup_memory_current_bytes": cgroup.get("current_bytes"),
        "cgroup_anon_bytes": cgroup.get("anon_bytes"),
        "cgroup_file_bytes": cgroup.get("file_bytes"),
        "cgroup_kernel_bytes": cgroup.get("kernel_bytes"),
        "cgroup_slab_bytes": cgroup.get("slab_bytes"),
        "cgroup_memory_stat": cgroup.get("stat") if isinstance(cgroup.get("stat"), dict) else {},
        "cgroup": cgroup,
    }


def _safe_evidence_label(value: str | None) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text.strip("._-")[:80] or "runtime"


def _positive_int_or_none(value: Any) -> int | None:
    try:
        item = int(value)
    except Exception:
        return None
    return item if item > 0 else None


class _AdoptedProcess(AdoptedProcess):
    def __init__(self, pid: int) -> None:
        super().__init__(pid, psutil_module=psutil)


def _listener_owner_pid(host: str, port: int) -> int | None:
    return _PROCESS_SUPERVISOR.listener_owner_pid(host, port)


def _format_slot_value(template: str, values: dict[str, str]) -> str:
    fields = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    payload = dict(values)
    for field in fields:
        payload.setdefault(field, "")
    return template.format(**payload)


def _owned_field(owner_name: str, field_name: str) -> property:
    """Expose a temporary manager-compatible view over component-owned state."""

    def _get(instance: Any) -> Any:
        return getattr(getattr(instance, owner_name), field_name)

    def _set(instance: Any, value: Any) -> None:
        setattr(getattr(instance, owner_name), field_name, value)

    return property(_get, _set)


class SupervisorManager:
    _proc = _owned_field("_process_supervisor", "active")
    _candidate_proc = _owned_field("_process_supervisor", "candidate")
    _sidecar_proc = _owned_field("_process_supervisor", "sidecar")
    _desired_running = _owned_field("_process_supervisor", "desired_running")
    _stopping = _owned_field("_process_supervisor", "stopping")
    _lock = _owned_field("_process_supervisor", "lock")
    _monitor_task = _owned_field("_process_supervisor", "monitor_task")
    _update_task = _owned_field("_update_state_machine", "task")
    _update_task_cancel_mode = _owned_field("_update_state_machine", "cancel_mode")
    _runtime_unhealthy_since = _owned_field("_recovery_policy", "unhealthy_since")
    _runtime_unhealthy_kind = _owned_field("_recovery_policy", "unhealthy_kind")
    _runtime_self_heal_last_decision = _owned_field("_recovery_policy", "last_decision")
    _runtime_self_heal_last_evidence = _owned_field("_recovery_policy", "last_evidence")
    _memory_profiler_adapter = _owned_field("_memory_profiling", "profiler_adapter_name")
    _memory_profile_mode = _owned_field("_memory_profiling", "profile_mode")
    _memory_requested_profile_mode = _owned_field("_memory_profiling", "requested_profile_mode")
    _memory_publish_request_session_id = _owned_field("_memory_profiling", "publish_request_session_id")
    _memory_profile_current_trigger_source = _owned_field("_memory_profiling", "profile_current_trigger_source")
    _memory_suspicion_state = _owned_field("_memory_profiling", "suspicion_state")
    _memory_suspicion_reason = _owned_field("_memory_profiling", "suspicion_reason")
    _memory_suspicion_since = _owned_field("_memory_profiling", "suspicion_since")
    _memory_active_session_id = _owned_field("_memory_profiling", "active_session_id")
    _memory_profile_finalizing_session_id = _owned_field("_memory_profiling", "profile_finalizing_session_id")
    _memory_last_session_id = _owned_field("_memory_profiling", "last_session_id")
    _memory_baseline_scope_key = _owned_field("_memory_profiling", "baseline_scope_key")
    _memory_baseline_pid = _owned_field("_memory_profiling", "baseline_pid")
    _memory_baseline_family_rss_bytes = _owned_field("_memory_profiling", "baseline_family_rss_bytes")
    _memory_baseline_started_at = _owned_field("_memory_profiling", "baseline_started_at")
    _memory_baseline_matured_at = _owned_field("_memory_profiling", "baseline_matured_at")
    _memory_baseline_phase = _owned_field("_memory_profiling", "baseline_phase")
    _memory_baseline_last_adjusted_at = _owned_field("_memory_profiling", "baseline_last_adjusted_at")
    _memory_baseline_last_adjustment_reason = _owned_field(
        "_memory_profiling",
        "baseline_last_adjustment_reason",
    )
    _memory_baseline_adjustment_total = _owned_field("_memory_profiling", "baseline_adjustment_total")
    _memory_last_growth_bytes = _owned_field("_memory_profiling", "last_growth_bytes")
    _memory_last_growth_bytes_per_min = _owned_field("_memory_profiling", "last_growth_bytes_per_min")
    _memory_last_available_bytes = _owned_field("_memory_profiling", "last_available_bytes")
    _memory_last_available_percent = _owned_field("_memory_profiling", "last_available_percent")
    _memory_last_telemetry_at = _owned_field("_memory_profiling", "last_telemetry_at")
    _memory_auto_profile_last_block_reason = _owned_field(
        "_memory_profiling",
        "auto_profile_last_block_reason",
    )
    _memory_auto_profile_last_block_at = _owned_field("_memory_profiling", "auto_profile_last_block_at")
    _memory_critical_since = _owned_field("_memory_profiling", "critical_since")
    _memory_critical_reason = _owned_field("_memory_profiling", "critical_reason")
    _memory_critical_pressure_owner = _owned_field("_memory_profiling", "critical_pressure_owner")
    _memory_critical_action = _owned_field("_memory_profiling", "critical_action")
    _memory_critical_attribution = _owned_field("_memory_profiling", "critical_attribution")
    _memory_critical_observation_last_at = _owned_field("_memory_profiling", "critical_observation_last_at")
    _memory_critical_restart_last_at = _owned_field("_memory_profiling", "critical_restart_last_at")
    _memory_critical_last_decision = _owned_field("_memory_profiling", "critical_last_decision")
    _memory_critical_last_evidence = _owned_field("_memory_profiling", "critical_last_evidence")

    def __init__(self, *, runtime_host: str, runtime_port: int, token: str | None) -> None:
        self.runtime_host = str(runtime_host or "127.0.0.1").strip() or "127.0.0.1"
        self.runtime_port = int(runtime_port)
        self.token = str(token or "").strip() or None
        self._process_supervisor = ProcessSupervisor(psutil)
        self._monitoring = SupervisorMonitoringService()
        self._status_service = SupervisorStatusService()
        self._update_execution = SupervisorUpdateExecution()
        self._update_state_machine = UpdateStateMachine()
        self._update_state_machine.bind_persistence(
            write_status=write_core_update_status,
            write_attempt=_replace_update_attempt,
        )
        self._recovery_policy = RuntimeRecoveryPolicy()
        self._memory_profiling = MemoryProfilingService(
            default_profiler_adapter=DEFAULT_PROFILER_ADAPTER,
        )
        ensure_memory_store()
        self._proc: Any | None = None
        self._candidate_proc: Any | None = None
        self._sidecar_proc: Any | None = None
        self._desired_running = True
        self._stopping = False
        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task[Any] | None = None
        self._monitor_loop_started_at: float | None = None
        self._monitor_last_iteration_at: float | None = None
        self._monitor_last_failure_at: float | None = None
        self._monitor_last_failure: str | None = None
        self._monitor_failure_total = 0
        self._monitor_recovery_total = 0
        self._retired_runtime_tasks: set[asyncio.Task[Any]] = set()
        self._retired_runtime_procs: dict[int, Any] = {}
        self._restart_count = 0
        self._last_start_at: float | None = None
        self._last_exit_at: float | None = None
        self._last_exit_code: int | None = None
        self._last_error: str | None = None
        self._runtime_unhealthy_since: float | None = None
        self._runtime_unhealthy_kind: str | None = None
        self._runtime_self_heal_last_decision: dict[str, Any] | None = None
        self._runtime_self_heal_last_evidence: dict[str, Any] | None = None
        self._hub_root_watchdog_last_reconnect_at: float | None = None
        self._hub_root_watchdog_last_state: str | None = None
        self._hub_root_watchdog_last_reason: str | None = None
        self._hub_root_watchdog_reconnect_total = 0
        self._hub_root_watchdog_last_result: dict[str, Any] | None = None
        self._hub_root_root_probe_last_at: float | None = None
        self._hub_root_root_probe_last_result: dict[str, Any] | None = None
        self._hub_root_root_probe_last_state: str | None = None
        self._hub_root_root_probe_last_reason: str | None = None
        self._hub_root_post_recovery_reconcile_last_at: float | None = None
        self._hub_root_post_recovery_reconcile_last_result: dict[str, Any] | None = None
        self._hub_root_post_recovery_reconcile_last_key: str | None = None
        self._member_hub_watchdog_last_reconnect_at: float | None = None
        self._member_hub_watchdog_last_state: str | None = None
        self._member_hub_watchdog_last_reason: str | None = None
        self._member_hub_watchdog_reconnect_total = 0
        self._member_hub_watchdog_last_result: dict[str, Any] | None = None
        self._member_hub_post_recovery_refresh_last_at: float | None = None
        self._member_hub_post_recovery_refresh_last_result: dict[str, Any] | None = None
        self._update_task: asyncio.Task[Any] | None = None
        self._update_task_cancel_mode: str | None = None
        self._skill_runtime_migration_gate_lease: Any | None = None
        self._managed_runtime_instance_id: str | None = None
        self._managed_transition_role: str | None = None
        self._managed_slot: str | None = None
        self._managed_runtime_port: int | None = None
        self._managed_runtime_base_url: str | None = None
        self._managed_runtime_cwd: str | None = None
        self._managed_start_reason: str | None = None
        self._managed_runtime_api_identity_verified = False
        self._managed_runtime_api_identity_observed_at: float | None = None
        self._managed_runtime_api_identity: dict[str, Any] = {}
        self._last_stop_reason: str | None = None
        self._candidate_slot: str | None = None
        self._candidate_runtime_instance_id: str | None = None
        self._candidate_transition_role: str | None = None
        self._candidate_runtime_cwd: str | None = None
        self._candidate_start_reason: str | None = None
        self._candidate_last_stop_reason: str | None = None
        self._service_restart_pending = False
        self._service_restart_thread: threading.Thread | None = None
        self._memory_profiler_adapter = _memory_profiler_adapter()
        self._memory_profile_mode = "normal"
        self._memory_requested_profile_mode: str | None = None
        self._memory_publish_request_session_id: str | None = None
        self._memory_profile_current_trigger_source: str | None = None
        self._memory_suspicion_state = "idle"
        self._memory_suspicion_reason: str | None = None
        self._memory_suspicion_since: float | None = None
        self._memory_active_session_id: str | None = None
        self._memory_profile_finalizing_session_id: str | None = None
        self._memory_last_session_id: str | None = None
        self._memory_baseline_scope_key: str | None = None
        self._memory_baseline_pid: int | None = None
        self._memory_baseline_family_rss_bytes: int | None = None
        self._memory_baseline_started_at: float | None = None
        self._memory_baseline_matured_at: float | None = None
        self._memory_baseline_phase = "uninitialized"
        self._memory_baseline_last_adjusted_at: float | None = None
        self._memory_baseline_last_adjustment_reason: str | None = None
        self._memory_baseline_adjustment_total = 0
        self._memory_last_growth_bytes: int | None = None
        self._memory_last_growth_bytes_per_min: float | None = None
        self._memory_last_available_bytes: int | None = None
        self._memory_last_available_percent: float | None = None
        self._memory_last_telemetry_at: float | None = None
        self._memory_auto_profile_last_block_reason: str | None = None
        self._memory_auto_profile_last_block_at: float | None = None
        self._memory_critical_since: float | None = None
        self._memory_critical_reason: str | None = None
        self._memory_critical_pressure_owner: str | None = None
        self._memory_critical_action: str | None = None
        self._memory_critical_attribution: dict[str, Any] = {}
        self._memory_critical_observation_last_at: float | None = None
        self._memory_critical_restart_last_at: float | None = None
        self._memory_critical_last_decision: dict[str, Any] = {}
        self._memory_critical_last_evidence: dict[str, Any] = {}
        self._sidecar_launch_cwd: str | None = None
        self._sidecar_last_start_reason: str | None = None
        self._sidecar_last_restart_reason: str | None = None
        self._sidecar_transition_in_progress = False
        self._sidecar_transition_id: str | None = None
        self._sidecar_transition_source: str | None = None
        self._sidecar_transition_reason: str | None = None
        self._sidecar_transition_started_at: float | None = None
        self._sidecar_transition_completed_at: float | None = None
        self._sidecar_transition_outcome: str | None = None
        self._sidecar_transition_error: str | None = None
        self._sidecar_last_probe_at: float | None = None
        self._sidecar_last_probe_ok: bool | None = None
        self._sidecar_last_probe_error: str | None = None
        self._sidecar_consecutive_probe_failures = 0
        self._sidecar_code_fingerprint: str | None = None
        self._sidecar_code_fingerprint_updated_at: float | None = None
        self._sidecar_code_change_pending_fingerprint: str | None = None
        self._sidecar_code_change_pending_since: float | None = None
        self._sidecar_restart_history: deque[float] = deque(maxlen=32)
        self._sidecar_restart_backoff_until: float | None = None
        self._sidecar_circuit_open_until: float | None = None
        self._sidecar_last_restart_at: float | None = None
        self._sidecar_last_sync_at: float | None = None
        self._sidecar_last_sync_source_slot: str | None = None
        self._sidecar_last_sync_reason: str | None = None
        self._sidecar_last_sync_changed_paths: list[str] = []
        self._required_upstream_watchdog_last_poll_at: float | None = None
        self._status_snapshot_lock = threading.RLock()
        self._status_snapshot_generation = 0
        self._status_snapshot_observed_at: float | None = None
        self._status_snapshot_reason = "initializing"
        self._status_durable_updated_at: float | None = None
        self._skill_runtime_migration_lease_path = str(
            (current_base_dir() / "state" / "skill_runtime_migration" / "worker.lock").resolve()
        )
        initialized_at = time.time()
        self._status_snapshot: dict[str, Any] = {
            "ok": True,
            "supervisor_pid": os.getpid(),
            "supervisor_url": _supervisor_base_url(),
            "runtime_url": self.slot_runtime_base_url(None),
            "runtime_host": self.runtime_host,
            "runtime_port": self.runtime_port,
            "managed_slot": None,
            "runtime_instance_id": None,
            "transition_role": None,
            "active_slot": None,
            "previous_slot": None,
            "desired_running": True,
            "stopping": False,
            "managed_pid": None,
            "managed_alive": False,
            "listener_running": False,
            "runtime_api_ready": False,
            "runtime_state": "initializing",
            "sidecar": {},
            "update_attempt": {},
            "updated_at": initialized_at,
        }
        self._status_snapshot_observed_at = initialized_at

    @staticmethod
    def _process_operations() -> ProcessSupervisorOperations:
        return ProcessSupervisorOperations(
            active_slot=active_slot,
            active_slot_manifest=active_slot_manifest,
            adopted_process_type=_AdoptedProcess,
            core_slot_status=core_slot_status,
            current_base_dir=current_base_dir,
            format_slot_value=_format_slot_value,
            listener_owner_pid=_listener_owner_pid,
            logger=_LOG,
            new_runtime_instance_id=_new_runtime_instance_id,
            proc_details=_proc_details,
            read_json=_read_json,
            read_memory_session_summary=read_memory_session_summary,
            read_slot_manifest=read_slot_manifest,
            requests_module=requests,
            runtime_api_ready=_runtime_api_ready,
            supervisor_runtime_state_path=_supervisor_runtime_state_path,
        )

    @staticmethod
    def _recovery_operations() -> RuntimeRecoveryOperations:
        return RuntimeRecoveryOperations(
            hub_root_watchdog_cooldown_sec=_hub_root_watchdog_cooldown_sec,
            hub_root_watchdog_enabled=_hub_root_watchdog_enabled,
            hub_root_watchdog_reset_degraded_route_enabled=(
                _hub_root_watchdog_reset_degraded_route_enabled
            ),
            member_hub_watchdog_cooldown_sec=_member_hub_watchdog_cooldown_sec,
            member_hub_watchdog_enabled=_member_hub_watchdog_enabled,
        )

    @staticmethod
    def _memory_operations() -> MemoryProfilingOperations:
        return MemoryProfilingOperations(
            default_profiler_adapter=DEFAULT_PROFILER_ADAPTER,
            http_exception_type=HTTPException,
            implemented_profile_control_actions=IMPLEMENTED_PROFILE_CONTROL_ACTIONS,
            implemented_profile_control_mode=IMPLEMENTED_PROFILE_CONTROL_MODE,
            memory_operation_contract_version=MEMORY_OPERATION_CONTRACT_VERSION,
            profile_launch_env_keys=PROFILE_LAUNCH_ENV_KEYS,
            top_level_operation_events=TOP_LEVEL_OPERATION_EVENTS,
            available_memory_bytes=_available_memory_bytes,
            memory_auto_profile_browser_live_ttl_sec=_memory_auto_profile_browser_live_ttl_sec,
            memory_auto_profile_min_uptime_sec=_memory_auto_profile_min_uptime_sec,
            memory_baseline_maturity_slope_bytes_per_min=_memory_baseline_maturity_slope_bytes_per_min,
            memory_baseline_warmup_sec=_memory_baseline_warmup_sec,
            memory_critical_available_bytes_threshold=_memory_critical_available_bytes_threshold,
            memory_critical_available_percent_threshold=_memory_critical_available_percent_threshold,
            memory_critical_duration_sec=_memory_critical_duration_sec,
            memory_critical_restart_cooldown_sec=_memory_critical_restart_cooldown_sec,
            memory_policy_profile_restarts_enabled=_memory_policy_profile_restarts_enabled,
            memory_suspicion_family_rss_threshold_bytes=_memory_suspicion_family_rss_threshold_bytes,
            memory_suspicion_growth_threshold_bytes=_memory_suspicion_growth_threshold_bytes,
            memory_suspicion_slope_threshold_bytes_per_min=(
                _memory_suspicion_slope_threshold_bytes_per_min
            ),
            memory_telemetry_interval_sec=_memory_telemetry_interval_sec,
            memory_telemetry_window_sec=_memory_telemetry_window_sec,
            positive_int_or_none=_positive_int_or_none,
            proc_details=_proc_details,
            process_family_rss_bytes=_process_family_rss_bytes,
            runtime_memory_attribution_snapshot=_runtime_memory_attribution_snapshot,
            total_memory_bytes=_total_memory_bytes,
            active_slot=active_slot,
            append_memory_telemetry_sample=append_memory_telemetry_sample,
            ensure_memory_store=ensure_memory_store,
            read_memory_session_index=read_memory_session_index,
            read_memory_session_operations=read_memory_session_operations,
            read_memory_session_summary=read_memory_session_summary,
            read_memory_telemetry_tail=read_memory_telemetry_tail,
            supervisor_memory_sessions_index_path=supervisor_memory_sessions_index_path,
            supervisor_memory_session_artifacts_dir=supervisor_memory_session_artifacts_dir,
            supervisor_memory_telemetry_path=supervisor_memory_telemetry_path,
        )

    @staticmethod
    def _status_operations() -> SupervisorStatusOperations:
        return SupervisorStatusOperations(
            active_slot=active_slot,
            active_slot_manifest=active_slot_manifest,
            core_slot_status=core_slot_status,
            listener_running=_listener_running,
            proc_details=_proc_details,
            read_core_update_status=read_core_update_status,
            read_jsonl_tail=_read_jsonl_tail,
            read_slot_manifest=read_slot_manifest,
            read_update_attempt=_read_update_attempt,
            realtime_sidecar_diag_path=realtime_sidecar_diag_path,
            realtime_sidecar_local_url=realtime_sidecar_local_url,
            resolved_root_promotion_requirement=resolved_root_promotion_requirement,
            runtime_api_ready=_runtime_api_ready,
            supervisor_base_url=_supervisor_base_url,
            validate_slot_structure=validate_slot_structure,
        )

    @staticmethod
    def _monitoring_operations() -> SupervisorMonitoringOperations:
        return SupervisorMonitoringOperations(
            active_slot=active_slot,
            logger=_LOG,
            realtime_sidecar_enabled=realtime_sidecar_enabled,
            realtime_sidecar_listener_snapshot=realtime_sidecar_listener_snapshot,
            restart_realtime_sidecar_subprocess=restart_realtime_sidecar_subprocess,
            sidecar_code_change_debounce_sec=_sidecar_code_change_debounce_sec,
        )

    @staticmethod
    def _update_execution_operations() -> SupervisorUpdateExecutionOperations:
        return SupervisorUpdateExecutionOperations(
            build_attempt_payload=_build_attempt_payload,
            complete_update_attempt=_complete_update_attempt,
            revoke_prepare_lease=_revoke_prepare_lease,
            warm_switch_cold_fallback_enabled=_warm_switch_cold_fallback_enabled,
            warm_switch_defer_sec=_warm_switch_defer_sec,
            warm_switch_enabled=_warm_switch_enabled,
            warm_switch_max_deferrals=_warm_switch_max_deferrals,
            warm_switch_strict_cutover_enabled=_warm_switch_strict_cutover_enabled,
            write_prepare_lease=_write_prepare_lease,
            write_update_attempt=_write_update_attempt_preserving_subsequent_transition,
            activate_slot=activate_slot,
            choose_inactive_slot=choose_inactive_slot,
            clear_core_update_plan=clear_core_update_plan,
            prepare_pending_update=prepare_pending_update,
            remove_inactive_slot=remove_inactive_slot,
            write_core_update_plan=write_core_update_plan,
            write_core_update_status=write_core_update_status,
        )

    def _sidecar_repo_root(self) -> Path | None:
        def _normalize_candidate(raw: Any) -> Path | None:
            try:
                text = str(raw or "").strip()
                if not text:
                    return None
                path = Path(text).expanduser().resolve()
            except Exception:
                return None
            return path if path.exists() else None

        def _looks_like_python_install_root(path: Path) -> bool:
            parts = tuple(part.lower() for part in path.parts)
            if "site-packages" in parts or "dist-packages" in parts:
                return True
            for idx, part in enumerate(parts):
                if part == "venv" and idx + 2 < len(parts):
                    if parts[idx + 1] == "lib" and parts[idx + 2].startswith("python"):
                        return True
            return False

        def _looks_like_project_root(path: Path) -> bool:
            try:
                if (path / ".git").exists() or (path / "pyproject.toml").exists():
                    return True
                src_root = path / "src" / "adaos"
                return src_root.exists()
            except Exception:
                return False

        def _shared_dotenv_repo_root() -> Path | None:
            raw = str(os.getenv("ADAOS_SHARED_DOTENV_PATH") or "").strip()
            if not raw:
                return None
            try:
                dotenv_path = Path(raw).expanduser().resolve()
            except Exception:
                return None
            if not dotenv_path.exists():
                return None
            candidate = dotenv_path.parent
            return candidate if _looks_like_project_root(candidate) else None

        shared_dotenv_root = _shared_dotenv_repo_root()
        if shared_dotenv_root is not None:
            return shared_dotenv_root
        try:
            ctx = get_ctx()
            repo_root = ctx.paths.repo_root()
            raw = repo_root() if callable(repo_root) else repo_root
            candidate = _normalize_candidate(raw)
            if candidate is not None and not _looks_like_python_install_root(candidate):
                return candidate
        except Exception:
            pass
        candidate = current_repo_root()
        if candidate is not None and not _looks_like_python_install_root(candidate):
            return candidate
        try:
            cwd = Path.cwd().resolve()
        except Exception:
            cwd = None
        if cwd is not None:
            for base in (cwd, *cwd.parents):
                if _looks_like_project_root(base):
                    return base
        return shared_dotenv_root

    def _sidecar_active_slot_repo_root(self) -> Path | None:
        manifest = active_slot_manifest()
        raw = str((manifest or {}).get("repo_dir") or "").strip()
        if raw:
            path = Path(raw).expanduser().resolve()
            if path.exists():
                return path
        current_slot = str(active_slot() or "").strip().upper() or None
        if not current_slot:
            return None
        slot_payload = core_slot_status().get("slots", {}).get(current_slot, {}) if isinstance(core_slot_status().get("slots"), dict) else {}
        structure = slot_payload.get("structure") if isinstance(slot_payload.get("structure"), dict) else {}
        raw = str(structure.get("repo_dir") or "").strip()
        if raw:
            path = Path(raw).expanduser().resolve()
            if path.exists():
                return path
        return None

    def _sidecar_controlled_relpaths(self) -> list[Path]:
        return [Path(rel_path) for rel_path in SIDECAR_CONTROLLED_PATHS]

    def _sidecar_validated_slot_source(self) -> dict[str, Any]:
        slot_repo = self._sidecar_active_slot_repo_root()
        status = read_core_update_status()
        state = str((status or {}).get("state") or "").strip().lower()
        phase = str((status or {}).get("phase") or "").strip().lower()
        current_slot = str(active_slot() or "").strip().upper() or None
        target_slot = str((status or {}).get("target_slot") or "").strip().upper() or None
        slot_validated = bool(
            current_slot
            and slot_repo is not None
            and (
                (state == "validated" and phase == "root_promotion_pending")
                or (state == "succeeded" and phase in {"validate", "root_promoted"})
            )
            and (not target_slot or target_slot == current_slot)
        )
        if slot_validated:
            return {
                "mode": "validated_active_slot_source",
                "reason": "active slot runtime validated; sidecar may sync controlled files from validated slot",
                "repo_root": slot_repo,
                "slot": current_slot,
            }
        return {
            "mode": "unavailable",
            "reason": "validated active slot source is unavailable",
            "repo_root": None,
            "slot": current_slot,
        }

    def _sidecar_tracked_paths(self) -> list[Path]:
        repo_root = self._sidecar_repo_root()
        if repo_root is None:
            return []
        candidates = [repo_root / rel_path for rel_path in self._sidecar_controlled_relpaths()]
        return [path.resolve() for path in candidates if path.exists()]

    def _sidecar_code_state(self) -> dict[str, Any]:
        repo_root = self._sidecar_repo_root()
        sync_source = self._sidecar_validated_slot_source()
        tracked_paths = self._sidecar_tracked_paths()
        digest = hashlib.sha256()
        # Version the digest contract so supervisors using the corrected
        # adoption semantics can distinguish processes labelled by the old
        # "current files == running files" assumption.
        digest.update(b"adaos-sidecar-code-fingerprint-v2\0")
        tracked_text: list[str] = []
        for path in tracked_paths:
            try:
                stat = path.stat()
                tracked_text.append(str(path))
                digest.update(str(path).encode("utf-8", errors="ignore"))
                digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
                digest.update(str(int(stat.st_size)).encode("ascii"))
            except Exception:
                continue
        fingerprint = digest.hexdigest() if tracked_text else None
        return {
            "mode": "root_project",
            "reason": "sidecar always executes from root project code",
            "repo_root": str(repo_root) if repo_root is not None else None,
            "launch_cwd": self._sidecar_launch_cwd,
            "fingerprint_contract": "v2",
            "fingerprint": fingerprint,
            "updated_at": time.time() if fingerprint else None,
            "tracked_paths": tracked_text,
            "sync_source_mode": str(sync_source.get("mode") or "").strip() or None,
            "sync_source_reason": str(sync_source.get("reason") or "").strip() or None,
            "sync_source_repo_root": str(sync_source.get("repo_root")) if isinstance(sync_source.get("repo_root"), Path) else None,
            "sync_source_slot": str(sync_source.get("slot") or "").strip() or None,
        }

    def _persisted_sidecar_code_for_listener(self, listener_pid: int) -> tuple[str | None, float | None]:
        """Return the code generation recorded for an inherited sidecar listener.

        A supervisor restart intentionally leaves the sidecar running.  The new
        supervisor must not label that inherited process with the fingerprint of
        files that were promoted while it was alive; doing so hides the pending
        rolling restart from the monitor.
        """

        persisted = _read_json(_supervisor_runtime_state_path())
        sidecar = persisted.get("sidecar") if isinstance(persisted, dict) else None
        if not isinstance(sidecar, dict):
            return None, None
        process = sidecar.get("process")
        code = sidecar.get("code")
        if not isinstance(process, dict) or not isinstance(code, dict):
            return None, None
        try:
            persisted_listener_pid = int(process.get("listener_pid") or 0)
        except (TypeError, ValueError):
            return None, None
        if persisted_listener_pid != int(listener_pid):
            return None, None
        fingerprint = str(code.get("active_fingerprint") or "").strip() or None
        if not fingerprint:
            return None, None
        try:
            updated_at = float(code.get("active_updated_at"))
        except (TypeError, ValueError):
            updated_at = None
        return fingerprint, updated_at

    def _sync_sidecar_controlled_files_from_validated_slot(self) -> dict[str, Any]:
        source = self._sidecar_validated_slot_source()
        source_root = source.get("repo_root")
        source_root = source_root if isinstance(source_root, Path) else None
        root_repo = self._sidecar_repo_root()
        if source_root is None or root_repo is None:
            return {"ok": True, "changed": False, "reason": str(source.get("reason") or "source_unavailable")}
        changed_paths: list[str] = []
        for rel_path in self._sidecar_controlled_relpaths():
            src = (source_root / rel_path).resolve()
            dst = (root_repo / rel_path).resolve()
            if not src.exists():
                continue
            try:
                src_bytes = src.read_bytes()
            except Exception:
                continue
            try:
                dst_bytes = dst.read_bytes() if dst.exists() else None
            except Exception:
                dst_bytes = None
            if dst_bytes == src_bytes:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src_bytes)
            changed_paths.append(str(rel_path).replace("\\", "/"))
        if changed_paths:
            self._sidecar_last_sync_at = time.time()
            self._sidecar_last_sync_source_slot = str(source.get("slot") or "").strip() or None
            self._sidecar_last_sync_reason = "validated_slot_sync"
            self._sidecar_last_sync_changed_paths = changed_paths
            return {
                "ok": True,
                "changed": True,
                "reason": "validated_slot_sync",
                "slot": self._sidecar_last_sync_source_slot,
                "changed_paths": changed_paths,
            }
        return {"ok": True, "changed": False, "reason": "up_to_date"}

    def _sidecar_restart_policy_state(self) -> dict[str, Any]:
        now = time.time()
        pending_code = bool(self._sidecar_code_change_pending_fingerprint)
        waiting_for_runtime = bool(
            pending_code
            and str(self._sidecar_last_restart_reason or "").startswith(
                "supervisor.sidecar.code_upgrade_waiting"
            )
        )
        return {
            "code_change_debounce_sec": _sidecar_code_change_debounce_sec(),
            "automatic_code_restart": True,
            "code_upgrade_policy": "controlled_restart_after_runtime_stable",
            "code_upgrade_state": (
                "waiting_for_runtime_stability"
                if waiting_for_runtime
                else ("pending_debounce" if pending_code else "current")
            ),
            "waiting_for_runtime_stability": waiting_for_runtime,
            "restart_window_sec": _sidecar_restart_window_sec(),
            "restart_limit": _sidecar_restart_limit(),
            "base_backoff_sec": _sidecar_restart_base_backoff_sec(),
            "max_backoff_sec": _sidecar_restart_max_backoff_sec(),
            "circuit_open_sec": _sidecar_restart_circuit_open_sec(),
            "pending_code_fingerprint": self._sidecar_code_change_pending_fingerprint,
            "pending_code_since": self._sidecar_code_change_pending_since,
            "pending_code_age_s": (
                max(0.0, now - float(self._sidecar_code_change_pending_since))
                if self._sidecar_code_change_pending_since
                else None
            ),
            "restart_backoff_until": self._sidecar_restart_backoff_until,
            "restart_backoff_remaining_s": (
                max(0.0, float(self._sidecar_restart_backoff_until) - now)
                if self._sidecar_restart_backoff_until and self._sidecar_restart_backoff_until > now
                else 0.0
            ),
            "circuit_open_until": self._sidecar_circuit_open_until,
            "circuit_open_remaining_s": (
                max(0.0, float(self._sidecar_circuit_open_until) - now)
                if self._sidecar_circuit_open_until and self._sidecar_circuit_open_until > now
                else 0.0
            ),
            "recent_restart_total": len(self._sidecar_restart_history),
            "last_restart_at": self._sidecar_last_restart_at,
        }

    def _record_sidecar_restart_attempt(self, *, reason: str) -> None:
        del reason
        now = time.time()
        window_sec = _sidecar_restart_window_sec()
        while self._sidecar_restart_history and now - self._sidecar_restart_history[0] > window_sec:
            self._sidecar_restart_history.popleft()
        self._sidecar_restart_history.append(now)
        self._sidecar_last_restart_at = now
        recent_total = len(self._sidecar_restart_history)
        if recent_total >= _sidecar_restart_limit():
            self._sidecar_circuit_open_until = now + _sidecar_restart_circuit_open_sec()
            self._sidecar_restart_backoff_until = self._sidecar_circuit_open_until
            return
        exponent = max(0, recent_total - 2)
        backoff_sec = min(_sidecar_restart_max_backoff_sec(), _sidecar_restart_base_backoff_sec() * (2 ** exponent))
        self._sidecar_restart_backoff_until = now + backoff_sec if recent_total > 1 else None
        self._sidecar_circuit_open_until = None

    def _sidecar_restart_allowed(self) -> tuple[bool, str | None]:
        now = time.time()
        if self._sidecar_circuit_open_until and self._sidecar_circuit_open_until > now:
            return False, "supervisor.sidecar.circuit_open"
        if self._sidecar_restart_backoff_until and self._sidecar_restart_backoff_until > now:
            return False, "supervisor.sidecar.backoff"
        return True, None

    async def _sidecar_code_upgrade_restart_allowed(self) -> tuple[bool, str | None]:
        if self._stopping or not self._desired_running:
            return False, "supervisor.sidecar.code_upgrade_waiting_runtime_stop"
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False, "supervisor.sidecar.code_upgrade_waiting_runtime_process"
        candidate = self._candidate_proc
        if candidate is not None and candidate.poll() is None:
            return False, "supervisor.sidecar.code_upgrade_waiting_candidate"
        if _is_transition_in_progress(read_core_update_status(), _read_update_attempt()):
            return False, "supervisor.sidecar.code_upgrade_waiting_transition"
        if self._last_start_at is not None and time.time() - float(self._last_start_at) < 5.0:
            return False, "supervisor.sidecar.code_upgrade_waiting_runtime_stability"
        ready = await asyncio.to_thread(
            _runtime_api_ready,
            self.runtime_base_url,
            token=self.token,
            timeout=0.75,
        )
        if not ready:
            return False, "supervisor.sidecar.code_upgrade_waiting_runtime_api"
        return True, None

    async def _probe_sidecar_health(self, *, force: bool = False) -> bool | None:
        snapshot = await asyncio.to_thread(
            realtime_sidecar_listener_snapshot,
            self._sidecar_proc,
            role=self._sidecar_role(),
        )
        if not bool(snapshot.get("listener_running")):
            self._sidecar_last_probe_at = time.time()
            self._sidecar_last_probe_ok = False
            self._sidecar_last_probe_error = "listener_not_running"
            self._sidecar_consecutive_probe_failures += 1
            return False
        if bool(snapshot.get("managed_alive")) and bool(snapshot.get("listener_matches_managed")):
            self._sidecar_last_probe_at = time.time()
            self._sidecar_last_probe_ok = True
            self._sidecar_last_probe_error = None
            self._sidecar_consecutive_probe_failures = 0
            return True
        now = time.time()
        if (
            not force
            and self._sidecar_last_probe_at is not None
            and now - self._sidecar_last_probe_at < 5.0
        ):
            return self._sidecar_last_probe_ok
        try:
            ready = await probe_realtime_sidecar_ready(
                host=str(snapshot.get("host") or "127.0.0.1"),
                port=int(snapshot.get("port") or 0),
                timeout_s=1.5,
            )
        except Exception as exc:
            ready = False
            self._sidecar_last_probe_error = f"{type(exc).__name__}: {exc}"
        else:
            self._sidecar_last_probe_error = None if ready else "probe_not_ready"
        self._sidecar_last_probe_at = now
        self._sidecar_last_probe_ok = bool(ready)
        if ready:
            self._sidecar_consecutive_probe_failures = 0
        else:
            self._sidecar_consecutive_probe_failures += 1
        return ready

    def _desired_memory_profile_mode(self) -> str:
        mode = str(self._memory_requested_profile_mode or "").strip().lower() or "normal"
        return mode if mode in {"normal", "sampled_profile", "trace_profile"} else "normal"

    def _managed_runtime_uptime_sec(self, *, now: float | None = None) -> float | None:
        if self._last_start_at is None:
            return None
        current_time = time.time() if now is None else float(now)
        return max(0.0, current_time - float(self._last_start_at))

    def _active_memory_profile_trigger_source(self) -> str:
        session_id = str(self._memory_active_session_id or "").strip()
        if not session_id:
            return ""
        summary = read_memory_session_summary(session_id) or {}
        return str(summary.get("trigger_source") or "").strip().lower()

    def _memory_session_index_items(self) -> list[dict[str, Any]]:
        index = read_memory_session_index()
        items = index.get("sessions") if isinstance(index.get("sessions"), list) else []
        return [dict(item) for item in items if isinstance(item, dict)]

    def _memory_session_telemetry_window(self, session: dict[str, Any], *, limit: int = 50) -> list[dict[str, Any]]:
        runtime_instance_id = str(session.get("runtime_instance_id") or "").strip() or None
        started_at = float(session.get("started_at") or session.get("requested_at") or 0.0)
        finished_at = float(session.get("finished_at") or time.time())
        items = read_memory_telemetry_tail(limit=5000)
        window: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sampled_at = float(item.get("sampled_at") or 0.0)
            if sampled_at and sampled_at < started_at:
                continue
            if finished_at and sampled_at and sampled_at > finished_at:
                continue
            if runtime_instance_id and str(item.get("runtime_instance_id") or "").strip() not in {"", runtime_instance_id}:
                continue
            window.append(item)
        return window[-max(1, int(limit or 1)) :]

    def _memory_scope_key(self, managed_pid: Any) -> str | None:
        runtime_instance_id = str(self._managed_runtime_instance_id or "").strip()
        if runtime_instance_id:
            return f"runtime:{runtime_instance_id}"
        try:
            pid = int(managed_pid or 0)
        except Exception:
            pid = 0
        return f"pid:{pid}" if pid > 0 else None

    def _reset_memory_baseline_scope(self, *, managed_pid: Any = None, now: float | None = None) -> None:
        try:
            pid = int(managed_pid or 0) or None
        except Exception:
            pid = None
        current_time = time.time() if now is None else float(now)
        self._memory_baseline_scope_key = self._memory_scope_key(pid)
        self._memory_baseline_pid = pid
        self._memory_baseline_family_rss_bytes = None
        self._memory_baseline_started_at = current_time
        self._memory_baseline_matured_at = None
        self._memory_baseline_phase = "warming"
        self._memory_baseline_last_adjusted_at = None
        self._memory_baseline_last_adjustment_reason = None
        self._memory_baseline_adjustment_total = 0
        self._memory_last_growth_bytes = 0
        self._memory_last_growth_bytes_per_min = 0.0
        self._memory_suspicion_state = "stable"
        self._memory_suspicion_reason = None
        self._memory_suspicion_since = None

    def _ensure_memory_baseline_scope(self, *, managed_pid: Any = None, now: float | None = None) -> None:
        scope_key = self._memory_scope_key(managed_pid)
        try:
            pid = int(managed_pid or 0) or None
        except Exception:
            pid = None
        if not scope_key:
            return
        previous_scope = str(self._memory_baseline_scope_key or "").strip() or None
        previous_pid = self._memory_baseline_pid
        if previous_scope is None:
            self._memory_baseline_scope_key = scope_key
            self._memory_baseline_pid = pid
            if self._memory_baseline_started_at is None:
                self._memory_baseline_started_at = time.time() if now is None else float(now)
            if self._memory_baseline_phase == "uninitialized":
                self._memory_baseline_phase = "warming"
            return
        if previous_scope != scope_key or (pid is not None and previous_pid is not None and previous_pid != pid):
            self._reset_memory_baseline_scope(managed_pid=pid, now=now)

    def _memory_profile_request_timeout_sec(self) -> float:
        try:
            timeout_sec = float(
                os.getenv("ADAOS_SUPERVISOR_MEMORY_PROFILE_REQUEST_TIMEOUT_S", "90") or "90"
            )
        except Exception:
            timeout_sec = 90.0
        return max(5.0, float(timeout_sec))

    def _capture_memory_profile_local_incident_artifact(
        self,
        session_id: str,
        *,
        reason: str,
        stage: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._memory_profiling.capture_local_incident_artifact(
            self,
            self._memory_operations(),
            session_id,
            reason=reason,
            stage=stage,
            details=details,
        )

    def _fail_active_memory_session(
        self,
        *,
        reason: str,
        exit_code: int | None = None,
        stage: str = "profile_runtime",
        details: dict[str, Any] | None = None,
    ) -> None:
        session_id = str(self._memory_active_session_id or "").strip()
        if not session_id:
            return
        summary = read_memory_session_summary(session_id)
        if not isinstance(summary, dict):
            return
        state = str(summary.get("session_state") or "").strip().lower()
        if state in {"finished", "stopped", "cancelled", "failed"}:
            return
        now = time.time()
        summary["session_state"] = "failed"
        summary["stop_reason"] = reason
        summary["stopped_at"] = now
        summary["finished_at"] = summary.get("finished_at") or now
        operation_window = summary.get("operation_window") if isinstance(summary.get("operation_window"), dict) else {}
        operation_window = dict(operation_window)
        operation_window["failure_reason"] = str(reason or "").strip() or "memory_profile_failure"
        operation_window["failure_stage"] = str(stage or "profile_runtime").strip() or "profile_runtime"
        operation_window["failure_at"] = now
        if details:
            operation_window["failure_details"] = dict(details)
        if exit_code is not None:
            operation_window["exit_code"] = int(exit_code)
        summary["operation_window"] = operation_window
        updated = self._upsert_memory_session_summary(summary)
        artifact_ref = None
        try:
            artifact_ref = self._capture_memory_profile_local_incident_artifact(
                session_id,
                reason=reason,
                stage=stage,
                details={
                    **(dict(details or {})),
                    "exit_code": int(exit_code) if exit_code is not None else None,
                },
            )
        except Exception:
            _LOG.warning(
                "failed to capture local memory incident artifact session_id=%s stage=%s",
                session_id,
                stage,
                exc_info=True,
            )
        self._append_memory_operation(
            session_id=session_id,
            event="tool_invoked",
            profile_mode=str(updated.get("profile_mode") or self._memory_profile_mode),
            details={
                "action": "profile_failed",
                "reason": reason,
                "stage": str(stage or "profile_runtime").strip() or "profile_runtime",
                "exit_code": int(exit_code) if exit_code is not None else None,
                "artifact_id": str((artifact_ref or {}).get("artifact_id") or "").strip() or None,
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
            },
        )
        self._memory_active_session_id = None
        self._memory_requested_profile_mode = None
        if session_id == str(self._memory_profile_finalizing_session_id or "").strip():
            self._memory_profile_finalizing_session_id = None
        if session_id == str(self._memory_publish_request_session_id or "").strip():
            self._memory_publish_request_session_id = None
        self._memory_profile_mode = "normal"
        self._persist_runtime_state()

    def _persist_memory_session_index_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "contract_version": "1",
            "sessions": items,
            "updated_at": time.time(),
        }
        return write_memory_session_index(payload)

    def _upsert_memory_session_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary = write_memory_session_summary(str(payload.get("session_id") or "session"), payload)
        items = self._memory_session_index_items()
        summary_item = dict(
            {
                "session_id": summary.get("session_id"),
                "slot": summary.get("slot"),
                "profile_mode": summary.get("profile_mode"),
                "session_state": summary.get("session_state"),
                "trigger_source": summary.get("trigger_source"),
                "trigger_reason": summary.get("trigger_reason"),
                "requested_at": summary.get("requested_at"),
                "started_at": summary.get("started_at"),
                "finished_at": summary.get("finished_at"),
                "suspected_leak": bool(summary.get("suspected_leak")),
                "retry_of_session_id": summary.get("retry_of_session_id"),
                "retry_depth": int(summary.get("retry_depth") or 0),
                "published_to_root": bool(summary.get("published_to_root")),
                "publish_state": summary.get("publish_state"),
                "published_ref": summary.get("published_ref"),
            }
        )
        replaced = False
        for index, item in enumerate(items):
            if str(item.get("session_id") or "").strip() == str(summary.get("session_id") or "").strip():
                items[index] = summary_item
                replaced = True
                break
        if not replaced:
            items.append(summary_item)
        self._persist_memory_session_index_items(items)
        self._memory_last_session_id = str(summary.get("session_id") or "").strip() or self._memory_last_session_id
        return summary

    def _append_memory_operation(
        self,
        *,
        session_id: str,
        event: str,
        profile_mode: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operations = read_memory_session_operations(session_id, limit=5000)
        payload = {
            "event_id": f"op-{uuid.uuid4().hex[:10]}",
            "event": event,
            "emitted_at": time.time(),
            "contract_version": MEMORY_OPERATION_CONTRACT_VERSION,
            "session_id": session_id,
            "profile_mode": profile_mode or self._memory_profile_mode,
            "slot": str(active_slot() or "").strip().upper() or None,
            "runtime_instance_id": self._managed_runtime_instance_id,
            "transition_role": self._managed_transition_role,
            "sample_source": "supervisor",
            "sequence": len(operations) + 1,
            "details": dict(details or {}),
        }
        return append_memory_session_operation(session_id, payload)

    def _request_memory_profile_session(
        self,
        *,
        profile_mode: str,
        reason: str,
        trigger_source: str,
        trigger_threshold: str | None = None,
    ) -> dict[str, Any]:
        requested_mode = str(profile_mode or "").strip().lower() or "sampled_profile"
        if requested_mode not in {"sampled_profile", "trace_profile"}:
            raise HTTPException(status_code=400, detail="unsupported profile_mode")
        if _is_transition_in_progress(read_core_update_status(), _read_update_attempt()):
            raise HTTPException(status_code=409, detail="memory profiling intent is blocked during active transition")
        active_session_id = str(self._memory_active_session_id or "").strip()
        if active_session_id:
            active_session = read_memory_session_summary(active_session_id) or {}
            active_state = str(active_session.get("session_state") or "").strip().lower()
            if active_state in {"planned", "requested", "running"}:
                raise HTTPException(status_code=409, detail="a memory profiling session is already active")
        finalizing_session_id = str(self._memory_profile_finalizing_session_id or "").strip()
        if finalizing_session_id:
            finalizing_session = read_memory_session_summary(finalizing_session_id) or {}
            finalizing_state = str(finalizing_session.get("session_state") or "").strip().lower()
            if finalizing_state not in {"finished", "failed", "cancelled"}:
                raise HTTPException(status_code=409, detail="a memory profiling session is finalizing")
            self._memory_profile_finalizing_session_id = None
        session_id = f"mem-{uuid.uuid4().hex[:8]}"
        now = time.time()
        summary = self._upsert_memory_session_summary(
            {
                "session_id": session_id,
                "slot": str(active_slot() or "").strip().upper() or None,
                "runtime_instance_id": self._managed_runtime_instance_id,
                "transition_role": self._managed_transition_role,
                "profile_mode": requested_mode,
                "session_state": "requested",
                "trigger_source": trigger_source,
                "trigger_reason": str(reason or "supervisor.memory.request"),
                "trigger_threshold": str(trigger_threshold or "").strip() or None,
                "baseline_rss_bytes": self._memory_baseline_family_rss_bytes,
                "peak_rss_bytes": None,
                "rss_growth_bytes": self._memory_last_growth_bytes,
                "requested_at": now,
                "started_at": None,
                "finished_at": None,
                "publish_state": "local_only",
                "suspected_leak": trigger_source == "policy",
                "retry_of_session_id": None,
                "retry_root_session_id": None,
                "retry_depth": 0,
                "operation_window": {
                    "contract_version": MEMORY_OPERATION_CONTRACT_VERSION,
                    "events_path": str(supervisor_memory_session_operations_path(session_id)),
                },
            }
        )
        self._memory_active_session_id = session_id
        self._memory_last_session_id = session_id
        self._memory_requested_profile_mode = requested_mode
        self._append_memory_operation(
            session_id=session_id,
            event="tool_invoked",
            profile_mode=requested_mode,
            details={
                "action": "profile_start",
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
                "reason": str(reason or "supervisor.memory.request"),
                "trigger_source": trigger_source,
                "trigger_threshold": str(trigger_threshold or "").strip() or None,
                "note": "Supervisor will apply requested profile mode via controlled runtime restart",
            },
        )
        self._persist_runtime_state()
        return summary

    def _mark_active_memory_session_running(self, *, runtime_instance_id: str | None, transition_role: str) -> None:
        session_id = str(self._memory_active_session_id or "").strip()
        if not session_id:
            return
        summary = read_memory_session_summary(session_id)
        if not isinstance(summary, dict):
            return
        now = time.time()
        summary["slot"] = str(active_slot() or "").strip().upper() or summary.get("slot")
        summary["runtime_instance_id"] = runtime_instance_id
        summary["transition_role"] = transition_role
        summary["session_state"] = "running"
        summary["started_at"] = summary.get("started_at") or now
        summary["baseline_rss_bytes"] = summary.get("baseline_rss_bytes") or self._memory_baseline_family_rss_bytes
        summary["rss_growth_bytes"] = self._memory_last_growth_bytes
        updated = self._upsert_memory_session_summary(summary)
        self._append_memory_operation(
            session_id=session_id,
            event="slot_started",
            profile_mode=str(updated.get("profile_mode") or self._memory_profile_mode),
            details={
                "action": "profile_mode_applied",
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
                "runtime_instance_id": runtime_instance_id,
                "transition_role": transition_role,
            },
        )

    def _update_memory_session_peak(self, family_rss_bytes: int | None) -> None:
        session_id = str(self._memory_active_session_id or "").strip()
        if not session_id or family_rss_bytes is None:
            return
        summary = read_memory_session_summary(session_id)
        if not isinstance(summary, dict):
            return
        peak = summary.get("peak_rss_bytes")
        if peak is None or int(family_rss_bytes) > int(peak):
            summary["peak_rss_bytes"] = int(family_rss_bytes)
        summary["rss_growth_bytes"] = self._memory_last_growth_bytes
        self._upsert_memory_session_summary(summary)

    def _memory_policy_auto_profile_guard(self, *, now: float) -> tuple[bool, str | None]:
        min_uptime_sec = _memory_auto_profile_min_uptime_sec()
        if min_uptime_sec > 0:
            uptime_sec = self._managed_runtime_uptime_sec(now=now)
            if uptime_sec is None or uptime_sec < min_uptime_sec:
                observed = "unknown" if uptime_sec is None else f"{uptime_sec:.1f}s"
                return False, f"auto_profile_min_uptime:{observed}<{min_uptime_sec:.1f}s"
        cooldown_cutoff = now - _memory_auto_profile_cooldown_sec()
        circuit_cutoff = now - _memory_auto_profile_circuit_window_sec()
        recent_policy_sessions = 0
        for item in reversed(self._memory_session_index_items()):
            if not isinstance(item, dict):
                continue
            if str(item.get("trigger_source") or "").strip().lower() != "policy":
                continue
            requested_at = float(item.get("requested_at") or 0.0)
            if requested_at >= cooldown_cutoff:
                return False, "auto_profile_cooldown"
            if requested_at >= circuit_cutoff:
                recent_policy_sessions += 1
                if recent_policy_sessions >= _memory_auto_profile_circuit_limit():
                    return False, "auto_profile_circuit_open"
        return self._memory_profile_subnet_guard()

    def _memory_live_subnet_state(
        self,
        *,
        runtime: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        browser_total = 0
        browser_latest_age: float | None = None
        now = time.time()
        try:
            from adaos.services.access_links import browser_snapshot

            ttl_sec = _memory_auto_profile_browser_live_ttl_sec()
            for entry in browser_snapshot():
                if not isinstance(entry, dict):
                    continue
                last_seen_at = float(entry.get("last_seen_at") or 0.0)
                if last_seen_at <= 0.0:
                    continue
                age = max(0.0, now - last_seen_at)
                state = str(entry.get("connection_state") or "").strip().lower()
                online = bool(entry.get("online"))
                if age <= ttl_sec and (online or state in {"connected", "open", "ready"}):
                    browser_total += 1
                    browser_latest_age = age if browser_latest_age is None else min(browser_latest_age, age)
        except Exception:
            browser_total = 0
            browser_latest_age = None
        runtime_payload = runtime if isinstance(runtime, dict) else self._runtime_reliability_payload(timeout=1.0)
        if browser_total > 0 and not _memory_auto_profile_allow_browser_sessions():
            age_text = "-" if browser_latest_age is None else f"{browser_latest_age:.1f}s"
            return True, f"browser_sessions_connected:{browser_total}:last_seen={age_text}"
        if not runtime_payload:
            # Policy-triggered profiling is optional diagnostics.  A busy Hub can
            # miss the bounded reliability preflight precisely while it still
            # owns browser/member sessions, so treating an unavailable snapshot
            # as "no live subnet" can turn transient event-loop lag into a full
            # runtime restart.  Fail closed for network roles; explicit operator
            # profiling still bypasses this policy-only guard.
            role = str(self._sidecar_role() or "").strip().lower()
            if role in {"hub", "member"}:
                return True, f"{role}_runtime_reliability_unavailable"
            return False, None
        node = runtime_payload.get("node") if isinstance(runtime_payload.get("node"), dict) else {}
        role = str(node.get("role") or self._managed_transition_role or self._sidecar_role() or "").strip().lower()
        if role == "hub":
            member_state = (
                runtime_payload.get("hub_member_connection_state")
                if isinstance(runtime_payload.get("hub_member_connection_state"), dict)
                else {}
            )
            connected_total = int(member_state.get("connected_total") or 0)
            if connected_total > 0:
                return True, f"subnet_members_connected:{connected_total}"
            return False, None
        if role == "member":
            member_state = (
                runtime_payload.get("hub_member_connection_state")
                if isinstance(runtime_payload.get("hub_member_connection_state"), dict)
                else {}
            )
            hub_state = member_state.get("hub") if isinstance(member_state.get("hub"), dict) else {}
            connected = bool(node.get("connected_to_subnet")) or bool(node.get("connected_to_hub")) or bool(hub_state.get("connected"))
            if connected:
                return True, "member_hub_connected"
        return False, None

    def _memory_profile_subnet_guard(
        self,
        *,
        runtime: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        subnet_live, subnet_reason = self._memory_live_subnet_state(runtime=runtime)
        if subnet_live:
            return False, subnet_reason
        return True, None

    def _memory_profile_restart_guard(self, *, desired_mode: str, now: float | None = None) -> tuple[bool, str | None]:
        normalized_mode = str(desired_mode or "").strip().lower()
        if normalized_mode == "normal":
            if (
                self._memory_profile_mode != "normal"
                and str(self._memory_profile_current_trigger_source or "").strip().lower() == "policy"
                and not _memory_policy_profile_restarts_enabled()
            ):
                return False, "policy_profile_restart_disabled"
            if self._memory_profile_mode != "normal" and str(self._memory_profile_current_trigger_source or "").strip().lower() == "policy":
                return self._memory_profile_subnet_guard()
            return True, None
        if self._active_memory_profile_trigger_source() != "policy":
            return True, None
        if not _memory_policy_profile_restarts_enabled():
            return False, "policy_profile_restart_disabled"
        min_uptime_sec = _memory_auto_profile_min_uptime_sec()
        if min_uptime_sec <= 0:
            return self._memory_profile_subnet_guard()
        uptime_sec = self._managed_runtime_uptime_sec(now=now)
        if uptime_sec is None or uptime_sec < min_uptime_sec:
            observed = "unknown" if uptime_sec is None else f"{uptime_sec:.1f}s"
            return False, f"auto_profile_min_uptime:{observed}<{min_uptime_sec:.1f}s"
        return self._memory_profile_subnet_guard()

    def _reset_memory_critical_episode(self) -> None:
        self._memory_critical_since = None
        self._memory_critical_reason = None
        self._memory_critical_pressure_owner = None
        self._memory_critical_action = None
        self._memory_critical_attribution = {}

    def _memory_critical_pressure_decision(self, *, now: float | None = None) -> dict[str, Any] | None:
        if self._stopping or not self._desired_running:
            self._reset_memory_critical_episode()
            return None
        if self._proc is None or self._proc.poll() is not None:
            self._reset_memory_critical_episode()
            return None
        if _is_transition_in_progress(read_core_update_status(), _read_update_attempt()):
            self._reset_memory_critical_episode()
            return None
        available_bytes = self._memory_last_available_bytes
        total_bytes = _total_memory_bytes()
        if available_bytes is None or total_bytes is None or total_bytes <= 0:
            self._reset_memory_critical_episode()
            return None
        available_percent = (float(available_bytes) / float(total_bytes)) * 100.0
        self._memory_last_available_percent = available_percent
        threshold_percent = _memory_critical_available_percent_threshold()
        threshold_bytes = _memory_critical_available_bytes_threshold()
        reasons: list[str] = []
        if available_percent <= threshold_percent:
            reasons.append(f"available_percent<={threshold_percent:.1f}")
        if int(available_bytes) <= int(threshold_bytes):
            reasons.append(f"available_bytes<={int(threshold_bytes)}")
        if not reasons:
            self._reset_memory_critical_episode()
            return None
        current_time = time.time() if now is None else float(now)
        reason = ",".join(reasons)
        if self._memory_critical_reason != reason:
            self._memory_critical_reason = reason
            self._memory_critical_since = current_time
            return None
        critical_since = float(self._memory_critical_since or current_time)
        duration_sec = _memory_critical_duration_sec()
        if (current_time - critical_since) < duration_sec:
            return None
        managed = _proc_details(self._proc, cwd_hint=self._managed_runtime_cwd)
        managed_pid = _positive_int_or_none(managed.get("managed_pid"))
        process_rss_bytes, family_rss_bytes = _process_family_rss_bytes(managed_pid)
        family_rss_bytes = _positive_int_or_none(family_rss_bytes)
        if family_rss_bytes is None:
            telemetry_tail = read_memory_telemetry_tail(limit=1)
            latest_telemetry = telemetry_tail[-1] if telemetry_tail else {}
            if str(latest_telemetry.get("runtime_instance_id") or "") == str(
                self._managed_runtime_instance_id or ""
            ):
                family_rss_bytes = _positive_int_or_none(latest_telemetry.get("family_rss_bytes"))
        baseline_rss_bytes = _positive_int_or_none(self._memory_baseline_family_rss_bytes)
        growth_bytes = max(
            0,
            int(
                self._memory_last_growth_bytes
                if self._memory_last_growth_bytes is not None
                else (
                    max(0, int(family_rss_bytes) - int(baseline_rss_bytes))
                    if family_rss_bytes is not None and baseline_rss_bytes is not None
                    else 0
                )
            ),
        )
        configured_family_threshold = _memory_suspicion_family_rss_threshold_bytes()
        dynamic_family_threshold = max(256 * 1024 * 1024, int(float(total_bytes) * 0.20))
        effective_family_threshold = (
            min(int(configured_family_threshold), dynamic_family_threshold)
            if configured_family_threshold is not None
            else None
        )
        growth_threshold = _memory_suspicion_growth_threshold_bytes()
        system_process_snapshot = _system_process_memory_snapshot(managed_pid)
        skill_runtime_totals = (
            system_process_snapshot.get("skill_runtime_totals", [])
            if isinstance(system_process_snapshot.get("skill_runtime_totals"), list)
            else []
        )
        skill_rss_bytes = sum(
            int(item.get("rss_bytes") or 0)
            for item in skill_runtime_totals
            if isinstance(item, dict)
        )
        skill_target = next(
            (item for item in skill_runtime_totals if isinstance(item, dict) and item.get("skill_runtime")),
            None,
        )
        skill_indicators: list[str] = []
        if (
            skill_target is not None
            and effective_family_threshold is not None
            and int(skill_target.get("rss_bytes") or 0) >= int(effective_family_threshold)
        ):
            skill_indicators.append("skill_rss_threshold")
        elif (
            skill_target is not None
            and effective_family_threshold is not None
            and skill_rss_bytes >= int(effective_family_threshold)
        ):
            skill_indicators.append("combined_skill_rss_threshold")
        runtime_indicators: list[str] = []
        if (
            family_rss_bytes is not None
            and effective_family_threshold is not None
            and int(family_rss_bytes) >= int(effective_family_threshold)
        ):
            runtime_indicators.append("family_rss_threshold")
        if growth_bytes >= int(growth_threshold):
            runtime_indicators.append("growth_threshold")
        if skill_indicators:
            pressure_owner = "skill_runtime"
            action = "quarantine_skill_runtime"
        elif runtime_indicators:
            pressure_owner = "runtime_family"
            action = "restart_runtime"
        else:
            pressure_owner = "external_or_system"
            action = "observe_external_pressure"
        attribution = {
            "managed_pid": managed_pid,
            "process_rss_bytes": process_rss_bytes,
            "family_rss_bytes": family_rss_bytes,
            "baseline_family_rss_bytes": baseline_rss_bytes,
            "growth_bytes": growth_bytes,
            "effective_family_rss_threshold_bytes": effective_family_threshold,
            "growth_threshold_bytes": int(growth_threshold),
            "runtime_indicators": runtime_indicators,
            "skill_rss_bytes": skill_rss_bytes,
            "skill_indicators": skill_indicators,
            "skill_target": skill_target,
        }
        self._memory_critical_pressure_owner = pressure_owner
        self._memory_critical_action = action
        self._memory_critical_attribution = attribution
        cooldown_sec = _memory_critical_restart_cooldown_sec()
        if action == "restart_runtime":
            last_action_at = float(self._memory_critical_restart_last_at or 0.0)
            if last_action_at > 0.0 and (current_time - last_action_at) < cooldown_sec:
                return None
        else:
            last_action_at = float(self._memory_critical_observation_last_at or 0.0)
        if action == "quarantine_skill_runtime":
            if last_action_at >= critical_since and (current_time - last_action_at) < cooldown_sec:
                return None
        elif last_action_at >= critical_since:
            return None
        subnet_live, subnet_reason = self._memory_live_subnet_state()
        if action == "restart_runtime":
            message = (
                "runtime restart requested because free memory stayed below the critical threshold"
                f" for {duration_sec:.0f}s and runtime-family memory was anomalous"
                f" (family={family_rss_bytes}B, available={int(available_bytes)}B)"
            )
            decision_reason = "supervisor.memory.critical_pressure"
        elif action == "quarantine_skill_runtime":
            skill_name = str((skill_target or {}).get("skill_runtime") or "").strip()
            skill_bytes = int((skill_target or {}).get("rss_bytes") or 0)
            message = (
                "skill runtime quarantine requested because host memory stayed critical and the skill"
                f" was the largest attributed AdaOS workload (skill={skill_name}, rss={skill_bytes}B,"
                f" available={int(available_bytes)}B)"
            )
            decision_reason = "supervisor.memory.skill_pressure"
        else:
            message = (
                "critical system memory pressure observed, but runtime restart was suppressed because"
                f" runtime-family memory was not anomalous (family={family_rss_bytes}B,"
                f" available={int(available_bytes)}B)"
            )
            decision_reason = "supervisor.memory.external_pressure"
        return {
            "reason": decision_reason,
            "message": message,
            "action": action,
            "pressure_owner": pressure_owner,
            "recorded_at": current_time,
            "available_memory_bytes": int(available_bytes),
            "available_memory_percent": round(available_percent, 3),
            "total_memory_bytes": int(total_bytes),
            "threshold_percent": float(threshold_percent),
            "threshold_bytes": int(threshold_bytes),
            "critical_for_sec": max(0.0, current_time - critical_since),
            "restart_cooldown_sec": float(cooldown_sec),
            "critical_reason": reason,
            "attribution": attribution,
            "system_process_snapshot": system_process_snapshot,
            "subnet_live": bool(subnet_live),
            "subnet_reason": subnet_reason,
        }

    def _memory_critical_restart_decision(self, *, now: float | None = None) -> dict[str, Any] | None:
        decision = self._memory_critical_pressure_decision(now=now)
        if decision is None or decision.get("action") != "restart_runtime":
            return None
        return decision

    async def _quarantine_skill_memory_pressure(self, decision: dict[str, Any]) -> dict[str, Any]:
        attribution = decision.get("attribution") if isinstance(decision.get("attribution"), dict) else {}
        target = attribution.get("skill_target") if isinstance(attribution.get("skill_target"), dict) else {}
        skill_name = str(target.get("skill_runtime") or "").strip()
        if not skill_name:
            return {"ok": False, "error": "skill_runtime_target_missing"}
        payload = {
            "reason": str(decision.get("reason") or "supervisor.memory.skill_pressure"),
            "cooloff_s": float(decision.get("restart_cooldown_sec") or 120.0),
            "pressure": {
                "available_memory_bytes": decision.get("available_memory_bytes"),
                "available_memory_percent": decision.get("available_memory_percent"),
                "critical_for_sec": decision.get("critical_for_sec"),
                "skill_rss_bytes": target.get("rss_bytes"),
                "skill_process_total": target.get("process_total"),
                "observed_pids": target.get("pids") if isinstance(target.get("pids"), list) else [],
                "indicators": attribution.get("skill_indicators"),
            },
        }
        try:
            return await asyncio.to_thread(
                self._runtime_request_json,
                path=f"/api/services/{quote(skill_name, safe='')}/resource-pressure",
                method="POST",
                payload=payload,
                timeout=15.0,
            )
        except Exception as exc:
            return {
                "ok": False,
                "skill": skill_name,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _record_memory_auto_profile_block(self, reason: str | None, *, now: float | None = None) -> None:
        reason_text = str(reason or "").strip()
        if not reason_text:
            return
        self._memory_auto_profile_last_block_reason = reason_text
        self._memory_auto_profile_last_block_at = time.time() if now is None else float(now)

    def _should_finalize_active_memory_profile(self, *, now: float | None = None) -> dict[str, Any] | None:
        session_id = str(self._memory_active_session_id or "").strip()
        if not session_id:
            return None
        profile_mode = str(self._memory_profile_mode or "").strip().lower()
        max_runtime_sec = _memory_profile_max_runtime_sec(profile_mode)
        if profile_mode == "normal" or max_runtime_sec <= 0:
            return None
        summary = read_memory_session_summary(session_id)
        if not isinstance(summary, dict):
            return None
        state = str(summary.get("session_state") or "").strip().lower()
        if state not in {"running", "requested"}:
            return None
        started_at = float(summary.get("started_at") or summary.get("requested_at") or 0.0)
        if started_at <= 0:
            return None
        current_time = time.time() if now is None else float(now)
        window_started_at = float(summary.get("profile_window_started_at") or 0.0)
        if window_started_at <= 0.0:
            runtime_snapshot = self.status()
            if not bool(runtime_snapshot.get("runtime_api_ready")):
                return None
            window_started_at = current_time
            summary["profile_window_started_at"] = window_started_at
            summary["profile_window_started_reason"] = "runtime_api_ready"
            updated_window = summary.get("operation_window") if isinstance(summary.get("operation_window"), dict) else {}
            updated_window = dict(updated_window)
            updated_window["profile_window_started_at"] = window_started_at
            updated_window["profile_window_started_reason"] = "runtime_api_ready"
            summary["operation_window"] = updated_window
            self._upsert_memory_session_summary(summary)
            self._append_memory_operation(
                session_id=session_id,
                event="slot_started",
                profile_mode=profile_mode,
                details={
                    "action": "profile_window_started",
                    "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
                    "reason": "runtime_api_ready",
                    "runtime_instance_id": self._managed_runtime_instance_id,
                    "transition_role": self._managed_transition_role,
                },
            )
            return None
        elapsed_sec = max(0.0, current_time - window_started_at)
        if elapsed_sec < max_runtime_sec:
            return None
        return {
            "session_id": session_id,
            "profile_mode": profile_mode,
            "trigger_source": str(summary.get("trigger_source") or "").strip().lower() or None,
            "elapsed_sec": elapsed_sec,
            "max_runtime_sec": max_runtime_sec,
            "reason": f"supervisor.memory.profile_window_complete.{profile_mode}",
        }

    def _expire_stuck_requested_memory_profile(self, *, now: float | None = None) -> dict[str, Any] | None:
        session_id = str(self._memory_active_session_id or "").strip()
        if not session_id:
            return None
        if str(self._memory_profile_mode or "").strip().lower() != "normal":
            return None
        desired_mode = self._desired_memory_profile_mode()
        if desired_mode == "normal":
            return None
        summary = read_memory_session_summary(session_id)
        if not isinstance(summary, dict):
            return None
        session_state = str(summary.get("session_state") or "").strip().lower()
        if session_state != "requested":
            return None
        current_time = time.time() if now is None else float(now)
        requested_at = float(summary.get("requested_at") or 0.0)
        if requested_at <= 0.0:
            return None
        timeout_sec = self._memory_profile_request_timeout_sec()
        elapsed_sec = max(0.0, current_time - requested_at)
        if elapsed_sec < timeout_sec:
            return None
        details = {
            "desired_profile_mode": desired_mode,
            "current_profile_mode": str(self._memory_profile_mode or "").strip().lower() or "normal",
            "requested_elapsed_sec": round(elapsed_sec, 3),
            "request_timeout_sec": timeout_sec,
            "active_slot": str(active_slot() or "").strip().upper() or None,
            "runtime_instance_id": self._managed_runtime_instance_id,
            "transition_role": self._managed_transition_role,
            "last_block_reason": self._memory_auto_profile_last_block_reason,
            "last_block_at": self._memory_auto_profile_last_block_at,
        }
        self._fail_active_memory_session(
            reason=f"requested_profile_mode_timeout.{desired_mode}",
            stage="profile_apply_timeout",
            details=details,
        )
        return {
            "session_id": session_id,
            "desired_mode": desired_mode,
            "elapsed_sec": elapsed_sec,
            "timeout_sec": timeout_sec,
        }

    async def _maybe_apply_memory_profile_mode(self) -> None:
        desired_mode = self._desired_memory_profile_mode()
        if desired_mode == self._memory_profile_mode:
            return
        if self._stopping or not self._desired_running:
            return
        if self._proc is None or self._proc.poll() is not None:
            return
        if _is_transition_in_progress(read_core_update_status(), _read_update_attempt()):
            return
        allowed, block_reason = self._memory_profile_restart_guard(desired_mode=desired_mode)
        if not allowed:
            self._record_memory_auto_profile_block(block_reason)
            self._persist_runtime_state()
            return
        await self.restart_runtime(reason=f"supervisor.memory.apply_profile_mode.{desired_mode}")

    def _sample_memory_telemetry(self) -> dict[str, Any] | None:
        return self._memory_profiling.sample_telemetry(
            self,
            self._memory_operations(),
        )

    def _memory_profile_finalize_observed(self, session_id: str | None) -> bool:
        token = str(session_id or "").strip()
        if not token:
            return False
        summary = read_memory_session_summary(token) or {}
        artifact_refs = summary.get("artifact_refs") if isinstance(summary.get("artifact_refs"), list) else []
        artifact_kinds = {str(item.get("kind") or "").strip() for item in artifact_refs if isinstance(item, dict)}
        if "runtime_profile_finalize_debug" in artifact_kinds:
            return True
        if "tracemalloc_final_snapshot" in artifact_kinds or "tracemalloc_top_growth" in artifact_kinds:
            return True
        artifacts_dir = supervisor_memory_session_artifacts_dir(token)
        for name in (
            "runtime-admin-shutdown-debug.json",
            "runtime-profile-finalize-debug.json",
            "tracemalloc-final.json",
            "tracemalloc-top-growth.json",
        ):
            if (artifacts_dir / name).exists():
                return True
        return False

    def _record_memory_profile_finalize_missing(
        self,
        session_id: str | None,
        *,
        shutdown_status_code: int | None,
        shutdown_error: str | None,
        reason: str,
    ) -> None:
        token = str(session_id or "").strip()
        if not token or self._memory_profile_finalize_observed(token):
            return
        summary = read_memory_session_summary(token)
        if not isinstance(summary, dict):
            return
        now = time.time()
        operation_window = summary.get("operation_window") if isinstance(summary.get("operation_window"), dict) else {}
        operation_window = dict(operation_window)
        operation_window["failure_reason"] = "profile_finalize_marker_missing"
        operation_window["failure_stage"] = "profile_finalize_timeout"
        operation_window["finalize_marker_missing_at"] = now
        operation_window["shutdown_status_code"] = shutdown_status_code
        operation_window["shutdown_error"] = shutdown_error
        summary["session_state"] = "failed"
        summary["failure_reason"] = "profile_finalize_marker_missing"
        summary["failure_stage"] = "profile_finalize_timeout"
        summary["stop_reason"] = reason
        summary["stopped_at"] = summary.get("stopped_at") or now
        summary["finished_at"] = summary.get("finished_at") or now
        summary["operation_window"] = operation_window
        updated = self._upsert_memory_session_summary(summary)
        artifact_ref = None
        try:
            artifact_ref = self._capture_memory_profile_local_incident_artifact(
                token,
                reason="profile_finalize_marker_missing",
                stage="profile_finalize_timeout",
                details={
                    "shutdown_status_code": shutdown_status_code,
                    "shutdown_error": shutdown_error,
                    "stop_reason": reason,
                },
            )
        except Exception:
            _LOG.warning(
                "failed to capture missing finalize marker incident artifact session_id=%s",
                token,
                exc_info=True,
            )
        self._append_memory_operation(
            session_id=token,
            event="tool_invoked",
            profile_mode=str(updated.get("profile_mode") or self._memory_profile_mode),
            details={
                "action": "profile_finalize_missing",
                "reason": "profile_finalize_marker_missing",
                "stage": "profile_finalize_timeout",
                "shutdown_status_code": shutdown_status_code,
                "shutdown_error": shutdown_error,
                "artifact_id": str((artifact_ref or {}).get("artifact_id") or "").strip() or None,
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
            },
        )
        self._persist_runtime_state()

    def _schedule_service_restart(
        self,
        *,
        reason: str,
        candidate_wrapper_refresh: dict[str, Any] | None = None,
        defer_wrapper_refresh: bool = False,
    ) -> dict[str, Any]:
        delay_sec = _root_restart_delay_sec()
        if not _autostart_self_restart_supported():
            return {
                "ok": True,
                "requested": False,
                "mode": "manual",
                "delay_sec": None,
                "reason": "autostart self-restart is unavailable for the current supervisor process",
            }
        candidate_refresh_ready = isinstance(candidate_wrapper_refresh, dict) and bool(
            candidate_wrapper_refresh.get("ok")
        )
        if self._service_restart_pending:
            return {
                "ok": True,
                "requested": True,
                "mode": "self_exit",
                "delay_sec": delay_sec,
                "duplicate": True,
                "wrapper_refresh": (
                    dict(candidate_wrapper_refresh)
                    if candidate_refresh_ready
                    else {
                        "ok": True,
                        "scheduled": True,
                        "reason": str(reason or "supervisor.service.restart"),
                    }
                ),
            }
        wrapper_refresh = (
            dict(candidate_wrapper_refresh)
            if candidate_refresh_ready
            else (
                {
                    "ok": True,
                    "scheduled": True,
                    "reason": str(reason or "supervisor.service.restart"),
                }
                if defer_wrapper_refresh
                else self._refresh_autostart_wrapper(reason=reason)
            )
        )
        self._service_restart_pending = True
        pid = os.getpid()
        restart_reason = str(reason or "supervisor.update.complete")

        def _worker() -> None:
            try:
                if defer_wrapper_refresh and not candidate_refresh_ready:
                    deferred_refresh = self._refresh_autostart_wrapper(reason=restart_reason)
                    if not bool(deferred_refresh.get("ok")):
                        raise RuntimeError(
                            str(
                                deferred_refresh.get("error")
                                or deferred_refresh.get("reason")
                                or "wrapper refresh failed"
                            )
                        )
                time.sleep(delay_sec)
                _LOG.info(
                    "requesting autostart service self-restart pid=%s delay_sec=%.3f reason=%s",
                    pid,
                    delay_sec,
                    restart_reason,
                )
                os.kill(pid, signal.SIGTERM)
            except Exception:
                self._service_restart_pending = False
                _LOG.warning("failed to request autostart service self-restart", exc_info=True)

        thread = threading.Thread(target=_worker, name="adaos-supervisor-self-restart", daemon=True)
        self._service_restart_thread = thread
        thread.start()
        return {
            "ok": True,
            "requested": True,
            "mode": "self_exit",
            "delay_sec": delay_sec,
            "wrapper_refresh": wrapper_refresh,
        }

    def restart_service(self, *, reason: str) -> dict[str, Any]:
        if self._update_task is not None and not self._update_task.done():
            return {
                "ok": False,
                "accepted": False,
                "reason": "core_update_active",
                "message": "supervisor service restart is blocked while a core update is active",
            }
        restart = self._schedule_service_restart(reason=reason, defer_wrapper_refresh=True)
        requested = bool(restart.get("requested"))
        return {
            "ok": bool(restart.get("ok")) and requested,
            "accepted": requested,
            "reason": str(reason or "supervisor.service.restart"),
            "supervisor_pid": os.getpid(),
            "managed_runtime_pid": getattr(self._proc, "pid", None),
            "managed_sidecar_pid": getattr(self._sidecar_proc, "pid", None),
            "restart": restart,
        }

    def _schedule_managed_handoff_reaper(self) -> dict[str, Any]:
        pids = sorted(
            {
                int(pid)
                for pid in (
                    getattr(self._proc, "pid", None),
                    getattr(self._sidecar_proc, "pid", None),
                )
                if isinstance(pid, int) and pid > 0
            }
        )
        if not pids:
            return {"ok": True, "scheduled": False, "reason": "no_managed_children"}
        service_name = str(os.getenv("ADAOS_AUTOSTART_SERVICE") or "adaos.service").strip() or "adaos.service"
        supervisor_port = _supervisor_port()
        lines = [
            "import os, signal, socket, subprocess, time",
            f"pids = {pids!r}",
            f"service_name = {service_name!r}",
            f"supervisor_port = {supervisor_port!r}",
            "time.sleep(4.0)",
        ]
        if os.name == "nt":
            lines.extend(
                [
                    "for _ in range(24):",
                    "    try:",
                    "        with socket.create_connection(('127.0.0.1', supervisor_port), timeout=0.5):",
                    "            raise SystemExit(0)",
                    "    except OSError:",
                    "        time.sleep(1.0)",
                ]
            )
        else:
            lines.extend(
                [
                    "inactive_total = 0",
                    "for _ in range(24):",
                    "    active = subprocess.run(['systemctl', 'is-active', '--quiet', service_name], check=False).returncode == 0",
                    "    if active:",
                    "        inactive_total = 0",
                    "        try:",
                    "            with socket.create_connection(('127.0.0.1', supervisor_port), timeout=0.5):",
                    "                raise SystemExit(0)",
                    "        except OSError:",
                    "            pass",
                    "    else:",
                    "        inactive_total += 1",
                    "        if inactive_total >= 3:",
                    "            break",
                    "    time.sleep(1.0)",
                ]
            )
        lines.extend(
            [
                "for pid in pids:",
                "    try:",
                "        os.kill(pid, signal.SIGTERM)",
                "    except ProcessLookupError:",
                "        pass",
                "time.sleep(3.0)",
                "for pid in pids:",
                "    try:",
                "        os.kill(pid, 0)",
                "    except ProcessLookupError:",
                "        continue",
                "    try:",
                "        os.kill(pid, signal.SIGKILL)",
                "    except ProcessLookupError:",
                "        pass",
            ]
        )
        code = "\n".join(lines)
        try:
            reaper = subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except Exception as exc:
            return {
                "ok": False,
                "scheduled": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "pids": pids,
            }
        return {
            "ok": True,
            "scheduled": True,
            "reaper_pid": int(reaper.pid),
            "pids": pids,
            "service": service_name,
            "supervisor_port": supervisor_port,
        }

    def _schedule_retired_runtime_cleanup(self) -> dict[str, Any]:
        pids = sorted(
            pid
            for pid, proc in self._retired_runtime_procs.items()
            if pid > 0 and proc is not None and proc.poll() is None
        )
        if not pids:
            return {"ok": True, "scheduled": False, "reason": "no_retired_runtimes"}
        send_lines = (
            [
                "def send(pid, sig):",
                "    try:",
                "        os.kill(pid, sig)",
                "    except ProcessLookupError:",
                "        pass",
            ]
            if os.name == "nt"
            else [
                "def send(pid, sig):",
                "    try:",
                "        os.killpg(os.getpgid(pid), sig)",
                "    except (ProcessLookupError, PermissionError):",
                "        try:",
                "            os.kill(pid, sig)",
                "        except ProcessLookupError:",
                "            pass",
            ]
        )
        code = "\n".join(
            ["import os, signal, time", f"pids = {pids!r}", *send_lines]
            + [
                "time.sleep(1.0)",
                "for pid in pids:",
                "    send(pid, signal.SIGTERM)",
                "time.sleep(3.0)",
                "for pid in pids:",
                "    try:",
                "        os.kill(pid, 0)",
                "    except ProcessLookupError:",
                "        continue",
                "    send(pid, signal.SIGKILL)",
            ]
        )
        try:
            cleanup = subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except Exception as exc:
            return {
                "ok": False,
                "scheduled": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "pids": pids,
            }
        return {
            "ok": True,
            "scheduled": True,
            "cleanup_pid": int(cleanup.pid),
            "pids": pids,
        }

    def _refresh_autostart_wrapper(self, *, reason: str) -> dict[str, Any]:
        try:
            from adaos.services.autostart import default_spec as _default_autostart_spec
            from adaos.services.autostart import refresh_wrapper as _refresh_autostart_wrapper

            ctx = get_ctx()
            spec = _default_autostart_spec(ctx, host=self.runtime_host, port=self.runtime_port, token=self.token)
            payload = _refresh_autostart_wrapper(ctx, spec)
            payload["reason"] = str(reason or "supervisor.root_restart")
            return payload
        except Exception as exc:
            _LOG.warning("failed to refresh autostart wrapper before self-restart", exc_info=True)
            return {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "reason": str(reason or "supervisor.root_restart"),
            }

    async def complete_update(self, *, reason: str, auto: bool = False) -> dict[str, Any]:
        status = read_core_update_status()
        attempt = _read_update_attempt() or {}
        runtime = await asyncio.to_thread(self.status)
        promotion: dict[str, Any] | None = None
        promotion_gate: dict[str, Any] | None = None
        if _is_root_promotion_pending_status(status) or bool(runtime.get("root_promotion_required")):
            reliability = await self._runtime_update_gate_payload_async()
            migration = (
                reliability.get("skill_runtime_migration")
                if isinstance(reliability.get("skill_runtime_migration"), dict)
                else {}
            )
            if not reliability or bool(migration.get("pending")):
                gate_reason = "runtime_reliability_unavailable" if not reliability else "skill_runtime_migration_pending"
                promotion_gate = {
                    "ok": False,
                    "ready": False,
                    "retryable": True,
                    "reason": gate_reason,
                    "migration": {
                        "operation_id": migration.get("operation_id"),
                        "state": migration.get("state"),
                        "phase": migration.get("phase"),
                        "pending": bool(migration.get("pending")),
                        "current": migration.get("current"),
                        "completed_total": migration.get("completed_total"),
                        "total": migration.get("total"),
                    },
                }
                return {
                    "ok": True,
                    "accepted": False,
                    "deferred": True,
                    "retryable": True,
                    "auto": bool(auto),
                    "restart_required": False,
                    "status": status,
                    "attempt": attempt,
                    "runtime": runtime,
                    "promotion": None,
                    "promotion_gate": promotion_gate,
                    "restart": {"ok": True, "requested": False, "mode": "deferred"},
                    "message": (
                        "root promotion deferred until skill runtime migration completes"
                        if gate_reason == "skill_runtime_migration_pending"
                        else "root promotion deferred because candidate reliability is unavailable"
                    ),
                    "_served_by": "supervisor",
                }
            promotion_gate = {
                "ok": True,
                "ready": True,
                "retryable": False,
                "reason": "skill_runtime_migration_not_pending",
                "migration": {
                    "operation_id": migration.get("operation_id"),
                    "state": migration.get("state"),
                    "phase": migration.get("phase"),
                    "pending": False,
                    "failed_total": migration.get("failed_total"),
                },
            }
            promotion = await self.promote_root(reason=reason)
            status = promotion.get("status") if isinstance(promotion.get("status"), dict) else read_core_update_status()
            attempt = _read_update_attempt() or {}
            runtime = await asyncio.to_thread(self.status)
        if not (_is_root_restart_pending_status(status) or _is_root_restart_pending_attempt(attempt)):
            return {
                "ok": True,
                "accepted": False,
                "noop": True,
                "auto": bool(auto),
                "restart_required": False,
                "status": status,
                "attempt": attempt,
                "runtime": runtime,
                "promotion": promotion,
                "promotion_gate": promotion_gate,
                "restart": {"ok": True, "requested": False, "mode": "none"},
                "message": "root promotion is not required for the current update state",
                "_served_by": "supervisor",
            }

        restart_requested_at = _epoch(attempt.get("restart_requested_at") or status.get("restart_requested_at"))
        if auto and _is_root_restart_pending_attempt(attempt) and restart_requested_at > 0.0:
            return {
                "ok": True,
                "accepted": False,
                "noop": True,
                "auto": True,
                "restart_required": True,
                "status": status,
                "attempt": attempt,
                "runtime": runtime,
                "promotion": promotion,
                "promotion_gate": promotion_gate,
                "restart": {
                    "ok": True,
                    "requested": False,
                    "mode": str(attempt.get("restart_mode") or status.get("restart_mode") or "already_requested"),
                    "already_requested": True,
                    "restart_requested_at": restart_requested_at,
                },
                "message": "root promotion restart was already requested; waiting for runtime boot validation",
                "_served_by": "supervisor",
            }

        root_promotion = status.get("root_promotion") if isinstance(status.get("root_promotion"), dict) else {}
        candidate_wrapper_refresh = (
            root_promotion.get("wrapper_refresh")
            if isinstance(root_promotion.get("wrapper_refresh"), dict)
            else None
        )
        now = time.time()
        status_payload = dict(status)
        status_payload["state"] = "succeeded"
        status_payload["phase"] = "root_promoted"
        status_payload["root_promotion_required"] = False
        status_payload["restart_mode"] = "scheduling"
        status_payload["restart_requested_by_instance_id"] = _SUPERVISOR_INSTANCE_ID
        status_payload["restart_requested_by_pid"] = os.getpid()
        status_payload["restart_requested_by_started_at"] = _SUPERVISOR_INSTANCE_STARTED_AT
        status_payload["restart_requested_at"] = now
        status_payload["message"] = "root promotion completed; arming autostart service restart"
        status_payload["updated_at"] = now
        status = write_core_update_status(status_payload)

        attempt_payload = dict(attempt)
        attempt_payload["state"] = "awaiting_root_restart"
        attempt_payload["action"] = str(attempt_payload.get("action") or status.get("action") or "update")
        attempt_payload["accepted"] = True
        attempt_payload["awaiting_restart"] = True
        attempt_payload["restart_required"] = True
        attempt_payload["restart_mode"] = "scheduling"
        attempt_payload["restart_requested_by_instance_id"] = _SUPERVISOR_INSTANCE_ID
        attempt_payload["restart_requested_by_pid"] = os.getpid()
        attempt_payload["restart_requested_by_started_at"] = _SUPERVISOR_INSTANCE_STARTED_AT
        attempt_payload["restart_requested_at"] = now
        attempt_payload["requested_at"] = _epoch(attempt_payload.get("requested_at")) or now
        attempt_payload["transitioned_at"] = _epoch(attempt_payload.get("transitioned_at")) or now
        attempt_payload["updated_at"] = now
        attempt_payload["completion_reason"] = ""
        attempt_payload["last_status"] = status
        attempt = _write_update_attempt_preserving_subsequent_transition(attempt_payload)

        # The durable root-promoted/awaiting markers above must exist before the
        # restart worker can signal this process. The worker delay is deliberately
        # short and can otherwise win under storage pressure.
        restart = self._schedule_service_restart(
            reason=reason,
            candidate_wrapper_refresh=candidate_wrapper_refresh,
        )
        restart_mode = str(restart.get("mode") or "manual")
        finalized_at = time.time()
        status_payload = dict(status)
        status_payload["restart_mode"] = restart_mode
        status_payload["updated_at"] = finalized_at
        attempt_payload = dict(attempt)
        attempt_payload["restart_mode"] = restart_mode
        attempt_payload["updated_at"] = finalized_at
        if restart.get("requested"):
            status_payload["message"] = "root promotion completed; restarting autostart service to activate updated supervisor"
        else:
            status_payload["message"] = "root promotion completed; autostart service restart is still required"
            status_payload["restart_requested_at"] = None
            attempt_payload["restart_requested_at"] = None
        status = write_core_update_status(status_payload)
        attempt_payload["last_status"] = status
        attempt = _write_update_attempt(attempt_payload)
        return {
            "ok": True,
            "accepted": True,
            "auto": bool(auto),
            "restart_required": True,
            "status": status,
            "attempt": attempt,
            "runtime": runtime,
            "promotion": promotion,
            "promotion_gate": promotion_gate,
            "restart": restart,
            "message": str(status.get("message") or "").strip(),
            "_served_by": "supervisor",
        }

    def _sidecar_role(self) -> str | None:
        try:
            ctx = get_ctx()
        except Exception:
            ctx = None
        if ctx is not None:
            with contextlib.suppress(Exception):
                role = str(getattr(ctx, "config", None).role or "").strip().lower()
                if role:
                    return role
            with contextlib.suppress(Exception):
                conf = load_config(ctx=ctx)
                role = str(getattr(conf, "role", "") or "").strip().lower()
                if role:
                    return role
        try:
            conf = load_config()
            role = str(getattr(conf, "role", "") or "").strip().lower()
            if role:
                return role
        except Exception:
            pass
        return None

    def _hub_root_root_probe_config(self) -> dict[str, Any]:
        try:
            conf = load_config()
        except Exception:
            conf = None
        subnet_id = str(getattr(conf, "subnet_id", "") or "").strip()
        zone_id = str(getattr(conf, "zone_id", "") or "").strip().lower()
        root_settings = getattr(conf, "root_settings", None)
        root_base_url = str(
            os.getenv("ROOT_BASE_URL")
            or getattr(root_settings, "base_url", None)
            or DEFAULT_PUBLIC_ROOT_BASE_URL
        ).strip().rstrip("/")
        root_token = str(
            os.getenv("ADAOS_ROOT_OWNER_TOKEN")
            or os.getenv("ROOT_TOKEN")
            or os.getenv("ADAOS_ROOT_TOKEN")
            or ""
        ).strip()
        return {
            "root_base_url": root_base_url,
            "root_token_present": bool(root_token),
            "root_token": root_token,
            "target_id": f"hub:{subnet_id}" if subnet_id else None,
            "subnet_id": subnet_id or None,
            "zone_id": zone_id or None,
        }

    def _hub_root_root_probe_state_payload(self) -> dict[str, Any]:
        return {
            "enabled": _hub_root_root_probe_enabled(),
            "interval_sec": _hub_root_root_probe_interval_sec(),
            "timeout_sec": _hub_root_root_probe_timeout_sec(),
            "ttl_sec": _hub_root_root_probe_ttl_sec(),
            "last_at": self._hub_root_root_probe_last_at,
            "last_state": self._hub_root_root_probe_last_state,
            "last_reason": self._hub_root_root_probe_last_reason,
            "last_result": dict(self._hub_root_root_probe_last_result or {}),
        }

    def _post_recovery_core_update_reconcile_state_payload(self) -> dict[str, Any]:
        return {
            "enabled": _post_recovery_core_update_reconcile_enabled(),
            "periodic_enabled": _periodic_core_update_reconcile_enabled(),
            "last_at": self._hub_root_post_recovery_reconcile_last_at,
            "last_key": self._hub_root_post_recovery_reconcile_last_key,
            "cooldown_sec": _post_recovery_core_update_reconcile_cooldown_sec(),
            "periodic_interval_sec": _periodic_core_update_reconcile_interval_sec(),
            "countdown_sec": _post_recovery_core_update_reconcile_countdown_sec(),
            "last_result": _compact_watchdog_last_result(self._hub_root_post_recovery_reconcile_last_result),
        }

    def _post_recovery_member_hub_refresh_state_payload(self) -> dict[str, Any]:
        return {
            "enabled": _post_recovery_member_hub_refresh_enabled(),
            "last_at": self._member_hub_post_recovery_refresh_last_at,
            "cooldown_sec": _post_recovery_member_hub_refresh_cooldown_sec(),
            "last_result": _compact_watchdog_last_result(self._member_hub_post_recovery_refresh_last_result),
        }

    def _probe_hub_root_from_root_once(self, *, now: float | None = None) -> dict[str, Any]:
        current_time = time.time() if now is None else float(now)
        ttl_sec = _hub_root_root_probe_ttl_sec()
        config = self._hub_root_root_probe_config()
        root_base_url = str(config.get("root_base_url") or "").strip().rstrip("/")
        target_id = str(config.get("target_id") or "").strip()
        lookup_hub_id = target_id[len("hub:") :] if target_id.startswith("hub:") else target_id
        root_token = str(config.get("root_token") or "").strip()
        base_result: dict[str, Any] = {
            "ok": False,
            "state": "unknown",
            "checked_at": current_time,
            "target_id": target_id or None,
            "lookup_hub_id": lookup_hub_id or None,
            "root_base_url": root_base_url or None,
            "ttl_sec": ttl_sec,
        }
        if not root_base_url:
            return {**base_result, "state": "not_configured", "reason": "root_base_url_missing"}
        if not target_id:
            return {**base_result, "state": "not_configured", "reason": "hub_target_id_missing"}
        if not root_token:
            return {**base_result, "state": "not_configured", "reason": "root_token_missing"}

        headers = {"Accept": "application/json", "X-Root-Token": root_token}
        if config.get("subnet_id"):
            headers["X-AdaOS-Subnet-Id"] = str(config["subnet_id"])
        if config.get("zone_id"):
            headers["X-AdaOS-Zone"] = str(config["zone_id"])
        session = requests.Session()
        try:
            try:
                session.trust_env = False
            except Exception:
                pass
            response = session.get(
                f"{root_base_url}/v1/hubs/control/reports",
                params={"hub_id": lookup_hub_id},
                headers=headers,
                timeout=_hub_root_root_probe_timeout_sec(),
            )
            status_code = int(response.status_code or 0)
            if status_code >= 400:
                return {
                    **base_result,
                    "state": "http_error",
                    "reason": f"http_{status_code}",
                    "status_code": status_code,
                }
            payload = response.json()
        except Exception as exc:
            return {
                **base_result,
                "state": "request_failed",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        finally:
            with contextlib.suppress(Exception):
                session.close()

        reports = payload.get("reports") if isinstance(payload, dict) else None
        if not isinstance(reports, list):
            reports = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(reports, list) or not reports:
            return {**base_result, "ok": True, "state": "no_report", "reason": "root returned no control report"}
        report_item = reports[0] if isinstance(reports[0], dict) else {}
        report = report_item.get("report") if isinstance(report_item.get("report"), dict) else {}
        observed_raw = (
            report_item.get("server_time_utc")
            or report_item.get("reported_at")
            or report.get("root_received_at")
            or report.get("reported_at")
        )
        observed_at = _parse_root_probe_time(observed_raw)
        age_sec = (current_time - observed_at) if observed_at is not None else None
        state = "ready" if age_sec is not None and age_sec <= ttl_sec else "stale"
        reason = (
            f"root control report fresh age={age_sec:.1f}s"
            if state == "ready" and age_sec is not None
            else (
                f"root control report stale age={age_sec:.1f}s"
                if age_sec is not None
                else "root control report has no parseable timestamp"
            )
        )
        root_control = report.get("root_control") if isinstance(report.get("root_control"), dict) else {}
        route = report.get("route") if isinstance(report.get("route"), dict) else {}
        transport = report.get("transport") if isinstance(report.get("transport"), dict) else {}
        return {
            **base_result,
            "ok": True,
            "state": state,
            "reason": reason,
            "observed_at": observed_at,
            "observed_at_raw": str(observed_raw or "").strip() or None,
            "age_sec": age_sec,
            "root_control_status": str(root_control.get("status") or "").strip().lower() or None,
            "route_status": str(route.get("status") or "").strip().lower() or None,
            "transport_assessment_state": str(transport.get("assessment_state") or "").strip().lower() or None,
            "runtime_instance_id": str(
                report.get("runtime_instance_id")
                or ((report.get("runtime") or {}) if isinstance(report.get("runtime"), dict) else {}).get("runtime_instance_id")
                or ""
            ).strip() or None,
            "transition_role": str(report.get("transition_role") or "").strip() or None,
            "event_id": str(report_item.get("event_id") or "").strip() or None,
        }

    async def _maybe_probe_hub_root_from_root(self, *, force: bool = False) -> dict[str, Any] | None:
        if not _hub_root_root_probe_enabled():
            self._hub_root_root_probe_last_state = "disabled"
            self._hub_root_root_probe_last_reason = "root perspective probe disabled"
            return None
        transition_role = str(self._managed_transition_role or "").strip().lower()
        managed_role = transition_role if transition_role in {"hub", "member"} else None
        role = str(managed_role or self._sidecar_role() or transition_role or "").strip().lower()
        if role != "hub":
            self._hub_root_root_probe_last_state = "not_applicable"
            self._hub_root_root_probe_last_reason = f"role={role or '-'}"
            return None
        now = time.time()
        last_at = self._hub_root_root_probe_last_at
        if not force and last_at is not None and (now - float(last_at)) < _hub_root_root_probe_interval_sec():
            return self._hub_root_root_probe_last_result
        previous_state = self._hub_root_root_probe_last_state
        result = await asyncio.to_thread(self._probe_hub_root_from_root_once, now=now)
        self._hub_root_root_probe_last_at = now
        self._hub_root_root_probe_last_result = dict(result)
        self._hub_root_root_probe_last_state = str(result.get("state") or "").strip().lower() or "unknown"
        self._hub_root_root_probe_last_reason = str(result.get("reason") or "").strip() or None
        if previous_state != self._hub_root_root_probe_last_state:
            self._append_hub_root_watchdog_event(
                {
                    "event": "root_perspective_probe",
                    "state": self._hub_root_root_probe_last_state,
                    "reason": self._hub_root_root_probe_last_reason,
                    "probe": dict(result),
                }
            )
        return result

    def _sidecar_status_payload(self) -> dict[str, Any]:
        role = self._sidecar_role()
        try:
            process = realtime_sidecar_listener_snapshot(self._sidecar_proc, role=role)
        except TypeError:
            process = realtime_sidecar_listener_snapshot(self._sidecar_proc)
        code_state = self._sidecar_code_state()
        process.update(
            {
                "health": {
                    "last_probe_at": self._sidecar_last_probe_at,
                    "last_probe_ok": self._sidecar_last_probe_ok,
                    "last_probe_error": self._sidecar_last_probe_error,
                    "consecutive_failures": int(self._sidecar_consecutive_probe_failures),
                },
                "code": {
                    **code_state,
                    "active_fingerprint": self._sidecar_code_fingerprint,
                    "active_updated_at": self._sidecar_code_fingerprint_updated_at,
                },
                "launch_cwd": self._sidecar_launch_cwd,
                "last_start_reason": self._sidecar_last_start_reason,
                "last_restart_reason": self._sidecar_last_restart_reason,
                "transition": self._sidecar_transition_payload(),
                "restart_policy": self._sidecar_restart_policy_state(),
                "sync": {
                    "last_sync_at": self._sidecar_last_sync_at,
                    "last_sync_source_slot": self._sidecar_last_sync_source_slot,
                    "last_sync_reason": self._sidecar_last_sync_reason,
                    "last_sync_changed_paths": list(self._sidecar_last_sync_changed_paths),
                },
            }
        )
        record = _read_jsonl_tail(realtime_sidecar_diag_path(), limit=1)
        last_diag = record[-1] if record else None
        if isinstance(last_diag, dict):
            if isinstance(last_diag.get("enablement_policy"), dict):
                current_enablement = (
                    process.get("enablement_policy")
                    if isinstance(process.get("enablement_policy"), dict)
                    else {}
                )
                current_source = str(current_enablement.get("source") or "").strip().lower()
                current_role = str(current_enablement.get("role") or "").strip().lower()
                if not current_enablement or current_source in {"legacy_runtime", "unavailable"} or not current_role:
                    process["enablement_policy"] = dict(last_diag.get("enablement_policy") or current_enablement or {})
            if isinstance(last_diag.get("route_tunnel_contract"), dict):
                process["route_tunnel_contract"] = dict(last_diag.get("route_tunnel_contract") or {})
        return {
            "enabled": bool(realtime_sidecar_enabled(role=role)),
            "role": role,
            "process": process,
            "code": process["code"],
            "health": process["health"],
            "restart_policy": process["restart_policy"],
            "sync": process["sync"],
            "launch_cwd": self._sidecar_launch_cwd,
            "last_start_reason": self._sidecar_last_start_reason,
            "last_restart_reason": self._sidecar_last_restart_reason,
            "transition": process["transition"],
        }

    def _sidecar_transition_payload(self) -> dict[str, Any]:
        return {
            "in_progress": bool(self._sidecar_transition_in_progress),
            "transition_id": self._sidecar_transition_id,
            "source": self._sidecar_transition_source,
            "reason": self._sidecar_transition_reason,
            "started_at": self._sidecar_transition_started_at,
            "completed_at": self._sidecar_transition_completed_at,
            "outcome": self._sidecar_transition_outcome,
            "error": self._sidecar_transition_error,
        }

    def _begin_sidecar_transition(
        self,
        *,
        source: str,
        reason: str,
        reject_if_active: bool,
    ) -> str | None:
        if self._sidecar_transition_in_progress:
            if reject_if_active:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "sidecar_transition_in_progress",
                        "message": "another sidecar lifecycle transition is already in progress",
                        "transition": self._sidecar_transition_payload(),
                    },
                )
            return None
        transition_id = f"sidecar-{uuid.uuid4().hex[:12]}"
        self._sidecar_transition_in_progress = True
        self._sidecar_transition_id = transition_id
        self._sidecar_transition_source = str(source or "supervisor").strip() or "supervisor"
        self._sidecar_transition_reason = str(reason or "supervisor.sidecar.restart").strip()
        self._sidecar_transition_started_at = time.time()
        self._sidecar_transition_completed_at = None
        self._sidecar_transition_outcome = "in_progress"
        self._sidecar_transition_error = None
        return transition_id

    def _finish_sidecar_transition(
        self,
        transition_id: str,
        *,
        outcome: str,
        error: str | None = None,
    ) -> None:
        if str(self._sidecar_transition_id or "") != str(transition_id or ""):
            return
        self._sidecar_transition_in_progress = False
        self._sidecar_transition_completed_at = time.time()
        self._sidecar_transition_outcome = str(outcome or "completed").strip() or "completed"
        self._sidecar_transition_error = str(error or "").strip() or None

    def _runtime_request_json(
        self,
        *,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-AdaOS-Token"] = self.token
        if payload is not None:
            headers["Content-Type"] = "application/json"
        session = requests.Session()
        try:
            try:
                session.trust_env = False
            except Exception:
                pass
            response = session.request(
                str(method or "GET").upper(),
                self.runtime_base_url + str(path or ""),
                headers=headers,
                json=payload,
                timeout=float(timeout),
            )
            if int(response.status_code or 0) >= 400:
                try:
                    detail: Any = response.json()
                except Exception:
                    detail = (response.text or f"runtime returned HTTP {response.status_code}").strip()[:500]
                if isinstance(detail, dict) and set(detail.keys()) == {"detail"}:
                    detail = detail["detail"]
                raise HTTPException(status_code=int(response.status_code), detail=detail)
            body = response.json()
            if not isinstance(body, dict):
                raise RuntimeError("runtime returned a non-object payload")
            return body
        finally:
            with contextlib.suppress(Exception):
                session.close()

    def _runtime_reliability_payload(self, *, timeout: float = 2.0) -> dict[str, Any]:
        path = "/api/node/reliability/supervisor-channel"
        try:
            payload = self._runtime_request_json(path=path, timeout=timeout)
        except Exception as exc:
            _LOG.debug("supervisor channel preflight unavailable: %s: %s", type(exc).__name__, exc)
            try:
                from adaos.services.incident_registry import record_runtime_api_timeout

                record_runtime_api_timeout(
                    source="supervisor.reliability_preflight",
                    path=path,
                    timeout_s=float(timeout),
                    exc=exc,
                    component="runtime_reliability_api",
                    evidence={
                        "runtime_base_url": self.runtime_base_url,
                        "runtime_port": self.runtime_port,
                    },
                )
            except Exception:
                pass
            try:
                payload = self._runtime_request_json(path="/api/node/reliability", timeout=timeout)
            except Exception:
                return {}
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        return dict(runtime) if isinstance(runtime, dict) else {}

    async def _runtime_reliability_payload_async(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Read the bounded channel watchdog contract off the supervisor loop."""

        resolved_timeout = (
            _runtime_reliability_probe_timeout_sec()
            if timeout is None
            else max(0.1, float(timeout))
        )
        return await asyncio.to_thread(
            self._runtime_reliability_payload,
            timeout=resolved_timeout,
        )

    def _runtime_update_gate_payload(self, *, timeout: float = 2.0) -> dict[str, Any]:
        path = "/api/node/reliability/update-gate"
        try:
            payload = self._runtime_request_json(path=path, timeout=timeout)
            runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
            return dict(runtime)
        except Exception as exc:
            _LOG.debug(
                "compact runtime update gate unavailable; falling back to full reliability: %s: %s",
                type(exc).__name__,
                exc,
            )
            return self._runtime_reliability_payload(timeout=timeout)

    async def _runtime_update_gate_payload_async(self, *, timeout: float | None = None) -> dict[str, Any]:
        resolved_timeout = (
            _runtime_reliability_probe_timeout_sec()
            if timeout is None
            else max(0.1, float(timeout))
        )
        return await asyncio.to_thread(
            self._runtime_update_gate_payload,
            timeout=resolved_timeout,
        )

    def _transition_continuity_guard_snapshot(self, *, timeout: float = 2.0) -> dict[str, Any]:
        runtime = self._runtime_reliability_payload(timeout=timeout)
        sidecar_runtime = runtime.get("sidecar_runtime") if isinstance(runtime.get("sidecar_runtime"), dict) else {}
        media_runtime = runtime.get("media_runtime") if isinstance(runtime.get("media_runtime"), dict) else {}
        continuity_contract = (
            sidecar_runtime.get("continuity_contract")
            if isinstance(sidecar_runtime.get("continuity_contract"), dict)
            else {}
        )
        update_guard = media_runtime.get("update_guard") if isinstance(media_runtime.get("update_guard"), dict) else {}
        role = str(update_guard.get("role") or self._sidecar_role() or "").strip().lower() or None
        return {
            "role": role,
            "continuity_contract": dict(continuity_contract) if isinstance(continuity_contract, dict) else {},
            "update_guard": dict(update_guard) if isinstance(update_guard, dict) else {},
        }

    @staticmethod
    def _transition_operation_label(operation: str) -> str:
        op = str(operation or "").strip().lower()
        if op == "restart":
            return "runtime restart"
        if op == "rollback":
            return "core rollback"
        return "core update"

    def _transition_continuity_guard_decision(self, *, operation: str) -> dict[str, Any] | None:
        snapshot = self._transition_continuity_guard_snapshot(timeout=2.0)
        role = str(snapshot.get("role") or "").strip().lower()
        continuity_contract = (
            snapshot.get("continuity_contract")
            if isinstance(snapshot.get("continuity_contract"), dict)
            else {}
        )
        update_guard = snapshot.get("update_guard") if isinstance(snapshot.get("update_guard"), dict) else {}
        if role not in {"hub", "member"}:
            return None

        member_policy = str(update_guard.get("member_runtime_update") or "allow").strip().lower() or "allow"
        hub_policy = str(
            continuity_contract.get("hub_runtime_update") or update_guard.get("hub_runtime_update") or "allow"
        ).strip().lower() or "allow"
        current_support = str(
            continuity_contract.get("current_support") or update_guard.get("current_support") or "unknown"
        ).strip().lower() or "unknown"
        required = bool(
            continuity_contract.get("required") or update_guard.get("hub_sidecar_continuity_required")
        )
        operation_label = self._transition_operation_label(operation)

        if role == "member" and member_policy == "defer" and bool(update_guard.get("live_session_present")):
            return {
                "code": "member_live_media_defer",
                "planned_reason": "live_media_guard",
                "message": f"{operation_label} deferred while member owns an active browser media session",
                "retry_after_sec": max(_live_media_guard_defer_sec(), _min_update_period_sec()),
                "live_media_guard": update_guard,
                "continuity_contract": continuity_contract,
            }

        if role == "hub" and required and hub_policy == "preserve_sidecar" and current_support != "ready":
            return {
                "code": "hub_sidecar_continuity_pending",
                "planned_reason": "live_media_guard",
                "message": (
                    f"{operation_label} deferred until independent sidecar continuity is ready "
                    "for the active live media path"
                ),
                "retry_after_sec": max(_live_media_guard_defer_sec(), _min_update_period_sec()),
                "live_media_guard": update_guard,
                "continuity_contract": continuity_contract,
            }

        return None

    async def _candidate_cutover_recovery_guard_snapshot(self) -> dict[str, Any]:
        runtime = await asyncio.to_thread(self.status)
        runtime_ready = bool(
            runtime.get("runtime_api_ready")
            and str(runtime.get("runtime_state") or "").strip().lower() == "ready"
        )
        channel_runtime = await self._runtime_reliability_payload_async()
        node = channel_runtime.get("node") if isinstance(channel_runtime.get("node"), dict) else {}
        role = str(node.get("role") or self._managed_transition_role or self._sidecar_role() or "").strip().lower()
        channel: dict[str, Any]
        if role == "hub":
            channel = self._hub_root_channel_state(channel_runtime)
            channel_ready = self._hub_root_channel_ready(channel)
        elif role == "member":
            channel = self._member_hub_channel_state(channel_runtime)
            channel_ready = bool(
                channel.get("connected")
                and str(channel.get("route_status") or "").strip().lower() == "ready"
                and str(channel.get("hub_member_status") or "").strip().lower() == "ready"
            )
        else:
            channel = {}
            channel_ready = False
        return {
            "ready": bool(runtime_ready and channel_ready),
            "runtime_ready": runtime_ready,
            "runtime_state": str(runtime.get("runtime_state") or "").strip().lower() or None,
            "runtime_instance_id": runtime.get("runtime_instance_id"),
            "role": role or None,
            "channel_ready": bool(channel_ready),
            "channel": channel,
            "captured_at": time.time(),
        }

    def _schedule_candidate_cutover_recovery_guard(
        self,
        *,
        request: dict[str, Any],
        status: dict[str, Any],
        attempt: dict[str, Any],
        guard: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        stable_sec = _cutover_recovery_stable_sec()
        previous_ready_since = _epoch(
            attempt.get("cutover_recovery_ready_since") or status.get("cutover_recovery_ready_since")
        )
        ready_since = previous_ready_since if bool(guard.get("ready")) else 0.0
        if bool(guard.get("ready")) and ready_since <= 0.0:
            ready_since = now
        stable_for_sec = max(0.0, now - ready_since) if ready_since > 0.0 else 0.0
        if bool(guard.get("ready")):
            retry_after_sec = max(1.0, stable_sec - stable_for_sec)
            message = "core update remains deferred until recovered runtime and channel pass the stability window"
        else:
            retry_after_sec = min(10.0, stable_sec)
            message = "core update remains deferred until runtime boot and upstream channel recover"
        diagnostics = {
            "cutover_recovery_guard": guard,
            "cutover_recovery_ready_since": ready_since or None,
            "cutover_recovery_stable_sec": stable_sec,
            "cutover_recovery_stable_for_sec": round(stable_for_sec, 3),
        }
        return self._schedule_planned_transition(
            request=request,
            scheduled_for=now + retry_after_sec,
            planned_reason="candidate_cutover_recovery",
            message=message,
            extra_status=diagnostics,
            extra_attempt=diagnostics,
        )

    async def _transition_continuity_guard_decision_async(self, *, operation: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._transition_continuity_guard_decision, operation=operation)

    def _schedule_continuity_guarded_transition(
        self,
        request: dict[str, Any],
        decision: dict[str, Any],
        *,
        current_status: dict[str, Any] | None = None,
        current_attempt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        retry_after_sec = max(30.0, float(decision.get("retry_after_sec") or _live_media_guard_defer_sec()))
        existing_due_at = _epoch((current_attempt or {}).get("scheduled_for") or (current_status or {}).get("scheduled_for"))
        scheduled_for = max(time.time() + retry_after_sec, existing_due_at)
        extra_payload = {
            "guard_code": str(decision.get("code") or "").strip() or None,
            "live_media_guard": decision.get("live_media_guard"),
            "continuity_contract": decision.get("continuity_contract"),
        }
        return self._schedule_planned_transition(
            request=request,
            scheduled_for=scheduled_for,
            planned_reason=str(decision.get("planned_reason") or "live_media_guard"),
            message=str(decision.get("message") or "transition deferred by live media guard"),
            extra_status=extra_payload,
            extra_attempt=extra_payload,
        )

    def _raise_restart_continuity_block(self, decision: dict[str, Any]) -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(decision.get("message") or "runtime restart blocked by live media guard"),
                "planned_reason": str(decision.get("planned_reason") or "live_media_guard"),
                "guard_code": str(decision.get("code") or "").strip() or None,
                "live_media_guard": decision.get("live_media_guard"),
                "continuity_contract": decision.get("continuity_contract"),
                "retry_after_sec": float(decision.get("retry_after_sec") or _live_media_guard_defer_sec()),
            },
        )

    def _runtime_sidecar_runtime_payload(self) -> dict[str, Any]:
        return self._status_service.sidecar_runtime_payload(
            self,
            self._status_operations(),
        )

    def _hub_root_watchdog_state_payload(self, *, include_events: bool = True) -> dict[str, Any]:
        log_path = _supervisor_hub_root_watchdog_log_path()
        payload = {
            "enabled": _hub_root_watchdog_enabled(),
            "last_state": self._hub_root_watchdog_last_state,
            "last_reason": self._hub_root_watchdog_last_reason,
            "last_reconnect_at": self._hub_root_watchdog_last_reconnect_at,
            "reconnect_total": int(self._hub_root_watchdog_reconnect_total),
            "cooldown_sec": _hub_root_watchdog_cooldown_sec(),
            "reset_degraded_route": _hub_root_watchdog_reset_degraded_route_enabled(),
            "verify_timeout_sec": _hub_root_watchdog_verify_timeout_sec(),
            "poll_interval_sec": _required_upstream_watchdog_poll_interval_sec(),
            "last_poll_at": self._required_upstream_watchdog_last_poll_at,
            "log_path": str(log_path),
            "last_result": _compact_watchdog_last_result(self._hub_root_watchdog_last_result),
            "root_perspective_probe": self._hub_root_root_probe_state_payload(),
            "post_recovery_core_update_reconcile": self._post_recovery_core_update_reconcile_state_payload(),
        }
        if include_events:
            payload["recent_events"] = [
                _compact_watchdog_event(event)
                for event in _read_jsonl_tail(log_path, limit=10)
                if event
            ]
        return payload

    def _member_hub_watchdog_state_payload(self, *, include_events: bool = True) -> dict[str, Any]:
        log_path = _supervisor_member_hub_watchdog_log_path()
        payload = {
            "enabled": _member_hub_watchdog_enabled(),
            "last_state": self._member_hub_watchdog_last_state,
            "last_reason": self._member_hub_watchdog_last_reason,
            "last_reconnect_at": self._member_hub_watchdog_last_reconnect_at,
            "reconnect_total": int(self._member_hub_watchdog_reconnect_total),
            "cooldown_sec": _member_hub_watchdog_cooldown_sec(),
            "verify_timeout_sec": _member_hub_watchdog_verify_timeout_sec(),
            "poll_interval_sec": _required_upstream_watchdog_poll_interval_sec(),
            "last_poll_at": self._required_upstream_watchdog_last_poll_at,
            "log_path": str(log_path),
            "last_result": _compact_watchdog_last_result(self._member_hub_watchdog_last_result),
            "post_recovery_refresh": self._post_recovery_member_hub_refresh_state_payload(),
        }
        if include_events:
            payload["recent_events"] = [
                _compact_watchdog_event(event)
                for event in _read_jsonl_tail(log_path, limit=10)
                if event
            ]
        return payload

    @staticmethod
    def _required_upstream_link_kind_for_role(role: str | None) -> str:
        role_norm = str(role or "").strip().lower()
        return "member_hub" if role_norm == "member" else "hub_root"

    @staticmethod
    def _hub_root_sidecar_handoff_evidence(sidecar_runtime: Any) -> dict[str, Any]:
        sidecar = sidecar_runtime if isinstance(sidecar_runtime, dict) else {}
        route_tunnel = (
            sidecar.get("route_tunnel_contract")
            if isinstance(sidecar.get("route_tunnel_contract"), dict)
            else {}
        )
        blockers: list[str] = []
        route_ready: dict[str, bool] = {}
        for kind in ("ws", "yws"):
            entry = route_tunnel.get(kind) if isinstance(route_tunnel.get(kind), dict) else {}
            entry_blockers = [str(item).strip() for item in list(entry.get("blockers") or []) if str(item).strip()]
            blockers.extend(f"{kind}: {item}" for item in entry_blockers)
            route_ready[kind] = (
                str(entry.get("current_owner") or "").strip().lower() == "sidecar"
                and bool(entry.get("listener_ready"))
                and bool(entry.get("handoff_ready"))
                and not entry_blockers
            )
        status = str(sidecar.get("status") or "").strip().lower()
        remote_state = str(sidecar.get("remote_session_state") or "").strip().lower()
        transport_ready = bool(sidecar.get("transport_ready")) or status == "ready" or remote_state == "ready"
        enabled = bool(sidecar.get("enabled"))
        ready = enabled and transport_ready and route_ready.get("ws", False) and route_ready.get("yws", False)
        return {
            "ready": ready,
            "state": "ready" if ready else (status or remote_state or "unknown"),
            "transport_ready": transport_ready,
            "routes": route_ready,
            "blockers": blockers,
        }

    def _required_upstream_link_state_payload(self, *, role: str | None = None) -> dict[str, Any]:
        transition_role = str(self._managed_transition_role or "").strip().lower()
        managed_role = transition_role if transition_role in {"hub", "member"} else None
        role_norm = str(role or managed_role or self._sidecar_role() or transition_role or "").strip().lower() or None
        kind = self._required_upstream_link_kind_for_role(role_norm)
        payload = (
            self._member_hub_watchdog_state_payload(include_events=False)
            if kind == "member_hub"
            else self._hub_root_watchdog_state_payload(include_events=False)
        )
        sidecar_enabled = bool(realtime_sidecar_enabled(role=role_norm))
        state = str(payload.get("last_state") or "").strip().lower() or "unknown"
        paused_states = {"waiting_restart", "restarting", "paused_for_update", "cooldown"}
        ready = state in {"ready", "not_applicable"} or state in paused_states
        desired_state = "connected" if self._desired_running and not self._stopping else "paused"
        current_owner = "runtime"
        planned_owner = "runtime"
        future_owner = None
        continuity_mode = "runtime_bound"
        if kind == "hub_root":
            current_owner = "sidecar" if sidecar_enabled else "runtime"
            planned_owner = current_owner
            continuity_mode = "slot_sticky" if current_owner == "sidecar" else "runtime_bound"
        elif kind == "member_hub":
            current_owner = "runtime"
            planned_owner = "runtime"
            future_owner = "sidecar"
            continuity_mode = "runtime_bound"
        result = {
            "kind": kind,
            "role": role_norm,
            "owner": "supervisor",
            "state": state,
            "reason": str(payload.get("last_reason") or "").strip() or None,
            "ready": ready,
            "visible": True,
            "desired_state": desired_state,
            "current_owner": current_owner,
            "planned_owner": planned_owner,
            "future_owner": future_owner,
            "continuity_mode": continuity_mode,
            "sidecar_enabled": sidecar_enabled,
            "reconnect_total": int(payload.get("reconnect_total") or 0),
            "cooldown_sec": float(payload.get("cooldown_sec") or 0.0),
            "verify_timeout_sec": float(payload.get("verify_timeout_sec") or 0.0),
            "served_by": "supervisor",
            "watchdog": dict(payload),
            "blockers": [],
        }
        if kind == "hub_root" and sidecar_enabled:
            try:
                evidence = self._hub_root_sidecar_handoff_evidence(self._runtime_sidecar_runtime_payload())
            except Exception:
                evidence = {"ready": False, "state": "unknown", "blockers": []}
            result["handoff_state"] = str(evidence.get("state") or "unknown")
            result["handoff_ready"] = bool(evidence.get("ready"))
            reason = str(result.get("reason") or "").strip().lower()
            if (
                not ready
                and bool(evidence.get("ready"))
                and "browser route degraded" in reason
                and not list(result.get("blockers") or [])
            ):
                result.update(
                    {
                        "state": "ready",
                        "reason": "sidecar browser route handoff is ready after stale runtime route degradation",
                        "ready": True,
                        "served_by": "supervisor_sidecar",
                    }
                )
        return result

    def _required_upstream_link_snapshot(
        self,
        *,
        runtime: dict[str, Any] | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        payload = (
            runtime.get("required_upstream_link")
            if isinstance(runtime, dict) and isinstance(runtime.get("required_upstream_link"), dict)
            else {}
        )
        if payload:
            return dict(payload)
        return self._required_upstream_link_state_payload(role=role)

    @staticmethod
    def _hub_root_channel_state(runtime: dict[str, Any]) -> dict[str, Any]:
        runtime = runtime if isinstance(runtime, dict) else {}
        readiness_tree = runtime.get("readiness_tree") if isinstance(runtime.get("readiness_tree"), dict) else {}
        root_control = readiness_tree.get("root_control") if isinstance(readiness_tree.get("root_control"), dict) else {}
        route = readiness_tree.get("route") if isinstance(readiness_tree.get("route"), dict) else {}
        channel_overview = runtime.get("channel_overview") if isinstance(runtime.get("channel_overview"), dict) else {}
        hub_root = channel_overview.get("hub_root") if isinstance(channel_overview.get("hub_root"), dict) else {}
        hub_root_browser = (
            channel_overview.get("hub_root_browser")
            if isinstance(channel_overview.get("hub_root_browser"), dict)
            else {}
        )
        strategy = (
            runtime.get("hub_root_transport_strategy")
            if isinstance(runtime.get("hub_root_transport_strategy"), dict)
            else {}
        )
        return {
            "root_control_status": str(root_control.get("status") or "").strip().lower() or None,
            "route_status": str(route.get("status") or "").strip().lower() or None,
            "hub_root_status": str(hub_root.get("effective_status") or "").strip().lower() or None,
            "hub_root_state": str(hub_root.get("effective_state") or "").strip().lower() or None,
            "hub_root_browser_status": str(hub_root_browser.get("effective_status") or "").strip().lower() or None,
            "hub_root_browser_state": str(hub_root_browser.get("effective_state") or "").strip().lower() or None,
            "last_event": str(strategy.get("last_event") or "").strip() or None,
            "last_summary": str(strategy.get("last_summary") or "").strip() or None,
            "selected_server": str(strategy.get("selected_server") or "").strip() or None,
            "effective_transport": str(strategy.get("effective_transport") or "").strip() or None,
            "strategy_updated_ago_s": (
                float(strategy["updated_ago_s"])
                if isinstance(strategy.get("updated_ago_s"), (int, float))
                else None
            ),
            "last_attempt_ago_s": (
                float(strategy["last_attempt_ago_s"])
                if isinstance(strategy.get("last_attempt_ago_s"), (int, float))
                else None
            ),
        }

    @staticmethod
    def _member_hub_channel_state(runtime: dict[str, Any]) -> dict[str, Any]:
        runtime = runtime if isinstance(runtime, dict) else {}
        node = runtime.get("node") if isinstance(runtime.get("node"), dict) else {}
        readiness_tree = runtime.get("readiness_tree") if isinstance(runtime.get("readiness_tree"), dict) else {}
        route = readiness_tree.get("route") if isinstance(readiness_tree.get("route"), dict) else {}
        hub_member = readiness_tree.get("hub_member") if isinstance(readiness_tree.get("hub_member"), dict) else {}
        member_state = (
            runtime.get("hub_member_connection_state")
            if isinstance(runtime.get("hub_member_connection_state"), dict)
            else {}
        )
        hub = member_state.get("hub") if isinstance(member_state.get("hub"), dict) else {}
        return {
            "route_status": str(route.get("status") or "").strip().lower() or None,
            "hub_member_status": str(hub_member.get("status") or "").strip().lower() or None,
            "member_state": str(member_state.get("state") or "").strip().lower() or None,
            "assessment_state": (
                str((member_state.get("assessment") or {}).get("state") or "").strip().lower()
                if isinstance(member_state.get("assessment"), dict)
                else None
            ),
            "assessment_reason": (
                str((member_state.get("assessment") or {}).get("reason") or "").strip() or None
                if isinstance(member_state.get("assessment"), dict)
                else None
            ),
            "connected": (
                bool(hub.get("connected"))
                or bool(node.get("connected_to_hub"))
                or bool(node.get("connected_to_subnet"))
            ),
            "transition_state": str(hub.get("transition_state") or "").strip().lower() or None,
            "transition_reason": str(hub.get("transition_reason") or "").strip() or None,
            "hub_url": str(hub.get("hub_url") or "").strip() or None,
            "last_error": str(hub.get("last_error") or "").strip() or None,
            "last_close_reason": str(hub.get("last_close_reason") or "").strip() or None,
        }

    @staticmethod
    def _hub_root_channel_ready(state: dict[str, Any]) -> bool:
        return (
            str(state.get("root_control_status") or "").strip().lower() == "ready"
            and str(state.get("hub_root_status") or "").strip().lower() == "ready"
            and str(state.get("route_status") or "").strip().lower() == "ready"
            and str(state.get("hub_root_browser_status") or "").strip().lower() == "ready"
        )

    @staticmethod
    def _hub_root_channel_down(state: dict[str, Any]) -> bool:
        return any(
            str(state.get(key) or "").strip().lower() == "down"
            for key in ("root_control_status", "hub_root_status", "hub_root_state")
        )

    @staticmethod
    def _hub_root_route_degraded(state: dict[str, Any]) -> bool:
        degraded_states = {"down", "degraded", "unstable", "flapping"}
        return any(
            str(state.get(key) or "").strip().lower() in degraded_states
            for key in ("route_status", "hub_root_browser_status", "hub_root_browser_state")
        )

    @staticmethod
    def _root_probe_reports_hub_root_unready(state: dict[str, Any]) -> bool:
        degraded_states = {"down", "degraded", "unstable", "flapping"}
        return any(
            str(state.get(key) or "").strip().lower() in degraded_states
            for key in (
                "root_control_status",
                "route_status",
                "hub_root_status",
                "hub_root_state",
                "hub_root_browser_status",
                "hub_root_browser_state",
            )
        )

    def _append_hub_root_watchdog_event(self, payload: dict[str, Any]) -> None:
        event = {
            "ts": time.time(),
            "runtime_url": self.runtime_base_url,
            **payload,
        }
        try:
            _append_jsonl(_supervisor_hub_root_watchdog_log_path(), _compact_watchdog_event(event))
        except Exception:
            _LOG.debug("failed to append hub-root watchdog event", exc_info=True)

    def _append_member_hub_watchdog_event(self, payload: dict[str, Any]) -> None:
        event = {
            "ts": time.time(),
            "runtime_url": self.runtime_base_url,
            **payload,
        }
        try:
            _append_jsonl(_supervisor_member_hub_watchdog_log_path(), _compact_watchdog_event(event))
        except Exception:
            _LOG.debug("failed to append member-hub watchdog event", exc_info=True)

    @staticmethod
    def _core_update_reconcile_key(payload: dict[str, Any] | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        if isinstance(result.get("result"), dict):
            result = result.get("result") or result
        release = result.get("release") if isinstance(result.get("release"), dict) else {}
        subnet_state = result.get("subnet_state") if isinstance(result.get("subnet_state"), dict) else {}
        branch = str(result.get("branch") or release.get("branch") or subnet_state.get("current_branch") or "").strip()
        head_sha = str(release.get("head_sha") or "").strip()
        current_commit = str(subnet_state.get("current_commit") or "").strip()
        parts = [part for part in (branch, head_sha, current_commit) if part]
        return ":".join(parts) if parts else None

    async def _maybe_reconcile_hub_core_update(
        self,
        *,
        trigger: str,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not _post_recovery_core_update_reconcile_enabled():
            return None
        role = str(self._managed_transition_role or self._sidecar_role() or "").strip().lower()
        if role != "hub":
            return None
        now = time.time()
        cooldown = _post_recovery_core_update_reconcile_cooldown_sec()
        last_at = self._hub_root_post_recovery_reconcile_last_at
        if last_at is not None and (now - float(last_at)) < cooldown:
            return {
                "ok": True,
                "accepted": False,
                "skipped": True,
                "reason": "cooldown",
                "cooldown_sec": cooldown,
                "last_at": last_at,
            }
        payload = {
            "reason": str(trigger or "supervisor.hub_root.recovered")[:128],
            "countdown_sec": _post_recovery_core_update_reconcile_countdown_sec(),
        }
        record: dict[str, Any]
        try:
            result = await asyncio.to_thread(
                self._runtime_request_json,
                path="/api/admin/update/reconcile",
                method="POST",
                payload=payload,
                timeout=30.0,
            )
            record = {
                "ok": True,
                "trigger": str(trigger or ""),
                "requested_at": now,
                "result": result,
                "verification": verification if isinstance(verification, dict) else None,
            }
        except Exception as exc:
            record = {
                "ok": False,
                "trigger": str(trigger or ""),
                "requested_at": now,
                "error": f"{type(exc).__name__}: {exc}",
                "verification": verification if isinstance(verification, dict) else None,
            }
            _LOG.warning("core update reconcile failed: %s: %s", type(exc).__name__, exc)
        self._hub_root_post_recovery_reconcile_last_at = now
        self._hub_root_post_recovery_reconcile_last_result = _compact_watchdog_last_result(record)
        self._hub_root_post_recovery_reconcile_last_key = self._core_update_reconcile_key(record)
        return dict(self._hub_root_post_recovery_reconcile_last_result or record)

    async def _maybe_reconcile_hub_core_update_after_recovery(
        self,
        *,
        trigger: str,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return await self._maybe_reconcile_hub_core_update(trigger=trigger, verification=verification)

    async def _maybe_reconcile_hub_core_update_periodic(self, runtime: dict[str, Any]) -> dict[str, Any] | None:
        if not _periodic_core_update_reconcile_enabled():
            return None
        role = str(self._managed_transition_role or self._sidecar_role() or "").strip().lower()
        if role != "hub":
            return None
        now = time.time()
        last_at = self._hub_root_post_recovery_reconcile_last_at
        interval = _periodic_core_update_reconcile_interval_sec()
        if last_at is not None and (now - float(last_at)) < interval:
            return None
        channel_state = self._hub_root_channel_state(runtime)
        channel_ready = self._hub_root_channel_ready(channel_state)
        root_probe = (
            self._hub_root_root_probe_last_result
            if isinstance(self._hub_root_root_probe_last_result, dict)
            else {}
        )
        root_probe_state = str(root_probe.get("state") or "").strip().lower()
        verification_state = (
            "ready"
            if channel_ready
            else ("root_perspective_ready" if root_probe_state == "ready" else "local_runtime_api_ready")
        )
        verification = {
            "ok": True,
            "state": verification_state,
            "source": "supervisor.periodic_core_update_reconcile.direct_root_mtls",
            "channel": dict(channel_state),
            "root_perspective_probe": dict(root_probe),
        }
        result = await self._maybe_reconcile_hub_core_update(
            trigger="supervisor.hub_root.periodic_core_update_reconcile",
            verification=verification,
        )
        if result is not None:
            self._append_hub_root_watchdog_event(
                {
                    "event": "core_update_periodic_reconcile",
                    "state": str(result.get("state") or result.get("reason") or "checked"),
                    "result": result,
                }
            )
        return result

    async def _maybe_refresh_member_hub_after_recovery(
        self,
        *,
        trigger: str,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not _post_recovery_member_hub_refresh_enabled():
            return None
        role = str(self._managed_transition_role or self._sidecar_role() or "").strip().lower()
        if role != "member":
            return None
        now = time.time()
        cooldown = _post_recovery_member_hub_refresh_cooldown_sec()
        last_at = self._member_hub_post_recovery_refresh_last_at
        if last_at is not None and (now - float(last_at)) < cooldown:
            return {
                "ok": True,
                "accepted": False,
                "skipped": True,
                "reason": "cooldown",
                "cooldown_sec": cooldown,
                "last_at": last_at,
            }
        payload = {"reason": str(trigger or "supervisor.member_hub.recovered")[:128]}
        record: dict[str, Any]
        try:
            result = await asyncio.to_thread(
                self._runtime_request_json,
                path="/api/node/member-hub/refresh",
                method="POST",
                payload=payload,
                timeout=5.0,
            )
            record = {
                "ok": True,
                "trigger": str(trigger or ""),
                "requested_at": now,
                "result": result,
                "verification": verification if isinstance(verification, dict) else None,
            }
        except Exception as exc:
            record = {
                "ok": False,
                "trigger": str(trigger or ""),
                "requested_at": now,
                "error": f"{type(exc).__name__}: {exc}",
                "verification": verification if isinstance(verification, dict) else None,
            }
            _LOG.warning("post-recovery member-hub refresh failed: %s: %s", type(exc).__name__, exc)
        self._member_hub_post_recovery_refresh_last_at = now
        self._member_hub_post_recovery_refresh_last_result = _compact_watchdog_last_result(record)
        return dict(self._member_hub_post_recovery_refresh_last_result or record)

    def _hub_root_watchdog_decision(
        self,
        reliability_payload: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        return self._recovery_policy.hub_root_watchdog_decision(
            self,
            self._recovery_operations(),
            reliability_payload,
            now=now,
        )

    async def _verify_hub_root_watchdog_recovery(self, *, timeout_sec: float | None = None) -> dict[str, Any]:
        timeout = _hub_root_watchdog_verify_timeout_sec() if timeout_sec is None else max(0.0, float(timeout_sec))
        interval = _hub_root_watchdog_verify_interval_sec()
        deadline = time.time() + timeout
        attempts = 0
        last_state: dict[str, Any] = {}
        while True:
            attempts += 1
            runtime = await self._runtime_reliability_payload_async()
            last_state = self._hub_root_channel_state(runtime)
            if self._hub_root_channel_ready(last_state):
                return {
                    "ok": True,
                    "state": "ready",
                    "attempts": attempts,
                    "timeout_sec": timeout,
                    "channel": last_state,
                }
            if timeout <= 0.0 or time.time() >= deadline:
                return {
                    "ok": False,
                    "state": "not_ready",
                    "attempts": attempts,
                    "timeout_sec": timeout,
                    "channel": last_state,
                }
            await asyncio.sleep(interval)

    async def _maybe_reconnect_hub_root_from_watchdog(self) -> None:
        if not _hub_root_watchdog_enabled() or self._stopping or not self._desired_running:
            return
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        await self._maybe_probe_hub_root_from_root()
        runtime = await self._runtime_reliability_payload_async()
        if not runtime:
            return
        previous_state = str(self._hub_root_watchdog_last_state or "").strip().lower()
        decision = self._hub_root_watchdog_decision(runtime, now=time.time())
        if decision is None:
            if self._hub_root_watchdog_last_state == "ready" and previous_state not in {"", "ready", "not_applicable", "disabled"}:
                await self._maybe_reconcile_hub_core_update_after_recovery(
                    trigger="supervisor.hub_root.self_recovered",
                    verification={"ok": True, "state": "ready", "source": "watchdog_ready_edge"},
                )
                self._persist_runtime_state()
            periodic_reconcile = await self._maybe_reconcile_hub_core_update_periodic(runtime)
            if periodic_reconcile is not None:
                self._persist_runtime_state()
            return
        self._hub_root_watchdog_last_state = "reconnect_requested"
        self._hub_root_watchdog_last_reason = str(decision.get("message") or decision.get("reason") or "")
        self._hub_root_watchdog_last_reconnect_at = time.time()
        self._hub_root_watchdog_reconnect_total += 1
        action = str(decision.get("action") or "runtime_reconnect")
        try:
            if action == "runtime_route_reset":
                result = await asyncio.to_thread(
                    self._runtime_request_json,
                    path="/api/node/hub-root/route-reset",
                    method="POST",
                    payload={
                        "reason": "supervisor_route_watchdog",
                        "notify_browser": True,
                    },
                    timeout=5.0,
                )
            else:
                result = await asyncio.to_thread(
                    self._runtime_request_json,
                    path="/api/node/hub-root/reconnect",
                    method="POST",
                    payload={},
                    timeout=5.0,
                )
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            _LOG.warning("hub-root watchdog reconnect request failed: %s: %s", type(exc).__name__, exc)
        verification = await self._verify_hub_root_watchdog_recovery()
        post_recovery_reconcile = None
        if bool(verification.get("ok")):
            post_recovery_reconcile = await self._maybe_reconcile_hub_core_update_after_recovery(
                trigger="supervisor.hub_root.recovered",
                verification=verification,
            )
        self._hub_root_watchdog_last_result = _compact_watchdog_last_result({
            "requested_at": self._hub_root_watchdog_last_reconnect_at,
            "action": action,
            "decision": decision,
            "result": result,
            "verification": verification,
            "post_recovery_core_update_reconcile": post_recovery_reconcile,
        })
        self._hub_root_watchdog_last_state = "ready" if bool(verification.get("ok")) else "recovery_failed"
        self._hub_root_watchdog_last_reason = (
            "hub-root channel recovered"
            if bool(verification.get("ok"))
            else "hub-root channel did not recover after watchdog action"
        )
        self._append_hub_root_watchdog_event(
            {
                "event": "recovery_attempt",
                "action": action,
                "transport_owner": decision.get("transport_owner"),
                "decision": decision,
                "result": result,
                "verification": verification,
                "post_recovery_core_update_reconcile": post_recovery_reconcile,
            }
        )
        self._persist_runtime_state()

    def _member_hub_watchdog_decision(
        self,
        reliability_payload: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        return self._recovery_policy.member_hub_watchdog_decision(
            self,
            self._recovery_operations(),
            reliability_payload,
            now=now,
        )

    async def _verify_member_hub_watchdog_recovery(self, *, timeout_sec: float | None = None) -> dict[str, Any]:
        timeout = _member_hub_watchdog_verify_timeout_sec() if timeout_sec is None else max(0.0, float(timeout_sec))
        interval = _member_hub_watchdog_verify_interval_sec()
        deadline = time.time() + timeout
        attempts = 0
        last_state: dict[str, Any] = {}
        while True:
            attempts += 1
            runtime = await self._runtime_reliability_payload_async()
            last_state = self._member_hub_channel_state(runtime)
            if bool(last_state.get("connected")):
                return {
                    "ok": True,
                    "state": "ready",
                    "attempts": attempts,
                    "timeout_sec": timeout,
                    "channel": last_state,
                }
            if timeout <= 0.0 or time.time() >= deadline:
                return {
                    "ok": False,
                    "state": "not_ready",
                    "attempts": attempts,
                    "timeout_sec": timeout,
                    "channel": last_state,
                }
            await asyncio.sleep(interval)

    async def _maybe_reconnect_member_hub_from_watchdog(self) -> None:
        if not _member_hub_watchdog_enabled() or self._stopping or not self._desired_running:
            return
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        runtime = await self._runtime_reliability_payload_async()
        if not runtime:
            return
        previous_state = str(self._member_hub_watchdog_last_state or "").strip().lower()
        decision = self._member_hub_watchdog_decision(runtime, now=time.time())
        if decision is None:
            if self._member_hub_watchdog_last_state == "ready" and previous_state not in {"", "ready", "not_applicable", "disabled"}:
                await self._maybe_refresh_member_hub_after_recovery(
                    trigger="supervisor.member_hub.self_recovered",
                    verification={"ok": True, "state": "ready", "source": "watchdog_ready_edge"},
                )
                self._persist_runtime_state()
            return
        self._member_hub_watchdog_last_state = "reconnect_requested"
        self._member_hub_watchdog_last_reason = str(decision.get("message") or decision.get("reason") or "")
        self._member_hub_watchdog_last_reconnect_at = time.time()
        self._member_hub_watchdog_reconnect_total += 1
        try:
            result = await asyncio.to_thread(
                self._runtime_request_json,
                path="/api/node/member-hub/reconnect",
                method="POST",
                payload={},
                timeout=5.0,
            )
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            _LOG.warning("member-hub watchdog reconnect request failed: %s: %s", type(exc).__name__, exc)
        verification = await self._verify_member_hub_watchdog_recovery()
        post_recovery_refresh = None
        if bool(verification.get("ok")):
            post_recovery_refresh = await self._maybe_refresh_member_hub_after_recovery(
                trigger="supervisor.member_hub.recovered",
                verification=verification,
            )
        self._member_hub_watchdog_last_result = _compact_watchdog_last_result({
            "requested_at": self._member_hub_watchdog_last_reconnect_at,
            "action": "runtime_reconnect",
            "decision": decision,
            "result": result,
            "verification": verification,
            "post_recovery_refresh": post_recovery_refresh,
        })
        self._member_hub_watchdog_last_state = "ready" if bool(verification.get("ok")) else "recovery_failed"
        self._member_hub_watchdog_last_reason = (
            "member-hub channel recovered"
            if bool(verification.get("ok"))
            else "member-hub channel did not recover after watchdog action"
        )
        self._append_member_hub_watchdog_event(
            {
                "event": "recovery_attempt",
                "action": "runtime_reconnect",
                "transport_owner": decision.get("transport_owner"),
                "decision": decision,
                "result": result,
                "verification": verification,
                "post_recovery_refresh": post_recovery_refresh,
            }
        )
        self._persist_runtime_state()

    def _required_upstream_link_decision(
        self,
        runtime: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        node = runtime.get("node") if isinstance(runtime.get("node"), dict) else {}
        role = str(node.get("role") or self._managed_transition_role or self._sidecar_role() or "").strip().lower()
        if role == "member":
            return self._member_hub_watchdog_decision(runtime, now=now)
        return self._hub_root_watchdog_decision(runtime, now=now)

    async def _maybe_maintain_required_upstream_link(self) -> None:
        now = time.time()
        last_poll_at = self._required_upstream_watchdog_last_poll_at
        if (
            last_poll_at is not None
            and now - float(last_poll_at) < _required_upstream_watchdog_poll_interval_sec()
        ):
            return
        self._required_upstream_watchdog_last_poll_at = now
        sidecar_role = str(self._sidecar_role() or "").strip().lower()
        transition_role = str(self._managed_transition_role or "").strip().lower()
        role = transition_role if transition_role in {"hub", "member"} else sidecar_role
        if role == "member":
            await self._maybe_reconnect_member_hub_from_watchdog()
        else:
            await self._maybe_reconnect_hub_root_from_watchdog()

    @property
    def active_runtime_port(self) -> int:
        return self.slot_runtime_port(active_slot())

    @property
    def runtime_base_url(self) -> str:
        return self.slot_runtime_base_url(active_slot())

    def slot_runtime_port(self, slot: str | None) -> int:
        return _slot_runtime_port(slot, self.runtime_port)

    def slot_runtime_base_url(self, slot: str | None) -> str:
        return f"http://{self.runtime_host}:{self.slot_runtime_port(slot)}"

    def slot_runtime_urls(self) -> dict[str, str]:
        ports = _slot_runtime_ports(self.runtime_port)
        return {slot_name: f"http://{self.runtime_host}:{port}" for slot_name, port in ports.items()}

    def _managed_proc_base_url(self, proc: subprocess.Popen[Any] | None = None) -> str:
        if proc is None or proc is self._proc:
            base_url = str(self._managed_runtime_base_url or "").strip()
            if base_url:
                return base_url
            if self._managed_runtime_port is not None:
                return f"http://{self.runtime_host}:{int(self._managed_runtime_port)}"
            managed_slot = str(self._managed_slot or "").strip().upper() or None
            if managed_slot:
                return self.slot_runtime_base_url(managed_slot)
        return self.runtime_base_url

    def _candidate_transition_slot(
        self,
        *,
        current_slot: str | None,
        update_status: dict[str, Any] | None,
        update_attempt: dict[str, Any] | None,
    ) -> str | None:
        state = str((update_status or {}).get("state") or "").strip().lower()
        phase = str((update_status or {}).get("phase") or "").strip().lower()
        attempt_state = str((update_attempt or {}).get("state") or "").strip().lower()
        current_slot_name = str(current_slot or "").strip().upper()
        transition_active = state in {
            "planned",
            "preparing",
            "countdown",
            "draining",
            "stopping",
            "restarting",
            "applying",
            "validated",
        } or attempt_state in {"planned", "active"}
        if not transition_active and attempt_state == "awaiting_root_restart":
            transition_active = _subsequent_transition_request(update_attempt) is not None
        if not transition_active and state == "succeeded" and phase == "root_promoted":
            transition_active = False
        if not transition_active:
            return None
        for source in (update_status or {}, update_attempt or {}):
            target_slot = str(source.get("target_slot") or "").strip().upper()
            if target_slot in {"A", "B"} and target_slot != current_slot_name:
                return target_slot
        if transition_active:
            target_slot = choose_inactive_slot()
            if target_slot and target_slot != current_slot_name:
                return target_slot
        return None

    def _warm_switch_state(
        self,
        *,
        current_slot: str | None,
        update_status: dict[str, Any] | None,
        update_attempt: dict[str, Any] | None,
        managed_pid: int | None,
    ) -> dict[str, Any]:
        candidate_slot = self._candidate_transition_slot(
            current_slot=current_slot,
            update_status=update_status,
            update_attempt=update_attempt,
        )
        slot_ports = _slot_runtime_ports(self.runtime_port)
        active_port = self.slot_runtime_port(current_slot)
        candidate_port = self.slot_runtime_port(candidate_slot)
        supported = bool(candidate_slot) and candidate_port != active_port
        enabled = _warm_switch_enabled()
        allowed = False
        reason = "warm switch is disabled"
        available_bytes = None
        estimated_candidate_bytes = None
        reserve_bytes = _warm_switch_min_available_bytes()
        current_rss_bytes = None
        current_family_rss_bytes = None
        if not candidate_slot:
            reason = "no transition candidate slot"
        elif not supported:
            reason = "candidate runtime uses the same port as the active slot"
        elif not enabled:
            reason = "warm switch is disabled"
        elif psutil is None:
            reason = "psutil unavailable; cannot evaluate memory gate"
        else:
            try:
                vm = psutil.virtual_memory()
                available_bytes = int(getattr(vm, "available", 0) or 0)
            except Exception:
                available_bytes = None
            if managed_pid:
                current_rss_bytes, current_family_rss_bytes = _process_family_rss_bytes(managed_pid)
            estimated_candidate_bytes = max(
                _warm_switch_min_candidate_bytes(),
                int(float(current_family_rss_bytes or current_rss_bytes or 0) * _warm_switch_rss_multiplier()),
            )
            if available_bytes is None or available_bytes <= 0:
                reason = "available memory is unknown"
            elif available_bytes < estimated_candidate_bytes + reserve_bytes:
                reason = "insufficient memory for warm switch; using stop-and-switch"
            else:
                allowed = True
                reason = "warm switch admitted"
        transition_mode = None
        if candidate_slot:
            transition_mode = "warm_switch" if supported and enabled and allowed else "stop_and_switch"
        return {
            "candidate_slot": candidate_slot,
            "candidate_runtime_port": candidate_port if candidate_slot else None,
            "candidate_runtime_url": self.slot_runtime_base_url(candidate_slot) if candidate_slot else None,
            "candidate_transition_role": "candidate" if candidate_slot else None,
            "transition_mode": transition_mode,
            "warm_switch_enabled": enabled,
            "warm_switch_supported": supported,
            "warm_switch_allowed": allowed if candidate_slot else None,
            "warm_switch_reason": reason if candidate_slot else None,
            "warm_switch_memory": {
                "available_bytes": available_bytes,
                "current_rss_bytes": current_family_rss_bytes or current_rss_bytes,
                "current_process_rss_bytes": current_rss_bytes,
                "current_family_rss_bytes": current_family_rss_bytes,
                "estimated_candidate_bytes": estimated_candidate_bytes,
                "reserve_bytes": reserve_bytes,
            },
            "slot_ports": slot_ports,
            "slot_urls": self.slot_runtime_urls(),
        }

    def _candidate_memory_guard_snapshot(self, runtime_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else {}
        candidate_pid = snapshot.get("candidate_managed_pid")
        candidate_alive = bool(snapshot.get("candidate_managed_alive"))
        if not candidate_pid:
            candidate_managed = _proc_details(self._candidate_proc, cwd_hint=self._candidate_runtime_cwd)
            candidate_pid = candidate_managed.get("managed_pid")
            candidate_alive = bool(candidate_managed.get("managed_alive"))
        try:
            normalized_pid = int(candidate_pid or 0)
        except Exception:
            normalized_pid = 0
        process_rss_bytes = None
        family_rss_bytes = None
        if normalized_pid > 0:
            process_rss_bytes, family_rss_bytes = _process_family_rss_bytes(normalized_pid)
        available_bytes = _available_memory_bytes()
        max_candidate_rss_bytes = _warm_switch_max_candidate_rss_bytes()
        reserve_bytes = _warm_switch_min_available_bytes()
        allowed = True
        reason = None
        if not candidate_alive or normalized_pid <= 0:
            reason = "candidate_not_running"
        elif max_candidate_rss_bytes > 0 and (family_rss_bytes or process_rss_bytes or 0) >= max_candidate_rss_bytes:
            allowed = False
            reason = "candidate_rss_threshold"
        elif available_bytes is not None and available_bytes < reserve_bytes:
            allowed = False
            reason = "available_memory_reserve"
        return {
            "allowed": allowed,
            "reason": reason,
            "candidate_pid": normalized_pid or None,
            "candidate_process_rss_bytes": process_rss_bytes,
            "candidate_family_rss_bytes": family_rss_bytes,
            "available_memory_bytes": available_bytes,
            "max_candidate_rss_bytes": max_candidate_rss_bytes,
            "reserve_bytes": reserve_bytes,
        }

    @staticmethod
    def _candidate_memory_guard_message(guard: dict[str, Any]) -> str:
        reason = str(guard.get("reason") or "candidate_memory_guard").strip()
        family = guard.get("candidate_family_rss_bytes")
        process = guard.get("candidate_process_rss_bytes")
        available = guard.get("available_memory_bytes")
        limit = guard.get("max_candidate_rss_bytes")
        reserve = guard.get("reserve_bytes")
        return (
            "candidate memory gate blocked warm switch"
            f" reason={reason}"
            f" family_rss_bytes={family}"
            f" process_rss_bytes={process}"
            f" max_candidate_rss_bytes={limit}"
            f" available_memory_bytes={available}"
            f" reserve_bytes={reserve}"
        )

    def _runtime_env(
        self,
        *,
        slot: str | None,
        slot_dir: str,
        slot_port: int,
        transition_role: str,
        runtime_instance_id: str,
        profile_mode: str = "normal",
        profile_session_id: str | None = None,
        profile_trigger: str | None = None,
        skip_pending_update: bool = False,
    ) -> dict[str, str]:
        env = dict(os.environ)
        env["ADAOS_SUPERVISOR_ENABLED"] = "1"
        env["ADAOS_SUPERVISOR_URL"] = _supervisor_base_url()
        env["ADAOS_SUPERVISOR_HOST"] = _supervisor_host()
        env["ADAOS_SUPERVISOR_PORT"] = str(_supervisor_port())
        env["ADAOS_RUNTIME_INSTANCE_ID"] = str(runtime_instance_id)
        env["ADAOS_RUNTIME_TRANSITION_ROLE"] = str(transition_role or "active")
        env["ADAOS_RUNTIME_HOST"] = self.runtime_host
        env["ADAOS_RUNTIME_PORT"] = str(slot_port)
        if self.token:
            env["ADAOS_TOKEN"] = self.token
        if slot:
            env["ADAOS_ACTIVE_CORE_SLOT"] = slot
            env["ADAOS_ACTIVE_CORE_SLOT_DIR"] = slot_dir
        env["ADAOS_SUPERVISOR_PROFILE_MODE"] = str(profile_mode or "normal")
        if profile_session_id:
            env["ADAOS_SUPERVISOR_PROFILE_SESSION_ID"] = str(profile_session_id)
        else:
            env.pop("ADAOS_SUPERVISOR_PROFILE_SESSION_ID", None)
        if profile_trigger:
            env["ADAOS_SUPERVISOR_PROFILE_TRIGGER"] = str(profile_trigger)
        else:
            env.pop("ADAOS_SUPERVISOR_PROFILE_TRIGGER", None)
        if skip_pending_update:
            env[SKIP_PENDING_CORE_UPDATE_ENV] = "1"
        return env

    def _runtime_launch_spec(
        self,
        *,
        slot: str | None = None,
        transition_role: str = "active",
        runtime_instance_id: str | None = None,
        profile_mode: str | None = None,
        profile_session_id: str | None = None,
        profile_trigger: str | None = None,
        skip_pending_update: bool = False,
    ) -> tuple[list[str] | None, str | None, dict[str, str], str | None, str, str]:
        return self._process_supervisor.runtime_launch_spec(
            self,
            self._process_operations(),
            slot=slot,
            transition_role=transition_role,
            runtime_instance_id=runtime_instance_id,
            profile_mode=profile_mode,
            profile_session_id=profile_session_id,
            profile_trigger=profile_trigger,
            skip_pending_update=skip_pending_update,
        )

    def _runtime_state_payload(self, *, runtime_api_timeout: float = 0.75) -> dict[str, Any]:
        return self._status_service.runtime_state_payload(
            self,
            self._status_operations(),
            runtime_api_timeout=runtime_api_timeout,
        )

    def _publish_status_snapshot(
        self,
        payload: dict[str, Any],
        *,
        update_attempt: dict[str, Any] | None,
        reason: str,
        persisted_at: float | None = None,
    ) -> None:
        observed_at = time.time()
        compact = self._status_service.compact_runtime_read_model(payload)
        compact["update_attempt"] = self._status_service.compact_update_attempt(update_attempt)
        compact["updated_at"] = observed_at
        with self._status_snapshot_lock:
            self._status_snapshot = copy.deepcopy(compact)
            self._status_snapshot_generation += 1
            self._status_snapshot_observed_at = observed_at
            self._status_snapshot_reason = str(reason or "state_transition")
            if persisted_at is not None:
                self._status_durable_updated_at = float(persisted_at)

    def _publish_status_observation(self, *, reason: str, fields: dict[str, Any]) -> None:
        observed_at = time.time()
        with self._status_snapshot_lock:
            for key, value in fields.items():
                self._status_snapshot[str(key)] = copy.deepcopy(value)
            self._status_snapshot["updated_at"] = observed_at
            self._status_snapshot_generation += 1
            self._status_snapshot_observed_at = observed_at
            self._status_snapshot_reason = str(reason or "runtime_observation")

    def _publish_sidecar_health_observation(self, process: dict[str, Any] | None) -> None:
        observed_at = time.time()
        process_source = process if isinstance(process, dict) else {}
        process_fields = {
            key: process_source.get(key)
            for key in (
                "host",
                "port",
                "control_port",
                "local_url",
                "managed_pid",
                "managed_alive",
                "managed_exit_code",
                "listener_pid",
                "listener_running",
                "listener_liveness_basis",
                "listener_process_relationship",
                "listener_matches_managed",
                "adopted_listener",
                "enablement_policy",
            )
            if key in process_source
        }
        health = {
            "last_probe_at": self._sidecar_last_probe_at,
            "last_probe_ok": self._sidecar_last_probe_ok,
            "last_probe_error": self._sidecar_last_probe_error,
            "consecutive_failures": int(self._sidecar_consecutive_probe_failures),
        }
        with self._status_snapshot_lock:
            sidecar = self._status_snapshot.get("sidecar")
            sidecar = copy.deepcopy(sidecar) if isinstance(sidecar, dict) else {}
            existing_process = sidecar.get("process") if isinstance(sidecar.get("process"), dict) else {}
            sidecar["process"] = {**existing_process, **process_fields}
            sidecar["health"] = health
            self._status_snapshot["sidecar"] = sidecar
            self._status_snapshot["updated_at"] = observed_at
            self._status_snapshot_generation += 1
            self._status_snapshot_observed_at = observed_at
            self._status_snapshot_reason = "sidecar_health_observation"

    def _refresh_status_snapshot(
        self,
        *,
        reason: str,
        runtime_api_timeout: float = 0.75,
        persist: bool = False,
    ) -> None:
        payload = self._runtime_state_payload(runtime_api_timeout=runtime_api_timeout)
        update_attempt = _observed_update_attempt(
            _read_update_attempt(),
            read_core_update_status(),
        )
        persisted_at = None
        if persist:
            _write_json(_supervisor_runtime_state_path(), payload)
            persisted_at = time.time()
        self._publish_status_snapshot(
            payload,
            update_attempt=update_attempt,
            reason=reason,
            persisted_at=persisted_at,
        )

    def _managed_runtime_slot_expectations(
        self,
        *,
        manifest: dict[str, Any] | None,
        managed_executable: str | None,
        managed_cwd: str | None,
    ) -> tuple[str | None, str | None, bool | None]:
        expected_executable = None
        expected_cwd = None
        matches_active_slot = None
        if isinstance(manifest, dict):
            argv = manifest.get("argv")
            if isinstance(argv, list) and argv:
                expected_executable = str(argv[0] or "").strip() or None
            expected_cwd = str(manifest.get("cwd") or "").strip() or None
        if expected_executable or expected_cwd:
            matches_active_slot = True
            if expected_executable and str(managed_executable or "").strip() != expected_executable:
                matches_active_slot = False
            if expected_cwd and str(managed_cwd or "").strip() != expected_cwd:
                matches_active_slot = False
        return expected_executable, expected_cwd, matches_active_slot

    def _verified_adopted_runtime_matches_active_slot(
        self,
        *,
        current_slot: str | None,
    ) -> bool:
        return bool(
            self._managed_runtime_api_identity_verified
            and current_slot
            and str(self._managed_slot or "").strip().upper() == str(current_slot).strip().upper()
            and str(self._managed_transition_role or "").strip().lower() == "active"
            and str(self._managed_runtime_instance_id or "").strip()
        )

    def _refresh_managed_runtime_api_identity(
        self,
        *,
        current_slot: str | None,
        probe: dict[str, Any],
    ) -> bool | None:
        identity = probe.get("runtime") if isinstance(probe.get("runtime"), dict) else {}
        reported_slot = str(identity.get("slot") or "").strip().upper()
        reported_role = str(identity.get("transition_role") or "").strip().lower()
        reported_instance_id = str(identity.get("runtime_instance_id") or "").strip()
        expected_instance_id = str(self._managed_runtime_instance_id or "").strip()
        if not reported_slot or not reported_role or not reported_instance_id:
            return None

        self._managed_runtime_api_identity_observed_at = time.time()
        self._managed_runtime_api_identity = {
            "slot": reported_slot,
            "transition_role": reported_role,
            "runtime_instance_id": reported_instance_id,
        }
        if not current_slot or not expected_instance_id:
            self._managed_runtime_api_identity_verified = False
            return None

        matches = bool(
            reported_slot == str(current_slot).strip().upper()
            and reported_role == "active"
            and reported_instance_id == expected_instance_id
        )
        self._managed_runtime_api_identity_verified = matches
        return matches

    def _persist_runtime_state(self) -> None:
        with contextlib.suppress(Exception):
            self._refresh_status_snapshot(
                reason="durable_state_transition",
                runtime_api_timeout=0.2,
                persist=True,
            )
        with contextlib.suppress(Exception):
            write_memory_runtime_state(self._memory_runtime_state_payload())

    def _memory_runtime_state_payload(self) -> dict[str, Any]:
        return self._memory_profiling.runtime_state_payload(
            self,
            self._memory_operations(),
        )

    def _runtime_memory_diagnostics_payload(self) -> dict[str, Any]:
        try:
            headers: dict[str, str] = {}
            if self.token:
                headers["X-AdaOS-Token"] = self.token
                headers["Authorization"] = f"Bearer {self.token}"
            response = requests.get(
                self.runtime_base_url + "/api/node/memory/diagnostics",
                headers=headers,
                timeout=3.0,
            )
            payload = response.json()
            if isinstance(payload, dict):
                return {
                    "ok": response.ok,
                    "status_code": int(response.status_code),
                    "payload": payload,
                }
            return {"ok": response.ok, "status_code": int(response.status_code), "payload_type": type(payload).__name__}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _recent_reconnect_markers(self) -> dict[str, Any]:
        log_lines = bounded_text_tail_lines(
            realtime_sidecar_log_path(),
            limit=200,
            max_bytes=512 * 1024,
            max_line_chars=2048,
        )
        marker_tokens = (
            "session open",
            "session close",
            "remote connect",
            "remote disconnect",
            "unexpected eof",
            "superseding previous local nats client",
            "quarantine",
            "serve start",
        )
        markers = [
            line
            for line in log_lines
            if any(token in line.lower() for token in marker_tokens)
        ][-40:]
        diag_tail = bounded_jsonl_tail(
            realtime_sidecar_diag_path(),
            limit=20,
            max_bytes=512 * 1024,
            max_line_chars=64 * 1024,
        )
        return {
            "sidecar_log_markers": markers,
            "sidecar_diag_tail": diag_tail,
        }

    def _capture_runtime_stop_evidence(
        self,
        *,
        reason: str,
        stage: str,
        proc: subprocess.Popen[Any] | None = None,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_proc = proc or self._proc
        pid: int | None = None
        try:
            pid = int(getattr(target_proc, "pid", 0) or 0) or None
        except Exception:
            pid = None
        process_rss_bytes, family_rss_bytes = _process_family_rss_bytes(pid)
        attribution = _runtime_memory_attribution_snapshot(
            pid,
            process_rss_bytes=process_rss_bytes,
            family_rss_bytes=family_rss_bytes,
        )
        log_paths = [
            realtime_sidecar_log_path(),
            realtime_sidecar_diag_path(),
            supervisor_memory_telemetry_path(),
        ]
        for suffix in range(1, 6):
            log_paths.append(realtime_sidecar_log_path().with_name(f"{realtime_sidecar_log_path().name}.{suffix}"))
            log_paths.append(realtime_sidecar_diag_path().with_name(f"{realtime_sidecar_diag_path().name}.{suffix}"))
        payload = {
            "captured_at": time.time(),
            "reason": str(reason or ""),
            "stage": str(stage or ""),
            "pid": pid,
            "runtime_instance_id": self._managed_runtime_instance_id,
            "transition_role": self._managed_transition_role,
            "memory": attribution,
            "smaps_rollup": _linux_smaps_rollup_snapshot(pid),
            "runtime_memory_diagnostics": self._runtime_memory_diagnostics_payload(),
            "process": _linux_process_state_snapshot(pid),
            "log_files": path_size_snapshot(log_paths),
            "recent_reconnect_markers": self._recent_reconnect_markers(),
            "telemetry_tail": read_memory_telemetry_tail(limit=50),
            "decision": decision or {},
        }
        evidence_dir = supervisor_memory_evidence_dir()
        file_name = (
            f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
            f"-{_safe_evidence_label(stage)}"
            f"-pid{pid or 'unknown'}"
            f"-{_safe_evidence_label(reason)}.json"
        )
        path = evidence_dir / file_name
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            payload["evidence_path"] = str(path)
            _LOG.warning(
                "captured runtime stop evidence stage=%s reason=%s pid=%s path=%s",
                stage,
                reason,
                pid,
                path,
            )
        except Exception as exc:
            payload["evidence_error"] = f"{type(exc).__name__}: {exc}"
            _LOG.warning("failed to capture runtime stop evidence", exc_info=True)
        return payload

    def _record_runtime_self_heal_restart(self, decision: dict[str, Any]) -> dict[str, Any]:
        payload = dict(decision or {})
        reason = str(payload.get("reason") or "supervisor.runtime.unhealthy")
        payload["recorded_at"] = time.time()
        evidence = self._capture_runtime_stop_evidence(
            reason=reason,
            stage="runtime_self_heal_restart",
            decision=payload,
        )
        compact_evidence = _compact_runtime_stop_evidence(evidence)
        payload["pre_restart_evidence"] = compact_evidence
        self._recovery_policy.last_decision = payload
        self._recovery_policy.record_evidence(compact_evidence)
        return payload

    def _runtime_self_heal_status_payload(self) -> dict[str, Any]:
        return {
            "last_decision": dict(self._runtime_self_heal_last_decision or {}),
            "last_evidence": dict(self._runtime_self_heal_last_evidence or {}),
            "unhealthy_since": self._runtime_unhealthy_since,
            "unhealthy_kind": self._runtime_unhealthy_kind,
        }

    async def _maybe_self_heal_runtime(self) -> bool:
        """Restart a live process whose listener or API stayed unhealthy.

        This is deliberately callable both from the normal monitor path and
        from its exception boundary. Auxiliary monitor failures must not keep
        a live-but-unresponsive runtime alive indefinitely.
        """
        async with self._lock:
            restart_decision = await asyncio.to_thread(self._runtime_self_heal_decision)
        if restart_decision is None:
            return False
        recorded_decision = self._record_runtime_self_heal_restart(restart_decision)
        self._last_error = str(recorded_decision.get("message") or "active runtime became unhealthy")
        self._runtime_unhealthy_since = None
        self._runtime_unhealthy_kind = None
        await asyncio.to_thread(self._persist_runtime_state)
        try:
            await self.restart_runtime(reason=str(recorded_decision.get("reason") or "supervisor.runtime.unhealthy"))
        except Exception:
            _LOG.warning("failed to self-heal active runtime", exc_info=True)
        return True

    def _memory_sessions_index_compact(self, *, limit: int = 10) -> dict[str, Any]:
        index = read_memory_session_index()
        items = index.get("sessions") if isinstance(index.get("sessions"), list) else []
        normalized_limit = max(0, min(int(limit or 0), 100))
        if normalized_limit > 0:
            sessions = [dict(item) for item in items[-normalized_limit:] if isinstance(item, dict)]
        else:
            sessions = []
        omitted = max(0, len(items) - len(sessions))
        return {
            "contract_version": str(index.get("contract_version") or "1"),
            "sessions": sessions,
            "total": len(items),
            "returned": len(sessions),
            "omitted": omitted,
            "limit": normalized_limit,
            "compact": True,
            "updated_at": index.get("updated_at"),
        }

    def memory_status(self) -> dict[str, Any]:
        payload = self._memory_runtime_state_payload()
        payload["persisted_state"] = read_memory_runtime_state()
        payload["sessions_index"] = self._memory_sessions_index_compact(limit=10)
        payload["runtime_state_path"] = str(supervisor_memory_runtime_state_path())
        return payload

    def memory_telemetry(self, *, limit: int = 100) -> dict[str, Any]:
        items = read_memory_telemetry_tail(limit=max(1, min(int(limit or 100), 1000)))
        return {
            "ok": True,
            "items": items,
            "total": len(items),
            "telemetry_path": str(supervisor_memory_telemetry_path()),
            "runtime": self._memory_runtime_state_payload(),
        }

    def memory_sessions(self, *, limit: int = 100) -> dict[str, Any]:
        index = read_memory_session_index()
        items = index.get("sessions") if isinstance(index.get("sessions"), list) else []
        normalized_limit = max(1, min(int(limit or 100), 1000))
        returned = [dict(item) for item in items[-normalized_limit:] if isinstance(item, dict)]
        return {
            "ok": True,
            "contract_version": str(index.get("contract_version") or "1"),
            "sessions": returned,
            "total": len(items),
            "returned": len(returned),
            "limit": normalized_limit,
            "updated_at": index.get("updated_at"),
        }

    def memory_incidents(self, *, limit: int = 50) -> dict[str, Any]:
        items = self._memory_session_index_items()
        incidents: list[dict[str, Any]] = []
        for item in reversed(items):
            if not isinstance(item, dict):
                continue
            state = str(item.get("session_state") or "").strip().lower()
            suspected = bool(item.get("suspected_leak"))
            publish_state = str(item.get("publish_state") or "").strip().lower()
            if state not in {"failed", "finished", "stopped"} and not suspected and publish_state != "publish_requested":
                continue
            incidents.append(dict(item))
            if len(incidents) >= max(1, min(int(limit or 50), 200)):
                break
        return {
            "ok": True,
            "incidents": incidents,
            "total": len(incidents),
            "updated_at": read_memory_session_index().get("updated_at"),
        }

    def memory_session(self, session_id: str) -> dict[str, Any] | None:
        token = str(session_id or "").strip()
        if not token:
            return None
        payload = read_memory_session_summary(token)
        if payload is None:
            return None
        artifacts_dir = supervisor_memory_session_artifacts_dir(token)
        return {
            "ok": True,
            "session": payload,
            "operations": read_memory_session_operations(token, limit=100),
            "operations_path": str(supervisor_memory_session_operations_path(token)),
            "artifacts_dir": str(artifacts_dir),
            "telemetry": self._memory_session_telemetry_window(payload, limit=100),
        }

    def memory_session_artifact(self, session_id: str, artifact_id: str) -> dict[str, Any] | None:
        return self.memory_session_artifact_chunk(session_id, artifact_id, offset=0, max_bytes=256 * 1024)

    def memory_session_artifact_chunk(
        self,
        session_id: str,
        artifact_id: str,
        *,
        offset: int = 0,
        max_bytes: int = 256 * 1024,
    ) -> dict[str, Any] | None:
        token = str(session_id or "").strip()
        ref_id = str(artifact_id or "").strip()
        if not token or not ref_id:
            return None
        session = read_memory_session_summary(token)
        if not isinstance(session, dict):
            return None
        refs = session.get("artifact_refs") if isinstance(session.get("artifact_refs"), list) else []
        artifact = next(
            (
                dict(item)
                for item in refs
                if isinstance(item, dict) and str(item.get("artifact_id") or "").strip() == ref_id
            ),
            None,
        )
        if artifact is None:
            return None
        path = Path(str(artifact.get("path") or "").strip()) if artifact.get("path") else None
        payload: dict[str, Any] = {
            "ok": True,
            "session_id": token,
            "artifact": artifact,
        }
        if path and path.exists():
            payload["exists"] = True
            size_bytes = int(path.stat().st_size)
            start = max(0, int(offset or 0))
            chunk_size = max(1, min(int(max_bytes or 256 * 1024), 1024 * 1024))
            if start > size_bytes:
                start = size_bytes
            remaining_bytes = max(0, size_bytes - start)
            read_bytes = min(chunk_size, remaining_bytes)
            content_type = str(artifact.get("content_type") or "").strip().lower()
            payload["transfer"] = {
                "offset": start,
                "requested_max_bytes": chunk_size,
                "size_bytes": size_bytes,
                "chunk_bytes": read_bytes,
                "remaining_bytes": max(0, remaining_bytes - read_bytes),
                "truncated": remaining_bytes > read_bytes,
                "pull_supported": True,
            }
            if content_type == "application/json" and start == 0 and size_bytes <= chunk_size:
                try:
                    payload["content"] = json.loads(path.read_text(encoding="utf-8"))
                    payload["transfer"]["encoding"] = "json"
                except Exception:
                    payload["content"] = None
                    payload["transfer"]["encoding"] = "unavailable"
            else:
                data = b""
                if read_bytes > 0:
                    with path.open("rb") as handle:
                        handle.seek(start)
                        data = handle.read(read_bytes)
                if content_type.startswith("text/"):
                    payload["text"] = data.decode("utf-8", errors="replace")
                    payload["transfer"]["encoding"] = "utf-8"
                else:
                    payload["content_base64"] = base64.b64encode(data).decode("ascii")
                    payload["transfer"]["encoding"] = "base64"
            payload["content"] = payload.get("content")
        else:
            payload["exists"] = False
            payload["content"] = None
            payload["transfer"] = {
                "offset": max(0, int(offset or 0)),
                "requested_max_bytes": max(1, min(int(max_bytes or 256 * 1024), 1024 * 1024)),
                "size_bytes": 0,
                "chunk_bytes": 0,
                "remaining_bytes": 0,
                "truncated": False,
                "pull_supported": False,
                "encoding": "unavailable",
            }
        return payload

    def start_memory_profile(
        self,
        *,
        profile_mode: str,
        reason: str,
        trigger_source: str = "operator",
    ) -> dict[str, Any]:
        summary = self._request_memory_profile_session(
            profile_mode=profile_mode,
            reason=reason,
            trigger_source=trigger_source,
        )
        return {
            "ok": True,
            "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
            "session": summary,
            "runtime": self.memory_status(),
        }

    def retry_memory_profile(self, session_id: str, *, reason: str) -> dict[str, Any]:
        token = str(session_id or "").strip()
        summary = read_memory_session_summary(token)
        if summary is None:
            raise HTTPException(status_code=404, detail="memory profiling session was not found")
        state = str(summary.get("session_state") or "").strip().lower()
        if state not in {"failed", "cancelled", "stopped", "finished"}:
            raise HTTPException(status_code=409, detail="memory profiling session is not retryable yet")
        trigger_source = str(summary.get("trigger_source") or "operator").strip() or "operator"
        retried = self._request_memory_profile_session(
            profile_mode=str(summary.get("profile_mode") or "sampled_profile"),
            reason=str(reason or "operator.retry"),
            trigger_source=trigger_source,
            trigger_threshold=str(summary.get("trigger_threshold") or "").strip() or None,
        )
        retry_root_session_id = str(summary.get("retry_root_session_id") or token).strip() or token
        retry_depth = max(1, int(summary.get("retry_depth") or 0) + 1)
        retried["retry_of_session_id"] = token
        retried["retry_root_session_id"] = retry_root_session_id
        retried["retry_depth"] = retry_depth
        retried_window = (
            retried.get("operation_window") if isinstance(retried.get("operation_window"), dict) else {}
        )
        retried_window["retry_of_session_id"] = token
        retried_window["retry_root_session_id"] = retry_root_session_id
        retried_window["retry_depth"] = retry_depth
        retried_window["retry_reason"] = str(reason or "operator.retry")
        retried["operation_window"] = retried_window
        retried = self._upsert_memory_session_summary(retried)
        self._append_memory_operation(
            session_id=str(retried.get("session_id") or ""),
            event="tool_invoked",
            profile_mode=str(retried.get("profile_mode") or ""),
            details={
                "action": "profile_retry",
                "retry_of_session_id": token,
                "retry_root_session_id": retry_root_session_id,
                "retry_depth": retry_depth,
                "reason": str(reason or "operator.retry"),
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
            },
        )
        return {
            "ok": True,
            "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
            "retry_of_session_id": token,
            "session": retried,
            "runtime": self.memory_status(),
        }

    def stop_memory_profile(self, session_id: str, *, reason: str) -> dict[str, Any]:
        token = str(session_id or "").strip()
        summary = read_memory_session_summary(token)
        if summary is None:
            raise HTTPException(status_code=404, detail="memory profiling session was not found")
        now = time.time()
        state = str(summary.get("session_state") or "").strip().lower() or "planned"
        next_state = "cancelled" if state in {"planned", "requested"} else "stopped"
        summary["session_state"] = next_state
        summary["stop_reason"] = str(reason or "operator.stop")
        summary["stopped_at"] = now
        summary["finished_at"] = summary.get("finished_at") or now
        updated = self._upsert_memory_session_summary(summary)
        self._append_memory_operation(
            session_id=token,
            event="tool_invoked",
            profile_mode=str(updated.get("profile_mode") or ""),
            details={
                "action": "profile_stop",
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
                "reason": str(reason or "operator.stop"),
            },
        )
        if token == str(self._memory_active_session_id or "").strip():
            if next_state == "stopped":
                self._memory_profile_finalizing_session_id = token
            elif token == str(self._memory_profile_finalizing_session_id or "").strip():
                self._memory_profile_finalizing_session_id = None
            self._memory_active_session_id = None
            self._memory_requested_profile_mode = None
        self._persist_runtime_state()
        return {
            "ok": True,
            "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
            "session": updated,
            "runtime": self.memory_status(),
        }

    def _publish_memory_profile_to_root(
        self,
        *,
        summary: dict[str, Any],
        reason: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        token = str(summary.get("session_id") or "").strip()
        operations = read_memory_session_operations(token, limit=200)
        telemetry = self._memory_session_telemetry_window(summary, limit=200)
        try:
            conf = load_config()
        except Exception as exc:
            return (
                {
                    "ok": False,
                    "state": "publish_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": "root_config_unavailable",
                },
                None,
            )
        try:
            result = report_hub_memory_profile(
                conf,
                session_summary=summary,
                operations=operations,
                telemetry=telemetry,
            )
        except Exception as exc:
            return (
                {
                    "ok": False,
                    "state": "publish_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": str(reason or "operator.publish"),
                },
                None,
            )
        if not isinstance(result, dict):
            return (
                {
                    "ok": False,
                    "state": "publish_failed",
                    "error": "root client is unavailable",
                    "reason": str(reason or "operator.publish"),
                },
                None,
            )
        protocol_meta = result.get("_protocol") if isinstance(result.get("_protocol"), dict) else {}
        published_ref = (
            str(result.get("published_ref") or "").strip()
            or str(protocol_meta.get("message_id") or "").strip()
            or f"root://hub-memory-profile/{token}"
        )
        return (
            {
                "ok": True,
                "state": "published",
                "reason": str(reason or "operator.publish"),
                "reported_at": result.get("reported_at"),
                "published_ref": published_ref,
                "duplicate": bool(result.get("duplicate")),
                "message_id": protocol_meta.get("message_id") or result.get("message_id"),
                "cursor": protocol_meta.get("cursor"),
            },
            result,
        )

    def publish_memory_profile(self, session_id: str, *, reason: str) -> dict[str, Any]:
        token = str(session_id or "").strip()
        summary = read_memory_session_summary(token)
        if summary is None:
            raise HTTPException(status_code=404, detail="memory profiling session was not found")
        now = time.time()
        summary["publish_state"] = "publish_requested"
        summary["publish_requested_at"] = now
        self._memory_publish_request_session_id = token
        updated = self._upsert_memory_session_summary(summary)
        self._append_memory_operation(
            session_id=token,
            event="tool_invoked",
            profile_mode=str(updated.get("profile_mode") or ""),
            details={
                "action": "publish_request",
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
                "reason": str(reason or "operator.publish"),
            },
        )
        publish_result, raw_result = self._publish_memory_profile_to_root(summary=updated, reason=reason)
        updated["publish_result"] = publish_result
        updated["published_to_root"] = bool(publish_result.get("ok"))
        updated["publish_state"] = str(publish_result.get("state") or "publish_failed")
        updated["published_ref"] = publish_result.get("published_ref")
        if bool(publish_result.get("ok")):
            artifact_refs = updated.get("artifact_refs") if isinstance(updated.get("artifact_refs"), list) else []
            published_artifacts: list[dict[str, Any]] = []
            for item in artifact_refs:
                if not isinstance(item, dict):
                    continue
                artifact_id = str(item.get("artifact_id") or "").strip()
                published_artifacts.append(
                    {
                        **item,
                        "published_ref": memory_profile_artifact_published_ref(
                            session_id=token,
                            artifact_id=artifact_id,
                        ) if artifact_id else item.get("published_ref"),
                        "fetch_strategy": (
                            "inline_content"
                            if bool(item.get("remote_available")) or str(item.get("publish_status") or "").strip() == "inline_available"
                            else "local_control_pull"
                        ),
                        "source_api_path": (
                            memory_profile_artifact_source_api_path(
                                session_id=token,
                                artifact_id=artifact_id,
                            )
                            if artifact_id
                            else item.get("source_api_path")
                        ),
                    }
                )
            updated["artifact_refs"] = published_artifacts
        updated_window = updated.get("operation_window") if isinstance(updated.get("operation_window"), dict) else {}
        updated_window["publish_result"] = publish_result
        updated["operation_window"] = updated_window
        updated = self._upsert_memory_session_summary(updated)
        self._append_memory_operation(
            session_id=token,
            event="tool_invoked",
            profile_mode=str(updated.get("profile_mode") or ""),
            details={
                "action": "publish_complete" if bool(publish_result.get("ok")) else "publish_failed",
                "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
                "reason": str(reason or "operator.publish"),
                "publish_state": updated.get("publish_state"),
                "published_ref": updated.get("published_ref"),
                "error": publish_result.get("error"),
            },
        )
        self._persist_runtime_state()
        return {
            "ok": True,
            "control_mode": IMPLEMENTED_PROFILE_CONTROL_MODE,
            "session": updated,
            "publish_result": publish_result,
            "root_result": raw_result,
            "runtime": self.memory_status(),
        }

    def _runtime_self_heal_decision(self, *, now: float | None = None) -> dict[str, Any] | None:
        proc = self._proc
        if proc is None or proc.poll() is not None or self._stopping or not self._desired_running:
            self._recovery_policy.clear_unhealthy_window()
            self._publish_status_observation(
                reason="runtime_health_observation",
                fields={
                    "managed_alive": False,
                    "listener_running": False,
                    "runtime_api_ready": False,
                    "runtime_state": "stopping" if self._stopping else "stopped",
                    "runtime_self_heal": self._runtime_self_heal_status_payload(),
                },
            )
            return None
        update_status = read_core_update_status()
        update_state = str(update_status.get("state") or "").strip().lower()
        update_phase = str(update_status.get("phase") or "").strip().lower()
        current_slot = str(active_slot() or "").strip().upper() or None
        active_manifest = active_slot_manifest()
        managed = _proc_details(proc, cwd_hint=self._managed_runtime_cwd)
        managed_executable = str(managed.get("managed_executable") or "").strip() or None
        managed_cwd = str(managed.get("managed_cwd") or "").strip() or None
        expected_executable, expected_cwd, managed_matches_active_slot = self._managed_runtime_slot_expectations(
            manifest=active_manifest,
            managed_executable=managed_executable,
            managed_cwd=managed_cwd,
        )
        runtime_port = self.slot_runtime_port(current_slot)
        runtime_url = self.slot_runtime_base_url(current_slot)
        listener_running = _listener_running(self.runtime_host, runtime_port)
        api_probe = (
            _runtime_api_probe(runtime_url, token=self.token)
            if listener_running
            else {"ready": False, "runtime": {}, "error_type": "listener_unavailable"}
        )
        api_ready = bool(api_probe.get("ready"))
        api_identity_matches = self._refresh_managed_runtime_api_identity(
            current_slot=current_slot,
            probe=api_probe,
        )
        if api_identity_matches is not None:
            managed_matches_active_slot = api_identity_matches
        if self._verified_adopted_runtime_matches_active_slot(current_slot=current_slot):
            managed_matches_active_slot = True
        current_time = time.time() if now is None else float(now)
        evaluation = self._recovery_policy.evaluate(
            RuntimeRecoveryFacts(
                process_running=bool(proc is not None and proc.poll() is None),
                stopping=self._stopping,
                desired_running=self._desired_running,
                update_state=update_state,
                update_phase=update_phase,
                current_slot=current_slot,
                managed_executable=managed_executable,
                managed_cwd=managed_cwd,
                expected_executable=expected_executable,
                expected_cwd=expected_cwd,
                managed_matches_active_slot=managed_matches_active_slot,
                runtime_host=self.runtime_host,
                runtime_port=runtime_port,
                runtime_url=runtime_url,
                listener_running=listener_running,
                runtime_api_ready=api_ready,
                now=current_time,
                unhealthy_kind=self._runtime_unhealthy_kind,
                unhealthy_since=self._runtime_unhealthy_since,
                last_start_at=self._last_start_at,
                listener_startup_grace_sec=_runtime_listener_startup_grace_sec(),
                listener_restart_timeout_sec=_runtime_listener_restart_timeout_sec(),
                api_restart_timeout_sec=_runtime_api_restart_timeout_sec(),
            )
        )
        decision = self._recovery_policy.record_evaluation(evaluation)
        runtime_state = "ready" if api_ready else ("starting" if listener_running else "spawned")
        self._publish_status_observation(
            reason="runtime_health_observation",
            fields={
                "active_slot": current_slot,
                "runtime_url": runtime_url,
                "runtime_port": runtime_port,
                "managed_pid": managed.get("managed_pid"),
                "managed_alive": True,
                "listener_running": listener_running,
                "runtime_api_ready": api_ready,
                "runtime_state": runtime_state,
                "managed_executable": managed_executable,
                "managed_cwd": managed_cwd,
                "expected_managed_executable": expected_executable,
                "expected_managed_cwd": expected_cwd,
                "managed_matches_active_slot": managed_matches_active_slot,
                "managed_runtime_api_identity": {
                    "verified": bool(self._managed_runtime_api_identity_verified),
                    "observed_at": self._managed_runtime_api_identity_observed_at,
                    **dict(self._managed_runtime_api_identity or {}),
                },
                "runtime_self_heal": self._runtime_self_heal_status_payload(),
                "monitor": {
                    "running": bool(self._monitor_task is not None and not self._monitor_task.done()),
                    "loop_started_at": self._monitor_loop_started_at,
                    "last_iteration_at": self._monitor_last_iteration_at,
                    "last_failure_at": self._monitor_last_failure_at,
                    "last_failure": self._monitor_last_failure,
                    "consecutive_failure_total": int(self._monitor_failure_total),
                    "recovery_total": int(self._monitor_recovery_total),
                },
            },
        )
        return decision

    def _local_supervisor_update_status_payload(self, *, runtime_api_timeout: float = 0.75) -> dict[str, Any]:
        payload = _local_update_payload()
        payload["runtime"] = self.status(runtime_api_timeout=runtime_api_timeout)
        payload["_served_by"] = "supervisor_fallback"
        return _reconcile_update_status(payload)

    def _adopt_active_runtime_listener(self, *, reason: str) -> bool:
        return self._process_supervisor.adopt_active_runtime_listener(
            self,
            self._process_operations(),
            reason=reason,
        )

    async def _spawn_runtime_locked(
        self,
        *,
        reason: str = "supervisor.start",
        adopt_existing: bool = False,
    ) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if adopt_existing and self._adopt_active_runtime_listener(reason="supervisor.adopt.active_listener"):
            self._persist_runtime_state()
            return
        profile_mode = self._desired_memory_profile_mode()
        profile_session_id = str(self._memory_active_session_id or "").strip() or None
        profile_trigger_source = self._active_memory_profile_trigger_source() if profile_mode != "normal" else ""
        allowed, block_reason = self._memory_profile_restart_guard(desired_mode=profile_mode)
        if not allowed:
            self._record_memory_auto_profile_block(block_reason)
            profile_mode = "normal"
            profile_session_id = None
            profile_trigger_source = ""
        argv, command, env, cwd, runtime_instance_id, transition_role = self._runtime_launch_spec(
            profile_mode=profile_mode,
            profile_session_id=profile_session_id,
        )
        proc = await asyncio.to_thread(
            subprocess.Popen,
            argv or command or [],
            shell=bool(command),
            cwd=cwd or os.getcwd(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            start_new_session=(os.name != "nt"),
            creationflags=(int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0),
        )
        self._process_supervisor.track_active(proc)
        managed_slot = str(env.get("ADAOS_ACTIVE_CORE_SLOT") or active_slot() or "").strip().upper() or None
        try:
            managed_port = int(env.get("ADAOS_RUNTIME_PORT") or self.slot_runtime_port(managed_slot))
        except Exception:
            managed_port = self.slot_runtime_port(managed_slot)
        self._managed_runtime_instance_id = runtime_instance_id
        self._managed_transition_role = transition_role
        self._managed_slot = managed_slot
        self._managed_runtime_port = int(managed_port)
        self._managed_runtime_base_url = f"http://{self.runtime_host}:{int(managed_port)}"
        self._managed_runtime_cwd = str(cwd or os.getcwd())
        self._managed_start_reason = str(reason or "supervisor.start")
        self._managed_runtime_api_identity_verified = False
        self._managed_runtime_api_identity_observed_at = None
        self._managed_runtime_api_identity = {}
        self._memory_profile_mode = profile_mode
        self._memory_profile_current_trigger_source = str(profile_trigger_source or "").strip().lower() or None
        self._reset_memory_baseline_scope(managed_pid=getattr(proc, "pid", None))
        self._last_start_at = time.time()
        self._last_error = None
        self._runtime_unhealthy_since = None
        self._runtime_unhealthy_kind = None
        if profile_mode != "normal":
            self._mark_active_memory_session_running(runtime_instance_id=runtime_instance_id, transition_role=transition_role)
        elif self._memory_profile_finalizing_session_id:
            self._memory_profile_finalizing_session_id = None
        self._persist_runtime_state()

    async def _spawn_sidecar_locked(self, *, reason: str = "supervisor.sidecar.start") -> None:
        proc = self._sidecar_proc
        if proc is not None and proc.poll() is None:
            return
        code_state = self._sidecar_code_state()
        repo_root = str(self._sidecar_repo_root() or "").strip() or None
        existing = realtime_sidecar_listener_snapshot(role=self._sidecar_role())
        listener_pid = existing.get("listener_pid")
        listener_ready = False
        if bool(existing.get("listener_running")) and listener_pid:
            listener_ready = await probe_realtime_sidecar_ready(
                host=str(existing.get("host") or "127.0.0.1"),
                port=int(existing.get("port") or 0),
                timeout_s=1.5,
            )
        if listener_ready:
            inherited_fingerprint, inherited_fingerprint_updated_at = self._persisted_sidecar_code_for_listener(
                int(listener_pid)
            )
            self._process_supervisor.track_sidecar(_AdoptedProcess(int(listener_pid)))
            _LOG.info(
                "supervisor adopted realtime sidecar listener pid=%s active_fingerprint=%s",
                listener_pid,
                inherited_fingerprint,
            )
        else:
            inherited_fingerprint = None
            inherited_fingerprint_updated_at = None
            self._process_supervisor.track_sidecar(
                await start_realtime_sidecar_subprocess(
                    role=self._sidecar_role(),
                    repo_root=repo_root,
                )
            )
        self._sidecar_launch_cwd = str(code_state.get("repo_root") or code_state.get("launch_cwd") or "") or None
        current_fingerprint = str(code_state.get("fingerprint") or "").strip() or None
        self._sidecar_code_fingerprint = inherited_fingerprint or current_fingerprint
        self._sidecar_code_fingerprint_updated_at = (
            inherited_fingerprint_updated_at
            if inherited_fingerprint
            else (time.time() if current_fingerprint else None)
        )
        self._sidecar_code_change_pending_fingerprint = None
        self._sidecar_code_change_pending_since = None
        self._sidecar_last_start_reason = str(reason or "supervisor.sidecar.start")
        self._sidecar_last_probe_at = None
        self._sidecar_last_probe_ok = None
        self._sidecar_last_probe_error = None
        self._sidecar_consecutive_probe_failures = 0
        self._record_sidecar_restart_attempt(reason=reason)
        self._persist_runtime_state()

    async def _spawn_candidate_runtime_locked(self, *, slot: str, reason: str = "supervisor.candidate.start") -> None:
        resolved_slot = str(slot or "").strip().upper()
        if not resolved_slot:
            raise RuntimeError("candidate slot is required")
        if resolved_slot == str(active_slot() or "").strip().upper():
            raise RuntimeError("candidate slot must differ from the active slot")
        existing = self._candidate_proc
        if (
            existing is not None
            and existing.poll() is None
            and str(self._candidate_slot or "").strip().upper() == resolved_slot
        ):
            return
        if existing is not None and existing.poll() is None:
            await self._terminate_candidate_proc_locked(graceful=True, reason="supervisor.candidate.replace")
        argv, command, env, cwd, runtime_instance_id, transition_role = self._runtime_launch_spec(
            slot=resolved_slot,
            transition_role="candidate",
            profile_mode="normal",
            profile_session_id=None,
            profile_trigger=None,
            skip_pending_update=True,
        )
        proc = await asyncio.to_thread(
            subprocess.Popen,
            argv or command or [],
            shell=bool(command),
            cwd=cwd or os.getcwd(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            start_new_session=(os.name != "nt"),
            creationflags=(int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0),
        )
        self._process_supervisor.track_candidate(proc)
        self._candidate_slot = resolved_slot
        self._candidate_runtime_instance_id = runtime_instance_id
        self._candidate_transition_role = transition_role
        self._candidate_runtime_cwd = str(cwd or os.getcwd())
        self._candidate_start_reason = str(reason or "supervisor.candidate.start")
        self._persist_runtime_state()

    async def ensure_started(self, *, reason: str = "supervisor.start") -> None:
        async with self._lock:
            self._process_supervisor.request_running()
            await self._spawn_runtime_locked(reason=reason, adopt_existing=True)

    async def ensure_sidecar_started(self) -> dict[str, Any]:
        async with self._lock:
            await self._spawn_sidecar_locked()
            self._persist_runtime_state()
            return self._sidecar_status_payload()

    async def _terminate_proc_locked(
        self,
        *,
        proc: Any | None,
        base_url: str | None = None,
        graceful: bool,
        reason: str,
        lifecycle_scope: str = "subnet",
    ) -> None:
        if proc is None:
            return
        if proc.poll() is not None:
            return
        profile_mode = self._memory_profile_mode if proc is self._proc else "normal"
        profile_session_id = (
            str(self._memory_active_session_id or self._memory_profile_finalizing_session_id or "").strip()
            if proc is self._proc
            else ""
        )
        drain_timeout_sec, signal_delay_sec, graceful_wait_sec, terminate_wait_sec = _runtime_profile_graceful_shutdown_timeout_sec(
            profile_mode
        )
        if graceful:
            shutdown_requested = False
            shutdown_url = str(base_url or self._managed_proc_base_url(proc)) + "/api/admin/shutdown"
            shutdown_status_code: int | None = None
            shutdown_error: str | None = None
            try:
                headers = {"Content-Type": "application/json"}
                if self.token:
                    headers["X-AdaOS-Token"] = self.token
                _LOG.info(
                    "supervisor requesting runtime shutdown reason=%s url=%s profile_mode=%s session_id=%s pid=%s",
                    reason,
                    shutdown_url,
                    profile_mode,
                    profile_session_id or None,
                    getattr(proc, "pid", None),
                )
                response = await asyncio.to_thread(
                    requests.post,
                    shutdown_url,
                    headers=headers,
                    json={
                        "reason": reason,
                        "drain_timeout_sec": float(drain_timeout_sec),
                        "signal_delay_sec": float(signal_delay_sec),
                        "lifecycle_scope": str(lifecycle_scope or "subnet"),
                    },
                    timeout=_runtime_shutdown_request_timeout(
                        drain_timeout_sec=drain_timeout_sec,
                        signal_delay_sec=signal_delay_sec,
                    ),
                )
                shutdown_status_code = int(response.status_code)
                response_tail = (response.text or "").strip()[-400:]
                _LOG.info(
                    "supervisor runtime shutdown response reason=%s url=%s status_code=%s body_tail=%s",
                    reason,
                    shutdown_url,
                    shutdown_status_code,
                    response_tail,
                )
                shutdown_requested = True
            except Exception as exc:
                shutdown_error = f"{type(exc).__name__}: {exc}"
                _LOG.warning(
                    "supervisor runtime shutdown request failed reason=%s url=%s profile_mode=%s session_id=%s error=%s",
                    reason,
                    shutdown_url,
                    profile_mode,
                    profile_session_id or None,
                    shutdown_error,
                )
            finalize_wait_deadline = time.time() + float(_runtime_profile_finalize_wait_sec())
            if shutdown_requested and profile_mode != "normal" and profile_session_id:
                _LOG.info(
                    "supervisor waiting for runtime profile finalize marker session_id=%s timeout_sec=%.2f",
                    profile_session_id,
                    _runtime_profile_finalize_wait_sec(),
                )
                finalize_checks = max(1, int(float(_runtime_profile_finalize_wait_sec()) / 0.1) + 2)
                while time.time() < finalize_wait_deadline and finalize_checks > 0:
                    finalize_checks -= 1
                    if proc.poll() is not None:
                        return
                    if self._memory_profile_finalize_observed(profile_session_id):
                        _LOG.info(
                            "supervisor observed runtime profile finalize marker session_id=%s",
                            profile_session_id,
                        )
                        break
                    await asyncio.sleep(0.1)
                else:
                    _LOG.warning(
                        "supervisor did not observe runtime profile finalize marker before timeout session_id=%s shutdown_status_code=%s shutdown_error=%s",
                        profile_session_id,
                        shutdown_status_code,
                        shutdown_error,
                    )
                    self._record_memory_profile_finalize_missing(
                        profile_session_id,
                        shutdown_status_code=shutdown_status_code,
                        shutdown_error=shutdown_error,
                        reason=reason,
                    )

        def _capture_before_signal(stage: str) -> None:
            with contextlib.suppress(Exception):
                self._capture_runtime_stop_evidence(
                    reason=reason,
                    stage=stage,
                    proc=proc,
                )

        try:
            await self._process_supervisor.terminate_process(
                proc,
                graceful_wait_sec=graceful_wait_sec if graceful else 0.0,
                terminate_wait_sec=terminate_wait_sec,
                before_signal=_capture_before_signal,
                signal_process=_signal_process_family,
            )
        except RuntimeError as exc:
            self._last_error = f"runtime process did not exit after forced kill: {reason}"
            self._persist_runtime_state()
            raise RuntimeError(self._last_error) from exc

    async def _terminate_candidate_proc_locked(self, *, graceful: bool, reason: str) -> None:
        candidate_proc = self._candidate_proc
        if candidate_proc is None:
            return
        candidate_slot = str(self._candidate_slot or "").strip().upper() or None
        candidate_base_url = self.slot_runtime_base_url(candidate_slot) if candidate_slot else None
        await self._terminate_proc_locked(
            proc=candidate_proc,
            base_url=candidate_base_url,
            graceful=graceful,
            reason=reason,
            lifecycle_scope="runtime_retire",
        )
        self._candidate_last_stop_reason = str(reason or "supervisor.candidate.stop")
        self._process_supervisor.track_candidate(None)
        self._candidate_slot = None
        self._candidate_runtime_instance_id = None
        self._candidate_transition_role = None
        self._candidate_runtime_cwd = None
        self._persist_runtime_state()

    def _schedule_retired_runtime_stop(
        self,
        *,
        proc: Any,
        base_url: str,
        reason: str,
    ) -> asyncio.Task[Any]:
        async def _retire() -> None:
            try:
                await self._terminate_proc_locked(
                    proc=proc,
                    base_url=base_url,
                    graceful=True,
                    reason=reason,
                    lifecycle_scope="runtime_retire",
                )
            except Exception:
                _LOG.warning(
                    "failed to retire old active runtime after cutover reason=%s pid=%s",
                    reason,
                    getattr(proc, "pid", None),
                    exc_info=True,
                )

        try:
            retired_pid = int(getattr(proc, "pid", 0) or 0)
        except Exception:
            retired_pid = 0
        if retired_pid > 0:
            self._retired_runtime_procs[retired_pid] = proc
        task = asyncio.create_task(
            _retire(),
            name=f"adaos-supervisor-retire-runtime-{getattr(proc, 'pid', 'unknown')}",
        )
        self._retired_runtime_tasks.add(task)

        def _retire_done(done: asyncio.Task[Any]) -> None:
            self._retired_runtime_tasks.discard(done)
            if retired_pid > 0:
                self._retired_runtime_procs.pop(retired_pid, None)

        task.add_done_callback(_retire_done)
        return task

    async def _single_owner_candidate_cutover(
        self,
        *,
        slot: str,
        reason: str,
        restore_active_on_failure: bool,
    ) -> dict[str, Any]:
        """Transfer transport ownership without exposing an interleaving restart window."""

        started_at = time.time()
        async with self._lock:
            old_proc = self._proc
            old_pid = getattr(old_proc, "pid", None) if old_proc is not None else None
            old_base_url = self._managed_proc_base_url(old_proc) if old_proc is not None else None
            old_was_running = bool(old_proc is not None and old_proc.poll() is None)
            self._desired_running = False
            self._persist_runtime_state()
            active_retirement: dict[str, Any] = {
                "ok": True,
                "stopped": False,
                "pid": old_pid,
                "base_url": old_base_url,
                "reason": "active_runtime_not_running",
                "invariant": "active_stopped_before_candidate_promotion",
            }
            candidate_cleanup: dict[str, Any] | None = None
            active_restore: dict[str, Any] | None = None
            try:
                if old_was_running:
                    retirement_started_at = time.time()
                    await self._terminate_proc_locked(
                        proc=old_proc,
                        base_url=old_base_url,
                        graceful=True,
                        reason="supervisor.fast_cutover.active_retire",
                        lifecycle_scope="runtime_retire",
                    )
                    if old_proc is not None and old_proc.poll() is None:
                        raise RuntimeError("active runtime still owns transport after cutover retirement")
                    active_retirement = {
                        "ok": True,
                        "stopped": True,
                        "pid": old_pid,
                        "base_url": old_base_url,
                        "elapsed_ms": round(max(0.0, time.time() - retirement_started_at) * 1000.0, 3),
                        "invariant": "active_stopped_before_candidate_promotion",
                    }
                promotion = await self._promote_candidate_runtime_locked(slot=slot, reason=reason)
            except Exception as exc:
                current_candidate_slot = str(self._candidate_slot or "").strip().upper() or None
                candidate_was_running = bool(self._candidate_proc is not None and self._candidate_proc.poll() is None)
                if self._candidate_proc is not None:
                    await self._terminate_candidate_proc_locked(
                        graceful=True,
                        reason="supervisor.candidate.atomic_cutover_failed",
                    )
                candidate_cleanup = {
                    "ok": True,
                    "stopped": candidate_was_running,
                    "slot": current_candidate_slot,
                    "lifecycle_scope": "runtime_retire",
                }
                if restore_active_on_failure:
                    restore_started_at = time.time()
                    self._desired_running = True
                    await self._spawn_runtime_locked(reason="supervisor.fast_cutover.restore_after_failure")
                    restored_proc = self._proc
                    active_restore = {
                        "ok": restored_proc is not None and restored_proc.poll() is None,
                        "pid": getattr(restored_proc, "pid", None) if restored_proc is not None else None,
                        "elapsed_ms": round(max(0.0, time.time() - restore_started_at) * 1000.0, 3),
                    }
                self._persist_runtime_state()
                return {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "active_retirement": active_retirement,
                    "candidate_cleanup": candidate_cleanup,
                    "active_restore": active_restore,
                    "elapsed_ms": round(max(0.0, time.time() - started_at) * 1000.0, 3),
                    "invariant": "no_concurrent_active_transport_owners",
                }

            self._desired_running = True
            self._persist_runtime_state()
            return {
                "ok": True,
                "promotion": promotion,
                "active_retirement": active_retirement,
                "candidate_cleanup": candidate_cleanup,
                "active_restore": active_restore,
                "elapsed_ms": round(max(0.0, time.time() - started_at) * 1000.0, 3),
                "invariant": "no_concurrent_active_transport_owners",
            }

    async def restart_runtime(self, *, reason: str = "supervisor.restart") -> dict[str, Any]:
        decision = await self._transition_continuity_guard_decision_async(operation="restart")
        if decision is not None:
            self._raise_restart_continuity_block(decision)
        async with self._lock:
            self._process_supervisor.desired_running = True
            await self._terminate_proc_locked(proc=self._proc, graceful=True, reason=reason)
            self._last_stop_reason = str(reason or "supervisor.restart")
            await self._spawn_runtime_locked(reason=reason)
            self._restart_count += 1
            self._persist_runtime_state()
            return self._runtime_state_payload()

    async def stop(self, *, reason: str = "supervisor.stop") -> None:
        async with self._lock:
            self._process_supervisor.request_stop()
            await self._terminate_proc_locked(proc=self._proc, graceful=True, reason=reason)
            self._last_stop_reason = str(reason or "supervisor.stop")
            await self._terminate_candidate_proc_locked(graceful=True, reason=f"{reason}.candidate")
            self._persist_runtime_state()

    async def stop_sidecar(self, *, reason: str = "supervisor.sidecar.stop") -> dict[str, Any]:
        async with self._lock:
            await stop_realtime_sidecar_subprocess(self._sidecar_proc)
            self._process_supervisor.track_sidecar(None)
            self._sidecar_last_restart_reason = str(reason or "supervisor.sidecar.stop")
            self._persist_runtime_state()
            return self._sidecar_status_payload()

    def sidecar_status(self) -> dict[str, Any]:
        payload = self._sidecar_status_payload()
        return {
            "ok": True,
            "runtime": self._runtime_sidecar_runtime_payload(),
            "process": payload.get("process"),
        }

    async def _reconnect_hub_root_after_sidecar_restart(self) -> dict[str, Any] | None:
        if str(self._sidecar_role() or "").strip().lower() != "hub":
            return None

        def _live_failback_state(channel: Any) -> dict[str, Any]:
            snapshot = channel if isinstance(channel, dict) else {}
            runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
            readiness = runtime.get("readiness_tree") if isinstance(runtime.get("readiness_tree"), dict) else {}
            overview = runtime.get("channel_overview") if isinstance(runtime.get("channel_overview"), dict) else {}
            diagnostics = (
                runtime.get("channel_diagnostics")
                if isinstance(runtime.get("channel_diagnostics"), dict)
                else {}
            )
            sidecar = runtime.get("sidecar_runtime") if isinstance(runtime.get("sidecar_runtime"), dict) else {}
            strategy = (
                runtime.get("hub_root_transport_strategy")
                if isinstance(runtime.get("hub_root_transport_strategy"), dict)
                else {}
            )

            def _current_status(name: str, overview_name: str) -> str:
                diagnostic = diagnostics.get(name) if isinstance(diagnostics.get(name), dict) else {}
                status = str(diagnostic.get("status") or "").strip().lower()
                if status:
                    return status
                readiness_item = readiness.get(name) if isinstance(readiness.get(name), dict) else {}
                status = str(readiness_item.get("status") or "").strip().lower()
                if status:
                    return status
                overview_item = overview.get(overview_name) if isinstance(overview.get(overview_name), dict) else {}
                return str(overview_item.get("effective_status") or "").strip().lower()

            selected_server = str(strategy.get("selected_server") or "").strip().rstrip("/")
            local_sidecar_url = str(realtime_sidecar_local_url() or "").strip().rstrip("/")
            sidecar_transport_confirmed = (
                str(sidecar.get("transport_owner") or "").strip().lower() == "sidecar"
                and (
                    bool(sidecar.get("transport_ready"))
                    or bool(selected_server and local_sidecar_url and selected_server == local_sidecar_url)
                )
            )
            root_status = _current_status("root_control", "hub_root")
            route_status = _current_status("route", "hub_root_browser")
            return {
                "ready": bool(
                    root_status == "ready"
                    and route_status == "ready"
                    and sidecar_transport_confirmed
                ),
                "root_control_status": root_status or None,
                "route_status": route_status or None,
                "transport_owner": str(sidecar.get("transport_owner") or "").strip().lower() or None,
                "transport_ready": bool(sidecar.get("transport_ready")),
                "selected_server": selected_server or None,
                "local_sidecar_url": local_sidecar_url or None,
                "sidecar_transport_confirmed": sidecar_transport_confirmed,
            }

        async def _wait_for_live_failback(timeout_sec: float) -> dict[str, Any]:
            deadline = time.monotonic() + max(0.0, float(timeout_sec))
            ready_since: float | None = None
            attempts = 0
            last_channel: dict[str, Any] = {}
            last_state: dict[str, Any] = {}
            while True:
                attempts += 1
                try:
                    channel = await asyncio.to_thread(
                        self._runtime_request_json,
                        path="/api/node/reliability/supervisor-channel",
                        timeout=0.75,
                    )
                except Exception:
                    channel = {}
                last_channel = channel if isinstance(channel, dict) else {}
                last_state = _live_failback_state(last_channel)
                now = time.monotonic()
                if bool(last_state.get("ready")):
                    ready_since = ready_since or now
                    if now - ready_since >= 0.25:
                        return {
                            "ok": True,
                            "state": "ready",
                            "attempts": attempts,
                            "channel_state": last_state,
                            "channel": last_channel,
                        }
                else:
                    ready_since = None
                if now >= deadline:
                    return {
                        "ok": False,
                        "state": "not_ready",
                        "attempts": attempts,
                        "channel_state": last_state,
                        "channel": last_channel,
                    }
                await asyncio.sleep(0.1)

        await asyncio.sleep(0.25)
        initial_verification = await _wait_for_live_failback(
            _sidecar_recovery_settle_timeout_sec()
        )
        if bool(initial_verification.get("ok")):
            return {
                "ok": True,
                "skipped": True,
                "reason": "hub_root_already_reconnected",
                "verification": initial_verification,
                "channel": initial_verification.get("channel"),
            }
        try:
            reconnect = await asyncio.to_thread(
                self._runtime_request_json,
                path="/api/node/hub-root/reconnect",
                method="POST",
                payload={},
                timeout=5.0,
            )
        except Exception as exc:
            return {
                "ok": False,
                "forced": True,
                "reason": "hub_root_sidecar_failback_required",
                "error": f"{type(exc).__name__}: {exc}",
                "verification_before_force": initial_verification,
            }
        verification = await _wait_for_live_failback(
            _hub_root_watchdog_verify_timeout_sec()
        )
        reconnect_payload = reconnect if isinstance(reconnect, dict) else {}
        return {
            **reconnect_payload,
            "ok": bool(reconnect_payload.get("ok", True)) and bool(verification.get("ok")),
            "forced": True,
            "reason": "hub_root_sidecar_failback_required",
            "verification_before_force": initial_verification,
            "verification": verification,
            "error": (
                None
                if bool(verification.get("ok"))
                else "hub-root did not converge to a live sidecar-owned root and route channel"
            ),
        }

    def _active_sidecar_channel_evidence(self) -> dict[str, Any] | None:
        if str(self._sidecar_role() or "").strip().lower() != "hub":
            return None
        try:
            runtime = self._runtime_sidecar_runtime_payload()
        except Exception:
            runtime = {}
        if not isinstance(runtime, dict):
            runtime = {}
        remote_state = str(runtime.get("remote_session_state") or "").strip().lower()
        status = str(runtime.get("status") or "").strip().lower()
        active = bool(
            runtime.get("active_session")
            or runtime.get("transport_ready")
            or remote_state == "ready"
            or status == "ready"
        )
        if not active:
            return None
        return {
            "active_session": bool(runtime.get("active_session")),
            "transport_ready": bool(runtime.get("transport_ready")),
            "status": status or None,
            "remote_session_state": remote_state or None,
            "session_id": runtime.get("session_id"),
            "remote_url": runtime.get("remote_url"),
            "route_tunnel_contract": runtime.get("route_tunnel_contract"),
        }

    async def restart_sidecar(
        self,
        *,
        reconnect_hub_root: bool = True,
        allow_active_channel_disruption: bool = False,
    ) -> dict[str, Any]:
        active_channel = self._active_sidecar_channel_evidence()
        if active_channel is not None and not allow_active_channel_disruption:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "active_sidecar_channel",
                    "message": "sidecar restart would disrupt active NATS and browser proxy sessions",
                    "required_override": "allow_active_channel_disruption=true",
                    "channel": active_channel,
                },
            )
        transition_id = self._begin_sidecar_transition(
            source="operator",
            reason="supervisor.sidecar.restart",
            reject_if_active=True,
        )
        assert transition_id is not None
        self._persist_runtime_state()
        transition_error: str | None = None
        transition_outcome = "failed"
        response: dict[str, Any] | None = None
        try:
            async with self._lock:
                # A validated slot may contain newer sidecar-controlled files than
                # root. Sync before launch so one operator request produces one
                # process generation and the monitor has nothing left to restart.
                self._sync_sidecar_controlled_files_from_validated_slot()
                try:
                    new_proc, restart_result = await restart_realtime_sidecar_subprocess(
                        proc=self._sidecar_proc,
                        role=self._sidecar_role(),
                        repo_root=str(self._sidecar_repo_root() or "").strip() or None,
                    )
                except TypeError:
                    new_proc, restart_result = await restart_realtime_sidecar_subprocess(
                        proc=self._sidecar_proc,
                        role=self._sidecar_role(),
                    )
                self._process_supervisor.track_sidecar(new_proc)
                code_state = self._sidecar_code_state()
                self._sidecar_launch_cwd = str(
                    code_state.get("repo_root") or code_state.get("launch_cwd") or ""
                ) or None
                self._sidecar_code_fingerprint = str(code_state.get("fingerprint") or "").strip() or None
                self._sidecar_code_fingerprint_updated_at = time.time() if self._sidecar_code_fingerprint else None
                self._sidecar_code_change_pending_fingerprint = None
                self._sidecar_code_change_pending_since = None
                self._sidecar_last_start_reason = "supervisor.sidecar.restart"
                self._sidecar_last_restart_reason = str(restart_result.get("reason") or "restarted")
                self._sidecar_last_probe_at = None
                self._sidecar_last_probe_ok = None
                self._sidecar_last_probe_error = None
                self._sidecar_consecutive_probe_failures = 0
                self._record_sidecar_restart_attempt(reason=self._sidecar_last_restart_reason)
                self._persist_runtime_state()
            reconnect_result: dict[str, Any] | None = None
            if reconnect_hub_root:
                reconnect_result = await self._reconnect_hub_root_after_sidecar_restart()
            restart_ok = bool(restart_result.get("ok", restart_result.get("accepted", True)))
            reconnect_ok = reconnect_result is None or bool(reconnect_result.get("ok"))
            transition_outcome = "completed" if restart_ok and reconnect_ok else "failed"
            if transition_outcome == "failed":
                transition_error = str(
                    (reconnect_result or {}).get("error")
                    or restart_result.get("error")
                    or "sidecar channel recovery did not complete"
                )
            response = {
                "ok": restart_ok and reconnect_ok,
                "process_restarted": restart_ok,
                "channel_recovered": reconnect_ok,
                "active_channel_disruption_allowed": bool(allow_active_channel_disruption),
                "transition_id": transition_id,
                "restart": restart_result,
                "reconnect": reconnect_result,
                "runtime": self._runtime_sidecar_runtime_payload(),
            }
        except Exception as exc:
            transition_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._finish_sidecar_transition(
                transition_id,
                outcome=transition_outcome,
                error=transition_error,
            )
            self._persist_runtime_state()
        assert response is not None
        response["transition"] = self._sidecar_transition_payload()
        response["process"] = self._sidecar_status_payload().get("process")
        return response

    async def start_candidate_runtime(
        self,
        *,
        slot: str | None = None,
        reason: str = "supervisor.candidate.start",
    ) -> dict[str, Any]:
        resolved_slot = str(slot or choose_inactive_slot() or "").strip().upper()
        if resolved_slot not in {"A", "B"}:
            raise HTTPException(status_code=409, detail="candidate slot is unavailable")
        current_slot = str(active_slot() or "").strip().upper()
        if resolved_slot == current_slot:
            raise HTTPException(status_code=409, detail="candidate slot must differ from the active slot")
        if self.slot_runtime_port(resolved_slot) == self.slot_runtime_port(current_slot):
            raise HTTPException(status_code=409, detail="candidate slot uses the same runtime port as the active slot")
        structure = validate_slot_structure(resolved_slot)
        if not bool(structure.get("ok")):
            raise HTTPException(status_code=409, detail=f"candidate slot {resolved_slot} is not launchable")
        async with self._lock:
            await self._spawn_candidate_runtime_locked(slot=resolved_slot, reason=reason)
            self._persist_runtime_state()
            return self._runtime_state_payload()

    async def stop_candidate_runtime(self, *, reason: str = "supervisor.candidate.stop") -> dict[str, Any]:
        async with self._lock:
            await self._terminate_candidate_proc_locked(graceful=True, reason=reason)
            self._persist_runtime_state()
            return self._runtime_state_payload()

    async def _candidate_prewarm(self, *, target_slot: str | None) -> dict[str, Any]:
        resolved_target = str(target_slot or "").strip().upper()
        if not resolved_target:
            return {
                "attempted": False,
                "state": "skipped",
                "message": "candidate prewarm skipped: target slot is unavailable",
            }

        runtime_snapshot = await asyncio.to_thread(self.status)
        candidate_slot = str(runtime_snapshot.get("candidate_slot") or "").strip().upper()
        transition_mode = str(runtime_snapshot.get("transition_mode") or "").strip().lower()
        warm_switch_allowed = bool(runtime_snapshot.get("warm_switch_allowed"))
        warm_switch_reason = str(runtime_snapshot.get("warm_switch_reason") or "").strip()
        if candidate_slot != resolved_target or transition_mode != "warm_switch" or not warm_switch_allowed:
            return {
                "attempted": False,
                "state": "skipped",
                "message": warm_switch_reason or "candidate prewarm skipped: warm switch is not admitted",
                "runtime": runtime_snapshot,
            }

        await self.start_candidate_runtime(slot=resolved_target, reason="supervisor.candidate.prewarm")
        timeout_sec = _warm_switch_candidate_ready_timeout_sec()
        deadline = time.time() + timeout_sec
        snapshot = await asyncio.to_thread(self.status)
        while timeout_sec > 0.0 and time.time() < deadline:
            snapshot = await asyncio.to_thread(self.status)
            if str(snapshot.get("candidate_slot") or "").strip().upper() != resolved_target:
                break
            memory_guard = self._candidate_memory_guard_snapshot(snapshot)
            if not bool(memory_guard.get("allowed")):
                cleanup = await self._cleanup_candidate_runtime(
                    reason="supervisor.candidate.memory_blocked",
                    slot=resolved_target,
                    graceful=False,
                )
                return {
                    "attempted": True,
                    "state": "memory_blocked",
                    "message": self._candidate_memory_guard_message(memory_guard),
                    "runtime": snapshot,
                    "candidate_memory_guard": memory_guard,
                    "candidate_cleanup": cleanup,
                }
            if bool(snapshot.get("candidate_runtime_api_ready")) and await asyncio.to_thread(
                _runtime_beacon_ready,
                str(snapshot.get("candidate_runtime_url") or "").strip(),
                token=self.token,
            ):
                return {
                    "attempted": True,
                    "state": "ready",
                    "message": (
                        f"passive candidate runtime is ready on {snapshot.get('candidate_runtime_url')}"
                    ),
                    "ready_at": time.time(),
                    "runtime": snapshot,
                }
            await asyncio.sleep(0.25)

        snapshot = await asyncio.to_thread(self.status)
        memory_guard = self._candidate_memory_guard_snapshot(snapshot)
        if not bool(memory_guard.get("allowed")):
            cleanup = await self._cleanup_candidate_runtime(
                reason="supervisor.candidate.memory_blocked",
                slot=resolved_target,
                graceful=False,
            )
            return {
                "attempted": True,
                "state": "memory_blocked",
                "message": self._candidate_memory_guard_message(memory_guard),
                "runtime": snapshot,
                "candidate_memory_guard": memory_guard,
                "candidate_cleanup": cleanup,
            }
        candidate_alive = bool(snapshot.get("candidate_managed_alive"))
        candidate_ready = bool(snapshot.get("candidate_runtime_api_ready"))
        candidate_url = str(snapshot.get("candidate_runtime_url") or "").strip()
        beacon_ready = bool(candidate_ready and candidate_url) and await asyncio.to_thread(
            _runtime_beacon_ready,
            candidate_url,
            token=self.token,
        )
        if candidate_ready and beacon_ready:
            return {
                "attempted": True,
                "state": "ready",
                "message": f"passive candidate runtime is ready on {candidate_url}",
                "ready_at": time.time(),
                "runtime": snapshot,
            }
        if candidate_alive:
            return {
                "attempted": True,
                "state": "starting",
                "message": (
                    f"passive candidate runtime beacon is still warming on {candidate_url or resolved_target}"
                    if candidate_ready
                    else f"passive candidate runtime is still warming on {candidate_url or resolved_target}"
                ),
                "runtime": snapshot,
            }
        return {
            "attempted": True,
            "state": "failed",
            "message": "candidate prewarm failed before the runtime became ready",
            "runtime": snapshot,
        }

    async def _refresh_starting_candidate_prewarm(self, *, target_slot: str | None) -> dict[str, Any]:
        resolved_target = str(target_slot or "").strip().upper()
        if not resolved_target:
            return {
                "state": "skipped",
                "message": "candidate prewarm refresh skipped: target slot is unavailable",
            }

        timeout_sec = _warm_switch_candidate_ready_timeout_sec()
        deadline = time.time() + timeout_sec
        snapshot = await asyncio.to_thread(self.status)
        while True:
            snapshot = await asyncio.to_thread(self.status)
            candidate_slot = str(snapshot.get("candidate_slot") or "").strip().upper()
            candidate_url = str(snapshot.get("candidate_runtime_url") or "").strip()
            candidate_alive = bool(snapshot.get("candidate_managed_alive"))
            candidate_ready = bool(snapshot.get("candidate_runtime_api_ready"))
            if candidate_slot != resolved_target:
                return {
                    "state": "failed",
                    "message": "candidate prewarm refresh failed: candidate slot changed before shutdown",
                    "runtime": snapshot,
                }
            memory_guard = self._candidate_memory_guard_snapshot(snapshot)
            if not bool(memory_guard.get("allowed")):
                cleanup = await self._cleanup_candidate_runtime(
                    reason="supervisor.candidate.memory_blocked",
                    slot=resolved_target,
                    graceful=False,
                )
                return {
                    "state": "memory_blocked",
                    "message": self._candidate_memory_guard_message(memory_guard),
                    "runtime": snapshot,
                    "candidate_memory_guard": memory_guard,
                    "candidate_cleanup": cleanup,
                }
            beacon_ready = bool(candidate_ready and candidate_url) and await asyncio.to_thread(
                _runtime_beacon_ready,
                candidate_url,
                token=self.token,
            )
            if candidate_ready and beacon_ready:
                return {
                    "state": "ready",
                    "message": f"passive candidate runtime is ready on {candidate_url or resolved_target}",
                    "ready_at": time.time(),
                    "runtime": snapshot,
                }
            if not candidate_alive:
                return {
                    "state": "failed",
                    "message": "candidate prewarm refresh failed: passive candidate runtime stopped before shutdown",
                    "runtime": snapshot,
                }
            if timeout_sec <= 0.0 or time.time() >= deadline:
                return {
                    "state": "starting",
                    "message": (
                        f"passive candidate runtime beacon is still warming on {candidate_url or resolved_target}"
                        if candidate_ready
                        else f"passive candidate runtime is still warming on {candidate_url or resolved_target}"
                    ),
                    "runtime": snapshot,
                }
            await asyncio.sleep(0.25)

    async def _cleanup_candidate_runtime(
        self,
        *,
        reason: str,
        slot: str | None = None,
        graceful: bool = True,
    ) -> dict[str, Any]:
        resolved_slot = str(slot or "").strip().upper() or None
        async with self._lock:
            current_slot = str(self._candidate_slot or "").strip().upper() or None
            if self._candidate_proc is None or (resolved_slot and current_slot != resolved_slot):
                self._persist_runtime_state()
                return {
                    "ok": True,
                    "stopped": False,
                    "slot": current_slot,
                }
            await self._terminate_candidate_proc_locked(graceful=graceful, reason=reason)
            self._persist_runtime_state()
            return {
                "ok": True,
                "stopped": True,
                "slot": current_slot,
            }

    async def _promote_candidate_runtime_locked(self, *, slot: str, reason: str) -> dict[str, Any]:
        resolved_slot = str(slot or "").strip().upper()
        current_candidate_slot = str(self._candidate_slot or "").strip().upper()
        candidate_proc = self._candidate_proc
        if resolved_slot not in {"A", "B"}:
            raise RuntimeError("candidate slot is unavailable for fast cutover")
        if current_candidate_slot != resolved_slot:
            raise RuntimeError("candidate runtime slot does not match the prepared target slot")
        if candidate_proc is None or candidate_proc.poll() is not None:
            raise RuntimeError("candidate runtime is not running for fast cutover")
        memory_guard = self._candidate_memory_guard_snapshot()
        if not bool(memory_guard.get("allowed")):
            raise RuntimeError(self._candidate_memory_guard_message(memory_guard))

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-AdaOS-Token"] = self.token
        candidate_base_url = self.slot_runtime_base_url(resolved_slot)
        response = await asyncio.to_thread(
            requests.post,
            candidate_base_url + "/api/admin/runtime/promote-active",
            headers=headers,
            json={"reason": reason, "reconnect_hub_root": True},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("candidate promotion returned a non-object payload")
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        promoted_role = str(runtime.get("transition_role") or "").strip().lower()
        if promoted_role != "active":
            raise RuntimeError("candidate runtime did not report active role after promotion")
        reconnect = payload.get("reconnect") if isinstance(payload.get("reconnect"), dict) else {}
        authority = reconnect.get("authority") if isinstance(reconnect.get("authority"), dict) else {}
        authority_required = authority.get("required") is not False
        if not bool(reconnect.get("ok")) or (authority_required and authority.get("ready") is not True):
            raise RuntimeError("candidate runtime did not acquire hub-root route authority")
        promoted_instance_id = str(runtime.get("runtime_instance_id") or self._candidate_runtime_instance_id or "").strip() or None
        proc = self._candidate_proc
        if proc is None or proc.poll() is not None:
            raise RuntimeError("candidate runtime exited before supervisor adopted it")
        self._process_supervisor.track_active(proc)
        self._managed_runtime_instance_id = promoted_instance_id
        self._managed_transition_role = "active"
        self._managed_slot = resolved_slot
        self._managed_runtime_port = self.slot_runtime_port(resolved_slot)
        self._managed_runtime_base_url = self.slot_runtime_base_url(resolved_slot)
        self._managed_runtime_cwd = self._candidate_runtime_cwd
        self._managed_runtime_api_identity_verified = False
        self._managed_runtime_api_identity_observed_at = None
        self._managed_runtime_api_identity = {}
        self._process_supervisor.track_candidate(None)
        self._candidate_slot = None
        self._candidate_runtime_instance_id = None
        self._candidate_transition_role = None
        self._candidate_runtime_cwd = None
        self._last_start_at = time.time()
        self._last_error = None
        self._restart_count += 1
        self._persist_runtime_state()
        return payload

    async def _promote_candidate_runtime(self, *, slot: str, reason: str) -> dict[str, Any]:
        async with self._lock:
            return await self._promote_candidate_runtime_locked(slot=slot, reason=reason)

    async def _monitor_iteration_loop(self) -> None:
        await self._monitoring.run_iteration_loop(
            self,
            self._monitoring_operations(),
        )

    async def monitor_forever(self) -> None:
        """Resume monitoring after a bounded iteration failure.

        Mutating transitions retain their durable status and attempt guards, so
        restarting this scheduler resumes reconciliation instead of blindly
        replaying a completed command.
        """
        while not self._stopping:
            started_at = time.time()
            self._monitor_loop_started_at = started_at
            try:
                await self._monitor_iteration_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                lived_for = max(0.0, time.time() - started_at)
                if lived_for >= 60.0:
                    self._monitor_failure_total = 0
                self._monitor_failure_total += 1
                self._monitor_recovery_total += 1
                self._monitor_last_failure_at = time.time()
                self._monitor_last_failure = f"{type(exc).__name__}: {exc}"
                self._last_error = f"supervisor monitor recovered after {self._monitor_last_failure}"
                self._persist_runtime_state()
                self_heal_attempted = False
                try:
                    self_heal_attempted = await self._maybe_self_heal_runtime()
                except Exception:
                    _LOG.warning("runtime self-heal fault boundary failed after monitor error", exc_info=True)
                delay_sec = (
                    1.0
                    if self_heal_attempted
                    else min(30.0, float(2 ** min(4, max(0, self._monitor_failure_total - 1))))
                )
                _LOG.exception(
                    "supervisor monitor iteration failed; resuming from durable state in %.1fs",
                    delay_sec,
                )
                await asyncio.sleep(delay_sec)
            else:
                if not self._stopping:
                    self._monitor_failure_total += 1
                    self._monitor_recovery_total += 1
                    self._monitor_last_failure_at = time.time()
                    self._monitor_last_failure = "RuntimeError: monitor loop returned unexpectedly"
                    await asyncio.sleep(1.0)

    async def start(self) -> None:
        try:
            await self.ensure_sidecar_started()
        except Exception:
            _LOG.warning("failed to start adaos-realtime sidecar", exc_info=True)
        await self.ensure_started(reason="supervisor.start")
        self._process_supervisor.start_monitor(self.monitor_forever)

    async def close(self) -> None:
        self._stopping = True
        await self._update_state_machine.cancel_task(mode="cancelled")
        self._release_skill_runtime_migration_gate(reason="supervisor_close")
        await self._process_supervisor.stop_monitor()
        preserve_managed_children = self._service_restart_pending or _autostart_self_restart_supported()
        if preserve_managed_children:
            reaper = self._schedule_managed_handoff_reaper()
            retired_cleanup = self._schedule_retired_runtime_cleanup()
            _LOG.info(
                "supervisor restart handoff preserving runtime pid=%s and sidecar pid=%s reaper=%s "
                "retired_cleanup=%s",
                getattr(self._proc, "pid", None),
                getattr(self._sidecar_proc, "pid", None),
                reaper,
                retired_cleanup,
            )
            self._persist_runtime_state()
            return
        async with self._lock:
            await self._terminate_candidate_proc_locked(graceful=True, reason="supervisor.shutdown.candidate")
        await self.stop(reason="supervisor.shutdown")
        try:
            await self.stop_sidecar(reason="supervisor.shutdown.sidecar")
        except Exception:
            _LOG.warning("failed to stop adaos-realtime sidecar", exc_info=True)

    def status(
        self,
        *,
        runtime_api_timeout: float = 0.75,
        refresh: bool = False,
    ) -> dict[str, Any]:
        if refresh:
            self._refresh_status_snapshot(
                reason="explicit_refresh",
                runtime_api_timeout=runtime_api_timeout,
            )
        with self._status_snapshot_lock:
            payload = copy.deepcopy(self._status_snapshot)
            generation = int(self._status_snapshot_generation)
            observed_at = self._status_snapshot_observed_at
            reason = self._status_snapshot_reason
            durable_updated_at = self._status_durable_updated_at
        age_s = max(0.0, time.time() - float(observed_at)) if observed_at is not None else None
        stale_after_s = 5.0
        payload["status_read_model"] = {
            "schema": "adaos.supervisor_status_read_model.v1",
            "mode": "event_projection",
            "read_only": True,
            "generation": generation,
            "observed_at": observed_at,
            "age_s": round(age_s, 3) if age_s is not None else None,
            "stale_after_s": stale_after_s,
            "stale": age_s is None or age_s > stale_after_s,
            "reason": reason,
            "durable_state_updated_at": durable_updated_at,
        }
        payload["update_task_running"] = self._update_state_machine.task_running()
        payload["workload_admission"] = {
            "core_update_holds_skill_migration_gate": self._skill_runtime_migration_gate_lease is not None,
            "skill_migration_lease_path": self._skill_runtime_migration_lease_path,
        }
        return payload

    def supervisor_update_status(self) -> dict[str, Any]:
        if self._update_state_machine.task_running():
            return self._local_supervisor_update_status_payload(runtime_api_timeout=0.1)
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-AdaOS-Token"] = self.token
        try:
            with requests.get(
                self.runtime_base_url + "/api/admin/update/status",
                headers=headers,
                timeout=5.0,
            ) as response:
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, dict):
                payload.setdefault("runtime", self.status())
                payload["_served_by"] = "runtime"
                return _reconcile_update_status(payload)
        except Exception:
            pass
        return self._local_supervisor_update_status_payload()

    def public_update_status(self) -> dict[str, Any]:
        return _public_update_status_payload(self._local_supervisor_update_status_payload(runtime_api_timeout=0.1))

    def public_memory_status(self) -> dict[str, Any]:
        return self._memory_profiling.public_status(
            self,
            self._memory_operations(),
        )

    async def _request_runtime_shutdown(self, *, reason: str, drain_timeout_sec: float, signal_delay_sec: float) -> dict[str, Any]:
        async with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._desired_running = True
                self._persist_runtime_state()
                return {"ok": True, "accepted": False, "reason": "runtime not running"}
            try:
                headers = {"Content-Type": "application/json"}
                if self.token:
                    headers["X-AdaOS-Token"] = self.token
                runtime_base_url = self._managed_proc_base_url(proc)
                response = await asyncio.to_thread(
                    requests.post,
                    runtime_base_url + "/api/admin/shutdown",
                    headers=headers,
                    json={
                        "reason": reason,
                        "drain_timeout_sec": float(drain_timeout_sec),
                        "signal_delay_sec": float(signal_delay_sec),
                    },
                    timeout=_runtime_shutdown_request_timeout(
                        drain_timeout_sec=drain_timeout_sec,
                        signal_delay_sec=signal_delay_sec,
                    ),
                )
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {"ok": True, "response": payload}
            except Exception as exc:
                self._last_error = f"shutdown request failed: {type(exc).__name__}: {exc}"
                self._persist_runtime_state()
                raise HTTPException(status_code=503, detail=f"runtime shutdown API unavailable: {type(exc).__name__}: {exc}") from exc

    async def _ensure_runtime_stopped_for_update(
        self,
        *,
        drain_timeout_sec: float,
        signal_delay_sec: float,
        reason: str,
    ) -> dict[str, Any]:
        graceful_deadline = time.time() + max(3.0, float(drain_timeout_sec) + float(signal_delay_sec) + 3.0)
        while time.time() < graceful_deadline:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return {"ok": True, "forced": False, "reason": reason}
            await asyncio.sleep(0.2)

        forced = False
        async with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                forced = True
                with contextlib.suppress(Exception):
                    proc.terminate()
                kill_deadline = time.time() + 5.0
                while time.time() < kill_deadline:
                    if proc.poll() is not None:
                        break
                    await asyncio.sleep(0.1)
                if proc.poll() is None:
                    with contextlib.suppress(Exception):
                        proc.kill()
                    final_deadline = time.time() + 5.0
                    while time.time() < final_deadline:
                        if proc.poll() is not None:
                            break
                        await asyncio.sleep(0.1)
                if proc.poll() is None:
                    raise RuntimeError(f"runtime process did not exit after forced stop: {reason}")
                self._last_error = f"forced runtime stop after shutdown timeout: {reason}"
                self._persist_runtime_state()
        return {"ok": True, "forced": forced, "reason": reason}

    def _begin_countdown_transition(self, request: dict[str, Any], *, countdown_sec: float | None = None) -> dict[str, Any]:
        countdown_value = max(0.0, float(request.get("countdown_sec") if countdown_sec is None else countdown_sec))
        request_payload = dict(request)
        request_payload["countdown_sec"] = countdown_value
        status = self._update_state_machine.persist_transition(
            status_payload={
                "state": "countdown",
                "phase": "countdown",
                "action": str(request.get("action") or "update"),
                "target_rev": str(request.get("target_rev") or ""),
                "target_version": str(request.get("target_version") or ""),
                "reason": str(request.get("reason") or ""),
                "countdown_sec": countdown_value,
                "drain_timeout_sec": float(request.get("drain_timeout_sec") or 10.0),
                "signal_delay_sec": float(request.get("signal_delay_sec") or 0.25),
                "started_at": time.time(),
                "scheduled_for": time.time() + countdown_value,
            },
            attempt_payload=lambda persisted_status: _build_attempt_payload(
                action=str(request_payload.get("action") or "update"),
                request=request_payload,
                status=persisted_status,
                accepted=True,
            ),
        )
        self._update_state_machine.start_task(
            f"adaos-supervisor-core-update-{request_payload.get('action') or 'update'}",
            lambda: self._countdown_update_worker(
                action=str(request_payload.get("action") or "update"),
                target_rev=str(request_payload.get("target_rev") or ""),
                target_version=str(request_payload.get("target_version") or ""),
                reason=str(request_payload.get("reason") or ""),
                countdown_sec=countdown_value,
                drain_timeout_sec=float(request_payload.get("drain_timeout_sec") or 10.0),
                signal_delay_sec=float(request_payload.get("signal_delay_sec") or 0.25),
            ),
        )
        return {"ok": True, "accepted": True, "status": status, "_served_by": "supervisor"}

    def _begin_prepare_transition(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._acquire_skill_runtime_migration_gate(request=request):
            migration = _active_skill_runtime_migration() or {}
            return {
                "ok": True,
                "accepted": False,
                "deferred": True,
                "retryable": True,
                "retry_after_sec": 15.0,
                "reason": "skill_runtime_migration_active",
                "migration": {
                    "operation_id": migration.get("operation_id"),
                    "state": migration.get("state"),
                    "phase": migration.get("phase"),
                    "pending": bool(migration.get("pending", True)),
                    "current": migration.get("current"),
                    "worker_pid": migration.get("worker_pid"),
                },
                "status": read_core_update_status(),
                "_served_by": "supervisor",
            }
        try:
            return self._begin_prepare_transition_admitted(request)
        except Exception:
            self._release_skill_runtime_migration_gate(reason="prepare_admission_failed")
            raise

    def _begin_prepare_transition_admitted(self, request: dict[str, Any]) -> dict[str, Any]:
        started_at = time.time()
        try:
            candidate_prewarm_deferral_count = max(
                0,
                int(request.get("candidate_prewarm_deferral_count") or 0),
            )
        except Exception:
            candidate_prewarm_deferral_count = 0
        prepare_lease_token = uuid.uuid4().hex
        prepare_lease_path = _prepare_lease_path(prepare_lease_token)
        prepare_timeout_sec = _update_prepare_timeout_sec()
        _write_prepare_lease(
            prepare_lease_path,
            token=prepare_lease_token,
            state="active",
            reason=str(request.get("reason") or "core_update.prepare"),
            action=str(request.get("action") or "update"),
            target_rev=str(request.get("target_rev") or ""),
            target_version=str(request.get("target_version") or ""),
            created_at=started_at,
            timeout_sec=prepare_timeout_sec,
        )
        status_payload = {
            "state": "preparing",
            "phase": "prepare",
            "action": str(request.get("action") or "update"),
            "target_rev": str(request.get("target_rev") or ""),
            "target_version": str(request.get("target_version") or ""),
            "reason": str(request.get("reason") or ""),
            "countdown_sec": float(request.get("countdown_sec") or 0.0),
            "drain_timeout_sec": float(request.get("drain_timeout_sec") or 10.0),
            "signal_delay_sec": float(request.get("signal_delay_sec") or 0.25),
            "started_at": started_at,
            "message": "preparing inactive slot before restart",
            "prepare_timeout_sec": prepare_timeout_sec,
            "prepare_lease_path": str(prepare_lease_path),
            "prepare_lease_token": prepare_lease_token,
            "candidate_prewarm_deferral_count": candidate_prewarm_deferral_count,
            "candidate_prewarm_max_deferrals": _warm_switch_max_deferrals(),
        }

        def _prepare_attempt(persisted_status: dict[str, Any]) -> dict[str, Any]:
            attempt_payload = dict(request)
            attempt_payload.update(
                {
                    "state": "active",
                    "accepted": True,
                    "requested_at": _epoch(request.get("requested_at")) or started_at,
                    "prepare_started_at": started_at,
                    "prepare_timeout_sec": prepare_timeout_sec,
                    "prepare_lease_path": str(prepare_lease_path),
                    "prepare_lease_token": prepare_lease_token,
                    "last_status": persisted_status,
                    "updated_at": started_at,
                }
            )
            return attempt_payload

        status = self._update_state_machine.persist_transition(
            status_payload=status_payload,
            attempt_payload=_prepare_attempt,
        )
        self._update_state_machine.start_task(
            f"adaos-supervisor-core-update-prepare-{request.get('action') or 'update'}",
            lambda: self._prepare_and_countdown_update_worker(
                action=str(request.get("action") or "update"),
                target_rev=str(request.get("target_rev") or ""),
                target_version=str(request.get("target_version") or ""),
                reason=str(request.get("reason") or ""),
                countdown_sec=float(request.get("countdown_sec") or 0.0),
                drain_timeout_sec=float(request.get("drain_timeout_sec") or 10.0),
                signal_delay_sec=float(request.get("signal_delay_sec") or 0.25),
                prepare_lease_path=str(prepare_lease_path),
                prepare_lease_token=prepare_lease_token,
                prepare_timeout_sec=prepare_timeout_sec,
                candidate_prewarm_deferral_count=candidate_prewarm_deferral_count,
            ),
        )
        return {"ok": True, "accepted": True, "status": status, "_served_by": "supervisor"}

    def _acquire_skill_runtime_migration_gate(self, *, request: dict[str, Any]) -> bool:
        if self._skill_runtime_migration_gate_lease is not None:
            return True
        try:
            from adaos.services.skill.runtime_migration_worker import _try_acquire_global_lease

            operation_id = "core-update-" + uuid.uuid4().hex[:12]
            lease_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=current_base_dir))
            lease = _try_acquire_global_lease(lease_ctx, operation_id=operation_id)
        except Exception:
            _LOG.warning("failed to acquire core/skill workload admission lease", exc_info=True)
            return False
        if lease is None:
            return False
        self._skill_runtime_migration_gate_lease = lease
        _LOG.info(
            "core update acquired skill migration gate action=%s target=%s",
            str(request.get("action") or "update"),
            str(request.get("target_version") or request.get("target_rev") or ""),
        )
        return True

    def _release_skill_runtime_migration_gate(self, *, reason: str) -> None:
        lease = self._skill_runtime_migration_gate_lease
        if lease is None:
            return
        self._skill_runtime_migration_gate_lease = None
        try:
            from adaos.services.skill.runtime_migration_worker import _release_global_lease

            _release_global_lease(lease)
        except Exception:
            _LOG.warning("failed to release core/skill workload admission lease", exc_info=True)
        else:
            _LOG.info("core update released skill migration gate reason=%s", reason)

    def _schedule_planned_transition(
        self,
        request: dict[str, Any],
        *,
        scheduled_for: float,
        planned_reason: str,
        message: str,
        extra_status: dict[str, Any] | None = None,
        extra_attempt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        due_at = max(time.time(), float(scheduled_for))
        status_payload = {
            "state": "planned",
            "phase": "scheduled",
            "action": str(request.get("action") or "update"),
            "target_rev": str(request.get("target_rev") or ""),
            "target_version": str(request.get("target_version") or ""),
            "reason": str(request.get("reason") or ""),
            "countdown_sec": float(request.get("countdown_sec") or 0.0),
            "drain_timeout_sec": float(request.get("drain_timeout_sec") or 10.0),
            "signal_delay_sec": float(request.get("signal_delay_sec") or 0.25),
            "min_update_period_sec": _min_update_period_sec(),
            "planned_reason": planned_reason,
            "scheduled_for": due_at,
            "message": message,
        }
        if isinstance(extra_status, dict):
            status_payload.update(extra_status)

        def _planned_attempt(persisted_status: dict[str, Any]) -> dict[str, Any]:
            payload = dict(request)
            payload.update(
                {
                    "state": "planned",
                    "accepted": True,
                    "scheduled_for": due_at,
                    "planned_reason": planned_reason,
                    "min_update_period_sec": _min_update_period_sec(),
                    "last_status": persisted_status,
                    "updated_at": time.time(),
                }
            )
            if isinstance(extra_attempt, dict):
                payload.update(extra_attempt)
            return payload

        status = self._update_state_machine.persist_transition(
            status_payload=status_payload,
            attempt_payload=_planned_attempt,
        )
        return {"ok": True, "accepted": True, "planned": True, "status": status, "_served_by": "supervisor"}

    def _deduplicate_active_slot_transition(
        self,
        *,
        request: dict[str, Any],
        current_status: dict[str, Any] | None,
        current_attempt: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = time.time()
        basis = read_core_update_last_result() or current_status or {}
        basis_map = basis if isinstance(basis, dict) else {}
        keep_completion_timestamps = (
            str(basis_map.get("state") or "").strip().lower() in {"succeeded", "validated"}
            and _transition_request_same_target(request, basis_map)
        )
        status_payload = dict(basis_map)
        _clear_stale_deduplicated_status_fields(
            status_payload,
            keep_completion_timestamps=keep_completion_timestamps,
        )
        status_payload.update(
            {
                "state": "succeeded",
                "phase": "validate",
                "action": "update",
                "target_rev": str(request.get("target_rev") or status_payload.get("target_rev") or ""),
                "target_version": str(request.get("target_version") or status_payload.get("target_version") or ""),
                "reason": str(request.get("reason") or status_payload.get("reason") or ""),
                "message": "core update target already active; request deduplicated",
                "same_target_deduped_at": now,
                "same_target_deduped_reason": "active_slot_same_target",
                "same_target_target_version": str(request.get("target_version") or ""),
                "subsequent_transition": False,
                "subsequent_transition_requested_at": None,
                "scheduled_for": None,
                "candidate_prewarm_state": None,
                "candidate_prewarm_message": None,
                "candidate_prewarm_ready_at": None,
                "updated_at": now,
            }
        )
        for key in (
            "subsequent_transition_action",
            "subsequent_transition_target_rev",
            "subsequent_transition_target_version",
        ):
            status_payload.pop(key, None)
        clear_core_update_plan()
        status = write_core_update_status(status_payload)

        attempt_payload = dict(current_attempt or {})
        attempt_payload.update(
            {
                "state": "deduplicated",
                "action": "update",
                "accepted": True,
                "deduplicated": True,
                "same_target": True,
                "same_target_deduped_at": now,
                "same_target_deduped_reason": "active_slot_same_target",
                "target_rev": str(request.get("target_rev") or ""),
                "target_version": str(request.get("target_version") or ""),
                "reason": str(request.get("reason") or ""),
                "scheduled_for": None,
                "planned_reason": None,
                "completed_at": None,
                "completion_reason": "",
                "awaiting_restart": False,
                "restart_required": False,
                "restart_mode": None,
                "restart_requested_at": None,
                "last_status": status,
                "updated_at": now,
            }
        )
        _replace_update_attempt(attempt_payload)
        return {
            "ok": True,
            "accepted": True,
            "planned": False,
            "deduplicated": True,
            "same_target": True,
            "status": status,
            "_served_by": "supervisor",
        }

    def _queue_subsequent_transition(
        self,
        *,
        request: dict[str, Any],
        current_status: dict[str, Any] | None,
        current_attempt: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = _epoch(request.get("requested_at")) or time.time()
        queued = dict(request)
        attempt = dict(current_attempt or {})
        if not attempt:
            attempt = {
                "state": "active",
                "action": str((current_status or {}).get("action") or request.get("action") or "update"),
                "requested_at": now,
                "updated_at": now,
                "last_status": dict(current_status or {}),
            }
        previous = _subsequent_transition_request(attempt)
        if _transition_request_same_target(queued, attempt) or _transition_request_same_target(queued, current_status):
            status = dict(current_status or read_core_update_status() or {})
            status["same_target_subsequent_deduped_at"] = now
            status["same_target_subsequent_deduped_reason"] = "active_transition_same_target"
            status["same_target_subsequent_target_version"] = str(queued.get("target_version") or "")
            status["updated_at"] = now
            status = write_core_update_status(status)
            return {
                "ok": True,
                "accepted": True,
                "deduplicated": True,
                "same_target": True,
                "status": status,
                "_served_by": "supervisor",
            }
        if previous and _transition_request_same_target(queued, previous):
            return {
                "ok": True,
                "accepted": True,
                "deferred": True,
                "deduplicated": True,
                "same_target": True,
                "subsequent_transition": True,
                "status": dict(current_status or read_core_update_status() or {}),
                "_served_by": "supervisor",
            }
        if previous:
            queued["first_requested_at"] = _epoch(previous.get("first_requested_at")) or _epoch(previous.get("requested_at")) or now
        attempt["subsequent_transition"] = True
        attempt["subsequent_transition_requested_at"] = now
        attempt["subsequent_transition_request"] = queued
        attempt["updated_at"] = now
        _write_update_attempt(attempt)

        status_payload = dict(current_status or read_core_update_status() or {})
        status_payload["subsequent_transition"] = True
        status_payload["subsequent_transition_requested_at"] = now
        status_payload["subsequent_transition_action"] = str(queued.get("action") or "update")
        status_payload["subsequent_transition_target_rev"] = str(queued.get("target_rev") or "")
        status_payload["subsequent_transition_target_version"] = str(queued.get("target_version") or "")
        status_payload["updated_at"] = time.time()
        status = write_core_update_status(status_payload)
        return {
            "ok": True,
            "accepted": True,
            "deferred": True,
            "subsequent_transition": True,
            "status": status,
            "_served_by": "supervisor",
        }

    async def _maybe_resume_or_continue_transition(self) -> None:
        payload = _reconcile_update_status(
            {
                "ok": True,
                "status": read_core_update_status(),
                "runtime": await asyncio.to_thread(self.status),
                "_served_by": "supervisor_monitor",
            }
        )
        status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        attempt = payload.get("attempt") if isinstance(payload.get("attempt"), dict) else _read_update_attempt() or {}
        status_state = str(status.get("state") or "").strip().lower()
        if status_state in {"validated", "succeeded", "failed", "cancelled", "canceled"}:
            self._release_skill_runtime_migration_gate(reason=f"update_status:{status_state}")
        if self._candidate_proc is not None and not _is_transition_in_progress(status, attempt):
            await self._cleanup_candidate_runtime(reason="supervisor.candidate.idle_cleanup")
            payload = _reconcile_update_status(
                {
                    "ok": True,
                    "status": read_core_update_status(),
                    "runtime": await asyncio.to_thread(self.status),
                    "_served_by": "supervisor_monitor",
                }
            )
            status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
            attempt = payload.get("attempt") if isinstance(payload.get("attempt"), dict) else _read_update_attempt() or {}
        if self._update_task is not None and not self._update_task.done():
            return

        attempt_state = str(attempt.get("state") or "").strip().lower()
        now = time.time()
        if attempt_state == "planned":
            scheduled_for = _epoch(attempt.get("scheduled_for") or status.get("scheduled_for"))
            if scheduled_for > 0.0 and scheduled_for <= now:
                request = _request_from_attempt(attempt)
                planned_reason = str(attempt.get("planned_reason") or status.get("planned_reason") or "").strip().lower()
                if planned_reason in {"candidate_cutover_failed", "candidate_cutover_recovery"}:
                    guard = await self._candidate_cutover_recovery_guard_snapshot()
                    stable_sec = _cutover_recovery_stable_sec()
                    ready_since = _epoch(
                        attempt.get("cutover_recovery_ready_since")
                        or status.get("cutover_recovery_ready_since")
                    )
                    stable = bool(guard.get("ready")) and ready_since > 0.0 and (now - ready_since) >= stable_sec
                    if not stable:
                        self._schedule_candidate_cutover_recovery_guard(
                            request=request,
                            status=status,
                            attempt=attempt,
                            guard=guard,
                            now=now,
                        )
                        return
                decision = await self._transition_continuity_guard_decision_async(
                    operation=str(request.get("action") or "update")
                )
                if decision is not None:
                    self._schedule_continuity_guarded_transition(
                        request,
                        decision,
                        current_status=status,
                        current_attempt=attempt,
                    )
                else:
                    if str(request.get("action") or "update").strip().lower() == "update":
                        self._begin_prepare_transition(request)
                    else:
                        self._begin_countdown_transition(request)
            return

        if attempt_state == "active" and str(status.get("state") or "").strip().lower() == "preparing":
            self._begin_prepare_transition(_request_from_attempt(attempt))
            return

        if attempt_state == "active" and str(status.get("state") or "").strip().lower() == "countdown":
            scheduled_for = _epoch(status.get("scheduled_for") or attempt.get("scheduled_for"))
            remaining = max(0.0, scheduled_for - now) if scheduled_for > 0.0 else float(attempt.get("countdown_sec") or 0.0)
            self._begin_countdown_transition(_request_from_attempt(attempt), countdown_sec=remaining)
            return

        if (
            _auto_update_complete_enabled()
            and _autostart_self_restart_supported()
            and not self._service_restart_pending
            and (
                _is_root_promotion_pending_status(status)
                or _is_root_restart_pending_status(status)
                or _is_root_restart_pending_attempt(attempt)
            )
        ):
            await self.complete_update(reason="supervisor.auto_update_complete", auto=True)
            return

        queued = _subsequent_transition_request(attempt)
        if queued and _is_terminal_update_status(status):
            if _transition_request_same_target(queued, status) or _transition_request_same_target(queued, attempt):
                _clear_same_target_subsequent_transition(
                    status=status,
                    attempt=attempt,
                    queued=queued,
                    reason="completed_transition_same_target",
                )
                return
            await self._cleanup_candidate_runtime(reason="supervisor.candidate.before_subsequent_transition")
            await self.start_update(
                action=str(queued.get("action") or "update"),
                target_rev=str(queued.get("target_rev") or ""),
                target_version=str(queued.get("target_version") or ""),
                reason=str(queued.get("reason") or "subsequent.transition"),
                countdown_sec=float(queued.get("countdown_sec") or 0.0),
                drain_timeout_sec=float(queued.get("drain_timeout_sec") or 10.0),
                signal_delay_sec=float(queued.get("signal_delay_sec") or 0.25),
                bypass_min_period=True,
            )

    async def _countdown_update_worker(self, **kwargs: Any) -> None:
        await self._update_execution.countdown(
            self,
            self._update_execution_operations(),
            **kwargs,
        )

    async def _prepare_and_countdown_update_worker(self, **kwargs: Any) -> None:
        await self._update_execution.prepare_and_countdown(
            self,
            self._update_execution_operations(),
            **kwargs,
        )

    async def start_update(
        self,
        *,
        action: str,
        target_rev: str,
        target_version: str,
        reason: str,
        countdown_sec: float,
        drain_timeout_sec: float,
        signal_delay_sec: float,
        bypass_min_period: bool = False,
    ) -> dict[str, Any]:
        request = _transition_request_payload(
            action=action,
            target_rev=target_rev,
            target_version=target_version,
            reason=reason,
            countdown_sec=countdown_sec,
            drain_timeout_sec=drain_timeout_sec,
            signal_delay_sec=signal_delay_sec,
        )
        disabled_reason = core_update_reactions_disabled_reason()
        if disabled_reason:
            return {
                "ok": True,
                "accepted": False,
                "skipped": True,
                "reason": disabled_reason,
                "status": read_core_update_status(),
                "_served_by": "supervisor",
            }
        current_status = read_core_update_status()
        current_attempt = _read_update_attempt()
        recovered_status = _recover_active_attempt_target_already_active(
            status=current_status,
            attempt=current_attempt,
            runtime=await asyncio.to_thread(self.status),
        )
        if isinstance(recovered_status, dict):
            current_status = recovered_status
            current_attempt = _read_update_attempt()
        if _is_transition_in_progress(current_status, current_attempt):
            if not _transition_request_has_resolved_target(request):
                status = dict(current_status or read_core_update_status() or {})
                status["ambiguous_subsequent_transition_rejected_at"] = time.time()
                status["ambiguous_subsequent_transition_target_version"] = str(request.get("target_version") or "")
                status["ambiguous_subsequent_transition_reason"] = "unresolved_update_target"
                status = write_core_update_status(status)
                return {
                    "ok": True,
                    "accepted": False,
                    "deferred": False,
                    "reason": "unresolved_subsequent_transition_target",
                    "status": status,
                    "_served_by": "supervisor",
                }
            return self._queue_subsequent_transition(
                request=request,
                current_status=current_status,
                current_attempt=current_attempt,
            )
        if action == "update":
            migration = await asyncio.to_thread(_active_skill_runtime_migration)
            if migration is not None:
                return {
                    "ok": True,
                    "accepted": False,
                    "deferred": True,
                    "retryable": True,
                    "retry_after_sec": 15.0,
                    "reason": "skill_runtime_migration_active",
                    "migration": {
                        "operation_id": migration.get("operation_id"),
                        "state": migration.get("state"),
                        "phase": migration.get("phase"),
                        "pending": bool(migration.get("pending", True)),
                        "current": migration.get("current"),
                        "worker_pid": migration.get("worker_pid"),
                    },
                    "status": current_status,
                    "_served_by": "supervisor",
                }
        if _transition_request_matches_active_slot(request):
            if _planned_transition_active(current_status, current_attempt) and not (
                _transition_request_same_target(request, current_attempt)
                or _transition_request_same_target(request, current_status)
            ):
                return {
                    "ok": True,
                    "accepted": True,
                    "planned": True,
                    "deduplicated": True,
                    "same_target": True,
                    "preserved_planned_transition": True,
                    "status": dict(current_status or read_core_update_status() or {}),
                    "_served_by": "supervisor",
                }
            return self._deduplicate_active_slot_transition(
                request=request,
                current_status=current_status,
                current_attempt=current_attempt,
            )

        decision = await self._transition_continuity_guard_decision_async(operation=action)
        if decision is not None:
            return self._schedule_continuity_guarded_transition(
                request,
                decision,
                current_status=current_status,
                current_attempt=current_attempt,
            )

        if str((current_attempt or {}).get("state") or "").strip().lower() == "planned" and action == "update":
            scheduled_for = _epoch((current_attempt or {}).get("scheduled_for") or current_status.get("scheduled_for")) or time.time()
            return self._schedule_planned_transition(
                request=request,
                scheduled_for=scheduled_for,
                planned_reason=str((current_attempt or {}).get("planned_reason") or "minimum_update_period"),
                message="planned core update refreshed while waiting for scheduled window",
            )

        if action == "update" and not bypass_min_period:
            min_period_sec = _min_update_period_sec()
            last_completed_at = _last_update_completion_at(current_status, current_attempt)
            next_allowed_at = last_completed_at + min_period_sec
            if min_period_sec > 0.0 and last_completed_at > 0.0 and next_allowed_at > time.time():
                return self._schedule_planned_transition(
                    request=request,
                    scheduled_for=next_allowed_at,
                    planned_reason="minimum_update_period",
                    message="core update deferred until minimum update interval elapses",
                )

        clear_core_update_plan()
        if action == "update":
            return self._begin_prepare_transition(request)
        return self._begin_countdown_transition(request)

    async def cancel_update(self, *, reason: str) -> dict[str, Any]:
        task = self._update_task
        clear_core_update_plan()
        current_attempt = _read_update_attempt() or {}
        current_status = read_core_update_status()
        if str(current_attempt.get("state") or "").strip().lower() == "planned":
            self._release_skill_runtime_migration_gate(reason="planned_update_cancelled")
            status = write_core_update_status(
                {
                    "state": "cancelled",
                    "phase": "scheduled",
                    "action": str(current_status.get("action") or current_attempt.get("action") or "update"),
                    "message": "planned core update cancelled by request",
                    "reason": reason,
                }
            )
            _complete_update_attempt(state="cancelled", status=status, reason=reason)
            return {"ok": True, "accepted": True, "status": status, "_served_by": "supervisor"}

        if task is None or task.done():
            self._release_skill_runtime_migration_gate(reason="inactive_update_cancelled")
            current_phase = str(current_status.get("phase") or "").strip().lower() or "countdown"
            status = write_core_update_status(
                {
                    "state": "cancelled",
                    "phase": current_phase,
                    "message": "no pending countdown task",
                    "reason": reason,
                }
            )
            _complete_update_attempt(state="cancelled", status=status, reason=reason)
            self._update_state_machine.release_finished_task(task)
            return {"ok": True, "accepted": False, "status": status, "_served_by": "supervisor"}

        await self._update_state_machine.cancel_task(mode="cancelled")
        self._release_skill_runtime_migration_gate(reason="active_update_cancelled")
        current_phase = str((read_core_update_status() or {}).get("phase") or "").strip().lower() or "countdown"
        status = write_core_update_status(
            {
                "state": "cancelled",
                "phase": current_phase,
                "action": str((read_core_update_status() or {}).get("action") or "update"),
                "message": "core update cancelled by request",
                "reason": reason,
                "drain_timeout_sec": float((read_core_update_status() or {}).get("drain_timeout_sec") or 10.0),
                "signal_delay_sec": float((read_core_update_status() or {}).get("signal_delay_sec") or 0.25),
            }
        )
        _complete_update_attempt(state="cancelled", status=status, reason=reason)
        return {"ok": True, "accepted": True, "status": status, "_served_by": "supervisor"}

    async def defer_update(self, *, delay_sec: float, reason: str) -> dict[str, Any]:
        delay_value = max(0.0, float(delay_sec))
        current_attempt = _read_update_attempt() or {}
        current_status = read_core_update_status()
        attempt_state = str(current_attempt.get("state") or "").strip().lower()
        status_state = str(current_status.get("state") or "").strip().lower()
        if attempt_state not in {"planned", "active"} and status_state not in {"planned", "countdown"}:
            raise HTTPException(status_code=409, detail="defer requires a planned update or active countdown")

        if self._update_task is not None and not self._update_task.done():
            await self._update_state_machine.cancel_task(mode="rescheduled")

        request = _request_from_attempt(current_attempt or current_status)
        scheduled_for = time.time() + delay_value
        return self._schedule_planned_transition(
            request=request,
            scheduled_for=scheduled_for,
            planned_reason="operator_defer",
            message="core update deferred by request",
        )

    async def promote_root(self, *, reason: str) -> dict[str, Any]:
        with _try_update_transition_guard(operation="update.root_promotion") as acquired:
            if not acquired:
                return {
                    "ok": True,
                    "accepted": False,
                    "deferred": True,
                    "retryable": True,
                    "reason": "update_transition_guard_busy",
                    "status": read_core_update_status(),
                    "attempt": _read_update_attempt() or {},
                    "_served_by": "supervisor_transition_busy",
                }
            return await self._promote_root_guarded(reason=reason)

    async def _promote_root_guarded(self, *, reason: str) -> dict[str, Any]:
        current_status = read_core_update_status()
        current_attempt = _read_update_attempt() or {}
        state = str(current_status.get("state") or "").strip().lower()
        phase = str(current_status.get("phase") or "").strip().lower()
        manifest = active_slot_manifest()
        expected_target_version = str(
            current_status.get("target_version") or current_attempt.get("target_version") or ""
        ).strip()
        if expected_target_version and not _manifest_matches_target_version(manifest, expected_target_version):
            status = write_core_update_status(
                {
                    "state": "failed",
                    "phase": "validate",
                    "message": "active slot does not match requested update target; refusing root promotion completion",
                    "target_rev": str(current_status.get("target_rev") or current_attempt.get("target_rev") or ""),
                    "target_version": expected_target_version,
                    "target_slot": str((manifest or {}).get("slot") or active_slot() or ""),
                    "manifest": manifest,
                    "root_promotion_refused": True,
                    "root_promotion_refused_reason": "active_slot_target_mismatch",
                    "finished_at": time.time(),
                }
            )
            _complete_update_attempt(state="failed", status=status, reason="active slot target mismatch")
            return {"ok": False, "accepted": False, "status": status, "_served_by": "supervisor"}
        root_promotion_required, bootstrap_update = resolved_root_promotion_requirement(manifest)
        if state not in {"validated", "succeeded"} and phase != "root_promotion_pending" and not root_promotion_required:
            raise HTTPException(status_code=409, detail="root promotion requires a validated slot runtime")
        if not root_promotion_required:
            status = write_core_update_status(
                {
                    "state": "succeeded",
                    "phase": "validate",
                    "message": "no root promotion required for the active slot",
                    "target_slot": str((manifest or {}).get("slot") or active_slot() or ""),
                    "manifest": manifest,
                    "root_promotion_required": False,
                    "bootstrap_update": bootstrap_update,
                    "scheduled_for": None,
                    "candidate_prewarm_state": None,
                    "candidate_prewarm_message": None,
                    "candidate_prewarm_ready_at": None,
                    "finished_at": time.time(),
                }
            )
            _complete_update_attempt(state="completed", status=status, reason=reason)
            return {"ok": True, "accepted": False, "status": status, "_served_by": "supervisor"}
        promotion_slot = str((manifest or {}).get("slot") or active_slot() or "")
        promotion_started_at = time.time()
        promoting_status = dict(current_status)
        promoting_status.update(
            {
                "state": "applying",
                "phase": "root_promotion",
                "message": "staging root bootstrap package for atomic promotion",
                "target_slot": promotion_slot,
                "manifest": manifest,
                "root_promotion_required": True,
                "bootstrap_update": bootstrap_update,
                "promotion_reason": reason,
                "root_promotion_started_at": promotion_started_at,
                "root_promotion_supervisor_instance_id": _SUPERVISOR_INSTANCE_ID,
                "root_promotion_supervisor_pid": os.getpid(),
                "root_promotion_supervisor_started_at": _SUPERVISOR_INSTANCE_STARTED_AT,
                "updated_at": promotion_started_at,
            }
        )
        promoting_status = write_core_update_status(promoting_status)
        promoting_attempt = dict(current_attempt)
        promoting_attempt.update(
            {
                "state": "active",
                "action": str(current_attempt.get("action") or current_status.get("action") or "update"),
                "accepted": True,
                "awaiting_restart": False,
                "restart_required": False,
                "requested_at": _epoch(current_attempt.get("requested_at")) or promotion_started_at,
                "transitioned_at": promotion_started_at,
                "updated_at": promotion_started_at,
                "completion_reason": "",
                "last_status": promoting_status,
            }
        )
        _write_update_attempt(promoting_attempt)
        promotion_task = asyncio.create_task(
            asyncio.to_thread(
                _promote_root_with_validated_candidate,
                slot=promotion_slot,
                manifest=manifest or {},
                runtime_host=self.runtime_host,
                runtime_port=self.runtime_port,
            ),
            name="adaos-supervisor-root-promotion",
        )
        try:
            promotion = await asyncio.shield(promotion_task)
        except asyncio.CancelledError:
            await promotion_task
            raise
        except Exception as exc:
            failed_at = time.time()
            failed_status = dict(promoting_status)
            failed_status.update(
                {
                    "state": "failed",
                    "phase": "root_promotion",
                    "message": f"root bootstrap promotion failed: {exc}",
                    "root_promotion_required": True,
                    "root_promotion_failed_at": failed_at,
                    "finished_at": failed_at,
                    "updated_at": failed_at,
                }
            )
            failed_status = write_core_update_status(failed_status)
            _complete_update_attempt(state="failed", status=failed_status, reason="root promotion failed")
            raise
        status_payload = dict(promoting_status)
        status_payload.update(
            {
                "state": "succeeded",
                "phase": "root_promoted",
                "message": "root bootstrap files promoted from validated slot; restart adaos.service to activate",
                "target_slot": promotion_slot,
                "manifest": manifest,
                "root_promotion_required": False,
                "bootstrap_update": bootstrap_update,
                "root_promotion": promotion,
                "promotion_reason": reason,
                "root_promotion_supervisor_instance_id": _SUPERVISOR_INSTANCE_ID,
                "root_promotion_supervisor_pid": os.getpid(),
                "root_promotion_supervisor_started_at": _SUPERVISOR_INSTANCE_STARTED_AT,
                "finished_at": time.time(),
                "updated_at": time.time(),
            }
        )
        status = write_core_update_status(status_payload)
        previous_attempt = _read_update_attempt() or {}
        now = time.time()
        awaiting_attempt = dict(previous_attempt)
        awaiting_attempt.update(
            {
                "state": "awaiting_root_restart",
                "action": str(previous_attempt.get("action") or "update"),
                "accepted": True,
                "awaiting_restart": True,
                "restart_required": True,
                "root_promotion_supervisor_instance_id": _SUPERVISOR_INSTANCE_ID,
                "root_promotion_supervisor_pid": os.getpid(),
                "root_promotion_supervisor_started_at": _SUPERVISOR_INSTANCE_STARTED_AT,
                "requested_at": _epoch(previous_attempt.get("requested_at")) or now,
                "transitioned_at": now,
                "updated_at": now,
                "completion_reason": "",
                "last_status": status,
            }
        )
        _write_update_attempt_preserving_subsequent_transition(awaiting_attempt)
        return {"ok": True, "accepted": True, "status": status, "root_promotion": promotion, "_served_by": "supervisor"}

    def proxy_update_post(self, path: str, *, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-AdaOS-Token"] = self.token
        try:
            response = requests.post(
                self.runtime_base_url + path,
                headers=headers,
                json=body,
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status") if isinstance(payload, dict) and isinstance(payload.get("status"), dict) else {}
            accepted = bool(payload.get("accepted", True)) if isinstance(payload, dict) else True
            if path.endswith("/update/start"):
                _replace_update_attempt(
                    _build_attempt_payload(action="update", request=body, status=status, accepted=accepted)
                )
            elif path.endswith("/update/rollback"):
                _replace_update_attempt(
                    _build_attempt_payload(action="rollback", request=body, status=status, accepted=accepted)
                )
            elif path.endswith("/update/cancel"):
                _complete_update_attempt(state="cancelled", status=status, reason=str(body.get("reason") or "cancelled"))
            if isinstance(payload, dict):
                payload["_served_by"] = "runtime"
                return payload
            return {"ok": True, "response": payload, "_served_by": "runtime"}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"runtime admin API unavailable: {type(exc).__name__}: {exc}") from exc


init_ctx()
async def _startup() -> None:
    args = _parse_args()
    manager = SupervisorManager(runtime_host=args.host, runtime_port=args.port, token=_resolved_token(args.token))
    app.state.manager = manager
    await manager.start()


async def _shutdown() -> None:
    manager = getattr(app.state, "manager", None)
    if manager is not None:
        await manager.close()

def _manager() -> SupervisorManager:
    manager = getattr(app.state, "manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="supervisor is not initialized")
    return manager


_API_ADAPTER = SupervisorApiAdapter(lambda: _manager())

app = create_supervisor_app(
    startup=_startup,
    shutdown=_shutdown,
    routes=create_supervisor_routes(_API_ADAPTER.handlers()),
)


def main() -> None:
    args = _parse_args()
    if args.token:
        os.environ["ADAOS_TOKEN"] = str(args.token)
    os.environ["ADAOS_SUPERVISOR_ENABLED"] = "1"
    os.environ["ADAOS_SUPERVISOR_URL"] = _supervisor_base_url()
    uvicorn.run(
        app,
        host=_supervisor_host(),
        port=_supervisor_port(),
        loop=_uvicorn_loop_mode(),
        reload=False,
        workers=1,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
