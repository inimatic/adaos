from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from adaos.apps.supervisor_runtime import ProcessSupervisor


def test_process_supervisor_describes_managed_process() -> None:
    proc = SimpleNamespace(pid=42, args=["python", "-m", "adaos.apps.api"], cwd="/runtime", poll=lambda: None)

    result = ProcessSupervisor(None).describe(proc)

    assert result["managed_pid"] == 42
    assert result["managed_alive"] is True
    assert result["managed_executable"] == "python"
    assert result["managed_cwd"] == "/runtime"


def test_process_supervisor_finds_listener_owner() -> None:
    connection = SimpleNamespace(
        status="LISTEN",
        laddr=SimpleNamespace(ip="0.0.0.0", port=8777),
        pid=123,
    )
    fake_psutil = SimpleNamespace(net_connections=lambda kind: [connection])

    assert ProcessSupervisor(fake_psutil).listener_owner_pid("127.0.0.1", 8777) == 123


@pytest.mark.anyio
async def test_process_supervisor_owns_handles_and_monitor_task() -> None:
    owner = ProcessSupervisor(None)
    release = asyncio.Event()

    async def _monitor() -> None:
        await release.wait()

    active = object()
    owner.track_active(active)
    first = owner.start_monitor(_monitor)
    second = owner.start_monitor(_monitor)

    assert owner.active is active
    assert first is second

    owner.request_stop()
    assert owner.desired_running is False
    assert owner.stopping is True
    await owner.stop_monitor()
    assert first.cancelled()


@pytest.mark.anyio
async def test_process_supervisor_owns_bounded_termination_ladder() -> None:
    class _Process:
        pid = 42

        def __init__(self) -> None:
            self.exited = False
            self.terminated = False

        def poll(self):
            return 0 if self.exited else None

        def terminate(self) -> None:
            self.terminated = True
            self.exited = True

        def kill(self) -> None:
            self.exited = True

    process = _Process()
    stages: list[str] = []

    await ProcessSupervisor(None).terminate_process(
        process,
        graceful_wait_sec=0.0,
        terminate_wait_sec=0.1,
        before_signal=stages.append,
    )

    assert process.terminated is True
    assert stages == ["forced_terminate"]


@pytest.mark.anyio
async def test_process_supervisor_allows_io_bound_process_to_reap_after_kill(monkeypatch) -> None:
    owner = ProcessSupervisor(None)
    process = SimpleNamespace(pid=42, poll=lambda: None)
    waits: list[tuple[float, float]] = []
    results = iter((False, False, True))
    signals: list[int] = []

    async def _wait(_process, timeout_sec: float, *, interval_sec: float) -> bool:
        waits.append((timeout_sec, interval_sec))
        return next(results)

    monkeypatch.setenv("ADAOS_SUPERVISOR_FORCED_KILL_WAIT_SEC", "30")
    monkeypatch.setattr(owner, "_wait_for_exit", _wait)

    await owner.terminate_process(
        process,
        graceful_wait_sec=8.0,
        terminate_wait_sec=5.0,
        signal_process=lambda _process, signal_number: signals.append(signal_number),
    )

    assert waits == [(8.0, 0.2), (5.0, 0.1), (30.0, 0.1)]
    assert len(signals) == 2
