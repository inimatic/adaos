from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable

from .lifecycle import BootstrapLifecycleCoordinator


class RootTransportService:
    """Own Root transport state and bridge/watchdog task orchestration."""

    def __init__(
        self,
        *,
        lifecycle: BootstrapLifecycleCoordinator,
        role: Callable[[], str],
        candidate_passive: Callable[[], bool],
        reconnect: Callable[..., Awaitable[dict[str, Any]]],
        watchdog_interval: Callable[[], float],
        record_event: Callable[..., Any],
        logger: logging.Logger,
    ) -> None:
        self.lifecycle = lifecycle
        self._role = role
        self._candidate_passive = candidate_passive
        self._reconnect = reconnect
        self._watchdog_interval = watchdog_interval
        self._record_event = record_event
        self._log = logger
        self.nats_client: Any = None
        self.route_reset: Any = None
        self.bridge_task_name = "adaos-nats-io-bridge"
        self.bridge_watchdog_task_name = "adaos-nats-io-bridge-watchdog"
        self.bridge_factory: Callable[[], Awaitable[Any]] | None = None
        self.bridge_watchdog_rearm_total = 0
        self.authority_waiters: set[asyncio.Event] = set()
        self.authority_ready_at: float | None = None

    def mark_authority_ready(self) -> None:
        self.authority_ready_at = time.time()
        for waiter in tuple(self.authority_waiters):
            waiter.set()

    async def reset_route_runtime(
        self,
        *,
        reason: str,
        notify_browser: bool,
    ) -> dict[str, Any]:
        normalized_reason = str(reason or "").strip() or "route_reset"
        callback = self.route_reset
        if not callable(callback):
            return {
                "ok": False,
                "reason": normalized_reason,
                "notify_browser": bool(notify_browser),
                "skipped": "route_reset_unavailable",
            }
        try:
            timeout_s = max(0.2, float(os.getenv("HUB_ROUTE_RESET_TIMEOUT_S", "2.5") or "2.5"))
        except Exception:
            timeout_s = 2.5
        try:
            result = callback(reason=normalized_reason, notify_browser=bool(notify_browser))
            if asyncio.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=timeout_s)
            if isinstance(result, dict):
                return result
            return {
                "ok": True,
                "reason": normalized_reason,
                "notify_browser": bool(notify_browser),
                "result": result,
            }
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "reason": normalized_reason,
                "notify_browser": bool(notify_browser),
                "error": "TimeoutError: hub route reset timed out",
            }
        except Exception as exc:
            return {
                "ok": False,
                "reason": normalized_reason,
                "notify_browser": bool(notify_browser),
                "error": f"{type(exc).__name__}: {exc}",
            }

    def start_bridge_task(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        start_immediately: bool = True,
    ) -> asyncio.Task[Any] | None:
        self.bridge_factory = coro_factory
        if not start_immediately:
            return None
        return self.lifecycle.start_task_once(self.bridge_task_name, coro_factory)

    def bridge_required(self) -> bool:
        try:
            role = str(self._role() or "").strip().lower()
        except Exception:
            role = ""
        if role != "hub":
            return False
        try:
            if self._candidate_passive():
                return False
        except Exception:
            pass
        return callable(self.bridge_factory)

    async def repair_missing_bridge(self, *, reason: str) -> dict[str, Any]:
        if not self.bridge_required():
            return {"attempted": False, "state": "not_required"}
        live = self.lifecycle.find_live_task(self.bridge_task_name)
        if live is not None:
            return {"attempted": False, "state": "running", "task_name": live.get_name()}
        result = await self._reconnect(_reason=f"bridge_watchdog:{reason}")
        bridge = result.get("bridge") if isinstance(result, dict) else None
        started = bool(isinstance(bridge, dict) and bridge.get("started"))
        if started:
            self.bridge_watchdog_rearm_total += 1
            self._log.warning(
                "hub-root bridge watchdog rearmed missing bridge reason=%s total=%s",
                str(reason or "watchdog"),
                self.bridge_watchdog_rearm_total,
            )
            try:
                self._record_event(
                    "bridge_watchdog_rearmed",
                    summary="runtime watchdog rearmed missing hub-root bridge",
                    details={
                        "reason": str(reason or "watchdog"),
                        "rearm_total": self.bridge_watchdog_rearm_total,
                    },
                )
            except Exception:
                pass
        return {
            "attempted": True,
            "state": "rearmed" if started else "rearm_failed",
            "result": result,
        }

    async def watchdog(self) -> None:
        interval_s = self._watchdog_interval()
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.repair_missing_bridge(reason="periodic_watchdog")
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.warning("hub-root bridge watchdog repair failed", exc_info=True)

    def ensure_bridge_task(
        self,
        *,
        force_rearm: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        factory = self.bridge_factory
        task_name = self.bridge_task_name
        if not callable(factory):
            return {
                "attempted": False,
                "started": False,
                "task_name": task_name,
                "reason": "bridge_factory_unavailable",
            }
        existing = self.lifecycle.find_live_task(task_name)
        if existing is not None:
            if not force_rearm:
                return {
                    "attempted": True,
                    "started": False,
                    "task_name": task_name,
                    "state": "already_running",
                }
            try:
                _task, cancelled_previous = self.lifecycle.replace_task(task_name, factory)
                return {
                    "attempted": True,
                    "started": True,
                    "task_name": task_name,
                    "state": "rearmed",
                    "reason": str(reason or "forced_rearm"),
                    "cancelled_previous": cancelled_previous,
                }
            except Exception as exc:
                return {
                    "attempted": True,
                    "started": False,
                    "task_name": task_name,
                    "state": "failed",
                    "reason": str(reason or "forced_rearm"),
                    "cancelled_previous": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        try:
            self.lifecycle.start_task_once(task_name, factory)
            return {
                "attempted": True,
                "started": True,
                "task_name": task_name,
                "state": "started",
            }
        except Exception as exc:
            return {
                "attempted": True,
                "started": False,
                "task_name": task_name,
                "state": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
