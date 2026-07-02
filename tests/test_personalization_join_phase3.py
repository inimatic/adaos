from __future__ import annotations

import pytest

from adaos.domain.personalization_access import ScopeRef, SessionKey, SubjectRef, UserProfile
from adaos.services.personalization_access import (
    PersonalizationAccessError,
    PersonalizationAccessService,
    PersonalizationAccessStore,
)


OWNER = SubjectRef("user", "owner")
MASHA = SubjectRef("user", "masha")
FAMILY = ScopeRef("workspace", "family")


def test_phase3_guest_join_is_public_limited_and_bulk_revocable(tmp_path) -> None:
    denied_links: list[str] = []
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER, access_link_denier=denied_links.append)

    with pytest.raises(PersonalizationAccessError, match="requires expires_at"):
        service.create_guest_join_link(
            invite_id="guest-no-expiry",
            scope=FAMILY,
            issued_by=OWNER,
            expires_at=None,
        )
    service.create_guest_join_link(
        invite_id="guest-class",
        scope=FAMILY,
        issued_by=OWNER,
        expires_at=9999999999.0,
        max_sessions=2,
    )
    preview = service.preview_invite("guest-class", expected_scope=FAMILY)

    assert preview["kind"] == "guest_join_link"
    assert preview["role"] == "guest"
    assert preview["profile_hint"] is None
    assert preview["requires_acceptance"] is True

    first = service.claim_invite(
        "guest-class",
        accepted_by=SubjectRef("session", "browser-a"),
        expected_scope=FAMILY,
        session_id="browser-a",
    )
    second = service.claim_invite(
        "guest-class",
        accepted_by=SubjectRef("session", "browser-b"),
        expected_scope=FAMILY,
        session_id="browser-b",
    )

    assert first["status"] == "pending"
    assert second["status"] == "accepted"
    assert store.get_session("browser-a")["status"] == "active"
    assert (
        service.evaluate(
            actor=SubjectRef("session", "browser-a"),
            action="workspace.read",
            subject=SubjectRef("session", "browser-a"),
            scope=FAMILY,
        ).decision
        == "allow"
    )

    with pytest.raises(PersonalizationAccessError, match="not pending"):
        service.claim_invite(
            "guest-class",
            accepted_by=SubjectRef("session", "browser-c"),
            expected_scope=FAMILY,
            session_id="browser-c",
        )
    with pytest.raises(PersonalizationAccessError, match="personal profile"):
        service.create_guest_join_link(
            invite_id="guest-profile-block",
            scope=FAMILY,
            issued_by=OWNER,
            expires_at=9999999999.0,
        )
        service.claim_invite("guest-profile-block", accepted_by=MASHA, expected_scope=FAMILY)

    service.revoke_guest_join_sessions("guest-class", actor=OWNER, reason="class ended")
    denied = service.evaluate(
        actor=SubjectRef("session", "browser-a"),
        action="workspace.read",
        subject=SubjectRef("session", "browser-a"),
        scope=FAMILY,
    )

    assert store.get_session("browser-a")["status"] == "revoked"
    assert store.get_session("browser-b")["status"] == "revoked"
    assert denied.reason_code == "inactive_session"
    assert denied_links == ["browser-a", "browser-b"]
    assert store.list_audit(event_type="invite.revoked")


def test_phase3_targeted_invite_is_one_time_scoped_and_auditable(tmp_path) -> None:
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)
    other_scope = ScopeRef("workspace", "school")

    with pytest.raises(PersonalizationAccessError, match="requires expires_at"):
        service.create_targeted_invite_link(
            invite_id="invite-no-expiry",
            scope=FAMILY,
            role="member",
            issued_by=OWNER,
            profile_hint="masha",
            expires_at=None,
        )
    service.create_targeted_invite_link(
        invite_id="invite-masha",
        scope=FAMILY,
        role="member",
        issued_by=OWNER,
        profile_hint="masha",
        expires_at=9999999999.0,
    )

    with pytest.raises(PersonalizationAccessError, match="scope mismatch"):
        service.preview_invite("invite-masha", expected_scope=other_scope)

    preview = service.preview_invite("invite-masha", expected_scope=FAMILY)
    accepted = service.claim_invite("invite-masha", accepted_by=MASHA, expected_scope=FAMILY)

    assert preview["profile_hint"] == "masha"
    assert accepted["status"] == "accepted"
    assert store.iter_memberships()[0]["subject"] == MASHA.to_dict()
    assert store.iter_grants()[0]["metadata"]["invite_id"] == "invite-masha"
    assert service.list_audit(subject=MASHA, event_type="invite.accepted")

    with pytest.raises(PersonalizationAccessError, match="not pending"):
        service.claim_invite("invite-masha", accepted_by=MASHA, expected_scope=FAMILY)


def test_phase3_owner_can_bind_unknown_session_to_existing_profile(tmp_path) -> None:
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)
    service.put_session(SessionKey(session_id="join-session", key_id="join-key"))

    bound = service.bind_session_to_profile(
        session_id="join-session",
        subject=MASHA,
        actor=OWNER,
        profile=UserProfile(user_id="masha", preferred_name="Masha"),
    )

    assert bound["subject"] == MASHA.to_dict()
    assert store.get_user("masha")["subject"] == MASHA.to_dict()
    assert store.get_profile("masha")["preferred_name"] == "Masha"
    assert service.list_audit(subject=MASHA, event_type="session.bound")
