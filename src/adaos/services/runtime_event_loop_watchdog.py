from __future__ import annotations

import asyncio
import faulthandler
import logging
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any

from adaos.services.incident_registry import (
    capture_process_activity_sample,
    record_runtime_event_loop_stall,
)
from adaos.services.reliability import (
    record_runtime_event_loop_watchdog_probe,
    record_runtime_event_loop_watchdog_stall_evidence,
    set_runtime_event_loop_watchdog_state,
)


_LOG = logging.getLogger("adaos.runtime.event_loop_watchdog")


_THREAD_HEADER_RE = re.compile(r"^(?:Current thread|Thread) 0x([0-9a-fA-F]+)")
_FAULT_FRAME_RE = re.compile(r'^\s*File "(?P<filename>.*)", line (?P<lineno>\d+) in (?P<function>.*)$')


def _stack_frames(thread_id: int, *, limit: int = 40) -> list[dict[str, Any]]:
    """Sample a thread stack without transferring live frame locals across threads."""
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
            faulthandler.dump_traceback(file=output, all_threads=True)
            output.flush()
            output.seek(0)
            dump = output.read()
    except Exception:
        return []

    target_thread_id = int(thread_id)
    current_thread_id: int | None = None
    newest_first: list[dict[str, Any]] = []
    for line in dump.splitlines():
        header = _THREAD_HEADER_RE.match(line)
        if header:
            try:
                current_thread_id = int(header.group(1), 16)
            except ValueError:
                current_thread_id = None
            if newest_first and current_thread_id != target_thread_id:
                break
            continue
        if current_thread_id != target_thread_id:
            continue
        frame = _FAULT_FRAME_RE.match(line)
        if frame:
            newest_first.append(
                {
                    "filename": str(frame.group("filename") or ""),
                    "lineno": int(frame.group("lineno") or 0),
                    "function": str(frame.group("function") or ""),
                }
            )

    capped = newest_first[: max(1, min(int(limit), 80))]
    capped.reverse()
    return capped


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
        active_stall_started: float | None = None
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
                active_stall_started = probe_started
                stall_ms = max(
                    self._threshold_sec * 1000.0,
                    (observed_at - probe_started) * 1000.0,
                )
                record_runtime_event_loop_watchdog_probe(elapsed_ms=stall_ms, stalled=True)
                frames = _stack_frames(self._loop_thread_id)
                last_frames = frames
                skill_stall_candidates: list[dict[str, str]] = []
                try:
                    from adaos.services.skill.subscription_execution import (
                        capture_active_skill_handlers_for_stack,
                    )

                    skill_stall_candidates = capture_active_skill_handlers_for_stack(frames)
                except Exception:
                    _LOG.exception("event-loop stall skill attribution capture failed")
                candidate_by_key = {
                    (
                        str(item.get("skill") or ""),
                        str(item.get("topic") or ""),
                        str(item.get("handler") or ""),
                    ): dict(item)
                    for item in skill_stall_candidates
                }
                record_runtime_event_loop_watchdog_stall_evidence(
                    phase="detected",
                    elapsed_ms=stall_ms,
                    stack_frames=frames,
                    skill_candidates=skill_stall_candidates,
                )
                process_sample: dict[str, Any] = {}
                report_incident = observed_at - last_report_at >= self._report_interval_sec
                if report_incident:
                    last_report_at = observed_at
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
                    sampled_stall_ms = (time.monotonic() - probe_started) * 1000.0
                    sampled_frames = _stack_frames(self._loop_thread_id)
                    if sampled_frames:
                        last_frames = sampled_frames
                    try:
                        from adaos.services.skill.subscription_execution import (
                            capture_active_skill_handlers_for_stack,
                        )

                        for item in capture_active_skill_handlers_for_stack(sampled_frames):
                            key = (
                                str(item.get("skill") or ""),
                                str(item.get("topic") or ""),
                                str(item.get("handler") or ""),
                            )
                            candidate_by_key[key] = dict(item)
                    except Exception:
                        _LOG.exception("event-loop stall skill attribution refresh failed")
                    record_runtime_event_loop_watchdog_stall_evidence(
                        phase="sample",
                        elapsed_ms=sampled_stall_ms,
                        stack_frames=last_frames,
                        skill_candidates=list(candidate_by_key.values()),
                    )
                if acknowledged.is_set():
                    completed_stall_ms = (time.monotonic() - probe_started) * 1000.0
                    record_runtime_event_loop_watchdog_probe(
                        elapsed_ms=completed_stall_ms,
                        stalled=False,
                        completed_stall=True,
                    )
                    record_runtime_event_loop_watchdog_stall_evidence(
                        phase="completed",
                        elapsed_ms=completed_stall_ms,
                        stack_frames=last_frames,
                        skill_candidates=list(candidate_by_key.values()),
                    )
                    try:
                        from adaos.services.skill.subscription_execution import (
                            correlate_runtime_event_loop_stall,
                        )

                        correlate_runtime_event_loop_stall(
                            stack_frames=last_frames,
                            stall_ms=completed_stall_ms,
                            threshold_ms=self._threshold_sec * 1000.0,
                            candidates=list(candidate_by_key.values()),
                        )
                    except Exception:
                        _LOG.exception("event-loop stall skill correlation failed")
                    initial_top = frames[-1] if frames else {}
                    final_top = last_frames[-1] if last_frames else {}
                    _LOG.warning(
                        "runtime event loop recovered duration_ms=%.1f initial_frame=%s:%s:%s "
                        "final_frame=%s:%s:%s skill_candidates=%s",
                        completed_stall_ms,
                        initial_top.get("filename") or "missing",
                        initial_top.get("lineno") or 0,
                        initial_top.get("function") or "unknown",
                        final_top.get("filename") or "missing",
                        final_top.get("lineno") or 0,
                        final_top.get("function") or "unknown",
                        list(candidate_by_key.values()),
                    )
                    if last_frames and report_incident:
                        try:
                            record_runtime_event_loop_stall(
                                stall_ms=completed_stall_ms,
                                threshold_ms=self._threshold_sec * 1000.0,
                                interval_sec=self._interval_sec,
                                stack_frames=last_frames,
                                loop_thread_id=self._loop_thread_id,
                                watchdog_thread_id=watchdog_thread_id,
                                process_sample=process_sample,
                                increment_occurrence=False,
                            )
                        except Exception:
                            _LOG.exception("event-loop watchdog incident completion recording failed")
                    active_stall_started = None
        finally:
            if active_stall_started is not None:
                record_runtime_event_loop_watchdog_stall_evidence(
                    phase="aborted",
                    elapsed_ms=(time.monotonic() - active_stall_started) * 1000.0,
                )
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
