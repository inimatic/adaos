from __future__ import annotations

from adaos.services import device_access


def test_command_profile_for_managed_member_enables_device_and_node_actions(monkeypatch) -> None:
    device = {
        "ref": "member:member-1",
        "kind": "member",
        "identity": {"node_id": "member-1"},
        "policy": {
            "present": True,
            "managed_state": "managed",
            "revoked": False,
        },
    }

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-1"),
    )

    profile = device_access.get_command_profile("member:member-1")

    assert profile is not None
    assert profile["rename"]["enabled"] is True
    assert profile["set_lifetime"]["enabled"] is True
    assert profile["set_lifetime"]["presets"] == ["permanent", "1h", "1d", "7d", "30d"]
    assert profile["detach"]["enabled"] is True
    assert profile["deny"]["enabled"] is True
    assert profile["open_apps"] == {"enabled": True, "node_id": "member-1"}
    assert profile["open_marketplace"] == {"enabled": True, "node_id": "member-1"}


def test_sdk_device_access_resolves_redevice_by_alias_and_assignment(monkeypatch) -> None:
    from adaos.sdk.data import device_access as sdk_device_access

    devices = [
        {
            "ref": "redevice:endpoint-1",
            "kind": "redevice",
            "identity": {"endpoint_id": "endpoint-1", "pair_code": "ABCD1234"},
            "policy": {
                "effective_name": "Kitchen tablet",
                "display_name": "Kitchen tablet",
                "labels": [{"text": "Планшет", "role": "alias", "locale": "ru"}],
            },
            "observation": {"online": True},
            "runtime": {
                "assignment": "slideshow",
                "endpoint_assignment": {"role": "slideshow", "owner": {"skill_id": "slideshow_skill"}},
                "active_app": {"app_id": "slideshow_skill"},
            },
        }
    ]

    monkeypatch.setattr(sdk_device_access, "list_endpoint_devices", lambda kind="redevice", sync_registry=True: devices)

    resolved = sdk_device_access.resolve_endpoint_device(query="Планшет", assignment="slideshow")

    assert resolved["ok"] is True
    assert resolved["device_ref"] == "redevice:endpoint-1"
    assert resolved["code"] == "ABCD1234"
    assert resolved["resolution"]["schema_version"] == "endpoint-resolution.v1"
    assert resolved["resolution"]["assignment"] == "slideshow"
    assert resolved["resolution"]["active_app"] == {"app_id": "slideshow_skill"}
    assert resolved["resolution"]["online"] is True


def test_sdk_device_access_prefers_live_root_redevice_snapshot(monkeypatch) -> None:
    from adaos.sdk import redevice as sdk_redevice
    from adaos.sdk.data import device_access as sdk_device_access
    from adaos.sdk.data import devices as sdk_devices

    monkeypatch.setattr(
        sdk_redevice,
        "list_endpoints",
        lambda sync_registry=True: [
            {
                "code": "SNX68P2A",
                "endpoint_id": "endpoint-1",
                "device_label": "Kitchen tablet",
                "state": "consumed",
                "last_seen_at": 1_900_000_000,
                "hub_id": "sn_92ffc943",
                "owner_id": "sn_92ffc943",
                "endpoint_policy": {"trust_level": "limited"},
            }
        ],
    )
    monkeypatch.setattr(
        sdk_devices,
        "list_devices",
        lambda kind=None: [
            {
                "ref": "redevice:endpoint-1",
                "kind": "redevice",
                "identity": {"endpoint_id": "endpoint-1", "pair_code": "FMRS7WTB"},
                "policy": {"effective_name": "Kitchen tablet"},
                "observation": {"online": False, "connection_state": "offline", "last_seen_at": 1},
            }
        ],
    )

    devices = sdk_device_access.list_endpoint_devices("redevice", sync_registry=True)

    assert len(devices) == 1
    assert devices[0]["code"] == "SNX68P2A"
    assert devices[0]["online_state"] in {"online", "stale"}


def test_sdk_device_access_resolves_old_pair_code_from_admission_history(monkeypatch) -> None:
    from adaos.sdk import redevice as sdk_redevice
    from adaos.sdk.data import device_access as sdk_device_access

    monkeypatch.setattr(
        sdk_redevice,
        "list_endpoints",
        lambda sync_registry=True: [
            {
                "code": "SNX68P2A",
                "endpoint_id": "endpoint-1",
                "state": "consumed",
                "last_seen_at": 1_900_000_000,
                "admission_history": [{"code": "FMRS7WTB", "state": "revoked"}],
            }
        ],
    )

    endpoint, pair_code = sdk_device_access._resolve_redevice_endpoint(code="FMRS7WTB")

    assert pair_code == "SNX68P2A"
    assert endpoint["_resolution"] == {
        "schema_version": "endpoint-resolution.v1",
        "matched_historical_code": "FMRS7WTB",
        "current_code": "SNX68P2A",
        "history_resolved": True,
    }


def test_sdk_device_access_assign_endpoint_records_owner(monkeypatch) -> None:
    from adaos.sdk.data import device_access as sdk_device_access

    devices = [
        {
            "ref": "redevice:endpoint-1",
            "kind": "redevice",
            "identity": {"endpoint_id": "endpoint-1", "pair_code": "ABCD1234"},
            "policy": {"effective_name": "Kitchen tablet"},
            "observation": {"online": True},
            "runtime": {},
        }
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr(sdk_device_access, "list_endpoint_devices", lambda kind="redevice", sync_registry=True: devices)

    def fake_set_assignment(endpoint_id, assignment, **kwargs):
        captured.update({"endpoint_id": endpoint_id, "assignment": assignment, **kwargs})
        return {
            "id": endpoint_id,
            "assignment": assignment,
            "endpoint_assignment": {
                "role": assignment,
                "assignment": assignment,
                "updated_at": 900.0,
                "owner": {"node_id": kwargs.get("owner_node_id"), "skill_id": kwargs.get("owner_skill_id")},
                "source": kwargs.get("source"),
                "reason": kwargs.get("reason"),
            },
        }

    monkeypatch.setattr(sdk_device_access._access_links, "set_redevice_assignment", fake_set_assignment)

    result = sdk_device_access.assign_endpoint(
        "redevice:endpoint-1",
        "slideshow",
        owner_node_id="hub-1",
        owner_skill_id="slideshow_skill",
        source="test",
        reason="operator_selected",
    )

    assert result["ok"] is True
    assert captured == {
        "endpoint_id": "endpoint-1",
        "assignment": "slideshow",
        "owner_node_id": "hub-1",
        "owner_skill_id": "slideshow_skill",
        "source": "test",
        "reason": "operator_selected",
    }
    assert result["endpoint_assignment"]["owner"] == {"node_id": "hub-1", "skill_id": "slideshow_skill"}


def test_sdk_device_access_send_endpoint_command_wraps_endpoint_envelope(monkeypatch) -> None:
    from adaos.sdk import redevice as sdk_redevice
    from adaos.sdk.data import device_access as sdk_device_access

    sent: dict[str, object] = {}

    class FakeBridge:
        def __init__(self, timeout=0):
            self.timeout = timeout

        def send_command(self, code, payload):
            sent["code"] = code
            sent["payload"] = payload
            return {"ok": True, "state": "queued"}

    monkeypatch.setattr(sdk_redevice, "ReDeviceBridge", FakeBridge)
    monkeypatch.setattr(
        sdk_device_access,
        "_resolve_redevice_endpoint",
        lambda device_ref=None, code=None: (
            {
                "endpoint_id": "endpoint-1",
                "code": "ABCD1234",
                "endpoint_policy": {"trust_level": "limited"},
            },
            "ABCD1234",
        ),
    )

    result = sdk_device_access.send_endpoint_command(
        "redevice:endpoint-1",
        {"type": "display.show_text", "text": "hello"},
        requested_by={"node_id": "hub-1", "skill_id": "test_skill"},
        constraints={"timeout_ms": 5000},
    )

    assert result["ok"] is True
    assert result["endpoint_command"]["schema_version"] == "endpoint-command.v1"
    assert result["endpoint_command"]["target"]["service"] == "display_endpoint"
    assert result["endpoint_command"]["requested_by"] == {"node_id": "hub-1", "skill_id": "test_skill"}
    assert sent["code"] == "ABCD1234"
    assert sent["payload"]["endpoint_command"]["command_id"] == result["endpoint_command"]["command_id"]
    assert sent["payload"]["command_id"] == result["endpoint_command"]["command_id"]


def test_command_profile_for_observed_only_member_allows_detach_and_deny(monkeypatch) -> None:
    device = {
        "ref": "member:member-2",
        "kind": "member",
        "identity": {"node_id": "member-2"},
        "policy": {
            "present": False,
            "managed_state": "observed_only",
            "revoked": False,
        },
    }

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-2"),
    )

    profile = device_access.get_command_profile("member:member-2")

    assert profile is not None
    assert profile["rename"] == {"enabled": False, "reason": "device_policy_missing"}
    assert profile["set_lifetime"] == {
        "enabled": False,
        "reason": "device_policy_missing",
        "presets": ["permanent", "1h", "1d", "7d", "30d"],
    }
    assert profile["detach"] == {"enabled": True}
    assert profile["deny"] == {"enabled": True}
    assert profile["open_apps"] == {"enabled": True, "node_id": "member-2"}
    assert profile["open_marketplace"] == {"enabled": True, "node_id": "member-2"}


def test_device_settings_schema_includes_lifetime_and_detach_metadata(monkeypatch) -> None:
    device = {
        "ref": "member:member-1",
        "kind": "member",
        "identity": {
            "node_id": "member-1",
            "browser_device_id": None,
            "hostname": "kitchen-tablet",
            "node_names": ["Kitchen tablet", "Kitchen screen"],
        },
        "policy": {
            "present": True,
            "managed_state": "managed",
            "display_name": "Kitchen tablet",
            "effective_name": "Kitchen tablet",
            "lifetime_mode": "permanent",
            "expires_at": None,
            "revoked": False,
        },
        "observation": {
            "online": True,
            "connection_state": "connected",
            "source": "member_link",
        },
        "runtime": {
            "connected_to_subnet": True,
        },
    }

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-1"),
    )

    settings = device_access.get_device_settings("member:member-1")

    assert settings is not None
    assert settings["title"] == "Kitchen tablet"
    assert settings["id"] == {
        "value": "member:member-1",
        "kind": "member",
        "node_id": "member-1",
        "link_id": "member-1",
    }
    assert settings["name"]["value"] == "Kitchen tablet, Kitchen screen"
    assert settings["name"]["primary"] == "Kitchen tablet"
    assert settings["name"]["names"] == ["Kitchen tablet", "Kitchen screen"]
    assert settings["name"]["placeholder"] == "Living room TV, Kitchen display"
    assert settings["name"]["save"] == {
        "enabled": True,
        "target": "browsers_skill.rename_device",
        "params": {"device_ref": "member:member-1"},
    }
    assert settings["name"]["policy"]["can_edit"] is True
    assert settings["name"]["policy"]["status"] == "managed"
    assert settings["name"]["policy"]["storage"] == "access_links.display_name + access_links.node_names"
    assert settings["lifetime"]["current_label"] == "Permanent"
    assert settings["lifetime"]["set"] == {
        "enabled": True,
        "presets": ["permanent", "1h", "1d", "7d", "30d"],
        "target": "browsers_skill.set_device_lifetime",
        "params": {"device_ref": "member:member-1"},
    }
    assert settings["lifetime"]["options"] == [
        {"id": "permanent", "label": "Permanent", "enabled": True},
        {"id": "1h", "label": "1h", "enabled": True},
        {"id": "1d", "label": "1d", "enabled": True},
        {"id": "7d", "label": "7d", "enabled": True},
        {"id": "30d", "label": "30d", "enabled": True},
    ]
    assert settings["detach"]["confirm_message"] == 'Detach device "Kitchen tablet"?'
    assert settings["detach"]["target"] == "browsers_skill.detach_device"
    assert settings["detach"]["params"] == {"device_ref": "member:member-1"}
    assert settings["deny"]["confirm_message"] == 'Deny future connections from "Kitchen tablet"?'
    assert settings["deny"]["target"] == "browsers_skill.deny_device"
    assert settings["deny"]["params"] == {"device_ref": "member:member-1"}
    assert settings["actions"]["open_apps"] == {"enabled": True, "node_id": "member-1"}
    assert settings["reconcile"]["issue_total"] == 0
    assert settings["adopt"] == {
        "enabled": False,
        "suggested_display_name": "Kitchen tablet",
        "preset": "permanent",
        "target": "browsers_skill.adopt_device",
        "params": {"device_ref": "member:member-1"},
    }


def test_device_settings_schema_preserves_disabled_policy_actions(monkeypatch) -> None:
    device = {
        "ref": "member:member-2",
        "kind": "member",
        "identity": {
            "node_id": "member-2",
            "browser_device_id": None,
            "hostname": None,
        },
        "policy": {
            "present": False,
            "managed_state": "observed_only",
            "display_name": None,
            "effective_name": "Node 2",
            "lifetime_mode": "permanent",
            "expires_at": None,
            "revoked": False,
        },
        "observation": {
            "online": False,
            "connection_state": None,
            "source": "subnet_directory",
        },
        "runtime": {
            "connected_to_subnet": False,
        },
    }

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-2"),
    )

    settings = device_access.get_device_settings("member:member-2")

    assert settings is not None
    assert settings["name"]["save"] == {
        "enabled": True,
        "target": "browsers_skill.adopt_device",
        "params": {"device_ref": "member:member-2"},
    }
    assert settings["name"]["policy"]["mode"] == "adopt"
    assert settings["name"]["policy"]["can_edit"] is True
    assert settings["lifetime"]["set"] == {
        "enabled": False,
        "reason": "device_policy_missing",
        "presets": ["permanent", "1h", "1d", "7d", "30d"],
        "target": "browsers_skill.set_device_lifetime",
        "params": {"device_ref": "member:member-2"},
    }
    assert settings["lifetime"]["options"] == [
        {"id": "permanent", "label": "Permanent", "enabled": False, "reason": "device_policy_missing"},
        {"id": "1h", "label": "1h", "enabled": False, "reason": "device_policy_missing"},
        {"id": "1d", "label": "1d", "enabled": False, "reason": "device_policy_missing"},
        {"id": "7d", "label": "7d", "enabled": False, "reason": "device_policy_missing"},
        {"id": "30d", "label": "30d", "enabled": False, "reason": "device_policy_missing"},
    ]
    assert settings["detach"]["enabled"] is True
    assert settings["detach"]["target"] == "browsers_skill.detach_device"
    assert settings["detach"]["params"] == {"device_ref": "member:member-2"}
    assert settings["deny"]["enabled"] is True
    assert settings["deny"]["target"] == "browsers_skill.deny_device"
    assert settings["deny"]["params"] == {"device_ref": "member:member-2"}
    assert settings["reconcile"]["state"] == "attention"
    assert settings["reconcile"]["issues"][0]["id"] == "device_policy_missing"
    assert settings["adopt"] == {
        "enabled": True,
        "suggested_display_name": "Node 2",
        "preset": "permanent",
        "target": "browsers_skill.adopt_device",
        "params": {"device_ref": "member:member-2"},
    }


def test_hub_device_settings_use_local_node_config_policy(monkeypatch) -> None:
    class _Config:
        subnet_id_value = "sn_local"
        node_id_value = "node-local"
        node_names = ["Main hub", "Workstation"]

    monkeypatch.setattr(device_access, "_load_node_config", lambda: _Config())

    settings = device_access.get_device_settings("hub:sn_local")

    assert settings is not None
    assert settings["kind"] == "hub"
    assert settings["id"] == {
        "value": "hub:sn_local",
        "kind": "hub",
        "node_id": "node-local",
        "link_id": "sn_local",
    }
    assert settings["name"]["value"] == "Main hub, Workstation"
    assert settings["name"]["save"] == {
        "enabled": True,
        "target": "browsers_skill.rename_device",
        "params": {"device_ref": "hub:sn_local"},
    }
    assert settings["name"]["policy"] == {
        "can_edit": True,
        "status": "local_config",
        "storage": ".adaos/node.yaml: node.node_names",
        "field": "node.node_names",
        "mode": "rename",
        "reason": None,
    }
    assert settings["lifetime"]["set"]["reason"] == "hub_lifetime_not_applicable"
    assert settings["detach"]["reason"] == "hub_detach_not_applicable"
    assert settings["deny"]["reason"] == "hub_deny_not_applicable"


def test_rename_hub_device_updates_local_node_names(monkeypatch) -> None:
    class _Config:
        subnet_id_value = "sn_local"
        node_id_value = "node-local"
        node_names = ["Main hub"]

    calls: list[list[str]] = []

    monkeypatch.setattr(device_access, "_load_node_config", lambda: _Config())

    def _fake_set_node_names(names: list[str]):
        calls.append(list(names))
        updated = _Config()
        updated.node_names = list(names)
        return updated

    monkeypatch.setattr(device_access, "_set_local_node_names", _fake_set_node_names)

    result = device_access.rename_device("hub:sn_local", "Main hub, Workstation, main hub")

    assert result["ok"] is True
    assert result["entry"]["kind"] == "hub"
    assert result["entry"]["display_name"] == "Main hub"
    assert result["entry"]["node_names"] == ["Main hub", "Workstation"]
    assert result["entry"]["storage"] == ".adaos/node.yaml: node.node_names"
    assert calls == [["Main hub", "Workstation"]]


def test_local_hub_member_alias_resolves_to_hub_settings(monkeypatch) -> None:
    class _Config:
        role = "hub"
        subnet_id_value = "sn_local"
        node_id_value = "node-local"
        node_names = ["Main hub"]

    monkeypatch.setattr(device_access, "_load_node_config", lambda: _Config())
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "node-local"),
    )

    settings = device_access.get_device_settings("member:node-local")

    assert settings is not None
    assert settings["device_ref"] == "hub:sn_local"
    assert settings["kind"] == "hub"
    assert settings["id"]["value"] == "hub:sn_local"
    assert settings["id"]["kind"] == "hub"
    assert settings["name"]["save"]["params"] == {"device_ref": "hub:sn_local"}
    assert settings["lifetime"]["set"]["enabled"] is False
    assert settings["detach"]["enabled"] is False


def test_rename_local_hub_member_alias_updates_local_node_names(monkeypatch) -> None:
    class _Config:
        role = "hub"
        subnet_id_value = "sn_local"
        node_id_value = "node-local"
        node_names = ["Main hub"]

    calls: list[list[str]] = []

    monkeypatch.setattr(device_access, "_load_node_config", lambda: _Config())
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "node-local"),
    )

    def _fake_set_node_names(names: list[str]):
        calls.append(list(names))
        updated = _Config()
        updated.node_names = list(names)
        return updated

    monkeypatch.setattr(device_access, "_set_local_node_names", _fake_set_node_names)

    result = device_access.rename_device("member:node-local", "Main hub, Workstation")

    assert result["ok"] is True
    assert result["device_ref"] == "hub:sn_local"
    assert result["entry"]["kind"] == "hub"
    assert result["entry"]["node_names"] == ["Main hub", "Workstation"]
    assert calls == [["Main hub", "Workstation"]]


def test_adopt_device_delegates_to_reconciler(monkeypatch) -> None:
    captured: list[tuple[str, str | None, str]] = []

    def _fake_adopt(device_ref: str, *, display_name: str | None = None, preset: str = "permanent"):
        captured.append((device_ref, display_name, preset))
        return {"ok": True, "device_ref": device_ref}

    monkeypatch.setattr(device_access._device_reconciler, "adopt_device", _fake_adopt)

    result = device_access.adopt_device("member:member-2", "Workshop display", "7d")

    assert result == {"ok": True, "device_ref": "member:member-2"}
    assert captured == [("member:member-2", "Workshop display", "7d")]


def test_rename_device_updates_live_member_when_connected(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    device = {
        "ref": "member:member-1",
        "kind": "member",
        "identity": {"node_id": "member-1"},
        "policy": {"present": True, "managed_state": "managed", "revoked": False},
    }

    class _FakeManager:
        def is_connected(self, node_id: str) -> bool:
            calls.append(("is_connected", node_id))
            return True

        async def set_member_node_names(self, node_id: str, *, node_names: list[str]) -> dict[str, object]:
            calls.append(("set_names", node_id, list(node_names)))
            return {"ok": True}

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-1"),
    )
    monkeypatch.setattr(
        device_access._access_links,
        "rename_link",
        lambda kind, link_id, display_name, *, node_names=None: {
            "kind": kind,
            "id": link_id,
            "display_name": display_name,
            "node_names": list(node_names or []),
        },
    )
    monkeypatch.setattr(device_access._device_inventory, "_get_hub_link_manager", lambda: _FakeManager())

    result = device_access.rename_device("member:member-1", "Kitchen tablet, Kitchen screen, kitchen tablet")

    assert result["ok"] is True
    assert result["entry"] == {
        "kind": "member",
        "id": "member-1",
        "display_name": "Kitchen tablet",
        "node_names": ["Kitchen tablet", "Kitchen screen"],
    }
    assert result["runtime_update"] == {"attempted": True, "applied": True}
    assert calls == [
        ("is_connected", "member-1"),
        ("set_names", "member-1", ["Kitchen tablet", "Kitchen screen"]),
    ]


def test_add_device_alias_requires_policy_and_delegates_to_access_links(monkeypatch) -> None:
    device = {
        "ref": "member:member-1",
        "kind": "member",
        "identity": {"node_id": "member-1"},
        "policy": {"present": True, "managed_state": "managed", "revoked": False},
    }
    calls: list[tuple[str, str, str, str | None, str | None, str | None]] = []

    def _fake_add_alias(
        kind,
        link_id,
        alias,
        *,
        locale=None,
        actor=None,
        source="access_links",
        request_id=None,
        base_fingerprint=None,
    ):
        calls.append((kind, link_id, alias, locale, actor, source))
        return {"ok": True, "status": "applied", "entry": {"id": link_id}}

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-1"),
    )
    monkeypatch.setattr(device_access._access_links, "add_link_alias", _fake_add_alias)

    result = device_access.add_device_alias(
        "member:member-1",
        "kitchen screen",
        locale="en",
        actor="user:operator",
    )

    assert result["ok"] is True
    assert result["device"] == device
    assert calls == [("member", "member-1", "kitchen screen", "en", "user:operator", "device_access")]


def test_remove_and_deprecate_device_alias_delegate_to_access_links(monkeypatch) -> None:
    device = {
        "ref": "member:member-1",
        "kind": "member",
        "identity": {"node_id": "member-1"},
        "policy": {"present": True, "managed_state": "managed", "revoked": False},
    }
    calls: list[tuple[str, str, str, str, str | None]] = []

    def _fake_remove_alias(kind, link_id, alias, *, locale=None, actor=None, source="access_links", request_id=None, base_fingerprint=None):
        calls.append(("remove", kind, link_id, alias, source))
        return {"ok": True, "status": "applied", "entry": {"id": link_id}}

    def _fake_deprecate_alias(kind, link_id, alias, *, locale=None, actor=None, source="access_links", request_id=None, base_fingerprint=None):
        calls.append(("deprecate", kind, link_id, alias, source))
        return {"ok": True, "status": "applied", "entry": {"id": link_id}}

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-1"),
    )
    monkeypatch.setattr(device_access._access_links, "remove_link_alias", _fake_remove_alias)
    monkeypatch.setattr(device_access._access_links, "deprecate_link_alias", _fake_deprecate_alias)

    removed = device_access.remove_device_alias("member:member-1", "kitchen screen", locale="en")
    deprecated = device_access.deprecate_device_alias("member:member-1", "old kitchen screen", locale="en")

    assert removed["ok"] is True
    assert deprecated["ok"] is True
    assert calls == [
        ("remove", "member", "member-1", "kitchen screen", "device_access"),
        ("deprecate", "member", "member-1", "old kitchen screen", "device_access"),
    ]


def test_detach_device_unregisters_live_member_when_connected(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    device = {
        "ref": "member:member-1",
        "kind": "member",
        "identity": {"node_id": "member-1"},
        "policy": {"present": True, "managed_state": "managed", "revoked": False},
    }

    class _FakeManager:
        def is_connected(self, node_id: str) -> bool:
            calls.append(("is_connected", node_id))
            return True

        async def unregister(self, node_id: str) -> dict[str, object]:
            calls.append(("unregister", node_id))
            return {"ok": True}

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-1"),
    )
    monkeypatch.setattr(
        device_access._access_links,
        "detach_link",
        lambda kind, link_id: {"kind": kind, "id": link_id, "admission_policy": "detached"},
    )
    monkeypatch.setattr(device_access._device_inventory, "_get_hub_link_manager", lambda: _FakeManager())

    result = device_access.detach_device("member:member-1")

    assert result["ok"] is True
    assert result["runtime_update"] == {"attempted": True, "applied": True}
    assert calls == [
        ("is_connected", "member-1"),
        ("unregister", "member-1"),
    ]


def test_deny_device_unregisters_live_member_when_connected(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    device = {
        "ref": "member:member-1",
        "kind": "member",
        "identity": {"node_id": "member-1"},
        "policy": {"present": True, "managed_state": "managed", "revoked": False},
    }

    class _FakeManager:
        def is_connected(self, node_id: str) -> bool:
            calls.append(("is_connected", node_id))
            return True

        async def unregister(self, node_id: str) -> dict[str, object]:
            calls.append(("unregister", node_id))
            return {"ok": True}

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-1"),
    )
    monkeypatch.setattr(
        device_access._access_links,
        "deny_link",
        lambda kind, link_id: {"kind": kind, "id": link_id, "admission_policy": "deny", "revoked": True},
    )
    monkeypatch.setattr(device_access._device_inventory, "_get_hub_link_manager", lambda: _FakeManager())

    result = device_access.deny_device("member:member-1")

    assert result["ok"] is True
    assert result["runtime_update"] == {"attempted": True, "applied": True}
    assert calls == [
        ("is_connected", "member-1"),
        ("unregister", "member-1"),
    ]


def test_browser_command_profile_disables_node_context_actions(monkeypatch) -> None:
    device = {
        "ref": "browser:browser-1",
        "kind": "browser",
        "identity": {"node_id": None},
        "policy": {"present": True, "managed_state": "managed", "revoked": False},
    }

    monkeypatch.setattr(device_access._device_inventory, "get_device", lambda device_ref: dict(device))
    monkeypatch.setattr(
        device_access._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("browser", "browser-1"),
    )

    profile = device_access.get_command_profile("browser:browser-1")

    assert profile is not None
    assert profile["rename"]["enabled"] is True
    assert profile["detach"]["enabled"] is True
    assert profile["deny"]["enabled"] is True
    assert profile["open_apps"] == {"enabled": False, "reason": "browser_has_no_node_context"}
    assert profile["open_marketplace"] == {"enabled": False, "reason": "browser_has_no_node_context"}
