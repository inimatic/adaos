from __future__ import annotations

import base64
import importlib
import json
from collections import deque

import pytest

from adaos.services import conversation_store, hub_root_outbox_store as store
from adaos.services.artifact_pipeline import storage


@pytest.fixture(autouse=True)
def isolated_outbox(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_base_state_dir", lambda: tmp_path)


def test_missing_outbox_is_empty():
    assert store.load_outbox_items("telegram") == deque()


@pytest.mark.parametrize("raw", [b"{", b"\xff", b"[]", b'{"items":null}',
    b'{"items":[{}]}', b'{"items":[{"subject":"tg.output.test","data_b64":"!!"}]}'])
def test_corruption_is_not_an_empty_queue_or_silent_overwrite(raw):
    path = store.outbox_store_path("telegram")
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        store.load_outbox_items("telegram")
    with pytest.raises(ValueError):
        store.save_outbox_items("telegram", deque())
    assert path.read_bytes() == raw


def test_partial_corruption_does_not_silently_drop_one_message():
    path = store.outbox_store_path("telegram")
    path.write_text(json.dumps({"items": [
        {"subject": "tg.output.test", "data_b64": base64.b64encode(b"kept").decode()},
        {"subject": "tg.output.test", "data_b64": "broken!"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_outbox_items("telegram")


@pytest.mark.parametrize("failure", ["sync", "replace"])
def test_interrupted_save_preserves_last_acknowledged_queue(monkeypatch, failure):
    old = deque([("tg.output.test", b"acknowledged", {"operation_key": "old"})])
    store.save_outbox_items("telegram", old)
    path = store.outbox_store_path("telegram")
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("injected disk/rename failure")

    monkeypatch.setattr(storage.os, "fsync", fail) if failure == "sync" else monkeypatch.setattr(storage, "replace_with_retry", fail)
    with pytest.raises(OSError):
        store.save_outbox_items("telegram", deque([("tg.output.test", b"new", None)]))
    assert path.read_bytes() == before
    assert store.load_outbox_items("telegram") == old
    assert list(path.parent.iterdir()) == [path]


def test_long_unicode_canonical_message_outbox_restart_and_duplicate_ingress(monkeypatch):
    text = ("Сохрани фактуру кожи, ёлку и фон. 👩🏽‍💻🧪 — этап завершён.\n" * 1800)
    payload = {"id": "unicode.message", "text": text, "from": "hub"}
    conversation_store.upsert_conversation(conversation_id="unicode.conversation", webspace_id="desktop", owner="core:test")
    first = conversation_store.append_message(conversation_id="unicode.conversation", webspace_id="desktop",
        channel_id="general", owner="core:test", role="hub", text=text, payload=payload,
        idempotency_key="unicode.message")
    duplicate = conversation_store.append_message(conversation_id="unicode.conversation", webspace_id="desktop",
        channel_id="general", owner="core:test", role="hub", text=text, payload=payload,
        idempotency_key="unicode.message")
    assert first["id"] == duplicate["id"]
    restored = conversation_store.get_message(first["id"])
    assert restored["text"] == text
    encoded = json.dumps(restored, ensure_ascii=False).encode("utf-8")
    queue = deque([("tg.output.test.chat.1", encoded, {"operation_key": "unicode.message", "label": "Готово ✅"})])
    assert store.save_outbox_items("telegram", queue) == 1
    # New module instance and DB connections model transport reconnect/restart.
    state_dir = store._base_state_dir()
    importlib.reload(store)
    monkeypatch.setattr(store, "_base_state_dir", lambda: state_dir)
    assert store.load_outbox_items("telegram") == queue
    decoded = json.loads(store.load_outbox_items("telegram")[0][1])
    assert decoded["text"] == text
    claim = conversation_store.claim_transport_ingress(idempotency_key="unicode.delivery", transport="test",
        event_id="unicode.message", payload=decoded)
    again = conversation_store.claim_transport_ingress(idempotency_key="unicode.delivery", transport="test",
        event_id="unicode.message", payload=decoded)
    assert claim["claimed"] and again["duplicate"] and not again["claimed"]
    assert len(conversation_store.list_messages("unicode.conversation")) == 1
