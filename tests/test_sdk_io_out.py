import hashlib
import json
from types import SimpleNamespace

from adaos.sdk.io import out
from adaos.services.eventbus import LocalEventBus
from adaos.services.webspace_id import coerce_webspace_id


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
