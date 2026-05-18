import asyncio

import pytest

from adaos.domain import Event
import adaos.services.eventbus as eventbus_module
from adaos.services.eventbus import LocalEventBus


@pytest.mark.asyncio
async def test_local_event_bus_waits_for_async_handlers():
    bus = LocalEventBus()
    seen: list[str] = []

    async def handler(event: Event):
        await asyncio.sleep(0.05)
        seen.append(event.type)

    bus.subscribe("subnet.", handler)
    bus.publish(Event(type="subnet.stopping", payload={}, source="test", ts=0.0))

    ok = await bus.wait_for_idle(timeout=1.0)

    assert ok is True
    assert seen == ["subnet.stopping"]


def test_local_event_bus_subscribe_debug_is_quiet_by_default(monkeypatch):
    monkeypatch.delenv("ADAOS_EVENTBUS_TRACE_SUBSCRIBE", raising=False)
    messages: list[str] = []
    monkeypatch.setattr(eventbus_module._log, "debug", lambda message, *args, **kwargs: messages.append(str(message)))
    bus = LocalEventBus()

    def handler(event: Event):
        return None

    bus.subscribe("subnet.", handler)

    assert not messages


def test_local_event_bus_subscribe_debug_can_be_enabled(monkeypatch):
    monkeypatch.setenv("ADAOS_EVENTBUS_TRACE_SUBSCRIBE", "1")
    messages: list[str] = []
    monkeypatch.setattr(eventbus_module._log, "debug", lambda message, *args, **kwargs: messages.append(str(message)))
    bus = LocalEventBus()

    def handler(event: Event):
        return None

    bus.subscribe("subnet.", handler)

    assert any("bus.subscribe" in message for message in messages)


@pytest.mark.asyncio
async def test_local_event_bus_reports_webio_stream_control_pressure():
    bus = LocalEventBus()
    seen: list[str] = []

    async def handler(event: Event):
        await asyncio.sleep(0.05)
        seen.append(str(event.payload.get("stream_id") or ""))

    bus.subscribe("webio.stream.snapshot.requested", handler)
    for _idx in range(3):
        bus.publish(
            Event(
                type="webio.stream.snapshot.requested",
                payload={
                    "webspace_id": "desktop",
                    "target_node_id": "node-1",
                    "stream_id": "infrastate.realtime",
                    "source": "events_ws",
                },
                source="test",
                ts=0.0,
            )
        )

    snapshot = bus.backlog_snapshot()
    controls = snapshot["top_webio_stream_controls"]
    row = next(item for item in controls if item["receiver"] == "infrastate.realtime")

    assert row["event_type"] == "webio.stream.snapshot.requested"
    assert row["webspace_id"] == "desktop"
    assert row["source"] == "events_ws"
    assert row["incoming_total"] == 3
    assert row["queued_total"] == 3
    assert row["superseded_total"] >= 1

    ok = await bus.wait_for_idle(timeout=1.0)
    assert ok is True
    assert seen == ["infrastate.realtime"]
