from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import service_event_bridge_api
from adaos.services.eventbus import LocalEventBus
from adaos.services.skill import service_event_bridge as bridge


def test_service_event_bridge_issues_scoped_rotating_capability(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda event: seen.append(event))
    monkeypatch.setattr(bridge, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "9123")

    first = bridge.service_event_bridge_environment("media_library_agent")
    second = bridge.service_event_bridge_environment("media_library_agent")

    assert first["ADAOS_SERVICE_EVENT_BRIDGE_URL"] == (
        "http://127.0.0.1:9123/api/node/internal/service-events"
    )
    assert first["ADAOS_SERVICE_EVENT_BRIDGE_TOKEN"] != second[
        "ADAOS_SERVICE_EVENT_BRIDGE_TOKEN"
    ]
    with pytest.raises(bridge.ServiceEventBridgeError) as stale:
        bridge.publish_service_event(
            token=first["ADAOS_SERVICE_EVENT_BRIDGE_TOKEN"],
            topic="io.out.stream.publish",
            payload={},
            remote_host="127.0.0.1",
        )
    assert stale.value.status_code == 401

    result = bridge.publish_service_event(
        token=second["ADAOS_SERVICE_EVENT_BRIDGE_TOKEN"],
        topic="io.out.stream.publish",
        payload={"receiver": "media.progress", "data": {"processed": 10}},
        remote_host="127.0.0.1",
    )

    assert result["ok"] is True
    assert result["skill"] == "media_library_agent"
    assert seen[0].source == "sdk.io.service:media_library_agent"
    assert seen[0].payload["_meta"]["skill_name"] == "media_library_agent"
    assert seen[0].payload["_meta"]["owner"] == "skill:media_library_agent"
    assert seen[0].payload["_meta"]["service_bridge"] is True


def test_service_event_bridge_rejects_remote_and_arbitrary_topics(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge,
        "get_ctx",
        lambda: SimpleNamespace(bus=LocalEventBus()),
    )
    environment = bridge.service_event_bridge_environment("test_service")
    token = environment["ADAOS_SERVICE_EVENT_BRIDGE_TOKEN"]

    with pytest.raises(bridge.ServiceEventBridgeError) as remote:
        bridge.publish_service_event(
            token=token,
            topic="io.out.stream.publish",
            payload={},
            remote_host="192.168.0.20",
        )
    with pytest.raises(bridge.ServiceEventBridgeError) as topic:
        bridge.publish_service_event(
            token=token,
            topic="skills.activated",
            payload={},
            remote_host="::1",
        )

    assert remote.value.status_code == 403
    assert topic.value.status_code == 403


def test_service_event_bridge_allows_only_exact_declared_domain_topics(
    monkeypatch,
) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("media.agent.changed", lambda event: seen.append(event))
    monkeypatch.setattr(bridge, "get_ctx", lambda: SimpleNamespace(bus=bus))
    environment = bridge.service_event_bridge_environment(
        "media_agent",
        publish_topics=("media.agent.changed",),
    )
    token = environment["ADAOS_SERVICE_EVENT_BRIDGE_TOKEN"]

    result = bridge.publish_service_event(
        token=token,
        topic="media.agent.changed",
        payload={"revision": 4},
        remote_host="127.0.0.1",
    )
    with pytest.raises(bridge.ServiceEventBridgeError) as undeclared:
        bridge.publish_service_event(
            token=token,
            topic="media.agent.changed.extra",
            payload={},
            remote_host="127.0.0.1",
        )

    assert result["ok"] is True
    assert seen[0].payload["revision"] == 4
    assert seen[0].payload["_meta"]["owner"] == "skill:media_agent"
    assert undeclared.value.status_code == 403


def test_service_event_bridge_http_endpoint_accepts_loopback_capability(
    monkeypatch,
) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda event: seen.append(event))
    monkeypatch.setattr(bridge, "get_ctx", lambda: SimpleNamespace(bus=bus))
    environment = bridge.service_event_bridge_environment("background_skill")
    app = FastAPI()
    app.include_router(
        service_event_bridge_api.router,
        prefix="/api/node/internal/service-events",
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/node/internal/service-events",
        headers={
            "X-AdaOS-Service-Event-Token": environment[
                "ADAOS_SERVICE_EVENT_BRIDGE_TOKEN"
            ]
        },
        json={
            "topic": "io.out.stream.publish",
            "payload": {"receiver": "background.progress", "data": {"count": 3}},
        },
    )

    assert response.status_code == 200
    assert response.json()["skill"] == "background_skill"
    assert seen[0].payload["receiver"] == "background.progress"
