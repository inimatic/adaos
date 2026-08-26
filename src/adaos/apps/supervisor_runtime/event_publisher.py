from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any


class SupervisorRuntimeEventPublisher:
    """Deliver supervisor events to the active runtime without blocking writes."""

    def __init__(
        self,
        deliver: Callable[[dict[str, Any]], Any],
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._deliver = deliver
        self._log = logger or logging.getLogger("adaos.supervisor.event_publisher")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[Any] | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._stopping = False
        self._snapshot_lock = threading.Lock()
        self._accepted_total = 0
        self._superseded_total = 0
        self._delivered_total = 0
        self._failed_total = 0
        self._last_delivered_at: float | None = None
        self._last_failure_at: float | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._stopping = False
        self._task = self._loop.create_task(
            self._run(),
            name="adaos-supervisor-runtime-events",
        )

    def publish(self, topic: str, payload: Mapping[str, Any]) -> bool:
        event_type = str(topic or "").strip()
        loop = self._loop
        if not event_type or loop is None or loop.is_closed() or self._stopping:
            return False
        event_payload = dict(payload or {})
        try:
            loop.call_soon_threadsafe(self._enqueue, event_type, event_payload)
        except RuntimeError:
            return False
        return True

    def _enqueue(self, topic: str, payload: dict[str, Any]) -> None:
        if self._stopping:
            return
        with self._snapshot_lock:
            self._accepted_total += 1
            if topic in self._pending:
                self._superseded_total += 1
            self._pending[topic] = payload
        if self._wake is not None:
            self._wake.set()

    async def _run(self) -> None:
        while True:
            wake = self._wake
            if wake is None:
                return
            await wake.wait()
            wake.clear()
            while True:
                with self._snapshot_lock:
                    if not self._pending:
                        envelope = None
                    else:
                        topic = next(iter(self._pending))
                        envelope = {
                            "topic": topic,
                            "payload": self._pending.pop(topic),
                        }
                if envelope is None:
                    break
                try:
                    await asyncio.to_thread(self._deliver, envelope)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    with self._snapshot_lock:
                        self._failed_total += 1
                        self._last_failure_at = time.time()
                        self._last_error = f"{type(exc).__name__}: {exc}"
                    self._log.debug(
                        "runtime event delivery failed topic=%s: %s: %s",
                        envelope["topic"],
                        type(exc).__name__,
                        exc,
                    )
                else:
                    with self._snapshot_lock:
                        self._delivered_total += 1
                        self._last_delivered_at = time.time()
                        self._last_error = None
            if self._stopping:
                return

    async def close(self) -> None:
        self._stopping = True
        wake = self._wake
        task = self._task
        if wake is not None:
            wake.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._task = None
        self._wake = None
        self._loop = None

    def snapshot(self) -> dict[str, Any]:
        with self._snapshot_lock:
            return {
                "schema": "adaos.supervisor_runtime_event_publisher.v1",
                "mode": "push",
                "running": bool(self._task is not None and not self._task.done()),
                "pending_topics": sorted(self._pending),
                "accepted_total": self._accepted_total,
                "superseded_total": self._superseded_total,
                "delivered_total": self._delivered_total,
                "failed_total": self._failed_total,
                "last_delivered_at": self._last_delivered_at,
                "last_failure_at": self._last_failure_at,
                "last_error": self._last_error,
            }
