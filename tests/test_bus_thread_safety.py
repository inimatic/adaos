from __future__ import annotations

import asyncio
import json

from adaos.sdk.data import bus
from adaos.services import incident_registry


class _YLikeMap:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def to_json(self) -> str:
        return json.dumps(self.payload)


class _FaultyYLikeMap:
    def to_json(self) -> str:
        raise RuntimeError("y_py::y_map::YMap is unsendbale, but is dropped on another thread!")


async def _run_threaded_plain_payload_flow(seen: dict) -> None:
    def handler(payload: dict):
        seen["payload"] = payload
        seen["is_plain_dict"] = isinstance(payload.get("node"), dict)

    await bus.on("unit.threaded", handler)
    await bus.emit("unit.threaded", {"node": _YLikeMap({"answer": 42})}, source="testcase")
    await asyncio.sleep(0.1)


def test_bus_threaded_sync_handler_receives_plain_payload(monkeypatch):
    monkeypatch.setenv("ADAOS_SYNC_SUBSCRIPTION_THREAD_TOPICS", "unit.threaded")
    seen = {}

    asyncio.run(_run_threaded_plain_payload_flow(seen))

    assert seen["is_plain_dict"] is True
    assert seen["payload"]["node"]["answer"] == 42


def test_thread_safe_plain_records_yjs_thread_affinity_fault():
    incident_registry.reset_incident_registry()

    try:
        payload = bus._thread_safe_plain({"node": _FaultyYLikeMap()})
        snapshot = incident_registry.incident_registry_snapshot()

        assert isinstance(payload["node"], str)
        assert snapshot["items"][0]["class"] == "yjs_thread_affinity_fault"
        assert snapshot["items"][0]["severity"] == "degraded"
    finally:
        incident_registry.reset_incident_registry()
