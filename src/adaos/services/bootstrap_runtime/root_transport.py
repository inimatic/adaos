from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .lifecycle import BootstrapLifecycleCoordinator


@dataclass(frozen=True, slots=True)
class RootTransportReconnectOperations:
    configure_strategy: Any
    record_event: Any
    strategy_snapshot: Any


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
        self.bridge_watchdog_transport_rearm_total = 0
        self._bridge_transport_unhealthy_since: float | None = None
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

    def bridge_transport_health(self) -> dict[str, Any]:
        """Inspect the NATS client owned by a live bridge task.

        The outer supervisor task can stay alive while nats-py's reader or
        connection has already stopped. Existence of the task alone is not a
        transport health signal.
        """

        if not self.bridge_required():
            return {"state": "not_required", "healthy": None, "reason": "bridge_not_required"}
        live = self.lifecycle.find_live_task(self.bridge_task_name)
        if live is None:
            return {"state": "missing", "healthy": False, "reason": "bridge_task_missing"}
        nc = self.nats_client
        if nc is None:
            if self.authority_ready_at is None:
                return {"state": "starting", "healthy": None, "reason": "nats_client_not_ready"}
            return {"state": "down", "healthy": False, "reason": "nats_client_missing_after_ready"}
        try:
            value = getattr(nc, "is_closed", None)
            closed = bool(value() if callable(value) else value)
        except Exception:
            closed = False
        if closed:
            return {"state": "down", "healthy": False, "reason": "nats_client_closed"}
        try:
            value = getattr(nc, "is_connected", None)
            connected = bool(value() if callable(value) else value)
        except Exception:
            connected = True
        if not connected:
            return {"state": "down", "healthy": False, "reason": "nats_client_disconnected"}
        for task_name in ("_reading_task", "_flusher_task"):
            task = getattr(nc, task_name, None)
            if isinstance(task, asyncio.Task) and task.done():
                return {
                    "state": "down",
                    "healthy": False,
                    "reason": f"nats_core_task_stopped:{task_name}",
                }
        return {"state": "ready", "healthy": True, "reason": "nats_client_connected"}

    def _transport_watchdog_grace_s(self) -> float:
        try:
            value = float(os.getenv("HUB_ROOT_BRIDGE_TRANSPORT_GRACE_S", "6") or "6")
        except Exception:
            value = 6.0
        return max(1.0, min(value, 60.0))

    async def repair_unhealthy_bridge(
        self,
        *,
        reason: str,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        health = self.bridge_transport_health()
        if health.get("healthy") is not False:
            self._bridge_transport_unhealthy_since = None
            return {"attempted": False, "state": str(health.get("state") or "unknown"), "health": health}
        now = time.monotonic() if observed_at is None else float(observed_at)
        if self._bridge_transport_unhealthy_since is None:
            self._bridge_transport_unhealthy_since = now
        unhealthy_for_s = max(0.0, now - float(self._bridge_transport_unhealthy_since))
        grace_s = self._transport_watchdog_grace_s()
        if unhealthy_for_s < grace_s:
            return {
                "attempted": False,
                "state": "observing_unhealthy",
                "health": health,
                "unhealthy_for_s": unhealthy_for_s,
                "grace_s": grace_s,
            }

        repair_reason = f"bridge_transport_watchdog:{str(health.get('reason') or reason or 'unhealthy')}"
        result = await self._reconnect(
            _reason=repair_reason,
            _force_bridge_rearm=True,
        )
        bridge = result.get("bridge") if isinstance(result, dict) else None
        started = bool(isinstance(bridge, dict) and bridge.get("started"))
        self._bridge_transport_unhealthy_since = None
        if started:
            self.bridge_watchdog_transport_rearm_total += 1
            self._log.warning(
                "hub-root bridge watchdog rearmed unhealthy transport reason=%s total=%s",
                str(health.get("reason") or reason),
                self.bridge_watchdog_transport_rearm_total,
            )
            try:
                self._record_event(
                    "bridge_watchdog_transport_rearmed",
                    summary="runtime watchdog rearmed unhealthy hub-root transport",
                    details={
                        "reason": str(health.get("reason") or reason),
                        "rearm_total": self.bridge_watchdog_transport_rearm_total,
                        "unhealthy_for_s": unhealthy_for_s,
                    },
                )
            except Exception:
                pass
        return {
            "attempted": True,
            "state": "rearmed" if started else "rearm_failed",
            "health": health,
            "unhealthy_for_s": unhealthy_for_s,
            "result": result,
        }

    async def watchdog(self) -> None:
        interval_s = self._watchdog_interval()
        while True:
            await asyncio.sleep(interval_s)
            try:
                missing = await self.repair_missing_bridge(reason="periodic_watchdog")
                if str(missing.get("state") or "") == "running":
                    await self.repair_unhealthy_bridge(reason="periodic_watchdog")
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

    async def request_reconnect(
        self,
        operations: RootTransportReconnectOperations,
        *,
        transport: str | None = None,
        url_override: str | None = None,
        wait_for_authority: bool = False,
        _reason: str = "manual_reconnect",
        _force_bridge_rearm: bool = False,
    ) -> dict[str, Any]:
        """
        Force hub-root transport reconnect.

        This is a debugging/ops hook: update env-like overrides and proactively close the current
        NATS connection so the supervisor reconnects using new settings.
        """
        tr = str(transport or "").strip().lower() or None
        override = str(url_override or "").strip() or None
        reconnect_reason = str(_reason or "manual_reconnect").strip() or "manual_reconnect"
        close_diag: dict[str, Any] = {"attempted": False, "timeout": False, "forced_ws_close": False}
        bridge_diag: dict[str, Any] = {"attempted": False, "started": False}
        authority_waiter = asyncio.Event() if wait_for_authority else None
        authority_diag: dict[str, Any] = {
            "required": bool(wait_for_authority),
            "ready": None if not wait_for_authority else False,
        }
        if authority_waiter is not None:
            self.authority_waiters.add(authority_waiter)
            current_task = asyncio.current_task()
            if current_task is not None:
                current_task.add_done_callback(
                    lambda _task, waiter=authority_waiter: self.authority_waiters.discard(waiter)
                )

        def _finish(payload: dict[str, Any]) -> dict[str, Any]:
            if authority_waiter is not None:
                self.authority_waiters.discard(authority_waiter)
            payload["authority"] = dict(authority_diag)
            return payload

        def _safe_strategy() -> dict[str, Any]:
            try:
                return operations.strategy_snapshot()
            except Exception:
                return {}

        try:
            self.authority_ready_at = None
            if tr is not None:
                os.environ["HUB_NATS_TRANSPORT"] = tr
            if override is not None:
                os.environ["HUB_NATS_URL_OVERRIDE"] = override
            elif url_override is not None:
                # Explicit empty override clears it.
                os.environ.pop("HUB_NATS_URL_OVERRIDE", None)
            try:
                strategy_update: dict[str, Any] = {}
                if transport is not None:
                    strategy_update["requested_transport"] = tr
                if url_override is not None:
                    strategy_update["url_override"] = override
                if strategy_update:
                    operations.configure_strategy(**strategy_update)
                operations.record_event(
                    "reconnect_requested",
                    transport=tr,
                    server=override,
                    summary=f"hub-root reconnect requested ({reconnect_reason})",
                    details={
                        "requested_transport": tr,
                        "url_override": override,
                        "reason": reconnect_reason,
                    },
                )
            except Exception:
                pass
            try:
                close_diag["route_reset"] = await self.reset_route_runtime(
                    reason=reconnect_reason,
                    notify_browser=True,
                )
            except Exception:
                pass
            # Trigger reconnect by closing the active connection if present.
            nc = self.nats_client
            if nc is not None:
                try:
                    close = getattr(nc, "close", None)
                    if callable(close):
                        close_diag["attempted"] = True
                        try:
                            close_timeout_s = float(os.getenv("HUB_ROOT_RECONNECT_CLOSE_TIMEOUT_S", "1.5") or "1.5")
                        except Exception:
                            close_timeout_s = 1.5
                        if close_timeout_s < 0.2:
                            close_timeout_s = 0.2

                        # NOTE: asyncio.wait_for() can itself hang if the close coroutine ignores cancellation.
                        # Use asyncio.wait() with timeout to ensure the HTTP request returns promptly.
                        try:
                            task = asyncio.create_task(close())
                            _done, pending = await asyncio.wait({task}, timeout=close_timeout_s)
                            if pending:
                                close_diag["timeout"] = True
                                try:
                                    task.cancel()
                                except Exception:
                                    pass
                                # Best-effort: force-close websocket transport internals if present to avoid a stuck close().
                                try:
                                    tr_obj = getattr(nc, "_transport", None)
                                    ws = getattr(tr_obj, "_ws", None) if tr_obj else None
                                    close_task = getattr(tr_obj, "_close_task", None) if tr_obj else None
                                    client = getattr(tr_obj, "_client", None) if tr_obj else None
                                    try:
                                        if ws is not None:
                                            t = asyncio.create_task(ws.close())
                                            await asyncio.wait({t}, timeout=0.5)
                                            if not t.done():
                                                try:
                                                    t.cancel()
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    try:
                                        if close_task is not None and hasattr(close_task, "done") and not close_task.done():
                                            close_task.set_result(None)
                                    except Exception:
                                        pass
                                    try:
                                        if client is not None:
                                            t = asyncio.create_task(client.close())
                                            await asyncio.wait({t}, timeout=0.5)
                                            if not t.done():
                                                try:
                                                    t.cancel()
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    close_diag["forced_ws_close"] = True
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                force_bridge_rearm = bool(_force_bridge_rearm) or nc is None or bool(close_diag.get("timeout"))
                bridge_diag = self.ensure_bridge_task(
                    force_rearm=force_bridge_rearm,
                    reason=(
                        f"{reconnect_reason}_without_active_nats"
                        if nc is None
                        else f"{reconnect_reason}_close_timeout"
                    ),
                )
                if bridge_diag.get("started"):
                    try:
                        operations.record_event(
                            "bridge_rearmed",
                            transport=tr,
                            server=override,
                            summary=f"hub-root reconnect rearmed bridge task ({reconnect_reason})",
                            details=dict(bridge_diag),
                        )
                    except Exception:
                        pass
            except Exception as exc:
                bridge_diag = {
                    "attempted": True,
                    "started": False,
                    "state": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if authority_waiter is not None:
                try:
                    authority_timeout_s = float(
                        os.getenv("HUB_ROOT_RECONNECT_AUTHORITY_TIMEOUT_S", "8.0") or "8.0"
                    )
                except Exception:
                    authority_timeout_s = 8.0
                authority_timeout_s = max(0.25, min(authority_timeout_s, 30.0))
                authority_started_at = time.monotonic()
                try:
                    await asyncio.wait_for(authority_waiter.wait(), timeout=authority_timeout_s)
                    authority_diag.update(
                        {
                            "ready": True,
                            "wait_sec": round(max(0.0, time.monotonic() - authority_started_at), 3),
                            "ready_at": self.authority_ready_at,
                        }
                    )
                except asyncio.TimeoutError:
                    authority_diag.update(
                        {
                            "ready": False,
                            "wait_sec": round(max(0.0, time.monotonic() - authority_started_at), 3),
                            "timeout_sec": authority_timeout_s,
                            "error": "hub_root_authority_timeout",
                        }
                    )
            return _finish({
                "ok": not bool(wait_for_authority) or bool(authority_diag.get("ready")),
                "requested": {"transport": tr, "url_override": override},
                "strategy": _safe_strategy(),
                "close": close_diag,
                "bridge": bridge_diag,
            })
        except Exception as exc:
            return _finish({
                "ok": False,
                "requested": {"transport": tr, "url_override": override},
                "strategy": _safe_strategy(),
                "close": close_diag,
                "bridge": bridge_diag,
                "error": f"{type(exc).__name__}: {exc}",
            })

