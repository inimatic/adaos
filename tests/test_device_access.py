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
