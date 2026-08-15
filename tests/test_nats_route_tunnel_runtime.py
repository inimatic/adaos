from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from adaos.services.bootstrap_runtime import HubRouteProxyPolicy
from adaos.services.bootstrap_runtime.route_tunnel_runtime import NatsRouteTunnelRuntime


@pytest.mark.asyncio
async def test_candidate_route_runtime_stays_passive_without_subscribing() -> None:
    subscriptions: list[str] = []
    service = SimpleNamespace(
        _log=logging.getLogger("test.nats-route-runtime"),
        _route_policy=HubRouteProxyPolicy(),
    )

    async def _subscribe(subject: str, *, cb: object) -> None:
        subscriptions.append(subject)

    runtime = NatsRouteTunnelRuntime(
        service,
        rate_limited_log=lambda *args, **kwargs: None,
        is_ready=lambda: True,
    )

    await runtime.install(
        nc=SimpleNamespace(),
        subscribe=_subscribe,
        sub_workers=[],
        hub_id="sn_candidate",
        candidate_passive_mode=True,
        runtime_instance="candidate",
        hub_nats_verbose=False,
        hub_nats_quiet=True,
    )

    assert subscriptions == []
    assert runtime.hub_id == "sn_candidate"
    assert not hasattr(service, "_hub_root_route_reset")


@pytest.mark.asyncio
async def test_route_runtime_reports_control_state_after_subscription_is_ready() -> None:
    subscriptions: list[str] = []
    reports: list[str] = []
    authority_ready = asyncio.Event()
    service = SimpleNamespace(
        _log=logging.getLogger("test.nats-route-runtime"),
        _route_policy=HubRouteProxyPolicy(),
        _mark_hub_root_authority_ready=authority_ready.set,
    )

    async def _subscribe(subject: str, *, cb: object) -> object:
        subscriptions.append(subject)
        return SimpleNamespace(subject=subject, cb=cb)

    async def _report(trigger: str) -> None:
        assert authority_ready.is_set()
        reports.append(trigger)

    workers: list[asyncio.Task] = []
    runtime = NatsRouteTunnelRuntime(
        service,
        rate_limited_log=lambda *args, **kwargs: None,
        is_ready=lambda: True,
        report_control_lifecycle=_report,
    )

    await runtime.install(
        nc=SimpleNamespace(),
        subscribe=_subscribe,
        sub_workers=workers,
        hub_id="sn_ready",
        candidate_passive_mode=False,
        runtime_instance="active",
        hub_nats_verbose=False,
        hub_nats_quiet=True,
    )
    await asyncio.sleep(0)

    assert subscriptions == ["route.v2.to_hub.sn_ready.*"]
    assert reports == ["route.ready"]
    await runtime.close()
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_route_runtime_closes_owned_tunnels_and_tasks() -> None:
    closed = asyncio.Event()

    class _WebSocket:
        async def close(self) -> None:
            closed.set()

    async def _pending() -> None:
        await asyncio.Event().wait()

    service = SimpleNamespace(
        _log=logging.getLogger("test.nats-route-runtime"),
        _route_policy=HubRouteProxyPolicy(),
    )
    runtime = NatsRouteTunnelRuntime(
        service,
        rate_limited_log=lambda *args, **kwargs: None,
        is_ready=lambda: True,
    )
    runtime.tunnels["route-key"] = {"ws": _WebSocket()}
    task = asyncio.create_task(_pending())
    runtime.tunnel_tasks["route-key"] = task
    reset_callback = object()
    runtime.reset_callback = reset_callback
    service._hub_root_route_reset = reset_callback

    await runtime.close()
    await asyncio.sleep(0)

    assert closed.is_set()
    assert runtime.tunnels == {}
    assert runtime.tunnel_tasks == {}
    assert task.cancelled()
    assert service._hub_root_route_reset is None
