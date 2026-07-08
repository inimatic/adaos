from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import personalization
from adaos.domain.personalization_access import DeviceKey, ScopeRef, SessionKey, SubjectRef
from adaos.services import access_links
from adaos.services.yjs import gateway_ws as gateway_module
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore


TOKEN_HEADERS = {"X-AdaOS-Token": "dev-local-token"}
OWNER = SubjectRef("user", "owner")
MASHA = SubjectRef("user", "masha")
FAMILY = ScopeRef("workspace", "family")
WORK = ScopeRef("workspace", "work")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(personalization.router, prefix="/api")
    return TestClient(app)


def test_phase6_device_pairing_link_records_device_and_revocation_cuts_sessions(tmp_path) -> None:
    denied: list[str] = []
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER, access_link_denier=denied.append)

    service.grant_role_preset(subject=MASHA, scope=FAMILY, role="member", actor=OWNER)
    invite = service.create_device_pairing_link(
        invite_id="device-masha-phone",
        subject=MASHA,
        scope=FAMILY,
        role="member",
        issued_by=MASHA,
        expires_at=9999999999.0,
        device_id="masha-phone",
        device_name="Masha phone",
    )

    assert invite["kind"] == "device_pairing_link"
    paired = service.claim_device_pairing_link(
        "device-masha-phone",
        subject=MASHA,
        actor=MASHA,
        device_id="masha-phone",
        key_id="masha-phone-key",
        public_key_ref="local-device:masha-phone",
        session_id="masha-phone-session",
        device_name="Masha phone",
    )

    assert paired["device"]["status"] == "active"
    assert store.get_device_key("masha-phone")["user_id"] == "masha"
    assert store.get_session("masha-phone-session")["device_id"] == "masha-phone"
    assert (
        service.evaluate(
            actor=SubjectRef("session", "masha-phone-session"),
            action="workspace.read",
            scope=FAMILY,
        ).decision
        == "allow"
    )

    service.revoke_device("masha-phone", actor=OWNER, reason="lost")
    assert store.get_device_key("masha-phone")["status"] == "revoked"
    assert store.get_session("masha-phone-session")["status"] == "revoked"
    assert denied == ["masha-phone", "masha-phone-session"]
    assert (
        service.evaluate(
            actor=SubjectRef("session", "masha-phone-session"),
            action="workspace.read",
            scope=FAMILY,
        ).reason_code
        == "inactive_session"
    )


def test_phase6_child_device_pairing_requires_admin_approval(tmp_path) -> None:
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)
    child = SubjectRef("user", "child-masha")

    service.grant_role_preset(subject=child, scope=FAMILY, role="child", actor=OWNER)

    with pytest.raises(PermissionError, match="missing_capability"):
        service.create_device_pairing_link(
            invite_id="child-self-device",
            subject=child,
            scope=FAMILY,
            role="child",
            issued_by=child,
            expires_at=9999999999.0,
            device_id="child-tablet",
            device_name="Child tablet",
        )

    approved = service.create_device_pairing_link(
        invite_id="child-owner-approved-device",
        subject=child,
        scope=FAMILY,
        role="child",
        issued_by=OWNER,
        expires_at=9999999999.0,
        device_id="child-tablet",
        device_name="Child tablet",
    )

    assert approved["subject_id"] == "child-masha"
    assert service.list_audit(actor=child, event_type="policy.deny")


def test_phase6_browser_link_denial_requests_live_yws_disconnect(monkeypatch) -> None:
    requested: list[tuple[str, str]] = []

    def _record_disconnect(token: str, *, reason: str = "", **kwargs: object) -> int:  # noqa: ARG001
        requested.append((token, reason))
        return 0

    monkeypatch.setattr(gateway_module, "request_close_browser_yws_connections", _record_disconnect)
    access_links.upsert_link(
        "browser",
        "phase67-runtime-device",
        {"admission_session_id": "phase67-runtime-session", "admission_policy": "allow"},
    )

    access_links.deny_link("browser", "phase67-runtime-device")

    assert requested == [
        ("phase67-runtime-device", "browser_access_denied"),
        ("phase67-runtime-session", "browser_access_denied"),
    ]


def test_phase6_admin_recovery_link_replaces_lost_device_and_revokes_old_sessions(tmp_path) -> None:
    denied: list[str] = []
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER, access_link_denier=denied.append)
    service.grant_role_preset(subject=MASHA, scope=FAMILY, role="member", actor=OWNER)
    service.put_device_key(
        DeviceKey(
            user_id="masha",
            device_id="masha-lost-phone",
            key_id="masha-lost-key",
            public_key_ref="local-device:masha-lost-phone",
        ),
        actor=OWNER,
    )
    service.put_session(
        SessionKey(
            session_id="masha-lost-session",
            key_id="masha-lost-key",
            subject=MASHA,
            device_id="masha-lost-phone",
        )
    )

    created = service.create_admin_recovery_link(
        invite_id="recovery-masha",
        recovery_id="recovery-action-masha",
        subject=MASHA,
        scope=FAMILY,
        issued_by=OWNER,
        expires_at=9999999999.0,
        replacement_device_id="masha-new-phone",
        revoked_device_ids=("masha-lost-phone",),
        reason="lost phone",
    )
    assert created["invite"]["kind"] == "admin_recovery_link"

    completed = service.complete_admin_recovery_link(
        "recovery-masha",
        subject=MASHA,
        replacement_device_id="masha-new-phone",
        key_id="masha-new-key",
        public_key_ref="local-device:masha-new-phone",
        session_id="masha-new-session",
    )

    assert completed["recovery"]["status"] == "accepted"
    assert store.get_device_key("masha-new-phone")["status"] == "active"
    assert store.get_device_key("masha-lost-phone")["status"] == "revoked"
    assert store.get_session("masha-lost-session")["status"] == "revoked"
    assert denied == ["masha-lost-phone", "masha-lost-session"]
    assert service.list_audit(subject=MASHA, event_type="admin_recovery.completed")


def test_phase7_multi_admin_grant_and_denial_paths(tmp_path) -> None:
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)
    co_owner = SubjectRef("user", "co-parent")
    scoped_admin = SubjectRef("user", "family-admin")
    member = SubjectRef("user", "family-member")

    service.grant_role_preset(subject=co_owner, scope=FAMILY, role="co_owner", actor=OWNER)
    service.grant_role_preset(subject=scoped_admin, scope=FAMILY, role="admin", actor=OWNER)
    service.grant_role_preset(subject=member, scope=FAMILY, role="member", actor=OWNER)

    co_owner_grant = service.grant_role_preset(
        subject=SubjectRef("user", "co-owner-added-member"),
        scope=FAMILY,
        role="member",
        actor=co_owner,
    )
    admin_grant = service.grant_role_preset(
        subject=SubjectRef("user", "admin-added-guest"),
        scope=FAMILY,
        role="guest",
        actor=scoped_admin,
    )

    assert co_owner_grant["membership"]["issued_by"] == co_owner.to_dict()
    assert admin_grant["membership"]["issued_by"] == scoped_admin.to_dict()

    with pytest.raises(PermissionError, match="missing_capability"):
        service.grant_role_preset(
            subject=SubjectRef("user", "member-added-user"),
            scope=FAMILY,
            role="guest",
            actor=member,
        )
    with pytest.raises(PermissionError, match="missing_capability"):
        service.grant_role_preset(
            subject=SubjectRef("user", "cross-scope-user"),
            scope=WORK,
            role="member",
            actor=scoped_admin,
        )


def test_phase7_admin_api_grants_pairs_summarizes_and_revokes_device() -> None:
    client = _client()
    subject_id = "api-phase67-masha"
    device_id = "api-phase67-phone"

    granted = client.post(
        "/api/personalization/admin/grants",
        json={"subject_id": subject_id, "role": "member", "scope": {"kind": "workspace", "id": "family"}},
        headers=TOKEN_HEADERS,
    )
    assert granted.status_code == 200
    assert granted.json()["grant"]["role"] == "member"

    created = client.post(
        "/api/personalization/devices/pairing-links",
        json={
            "subject_id": subject_id,
            "role": "member",
            "scope": {"kind": "workspace", "id": "family"},
            "device_id": device_id,
            "device_name": "API phase67 phone",
            "expires_in_minutes": 10,
        },
        headers=TOKEN_HEADERS,
    )
    assert created.status_code == 200
    invite_id = created.json()["invite"]["invite_id"]

    claimed = client.post(
        f"/api/personalization/invites/{invite_id}/claim",
        json={
            "subject_id": subject_id,
            "session_id": f"{device_id}-session",
            "device_id": device_id,
            "device_name": "API phase67 phone",
        },
    )
    assert claimed.status_code == 200
    assert claimed.json()["device"]["device_id"] == device_id
    assert access_links.authorize_link("browser", device_id) == (True, None)
    browser_link = access_links.get_link("browser", device_id)
    assert browser_link is not None
    assert browser_link["device_display_name"] == "API phase67 phone"
    assert browser_link["display_name"] == ""

    summary = client.get("/api/personalization/admin/summary", headers=TOKEN_HEADERS)
    assert summary.status_code == 200
    payload = summary.json()["summary"]
    assert any(item["device_id"] == device_id for item in payload["devices"])
    assert any(item["subject"]["id"] == subject_id for item in payload["memberships"])

    revoked = client.post(
        f"/api/personalization/admin/devices/{device_id}/revoke",
        json={"reason": "test cleanup"},
        headers=TOKEN_HEADERS,
    )
    assert revoked.status_code == 200
    assert revoked.json()["device"]["status"] == "revoked"
    assert access_links.authorize_link("browser", device_id) == (False, "denied")
