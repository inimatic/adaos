import pytest

from adaos.domain.personalization_access import (
    DeviceKey,
    Grant,
    GrantConstraint,
    Invite,
    Membership,
    RecoveryAction,
    ScopeRef,
    SessionKey,
    SubjectRef,
    UserKey,
    UserProfile,
)
from adaos.services.personalization_access import (
    PersonalizationAccessError,
    PersonalizationAccessService,
    PersonalizationAccessStore,
)


OWNER = SubjectRef("user", "owner")
MASHA = SubjectRef("user", "masha")
FAMILY = ScopeRef("workspace", "family")


def test_phase1_policy_grant_revoke_and_audit_roundtrip(tmp_path):
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)

    service.put_user(MASHA, actor=OWNER)
    service.put_profile(UserProfile(user_id="masha", preferred_name="Masha"), actor=OWNER)
    service.put_grant(
        Grant(
            grant_id="grant-1",
            subject=MASHA,
            scope=FAMILY,
            capabilities=("profile.read.self",),
            issued_by=OWNER,
        ),
        actor=OWNER,
    )

    owner_decision = service.evaluate(actor=OWNER, action="users.manage", subject=MASHA, scope=FAMILY)
    allow_decision = service.evaluate(actor=MASHA, action="profile.read.self", subject=MASHA, scope=FAMILY)
    deny_decision = service.evaluate(actor=MASHA, action="users.invite", subject=MASHA, scope=FAMILY)

    assert owner_decision.decision == "allow"
    assert owner_decision.reason_code == "owner_implicit_subnet_admin"
    assert allow_decision.decision == "allow"
    assert allow_decision.grant_ids == ("grant-1",)
    assert deny_decision.decision == "deny"
    assert deny_decision.reason_code == "missing_capability"

    service.revoke_grant("grant-1", actor=OWNER, reason="test")
    revoked_decision = service.evaluate(actor=MASHA, action="profile.read.self", subject=MASHA, scope=FAMILY)

    assert revoked_decision.decision == "deny"
    assert store.list_audit(actor=OWNER, decision="allow")
    assert store.list_audit(subject=MASHA, event_type="grant.revoked")

    reloaded = PersonalizationAccessStore(tmp_path / "access.json")
    assert reloaded.get_user("masha")["subject"] == MASHA.to_dict()
    assert reloaded.get_grant("grant-1")["status"] == "revoked"
    assert reloaded.list_audit(decision="deny", limit=10)


def test_phase1_existing_local_owner_baseline_has_implicit_admin_without_ui_state(tmp_path):
    local_owner = SubjectRef("user", "local-owner")
    service = PersonalizationAccessService(
        PersonalizationAccessStore(tmp_path / "access.json"),
        owner=local_owner,
    )

    owner_decision = service.evaluate(actor=local_owner, action="subnet.admin", scope=ScopeRef("subnet", "local"))
    other_decision = service.evaluate(actor=MASHA, action="subnet.admin", scope=ScopeRef("subnet", "local"))

    assert owner_decision.decision == "allow"
    assert owner_decision.reason_code == "owner_implicit_subnet_admin"
    assert other_decision.decision == "deny"


def test_phase1_membership_role_preset_allows_session_until_device_revoked(tmp_path):
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)
    session = SubjectRef("session", "phone-session")

    service.put_user_key(
        UserKey(user_id="masha", key_id="masha-key", public_key_ref="pk:masha"),
        actor=OWNER,
    )
    service.put_device_key(
        DeviceKey(user_id="masha", device_id="phone", key_id="phone-key", public_key_ref="pk:phone"),
        actor=OWNER,
    )
    service.put_session(
        SessionKey(session_id="phone-session", key_id="phone-key", subject=MASHA, device_id="phone"),
    )
    service.put_membership(Membership(subject=MASHA, scope=FAMILY, role="member", issued_by=OWNER), actor=OWNER)

    allow_decision = service.evaluate(actor=session, action="devices.add.self", subject=MASHA, scope=FAMILY)
    deny_decision = service.evaluate(actor=session, action="users.invite", subject=MASHA, scope=FAMILY)

    assert allow_decision.decision == "allow"
    assert allow_decision.actor == session
    assert deny_decision.decision == "deny"

    service.revoke_device("phone", actor=OWNER, reason="lost")
    revoked_session = store.get_session("phone-session")
    revoked_device_decision = service.evaluate(actor=session, action="devices.add.self", subject=MASHA, scope=FAMILY)

    assert revoked_session["status"] == "revoked"
    assert revoked_device_decision.decision == "deny"
    assert revoked_device_decision.reason_code == "inactive_session"
    assert service.list_audit(device=SubjectRef("device", "phone"), source="personalization_access")


def test_phase1_approval_constraint_blocks_dangerous_tool_until_approved(tmp_path):
    service = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / "access.json"), owner=OWNER)
    service.put_grant(
        Grant(
            grant_id="grant-tool",
            subject=MASHA,
            scope=FAMILY,
            capabilities=("tools.invoke.browser_automation",),
            constraints=GrantConstraint(requires_approval_for=("tools.invoke.browser_automation",)),
            issued_by=OWNER,
        ),
        actor=OWNER,
    )

    denied = service.evaluate(
        actor=MASHA,
        action="tools.invoke.browser_automation",
        subject=MASHA,
        scope=FAMILY,
    )
    allowed = service.evaluate(
        actor=MASHA,
        action="tools.invoke.browser_automation",
        subject=MASHA,
        scope=FAMILY,
        context={"approval_id": "approval-1"},
    )

    assert denied.decision == "deny"
    assert denied.reason_code == "approval_required"
    assert allowed.decision == "allow"
    assert service.list_audit(subject=MASHA, decision="deny", event_type="policy.deny")


def test_phase1_invite_claim_rejects_reuse_and_expired_material(tmp_path):
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)

    service.put_invite(
        Invite(
            invite_id="invite-masha",
            kind="targeted_invite_link",
            scope=FAMILY,
            role="member",
            issued_by=OWNER,
            profile_hint="masha",
            expires_at=9999999999.0,
        )
    )
    accepted = service.claim_invite("invite-masha", accepted_by=MASHA)

    assert accepted["status"] == "accepted"
    with pytest.raises(PersonalizationAccessError, match="not pending"):
        service.claim_invite("invite-masha", accepted_by=MASHA)

    service.put_invite(
        Invite(
            invite_id="invite-expired",
            kind="targeted_invite_link",
            scope=FAMILY,
            role="member",
            issued_by=OWNER,
            profile_hint="masha",
            expires_at=1.0,
        )
    )

    with pytest.raises(PersonalizationAccessError, match="expired"):
        service.claim_invite("invite-expired", accepted_by=MASHA)
    assert store.get_invite("invite-expired")["status"] == "expired"

    with pytest.raises(PersonalizationAccessError, match="not mutable"):
        service.put_invite(
            Invite(
                invite_id="invite-expired",
                kind="targeted_invite_link",
                scope=FAMILY,
                role="member",
                issued_by=OWNER,
                profile_hint="masha",
                expires_at=9999999999.0,
            )
        )


def test_phase1_recovery_completion_rejects_replay(tmp_path):
    store = PersonalizationAccessStore(tmp_path / "access.json")
    service = PersonalizationAccessService(store, owner=OWNER)

    service.put_recovery_action(
        RecoveryAction(
            recovery_id="recovery-masha-phone",
            subject=MASHA,
            issued_by=OWNER,
            replacement_device_id="phone-2",
            revoked_device_ids=("phone-1",),
        )
    )
    completed = service.complete_recovery_action("recovery-masha-phone", actor=OWNER)

    assert completed["status"] == "accepted"
    with pytest.raises(PersonalizationAccessError, match="not pending"):
        service.complete_recovery_action("recovery-masha-phone", actor=OWNER)
    assert store.list_audit(subject=MASHA, event_type="recovery.completed")
