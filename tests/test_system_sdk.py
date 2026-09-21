from __future__ import annotations

import pytest


def _object(object_id: str, kind: str, *, incidents=None) -> dict:
    return {
        "id": object_id,
        "kind": kind,
        "title": object_id,
        "status": "online",
        "incidents": list(incidents or []),
    }


def test_operational_snapshot_summary_does_not_load_heavy_sections(monkeypatch) -> None:
    from adaos.sdk import system

    monkeypatch.setattr(system.control_plane, "get_self_object", lambda: _object("node:hub", "node"))
    monkeypatch.setattr(
        system.control_plane,
        "get_local_capacity_object",
        lambda: _object("capacity:hub", "capacity"),
    )
    monkeypatch.setattr(
        system.control_plane,
        "get_reliability_projection",
        lambda **_: pytest.fail("summary must not load reliability"),
    )
    monkeypatch.setattr(
        system.control_plane,
        "list_runtime_objects",
        lambda **_: pytest.fail("summary must not load runtimes"),
    )

    result = system.get_operational_snapshot(sections="summary", webspace_id="desktop")

    assert result["schema"] == "adaos.sdk.system.operational_snapshot.v1"
    assert result["subject"]["id"] == "node:hub"
    assert result["capacity"]["id"] == "capacity:hub"
    assert "services" not in result
    assert "incidents" not in result


def test_operational_snapshot_composes_requested_sections(monkeypatch) -> None:
    from adaos.sdk import system

    monkeypatch.setattr(system.control_plane, "get_self_object", lambda: _object("node:hub", "node"))
    monkeypatch.setattr(
        system.control_plane,
        "get_local_capacity_object",
        lambda: _object("capacity:hub", "capacity"),
    )
    system._RELIABILITY_CACHE.clear()
    monkeypatch.setattr(
        system.control_plane,
        "get_reliability_projection",
        lambda **_: {
            "id": "projection:reliability",
            "kind": "reliability",
            "title": "Reliability",
            "subject": _object(
                "node:hub",
                "node",
                incidents=[{"id": "incident:route", "severity": "warning"}],
            ),
            "objects": [
                _object(
                    "runtime:yjs",
                    "runtime",
                    incidents=[{"id": "incident:sync", "severity": "warning"}],
                ),
                _object("connection:root", "connection"),
                _object("quota:relay", "quota"),
            ],
            "incidents": [{"id": "incident:route", "severity": "warning"}],
            "context": {"state": "limited"},
        },
    )
    monkeypatch.setattr(system, "current_update_status", lambda: {"state": "idle"})

    result = system.get_operational_snapshot(sections="all", webspace_id="desktop")

    assert result["services"][0]["id"] == "runtime:yjs"
    assert result["connections"][0]["id"] == "connection:root"
    assert result["quotas"][0]["id"] == "quota:relay"
    assert [item["id"] for item in result["incidents"]] == [
        "incident:route",
        "incident:sync",
    ]
    assert result["update"]["state"] == "idle"
    assert result["counts"] == {
        "services": 1,
        "connections": 1,
        "quotas": 1,
        "incidents": 2,
    }


def test_operational_snapshot_rejects_unknown_sections(monkeypatch) -> None:
    from adaos.sdk import system

    with pytest.raises(ValueError, match="unsupported system sections"):
        system.get_operational_snapshot(sections=["summary", "infrastate"])


def test_operational_snapshot_reuses_reliability_across_sections(monkeypatch) -> None:
    from adaos.sdk import system

    calls = 0

    def _reliability(**_):
        nonlocal calls
        calls += 1
        return {
            "objects": [
                _object("runtime:yjs", "runtime"),
                _object("connection:root", "connection"),
            ]
        }

    system._RELIABILITY_CACHE.clear()
    monkeypatch.setattr(system.control_plane, "get_self_object", lambda: _object("node:hub", "node"))
    monkeypatch.setattr(
        system.control_plane,
        "get_local_capacity_object",
        lambda: _object("capacity:hub", "capacity"),
    )
    monkeypatch.setattr(system.control_plane, "get_reliability_projection", _reliability)

    services = system.get_operational_snapshot(sections={"summary", "services"}, webspace_id="desktop")
    connections = system.get_operational_snapshot(
        sections={"summary", "connections"},
        webspace_id="desktop",
    )

    assert services["services"][0]["id"] == "runtime:yjs"
    assert connections["connections"][0]["id"] == "connection:root"
    assert calls == 1
