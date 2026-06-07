from __future__ import annotations

from adaos.sdk.data import devices as sdk_devices
from adaos.services import access_links
from adaos.services import device_inventory as device_inventory
from adaos.services.redevice_versions import endpoint_version_info


class _FakeDirectory:
    def __init__(self, items: list[dict[str, object]] | None = None) -> None:
        self._items = list(items or [])

    def list_known_nodes(self) -> list[dict[str, object]]:
        return [dict(item) for item in self._items]


def _patch_sources(
    monkeypatch,
    *,
    browser_entries: list[dict[str, object]] | None = None,
    member_entries: list[dict[str, object]] | None = None,
    redevice_entries: list[dict[str, object]] | None = None,
    directory_nodes: list[dict[str, object]] | None = None,
    live_members: list[dict[str, object]] | None = None,
    now: float = 1000.0,
) -> None:
    browser_items = list(browser_entries or [])
    member_items = list(member_entries or [])
    redevice_items = list(redevice_entries or [])

    def _list_links(kind=None):
        if kind == "browser":
            return [dict(item) for item in browser_items]
        if kind == "member":
            return [dict(item) for item in member_items]
        if kind == "redevice":
            return [dict(item) for item in redevice_items]
        return [dict(item) for item in browser_items + member_items + redevice_items]

    monkeypatch.setattr(device_inventory, "_now_ts", lambda: now)
    monkeypatch.setattr(device_inventory._access_links, "list_links", _list_links)
    monkeypatch.setattr(device_inventory, "_local_redevice_scope", lambda: ("", ""))
    monkeypatch.setattr(device_inventory, "get_directory", lambda: _FakeDirectory(directory_nodes))
    monkeypatch.setattr(
        device_inventory,
        "_hub_link_manager_snapshot",
        lambda: {"members": [dict(item) for item in list(live_members or [])]},
    )


def test_device_inventory_aggregates_browser_policy_record(monkeypatch) -> None:
    _patch_sources(
        monkeypatch,
        browser_entries=[
            {
                "id": "browser-1",
                "kind": "browser",
                "display_name": "Living room TV",
                "access_class": "device",
                "lifetime_mode": "permanent",
                "revoked": False,
                "last_seen_at": 995.0,
                "online": True,
                "connection_state": "connected",
                "last_webspace_id": "desktop",
                "browser_family": "Edge",
                "os_name": "Windows",
                "form_factor": "Desktop",
                "user_agent": "Mozilla/5.0 Edge",
            }
        ],
    )

    items = device_inventory.list_devices(kind="browser")

    assert [item["ref"] for item in items] == ["browser:browser-1"]
    item = items[0]
    assert item["policy"]["managed_state"] == "managed"
    assert item["policy"]["effective_name"] == "Living room TV"
    assert item["observation"] == {
        "online": True,
        "connection_state": "connected",
        "last_seen_at": 995.0,
        "source": "browser_session",
        "last_webspace_id": "desktop",
    }
    assert item["identity"]["browser_family"] == "Edge"
    assert item["identity"]["os_name"] == "Windows"
    assert item["identity"]["form_factor"] == "Desktop"
    assert item["runtime"]["connected_to_subnet"] is None


def test_device_inventory_merges_member_policy_directory_and_live_presence(monkeypatch) -> None:
    _patch_sources(
        monkeypatch,
        member_entries=[
            {
                "id": "member-1",
                "kind": "member",
                "display_name": "Kitchen tablet",
                "access_class": "device",
                "lifetime_mode": "permanent",
                "revoked": False,
                "online": False,
                "connection_state": "closed",
                "last_seen_at": 920.0,
                "hostname": "kitchen-host",
            }
        ],
        directory_nodes=[
            {
                "node_id": "member-1",
                "hostname": "kitchen-host",
                "base_url": "http://member-1.local",
                "online": False,
                "last_seen": 930.0,
                "runtime_projection": {
                    "captured_at": 940.0,
                    "ready": True,
                    "route_mode": "ws",
                    "connected_to_hub": False,
                    "snapshot": {
                        "captured_at": 940.0,
                        "ready": True,
                        "node_names": ["Kitchen runtime"],
                        "route_mode": "ws",
                        "connected_to_hub": False,
                        "build": {"runtime_version": "1.2.3"},
                    },
                },
            }
        ],
        live_members=[
            {
                "node_id": "member-1",
                "hostname": "kitchen-live",
                "node_names": ["Kitchen live"],
                "connected": True,
                "node_snapshot": {
                    "captured_at": 980.0,
                    "ready": True,
                    "node_names": ["Kitchen live"],
                    "route_mode": "ws",
                    "connected_to_hub": True,
                    "build": {"runtime_version": "9.9.9"},
                },
            }
        ],
        now=1000.0,
    )

    item = device_inventory.get_device("member:member-1")

    assert item is not None
    assert item["policy"]["managed_state"] == "managed"
    assert item["policy"]["effective_name"] == "Kitchen tablet"
    assert item["identity"]["node_names"] == ["Kitchen live"]
    assert item["identity"]["base_url"] == "http://member-1.local"
    assert item["observation"]["online"] is True
    assert item["observation"]["source"] == "member_link"
    assert item["observation"]["last_seen_at"] == 930.0
    assert item["runtime"]["connected_to_subnet"] is True
    assert item["runtime"]["route_mode"] == "ws"
    assert item["runtime"]["runtime_version"] == "9.9.9"
    assert item["runtime"]["snapshot_state"] == "fresh"


def test_device_inventory_includes_observed_only_member_without_policy(monkeypatch) -> None:
    _patch_sources(
        monkeypatch,
        directory_nodes=[
            {
                "node_id": "member-2",
                "hostname": "guest-node",
                "online": False,
                "last_seen": 880.0,
                "runtime_projection": {
                    "captured_at": 880.0,
                    "ready": False,
                    "route_mode": "relay",
                    "connected_to_hub": False,
                    "snapshot": {
                        "captured_at": 880.0,
                        "ready": False,
                        "node_names": ["Workshop display"],
                        "route_mode": "relay",
                        "connected_to_hub": False,
                    },
                },
            }
        ],
        now=1000.0,
    )

    item = device_inventory.get_device("member:member-2")

    assert item is not None
    assert item["policy"]["present"] is False
    assert item["policy"]["managed_state"] == "observed_only"
    assert item["policy"]["effective_name"] == "Workshop display"
    assert item["observation"]["source"] == "subnet_directory"
    assert item["runtime"]["connected_to_subnet"] is False
    assert item["runtime"]["snapshot_state"] == "stale"


def test_device_inventory_reads_connected_to_subnet_field_directly(monkeypatch) -> None:
    _patch_sources(
        monkeypatch,
        directory_nodes=[
            {
                "node_id": "member-3",
                "hostname": "display-node",
                "online": True,
                "last_seen": 990.0,
                "runtime_projection": {
                    "captured_at": 995.0,
                    "ready": True,
                    "route_mode": "p2p",
                    "connected_to_subnet": True,
                    "snapshot": {
                        "captured_at": 995.0,
                        "ready": True,
                        "node_names": ["Hall display"],
                        "route_mode": "p2p",
                        "connected_to_subnet": True,
                    },
                },
            }
        ],
        now=1000.0,
    )

    item = device_inventory.get_device("member:member-3")

    assert item is not None
    assert item["runtime"]["connected_to_subnet"] is True
    assert item["runtime"]["route_mode"] == "p2p"


def test_redevice_version_info_detects_agent_version_drift() -> None:
    info = endpoint_version_info(
        {
            "endpoint_manifest": {
                "schema_version": "endpoint-manifest.v1",
                "agent_version": "0.1.1",
                "agent_version_code": 2,
            },
            "endpoint_policy": {"redevice_agent": {"version": "0.1.2", "version_code": 3}},
        },
        use_default_served=False,
    )

    assert info["software_version"] == "0.1.1"
    assert info["software_version_code"] == "2"
    assert info["served_version"] == "0.1.2"
    assert info["served_version_code"] == "3"
    assert info["version_status"] == "drift"


def test_access_link_normalizer_preserves_redevice_version_payloads() -> None:
    entry = access_links._normalize_entry(
        "redevice",
        "endpoint-1",
        {
            "endpoint_manifest": {"agent_version": "0.1.1", "agent_version_code": 2},
            "endpoint_policy": {"redevice_agent": {"version": "0.1.1"}},
            "diagnostic_report": {"agent_version": "0.1.1"},
            "service_state": {"agent_version": "0.1.1"},
            "active_app": {"app_id": "demo"},
        },
    )

    assert entry["endpoint_manifest"] == {"agent_version": "0.1.1", "agent_version_code": 2}
    assert entry["endpoint_policy"] == {"redevice_agent": {"version": "0.1.1"}}
    assert entry["diagnostic_report"] == {"agent_version": "0.1.1"}
    assert entry["service_state"] == {"agent_version": "0.1.1"}
    assert entry["active_app"] == {"app_id": "demo"}


def test_device_inventory_surfaces_redevice_agent_versions(monkeypatch) -> None:
    _patch_sources(
        monkeypatch,
        redevice_entries=[
            {
                "id": "endpoint-1",
                "kind": "redevice",
                "display_name": "Kitchen ReDevice",
                "online": True,
                "connection_state": "connected",
                "last_seen_at": 995.0,
                "endpoint_manifest": {
                    "schema_version": "endpoint-manifest.v1",
                    "endpoint_id": "endpoint-1",
                    "agent_version": "0.1.1",
                    "agent_version_code": 2,
                },
                "endpoint_policy": {"redevice_agent": {"version": "0.1.2", "version_code": 3}},
            }
        ],
        now=1000.0,
    )

    item = device_inventory.get_device("redevice:endpoint-1")

    assert item is not None
    assert item["runtime"]["runtime_version"] == "0.1.1"
    assert item["runtime"]["software_version"] == "0.1.1"
    assert item["runtime"]["software_version_code"] == "2"
    assert item["runtime"]["served_version"] == "0.1.2"
    assert item["runtime"]["version_status"] == "drift"
    assert item["diagnostics"]["version_info"]["served_version_code"] == "3"


def test_sdk_devices_inspect_device_separates_diagnostics(monkeypatch) -> None:
    _patch_sources(
        monkeypatch,
        browser_entries=[
            {
                "id": "browser-9",
                "kind": "browser",
                "display_name": "",
                "access_class": "client",
                "lifetime_mode": "fixed",
                "expires_at": 1200.0,
                "revoked": False,
                "online": False,
                "connection_state": "closed",
                "last_seen_at": 910.0,
            }
        ],
        now=1000.0,
    )

    payload = sdk_devices.inspect_device("browser:browser-9")

    assert payload is not None
    assert payload["device"]["ref"] == "browser:browser-9"
    assert "diagnostics" not in payload["device"]
    assert payload["diagnostics"] == {
        "policy_source": "access_links",
        "runtime_sources": ["browser_session"],
        "aggregated_at": 1000.0,
    }
    assert payload["reconcile"] == {
        "device_ref": "browser:browser-9",
        "kind": "browser",
        "title": "browser-9",
        "state": "steady",
        "consistent": True,
        "issue_total": 0,
        "issues": [],
        "actions": {
            "adopt_device": {
                "enabled": False,
                "suggested_display_name": "",
                "preset": "permanent",
            },
            "sync_runtime_name": {"enabled": False},
            "detach_runtime": {"enabled": False},
        },
        "runtime": {
            "connected_to_subnet": None,
            "observation_source": "browser_session",
        },
    }
