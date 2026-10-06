from __future__ import annotations

from types import SimpleNamespace

from adaos.domain import Event
from adaos.services.eventbus import LocalEventBus
from adaos.services import system_hardware_stream as stream


def test_hardware_projection_uses_readable_units_and_all_three_metrics() -> None:
    result = stream._project(  # noqa: SLF001 - focused projection contract test
        {
            "observed_at": "2026-10-03T12:00:00+00:00",
            "resources": {
                "available": True,
                "freshness": "live_sample",
                "cpu": {"percent": 37.5},
                "memory": {
                    "percent": 50,
                    "used_bytes": 8_200_000_000,
                    "total_bytes": 16_400_000_000,
                },
                "disk": {
                    "percent": 90,
                    "used_bytes": 225_000_000_000,
                    "total_bytes": 250_000_000_000,
                },
            },
        },
        node_id="hub:test",
    )

    assert result["center"] == {"value": "37.5%", "label": "CPU"}
    assert [item["id"] for item in result["hardware_metrics"]] == ["cpu", "memory", "disk"]
    assert result["hardware_metrics"][1]["display"] == "50% · 8.2 GB / 16.4 GB"
    assert result["hardware_metrics"][2]["display"] == "90% · 225.0 GB / 250.0 GB"


def test_snapshot_request_publishes_only_for_local_selected_node(monkeypatch) -> None:
    bus = LocalEventBus()
    seen = []
    bus.subscribe("io.out.stream.publish", seen.append)
    ctx = SimpleNamespace(bus=bus, config=SimpleNamespace(node_id="local-node"))
    monkeypatch.setattr(stream, "get_ctx", lambda: ctx)
    monkeypatch.setattr(
        "adaos.sdk.system.get_operational_snapshot",
        lambda **_kwargs: {
            "observed_at": "now",
            "resources": {
                "available": True,
                "freshness": "live_sample",
                "cpu": {"percent": 10},
                "memory": {"percent": 20},
                "disk": {"percent": 30},
            },
        },
    )

    stream.on_snapshot_requested(
        Event(
            type="webio.stream.snapshot.requested",
            source="test",
            ts=1.0,
            payload={
                "receiver": stream.RECEIVER,
                "webspace_id": "desktop",
                "target_node_id": "local-node",
            },
        )
    )
    stream.on_snapshot_requested(
        Event(
            type="webio.stream.snapshot.requested",
            source="test",
            ts=1.0,
            payload={
                "receiver": stream.RECEIVER,
                "webspace_id": "desktop",
                "target_node_id": "remote-node",
            },
        )
    )

    assert len(seen) == 1
    assert seen[0].payload["receiver"] == stream.RECEIVER
    assert seen[0].payload["data"]["hardware_metrics"][0]["value"] == 10.0
    assert seen[0].payload["_meta"]["target_node_id"] == "local-node"
    assert seen[0].payload["_meta"]["params"] == {"target_node_id": "local-node"}
