from __future__ import annotations

import asyncio
import logging

import pytest

from adaos.services.bootstrap_runtime import BootstrapLifecycleCoordinator, RootTransportService


pytestmark = pytest.mark.anyio


async def test_root_transport_repairs_missing_bridge() -> None:
    lifecycle = BootstrapLifecycleCoordinator()
    events: list[tuple[str, dict]] = []

    async def _reconnect(**kwargs):
        assert kwargs == {"_reason": "bridge_watchdog:test"}
        transport.bridge_factory = _bridge
        return {"bridge": transport.ensure_bridge_task()}

    async def _bridge() -> None:
        await asyncio.Event().wait()

    transport = RootTransportService(
        lifecycle=lifecycle,
        role=lambda: "hub",
        candidate_passive=lambda: False,
        reconnect=_reconnect,
        watchdog_interval=lambda: 1.0,
        record_event=lambda name, **details: events.append((name, details)),
        logger=logging.getLogger("test.bootstrap.root_transport"),
    )
    transport.bridge_factory = _bridge

    result = await transport.repair_missing_bridge(reason="test")

    assert result["state"] == "rearmed"
    assert transport.bridge_watchdog_rearm_total == 1
    assert lifecycle.find_live_task(transport.bridge_task_name) is not None
    assert events[0][0] == "bridge_watchdog_rearmed"

    await lifecycle.stop()


async def test_root_transport_skips_bridge_for_passive_candidate() -> None:
    lifecycle = BootstrapLifecycleCoordinator()
    reconnect_called = False

    async def _reconnect(**kwargs):
        nonlocal reconnect_called
        reconnect_called = True
        return {}

    transport = RootTransportService(
        lifecycle=lifecycle,
        role=lambda: "hub",
        candidate_passive=lambda: True,
        reconnect=_reconnect,
        watchdog_interval=lambda: 1.0,
        record_event=lambda *args, **kwargs: None,
        logger=logging.getLogger("test.bootstrap.root_transport"),
    )
    transport.bridge_factory = lambda: asyncio.sleep(0)

    result = await transport.repair_missing_bridge(reason="passive")

    assert result == {"attempted": False, "state": "not_required"}
    assert reconnect_called is False


async def test_root_transport_owns_route_reset_timeout_and_result() -> None:
    lifecycle = BootstrapLifecycleCoordinator()
    transport = RootTransportService(
        lifecycle=lifecycle,
        role=lambda: "hub",
        candidate_passive=lambda: False,
        reconnect=lambda **kwargs: asyncio.sleep(0, result={}),
        watchdog_interval=lambda: 1.0,
        record_event=lambda *args, **kwargs: None,
        logger=logging.getLogger("test.bootstrap.root_transport"),
    )
    calls: list[tuple[str, bool]] = []

    async def _reset(*, reason: str, notify_browser: bool) -> dict:
        calls.append((reason, notify_browser))
        return {"ok": True, "generation": 2}

    transport.route_reset = _reset

    result = await transport.reset_route_runtime(reason="reconnect", notify_browser=True)

    assert result == {"ok": True, "generation": 2}
    assert calls == [("reconnect", True)]
