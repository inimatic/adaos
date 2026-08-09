"""Small psutil-free resource sampler for the embedded Android process."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any


_STEADY_PSS_BUDGET_KIB = 200 * 1024
_STARTUP_PSS_BUDGET_KIB = 320 * 1024


def _read_keyed_kib(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, raw = line.partition(":")
            if not separator:
                continue
            first = raw.strip().split(" ", 1)[0]
            try:
                values[key.strip()] = max(0, int(first))
            except (TypeError, ValueError):
                continue
    except OSError:
        return {}
    return values


class AndroidResourceSampler:
    """Read only process-owned procfs files and retain bounded peak counters."""

    def __init__(self, proc_root: Path | str = "/proc") -> None:
        self.proc_root = Path(proc_root)
        self._lock = threading.Lock()
        self._peak_pss_kib = 0
        self._peak_rss_kib = 0
        self._sample_total = 0

    def reset(self) -> None:
        with self._lock:
            self._peak_pss_kib = 0
            self._peak_rss_kib = 0
            self._sample_total = 0

    def sample(self) -> dict[str, Any]:
        status = _read_keyed_kib(self.proc_root / "self" / "status")
        rollup = _read_keyed_kib(self.proc_root / "self" / "smaps_rollup")
        memory = _read_keyed_kib(self.proc_root / "meminfo")
        rss_kib = int(status.get("VmRSS") or 0)
        pss_kib = int(rollup.get("Pss") or rss_kib)
        with self._lock:
            self._sample_total += 1
            self._peak_pss_kib = max(self._peak_pss_kib, pss_kib)
            self._peak_rss_kib = max(
                self._peak_rss_kib,
                rss_kib,
                int(status.get("VmHWM") or 0),
            )
            peak_pss_kib = self._peak_pss_kib
            peak_rss_kib = self._peak_rss_kib
            sample_total = self._sample_total
        if not pss_kib:
            pressure = "unavailable"
        elif pss_kib <= _STEADY_PSS_BUDGET_KIB:
            pressure = "ready"
        elif pss_kib <= _STARTUP_PSS_BUDGET_KIB:
            pressure = "warning"
        else:
            pressure = "critical"
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
        except (AttributeError, OSError, TypeError, ValueError):
            page_size = 0
        return {
            "schema": "adaos.android.resources.v1",
            "sampled_at": time.time(),
            "sample_total": sample_total,
            "process": {
                "pss_kib": pss_kib,
                "rss_kib": rss_kib,
                "peak_pss_kib": peak_pss_kib,
                "peak_rss_kib": peak_rss_kib,
                "vm_hwm_kib": int(status.get("VmHWM") or 0),
                "swap_kib": int(status.get("VmSwap") or 0),
                "swap_pss_kib": int(rollup.get("SwapPss") or 0),
                "private_dirty_kib": int(rollup.get("Private_Dirty") or 0),
                "threads": int(status.get("Threads") or 0),
            },
            "device": {
                "memory_total_kib": int(memory.get("MemTotal") or 0),
                "page_size_bytes": page_size,
            },
            "budgets": {
                "steady_pss_kib": _STEADY_PSS_BUDGET_KIB,
                "startup_peak_pss_kib": _STARTUP_PSS_BUDGET_KIB,
                "steady_within_budget": bool(
                    pss_kib and pss_kib <= _STEADY_PSS_BUDGET_KIB
                ),
                "sampled_peak_within_startup_budget": bool(
                    peak_pss_kib and peak_pss_kib <= _STARTUP_PSS_BUDGET_KIB
                ),
                "pressure": pressure,
            },
            "policy": {
                "large_heap_requested": False,
                "sampler": "procfs_no_psutil",
            },
        }
