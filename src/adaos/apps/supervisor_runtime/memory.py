from __future__ import annotations

import os
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any


DEFAULT_MEMORY_SUSPICION_FAMILY_RSS_THRESHOLD_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MemoryProfilingOperations:
    http_exception_type: Any
    implemented_profile_control_actions: Any
    implemented_profile_control_mode: Any
    memory_operation_contract_version: Any
    profile_launch_env_keys: Any
    top_level_operation_events: Any
    available_memory_bytes: Any
    memory_auto_profile_browser_live_ttl_sec: Any
    memory_auto_profile_min_uptime_sec: Any
    memory_baseline_maturity_slope_bytes_per_min: Any
    memory_baseline_warmup_sec: Any
    memory_critical_available_bytes_threshold: Any
    memory_critical_available_percent_threshold: Any
    memory_critical_duration_sec: Any
    memory_critical_restart_cooldown_sec: Any
    memory_policy_profile_restarts_enabled: Any
    memory_suspicion_family_rss_threshold_bytes: Any
    memory_suspicion_growth_threshold_bytes: Any
    memory_suspicion_slope_threshold_bytes_per_min: Any
    memory_telemetry_interval_sec: Any
    memory_telemetry_window_sec: Any
    positive_int_or_none: Any
    proc_details: Any
    process_family_rss_bytes: Any
    runtime_memory_attribution_snapshot: Any
    total_memory_bytes: Any
    active_slot: Any
    append_memory_telemetry_sample: Any
    ensure_memory_store: Any
    read_memory_session_index: Any
    read_memory_telemetry_tail: Any
    supervisor_memory_sessions_index_path: Any
    supervisor_memory_telemetry_path: Any


class MemoryProfilingService:
    """Own memory profiling timing policy and process-family snapshots."""

    def __init__(self, *, default_profiler_adapter: str = "none") -> None:
        self.profiler_adapter_name = self.profiler_adapter(default_profiler_adapter)
        self.profile_mode = "normal"
        self.requested_profile_mode: str | None = None
        self.publish_request_session_id: str | None = None
        self.profile_current_trigger_source: str | None = None
        self.suspicion_state = "idle"
        self.suspicion_reason: str | None = None
        self.suspicion_since: float | None = None
        self.active_session_id: str | None = None
        self.profile_finalizing_session_id: str | None = None
        self.last_session_id: str | None = None
        self.baseline_scope_key: str | None = None
        self.baseline_pid: int | None = None
        self.baseline_family_rss_bytes: int | None = None
        self.baseline_started_at: float | None = None
        self.baseline_matured_at: float | None = None
        self.baseline_phase = "uninitialized"
        self.baseline_last_adjusted_at: float | None = None
        self.baseline_last_adjustment_reason: str | None = None
        self.baseline_adjustment_total = 0
        self.last_growth_bytes: int | None = None
        self.last_growth_bytes_per_min: float | None = None
        self.last_available_bytes: int | None = None
        self.last_available_percent: float | None = None
        self.last_telemetry_at: float | None = None
        self.auto_profile_last_block_reason: str | None = None
        self.auto_profile_last_block_at: float | None = None
        self.critical_since: float | None = None
        self.critical_reason: str | None = None
        self.critical_restart_last_at: float | None = None

    @staticmethod
    def profiler_adapter(default_adapter: str) -> str:
        token = str(os.getenv("ADAOS_SUPERVISOR_MEMORY_PROFILER") or "").strip().lower()
        return token or str(default_adapter or "").strip() or "none"

    @staticmethod
    def telemetry_interval_sec() -> float:
        try:
            return max(5.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_TELEMETRY_SEC") or "15").strip()))
        except Exception:
            return 15.0

    @staticmethod
    def telemetry_window_sec() -> float:
        try:
            return max(60.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_WINDOW_SEC") or "180").strip()))
        except Exception:
            return 180.0

    @staticmethod
    def baseline_warmup_sec() -> float:
        try:
            return max(0.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_BASELINE_WARMUP_SEC") or "300").strip()))
        except Exception:
            return 300.0

    @staticmethod
    def baseline_maturity_slope_bytes_per_min() -> float:
        try:
            return max(
                0.0,
                float(
                    str(
                        os.getenv("ADAOS_SUPERVISOR_MEMORY_BASELINE_MATURITY_SLOPE_BYTES_PER_MIN")
                        or str(32 * 1024 * 1024)
                    ).strip()
                ),
            )
        except Exception:
            return float(32 * 1024 * 1024)

    def suspicion_growth_threshold_bytes(self, *, psutil_module: Any | None) -> int:
        default_value = 1024 * 1024 * 1024
        total_memory = self.total_memory_bytes(psutil_module=psutil_module)
        if total_memory and total_memory > 0:
            default_value = min(
                1024 * 1024 * 1024,
                max(256 * 1024 * 1024, int(float(total_memory) * 0.20)),
            )
        try:
            return max(
                32 * 1024 * 1024,
                int(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_GROWTH_BYTES") or str(default_value)).strip()),
            )
        except Exception:
            return default_value

    @staticmethod
    def suspicion_family_rss_threshold_bytes() -> int | None:
        raw = os.getenv("ADAOS_SUPERVISOR_MEMORY_FAMILY_RSS_BYTES")
        if raw is None or not str(raw).strip():
            return DEFAULT_MEMORY_SUSPICION_FAMILY_RSS_THRESHOLD_BYTES
        text = str(raw).strip()
        if text.lower() in {"0", "false", "no", "off", "disabled", "none"}:
            return None
        try:
            value = int(text)
        except Exception:
            return DEFAULT_MEMORY_SUSPICION_FAMILY_RSS_THRESHOLD_BYTES
        return max(32 * 1024 * 1024, value)

    @staticmethod
    def suspicion_slope_threshold_bytes_per_min() -> float:
        try:
            return max(
                float(8 * 1024 * 1024),
                float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_SLOPE_BYTES_PER_MIN") or str(128 * 1024 * 1024)).strip()),
            )
        except Exception:
            return float(128 * 1024 * 1024)

    @staticmethod
    def auto_profile_cooldown_sec() -> float:
        try:
            return max(60.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_PROFILE_COOLDOWN_SEC") or "86400").strip()))
        except Exception:
            return 86400.0

    @staticmethod
    def policy_profile_restarts_enabled() -> bool:
        raw = os.getenv("ADAOS_SUPERVISOR_MEMORY_POLICY_PROFILE_RESTARTS")
        if raw is None:
            return True
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def auto_profile_min_uptime_sec() -> float:
        try:
            return max(
                0.0,
                float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC") or "300").strip()),
            )
        except Exception:
            return 300.0

    @staticmethod
    def auto_profile_browser_live_ttl_sec() -> float:
        try:
            return max(
                5.0,
                float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_BROWSER_LIVE_TTL_SEC") or "45").strip()),
            )
        except Exception:
            return 45.0

    @staticmethod
    def auto_profile_allow_browser_sessions() -> bool:
        raw = os.getenv("ADAOS_SUPERVISOR_MEMORY_PROFILE_ALLOW_BROWSER_SESSIONS")
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def auto_profile_circuit_window_sec() -> float:
        try:
            return max(300.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_PROFILE_CIRCUIT_WINDOW_SEC") or "1800").strip()))
        except Exception:
            return 1800.0

    @staticmethod
    def auto_profile_circuit_limit() -> int:
        try:
            return max(1, int(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_PROFILE_CIRCUIT_LIMIT") or "3").strip()))
        except Exception:
            return 3

    @staticmethod
    def available_memory_bytes(*, psutil_module: Any | None) -> int | None:
        if psutil_module is None:
            return None
        try:
            return int(psutil_module.virtual_memory().available)
        except Exception:
            return None

    @staticmethod
    def total_memory_bytes(*, psutil_module: Any | None) -> int | None:
        if psutil_module is None:
            return None
        try:
            return int(psutil_module.virtual_memory().total)
        except Exception:
            return None

    @staticmethod
    def critical_available_percent_threshold() -> float:
        try:
            return max(
                1.0,
                min(25.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_PERCENT") or "5").strip())),
            )
        except Exception:
            return 5.0

    @staticmethod
    def critical_available_bytes_threshold() -> int:
        try:
            return max(
                64 * 1024 * 1024,
                int(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_BYTES") or str(256 * 1024 * 1024)).strip()),
            )
        except Exception:
            return 256 * 1024 * 1024

    @staticmethod
    def critical_duration_sec() -> float:
        try:
            return max(5.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_DURATION_SEC") or "20").strip()))
        except Exception:
            return 20.0

    @staticmethod
    def critical_restart_cooldown_sec() -> float:
        try:
            return max(30.0, float(str(os.getenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_RESTART_COOLDOWN_SEC") or "120").strip()))
        except Exception:
            return 120.0

    @staticmethod
    def graceful_shutdown_timeouts(profile_mode: str) -> tuple[float, float, float, float]:
        if str(profile_mode or "normal").strip().lower() == "normal":
            return 5.0, 0.25, 8.0, 5.0

        def _setting(name: str, default: float, minimum: float) -> float:
            try:
                return max(minimum, float(str(os.getenv(name) or default).strip()))
            except Exception:
                return default

        return (
            _setting("ADAOS_SUPERVISOR_PROFILE_DRAIN_TIMEOUT_SEC", 20.0, 5.0),
            _setting("ADAOS_SUPERVISOR_PROFILE_SIGNAL_DELAY_SEC", 1.0, 0.25),
            _setting("ADAOS_SUPERVISOR_PROFILE_GRACEFUL_WAIT_SEC", 25.0, 8.0),
            _setting("ADAOS_SUPERVISOR_PROFILE_TERMINATE_WAIT_SEC", 10.0, 5.0),
        )

    @staticmethod
    def finalize_wait_sec() -> float:
        try:
            return max(
                2.0,
                float(str(os.getenv("ADAOS_SUPERVISOR_PROFILE_FINALIZE_WAIT_SEC") or "8").strip()),
            )
        except Exception:
            return 8.0

    @staticmethod
    def max_runtime_sec(profile_mode: str) -> float:
        normalized = str(profile_mode or "normal").strip().lower()
        if normalized == "sampled_profile":
            raw = os.getenv("ADAOS_SUPERVISOR_SAMPLED_PROFILE_MAX_RUNTIME_SEC")
            default = "40"
        elif normalized == "trace_profile":
            raw = os.getenv("ADAOS_SUPERVISOR_TRACE_PROFILE_MAX_RUNTIME_SEC")
            default = "75"
        else:
            return 0.0
        try:
            return max(5.0, float(str(raw or default).strip()))
        except Exception:
            return float(default)

    @staticmethod
    def family_rss_bytes(pid: int | None, *, psutil_module: Any | None) -> tuple[int | None, int | None]:
        if not pid or psutil_module is None:
            return None, None
        try:
            root = psutil_module.Process(int(pid))
        except Exception:
            return None, None
        try:
            root_rss = int(root.memory_info().rss)
        except Exception:
            root_rss = None
        family_rss = int(root_rss or 0)
        try:
            children = list(root.children(recursive=True))
        except Exception:
            children = []
        for child in children:
            try:
                family_rss += int(child.memory_info().rss)
            except Exception:
                continue
        return root_rss, family_rss if family_rss > 0 else root_rss

    @staticmethod
    def cmdline_label(cmdline: list[str]) -> str | None:
        parts = [Path(str(item)).name if index == 0 else str(item) for index, item in enumerate(cmdline[:4])]
        text = " ".join(part for part in parts if part.strip()).strip()
        return text[:240] or None

    @staticmethod
    def skill_runtime_name(cmdline: list[str]) -> str | None:
        marker = "/skills/.runtime/"
        for item in cmdline:
            normalized = str(item or "").replace("\\", "/")
            if marker not in normalized:
                continue
            name = normalized.split(marker, 1)[1].split("/", 1)[0].strip()
            if name:
                return name[:120]
        return None

    def process_item(self, proc: Any) -> dict[str, Any] | None:
        try:
            pid = int(proc.pid)
        except Exception:
            return None
        try:
            ppid = int(proc.ppid())
        except Exception:
            ppid = None
        try:
            rss_bytes = int(proc.memory_info().rss)
        except Exception:
            rss_bytes = None
        try:
            name = str(proc.name() or "").strip() or None
        except Exception:
            name = None
        try:
            cmdline = [str(item) for item in proc.cmdline() if str(item or "").strip()]
        except Exception:
            cmdline = []
        return {
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "rss_bytes": rss_bytes,
            "cmdline_label": self.cmdline_label(cmdline),
            "skill_runtime": self.skill_runtime_name(cmdline),
        }
    def process_family_snapshot(
        self,
        pid: int | None,
        *,
        psutil_module: Any | None,
        max_children: int = 12,
    ) -> dict[str, Any]:
        if not pid:
            return {"available": False, "reason": "pid_unavailable"}
        if psutil_module is None:
            return {"available": False, "reason": "psutil_unavailable", "pid": int(pid)}
        try:
            root = psutil_module.Process(int(pid))
        except Exception as exc:
            return {"available": False, "reason": f"process_unavailable:{type(exc).__name__}", "pid": int(pid)}
        root_item = self.process_item(root) or {"pid": int(pid), "rss_bytes": None}
        try:
            raw_children = list(root.children(recursive=True))
        except Exception:
            raw_children = []
        child_items = [item for child in raw_children if (item := self.process_item(child)) is not None]
        child_items.sort(key=lambda item: int(item.get("rss_bytes") or 0), reverse=True)
        child_total = sum(int(item.get("rss_bytes") or 0) for item in child_items)
        root_rss = int(root_item.get("rss_bytes") or 0)
        limit = max(0, min(int(max_children or 0), 64))
        return {
            "available": True,
            "pid": int(pid),
            "root": root_item,
            "children": child_items[:limit],
            "children_total": len(child_items),
            "children_returned": min(len(child_items), limit),
            "children_omitted": max(0, len(child_items) - limit),
            "children_rss_bytes": child_total,
            "family_rss_bytes": root_rss + child_total if root_rss or child_total else None,
        }

    @staticmethod
    def parse_linux_memory_stat(text: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for line in str(text or "").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                result[str(parts[0])] = int(parts[1])
            except Exception:
                continue
        return result

    def sample_telemetry(
        self,
        manager: Any,
        operations: MemoryProfilingOperations,
    ) -> dict[str, Any] | None:
        now = time.time()
        interval_sec = operations.memory_telemetry_interval_sec()
        if manager._memory_last_telemetry_at and now - manager._memory_last_telemetry_at < interval_sec:
            return None
        managed = operations.proc_details(manager._proc, cwd_hint=manager._managed_runtime_cwd)
        managed_pid = managed.get("managed_pid")
        if not managed_pid:
            return None
        manager._ensure_memory_baseline_scope(managed_pid=managed_pid, now=now)
        process_rss_bytes, family_rss_bytes = operations.process_family_rss_bytes(managed_pid)
        if family_rss_bytes is None:
            return None
        attribution = operations.runtime_memory_attribution_snapshot(
            managed_pid,
            process_rss_bytes=process_rss_bytes,
            family_rss_bytes=family_rss_bytes,
        )
        manager._memory_last_telemetry_at = now
        manager._memory_last_available_bytes = operations.available_memory_bytes()
        total_memory_bytes = operations.total_memory_bytes()
        manager._memory_last_available_percent = (
            ((float(manager._memory_last_available_bytes) / float(total_memory_bytes)) * 100.0)
            if manager._memory_last_available_bytes is not None and total_memory_bytes not in {None, 0}
            else None
        )
        family_rss_value = int(family_rss_bytes)
        baseline_family_rss = operations.positive_int_or_none(manager._memory_baseline_family_rss_bytes)
        previous_baseline_family_rss = baseline_family_rss
        if family_rss_value > 0 and (baseline_family_rss is None or family_rss_value < baseline_family_rss):
            baseline_family_rss = family_rss_value
            if previous_baseline_family_rss is not None and family_rss_value < previous_baseline_family_rss:
                manager._memory_baseline_last_adjusted_at = now
                manager._memory_baseline_last_adjustment_reason = "rss_relaxed"
                manager._memory_baseline_adjustment_total += 1
        manager._memory_baseline_family_rss_bytes = baseline_family_rss
        tail = operations.read_memory_telemetry_tail(limit=256)
        window_start = now - operations.memory_telemetry_window_sec()
        window = [item for item in tail if float(item.get("sampled_at") or 0.0) >= window_start]
        first = window[0] if window else None
        slope = 0.0
        if isinstance(first, dict):
            first_family = int(first.get("family_rss_bytes") or family_rss_bytes)
            first_at = float(first.get("sampled_at") or now)
            elapsed_min = max((now - first_at) / 60.0, 1.0 / 60.0)
            slope = max(0.0, (int(family_rss_bytes) - first_family) / elapsed_min)
        if manager._memory_baseline_started_at is None:
            manager._memory_baseline_started_at = now
        baseline_age_sec = max(0.0, now - float(manager._memory_baseline_started_at or now))
        warmup_sec = operations.memory_baseline_warmup_sec()
        maturity_slope_threshold = operations.memory_baseline_maturity_slope_bytes_per_min()
        if manager._memory_baseline_matured_at is not None:
            baseline_phase = "mature"
        elif baseline_age_sec < warmup_sec:
            baseline_phase = "warming"
        elif slope > maturity_slope_threshold:
            baseline_phase = "maturity_blocked_slope"
        else:
            baseline_phase = "mature"
            manager._memory_baseline_matured_at = now
            if baseline_family_rss is not None and family_rss_value > baseline_family_rss:
                baseline_family_rss = family_rss_value
                manager._memory_baseline_family_rss_bytes = baseline_family_rss
                manager._memory_baseline_last_adjusted_at = now
                manager._memory_baseline_last_adjustment_reason = "warmup_matured"
                manager._memory_baseline_adjustment_total += 1
        manager._memory_baseline_phase = baseline_phase
        growth_bytes = max(0, family_rss_value - baseline_family_rss) if baseline_family_rss is not None else 0
        suspicion_state = "stable"
        suspicion_reason: str | None = None
        growth_threshold = operations.memory_suspicion_growth_threshold_bytes()
        family_rss_threshold = operations.memory_suspicion_family_rss_threshold_bytes()
        slope_threshold = operations.memory_suspicion_slope_threshold_bytes_per_min()
        if family_rss_threshold is not None and family_rss_value >= family_rss_threshold:
            suspicion_state = "suspected"
            suspicion_reason = "family_rss_threshold"
            if manager._memory_suspicion_since is None:
                manager._memory_suspicion_since = now
        elif growth_bytes >= growth_threshold and slope >= slope_threshold:
            suspicion_state = "suspected"
            suspicion_reason = "growth_and_slope_threshold"
            if manager._memory_suspicion_since is None:
                manager._memory_suspicion_since = now
        elif growth_bytes >= growth_threshold:
            suspicion_state = "suspected"
            suspicion_reason = "growth_threshold"
            if manager._memory_suspicion_since is None:
                manager._memory_suspicion_since = now
        elif slope >= slope_threshold:
            suspicion_state = "watch"
            suspicion_reason = "slope_threshold"
            manager._memory_suspicion_since = None
        else:
            manager._memory_suspicion_since = None
        manager._memory_suspicion_state = suspicion_state
        manager._memory_suspicion_reason = suspicion_reason
        manager._memory_last_growth_bytes = growth_bytes
        manager._memory_last_growth_bytes_per_min = slope
        sample = operations.append_memory_telemetry_sample(
            {
                "sampled_at": now,
                "slot": str(operations.active_slot() or "").strip().upper() or None,
                "runtime_instance_id": manager._managed_runtime_instance_id,
                "transition_role": manager._managed_transition_role,
                "managed_pid": managed_pid,
                "profile_mode": manager._memory_profile_mode,
                "suspicion_state": suspicion_state,
                "suspicion_reason": suspicion_reason,
                "process_rss_bytes": process_rss_bytes,
                "family_rss_bytes": family_rss_bytes,
                "process_tree": attribution.get("process_tree") if isinstance(attribution.get("process_tree"), dict) else {},
                "cgroup_memory_current_bytes": attribution.get("cgroup_memory_current_bytes"),
                "cgroup_anon_bytes": attribution.get("cgroup_anon_bytes"),
                "cgroup_file_bytes": attribution.get("cgroup_file_bytes"),
                "cgroup_kernel_bytes": attribution.get("cgroup_kernel_bytes"),
                "cgroup_slab_bytes": attribution.get("cgroup_slab_bytes"),
                "cgroup_memory_stat": attribution.get("cgroup_memory_stat") if isinstance(attribution.get("cgroup_memory_stat"), dict) else {},
                "available_memory_bytes": manager._memory_last_available_bytes,
                "available_memory_percent": manager._memory_last_available_percent,
                "baseline_rss_bytes": manager._memory_baseline_family_rss_bytes,
                "baseline_scope_key": manager._memory_baseline_scope_key,
                "baseline_pid": manager._memory_baseline_pid,
                "baseline_phase": manager._memory_baseline_phase,
                "baseline_started_at": manager._memory_baseline_started_at,
                "baseline_matured_at": manager._memory_baseline_matured_at,
                "baseline_age_sec": baseline_age_sec,
                "baseline_warmup_sec": warmup_sec,
                "baseline_maturity_slope_threshold_bytes_per_min": maturity_slope_threshold,
                "baseline_last_adjusted_at": manager._memory_baseline_last_adjusted_at,
                "baseline_last_adjustment_reason": manager._memory_baseline_last_adjustment_reason,
                "baseline_adjustment_total": manager._memory_baseline_adjustment_total,
                "rss_growth_bytes": growth_bytes,
                "rss_growth_bytes_per_min": slope,
                "sample_source": "supervisor",
            }
        )
        manager._update_memory_session_peak(family_rss_bytes)
        if (
            suspicion_state == "suspected"
            and manager._desired_memory_profile_mode() == "normal"
            and not str(manager._memory_active_session_id or "").strip()
        ):
            auto_allowed, auto_block_reason = manager._memory_policy_auto_profile_guard(now=now)
            if auto_allowed:
                if not operations.memory_policy_profile_restarts_enabled() and manager._memory_profile_mode != "sampled_profile":
                    manager._record_memory_auto_profile_block("policy_profile_restart_disabled", now=now)
                else:
                    try:
                        manager._request_memory_profile_session(
                            profile_mode="sampled_profile",
                            reason=f"memory.{suspicion_reason or 'threshold'}",
                            trigger_source="policy",
                            trigger_threshold=(
                                f"family_rss>={family_rss_threshold or 0}; "
                                f"growth>={growth_threshold}; slope>={int(slope_threshold)}"
                            ),
                        )
                    except operations.http_exception_type:
                        pass
            else:
                manager._record_memory_auto_profile_block(auto_block_reason, now=now)
        manager._persist_runtime_state()
        return sample


    def runtime_state_payload(
        self,
        manager: Any,
        operations: MemoryProfilingOperations,
    ) -> dict[str, Any]:
        operations.ensure_memory_store()
        manager._memory_baseline_family_rss_bytes = operations.positive_int_or_none(manager._memory_baseline_family_rss_bytes)
        current_slot = str(operations.active_slot() or "").strip().upper() or None
        now = time.time()
        managed = operations.proc_details(manager._proc, cwd_hint=manager._managed_runtime_cwd)
        managed_pid = managed.get("managed_pid")
        manager._ensure_memory_baseline_scope(managed_pid=managed_pid, now=now)
        process_rss_bytes, family_rss_bytes = operations.process_family_rss_bytes(managed_pid)
        attribution = operations.runtime_memory_attribution_snapshot(
            managed_pid,
            process_rss_bytes=process_rss_bytes,
            family_rss_bytes=family_rss_bytes,
        )
        telemetry_tail = operations.read_memory_telemetry_tail(limit=5000)
        sessions_index = operations.read_memory_session_index()
        session_items = sessions_index.get("sessions") if isinstance(sessions_index.get("sessions"), list) else []
        last_session_id = manager._memory_last_session_id
        if not last_session_id and session_items:
            last_item = session_items[-1] if isinstance(session_items[-1], dict) else {}
            last_session_id = str(last_item.get("session_id") or "").strip() or None
        baseline_family_rss = operations.positive_int_or_none(manager._memory_baseline_family_rss_bytes)
        baseline_started_at = manager._memory_baseline_started_at
        baseline_age_sec = max(0.0, now - baseline_started_at) if baseline_started_at is not None else None
        current_sample_state = "fresh" if family_rss_bytes is not None else "unavailable"
        if current_sample_state == "fresh":
            current_sample_reason = None
        elif managed_pid:
            current_sample_reason = "process_family_rss_unavailable"
        else:
            current_sample_reason = "managed_pid_unavailable"
        current_growth_bytes = (
            max(0, int(family_rss_bytes) - int(baseline_family_rss))
            if family_rss_bytes is not None and baseline_family_rss is not None
            else None
        )
        current_growth_bytes_per_min = manager._memory_last_growth_bytes_per_min if current_sample_state == "fresh" else None
        last_telemetry_sample = None
        baseline_scope_key = str(manager._memory_baseline_scope_key or "").strip() or None
        runtime_instance_id = str(manager._managed_runtime_instance_id or "").strip() or None
        for item in reversed(telemetry_tail):
            if not isinstance(item, dict):
                continue
            item_scope_key = str(item.get("baseline_scope_key") or "").strip() or None
            item_runtime_instance_id = str(item.get("runtime_instance_id") or "").strip() or None
            item_pid = operations.positive_int_or_none(item.get("managed_pid"))
            if baseline_scope_key and item_scope_key == baseline_scope_key:
                last_telemetry_sample = item
                break
            if runtime_instance_id and item_runtime_instance_id == runtime_instance_id:
                last_telemetry_sample = item
                break
            if managed_pid and item_pid == managed_pid:
                last_telemetry_sample = item
                break
        last_telemetry_sampled_at = (
            float(last_telemetry_sample.get("sampled_at"))
            if isinstance(last_telemetry_sample, dict) and last_telemetry_sample.get("sampled_at") is not None
            else None
        )
        last_telemetry_age_sec = (
            max(0.0, now - last_telemetry_sampled_at)
            if last_telemetry_sampled_at is not None
            else None
        )
        return {
            "contract_version": "1",
            "authority": "supervisor",
            "selected_profiler_adapter": manager._memory_profiler_adapter,
            "implemented_profiler_adapters": ["tracemalloc"],
            "planned_profiler_adapters": ["tracemalloc", "memray"],
            "current_profile_mode": manager._memory_profile_mode,
            "implemented_profile_modes": ["normal", "sampled_profile", "trace_profile"],
            "planned_profile_modes": ["normal", "sampled_profile", "trace_profile"],
            "profile_control_mode": operations.implemented_profile_control_mode,
            "implemented_profile_control_actions": list(operations.implemented_profile_control_actions),
            "implemented_profile_launch_env": list(operations.profile_launch_env_keys),
            "requested_profile_mode": manager._memory_requested_profile_mode,
            "requested_session_id": manager._memory_active_session_id,
            "finalizing_session_id": manager._memory_profile_finalizing_session_id,
            "publish_request_session_id": manager._memory_publish_request_session_id,
            "suspicion_state": manager._memory_suspicion_state,
            "suspicion_reason": manager._memory_suspicion_reason,
            "suspicion_since": manager._memory_suspicion_since,
            "active_session_id": manager._memory_active_session_id,
            "last_session_id": last_session_id,
            "active_slot": current_slot,
            "runtime_instance_id": manager._managed_runtime_instance_id,
            "transition_role": manager._managed_transition_role,
            "managed_pid": managed_pid,
            "current_sample_state": current_sample_state,
            "current_sample_reason": current_sample_reason,
            "current_process_rss_bytes": process_rss_bytes,
            "current_family_rss_bytes": family_rss_bytes,
            "current_process_tree": attribution.get("process_tree") if isinstance(attribution.get("process_tree"), dict) else {},
            "current_cgroup_memory_current_bytes": attribution.get("cgroup_memory_current_bytes"),
            "current_cgroup_anon_bytes": attribution.get("cgroup_anon_bytes"),
            "current_cgroup_file_bytes": attribution.get("cgroup_file_bytes"),
            "current_cgroup_kernel_bytes": attribution.get("cgroup_kernel_bytes"),
            "current_cgroup_slab_bytes": attribution.get("cgroup_slab_bytes"),
            "current_cgroup_memory_stat": attribution.get("cgroup_memory_stat") if isinstance(attribution.get("cgroup_memory_stat"), dict) else {},
            "current_memory_attribution": attribution,
            "available_memory_bytes": manager._memory_last_available_bytes,
            "available_memory_percent": manager._memory_last_available_percent,
            "telemetry_interval_sec": operations.memory_telemetry_interval_sec(),
            "telemetry_window_sec": operations.memory_telemetry_window_sec(),
            "telemetry_samples_total": len(telemetry_tail),
            "baseline_scope_key": manager._memory_baseline_scope_key,
            "baseline_pid": manager._memory_baseline_pid,
            "baseline_family_rss_bytes": baseline_family_rss,
            "baseline_phase": manager._memory_baseline_phase,
            "baseline_started_at": baseline_started_at,
            "baseline_matured_at": manager._memory_baseline_matured_at,
            "baseline_age_sec": baseline_age_sec,
            "baseline_warmup_sec": operations.memory_baseline_warmup_sec(),
            "baseline_maturity_slope_threshold_bytes_per_min": operations.memory_baseline_maturity_slope_bytes_per_min(),
            "baseline_last_adjusted_at": manager._memory_baseline_last_adjusted_at,
            "baseline_last_adjustment_reason": manager._memory_baseline_last_adjustment_reason,
            "baseline_adjustment_total": manager._memory_baseline_adjustment_total,
            "rss_growth_bytes": current_growth_bytes,
            "rss_growth_bytes_per_min": current_growth_bytes_per_min,
            "last_telemetry_sampled_at": last_telemetry_sampled_at,
            "last_telemetry_age_sec": last_telemetry_age_sec,
            "last_observed_process_rss_bytes": (
                last_telemetry_sample.get("process_rss_bytes") if isinstance(last_telemetry_sample, dict) else None
            ),
            "last_observed_family_rss_bytes": (
                last_telemetry_sample.get("family_rss_bytes") if isinstance(last_telemetry_sample, dict) else None
            ),
            "last_observed_rss_growth_bytes": (
                last_telemetry_sample.get("rss_growth_bytes") if isinstance(last_telemetry_sample, dict) else manager._memory_last_growth_bytes
            ),
            "last_observed_rss_growth_bytes_per_min": (
                last_telemetry_sample.get("rss_growth_bytes_per_min")
                if isinstance(last_telemetry_sample, dict)
                else manager._memory_last_growth_bytes_per_min
            ),
            "suspicion_family_rss_threshold_bytes": operations.memory_suspicion_family_rss_threshold_bytes(),
            "suspicion_growth_threshold_bytes": operations.memory_suspicion_growth_threshold_bytes(),
            "suspicion_slope_threshold_bytes_per_min": operations.memory_suspicion_slope_threshold_bytes_per_min(),
            "policy_profile_restarts_enabled": operations.memory_policy_profile_restarts_enabled(),
            "auto_profile_min_uptime_sec": operations.memory_auto_profile_min_uptime_sec(),
            "auto_profile_browser_live_ttl_sec": operations.memory_auto_profile_browser_live_ttl_sec(),
            "auto_profile_last_block_reason": manager._memory_auto_profile_last_block_reason,
            "auto_profile_last_block_at": manager._memory_auto_profile_last_block_at,
            "critical_available_percent_threshold": operations.memory_critical_available_percent_threshold(),
            "critical_available_bytes_threshold": operations.memory_critical_available_bytes_threshold(),
            "critical_duration_sec": operations.memory_critical_duration_sec(),
            "critical_restart_cooldown_sec": operations.memory_critical_restart_cooldown_sec(),
            "critical_state": "critical" if manager._memory_critical_since is not None else "normal",
            "critical_reason": manager._memory_critical_reason,
            "critical_since": manager._memory_critical_since,
            "critical_restart_last_at": manager._memory_critical_restart_last_at,
            "telemetry_path": str(operations.supervisor_memory_telemetry_path()),
            "sessions_index_path": str(operations.supervisor_memory_sessions_index_path()),
            "implemented_operation_events": list(operations.top_level_operation_events),
            "operation_log_contract_version": operations.memory_operation_contract_version,
            "sessions_total": len(session_items),
            "updated_at": now,
        }


