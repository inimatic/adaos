from __future__ import annotations

import os
from pathlib import Path
from typing import Any


DEFAULT_MEMORY_SUSPICION_FAMILY_RSS_THRESHOLD_BYTES = 2 * 1024 * 1024 * 1024


class MemoryProfilingService:
    """Own memory profiling timing policy and process-family snapshots."""

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
