from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from adaos.services.scenario.webspace_components import (
    MaterializationExecutorOwner,
    WebspaceCacheState,
    WebspaceTaskState,
)
from adaos.services.scenario.webspace_components.materialization import capture_cpu_timings


def test_webspace_state_owners_are_isolated() -> None:
    tasks = WebspaceTaskState()
    caches = WebspaceCacheState()

    tasks.put_record(
        tasks.WEBSPACE_REBUILD_STATUS,
        "alpha",
        {"state": "running"},
    )
    caches.put_resolved_webspace(
        "fingerprint",
        {"scenario_id": "home", "_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=100,
    )

    assert tasks.get_record(tasks.WEBSPACE_REBUILD_STATUS, "alpha") == {"state": "running"}
    assert caches.get_resolved_webspace("fingerprint") == {
        "scenario_id": "home",
        "_cache_size_bytes": 10,
    }
    assert tasks.task_count(tasks.WORKFLOW_SYNC) == 0
    assert caches.clear_materialized_webspaces() == 0


@pytest.mark.asyncio
async def test_webspace_task_owner_replaces_and_finishes_by_identity() -> None:
    tasks = WebspaceTaskState()
    blocker = asyncio.Event()
    first = asyncio.create_task(blocker.wait(), name="first")
    second = asyncio.create_task(blocker.wait(), name="second")

    tasks.put_task(tasks.SCENARIO_SWITCH, "desktop", first)
    replaced = tasks.put_task(
        tasks.SCENARIO_SWITCH,
        "desktop",
        second,
        cancel_existing=True,
    )
    await asyncio.sleep(0)

    assert replaced is first
    assert first.cancelled()
    assert tasks.pop_task(tasks.SCENARIO_SWITCH, "desktop", expected=first) is None
    assert tasks.active_task(tasks.SCENARIO_SWITCH, "desktop") is second
    assert tasks.pop_task(tasks.SCENARIO_SWITCH, "desktop", expected=second) is second

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second


def test_webspace_cache_owner_applies_lru_and_byte_limits() -> None:
    caches = WebspaceCacheState()

    caches.put_resolved_webspace(
        "first",
        {"_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=25,
    )
    caches.put_resolved_webspace(
        "second",
        {"_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=25,
    )
    assert caches.get_resolved_webspace("first") is not None

    caches.put_resolved_webspace(
        "third",
        {"_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=25,
    )

    assert caches.get_resolved_webspace("second") is None
    assert caches.get_resolved_webspace("first") is not None
    assert caches.get_resolved_webspace("third") is not None


def test_webspace_cache_owner_invalidates_materialized_entries_by_identity() -> None:
    caches = WebspaceCacheState()
    for key, webspace_id in (("one", "alpha"), ("two", "beta"), ("three", "alpha")):
        caches.put_materialized_webspace(
            key,
            {"identity": {"webspace_id": webspace_id}, "_cache_size_bytes": 1},
            max_entries=4,
            max_bytes=100,
        )

    removed = caches.discard_materialized_webspaces(
        lambda _key, value: value.get("identity", {}).get("webspace_id") == "alpha"
    )

    assert removed == 2
    assert caches.get_materialized_webspace("one") is None
    assert caches.get_materialized_webspace("two") is not None
    assert caches.get_materialized_webspace("three") is None


def test_webspace_cache_owner_clears_desktop_scenario_discovery() -> None:
    caches = WebspaceCacheState()
    cache_key = "workspace:C:/workspace/scenarios"
    caches.put_desktop_scenarios(
        cache_key,
        1.0,
        (("C:/workspace/scenarios/alpha", 1, 0),),
        [("alpha", "Alpha")],
    )

    assert caches.clear_desktop_scenarios() == 1
    assert caches.get_desktop_scenarios(cache_key) is None


def test_materialization_executor_owner_reuses_and_replaces_executor() -> None:
    owner = MaterializationExecutorOwner()

    first = owner.get(max_workers=1)
    assert owner.get(max_workers=4) is first

    owner.shutdown()
    second = owner.get(max_workers=1)

    assert second is not first
    owner.shutdown()


@pytest.mark.asyncio
async def test_materialization_executor_owner_runs_cpu_work_in_both_modes() -> None:
    owner = MaterializationExecutorOwner()

    assert await owner.run_cpu(lambda value: value + 1, 4, max_workers=1, oneshot=True) == 5
    assert await owner.run_cpu(lambda value: value * 2, 4, max_workers=1, oneshot=False) == 8

    owner.shutdown()


@pytest.mark.asyncio
async def test_materialization_cpu_timings_separate_queue_and_work() -> None:
    owner = MaterializationExecutorOwner()
    entered = threading.Event()
    release = threading.Event()
    timings: dict[str, float] = {}

    def blocked():
        entered.set()
        assert release.wait(5)

    first = asyncio.create_task(owner.run_cpu(blocked, max_workers=1, oneshot=False))
    try:
        assert await asyncio.to_thread(entered.wait, 5)

        async def queued():
            with capture_cpu_timings(timings, "catalog"):
                return await owner.run_cpu(lambda: 42, max_workers=1, oneshot=False)

        second = asyncio.create_task(queued())
        await asyncio.sleep(0.025)
        release.set()
        assert await second == 42
        await first
        assert timings["catalog.queue_wait"] >= 10
        assert timings["catalog.function_wall"] < timings["catalog.queue_wait"]
        assert timings["catalog.loop_resume_delay"] >= 0
        parts = sum(timings[f"catalog.{part}"] for part in ("queue_wait", "function_wall", "loop_resume_delay"))
        assert parts == pytest.approx(timings["catalog.dispatch_wall"], abs=0.01)
    finally:
        release.set()
        await first
        owner.shutdown()


@pytest.mark.asyncio
async def test_materialization_cpu_timings_isolate_tasks_and_preserve_errors() -> None:
    owner = MaterializationExecutorOwner()
    sinks = [{}, {}]

    async def observed(index):
        with capture_cpu_timings(sinks[index], str(index)):
            assert await owner.run_cpu(lambda: index, max_workers=1, oneshot=False) == index

    try:
        await asyncio.gather(observed(0), observed(1))
        assert all(key.startswith("0.") for key in sinks[0])
        assert all(key.startswith("1.") for key in sinks[1])
        timings = {}

        def fails():
            raise ValueError("expected")

        with capture_cpu_timings(timings, "oneshot"):
            with pytest.raises(ValueError, match="expected"):
                await owner.run_cpu(fails, max_workers=1, oneshot=True)
        assert timings["oneshot.queue_wait"] == timings["oneshot.loop_resume_delay"] == 0
        assert timings["oneshot.function_wall"] >= 0
        before = dict(timings)
        await owner.run_cpu(lambda: None, max_workers=1, oneshot=True)
        assert timings == before  # Scope reset even on an exception.
    finally:
        owner.shutdown()


@pytest.mark.asyncio
async def test_materialization_cpu_cancellation_does_not_write_late_timings() -> None:
    owner = MaterializationExecutorOwner()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    timings = {}

    def blocked():
        entered.set()
        try:
            assert release.wait(5)
        finally:
            finished.set()

    async def observed():
        with capture_cpu_timings(timings, "cancelled"):
            await owner.run_cpu(blocked, max_workers=1, oneshot=False)

    task = asyncio.create_task(observed())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "cancelled.function_wall" not in timings
        captured = dict(timings)
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        assert captured == timings
    finally:
        release.set()
        owner.shutdown()


@pytest.mark.asyncio
async def test_materialization_executor_owner_decodes_worker_result(monkeypatch) -> None:
    owner = MaterializationExecutorOwner()

    class Process:
        pid = 2_000_000_000
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def create_subprocess(*cmd, **_kwargs):
        Path(cmd[-1]).write_text(
            '{"ok":true,"worker_rss_bytes":0,"snapshot_update_b64":"YQ==",'
            '"state_vector_b64":"Yg==","materialized_payload":{"scenario_id":"home"}}',
            encoding="utf-8",
        )
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    result = await owner.run_worker(
        {"schema": "test"},
        timeout_s=1.0,
        max_rss_bytes=1024,
        max_result_bytes=4096,
        result_adapter=lambda payload: payload["scenario_id"],
    )

    assert result["snapshot_update"] == b"a"
    assert result["state_vector"] == b"b"
    assert result["entry"] == "home"
