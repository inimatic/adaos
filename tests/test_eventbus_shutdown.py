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


@pytest.mark.asyncio
async def test_thread_publish_uses_owner_loop_for_async_handlers():
    bus = LocalEventBus()
    owner_loop = asyncio.get_running_loop()
    release = asyncio.Event()
    started = asyncio.Event()
    seen_owner_loop: list[bool] = []

    async def handler(event: Event):
        seen_owner_loop.append(asyncio.get_running_loop() is owner_loop)
        started.set()
        await release.wait()

    bus.subscribe("io.out.stream.publish", handler)
    bus.publish(Event(type="prime.noop", payload={}, source="test", ts=0.0))

    def publish_from_thread() -> None:
        bus.publish(
            Event(
                type="io.out.stream.publish",
                payload={
                    "receiver": "browsers.summary",
                    "data": {"seq": 1},
                    "_meta": {"webspace_id": "desktop"},
                },
                source="test",
                ts=0.0,
            )
        )

    await asyncio.wait_for(asyncio.to_thread(publish_from_thread), timeout=0.5)
    await asyncio.wait_for(started.wait(), timeout=1.0)

    assert seen_owner_loop == [True]

    release.set()
    ok = await bus.wait_for_idle(timeout=1.0)

    assert ok is True


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


def test_browser_session_changed_is_bounded_by_default(monkeypatch):
    monkeypatch.delenv("ADAOS_EVENTBUS_BOUNDED_TOPICS", raising=False)
    monkeypatch.delenv("ADAOS_EVENTBUS_SUPERSEDE_BY_HANDLER_TOPICS", raising=False)

    bus = LocalEventBus()
    snapshot = bus.backlog_snapshot()

    assert "browser.session.changed" in snapshot["bounded_topics"]
    assert "io.out.stream.publish" in snapshot["bounded_topics"]


@pytest.mark.asyncio
async def test_browser_session_changed_supersedes_queued_handler_work(monkeypatch):
    monkeypatch.delenv("ADAOS_EVENTBUS_BOUNDED_TOPICS", raising=False)
    monkeypatch.delenv("ADAOS_EVENTBUS_SUPERSEDE_BY_HANDLER_TOPICS", raising=False)
    bus = LocalEventBus()
    release = asyncio.Event()
    seen: list[int] = []

    async def handler(event: Event):
        seen.append(int(event.payload.get("seq") or 0))
        await release.wait()

    bus.subscribe("browser.session.changed", handler)
    for seq in range(5):
        bus.publish(
            Event(
                type="browser.session.changed",
                payload={
                    "webspace_id": "desktop",
                    "device_id": "dev-1",
                    "seq": seq,
                },
                source="test",
                ts=0.0,
            )
        )

    snapshot = bus.backlog_snapshot()
    superseded = dict(snapshot["top_bounded_superseded_types"])

    assert snapshot["bounded_queue_total"] <= 1
    assert superseded["browser.session.changed"] >= 4

    release.set()
    ok = await bus.wait_for_idle(timeout=1.0)

    assert ok is True
    assert seen == [4]


def test_local_event_bus_unsubscribe_matching_removes_skill_handlers():
    bus = LocalEventBus()
    seen: list[str] = []

    def handler(event: Event):
        seen.append(event.type)

    setattr(handler, "_adaos_skill", "demo_skill")
    bus.subscribe("topic.", handler)

    removed = bus.unsubscribe_matching(
        lambda _prefix, candidate: getattr(candidate, "_adaos_skill", None) == "demo_skill"
    )
    bus.publish(Event(type="topic.demo", payload={}, source="test", ts=0.0))

    assert removed == 1
    assert seen == []


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


@pytest.mark.asyncio
async def test_local_event_bus_preserves_each_webio_stream_control_handler(monkeypatch):
    monkeypatch.delenv("ADAOS_EVENTBUS_BOUNDED_TOPICS", raising=False)
    monkeypatch.delenv("ADAOS_EVENTBUS_SUPERSEDE_BY_HANDLER_TOPICS", raising=False)
    bus = LocalEventBus()
    seen: list[str] = []

    async def first_handler(event: Event):
        await asyncio.sleep(0.01)
        seen.append(f"first:{event.payload.get('receiver')}")

    async def second_handler(event: Event):
        await asyncio.sleep(0.01)
        seen.append(f"second:{event.payload.get('receiver')}")

    bus.subscribe("webio.stream.snapshot.requested", first_handler)
    bus.subscribe("webio.stream.snapshot.requested", second_handler)
    bus.publish(
        Event(
            type="webio.stream.snapshot.requested",
            payload={
                "webspace_id": "desktop",
                "receiver": "infrastate.skills",
                "source": "events_ws",
            },
            source="test",
            ts=0.0,
        )
    )

    controls = bus.backlog_snapshot()["top_webio_stream_controls"]
    row = next(item for item in controls if item["receiver"] == "infrastate.skills")
    assert row["queued_total"] == 2
    assert int(row.get("superseded_total") or 0) == 0

    ok = await bus.wait_for_idle(timeout=1.0)

    assert ok is True
    assert seen == ["first:infrastate.skills", "second:infrastate.skills"]


def test_local_event_bus_keeps_distinct_webio_stream_receivers_for_same_handler():
    async def _run() -> None:
        bus = LocalEventBus()
        seen: list[str] = []
        receivers = ["infrastate.skills", "infrastate.scenarios", "infrastate.yjs.load_mark"]

        async def handler(event: Event):
            await asyncio.sleep(0.01)
            seen.append(str(event.payload.get("receiver") or ""))

        bus.subscribe("webio.stream.snapshot.requested", handler)
        for receiver in receivers:
            bus.publish(
                Event(
                    type="webio.stream.snapshot.requested",
                    payload={
                        "webspace_id": "desktop",
                        "receiver": receiver,
                        "source": "events_ws",
                    },
                    source="test",
                    ts=0.0,
                )
            )

        ok = await bus.wait_for_idle(timeout=1.0)

        assert ok is True
        assert seen == receivers

        controls = bus.backlog_snapshot()["top_webio_stream_controls"]
        by_receiver = {item["receiver"]: item for item in controls}
        for receiver in receivers:
            assert by_receiver[receiver]["incoming_total"] == 1
            assert by_receiver[receiver]["queued_total"] == 1
            assert int(by_receiver[receiver].get("superseded_total") or 0) == 0

    asyncio.run(_run())


def test_local_event_bus_keeps_distinct_webio_stream_params_for_same_receiver():
    async def _run() -> None:
        bus = LocalEventBus()
        seen: list[dict] = []

        async def handler(event: Event):
            await asyncio.sleep(0.01)
            seen.append(dict(event.payload.get("params") or {}))

        bus.subscribe("webio.stream.snapshot.requested", handler)
        for online_only in (True, False):
            bus.publish(
                Event(
                    type="webio.stream.snapshot.requested",
                    payload={
                        "webspace_id": "desktop",
                        "receiver": "browsers.devices",
                        "source": "events_ws",
                        "params": {"online_only": online_only},
                    },
                    source="test",
                    ts=0.0,
                )
            )

        ok = await bus.wait_for_idle(timeout=1.0)

        assert ok is True
        assert seen == [{"online_only": True}, {"online_only": False}]

        controls = [
            item
            for item in bus.backlog_snapshot()["top_webio_stream_controls"]
            if item["receiver"] == "browsers.devices"
        ]
        assert len(controls) == 2
        assert {item["params"] for item in controls} == {
            '{"online_only":true}',
            '{"online_only":false}',
        }

    asyncio.run(_run())


def test_local_event_bus_coalesces_io_out_stream_publish_by_receiver():
    async def _run() -> None:
        bus = LocalEventBus()
        seen: list[tuple[str, int]] = []

        async def handler(event: Event):
            seen.append((str(event.payload.get("receiver") or ""), int(event.payload.get("data", {}).get("seq") or 0)))

        bus.subscribe("io.out.stream.publish", handler)
        for seq in range(5):
            bus.publish(
                Event(
                    type="io.out.stream.publish",
                    payload={
                        "receiver": "browsers.devices",
                        "data": {"seq": seq},
                        "_meta": {"webspace_id": "desktop"},
                    },
                    source="test",
                    ts=0.0,
                )
            )
        for seq in range(2):
            bus.publish(
                Event(
                    type="io.out.stream.publish",
                    payload={
                        "receiver": "infrastate.marketplace.skills",
                        "data": {"seq": seq},
                        "_meta": {"webspace_id": "desktop"},
                    },
                    source="test",
                    ts=0.0,
                )
            )

        snapshot = bus.backlog_snapshot()
        superseded = dict(snapshot["top_bounded_superseded_types"])

        assert snapshot["bounded_queue_total"] <= 2
        assert superseded["io.out.stream.publish"] >= 4

        ok = await bus.wait_for_idle(timeout=1.0)

        assert ok is True
        assert seen == [
            ("browsers.devices", 4),
            ("infrastate.marketplace.skills", 1),
        ]

    asyncio.run(_run())
