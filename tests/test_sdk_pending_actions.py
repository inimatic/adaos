from __future__ import annotations

import pytest

from adaos.sdk import data
from adaos.sdk.data import pending_actions


def test_legacy_sdk_cannot_forge_pending_action_response() -> None:
    with pytest.raises(PermissionError, match="requires_verified_ingress"):
        pending_actions.respond_pending_action(
            "pa.target",
            "approve",
            responder={"type": "system", "system_id": "forged"},
        )


def test_legacy_response_helper_is_hidden_from_sdk_discovery() -> None:
    assert "respond_pending_action" not in pending_actions.__all__
    assert "respond_pending_action" not in data.__all__


def test_expiry_control_is_hidden_from_public_sdk_surface() -> None:
    assert "expire_pending_actions" not in pending_actions.__all__
    assert "expire_pending_actions" not in data.__all__


def test_canonical_interaction_query_is_public() -> None:
    assert "get_pending_interaction" in pending_actions.__all__
    assert "get_pending_interaction" in data.__all__
    assert "list_pending_interactions" in pending_actions.__all__
    assert "list_pending_interactions" in data.__all__


def test_bounded_list_derives_acl_principal_from_verified_ingress(monkeypatch) -> None:
    import adaos.services.pending_actions as service

    calls: list[dict] = []
    monkeypatch.setattr(pending_actions, "require_ctx", lambda _feature: object())
    monkeypatch.setattr(pending_actions.access, "require", lambda capability: {"action": capability})
    monkeypatch.setattr(pending_actions.access, "caller", lambda: {"kind": "user", "id": "user.1"})
    monkeypatch.setattr(
        pending_actions.access,
        "application",
        lambda: {"application_id": "app.1"},
    )
    monkeypatch.setattr(
        service,
        "query_pending_actions",
        lambda **kwargs: calls.append(dict(kwargs)) or {"items": [], "next_cursor": None},
    )

    pending_actions.list_pending_actions(limit=7, field_mask="summary")

    assert calls[0]["limit"] == 7
    assert calls[0]["principal"] == {
        "kind": "user",
        "id": "user.1",
        "application_id": "app.1",
    }


def test_legacy_terminal_flag_retains_bounded_authorized_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(pending_actions, "require_ctx", lambda _: object())
    monkeypatch.setattr(pending_actions.access, "require", lambda _: None)
    monkeypatch.setattr(pending_actions, "_verified_principal", lambda: {"id": "owner"})
    monkeypatch.setattr("adaos.services.pending_actions.query_pending_actions", lambda **kwargs:
        calls.append(kwargs) or {"items": [{"id": "one", "status": "pending"}],
                                "next_cursor": "opaque", "has_more": True})
    page = pending_actions.list_pending_actions(include_terminal=False, limit=7)
    assert set(calls[0]["statuses"]) == {"pending", "postponed"}
    assert calls[0]["principal"] == {"id": "owner"}
    assert calls[0]["limit"] == 7
    assert page["active_items"] == page["items"]
    assert page["history_items"] == []
    assert page["next_cursor"] == "opaque"
    with pytest.raises(ValueError, match="conflicts"):
        pending_actions.list_pending_actions(include_terminal=False, statuses=["cancelled"])


def test_canonical_query_derives_acl_principal_from_verified_ingress(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(pending_actions, "require_ctx", lambda _feature: object())
    monkeypatch.setattr(pending_actions.access, "require", lambda capability: {"action": capability})
    monkeypatch.setattr(pending_actions.access, "caller", lambda: {"kind": "user", "id": "user.1"})
    monkeypatch.setattr(
        pending_actions.access,
        "application",
        lambda: {"application_id": "app.1"},
    )
    monkeypatch.setattr(
        "adaos.services.conversation_interactions.query_interactions",
        lambda **kwargs: calls.append(dict(kwargs)) or {"items": [], "next_cursor": None},
    )

    pending_actions.list_pending_interactions(limit=7, field_mask="audit")

    assert calls[0]["limit"] == 7
    assert calls[0]["field_mask"] == "audit"
    assert calls[0]["principal"] == {
        "kind": "user",
        "id": "user.1",
        "application_id": "app.1",
    }


def test_canonical_projection_derives_acl_principal_from_verified_ingress(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(pending_actions, "require_ctx", lambda _feature: object())
    monkeypatch.setattr(pending_actions.access, "require", lambda capability: {"action": capability})
    monkeypatch.setattr(pending_actions.access, "caller", lambda: {"kind": "user", "id": "user.1"})
    monkeypatch.setattr(
        pending_actions.access,
        "application",
        lambda: {"application_id": "app.1"},
    )
    monkeypatch.setattr(
        "adaos.services.pending_action_projection.project_pending_action_from_store",
        lambda interaction_id, **kwargs: calls.append(
            {"interaction_id": interaction_id, **kwargs}
        )
        or {"projection_id": interaction_id},
    )

    pending_actions.get_pending_interaction("interaction.1")

    assert calls == [
        {
            "interaction_id": "interaction.1",
            "principal": {
                "kind": "user",
                "id": "user.1",
                "application_id": "app.1",
            },
        }
    ]
