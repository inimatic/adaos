from __future__ import annotations

import asyncio

from adaos.services.webio_snapshot_demand import (
    clear_snapshot_demand_for_tests,
    request_snapshot_event,
    snapshot_demand_snapshot,
)


def test_snapshot_demand_debounces_identical_requests_on_running_loop() -> None:
    clear_snapshot_demand_for_tests()
    published: list[tuple[str, dict[str, object], str]] = []

    def _publish(event_type: str, payload: dict[str, object], source: str) -> None:
        published.append((event_type, payload, source))

    async def _run() -> None:
        first = request_snapshot_event(
            "webio.stream.snapshot.requested",
            {
                "topic": "webio.stream.desktop.todo.list",
                "webspace_id": "desktop",
                "receiver": "todo.list",
                "transport": "ws",
            },
            "events_ws",
            _publish,
            debounce_s=0.01,
            cooldown_s=0.0,
        )
        second = request_snapshot_event(
            "webio.stream.snapshot.requested",
            {
                "topic": "webio.stream.desktop.todo.list",
                "webspace_id": "desktop",
                "receiver": "todo.list",
                "transport": "webrtc_data:events",
            },
            "webrtc.peer",
            _publish,
            debounce_s=0.01,
            cooldown_s=0.0,
        )
        assert first is True
        assert second is False
        assert published == []
        await asyncio.sleep(0.03)

    asyncio.run(_run())

    assert len(published) == 1
    assert published[0][0] == "webio.stream.snapshot.requested"
    assert published[0][1]["transport"] == "webrtc_data:events"
    assert published[0][2] == "webrtc.peer"
    snapshot = snapshot_demand_snapshot()
    assert snapshot["coalesced_total"] == 1
    assert snapshot["published_total"] == 1
    assert snapshot["pending"] == 0
    clear_snapshot_demand_for_tests()


def test_snapshot_demand_drops_recent_identical_request_without_event_loop() -> None:
    clear_snapshot_demand_for_tests()
    published: list[tuple[str, dict[str, object], str]] = []

    def _publish(event_type: str, payload: dict[str, object], source: str) -> None:
        published.append((event_type, payload, source))

    payload = {
        "topic": "webio.yjs.desktop.infrastate.summary",
        "webspace_id": "desktop",
        "slot": "infrastate.summary",
        "projection": "infrastate.summary",
        "transport": "ws",
    }

    assert request_snapshot_event(
        "webio.yjs.snapshot.requested",
        payload,
        "events_ws",
        _publish,
        debounce_s=0.01,
        cooldown_s=1.0,
    ) is True
    assert request_snapshot_event(
        "webio.yjs.snapshot.requested",
        {**payload, "transport": "webrtc_data:events"},
        "webrtc.peer",
        _publish,
        debounce_s=0.01,
        cooldown_s=1.0,
    ) is False

    assert len(published) == 1
    snapshot = snapshot_demand_snapshot()
    assert snapshot["immediate_total"] == 1
    assert snapshot["dropped_recent_total"] == 1
    clear_snapshot_demand_for_tests()
