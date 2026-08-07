from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from adaos.services.bootstrap_runtime import BootstrapBootCoordinator, BootstrapLifecycleCoordinator


pytestmark = pytest.mark.anyio


async def test_lifecycle_coordinator_serializes_boot_attempts() -> None:
    lifecycle = BootstrapLifecycleCoordinator()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[object] = []

    async def _boot(app: object) -> None:
        calls.append(app)
        entered.set()
        await release.wait()
        lifecycle.booted = True

    first = asyncio.create_task(lifecycle.run_once("app", _boot))
    await entered.wait()
    second = asyncio.create_task(lifecycle.run_once("ignored", _boot))
    await asyncio.sleep(0)

    assert calls == ["app"]
    release.set()
    await asyncio.gather(first, second)

    assert calls == ["app"]
    assert lifecycle.boot_in_progress is False
    assert lifecycle.boot_done.is_set()


async def test_lifecycle_coordinator_starts_named_task_once_and_resets() -> None:
    lifecycle = BootstrapLifecycleCoordinator()
    release = asyncio.Event()

    async def _worker() -> None:
        await release.wait()

    first = lifecycle.start_task_once("bootstrap-worker", _worker)
    second = lifecycle.start_task_once("bootstrap-worker", _worker)

    assert first is second
    assert lifecycle.find_live_task("bootstrap-worker") is first

    lifecycle.ready.set()
    lifecycle.booted = True
    await lifecycle.stop()

    assert first.cancelled()
    assert lifecycle.boot_tasks == []
    assert lifecycle.booted is False
    assert lifecycle.is_ready() is False


async def test_lifecycle_coordinator_replaces_and_tracks_tasks() -> None:
    lifecycle = BootstrapLifecycleCoordinator()
    release = asyncio.Event()

    async def _worker() -> None:
        await release.wait()

    old = lifecycle.start_task_once("bootstrap-worker", _worker)
    replacement, cancelled_previous = lifecycle.replace_task("bootstrap-worker", _worker)
    await asyncio.gather(old, return_exceptions=True)

    assert cancelled_previous is True
    assert old.cancelled()
    assert lifecycle.find_live_task("bootstrap-worker") is replacement

    external = asyncio.create_task(_worker(), name="external-worker")
    assert lifecycle.track_task(external) is external
    assert lifecycle.track_task(external) is external
    assert lifecycle.boot_tasks.count(external) == 1

    await lifecycle.stop()


async def test_boot_coordinator_uses_agent_context_bus() -> None:
    class _ContextBus:
        pass

    class _StopAfterBusSelection(RuntimeError):
        pass

    class _IoBus:
        def __init__(self, *, core: object) -> None:
            assert core is context_bus

        async def connect(self) -> None:
            raise _StopAfterBusSelection

    class _Watchdog:
        report_control_lifecycle = object()
        emit_node_status = object()

    context_bus = _ContextBus()
    config = SimpleNamespace(role="member", node_id="node-1", subnet_id="subnet-1")
    service = SimpleNamespace(
        _booted=False,
        _lifecycle=SimpleNamespace(bind_app=lambda app: None),
        _log=SimpleNamespace(debug=lambda *args, **kwargs: None),
        _prepare_environment=lambda: None,
        ctx=SimpleNamespace(bus=context_bus, config=config),
    )
    operations = SimpleNamespace(
        load_config=lambda **kwargs: config,
        status_watchdog_service=SimpleNamespace(
            from_environment=lambda **kwargs: _Watchdog(),
        ),
        report_hub_control_lifecycle_state=lambda config: None,
        should_emit_node_status=lambda **kwargs: False,
        bus=SimpleNamespace(emit=lambda *args, **kwargs: None),
        local_event_bus_type=_ContextBus,
        local_io_bus_type=_IoBus,
    )

    with pytest.raises(_StopAfterBusSelection):
        await BootstrapBootCoordinator().run(service, operations, SimpleNamespace())


def test_candidate_member_runtime_does_not_own_upstream_link() -> None:
    coordinator = BootstrapBootCoordinator()

    assert coordinator._member_runtime_owns_upstream(
        SimpleNamespace(runtime_transition_role=lambda: "active")
    ) is True
    assert coordinator._member_runtime_owns_upstream(
        SimpleNamespace(runtime_transition_role=lambda: "candidate")
    ) is False
