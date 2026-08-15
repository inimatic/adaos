from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable

from .lifecycle import BootstrapLifecycleCoordinator
from .status_policy import _bounded_interval_seconds, _env_truthy


class BootstrapStatusWatchdogService:
    """Publish lifecycle/node status and own their heartbeat watchdog state."""

    def __init__(
        self,
        *,
        config: Any,
        logger: logging.Logger,
        control_report_enabled: bool,
        control_await_watch_enabled: bool,
        control_heartbeat_s: float,
        node_status_heartbeat_s: float,
        report_control: Callable[[Any], Any],
        node_status_payload: Callable[[], dict[str, Any]] | None,
        should_emit_node_status: Callable[..., tuple[bool, tuple[Any, ...]]],
        emit_event: Callable[..., Awaitable[Any]],
    ) -> None:
        self.config = config
        self._log = logger
        self.control_report_enabled = bool(control_report_enabled)
        self.control_await_watch_enabled = bool(control_await_watch_enabled)
        self.control_heartbeat_s = float(control_heartbeat_s)
        self.node_status_heartbeat_s = float(node_status_heartbeat_s)
        self._report_control = report_control
        self._node_status_payload = node_status_payload
        self._should_emit_node_status = should_emit_node_status
        self._emit_event = emit_event
        self._last_node_status_emit_at = 0.0
        self._last_node_status_fingerprint: tuple[Any, ...] | None = None
        self._suppressed_duplicate_node_status_total = 0
        self._control_report_lock = asyncio.Lock()
        self._control_report_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="adaos-control-report",
        )
        self._control_report_requested_total = 0
        self._control_report_completed_total = 0
        self._control_report_failed_total = 0
        self._control_report_in_flight = False
        self._control_report_last_trigger: str | None = None
        self._control_report_last_duration_s: float | None = None
        self._control_report_last_completed_at: float | None = None
        self._control_report_last_error: str | None = None
        self._closed = False

    @classmethod
    def from_environment(
        cls,
        *,
        config: Any,
        logger: logging.Logger,
        report_control: Callable[[Any], Any],
        node_status_payload: Callable[[], dict[str, Any]] | None,
        node_status_heartbeat_s: float | None,
        should_emit_node_status: Callable[..., tuple[bool, tuple[Any, ...]]],
        emit_event: Callable[..., Awaitable[Any]],
    ) -> BootstrapStatusWatchdogService:
        """Build the status owner from its environment-backed policy once."""
        control_report_enabled = _env_truthy(
            os.getenv("ADAOS_HUB_CONTROL_REPORT_ENABLED"),
            default=True,
        )
        control_await_watch_enabled = _env_truthy(
            os.getenv("ADAOS_CONTROL_LIFECYCLE_AWAIT_WATCH"),
            default=_env_truthy(os.getenv("HUB_TRACE"), default=False),
        )
        return cls(
            config=config,
            logger=logger,
            control_report_enabled=control_report_enabled,
            control_await_watch_enabled=control_await_watch_enabled,
            control_heartbeat_s=_bounded_interval_seconds(
                os.getenv("HUB_CONTROL_LIFECYCLE_HEARTBEAT_S", "15") or "15",
                default=15.0,
                minimum=5.0,
            ),
            node_status_heartbeat_s=_bounded_interval_seconds(
                node_status_heartbeat_s if node_status_heartbeat_s is not None else 5.0,
                default=5.0,
                minimum=2.0,
            ),
            report_control=report_control,
            node_status_payload=node_status_payload,
            should_emit_node_status=should_emit_node_status,
            emit_event=emit_event,
        )

    def start_heartbeats(self, lifecycle: BootstrapLifecycleCoordinator) -> None:
        """Register watchdog loops in the active bootstrap task generation."""
        if self.control_report_enabled:
            lifecycle.start_task_once(
                "adaos-control-lifecycle-heartbeat",
                self.control_lifecycle_heartbeat,
            )
        lifecycle.start_task_once(
            "adaos-node-status-push-heartbeat",
            self.node_status_heartbeat,
        )

    async def report_control_lifecycle(self, trigger: str) -> None:
        self._control_report_requested_total += 1
        started_at: float | None = None
        owns_report = False
        try:
            if getattr(self.config, "role", None) != "hub":
                return
            if not self.control_report_enabled:
                return
            if self._closed:
                return
            async with self._control_report_lock:
                if self._closed:
                    return
                started_at = time.monotonic()
                owns_report = True
                self._control_report_in_flight = True
                self._control_report_last_trigger = str(trigger or "runtime")
                done_box: dict[str, Any] = {"thread_done_at": None, "dumped": False}
                main_tid = threading.get_ident()

                def _is_idle_event_loop_stack(stack_text: str) -> bool:
                    try:
                        normalized = stack_text.replace("\\", "/")
                        return (
                            "asyncio/base_events.py" in normalized
                            and "in _run_once" in normalized
                            and (
                                ("selectors.py" in normalized and "select.select(" in normalized)
                                or (
                                    "asyncio/windows_events.py" in normalized
                                    and "_overlapped.GetQueuedCompletionStatus" in normalized
                                )
                            )
                        )
                    except Exception:
                        return False

                def _safe_thread_stack(frame: Any, *, limit: int) -> tuple[str | None, str | None]:
                    try:
                        frames: list[str] = []
                        current = frame
                        remaining = max(1, int(limit))
                        while current is not None and remaining > 0:
                            filename = str(getattr(getattr(current, "f_code", None), "co_filename", "") or "")
                            func = str(getattr(getattr(current, "f_code", None), "co_name", "") or "")
                            lineno = int(getattr(current, "f_lineno", 0) or 0)
                            normalized = filename.replace("\\", "/")
                            if "y_py" in normalized or "site-packages/y_py" in normalized:
                                return None, "y_py_frame"
                            frames.append(f'  File "{filename}", line {lineno}, in {func}')
                            current = getattr(current, "f_back", None)
                            remaining -= 1
                        return "\n".join(reversed(frames)), None
                    except Exception as exc:
                        return None, f"{type(exc).__name__}: {exc}"

                def _run_report() -> Any:
                    try:
                        return self._report_control(self.config)
                    finally:
                        done_box["thread_done_at"] = time.monotonic()

                def _watch_resume() -> None:
                    while True:
                        time.sleep(0.25)
                        finished_at = done_box.get("thread_done_at")
                        if finished_at is None:
                            continue
                        if done_box.get("dumped"):
                            return
                        lag_s = time.monotonic() - float(finished_at)
                        if lag_s < 1.0:
                            continue
                        done_box["dumped"] = True
                        try:
                            frame = sys._current_frames().get(main_tid)  # type: ignore[attr-defined]
                            if frame is None:
                                self._log.warning(
                                    "control lifecycle await resume delayed lag_s=%.3f main_frame=missing trigger=%s",
                                    lag_s,
                                    trigger,
                                )
                                return
                            stack, stack_error = _safe_thread_stack(frame, limit=40)
                            if stack_error:
                                self._log.warning(
                                    "control lifecycle await resume delayed lag_s=%.3f trigger=%s stack_unavailable=%s",
                                    lag_s,
                                    trigger,
                                    stack_error,
                                )
                                return
                            stack = stack or ""
                            if _is_idle_event_loop_stack(stack):
                                self._log.debug(
                                    "control lifecycle await resume delayed but main loop idle lag_s=%.3f trigger=%s",
                                    lag_s,
                                    trigger,
                                )
                                return
                            self._log.warning(
                                "control lifecycle await resume delayed lag_s=%.3f trigger=%s stack=\n%s",
                                lag_s,
                                trigger,
                                stack.rstrip(),
                            )
                        except Exception:
                            return

                if self.control_await_watch_enabled:
                    watcher = threading.Thread(
                        target=_watch_resume,
                        name="adaos-control-lifecycle-await-watch",
                        daemon=True,
                    )
                    watcher.start()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._control_report_executor, _run_report)
                self._control_report_completed_total += 1
                self._control_report_last_error = None
        except Exception as exc:
            self._control_report_failed_total += 1
            self._control_report_last_error = f"{type(exc).__name__}: {exc}"
            self._log.debug(
                "control lifecycle report failed trigger=%s error=%s",
                trigger,
                str(exc),
                exc_info=True,
            )
        finally:
            if owns_report and started_at is not None:
                self._control_report_last_duration_s = max(0.0, time.monotonic() - started_at)
                self._control_report_last_completed_at = time.time()
                self._control_report_in_flight = False

    def control_report_snapshot(self) -> dict[str, Any]:
        return {
            "requested_total": self._control_report_requested_total,
            "completed_total": self._control_report_completed_total,
            "failed_total": self._control_report_failed_total,
            "in_flight": self._control_report_in_flight,
            "last_trigger": self._control_report_last_trigger,
            "last_duration_s": self._control_report_last_duration_s,
            "last_completed_at": self._control_report_last_completed_at,
            "last_error": self._control_report_last_error,
            "executor": "dedicated_single_worker",
            "ordered": True,
        }

    def close(self) -> None:
        self._closed = True
        self._control_report_executor.shutdown(wait=False, cancel_futures=False)

    async def control_lifecycle_heartbeat(self) -> None:
        if getattr(self.config, "role", None) != "hub":
            return
        while True:
            await asyncio.sleep(self.control_heartbeat_s)
            await self.report_control_lifecycle("heartbeat")

    async def emit_node_status(self, trigger: str) -> None:
        try:
            if str(getattr(self.config, "role", "") or "").strip().lower() != "hub":
                return
            if not callable(self._node_status_payload):
                return
            # The status payload includes filesystem/SQLite reads and the
            # sidecar listener probe (psutil.net_connections).  Building it on
            # the runtime loop turns a routine heartbeat into a 250-1500 ms
            # stop-the-world pause on small hubs.
            payload = await asyncio.to_thread(self._node_status_payload)
            payload["trigger"] = str(trigger or "").strip() or "runtime"
            now = time.time()
            should_emit, fingerprint = self._should_emit_node_status(
                payload=payload,
                now=now,
                last_emitted_at=self._last_node_status_emit_at,
                last_fingerprint=self._last_node_status_fingerprint,
            )
            if not should_emit:
                self._suppressed_duplicate_node_status_total += 1
                if self._suppressed_duplicate_node_status_total in {1, 10} or (
                    self._suppressed_duplicate_node_status_total % 100 == 0
                ):
                    self._log.warning(
                        "suppressed duplicate node.status trigger=%s total=%s",
                        payload["trigger"],
                        self._suppressed_duplicate_node_status_total,
                    )
                return
            self._last_node_status_emit_at = now
            self._last_node_status_fingerprint = fingerprint
            await self._emit_event(
                "node.status",
                payload,
                source="lifecycle",
                actor="system",
            )
        except Exception:
            self._log.debug("failed to emit node.status trigger=%s", trigger, exc_info=True)

    async def node_status_heartbeat(self) -> None:
        if getattr(self.config, "role", None) != "hub":
            return
        while True:
            await asyncio.sleep(self.node_status_heartbeat_s)
            await self.emit_node_status("heartbeat")
