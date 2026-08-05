from __future__ import annotations

import asyncio

import pytest

from adaos.services.bootstrap_runtime import BootstrapLifecycleCoordinator


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
