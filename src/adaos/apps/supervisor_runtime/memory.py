from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class MemoryProfilingService:
    """Own memory profiling timing policy and process-family snapshots."""

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
