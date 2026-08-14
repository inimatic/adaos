from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

from adaos.services.incident_registry import (
    capture_process_activity_sample,
    record_runtime_event_loop_stall,
)
from adaos.services.reliability import (
    record_runtime_event_loop_watchdog_probe,
    set_runtime_event_loop_watchdog_state,
)


_LOG = logging.getLogger("adaos.runtime.event_loop_watchdog")


def _stack_frames(thread_id: int, *, limit: int = 40) -> list[dict[str, Any]]:
    try:
        frame = sys._current_frames().get(int(thread_id))  # type: ignore[attr-defined]
    except Exception:
        frame = None
    if frame is None:
        return []
    try:
        extracted = traceback.extract_stack(frame, limit=max(1, min(int(limit), 80)))
    except Exception:
        return []
    return [
        {
            "filename": str(item.filename or ""),
            "lineno": int(item.lineno or 0),
            "function": str(item.name or ""),
        }
        for item in extracted
    ]


@dataclass(frozen=True)
class RuntimeEventLoopWatchdogConfig:
    interval_sec: float = 0.5
    threshold_ms: float = 250.0
    report_interval_sec: float = 30.0


class RuntimeEventLoopWatchdog:
    """Observe event-loop responsiveness from a thread outside that loop."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        loop_thread_id: int,
        config: RuntimeEventLoopWatchdogConfig,
    ) -> None:
        self._loop = loop
        self._loop_thread_id = int(loop_thread_id)
        self._interval_sec = max(0.05, min(float(config.interval_sec), 30.0))
        self._threshold_sec = max(0.01, min(float(config.threshold_ms) / 1000.0, 60.0))
        self._report_interval_sec = max(1.0, min(float(config.report_interval_sec), 3600.0))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="adaos-event-loop-watchdog",
            daemon=True,
        )
        self._started = False

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._started and self._thread.is_alive() and threading.get_ident() != self._thread.ident:
            self._thread.join(timeout=max(0.0, float(timeout)))
        set_runtime_event_loop_watchdog_state(
            running=False,
            watchdog_thread_id=self._thread.ident,
            loop_thread_id=self._loop_thread_id,
            interval_sec=self._interval_sec,
            threshold_ms=self._threshold_sec * 1000.0,
        )

    def _run(self) -> None:
        watchdog_thread_id = threading.get_ident()
        set_runtime_event_loop_watchdog_state(
            running=True,
            watchdog_thread_id=watchdog_thread_id,
            loop_thread_id=self._loop_thread_id,
            interval_sec=self._interval_sec,
            threshold_ms=self._threshold_sec * 1000.0,
        )
        last_report_at = 0.0
        try:
            while not self._stop.wait(self._interval_sec):
                acknowledged = threading.Event()
                probe_started = time.monotonic()
                try:
                    self._loop.call_soon_threadsafe(acknowledged.set)
                except RuntimeError:
                    break
                if acknowledged.wait(self._threshold_sec):
                    record_runtime_event_loop_watchdog_probe(
                        elapsed_ms=(time.monotonic() - probe_started) * 1000.0,
                        stalled=False,
                    )
                    continue

                observed_at = time.monotonic()
                stall_ms = (observed_at - probe_started) * 1000.0
                record_runtime_event_loop_watchdog_probe(elapsed_ms=stall_ms, stalled=True)
                frames: list[dict[str, Any]] = []
                process_sample: dict[str, Any] = {}
                if observed_at - last_report_at >= self._report_interval_sec:
                    last_report_at = observed_at
                    frames = _stack_frames(self._loop_thread_id)
                    try:
                        process_sample = capture_process_activity_sample()
                    except Exception as exc:
                        process_sample = {"available": False, "error": type(exc).__name__}
                    try:
                        record_runtime_event_loop_stall(
                            stall_ms=stall_ms,
                            threshold_ms=self._threshold_sec * 1000.0,
                            interval_sec=self._interval_sec,
                            stack_frames=frames,
                            loop_thread_id=self._loop_thread_id,
                            watchdog_thread_id=watchdog_thread_id,
                            process_sample=process_sample,
                        )
                    except Exception:
                        _LOG.exception("event-loop watchdog incident recording failed")
                    top = frames[-1] if frames else {}
                    _LOG.warning(
                        "runtime event loop unresponsive stall_ms=%.1f threshold_ms=%.1f frame=%s:%s function=%s",
                        stall_ms,
                        self._threshold_sec * 1000.0,
                        top.get("filename") or "missing",
                        top.get("lineno") or 0,
                        top.get("function") or "unknown",
                    )

                while not self._stop.is_set() and not acknowledged.wait(self._interval_sec):
                    continue
                if acknowledged.is_set():
                    completed_stall_ms = (time.monotonic() - probe_started) * 1000.0
                    record_runtime_event_loop_watchdog_probe(
                        elapsed_ms=completed_stall_ms,
                        stalled=False,
                        completed_stall=True,
                    )
                    if frames:
                        try:
                            record_runtime_event_loop_stall(
                                stall_ms=completed_stall_ms,
                                threshold_ms=self._threshold_sec * 1000.0,
                                interval_sec=self._interval_sec,
                                stack_frames=frames,
                                loop_thread_id=self._loop_thread_id,
                                watchdog_thread_id=watchdog_thread_id,
                                process_sample=process_sample,
                                increment_occurrence=False,
                            )
                        except Exception:
                            _LOG.exception("event-loop watchdog incident completion recording failed")
        finally:
            set_runtime_event_loop_watchdog_state(
                running=False,
                watchdog_thread_id=watchdog_thread_id,
                loop_thread_id=self._loop_thread_id,
                interval_sec=self._interval_sec,
                threshold_ms=self._threshold_sec * 1000.0,
            )


__all__ = [
    "RuntimeEventLoopWatchdog",
    "RuntimeEventLoopWatchdogConfig",
]
