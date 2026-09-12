import hashlib
import json
from uuid import uuid4
from types import SimpleNamespace

import pytest

from adaos.sdk.io import out
from adaos.sdk.io.context import io_meta
from adaos.services.eventbus import LocalEventBus
from adaos.services.webspace_id import coerce_webspace_id


def test_durable_chat_survives_missing_router_and_projection_replay(monkeypatch):
    from adaos.services import conversation_store

    monkeypatch.setattr(out, '_publish', lambda *args, **kwargs: None)
    conversation_id = f'conv.test.{uuid4().hex}'
    metadata = dict(conversation_id=conversation_id, conversation_owner='skill:test',
                    webspace_id='test', dialog_channel_id='test', thread_id='project:one')
    message_id = f'm.test.{uuid4().hex}'
    for _ in range(2):
        assert out.chat_append('Readable user request', from_='user', msg_id=message_id,
                               persist=True, _meta=metadata) == {'ok': True, 'persisted': True}
    messages = conversation_store.list_messages(conversation_id, thread_id='project:one')
    assert len(messages) == 1
    assert messages[0]['text'] == 'Readable user request'
    assert messages[0]['id'] == message_id


def test_durable_chat_requires_scope_and_does_not_claim_unavailable_storage(monkeypatch):
    from adaos.services import conversation_store

    monkeypatch.setattr(out, '_publish', lambda *args, **kwargs: pytest.fail('No projection before durable ack'))
    with pytest.raises(ValueError, match='explicit conversation'):
        out.chat_append('Message', persist=True, _meta={})
    monkeypatch.setattr(conversation_store, 'materialize_message', lambda **kwargs: None)
    result = out.chat_append('Message', persist=True, _meta=dict(conversation_id='conv.test',
                            conversation_owner='skill:test', webspace_id='test', dialog_channel_id='test'))
    assert result == {'ok': False, 'persisted': False}


def test_durable_chat_reuses_canonical_progress_id_for_projection(monkeypatch):
    from adaos.services import conversation_store

    projected = []
    monkeypatch.setattr(out, '_publish', lambda topic, payload, **kwargs: projected.append(payload))
    monkeypatch.setattr(conversation_store, 'materialize_message', lambda **kwargs: {'id': 'm.canonical.job'})
    out.chat_append('Completed', persist=True, _meta=dict(conversation_id='conv.test', progress_group_id='job.test',
                    conversation_owner='skill:test', webspace_id='test', dialog_channel_id='test'))
    assert projected[0]['id'] == 'm.canonical.job'


def test_coerce_webspace_id_unwraps_nested_and_stringified_values() -> None:
    assert coerce_webspace_id({"webspace_id": "default"}, fallback="fallback") == "default"
    assert coerce_webspace_id("{'webspace_id': 'default'}", fallback="fallback") == "default"
    assert coerce_webspace_id([{"workspace_id": "desktop"}], fallback="fallback") == "desktop"
    assert coerce_webspace_id("", fallback="fallback") == "fallback"


def test_stream_publish_normalizes_webspace_meta(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen.append(ev))
    monkeypatch.setattr(out, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr(out, "load_config", lambda: SimpleNamespace(node_id="member-01"))

    result = out.stream_publish(
        "infrastate.realtime",
        {"state": "ok"},
        _meta={
            "webspace_id": "{'webspace_id': 'default'}",
            "webspace_ids": [{"webspace_id": "default"}, {"workspace_id": "desktop"}],
        },
    )

    assert result == {"ok": True}
    assert len(seen) == 1
    meta = seen[0].payload["_meta"]
    assert meta["webspace_id"] == "default"
    assert meta["webspace_ids"] == ["default", "desktop"]
    assert meta["node_id"] == "member-01"
    assert meta["source_node_id"] == "member-01"


def test_stream_publish_explicit_webspace_overrides_ambient_webspace_ids(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen.append(ev))
    monkeypatch.setattr(out, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr(out, "load_config", lambda: SimpleNamespace(node_id="member-01"))

    with io_meta({"webspace_id": "desktop", "webspace_ids": ["desktop"]}):
        result = out.stream_publish(
            "infrastate.realtime",
            {"state": "ok"},
            _meta={"webspace_id": "homepoint"},
        )

    assert result == {"ok": True}
    meta = seen[0].payload["_meta"]
    assert meta["webspace_id"] == "homepoint"
    assert "webspace_ids" not in meta


def test_stream_publish_prefers_agent_context_node_id(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen.append(ev))
    monkeypatch.setattr(
        out,
        "get_ctx",
        lambda: SimpleNamespace(bus=bus, config=SimpleNamespace(node_id="ctx-node")),
    )
    monkeypatch.setattr(out, "load_config", lambda: SimpleNamespace(node_id="file-node"))

    result = out.stream_publish("infrastate.realtime", {"state": "ok"})

    assert result == {"ok": True}
    meta = seen[0].payload["_meta"]
    assert meta["node_id"] == "ctx-node"
    assert meta["source_node_id"] == "ctx-node"


def test_stream_publish_uses_env_node_id_before_file_config(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen.append(ev))
    monkeypatch.setattr(out, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setenv("ADAOS_NODE_ID", "env-node")
    monkeypatch.setattr(out, "load_config", lambda: SimpleNamespace(node_id="file-node"))

    result = out.stream_publish("infrastate.realtime", {"state": "ok"})

    assert result == {"ok": True}
    meta = seen[0].payload["_meta"]
    assert meta["node_id"] == "env-node"
    assert meta["source_node_id"] == "env-node"


def test_stream_publish_overrides_ambient_node_id(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen.append(ev))
    monkeypatch.setattr(out, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr(out, "load_config", lambda: SimpleNamespace(node_id="local-node"))

    with io_meta({"node_id": "incoming-node", "source_node_id": "incoming-node"}):
        result = out.stream_publish("infrastate.realtime", {"state": "ok"})

    assert result == {"ok": True}
    meta = seen[0].payload["_meta"]
    assert meta["node_id"] == "local-node"
    assert meta["source_node_id"] == "local-node"


def test_stream_variable_publish_wraps_replace_mode_envelope(monkeypatch) -> None:
    from adaos.sdk.io import stream_variable_publish

    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen.append(ev))
    monkeypatch.setattr(out, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr(out, "load_config", lambda: SimpleNamespace(node_id="member-01"))

    value = {"state": "ok", "count": 2}
    result = stream_variable_publish(
        "infrastate.runtime",
        value,
        var_id="runtime",
        seq=7,
        updated_at=123.0,
        ttl_ms=30000,
        ts=124.0,
        _meta={"webspace_id": "desktop"},
    )

    assert result == {"ok": True}
    assert len(seen) == 1
    event = seen[0].payload
    assert event["receiver"] == "infrastate.runtime"
    assert event["ts"] == 124.0
    assert event["_meta"]["stream_semantics"] == "replace_variable"
    assert event["_meta"]["webspace_id"] == "desktop"
    data = event["data"]
    assert data["id"] == "runtime"
    assert data["value"] == value
    assert data["seq"] == 7
    assert data["updated_at"] == 123.0
    assert data["ttl_ms"] == 30000
    expected_fingerprint = hashlib.sha1(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert data["fingerprint"] == expected_fingerprint


def test_stream_publish_uses_service_bridge_without_agent_context(monkeypatch) -> None:
    seen = []

    def missing_context():
        raise RuntimeError("AgentContext is not initialized")

    monkeypatch.setattr(out, "get_ctx", missing_context)
    monkeypatch.setattr(out, "load_config", lambda: SimpleNamespace(node_id="member-01"))
    monkeypatch.setattr(
        out,
        "_publish_via_service_bridge",
        lambda topic, payload: seen.append((topic, payload)),
    )
    monkeypatch.setenv(
        "ADAOS_SERVICE_EVENT_BRIDGE_URL",
        "http://127.0.0.1:8777/api/node/internal/service-events",
    )

    result = out.stream_publish(
        "media.progress",
        {"processed": 7},
        _meta={"webspace_id": "desktop"},
    )

    assert result == {"ok": True}
    assert seen[0][0] == "io.out.stream.publish"
    assert seen[0][1]["receiver"] == "media.progress"
    assert seen[0][1]["data"] == {"processed": 7}
