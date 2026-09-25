from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from adaos.services import hub_root_protocol_store as store


def test_protocol_mutations_hold_cross_process_lock_for_read_modify_write(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lock_events: list[tuple[str, Path]] = []

    @contextmanager
    def fake_mutation_lock(path: Path, *, timeout_s: float):
        assert timeout_s == 10.0
        lock_events.append(("enter", Path(path)))
        yield
        lock_events.append(("exit", Path(path)))

    monkeypatch.setattr(store, "_base_state_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "mutation_lock", fake_mutation_lock)

    issued = store.prepare_stream_message(
        stream_id="control:test",
        flow_id="control",
        traffic_class="control",
        delivery_class="must_not_lose",
        message_type="state_report",
        payload={"generation": 1},
        ttl_ms=5_000,
        authority_epoch="epoch-1",
    )
    store.ack_stream_message(
        "control:test",
        message_id=issued["message_id"],
        cursor=issued["cursor"],
        result="accepted",
    )
    snapshot = store.protocol_streams_snapshot()

    expected_lock = tmp_path / "hub_root_protocol" / "streams.lock"
    assert lock_events == [
        ("enter", expected_lock),
        ("exit", expected_lock),
        ("enter", expected_lock),
        ("exit", expected_lock),
        ("enter", expected_lock),
        ("exit", expected_lock),
    ]
    assert snapshot["streams"]["control:test"]["last_ack_result"] == "accepted"
    persisted = json.loads((tmp_path / "hub_root_protocol" / "streams.json").read_text(encoding="utf-8"))
    assert persisted["streams"]["control:test"]["pending"] is None


def test_protocol_atomic_write_does_not_use_shared_temporary_name(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[Path, dict[str, object]]] = []

    def fake_atomic_write(path: Path, payload: dict[str, object]) -> None:
        calls.append((Path(path), payload))

    monkeypatch.setattr(store, "atomic_write_json", fake_atomic_write)
    target = tmp_path / "streams.json"
    payload = {"streams": {}}

    store._write_json(target, payload)

    assert calls == [(target, payload)]
    assert not target.with_suffix(".json.tmp").exists()
