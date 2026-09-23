from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.application import (
    Application,
    ApplicationInstallation,
    ApplicationRelease,
    RuntimeSelection,
)
from adaos.domain.application_access import ApplicationPermissionProfile
from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectCompositionLock,
    ProjectMemberLock,
    ProjectRelease,
    canonical_payload_digest,
)
from adaos.services.applications import ApplicationService, ApplicationStore
from adaos.services.applications.access import ApplicationAccessError
from adaos.services.applications.access_management import (
    ROLE_TEMPLATES,
    ApplicationAccessManagementService,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def test_runtime_permission_supports_simple_and_domain_role_actions() -> None:
    simple = ApplicationAccessManagementService.runtime_permission(
        side_effects="read_only",
        application_access={},
        component_capabilities=("workspace.read",),
    )
    domain = ApplicationAccessManagementService.runtime_permission(
        side_effects="local_write",
        application_access={
            "permission": "workspace.write",
            "capability": "roster.manage",
        },
        component_capabilities=("workspace.write",),
    )

    assert simple == ("workspace.read", "workspace.read")
    assert domain == ("workspace.write", "roster.manage")


def _profile(*, updated: bool = False) -> ApplicationPermissionProfile:
    required = [
        {"id": "workspace.read", "purpose": "Read assigned household tasks."},
        {"id": "workspace.write", "purpose": "Complete assigned household tasks."},
        {"id": "llm.generate", "purpose": "Suggest task wording."},
        {"id": "network.egress", "purpose": "Synchronize the household calendar."},
        {"id": "secrets.use", "purpose": "Use the connected calendar credential."},
    ]
    if updated:
        required.append(
            {"id": "notifications.send", "purpose": "Notify task assignees."}
        )
    return ApplicationPermissionProfile.from_mapping(
        {
            "schema": "adaos.application.permission_profile.v1",
            "required": required,
            "optional": [
                {"id": "background.run", "purpose": "Refresh due dates overnight."}
            ],
            "secrets": [
                {
                    "id": "calendar_token",
                    "provider": "calendar",
                    "scopes": ["calendar.read"],
                    "purpose": "Read shared calendar due dates.",
                    "required": False,
                    "binding": "user_or_app",
                }
            ],
            "data_practices": {
                "collected": ["task_metadata"],
                "sent_off_device": ["task_metadata"],
                "linked_to_user": ["task_metadata"],
                "tracking": False,
                "retention": "until_task_deleted",
            },
            "llm_model_use": [{"id": "task_wording", "provider": "root_llm_proxy"}],
            "notifications": [{"id": "task_due"}] if updated else [],
            "background_actions": [{"id": "due_date_refresh"}],
            "external_providers": [
                {
                    "id": "calendar",
                    "destination": "api.calendar.example",
                    "scopes": ["calendar.read"],
                }
            ],
        }
    )


def _roles(*, updated: bool = False) -> tuple[dict[str, object], ...]:
    editor_grants = ["application.use", "task.read", "task.complete"]
    if updated:
        editor_grants.append("task.assign")
    return (
        {
            "id": "viewer",
            "title": "Viewer",
            "grants": ["application.use", "task.read"],
            "assignable_to": ["owner", "member", "child", "guest"],
            "default_for": {"guest": "viewer"},
            "requires_permissions": ["workspace.read"],
        },
        {
            "id": "editor",
            "title": "Editor",
            "grants": editor_grants,
            "assignable_to": ["owner", "member"],
            "requires_permissions": ["workspace.read", "workspace.write"],
            "sensitive": updated,
        },
    )


def _application() -> Application:
    return Application(
        application_id="family_tasks",
        legacy_project_id="family_tasks",
        publisher_ref="subnet:home",
        slug="family_tasks",
        display={"title": "Family Tasks", "summary": "Shared household tasks"},
        visibility="private",
        entrypoints=(
            {"entrypoint_id": "main", "presentation_ref": "scenario:family_tasks"},
        ),
        publisher={
            "publisher_ref": "subnet:home",
            "display_name": "Home",
            "subnet_short_ref": "home",
            "release_key_ref": "subnet-key:release-signing:1",
            "release_key_fingerprint": DIGEST_C,
            "home_zone": "local-dev",
            "trust_relation": "local",
        },
    )


def _release(
    service: ApplicationService,
    *,
    version: str = "1.0.0",
    updated: bool = False,
) -> ApplicationRelease:
    profile = _profile(updated=updated)
    roles = _roles(updated=updated)
    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/family_tasks",
        revision=SOURCE_COMMIT,
        path_scope=("projects/family_tasks/",),
    )
    package = ArtifactPackageRef(
        kind="skill",
        artifact_id="family_tasks_skill",
        version=version,
        digest=DIGEST_B if updated else DIGEST_A,
        manifest_digest=DIGEST_C if updated else DIGEST_B,
        source_ref=source,
    )
    composition = ProjectCompositionLock(
        project_definition_digest=canonical_payload_digest(
            {"id": "family_tasks", "version": version, "profile": profile.to_dict()}
        ),
        profiles=("adaos.application.v1",),
        members=(
            ProjectMemberLock(
                ref=package.key,
                package_digest=package.digest,
                role="primary",
                exposure="application",
                lifecycle="bound",
                relations=("realizes",),
            ),
        ),
        project_dependencies=(),
        entrypoints=(),
        compatibility={},
        lifecycle={},
        permission_profile=profile.to_dict(),
        application_roles=roles,
    )
    project = ProjectRelease(
        project_id="family_tasks",
        version=version,
        source_ref=source,
        components=(package,),
        permissions=profile.flat_permissions,
        validation_evidence=({"status": "passed", "suite": "access-e2e"},),
        composition_lock=composition,
    ).seal()
    return service.register_release(
        ApplicationRelease(
            application_id="family_tasks",
            publisher_ref="subnet:home",
            project_release=project,
            accepted_candidate_id=f"candidate.family_tasks.{version}",
            acceptance_evidence=({"status": "accepted"},),
            provenance_refs=(DIGEST_C,),
            lifecycle="prerelease" if updated else "trial",
        )
    )


def _services(
    tmp_path: Path,
) -> tuple[ApplicationService, ApplicationAccessManagementService, ApplicationRelease]:
    applications = ApplicationService(ApplicationStore(tmp_path / "state"))
    applications.register(_application())
    release = _release(applications)
    management = ApplicationAccessManagementService(applications)
    return applications, management, release


def _expires(hours: int = 2) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _verification(
    management: ApplicationAccessManagementService,
    release: ApplicationRelease,
    *,
    scope: str,
    observed: tuple[str, ...] | None = None,
) -> dict[str, object]:
    capabilities = observed or release.permission_profile.flat_permissions
    return management.final_verification(
        release.application_id,
        release_digest=release.release_digest,
        source_commit=SOURCE_COMMIT,
        observed_capabilities=capabilities,
        inferred_capabilities=release.permission_profile.flat_permissions,
        regression_evidence=(
            "suite:release:application-access"
            if scope == "publication"
            else "pytest:tests/test_application_access_management.py",
        ),
        access_matrix_evidence=("e2e:owner-member-child-guest",),
        pending_action_evidence=("e2e:pending-action-keyboard-and-voice",),
        audit_evidence=("audit:application-access",),
        disclosure_evidence=("browser:application-permission-review",),
        redaction_evidence=("test:connected-account-redaction",),
        release_scope=scope,
        actor_ref="user:builder",
    )


def test_project_release_binds_structured_application_contract(tmp_path: Path) -> None:
    _, _, release = _services(tmp_path)

    restored = ApplicationRelease.from_mapping(release.to_dict())

    assert restored.permission_profile == release.permission_profile
    assert [item.role_id for item in restored.application_roles] == ["editor", "viewer"]
    assert restored.project_release.composition_lock is not None
    assert (
        restored.project_release.composition_lock.permission_profile
        == _profile().to_dict()
    )


def test_access_surface_uses_latest_release_without_installation_or_channel(
    tmp_path: Path,
) -> None:
    _, management, release = _services(tmp_path)

    surface = management.application_detail("family_tasks")

    assert surface["release"]["release_digest"] == release.release_digest
    assert surface["installation"] is None


def test_trial_runtime_context_resolves_without_stable_installation(
    tmp_path: Path,
) -> None:
    applications, management, release = _services(tmp_path)
    applications.store.save_runtime_selection(
        RuntimeSelection(
            webspace_id="desktop",
            application_id=release.application_id,
            source="local_trial",
            release_digest=release.release_digest,
            runtime_root_ref="trial://family-tasks",
            revision=1,
        ),
        expected_revision=0,
    )

    resolved = management.resolve_runtime_context(
        skill_name="family_tasks_skill",
        requested_application_id="family_tasks",
        webspace_id="desktop",
    )

    assert resolved is not None
    assert resolved["installation_revision"] is None
    assert resolved["runtime_selection"]["source"] == "local_trial"


def test_applications_and_users_access_share_grants_roles_and_redacted_accounts(
    tmp_path: Path,
) -> None:
    applications, management, release = _services(tmp_path)
    applications.store.save_installation(
        ApplicationInstallation(
            installation_id="installation:family_tasks",
            application_id="family_tasks",
            installed_release_digest=release.release_digest,
            component_refs=(
                {
                    "component_ref": "skill:family_tasks_skill",
                    "package_digest": DIGEST_A,
                    "lifecycle": "bound",
                },
            ),
            data_policy="retain",
            status="active",
            revision=1,
        ),
        expected_revision=0,
    )
    member = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="user:masha",
        application_roles=("editor",),
        permission_ceiling=("workspace.read", "workspace.write"),
        issuer_ref="user:owner",
        idempotency_key="member-editor",
        constraints={"platform_role": "member"},
    )
    child = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="child:petya",
        application_roles=("viewer",),
        permission_ceiling=("workspace.read",),
        issuer_ref="user:owner",
        idempotency_key="child-viewer",
        constraints={
            "platform_role": "child",
            "guardian_approval_id": "approval:guardian:1",
        },
    )
    guest = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="session:guest-1",
        application_roles=("viewer",),
        permission_ceiling=("workspace.read",),
        issuer_ref="user:owner",
        idempotency_key="guest-viewer",
        constraints={"platform_role": "guest", "profile_binding": False},
        expires_at=_expires(),
    )
    account = management.put_connected_account(
        "family_tasks",
        {
            "release_digest": release.release_digest,
            "account_id": "calendar-masha",
            "provider_id": "calendar",
            "subject_ref": "user:masha",
            "mode": "delegated_user",
            "scopes": ["calendar.read"],
            "status": "connected",
            "token_expires_at": _expires(24),
            "secret_value": "must-never-be-persisted",
        },
        expected_revision=0,
    )

    application_view = management.application_detail("family_tasks")
    users_view = management.users_access(
        {
            "users": [
                {
                    "user_id": "sasha",
                    "subject": {"kind": "user", "id": "sasha"},
                }
            ],
            "profiles": [
                {
                    "user_id": "sasha",
                    "preferred_name": "Sasha",
                    "metadata_only": True,
                }
            ],
            "memberships": [
                {
                    "subject": {"kind": "user", "id": "sasha"},
                    "scope": {"kind": "workspace", "id": "home"},
                    "role": "child",
                    "status": "active",
                }
            ],
            "invites": [
                {
                    "invite_id": "guest-link-1",
                    "kind": "guest_join_link",
                    "status": "pending",
                    "single_use": True,
                    "max_sessions": 1,
                }
            ],
            "devices": [
                {
                    "device_id": "phone-1",
                    "status": "active",
                    "public_key": "must-not-be-projected",
                }
            ],
            "sessions": [
                {
                    "session_id": "session-1",
                    "device_id": "phone-1",
                    "status": "active",
                    "expires_at": 1_900_000_000,
                    "key_id": "invite:guest-link-1",
                    "subject": {"kind": "user", "id": "sasha"},
                    "tool_credential": {
                        "issued_at": 1_800_000_000,
                        "scope": {"kind": "skill", "id": "family_tasks_skill"},
                        "token_hash": "must-not-be-projected",
                    },
                }
            ],
        }
    )

    assert application_view["sections"]["activity_page"] == {
        "limit": 50,
        "has_more": False,
    }
    assert users_view["activity_page"] == {"limit": 50, "has_more": False}

    app_grant_ids = {
        item["grant_id"] for item in application_view["sections"]["access"]
    }
    user_grant_ids = {
        grant_value["grant_id"]
        for group in ("people", "children", "guests")
        for person in users_view[group]
        for grant_value in person["application_access"]
    }
    assert (
        app_grant_ids
        == user_grant_ids
        == {
            member.grant_id,
            child.grant_id,
            guest.grant_id,
        }
    )
    assert users_view["diagnostics"]["content_redacted"] is True
    assert users_view["devices"] == [{"device_id": "phone-1", "status": "active"}]
    assert users_view["sessions"] == [
        {
            "session_id": "session-1",
            "device_id": "phone-1",
            "status": "active",
            "expires_at": "2030-03-17T17:46:40+00:00",
            "subject_ref": "user:sasha",
            "scope_ref": "skill:family_tasks_skill",
            "opened_at": "2027-01-15T08:00:00+00:00",
            "authentication_source": "invitation",
        }
    ]
    assert "token_hash" not in json.dumps(users_view)
    assert "public_key" not in json.dumps(users_view)
    permission_rows = {
        item["permission_id"]: item for item in users_view["permissions"]
    }
    assert permission_rows["workspace.read"]["application_count"] == 1
    assert permission_rows["workspace.read"]["active_grant_count"] == 3
    assert permission_rows["workspace.read"]["applications"][0] == {
        "application_id": "family_tasks",
        "title": "Family Tasks",
        "requirement": "required",
        "purpose": "Read assigned household tasks.",
        "approval_policy": "grant_on_install",
        "active_grant_count": 3,
        "explicit_deny_count": 0,
    }
    assert any(item["subject_ref"] == "user:sasha" for item in users_view["children"])
    sasha = next(
        item for item in users_view["children"] if item["subject_ref"] == "user:sasha"
    )
    assert sasha["display_label"] == "Sasha"
    assert sasha["display_label_source"] == "profile"
    assert sasha["initials"] == "S"
    assert sasha["membership_summary"] == "child"
    assert sasha["membership_count"] == 1
    assert sasha["primary_role"] == "child"
    assert sasha["application_access_count"] == 0
    masha = next(
        item for item in users_view["people"] if item["subject_ref"] == "user:masha"
    )
    assert masha["display_label"] == "masha"
    assert masha["display_label_source"] == "subject_ref"
    assert masha["initials"] == "M"
    assert masha["application_access_count"] == 1
    assert any(
        item["subject_ref"] == "invite:guest-link-1" for item in users_view["guests"]
    )
    assert account["status"] == "connected"
    assert "secret_value" not in account
    persisted_accounts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (applications.store.root / "connected_accounts").glob("*.json")
    )
    assert "must-never-be-persisted" not in persisted_accounts
    with pytest.raises(ApplicationAccessError, match="scopes are not declared"):
        management.put_connected_account(
            "family_tasks",
            {
                "release_digest": release.release_digest,
                "account_id": "calendar-broad",
                "provider_id": "calendar",
                "subject_ref": "user:masha",
                "mode": "delegated_user",
                "scopes": ["calendar.write"],
                "status": "connected",
            },
            expected_revision=0,
        )
    assert {item["id"] for item in application_view["sections"]["roles"]} == {
        "editor",
        "viewer",
    }


def test_profiler_update_review_simulation_reviews_anomalies_and_snapshots(
    tmp_path: Path,
) -> None:
    applications, management, release = _services(tmp_path)
    grant = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="user:masha",
        application_roles=("editor",),
        permission_ceiling=("workspace.read", "workspace.write"),
        issuer_ref="user:owner",
        idempotency_key="member-editor",
        constraints={"platform_role": "member", "device_status": "stale"},
    )
    simulation = management.simulate(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="user:masha",
        permission_id="workspace.write",
        app_capability="task.complete",
        application_roles=("editor",),
        permission_ceiling=("workspace.read", "workspace.write"),
        constraints={"platform_role": "member"},
        component_capabilities=("workspace.write",),
    )
    assert simulation["decision"]["decision"] == "allow"
    assert simulation["persisted"] is False

    management.record_runtime_observation(
        application_id="family_tasks",
        subject_ref="user:masha",
        permission_id="camera.capture",
        actor_chain={"application_id": "family_tasks", "subject_ref": "user:masha"},
        outcome="deny",
        network_destination="unexpected.example",
        data_categories=("image",),
    )
    profiler = management.permission_profiler(
        "family_tasks",
        release_digest=release.release_digest,
        observed_capabilities=("workspace.read", "network.egress"),
        inferred_capabilities=("workspace.write",),
    )
    assert profiler["undeclared_high_risk"] == []
    assert profiler["role_matrix"][1]["compatible"]["guest"] is True
    assert profiler["preview_modes"]["guest"] == ["viewer"]
    assert profiler["preview_modes"]["custom"] == ["editor", "viewer"]
    assert {
        item["kind"]
        for item in management.anomalies(
            "family_tasks", release_digest=release.release_digest
        )
    } == {"unexpected_permission", "unexpected_network_destination"}
    assert "stale_device" in management.access_reviews()[0]["reasons"]

    updated = _release(applications, version="1.1.0", updated=True)
    update = management.update_review(
        "family_tasks",
        old_release_digest=release.release_digest,
        new_release_digest=updated.release_digest,
    )
    assert update["review_required"] is True
    assert update["auto_update_allowed"] is False
    assert update["role_changes"]["affected_users"] == {"editor": ["user:masha"]}
    assert any(
        "newly_elevated_permissions_or_roles" in item["reasons"]
        for item in management.access_reviews(application_id="family_tasks")
    )
    for index in range(2):
        management.access.grant_access(
            "family_tasks",
            release_digest=updated.release_digest,
            subject_ref=f"user:editor-{index}",
            application_roles=("editor",),
            permission_ceiling=("workspace.read", "workspace.write"),
            issuer_ref="user:owner",
            idempotency_key=f"sensitive-editor-{index}",
            constraints={"platform_role": "member"},
        )
    broad_assignment = next(
        item
        for item in management.anomalies(
            "family_tasks", release_digest=updated.release_digest
        )
        if item["kind"] == "broad_sensitive_role_assignment"
    )
    assert broad_assignment == {
        "kind": "broad_sensitive_role_assignment",
        "role_id": "editor",
        "assignment_count": 3,
        "severity": "high",
    }

    snapshot = management.export_snapshot("family_tasks")
    preview = management.import_snapshot(snapshot, issuer_ref="user:owner", apply=False)
    assert preview["valid"] is True
    assert grant.grant_id in {item["grant_id"] for item in preview["planned_grants"]}
    assert set(ROLE_TEMPLATES) == {
        "classroom",
        "dashboard",
        "household_tasks",
        "media_queue",
        "moderation",
        "research_review",
    }


def test_final_verification_blocks_drift_and_admits_scoped_release(
    tmp_path: Path,
) -> None:
    _, management, release = _services(tmp_path)

    failed = _verification(
        management,
        release,
        scope="publication",
        observed=(*release.permission_profile.flat_permissions, "network.undeclared"),
    )
    assert failed["publication_allowed"] is False
    with pytest.raises(ApplicationAccessError, match="Final Verification"):
        management.admit_release_stage(
            "family_tasks", release_digest=release.release_digest, stage="trial"
        )

    trial = _verification(management, release, scope="trial")
    admitted_trial = management.admit_release_stage(
        "family_tasks", release_digest=release.release_digest, stage="trial"
    )
    assert trial["publication_allowed"] is True
    assert admitted_trial["report_digest"] == trial["report"]["report_digest"]
    with pytest.raises(ApplicationAccessError, match="publication"):
        management.admit_release_stage(
            "family_tasks", release_digest=release.release_digest, stage="publication"
        )

    publication = _verification(management, release, scope="publication")
    admitted_publication = management.admit_release_stage(
        "family_tasks", release_digest=release.release_digest, stage="publication"
    )
    assert (
        admitted_publication["report_digest"] == publication["report"]["report_digest"]
    )
    assert publication["attestation"]["_type"] == "https://in-toto.io/Statement/v1"
    assert publication["attestation"]["statement_digest"].startswith("sha256:")


def test_surface_contract_covers_app_user_embedded_and_keyboard_flows() -> None:
    contract = ApplicationAccessManagementService.surface_contract()

    assert contract["applications_tabs"] == [
        "permissions",
        "access",
        "roles",
        "connected_accounts",
        "release_readiness",
        "activity",
    ]
    assert contract["users_access_tabs"] == [
        "people",
        "guests",
        "children",
        "devices",
        "sessions",
        "application_access",
        "activity",
    ]
    assert contract["keyboard"]["tabs"] == ["ArrowLeft", "ArrowRight"]
    assert contract["embedded_role_management"]["writes"] == "platform_api_only"


def test_pending_action_response_revokes_the_bound_grant_and_records_audit(
    tmp_path: Path,
) -> None:
    _, management, release = _services(tmp_path)
    grant = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="user:masha",
        application_roles=("viewer",),
        permission_ceiling=("workspace.read",),
        issuer_ref="user:owner",
        idempotency_key="pending-revoke",
        constraints={"platform_role": "member"},
    )
    pending_action = {
        "id": "pending.application-access.1",
        "kind": "application.access.revoke",
        "domain_ref": {
            "application_id": "family_tasks",
            "subject_ref": "user:masha",
            "grant_id": grant.grant_id,
            "expected_revision": grant.revision,
            "reviewed_permission_profile_digest": (
                grant.reviewed_permission_profile_digest
            ),
        },
    }

    with pytest.raises(ApplicationAccessError, match="permission profile mismatch"):
        management.apply_pending_action_response(
            {
                "pending_action_id": pending_action["id"],
                "pending_action": pending_action,
                "response_action_id": "approve",
                "response": {
                    "response_action_id": "approve",
                    "responder": {"type": "user", "user_id": "owner"},
                },
                "domain_ref": {
                    **pending_action["domain_ref"],
                    "reviewed_permission_profile_digest": DIGEST_C,
                },
            }
        )

    result = management.apply_pending_action_response(
        {
            "pending_action_id": pending_action["id"],
            "pending_action": pending_action,
            "response_action_id": "approve",
            "response": {
                "response_action_id": "approve",
                "responder": {"type": "user", "user_id": "owner"},
            },
            "domain_ref": pending_action["domain_ref"],
        }
    )

    assert result["applied"] is True
    assert result["grant"]["status"] == "revoked"
    assert result["grant"]["grant_id"] == grant.grant_id
    actions = [
        item["action"]
        for item in management.store.list_application_access_audit(
            application_id="family_tasks",
            subject_ref="user:masha",
        )
    ]
    assert "pending_action_response" in actions
    assert "grant_revoke" in actions
    assert (
        management.access.revoke_access(
            grant.grant_id,
            issuer_ref="user:owner",
            expected_revision=grant.revision,
        ).status
        == "revoked"
    )


def test_application_access_v1_end_to_end_evidence_bundle(tmp_path: Path) -> None:
    applications, management, release = _services(tmp_path)
    applications.store.save_installation(
        ApplicationInstallation(
            installation_id="installation:family_tasks",
            application_id="family_tasks",
            installed_release_digest=release.release_digest,
            component_refs=(
                {
                    "component_ref": "skill:family_tasks_skill",
                    "package_digest": DIGEST_A,
                    "lifecycle": "bound",
                },
            ),
            data_policy="retain",
            status="active",
            revision=1,
        ),
        expected_revision=0,
    )
    owner = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="user:owner",
        application_roles=("editor",),
        permission_ceiling=release.permission_profile.flat_permissions,
        issuer_ref="user:owner",
        idempotency_key="e2e-owner",
        constraints={"platform_role": "owner"},
    )
    management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="user:member",
        application_roles=("editor",),
        permission_ceiling=("workspace.read", "workspace.write"),
        issuer_ref="user:owner",
        idempotency_key="e2e-member",
        constraints={"platform_role": "member"},
    )
    child = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="child:petya",
        application_roles=("viewer",),
        permission_ceiling=("workspace.read",),
        issuer_ref="user:owner",
        idempotency_key="e2e-child",
        constraints={
            "platform_role": "child",
            "guardian_approval_id": "approval:guardian:e2e",
        },
    )
    guest = management.access.grant_access(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="session:guest-e2e",
        application_roles=("viewer",),
        permission_ceiling=("workspace.read",),
        issuer_ref="user:owner",
        idempotency_key="e2e-guest",
        constraints={"platform_role": "guest"},
        expires_at=_expires(),
    )

    owner_decision = management.access.decide(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="user:owner",
        permission_id="workspace.write",
        app_capability="task.complete",
        component_capabilities=("workspace.write",),
        actor_chain={"user_ref": "user:owner"},
    )
    child_llm = management.access.decide(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="child:petya",
        permission_id="llm.generate",
        app_capability="application.use",
        component_capabilities=("llm.generate",),
        actor_chain={"user_ref": "child:petya"},
    )
    guest_read = management.access.decide(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="session:guest-e2e",
        permission_id="workspace.read",
        app_capability="application.use",
        component_capabilities=("workspace.read",),
        actor_chain={"session_ref": "session:guest-e2e"},
    )
    assert owner_decision.decision == "allow"
    assert child_llm.decision == "pending_action"
    assert guest_read.decision == "allow"
    assert guest.constraints["profile_binding"] is False
    assert guest.constraints["durable_approvals"] is False

    connected_account = management.put_connected_account(
        "family_tasks",
        {
            "release_digest": release.release_digest,
            "account_id": "calendar-connected-e2e",
            "provider_id": "calendar",
            "subject_ref": "user:owner",
            "mode": "delegated_user",
            "scopes": ["calendar.read"],
            "status": "connected",
            "token_expires_at": _expires(24),
            "secret_value": "must-never-enter-evidence",
        },
        expected_revision=0,
    )
    missing_account = management.put_connected_account(
        "family_tasks",
        {
            "release_digest": release.release_digest,
            "account_id": "calendar-e2e",
            "provider_id": "calendar",
            "subject_ref": "user:owner",
            "mode": "delegated_user",
            "scopes": ["calendar.read"],
            "status": "missing",
        },
        expected_revision=0,
    )
    revoked_account = management.put_connected_account(
        "family_tasks",
        {
            "release_digest": release.release_digest,
            "account_id": "calendar-e2e",
            "provider_id": "calendar",
            "subject_ref": "user:owner",
            "mode": "delegated_user",
            "scopes": ["calendar.read"],
            "status": "revoked",
        },
        expected_revision=1,
    )
    denied_account = management.put_connected_account(
        "family_tasks",
        {
            "release_digest": release.release_digest,
            "account_id": "calendar-denied-e2e",
            "provider_id": "calendar",
            "subject_ref": "user:owner",
            "mode": "delegated_user",
            "scopes": ["calendar.read"],
            "status": "denied",
        },
        expected_revision=0,
    )
    expired_account = management.put_connected_account(
        "family_tasks",
        {
            "release_digest": release.release_digest,
            "account_id": "calendar-expired-e2e",
            "provider_id": "calendar",
            "subject_ref": "user:owner",
            "mode": "delegated_user",
            "scopes": ["calendar.read"],
            "status": "connected",
            "token_expires_at": "2020-01-01T00:00:00+00:00",
        },
        expected_revision=0,
    )
    assert connected_account["status"] == "connected"
    assert connected_account["revision"] == 1
    assert "secret_value" not in connected_account
    assert missing_account["status"] == "missing"
    assert revoked_account["status"] == "revoked"
    assert revoked_account["revision"] == 2
    assert denied_account["status"] == "denied"
    assert expired_account["status"] == "expired"
    with pytest.raises(ApplicationAccessError, match="revision conflict"):
        management.put_connected_account(
            "family_tasks",
            {
                "release_digest": release.release_digest,
                "account_id": "calendar-e2e",
                "provider_id": "calendar",
                "subject_ref": "user:owner",
                "mode": "delegated_user",
                "scopes": ["calendar.read"],
                "status": "connected",
            },
            expected_revision=1,
        )
    persisted_accounts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (applications.store.root / "connected_accounts").glob("*.json")
    )
    assert "must-never-enter-evidence" not in persisted_accounts

    updated = _release(applications, version="1.1.0", updated=True)
    update_review = management.update_review(
        "family_tasks",
        old_release_digest=release.release_digest,
        new_release_digest=updated.release_digest,
    )
    assert update_review["review_required"] is True
    assert update_review["auto_update_allowed"] is False

    revoked_guest = management.access.revoke_access(
        guest.grant_id,
        issuer_ref="user:owner",
        expected_revision=guest.revision,
    )
    cutoff = management.access.decide(
        "family_tasks",
        release_digest=release.release_digest,
        subject_ref="session:guest-e2e",
        permission_id="workspace.read",
        app_capability="application.use",
        component_capabilities=("workspace.read",),
        actor_chain={"session_ref": "session:guest-e2e"},
    )
    assert revoked_guest.status == "revoked"
    assert cutoff.reason_code == "grant_revoked"

    users = management.users_access()
    assert {item["subject_ref"] for item in users["children"]} == {child.subject_ref}
    assert all(item["eligible_for_application_access"] for item in users["subjects"])
    assert any(
        item["grant_id"] == owner.grant_id for item in users["application_access"]
    )

    verification = _verification(management, release, scope="publication")
    assert verification["ci_status"] == "passed"
    assert verification["evidence_bundle"]["schema"] == (
        "adaos.application.release_evidence_bundle.v1"
    )
    assert verification["evidence_bundle"]["bundle_digest"].startswith("sha256:")
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "src"
            / "adaos"
            / "abi"
            / "application.release-evidence-bundle.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(verification["evidence_bundle"])
