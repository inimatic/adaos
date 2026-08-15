from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any


_LOG = logging.getLogger("adaos.subnet.heartbeat_persistence")


@dataclass(frozen=True)
class _PendingHeartbeat:
    directory: Any
    node_id: str
    capacity: dict[str, Any] | None
    node_state: str | None
    base_url: str | None
    submitted_at: float


class HeartbeatPersistenceRuntime:
    """Serialize and coalesce durable heartbeat writes outside route handling."""

    def __init__(self, *, idle_exit_s: float = 1.0) -> None:
        self._idle_exit_s = max(0.05, float(idle_exit_s))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._pending: dict[str, _PendingHeartbeat] = {}
        self._in_flight: str | None = None
        self._stats_lock = threading.RLock()
        self._stats: dict[str, Any] = {
            "accepted_total": 0,
            "coalesced_total": 0,
            "persisted_total": 0,
            "failed_total": 0,
            "last_persisted_at": None,
            "last_failed_at": None,
            "last_duration_ms": 0.0,
            "max_duration_ms": 0.0,
            "last_error": None,
        }

    def submit(
        self,
        directory: Any,
        *,
        node_id: str,
        capacity: dict[str, Any] | None,
        node_state: str | None,
        base_url: str | None,
    ) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            previous_task = self._task
            if previous_task is not None and not previous_task.done():
                previous_task.cancel()
            self._loop = loop
            self._event = asyncio.Event()
            self._task = None
            self._pending.clear()
            self._in_flight = None
        token = str(node_id or "").strip()
        if not token:
            raise ValueError("node_id is required")
        coalesced = token in self._pending
        self._pending[token] = _PendingHeartbeat(
            directory=directory,
            node_id=token,
            capacity=dict(capacity) if isinstance(capacity, dict) else None,
            node_state=str(node_state).strip() if node_state is not None else None,
            base_url=str(base_url).strip() if base_url is not None else None,
            submitted_at=time.time(),
        )
        with self._stats_lock:
            self._stats["accepted_total"] = int(self._stats.get("accepted_total") or 0) + 1
            if coalesced:
                self._stats["coalesced_total"] = int(self._stats.get("coalesced_total") or 0) + 1
        assert self._event is not None
        self._event.set()
        if self._task is None or self._task.done():
            self._task = loop.create_task(
                self._run(),
                name="adaos-subnet-heartbeat-persistence",
            )

    async def _run(self) -> None:
        assert self._event is not None
        event = self._event
        while True:
            if not self._pending:
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=self._idle_exit_s)
                except asyncio.TimeoutError:
                    if not self._pending:
                        return
                    continue
            if not self._pending:
                continue
            node_id = next(iter(self._pending))
            pending = self._pending.pop(node_id)
            self._in_flight = node_id
            started = time.perf_counter()
            error: Exception | None = None
            try:
                await asyncio.to_thread(
                    pending.directory.persist_heartbeat,
                    pending.node_id,
                    pending.capacity,
                    node_state=pending.node_state,
                    base_url=pending.base_url,
                )
            except asyncio.CancelledError:
                self._pending.setdefault(node_id, pending)
                raise
            except Exception as exc:
                error = exc
            finally:
                self._in_flight = None
            duration_ms = (time.perf_counter() - started) * 1000.0
            with self._stats_lock:
                self._stats["last_duration_ms"] = round(duration_ms, 3)
                self._stats["max_duration_ms"] = round(
                    max(float(self._stats.get("max_duration_ms") or 0.0), duration_ms),
                    3,
                )
                if error is None:
                    self._stats["persisted_total"] = int(self._stats.get("persisted_total") or 0) + 1
                    self._stats["last_persisted_at"] = time.time()
                    self._stats["last_error"] = None
                else:
                    self._stats["failed_total"] = int(self._stats.get("failed_total") or 0) + 1
                    self._stats["last_failed_at"] = time.time()
                    self._stats["last_error"] = f"{type(error).__name__}: {error}"
            if error is not None:
                _LOG.warning(
                    "subnet heartbeat durable write failed node_id=%s duration_ms=%.3f error=%s: %s",
                    node_id,
                    duration_ms,
                    type(error).__name__,
                    error,
                )
            elif duration_ms >= 500.0:
                _LOG.warning(
                    "subnet heartbeat durable write slow node_id=%s duration_ms=%.3f pending=%d",
                    node_id,
                    duration_ms,
                    len(self._pending),
                )

    async def wait_idle(self, *, timeout_s: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + max(0.01, float(timeout_s))
        while self._pending or self._in_flight:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("heartbeat persistence did not become idle")
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._stats_lock:
            stats = dict(self._stats)
        pending_rows = list(self._pending.values())
        oldest_pending_at = min((item.submitted_at for item in pending_rows), default=None)
        return {
            "schema": "adaos.subnet_heartbeat_persistence.v1",
            "status": "degraded" if stats.get("last_error") else "ready",
            **stats,
            "pending_total": len(pending_rows),
            "in_flight_node_id": self._in_flight,
            "oldest_pending_age_s": (
                round(max(0.0, now - oldest_pending_at), 3)
                if oldest_pending_at is not None
                else None
            ),
            "worker_alive": bool(self._task is not None and not self._task.done()),
        }


_RUNTIME = HeartbeatPersistenceRuntime()


def get_heartbeat_persistence_runtime() -> HeartbeatPersistenceRuntime:
    return _RUNTIME


def heartbeat_persistence_snapshot() -> dict[str, Any]:
    return _RUNTIME.snapshot()
