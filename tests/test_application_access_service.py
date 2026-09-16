from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.application_access import ApplicationPermissionProfile, normalize_application_roles
from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease
from adaos.services.applications import (
    ApplicationAccessError,
    ApplicationAccessService,
    ApplicationService,
    ApplicationStore,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _application() -> Application:
    return Application(
        application_id="family_tasks",
        legacy_project_id="family_tasks",
        publisher_ref="subnet:home",
        slug="family_tasks",
        display={"title": "Family Tasks", "summary": "Shared tasks"},
        visibility="private",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:family_tasks"},),
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


def _profile() -> ApplicationPermissionProfile:
    return ApplicationPermissionProfile.from_mapping(
        {
            "schema": "adaos.application.permission_profile.v1",
            "required": [
                {"id": "workspace.read", "purpose": "Read tasks"},
                {"id": "workspace.write", "purpose": "Save tasks"},
                {"id": "llm.generate", "purpose": "Suggest wording"},
            ],
            "optional": [],
        }
    )


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def _roles(profile: ApplicationPermissionProfile):
    declarations = [
        {
            "id": "viewer",
            "title": "Viewer",
            "grants": ["app.view"],
            "assignable_to": ["owner", "member", "child", "guest"],
            "requires_permissions": ["workspace.read"]
            if "workspace.read" in profile.flat_permissions
            else [],
        },
    ]
    if "workspace.write" in profile.flat_permissions:
        declarations.append(
            {
                "id": "editor",
                "title": "Editor",
                "grants": ["app.view", "app.write"],
                "assignable_to": ["owner", "member"],
                "requires_permissions": ["workspace.write"],
            }
        )
    return normalize_application_roles(
        declarations,
        known_permissions=profile.flat_permissions,
    )


def _register_release(
    service: ApplicationService,
    profile: ApplicationPermissionProfile,
    *,
    version: str = "1.0.0",
    lifecycle: str = "trial",
) -> str:
    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/family_tasks",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("projects/family_tasks/",),
    )
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="family_tasks",
        version=version,
        digest=DIGEST_A if version == "1.0.0" else DIGEST_B,
        manifest_digest=DIGEST_B if version == "1.0.0" else DIGEST_C,
        source_ref=source,
    )
    project_release = ProjectRelease(
        project_id="family_tasks",
        version=version,
        source_ref=source,
        components=(package,),
        permissions=profile.flat_permissions,
        validation_evidence=({"status": "passed"},),
    ).seal()
    release = service.register_release(
        ApplicationRelease(
            application_id="family_tasks",
            publisher_ref="subnet:home",
            project_release=project_release,
            accepted_candidate_id=f"candidate.family_tasks.{version}",
            acceptance_evidence=({"status": "accepted"},),
            provenance_refs=(DIGEST_C,),
            permission_profile=profile,
            application_roles=_roles(profile),
            lifecycle=lifecycle,
        )
    )
    return release.release_digest


def _service(tmp_path: Path) -> tuple[ApplicationService, ApplicationAccessService, str]:
    service = ApplicationService(ApplicationStore(tmp_path / "state"))
    service.register(_application())
    return service, ApplicationAccessService(service), _register_release(service, _profile())


def test_application_access_grant_decision_audit_and_revoke(tmp_path: Path) -> None:
    service, access, release_digest = _service(tmp_path)
    grant = access.grant_access(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="user:masha",
        application_roles=("editor",),
        permission_ceiling=("workspace.read", "workspace.write"),
        issuer_ref="user:owner",
        idempotency_key="masha-editor",
    )

    decision = access.decide(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="user:masha",
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=("workspace.write",),
        actor_chain={"user": "user:masha", "component": "scenario:family_tasks"},
    )

    assert decision.decision == "allow"
    assert decision.grant_id == grant.grant_id
    audit = service.store.list_application_access_audit("family_tasks", subject_ref="user:masha")
    assert [item["action"] for item in audit] == ["permission_decision", "grant_create"]

    revoked = access.revoke_access(grant.grant_id, issuer_ref="user:owner", expected_revision=grant.revision)
    denied = access.decide(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="user:masha",
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=("workspace.write",),
        actor_chain={"user": "user:masha"},
    )

    assert revoked.status == "revoked"
    assert denied.decision == "deny"
    assert denied.reason_code == "grant_revoked"


def test_application_access_pauses_when_release_profile_digest_changes(tmp_path: Path) -> None:
    service, access, release_digest = _service(tmp_path)
    grant = access.grant_access(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="user:masha",
        application_roles=("editor",),
        permission_ceiling=("workspace.read", "workspace.write"),
        issuer_ref="user:owner",
        idempotency_key="masha-editor",
    )
    next_profile = ApplicationPermissionProfile.from_mapping(
        {
            "schema": "adaos.application.permission_profile.v1",
            "required": [
                {"id": "workspace.read", "purpose": "Read tasks"},
                {"id": "workspace.write", "purpose": "Save tasks"},
                {"id": "llm.generate", "purpose": "Suggest wording"},
                {"id": "network.egress", "purpose": "Sync assigned tasks"},
            ],
            "optional": [],
        }
    )
    next_release_digest = _register_release(
        service,
        next_profile,
        version="1.1.0",
        lifecycle="prerelease",
    )

    decision = access.decide(
        "family_tasks",
        release_digest=next_release_digest,
        subject_ref="user:masha",
        permission_id="workspace.write",
        app_capability="app.write",
        component_capabilities=("workspace.write",),
        actor_chain={"user": "user:masha", "component": "scenario:family_tasks"},
    )

    assert decision.decision == "pending_action"
    assert decision.reason_code == "permission_profile_review_required"
    assert decision.grant_id == grant.grant_id


def test_application_access_enforces_guest_and_child_floors_on_grant(tmp_path: Path) -> None:
    _, access, release_digest = _service(tmp_path)

    with pytest.raises(ApplicationAccessError, match="guest Application access requires expires_at"):
        access.grant_access(
            "family_tasks",
            release_digest=release_digest,
            subject_ref="session:guest-1",
            application_roles=("viewer",),
            permission_ceiling=("workspace.read",),
            issuer_ref="user:owner",
            idempotency_key="guest-no-ttl",
        )

    with pytest.raises(ApplicationAccessError, match="guest Application access cannot include sensitive"):
        access.grant_access(
            "family_tasks",
            release_digest=release_digest,
            subject_ref="session:guest-1",
            application_roles=("viewer",),
            permission_ceiling=("llm.generate",),
            issuer_ref="user:owner",
            idempotency_key="guest-llm",
            expires_at=_expiry(),
        )

    with pytest.raises(ApplicationAccessError, match="child sensitive Application access requires guardian"):
        access.grant_access(
            "family_tasks",
            release_digest=release_digest,
            subject_ref="child:masha",
            application_roles=("viewer",),
            permission_ceiling=("llm.generate",),
            issuer_ref="user:owner",
            idempotency_key="child-llm",
        )

    guest = access.grant_access(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="session:guest-1",
        application_roles=("viewer",),
        permission_ceiling=("workspace.read",),
        issuer_ref="user:owner",
        idempotency_key="guest-read",
        expires_at=_expiry(),
    )

    assert guest.constraints["subject_kind"] == "guest"
    assert guest.constraints["session_bound"] is True
    assert guest.constraints["session_ref"] == "session:guest-1"
    assert guest.constraints["profile_binding"] is False
    assert guest.constraints["durable_approvals"] is False
    assert guest.expires_at is not None

    allowed = access.decide(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="session:guest-1",
        permission_id="workspace.read",
        app_capability="app.view",
        component_capabilities=("workspace.read",),
        actor_chain={"session_ref": "session:guest-1"},
    )
    wrong_session = access.decide(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="session:guest-1",
        permission_id="workspace.read",
        app_capability="app.view",
        component_capabilities=("workspace.read",),
        actor_chain={"session_ref": "session:guest-2"},
    )
    assert allowed.decision == "allow"
    assert wrong_session.reason_code == "session_scope_mismatch"


def test_application_access_requires_guardian_for_child_external_data_profile(tmp_path: Path) -> None:
    service = ApplicationService(ApplicationStore(tmp_path / "state"))
    service.register(_application())
    external_profile = ApplicationPermissionProfile.from_mapping(
        {
            "schema": "adaos.application.permission_profile.v1",
            "required": [{"id": "workspace.read", "purpose": "Read shared tasks"}],
            "optional": [],
            "data_practices": {"sent_off_device": ["user_content"]},
        }
    )
    release_digest = _register_release(service, external_profile)
    access = ApplicationAccessService(service)

    with pytest.raises(ApplicationAccessError, match="child sensitive Application access requires guardian"):
        access.grant_access(
            "family_tasks",
            release_digest=release_digest,
            subject_ref="child:masha",
            application_roles=("viewer",),
            permission_ceiling=("workspace.read",),
            issuer_ref="user:owner",
            idempotency_key="child-external-no-guardian",
        )

    grant = access.grant_access(
        "family_tasks",
        release_digest=release_digest,
        subject_ref="child:masha",
        application_roles=("viewer",),
        permission_ceiling=("workspace.read",),
        issuer_ref="user:owner",
        idempotency_key="child-external-with-guardian",
        constraints={"guardian_approval_id": "approval:guardian:1"},
    )

    assert grant.constraints["guardian_approval_id"] == "approval:guardian:1"
