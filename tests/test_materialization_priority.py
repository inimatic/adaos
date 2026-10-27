import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from adaos.services.scenario.webspace_components.materialization import (
    MaterializationExecutorOwner, _MaterializationQueue, capture_cpu_timings, materialization_request_priority,
)


@pytest.mark.asyncio
async def test_navigation_bypasses_waiting_background_without_adding_threads():
    owner = MaterializationExecutorOwner()
    gate, started = threading.Event(), threading.Event()
    order, threads = [], []

    def call(label, block=False):
        threads.append(threading.get_ident()); order.append(label)
        if block:
            started.set(); assert gate.wait(5)
        return label

    async def run(label, interactive=False, block=False):
        with capture_cpu_timings({}, label, interactive=interactive):
            return await owner.run_cpu(call, label, block, max_workers=1, oneshot=False)

    active = asyncio.create_task(run("active", block=True))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        backgrounds = [asyncio.create_task(run("background" + str(i))) for i in range(4)]
        await asyncio.sleep(0)
        foreground = asyncio.create_task(run("navigation", interactive=True))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(active, *backgrounds, foreground)
        assert order[:2] == ["active", "navigation"]
        assert len(set(threads)) == 1
    finally:
        gate.set(); owner.shutdown()


def test_priority_burst_yields_to_background_and_backlog_is_bounded():
    gate, started = threading.Event(), threading.Event()
    order = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        queue = _MaterializationQueue(executor, 1, pending_limit=8)
        active = queue.submit(lambda: (started.set(), gate.wait(5)), interactive=False)
        assert started.wait(3)
        try:
            jobs = [queue.submit(lambda i=i: order.append("nav" + str(i)), interactive=True) for i in range(7)]
            background = queue.submit(lambda: order.append("background"), interactive=False)
            with pytest.raises(RuntimeError, match="capacity exceeded"):
                queue.submit(lambda: None, interactive=True)
            gate.set(); active.result(5)
            for job in [*jobs, background]:job.result(5)
            assert order[:5] == ["nav0", "nav1", "nav2", "nav3", "background"]
        finally:
            gate.set(); queue.close()


@pytest.mark.asyncio
async def test_cancelled_queued_read_and_retired_queue_do_not_execute():
    owner = MaterializationExecutorOwner()
    gate, started = threading.Event(), threading.Event()
    executed = []
    def active_call():
        started.set(); assert gate.wait(5)
        return "completed"
    active = asyncio.create_task(owner.run_cpu(active_call, max_workers=1, oneshot=False))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        obsolete = asyncio.create_task(owner.run_cpu(lambda: executed.append("obsolete"), max_workers=1, oneshot=False))
        pending = asyncio.create_task(owner.run_cpu(lambda: executed.append("retired"), max_workers=1, oneshot=False))
        await asyncio.sleep(0)
        obsolete.cancel()
        with pytest.raises(asyncio.CancelledError):await obsolete
        owner.shutdown()
        gate.set()
        assert await active == "completed", "Retirement must not cancel an active callable"
        with pytest.raises(asyncio.CancelledError):await pending
        assert executed == []
        assert await owner.run_cpu(lambda: "new epoch", max_workers=1, oneshot=False) == "new epoch"
    finally:
        gate.set(); owner.shutdown()


def test_same_owner_supports_independent_event_loops():
    owner = MaterializationExecutorOwner()
    def run(value):
        return asyncio.run(owner.run_cpu(lambda: value, max_workers=1, oneshot=False))
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            assert sorted(callers.map(run, [1, 2])) == [1, 2]
    finally:
        owner.shutdown()


def test_obsolete_waiters_do_not_consume_the_capacity_of_latest_navigation():
    gate, started = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        queue = _MaterializationQueue(executor, 1, pending_limit=1)
        active = queue.submit(lambda: (started.set(), gate.wait(5)), interactive=False)
        assert started.wait(3)
        try:
            obsolete = queue.submit(lambda: pytest.fail("obsolete job executed"), interactive=True)
            assert obsolete.cancel()
            latest = queue.submit(lambda: "latest", interactive=True)
            gate.set(); active.result(5)
            assert latest.result(5) == "latest"
        finally:
            gate.set(); queue.close()


@pytest.mark.asyncio
async def test_legacy_unprofiled_and_profiled_calls_inherit_only_their_admitted_priority():
    owner = MaterializationExecutorOwner()
    gate, started = threading.Event(), threading.Event()
    order, observations = [], {}
    def call(label, block=False):
        order.append(label)
        if block:
            started.set(); assert gate.wait(5)
        return label
    async def legacy():
        with materialization_request_priority("nav:legacy"):
            # The phased legacy entry point has no timing wrapper here.
            return await owner.run_cpu(call, "legacy", max_workers=1, oneshot=False)
    active = asyncio.create_task(owner.run_cpu(call, "active", True, max_workers=1, oneshot=False))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        background = asyncio.create_task(owner.run_cpu(call, "background", max_workers=1, oneshot=False))
        await asyncio.sleep(0)
        navigation = asyncio.create_task(legacy()); await asyncio.sleep(0)
        gate.set(); await asyncio.gather(active, background, navigation)
        assert order == ["active", "legacy", "background"]
        with materialization_request_priority("nav:profiled"):
            with capture_cpu_timings(observations, "legacy"):
                await owner.run_cpu(lambda: None, max_workers=1, oneshot=False)
        with capture_cpu_timings(observations, "background"):
            await owner.run_cpu(lambda: None, max_workers=1, oneshot=False)
        assert observations["legacy.interactive"] == 1
        assert observations["background.interactive"] == 0
    finally:
        gate.set(); owner.shutdown()
