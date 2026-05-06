from __future__ import annotations

from adaos.services import device_reconciler


def test_reconcile_device_flags_observed_only_member_and_adopt_action(monkeypatch) -> None:
    device = {
        "ref": "member:member-2",
        "kind": "member",
        "identity": {
            "link_id": "member-2",
            "node_id": "member-2",
            "hostname": "guest-node",
            "node_names": ["Workshop display"],
        },
        "policy": {
            "present": False,
            "managed_state": "observed_only",
            "display_name": None,
            "effective_name": "Workshop display",
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

    monkeypatch.setattr(device_reconciler._device_inventory, "get_device", lambda device_ref: dict(device))

    result = device_reconciler.reconcile_device("member:member-2")

    assert result is not None
    assert result["state"] == "attention"
    assert result["issue_total"] == 1
    assert result["issues"][0]["id"] == "device_policy_missing"
    assert result["actions"]["adopt_device"] == {
        "enabled": True,
        "suggested_display_name": "Workshop display",
        "preset": "permanent",
    }


def test_reconcile_device_detects_runtime_name_drift_for_connected_member(monkeypatch) -> None:
    device = {
        "ref": "member:member-3",
        "kind": "member",
        "identity": {
            "link_id": "member-3",
            "node_id": "member-3",
            "hostname": "kitchen-node",
            "node_names": ["Kitchen runtime"],
        },
        "policy": {
            "present": True,
            "managed_state": "managed",
            "display_name": "Kitchen tablet",
            "effective_name": "Kitchen tablet",
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

    monkeypatch.setattr(device_reconciler._device_inventory, "get_device", lambda device_ref: dict(device))

    result = device_reconciler.reconcile_device("member:member-3")

    assert result is not None
    assert result["state"] == "steady"
    assert result["issue_total"] == 1
    assert result["issues"][0] == {
        "id": "display_name_runtime_drift",
        "severity": "info",
        "summary": "Runtime member name differs from managed device display name.",
        "action": "sync_runtime_name",
        "expected_name": "Kitchen tablet",
        "observed_name": "Kitchen runtime",
    }
    assert result["actions"]["sync_runtime_name"] == {"enabled": True}


def test_adopt_device_materializes_policy_from_observed_member(monkeypatch) -> None:
    state = {"managed": False}
    captured: dict[str, object] = {}
    observed_device = {
        "ref": "member:member-2",
        "kind": "member",
        "identity": {
            "link_id": "member-2",
            "node_id": "member-2",
            "hostname": "guest-node",
            "node_names": ["Workshop display"],
        },
        "policy": {
            "present": False,
            "managed_state": "observed_only",
            "display_name": None,
            "effective_name": "Workshop display",
        },
        "observation": {
            "online": True,
            "connection_state": "connected",
            "last_seen_at": 123.0,
            "source": "member_link",
        },
        "runtime": {
            "connected_to_subnet": True,
        },
    }
    managed_device = {
        **observed_device,
        "policy": {
            "present": True,
            "managed_state": "managed",
            "display_name": "Workshop display",
            "effective_name": "Workshop display",
        },
    }

    monkeypatch.setattr(
        device_reconciler._device_inventory,
        "parse_device_ref",
        lambda device_ref: ("member", "member-2"),
    )
    monkeypatch.setattr(
        device_reconciler._device_inventory,
        "get_device",
        lambda device_ref: dict(managed_device if state["managed"] else observed_device),
    )

    def _fake_upsert(kind, entry_id, patch):
        captured["kind"] = kind
        captured["entry_id"] = entry_id
        captured["patch"] = dict(patch)
        return {"kind": kind, "id": entry_id, **dict(patch)}

    def _fake_set_lifetime(kind, entry_id, preset):
        state["managed"] = True
        return {
            "kind": kind,
            "id": entry_id,
            "display_name": "Workshop display",
            "lifetime_mode": "permanent",
            "preset": preset,
        }

    monkeypatch.setattr(device_reconciler._access_links, "upsert_link", _fake_upsert)
    monkeypatch.setattr(device_reconciler._access_links, "set_link_lifetime", _fake_set_lifetime)

    result = device_reconciler.adopt_device("member:member-2")

    assert result["ok"] is True
    assert captured == {
        "kind": "member",
        "entry_id": "member-2",
        "patch": {
            "display_name": "Workshop display",
            "online": True,
            "connection_state": "connected",
            "last_seen_at": 123.0,
            "revoked": False,
            "revoked_at": None,
            "hostname": "guest-node",
            "node_names": ["Workshop display"],
        },
    }
    assert result["device"]["policy"]["present"] is True
    assert result["reconcile"]["consistent"] is True
