from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest


def test_operational_snapshot_uses_node_authority_from_trial(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services.agent_context import get_ctx, use_ctx

    owner = get_ctx()
    observed_contexts = []

    def _subject():
        observed_contexts.append(get_ctx())
        return _object("node:authority", "node")

    monkeypatch.setattr(system.control_plane, "get_self_object", _subject)
    monkeypatch.setattr(
        system.control_plane,
        "get_local_capacity_object",
        lambda: _object("capacity:authority", "capacity"),
    )
    trial = replace(owner, authority_context=owner)

    with use_ctx(trial):
        result = system.get_operational_snapshot(sections="summary")

    assert result["subject"]["id"] == "node:authority"
    assert observed_contexts == [owner]


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
    assert result["subnet"] == {
        "available": False,
        "subnet_id": None,
        "display_name": None,
        "source": "local_node_identity",
        "freshness": "unavailable",
    }
    assert result["service_summary"]["subscription"] == {
        "available": False,
        "reason": "provider_not_admitted",
    }
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
        lambda **_: [
            {
                "id": "device:browser-1",
                "status": "online",
                "relations": {"connected_to": ["browser:browser-1"]},
            },
            {
                "id": "device:member:local-hub",
                "title": "Local host duplicate",
                "status": "online",
                "relations": {"connected_to": ["member:hub"]},
            },
            {
                "id": "device:member:worker",
                "title": "Worker",
                "status": "offline",
                "relations": {"connected_to": ["member:worker"]},
            },
        ],
    )
    monkeypatch.setattr(
        applications,
        "list_system_application_summaries",
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

    assert result["member_summary"] == {
        "available": True,
        "online": 1,
        "total": 2,
        "source": "device_inventory",
        "freshness": "current",
    }
    assert [item["id"] for item in result["members"]] == [
        "node:hub",
        "member:worker",
    ]
    assert result["applications"][0]["id"] == "notebook"
    assert result["application_updates"] == {"available": 1, "total": 1}
    assert result["resources"]["cpu"]["percent"] == 12.5
    assert result["provenance"] == {"authority": "local_node", "projection": "read_only"}


def test_operational_snapshot_exposes_bounded_progressive_system_sections(monkeypatch) -> None:
    from adaos.sdk import applications, system
    from adaos.sdk.data import root_mcp

    monkeypatch.setattr(
        system.control_plane,
        "get_self_object",
        lambda: {
            **_object("node:hub", "node"),
            "relations": {"subnet": ["subnet:sn_demo"]},
            "versioning": {"current": "1.5.0"},
        },
    )
    monkeypatch.setattr(
        system.control_plane,
        "get_local_capacity_object",
        lambda: {**_object("capacity:hub", "capacity"), "resources": {"active_skill_total": 2}},
    )
    monkeypatch.setattr(
        system.control_plane,
        "list_skill_objects",
        lambda: [
            {"id": "skill:notebook", "title": "Notebook", "status": "active"},
            {"id": "skill:weather", "title": "Weather", "status": "active"},
        ],
    )
    system._RELIABILITY_CACHE.clear()
    monkeypatch.setattr(
        system.control_plane,
        "get_reliability_projection",
        lambda **_: {
            "status": "ready",
            "context": {"state": "ready"},
            "objects": [_object("connection:root", "connection")],
        },
    )
    monkeypatch.setattr(system, "current_update_status", lambda: {"state": "idle", "phase": "ready"})
    monkeypatch.setattr(
        applications,
        "list_development_reports",
        lambda: [
            {"report_id": "report.1", "status": "accepted", "updated_at": "2026-10-01T10:00:00Z"},
            {"report_id": "report.2", "status": "queued", "updated_at": "2026-10-01T11:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        root_mcp,
        "get_local_activity_log",
        lambda **_: {
            "ok": True,
            "response": {
                "result": {
                    "events": [
                        {
                            "event_id": "evt.1",
                            "tool_id": "hub.update.reconcile",
                            "status": "ok",
                            "finished_at": "2026-10-01T12:00:00Z",
                        },
                        {"event_id": "evt.2", "tool_id": "root.tokens.list", "status": "ok"},
                    ]
                }
            },
        },
    )

    result = system.get_operational_snapshot(
        sections={"skills", "development", "activity", "technical"},
        webspace_id="desktop",
        limit=20,
    )

    assert result["skill_summary"] == {
        "available": True,
        "total": 2,
        "returned": 2,
        "truncated": False,
        "source": "control_plane",
        "freshness": "current",
    }
    assert [item["id"] for item in result["skills"]] == ["skill:notebook", "skill:weather"]
    assert result["development_delivery"]["delivered"] == 1
    assert result["development_delivery"]["accepted"] == 1
    assert result["development_delivery"]["pending"] == 1
    assert [item["id"] for item in result["activity"]["items"]] == ["evt.1"]
    assert result["technical"]["identifiers"] == {
        "node_id": "node:hub",
        "subnet_id": "sn_demo",
        "webspace_id": "desktop",
    }
    assert result["technical"]["connectivity"] == {
        "status": "ready",
        "observed": 1,
        "ready": 1,
    }
    assert result["technical"]["update"] == {"state": "idle", "phase": "ready"}


def test_installed_application_projection_singleflights_repeated_system_cards(monkeypatch) -> None:
    from adaos.sdk import applications, system

    calls = 0

    def list_system_application_summaries(**_kwargs):
        nonlocal calls
        calls += 1
        return [{
            "application": {
                "application_id": "notebook",
                "display": {"title": "Notebook"},
            },
            "installed": True,
        }]

    monkeypatch.setattr(
        applications,
        "list_system_application_summaries",
        list_system_application_summaries,
    )
    first = system._installed_application_summaries(webspace_id="desktop", limit=40)
    first[0]["title"] = "mutated by consumer"
    second = system._installed_application_summaries(webspace_id="desktop", limit=40)

    assert calls == 1
    assert second[0]["title"] == "Notebook"


def test_development_delivery_projection_is_single_flight_cached(monkeypatch) -> None:
    from adaos.sdk import applications, system

    calls = 0

    def list_development_reports():
        nonlocal calls
        calls += 1
        return [{"status": "accepted", "updated_at": "2026-10-04T10:00:00Z"}]

    monkeypatch.setattr(applications, "list_development_reports", list_development_reports)
    system._DEVELOPMENT_DELIVERY_CACHE = None

    first = system._development_delivery_projection()
    first["accepted"] = 99
    second = system._development_delivery_projection()

    assert calls == 1
    assert second["accepted"] == 1


def test_progressive_system_sections_fail_closed_with_explicit_unavailable(monkeypatch) -> None:
    from adaos.sdk import applications, system
    from adaos.sdk.data import root_mcp

    monkeypatch.setattr(
        system.control_plane,
        "list_skill_objects",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        applications,
        "list_development_reports",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        root_mcp,
        "get_local_activity_log",
        lambda **_: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    skills, summary = system._installed_skill_projection(limit=10)
    delivery = system._development_delivery_projection()
    activity = system._root_activity_events(limit=10)

    assert skills == []
    assert summary["available"] is False
    assert summary["freshness"] == "unavailable"
    assert delivery["available"] is False
    assert delivery["freshness"] == "unavailable"
    assert activity["available"] is False
    assert activity["items"] == []


def test_operational_snapshot_exposes_typed_subnet_identity(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import subnet_alias

    monkeypatch.setattr(
        system.control_plane,
        "get_self_object",
        lambda: {
            **_object("hub:node-1", "hub"),
            "relations": {"subnet": ["subnet:sn_demo"]},
        },
    )
    monkeypatch.setattr(
        system.control_plane,
        "get_local_capacity_object",
        lambda: _object("capacity:node-1", "capacity"),
    )
    monkeypatch.setattr(
        subnet_alias,
        "load_subnet_alias",
        lambda *, subnet_id=None: "Product Lab" if subnet_id == "sn_demo" else None,
    )

    result = system.get_operational_snapshot(sections="summary")

    assert result["subnet"] == {
        "available": True,
        "subnet_id": "sn_demo",
        "display_name": "Product Lab",
        "source": "local_node_identity",
        "freshness": "current",
    }


def test_rename_local_subnet_preserves_identity(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import agent_context, eventbus, subnet_alias

    observed = {}
    published = {}
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
    bus = object()
    monkeypatch.setattr(agent_context, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr(
        eventbus,
        "emit",
        lambda target_bus, topic, payload, source: published.update(
            bus=target_bus, topic=topic, payload=payload, source=source,
        ),
    )

    result = system.rename_local_subnet("  Product   Lab  ")

    assert observed == {"alias": "Product Lab", "subnet_id": "sn_test"}
    assert published == {
        "bus": bus,
        "topic": "subnet.alias.changed",
        "payload": {"alias": "Product Lab", "subnet_id": "sn_test"},
        "source": "sdk.system",
    }
    assert result == {
        "ok": True,
        "target": "local_subnet",
        "subnet_id": "sn_test",
        "display_name": "Product Lab",
        "current": "Product Lab",
        "desired": "Product Lab",
        "applied": True,
    }


def test_rename_local_subnet_resolves_identity_from_subject_relation(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import subnet_alias

    observed = {}
    monkeypatch.setattr(
        system.control_plane,
        "get_self_object",
        lambda: {
            "id": "hub:8db40740-b3ff-44bf-baf5-9fb013b35b01",
            "relations": {"subnet": ["subnet:sn_6acf0c01"]},
        },
    )
    monkeypatch.setattr(
        subnet_alias,
        "save_subnet_alias",
        lambda alias, *, subnet_id=None: observed.update(
            alias=alias,
            subnet_id=subnet_id,
        )
        or alias,
    )

    result = system.rename_local_subnet("Home assistant")

    assert observed == {"alias": "Home assistant", "subnet_id": "sn_6acf0c01"}
    assert result["subnet_id"] == "sn_6acf0c01"


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


def test_operational_snapshot_exposes_core_autoupdate_control(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import operator_controls

    monkeypatch.setattr(system.control_plane, "get_self_object", lambda: _object("node:hub", "node"))
    monkeypatch.setattr(system.control_plane, "get_local_capacity_object", lambda: _object("capacity:hub", "capacity"))
    monkeypatch.setattr(system, "current_update_status", lambda: {"state": "idle"})
    monkeypatch.setattr(operator_controls, "read_controls", lambda: {"core_auto_update": False})

    result = system.get_operational_snapshot(sections={"summary", "update"})

    assert result["update"] == {
        "state": "idle",
        "runtime_channel": "dev",
        "runtime_version": system._runtime_release_metadata()["runtime_version"],
        "development": True,
    }
    assert result["update_controls"] == {
        "core_autoupdate": False,
        "source": "operator_controls",
        "mutable": True,
    }


def test_operational_snapshot_keeps_update_projection_bounded(monkeypatch) -> None:
    from adaos.sdk import system

    monkeypatch.setattr(system.control_plane, "get_self_object", lambda: _object("node:hub", "node"))
    monkeypatch.setattr(system.control_plane, "get_local_capacity_object", lambda: _object("capacity:hub", "capacity"))
    monkeypatch.setattr(
        system,
        "current_update_status",
        lambda: {
            "state": "succeeded",
            "message": "runtime validated",
            "target_version": "0.1.1132",
            "manifest": {"history": "x" * 300_000},
            "self_hygiene": {"removed": ["irrelevant"] * 1000},
        },
    )

    result = system.get_operational_snapshot(sections={"summary", "update"})

    assert result["update"]["state"] == "succeeded"
    assert result["update"]["target_version"] == "0.1.1132"
    assert "manifest" not in result["update"]
    assert "self_hygiene" not in result["update"]


def test_set_core_autoupdate_requires_write_and_reports_transition(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import operator_controls

    required = []
    monkeypatch.setattr(system.access, "require", required.append)
    monkeypatch.setattr(operator_controls, "read_controls", lambda: {"core_auto_update": True})
    monkeypatch.setattr(
        operator_controls,
        "update_controls",
        lambda patch: {"core_auto_update": patch["core_auto_update"]},
    )

    result = system.set_core_autoupdate("management:autoupdate:off", False)

    assert required == ["workspace.write"]
    assert result == {
        "ok": True,
        "request_id": "management:autoupdate:off",
        "target": "core_autoupdate",
        "accepted": True,
        "current": False,
        "previous": True,
        "desired": False,
        "applied": True,
    }


def test_request_core_update_uses_governed_reconcile_endpoint(monkeypatch) -> None:
    from adaos.sdk import system

    required = []
    requests = []
    monkeypatch.setattr(system.access, "require", required.append)
    monkeypatch.setattr(
        system,
        "_post_local_admin",
        lambda path, payload: requests.append((path, payload)) or {
            "ok": True,
            "accepted": True,
            "reason": payload["reason"],
            "status": {"state": "scheduled"},
            "result": {"needs_update": True},
        },
    )

    result = system.request_core_update("management:update:1", countdown_sec=3)

    assert required == ["workspace.write"]
    assert requests == [(
        "/api/admin/update/reconcile",
        {"reason": "sdk.system.core_update:management:update:1", "countdown_sec": 5.0},
    )]
    assert result["accepted"] is True
    assert result["current"] == "scheduled"
    assert result["desired"] == "root_governed_release"
    assert result["applied"] is True


def test_request_core_update_dry_run_does_not_mutate(monkeypatch) -> None:
    from adaos.sdk import system

    monkeypatch.setattr(system.access, "require", lambda capability: None)
    monkeypatch.setattr(system, "current_update_status", lambda: {"state": "idle"})
    monkeypatch.setattr(
        system,
        "_post_local_admin",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not mutate"),
    )

    result = system.request_core_update("management:update:dry", dry_run=True)

    assert result["accepted"] is False
    assert result["dry_run"] is True
    assert result["current"] == "idle"
    assert result["applied"] is False


def test_runtime_controls_public_read_redacts_supervisor_internals(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import operator_controls
    from adaos.services.nlu import rasa_skill_installer
    from adaos.services.skill import service_supervisor

    required = []
    discovered = []
    monkeypatch.setattr(system.access, "require", required.append)
    monkeypatch.setattr(
        operator_controls,
        "read_controls",
        lambda: {
            "core_auto_update": True,
            "application_auto_update_default": False,
            "log_level": "warning",
            "rasa_enabled": True,
        },
    )
    monkeypatch.setattr(rasa_skill_installer, "is_rasa_nlu_enabled", lambda: True)
    monkeypatch.setattr(
        service_supervisor,
        "get_service_supervisor",
        lambda: SimpleNamespace(
            ensure_discovered=lambda **kwargs: discovered.append(kwargs),
            status=lambda *_args, **_kwargs: {
                "running": True,
                "health_ok": True,
                "env_mode": "core",
                "private_path": "must-not-leak",
            },
        ),
    )

    result = system.get_runtime_controls()

    assert required == ["workspace.read"]
    assert discovered == [{"force": True}]
    assert result["schema"] == "adaos.sdk.system.runtime_controls.v1"
    assert result["controls"] == {
        "core_auto_update": True,
        "application_auto_update_default": False,
        "log_level": "WARNING",
        "rasa_enabled": True,
    }
    assert result["rasa"] == {
        "availability": "ready",
        "configured": True,
        "installed": True,
        "running": True,
        "health": True,
        "environment": "core",
        "version_profile": "lightweight",
        "diet_profile": "deferred",
    }
    assert "private_path" not in str(result)


def test_set_runtime_control_updates_log_level_through_public_contract(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import operator_controls

    required = []
    state = {
        "core_auto_update": True,
        "application_auto_update_default": True,
        "log_level": "INFO",
        "rasa_enabled": True,
    }
    monkeypatch.setattr(system.access, "require", required.append)
    monkeypatch.setattr(operator_controls, "read_controls", lambda: dict(state))

    def _update(patch):
        state.update(patch)
        return dict(state)

    monkeypatch.setattr(operator_controls, "update_controls", _update)
    monkeypatch.setattr(
        system,
        "_runtime_controls_snapshot",
        lambda: {
            "ok": True,
            "schema": "adaos.sdk.system.runtime_controls.v1",
            "controls": dict(state),
            "rasa": {"installed": True},
        },
    )

    result = asyncio.run(
        system.set_runtime_control("management:log-level:debug", "log_level", "debug")
    )

    assert required == ["workspace.write"]
    assert result["request_id"] == "management:log-level:debug"
    assert result["target"] == "log_level"
    assert result["previous"] == "INFO"
    assert result["current"] == "DEBUG"
    assert result["desired"] == "DEBUG"
    assert result["applied"] is True


def test_set_runtime_control_updates_core_autoupdate_with_runtime_schema(monkeypatch) -> None:
    from adaos.sdk import system
    from adaos.services import operator_controls

    required = []
    state = {
        "core_auto_update": True,
        "application_auto_update_default": True,
        "log_level": "INFO",
        "rasa_enabled": True,
    }
    monkeypatch.setattr(system.access, "require", required.append)
    monkeypatch.setattr(operator_controls, "read_controls", lambda: dict(state))

    def _update(patch):
        state.update(patch)
        return dict(state)

    monkeypatch.setattr(operator_controls, "update_controls", _update)
    monkeypatch.setattr(
        system,
        "_runtime_controls_snapshot",
        lambda: {
            "ok": True,
            "schema": "adaos.sdk.system.runtime_controls.v1",
            "controls": dict(state),
            "rasa": {"installed": True},
        },
    )

    result = asyncio.run(
        system.set_runtime_control(
            "management:core-autoupdate:off", "core_auto_update", False
        )
    )

    assert required == ["workspace.write"]
    assert result["schema"] == "adaos.sdk.system.runtime_controls.v1"
    assert result["target"] == "core_auto_update"
    assert result["previous"] is True
    assert result["current"] is False
    assert result["desired"] is False
    assert result["applied"] is True
