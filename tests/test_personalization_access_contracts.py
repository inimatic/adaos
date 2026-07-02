from __future__ import annotations

import pytest

from adaos.domain.personalization_access import (
    PERSONALIZATION_ACCESS_CONTRACT_VERSION,
    AuditRecord,
    Grant,
    GrantConstraint,
    Invite,
    PersonalizationAccessContractError,
    PolicyDecision,
    ScopeRef,
    SubjectRef,
    UserProfile,
    personalization_access_contract_snapshot,
)


def test_personalization_access_contract_snapshot_covers_phase0_terms() -> None:
    snapshot = personalization_access_contract_snapshot(now=42.0)

    assert snapshot["contract"] == PERSONALIZATION_ACCESS_CONTRACT_VERSION
    assert snapshot["ts"] == 42.0
    assert "UserProfile" in snapshot["schemas"]
    assert "Grant" in snapshot["schemas"]
    assert "Invite" in snapshot["schemas"]
    assert "ExternalIdentityBinding" in snapshot["schemas"]
    assert "scope_lattice" in snapshot
    assert {"kind": "subnet", "parent": None} in snapshot["scope_lattice"]
    assert {"kind": "user_private", "parent": "subnet"} in snapshot["scope_lattice"]
    assert "guest_join_link" in snapshot["join_flows"]
    assert snapshot["join_flows"]["guest_join_link"]["profile_binding_allowed"] is False
    assert "profile.read.self" in snapshot["capabilities"]
    assert "devices.add.self" in snapshot["role_presets"]["member"]
    assert "public_guest_join_cannot_bind_profile" in snapshot["security_regression_matrix"]


def test_user_profile_rejects_access_policy_fields() -> None:
    profile = UserProfile(
        user_id="masha",
        preferred_name="Masha",
        locale="ru-RU",
        settings={"theme": "dark"},
    )

    assert profile.to_dict()["settings"] == {"theme": "dark"}
    assert "role" not in profile.to_dict()

    with pytest.raises(PersonalizationAccessContractError, match="profile settings cannot contain"):
        UserProfile(user_id="masha", settings={"role": "owner"})


def test_guest_join_invite_cannot_bind_profile() -> None:
    owner = SubjectRef("user", "owner")
    workspace = ScopeRef("workspace", "family")

    invite = Invite(
        invite_id="guest-1",
        kind="guest_join_link",
        scope=workspace,
        role="guest",
        issued_by=owner,
        single_use=False,
        max_sessions=20,
    )

    assert invite.to_dict()["kind"] == "guest_join_link"
    assert invite.to_dict()["role"] == "guest"

    with pytest.raises(PersonalizationAccessContractError, match="guest_join_link cannot bind"):
        Invite(
            invite_id="guest-2",
            kind="guest_join_link",
            scope=workspace,
            role="guest",
            issued_by=owner,
            profile_hint="masha",
        )

    with pytest.raises(PersonalizationAccessContractError, match="must use guest role"):
        Invite(
            invite_id="guest-3",
            kind="guest_join_link",
            scope=workspace,
            role="member",
            issued_by=owner,
        )


def test_grant_constraints_normalize_capabilities_and_scopes() -> None:
    grant = Grant(
        grant_id="grant-1",
        subject=SubjectRef("user", "masha"),
        scope=ScopeRef("workspace", "family"),
        role="member",
        capabilities=("profile.read.self", "devices.add.self"),
        constraints=GrantConstraint(
            requires_approval_for=("tools.invoke.browser_automation",),
            allowed_scopes=(ScopeRef("workspace", "family"),),
            delegation=("devices.add.self",),
        ),
        issued_by=SubjectRef("user", "owner"),
        created_at=100.0,
    )

    data = grant.to_dict()
    assert data["subject"] == {"kind": "user", "id": "masha"}
    assert data["scope"] == {"kind": "workspace", "id": "family"}
    assert data["capabilities"] == ["profile.read.self", "devices.add.self"]
    assert data["constraints"]["requires_approval_for"] == ["tools.invoke.browser_automation"]
    assert data["constraints"]["allowed_scopes"] == [{"kind": "workspace", "id": "family"}]

    with pytest.raises(PersonalizationAccessContractError, match="capabilities must be unique"):
        Grant(
            grant_id="grant-dup",
            subject=SubjectRef("user", "masha"),
            scope=ScopeRef("workspace", "family"),
            capabilities=("profile.read.self", "profile.read.self"),
        )


def test_policy_decision_and_audit_record_are_redaction_ready() -> None:
    actor = SubjectRef("user", "owner")
    subject = SubjectRef("user", "masha")
    scope = ScopeRef("workspace", "family")
    decision = PolicyDecision(
        decision="deny",
        actor=actor,
        action="tools.invoke.browser_automation",
        subject=subject,
        scope=scope,
        reason_code="missing_capability",
        grant_ids=("grant-1",),
        trace_id="trace-1",
    )
    audit = AuditRecord(
        audit_id="audit-1",
        event_type="tool.invocation.denied",
        actor=actor,
        subject=subject,
        scope=scope,
        source="policy",
        decision=decision,
        redacted_diff={"preferred_name": {"old": "<redacted>", "new": "<redacted>"}},
        metadata={"private_content": False},
        ts=123.0,
        trace_id="trace-1",
    )

    data = audit.to_dict()
    assert data["event_type"] == "tool.invocation.denied"
    assert data["decision"]["decision"] == "deny"
    assert data["decision"]["reason_code"] == "missing_capability"
    assert data["redacted_diff"]["preferred_name"]["old"] == "<redacted>"
    assert data["metadata"] == {"private_content": False}
