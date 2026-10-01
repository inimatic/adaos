from __future__ import annotations

from types import SimpleNamespace

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


def test_operational_snapshot_exposes_bounded_management_sections(monkeypatch) -> None:
    from adaos.sdk import applications, system

    monkeypatch.setattr(system.control_plane, "get_self_object", lambda: _object("node:hub", "node"))
    monkeypatch.setattr(system.control_plane, "get_local_capacity_object", lambda: _object("capacity:hub", "capacity"))
    monkeypatch.setattr(
        system.control_plane,
        "list_device_objects",
        lambda: [{"id": "node:hub", "status": "online"}, {"id": "node:worker", "status": "offline"}],
    )
    monkeypatch.setattr(
        applications,
        "list_applications",
        lambda **_: [{
            "application": {"application_id": "notebook", "display": {"title": "Notebook"}},
            "installed": True,
            "effective_version": "1.2.3",
            "update_state": "available",
            "has_update": True,
        }],
    )
    monkeypatch.setattr(system, "_resource_snapshot", lambda: {"available": True, "cpu": {"percent": 12.5}})

    result = system.get_operational_snapshot(
        sections={"summary", "members", "applications", "resources"},
        webspace_id="desktop",
        limit=20,
    )

    assert result["member_summary"] == {"online": 1, "total": 2}
    assert result["applications"][0]["id"] == "notebook"
    assert result["application_updates"] == {"available": 1, "total": 1}
    assert result["resources"]["cpu"]["percent"] == 12.5
    assert result["provenance"] == {"authority": "local_node", "projection": "read_only"}


def test_rename_local_subnet_preserves_identity(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import subnet_alias

    observed = {}
    monkeypatch.setattr(
        system.control_plane,
        "get_self_object",
        lambda: {"id": "node:hub", "identity": {"subnet_id": "sn_test"}},
    )
    monkeypatch.setattr(
        subnet_alias,
        "save_subnet_alias",
        lambda alias, *, subnet_id=None: observed.update(alias=alias, subnet_id=subnet_id) or alias,
    )

    result = system.rename_local_subnet("  Product   Lab  ")

    assert observed == {"alias": "Product Lab", "subnet_id": "sn_test"}
    assert result == {
        "ok": True,
        "target": "local_subnet",
        "subnet_id": "sn_test",
        "display_name": "Product Lab",
        "current": "Product Lab",
        "desired": "Product Lab",
        "applied": True,
    }


def test_rename_current_node_uses_durable_node_configuration(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import node_config

    monkeypatch.setattr(
        node_config,
        "set_node_names",
        lambda names: SimpleNamespace(
            node_id="node_test",
            node_settings=SimpleNamespace(node_names=list(names)),
        ),
    )

    result = system.rename_current_node("Hub node")

    assert result["node_id"] == "node_test"
    assert result["current"] == "Hub node"
    assert result["desired"] == "Hub node"
    assert result["applied"] is True
