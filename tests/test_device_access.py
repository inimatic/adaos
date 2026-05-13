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
    assert profile["open_apps"] == {"enabled": True, "node_id": "member-1"}
    assert profile["open_marketplace"] == {"enabled": True, "node_id": "member-1"}


def test_command_profile_for_observed_only_member_disables_policy_commands(monkeypatch) -> None:
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
    assert profile["detach"] == {"enabled": False, "reason": "device_policy_missing"}
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
    assert settings["name"] == {
        "value": "Kitchen tablet",
        "placeholder": "Living room TV",
        "save": {
            "enabled": True,
            "target": "browsers_skill.rename_device",
            "params": {"device_ref": "member:member-1"},
        },
    }
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
        "enabled": False,
        "reason": "device_policy_missing",
        "target": "browsers_skill.rename_device",
        "params": {"device_ref": "member:member-2"},
    }
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
    assert settings["detach"]["enabled"] is False
    assert settings["detach"]["reason"] == "device_policy_missing"
    assert settings["detach"]["target"] == "browsers_skill.detach_device"
    assert settings["detach"]["params"] == {"device_ref": "member:member-2"}
    assert settings["reconcile"]["state"] == "attention"
    assert settings["reconcile"]["issues"][0]["id"] == "device_policy_missing"
    assert settings["adopt"] == {
        "enabled": True,
        "suggested_display_name": "Node 2",
        "preset": "permanent",
        "target": "browsers_skill.adopt_device",
        "params": {"device_ref": "member:member-2"},
    }


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
        lambda kind, link_id, display_name: {
            "kind": kind,
            "id": link_id,
            "display_name": display_name,
        },
    )
    monkeypatch.setattr(device_access, "_get_hub_link_manager", lambda: _FakeManager())

    result = device_access.rename_device("member:member-1", "Kitchen tablet")

    assert result["ok"] is True
    assert result["entry"] == {
        "kind": "member",
        "id": "member-1",
        "display_name": "Kitchen tablet",
    }
    assert result["runtime_update"] == {"attempted": True, "applied": True}
    assert calls == [
        ("is_connected", "member-1"),
        ("set_names", "member-1", ["Kitchen tablet"]),
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
        lambda kind, link_id: {"kind": kind, "id": link_id, "revoked": True},
    )
    monkeypatch.setattr(device_access, "_get_hub_link_manager", lambda: _FakeManager())

    result = device_access.detach_device("member:member-1")

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
    assert profile["open_apps"] == {"enabled": False, "reason": "browser_has_no_node_context"}
    assert profile["open_marketplace"] == {"enabled": False, "reason": "browser_has_no_node_context"}
