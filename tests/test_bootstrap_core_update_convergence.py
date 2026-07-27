import asyncio

from adaos.services.bootstrap import (
    _core_update_waits_for_supervisor_convergence,
    _watch_supervisor_core_update_convergence,
)


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str, str]] = []

    async def emit(self, topic: str, payload: dict, *, source: str, actor: str) -> None:
        self.events.append((topic, dict(payload), source, actor))


def test_root_promoted_status_waits_for_supervisor_convergence() -> None:
    assert _core_update_waits_for_supervisor_convergence(
        {"state": "succeeded", "phase": "root_promoted"}
    )
    assert not _core_update_waits_for_supervisor_convergence(
        {"state": "succeeded", "phase": "validate"}
    )


def test_supervisor_convergence_watch_emits_final_status_once() -> None:
    initial = {"state": "succeeded", "phase": "root_promoted", "updated_at": 1.0}
    final = {"state": "succeeded", "phase": "validate", "updated_at": 2.0}
    reads = iter([dict(initial), dict(final)])
    bus = _RecordingBus()

    result = asyncio.run(
        _watch_supervisor_core_update_convergence(
            bus,
            read_status=lambda: next(reads),
            initial_status=initial,
            poll_interval_s=0.05,
            timeout_s=1.0,
        )
    )

    assert result["ok"] is True
    assert result["emitted_total"] == 1
    assert result["status"] == final
    assert bus.events == [
        ("core.update.status", final, "supervisor.convergence", "system"),
    ]


def test_supervisor_convergence_watch_is_noop_for_terminal_boot() -> None:
    final = {"state": "succeeded", "phase": "validate", "updated_at": 2.0}
    bus = _RecordingBus()

    result = asyncio.run(
        _watch_supervisor_core_update_convergence(
            bus,
            read_status=lambda: (_ for _ in ()).throw(AssertionError("must not poll")),
            initial_status=final,
            poll_interval_s=0.05,
            timeout_s=1.0,
        )
    )

    assert result["ok"] is True
    assert result["emitted_total"] == 0
    assert bus.events == []
