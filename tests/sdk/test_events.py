from __future__ import annotations

import pytest
from types import SimpleNamespace

from adaos.sdk.data import events
from adaos.sdk.core.errors import SdkRuntimeNotInitialized
from adaos.services.agent_context import clear_ctx, set_ctx


def test_events_publish_no_ctx(monkeypatch):
    monkeypatch.delenv("ADAOS_SERVICE_EVENT_BRIDGE_URL", raising=False)
    clear_ctx()
    with pytest.raises(SdkRuntimeNotInitialized):
        events.publish("demo.event", {"foo": "bar"})


def test_events_publish_uses_service_bridge_without_context(monkeypatch):
    clear_ctx()
    seen = []
    monkeypatch.setenv(
        "ADAOS_SERVICE_EVENT_BRIDGE_URL",
        "http://127.0.0.1:8777/api/node/internal/service-events",
    )
    monkeypatch.setattr(
        events,
        "publish_service_event",
        lambda topic, payload: seen.append((topic, payload)) or {"ok": True},
    )

    result = events.publish(
        "media.agent.changed",
        {"revision": 7},
        source="media_agent",
        schema="adaos.media.changed.v1",
    )

    assert result == {"ok": True}
    assert seen[0][0] == "media.agent.changed"
    assert seen[0][1]["revision"] == 7
    assert seen[0][1]["_meta"]["event"]["schema"] == "adaos.media.changed.v1"


def test_events_publish_prefers_service_bridge_over_process_local_context(monkeypatch):
    local_events = []
    bridge_events = []
    set_ctx(
        SimpleNamespace(
            bus=SimpleNamespace(
                publish=lambda *args, **kwargs: local_events.append((args, kwargs))
            )
        )
    )
    monkeypatch.setenv(
        "ADAOS_SERVICE_EVENT_BRIDGE_URL",
        "http://127.0.0.1:8777/api/node/internal/service-events",
    )
    monkeypatch.setattr(
        events,
        "publish_service_event",
        lambda topic, payload: bridge_events.append((topic, payload))
        or {"ok": True},
    )

    try:
        result = events.publish("media.agent.changed", {"revision": 8})
    finally:
        clear_ctx()

    assert result == {"ok": True}
    assert local_events == []
    assert bridge_events[0][0] == "media.agent.changed"
