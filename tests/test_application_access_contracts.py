from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.application_access import (
    ApplicationAccessContractError,
    ApplicationAccessGrant,
    ApplicationPermissionProfile,
    build_application_verification_report,
    classify_access_profile_diff,
    evaluate_application_access,
    normalize_application_roles,
)


ABI_ROOT = Path(__file__).parents[1] / "src" / "adaos" / "abi"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _profile() -> ApplicationPermissionProfile:
    return ApplicationPermissionProfile.from_mapping(
        {
            "schema": "adaos.application.permission_profile.v1",
            "required": [
                {
                    "id": "workspace.write",
                    "purpose": "Save tasks",
                    "authorization_details": {
                        "actions": ["read", "write"],
                        "resources": ["application_source"],
                    },
                },
                {
                    "id": "llm.generate",
                    "purpose": "Suggest task descriptions",
                },
            ],
            "optional": [
                {
                    "id": "notifications.send",
                    "purpose": "Notify assignees",
                    "approval_policy": "ask_in_context",
                }
            ],
            "secrets": [
                {
                    "id": "github_token",
                    "provider": "github",
                    "scopes": ["contents.read"],
                    "purpose": "Import release metadata",
                    "required": False,
                }
            ],
            "data_practices": {
                "collected": ["user_content", "usage_data"],
                "sent_off_device": ["project_metadata"],
                "linked_to_user": ["usage_data"],
                "tracking": False,
            },
            "llm_model_use": [{"id": "task_copy", "provider": "root_llm_proxy"}],
            "notifications": [{"id": "task_completed"}],
            "background_actions": [{"id": "daily_digest"}],
            "external_providers": [{"id": "github"}],
        }
    )


def _roles(profile: ApplicationPermissionProfile):
    return normalize_application_roles(
        [
            {
                "id": "viewer",
                "title": "Viewer",
                "grants": ["app.view"],
                "assignable_to": ["owner", "member", "child", "guest"],
                "default_for": {"guest": "viewer"},
            },
            {
                "id": "editor",
                "title": "Editor",
                "grants": ["app.view", "app.write"],
                "assignable_to": ["owner", "member"],
                "requires_permissions": ["workspace.write"],
            },
        ],
        known_permissions=profile.flat_permissions,
    )


def test_permission_profile_normalizes_digest_privacy_and_legacy_projection() -> None:
    profile = _profile()
    restored = ApplicationPermissionProfile.from_mapping(profile.to_dict())
    legacy = ApplicationPermissionProfile.from_mapping(None, legacy_permissions=["workspace.read"])

    assert restored == profile
    assert profile.digest.startswith("sha256:")
    assert profile.flat_permissions == ("llm.generate", "notifications.send", "workspace.write")
    assert profile.privacy_labels["sent_off_device"] is True
    assert legacy.flat_permissions == ("workspace.read",)
    assert legacy.to_dict()["required"][0]["purpose"] == "Legacy ProjectRelease permission"


def test_application_roles_validate_permissions_and_diff_update_impact() -> None:
    old_profile = ApplicationPermissionProfile.from_mapping(None, legacy_permissions=["workspace.read"])
    new_profile = _profile()
    roles = _roles(new_profile)

    diff = classify_access_profile_diff(
        old_profile,
        new_profile,
        old_roles=(),
        new_roles=roles,
        existing_role_assignments={"editor": ["user:masha"]},
    )

    assert "workspace.write" in diff["permission_changes"]["sensitive_added"]
    assert diff["role_changes"]["added"] == ["editor", "viewer"]

    with pytest.raises(ApplicationAccessContractError, match="unknown permissions"):
        normalize_application_roles(
            [
                {
                    "id": "bad",
                    "title": "Bad",
                    "grants": ["app.write"],
                    "requires_permissions": ["network.egress"],
                }
            ],
            known_permissions=new_profile.flat_permissions,
        )

    with pytest.raises(ApplicationAccessContractError, match="unknown permissions"):
        normalize_application_roles(
            [
                {
                    "id": "orphan",
                    "title": "Orphan",
                    "grants": ["app.view"],
                    "requires_permissions": ["workspace.read"],
                }
            ],
            known_permissions=(),
        )


def test_application_access_decision_intersects_profile_grant_role_component_and_floors() -> None:
    profile = _profile()
    roles = _roles(profile)
    grant = ApplicationAccessGrant(
        grant_id="appgrant.editor",
        subject_ref="user:masha",
        application_id="family_tasks",
        application_roles=("editor",),
        permission_ceiling=profile.flat_permissions,
        explicit_denies=(),
        constraints={"subject_kind": "user"},
        issuer_ref="user:owner",
        reviewed_permission_profile_digest=profile.digest,
    )

    allowed = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=grant,
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=["workspace.write"],
        actor_chain={"application_id": "family_tasks", "subject_ref": "user:masha"},
    )

    assert allowed.decision == "allow"
    assert allowed.reason_code == "allowed"

    missing_component = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=grant,
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=["workspace.read"],
        actor_chain={"application_id": "family_tasks", "subject_ref": "user:masha"},
    )
    assert missing_component.decision == "deny"
    assert missing_component.reason_code == "component_capability_missing"

    unverified_component = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=grant,
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=(),
        actor_chain={"application_id": "family_tasks", "subject_ref": "user:masha"},
    )
    assert unverified_component.decision == "deny"
    assert unverified_component.reason_code == "component_capability_unverified"

    role_required_missing = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=ApplicationAccessGrant(
            grant_id="appgrant.partial",
            subject_ref="user:masha",
            application_id="family_tasks",
            application_roles=("editor",),
            permission_ceiling=("llm.generate",),
            explicit_denies=(),
            constraints={"subject_kind": "user"},
            issuer_ref="user:owner",
            reviewed_permission_profile_digest=profile.digest,
        ),
        permission_id="llm.generate",
        app_capability="app.write",
        component_capabilities=("llm.generate",),
        actor_chain={"application_id": "family_tasks", "subject_ref": "user:masha"},
    )
    assert role_required_missing.decision == "pending_action"
    assert role_required_missing.reason_code == "role_required_permission_missing"

    scoped_grant = ApplicationAccessGrant(
        grant_id="appgrant.scoped",
        subject_ref="user:masha",
        application_id="family_tasks",
        application_roles=("editor",),
        permission_ceiling=("workspace.write",),
        explicit_denies=(),
        constraints={
            "subject_kind": "user",
            "webspace_id": "desktop",
            "resource_scope": ["resource:task/1"],
            "requires_trusted_device": True,
        },
        issuer_ref="user:owner",
        reviewed_permission_profile_digest=profile.digest,
    )
    scoped_denied = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=scoped_grant,
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=("workspace.write",),
        actor_chain={
            "application_id": "family_tasks",
            "subject_ref": "user:masha",
            "webspace_id": "desktop",
            "resource_ref": "resource:task/2",
            "device_trusted": True,
        },
    )
    assert scoped_denied.decision == "deny"
    assert scoped_denied.reason_code == "resource_scope_mismatch"

    device_pending = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=scoped_grant,
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=("workspace.write",),
        actor_chain={
            "application_id": "family_tasks",
            "subject_ref": "user:masha",
            "webspace_id": "desktop",
            "resource_ref": "resource:task/1",
        },
    )
    assert device_pending.decision == "pending_action"
    assert device_pending.reason_code == "device_trust_required"

    guest = ApplicationAccessGrant(
        grant_id="appgrant.guest",
        subject_ref="session:guest-1",
        application_id="family_tasks",
        application_roles=("viewer",),
        permission_ceiling=profile.flat_permissions,
        explicit_denies=(),
        constraints={"subject_kind": "guest"},
        issuer_ref="user:owner",
        reviewed_permission_profile_digest=profile.digest,
    )
    guest_llm = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=guest,
        permission_id="llm.generate",
        app_capability="app.view",
        component_capabilities=("llm.generate",),
        actor_chain={"application_id": "family_tasks", "subject_ref": "session:guest-1"},
    )
    assert guest_llm.decision == "deny"
    assert guest_llm.reason_code == "guest_floor_denied"

    child = ApplicationAccessGrant(
        grant_id="appgrant.child",
        subject_ref="child:masha",
        application_id="family_tasks",
        application_roles=("viewer",),
        permission_ceiling=profile.flat_permissions,
        explicit_denies=(),
        constraints={"subject_kind": "child"},
        issuer_ref="user:owner",
        reviewed_permission_profile_digest=profile.digest,
    )
    child_llm = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=child,
        permission_id="llm.generate",
        app_capability="app.view",
        component_capabilities=("llm.generate",),
        actor_chain={"application_id": "family_tasks", "subject_ref": "child:masha"},
    )
    assert child_llm.decision == "pending_action"
    assert child_llm.reason_code == "guardian_approval_required"
    assert child_llm.actor_chain["application_id"] == "family_tasks"
    assert "device_ref" in child_llm.actor_chain


def test_verification_report_blocks_undeclared_high_risk_and_round_trips() -> None:
    profile = _profile()
    roles = _roles(profile)
    failed = build_application_verification_report(
        application_id="family_tasks",
        release_digest=DIGEST_A,
        source_commit=COMMIT,
        profile=profile,
        roles=roles,
        observed_capabilities=["workspace.write", "network.egress"],
        regression_evidence=["artifacts/tests.xml"],
        access_matrix_evidence=["artifacts/access.json"],
        pending_action_fallbacks=["artifacts/pending.json"],
        audit_evidence=["artifacts/audit.json"],
    )

    assert failed.overall == "failed"
    assert failed.to_dict()["report_digest"].startswith("sha256:")
    assert any(
        item["id"] == "permissions.declared_vs_observed" and item["result"] == "failed"
        for item in failed.to_dict()["checks"]
    )

    passed = build_application_verification_report(
        application_id="family_tasks",
        release_digest=DIGEST_B,
        source_commit=COMMIT,
        profile=profile,
        roles=roles,
        observed_capabilities=profile.flat_permissions,
        regression_evidence=["artifacts/tests.xml"],
        access_matrix_evidence=["artifacts/access.json"],
        pending_action_fallbacks=["artifacts/pending.json"],
        audit_evidence=["artifacts/audit.json"],
    )

    assert passed.overall == "passed"
    assert type(passed).from_mapping(passed.to_dict()) == passed


def test_application_access_payloads_validate_against_abi_schemas() -> None:
    profile = _profile()
    roles = _roles(profile)
    grant = ApplicationAccessGrant(
        grant_id="appgrant.editor",
        subject_ref="user:masha",
        application_id="family_tasks",
        application_roles=("editor",),
        permission_ceiling=profile.flat_permissions,
        explicit_denies=(),
        constraints={"subject_kind": "user"},
        issuer_ref="user:owner",
        reviewed_permission_profile_digest=profile.digest,
    )
    decision = evaluate_application_access(
        profile=profile,
        roles=roles,
        grant=grant,
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=("workspace.write",),
        actor_chain={"application_id": "family_tasks", "subject_ref": "user:masha"},
    )
    diff = classify_access_profile_diff(
        ApplicationPermissionProfile.from_mapping(None, legacy_permissions=["workspace.read"]),
        profile,
        new_roles=roles,
    )
    report = build_application_verification_report(
        application_id="family_tasks",
        release_digest=DIGEST_A,
        source_commit=COMMIT,
        profile=profile,
        roles=roles,
        observed_capabilities=profile.flat_permissions,
        regression_evidence=["artifacts/tests.xml"],
        access_matrix_evidence=["artifacts/access.json"],
        pending_action_fallbacks=["artifacts/pending.json"],
        audit_evidence=["artifacts/audit.json"],
    )

    cases = {
        "application.permission-profile.v1.schema.json": profile.to_dict(),
        "application.access-grant.v1.schema.json": grant.to_dict(),
        "application.access-decision.v1.schema.json": decision.to_dict(),
        "application.access-profile-diff.v1.schema.json": diff,
        "application.verification-report.v1.schema.json": report.to_dict(),
    }
    for schema_name, payload in cases.items():
        schema = json.loads((ABI_ROOT / schema_name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(payload)
