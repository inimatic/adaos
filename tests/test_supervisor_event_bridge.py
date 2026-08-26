from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import supervisor_event_bridge_api
from adaos.apps.supervisor_runtime.event_publisher import (
    SupervisorRuntimeEventPublisher,
)
from adaos.services import supervisor_event_bridge as bridge
from adaos.services.eventbus import LocalEventBus


def test_supervisor_event_bridge_publishes_update_contract(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("core.update.status", lambda event: seen.append(event))
    bus.subscribe("supervisor.update.status.raw", lambda event: seen.append(event))
    monkeypatch.setattr(bridge, "get_ctx", lambda: SimpleNamespace(bus=bus))

    result = bridge.publish_supervisor_event(
        topic="core.update.status",
        payload={
            "state": "preparing",
            "phase": "prepare",
            "prepare_elapsed_s": 45.0,
            "updated_at": 10.0,
        },
        remote_host="127.0.0.1",
    )

    assert result["published_topics"] == [
        "core.update.status",
        "supervisor.update.status.raw",
    ]
    assert [event.type for event in seen] == [
        "core.update.status",
        "supervisor.update.status.raw",
    ]
    assert seen[0].source == "supervisor.event_bridge"
    assert seen[0].payload["prepare_elapsed_s"] == 45.0
    assert seen[1].payload["_served_by"] == "supervisor_event_bridge"
    assert seen[1].payload["status"]["state"] == "preparing"


def test_supervisor_event_bridge_http_requires_token_and_loopback(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "supervisor-token")
    monkeypatch.setattr(
        bridge,
        "get_ctx",
        lambda: SimpleNamespace(bus=LocalEventBus()),
    )
    app = FastAPI()
    app.include_router(
        supervisor_event_bridge_api.router,
        prefix="/api/node/internal/supervisor-events",
    )
    client = TestClient(app, client=("127.0.0.1", 50000))
    envelope = {
        "topic": "core.update.status",
        "payload": {"state": "countdown", "updated_at": 20.0},
    }

    unauthorized = client.post(
        "/api/node/internal/supervisor-events",
        json=envelope,
    )
    accepted = client.post(
        "/api/node/internal/supervisor-events",
        headers={"X-AdaOS-Token": "supervisor-token"},
        json=envelope,
    )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["topic"] == "core.update.status"


def test_supervisor_event_bridge_rejects_remote_and_unknown_topic(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge,
        "get_ctx",
        lambda: SimpleNamespace(bus=LocalEventBus()),
    )

    try:
        bridge.publish_supervisor_event(
            topic="core.update.status",
            payload={},
            remote_host="192.168.0.30",
        )
    except bridge.SupervisorEventBridgeError as exc:
        assert exc.code == "supervisor_event_loopback_required"
    else:
        raise AssertionError("remote supervisor event must be rejected")

    try:
        bridge.publish_supervisor_event(
            topic="skills.activated",
            payload={},
            remote_host="::1",
        )
    except bridge.SupervisorEventBridgeError as exc:
        assert exc.code == "supervisor_event_topic_denied"
    else:
        raise AssertionError("unknown supervisor topic must be rejected")


def test_supervisor_runtime_event_publisher_coalesces_without_blocking_loop() -> None:
    asyncio.run(_exercise_supervisor_runtime_event_publisher_coalescing())


async def _exercise_supervisor_runtime_event_publisher_coalescing() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    delivered: list[dict] = []

    def _deliver(envelope: dict) -> None:
        delivered.append(envelope)
        if len(delivered) == 1:
            first_started.set()
            release_first.wait(timeout=2.0)

    publisher = SupervisorRuntimeEventPublisher(
        _deliver,
        logger=logging.getLogger("test.supervisor.event_publisher"),
    )
    publisher.start()
    assert publisher.publish("core.update.status", {"revision": 1})
    assert await asyncio.to_thread(first_started.wait, 1.0)

    assert publisher.publish("core.update.status", {"revision": 2})
    assert publisher.publish("core.update.status", {"revision": 3})
    await asyncio.sleep(0)
    release_first.set()
    for _ in range(100):
        if len(delivered) == 2:
            break
        await asyncio.sleep(0.01)
    await publisher.close()

    assert [item["payload"]["revision"] for item in delivered] == [1, 3]
    snapshot = publisher.snapshot()
    assert snapshot["accepted_total"] == 3
    assert snapshot["superseded_total"] == 1
    assert snapshot["delivered_total"] == 2
    assert snapshot["failed_total"] == 0
