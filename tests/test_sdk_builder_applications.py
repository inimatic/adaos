from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.sdk.builder import applications
from adaos.sdk.core.exporter import export
from adaos.sdk.developer import compositions
from adaos.domain.application import Application
from adaos.services.applications import ApplicationDevelopmentCoordinator, ApplicationService, ApplicationStore


def test_builder_application_create_uses_bounded_composition_and_core(monkeypatch, tmp_path: Path) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    created = []
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(applications, "_admit_builder_mutation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        applications,
        "publisher_context",
        lambda: {
            "publisher_ref": "subnet:home",
            "display_name": "Home Lab",
            "subnet_short_ref": "home",
            "home_zone": "local",
            "release_key_ref": "artifact-signing:home:key",
            "release_key_fingerprint": "sha256:" + "f" * 64,
            "release_key_algorithm": "ed25519",
            "release_key_issuer": "home",
            "trust_relation": "local",
        },
    )
    monkeypatch.setattr(
        compositions,
        "get",
        lambda _project_id: (_ for _ in ()).throw(
            compositions.ProjectCompositionNotFound("missing")
        ),
    )

    def create(project_id, **kwargs):
        created.append((project_id, kwargs))
        return {"ok": True, "project": {"id": project_id}}

    monkeypatch.setattr(compositions, "create_with_primary_component", create)

    operation = applications.create_application(
        "applications", title="Applications", summary="Application manager",
        protection={
            "system_application": True,
            "bootstrap_capable": True,
            "active_installation_removable": False,
            "recovery_surfaces": ["cli", "mcp"],
        },
        actor_ref="user:owner", subnet_ref="subnet:home",
        capability="applications.develop", expected_revision=0,
        idempotency_key="create-applications-1",
    )

    assert operation["status"] == "succeeded"
    assert service.store.get_application("applications").legacy_project_id == "applications"
    assert service.store.get_application("applications").protection["system_application"] is True
    assert created[0][1]["kind"] == "scenario"
    assert created[0][1]["entrypoints"][0]["presentation"] == "scenario:applications"


def test_candidate_verification_adopts_project_before_release_gate(monkeypatch) -> None:
    application = Application(
        application_id="desktop",
        legacy_project_id="desktop",
        publisher_ref="subnet:home",
        slug="desktop",
        display={"title": "Desktop", "summary": None},
        visibility="private",
        entrypoints=(
            {"entrypoint_id": "main", "presentation_ref": "scenario:desktop"},
        ),
        publisher={
            "publisher_ref": "subnet:home",
            "display_name": "Home",
            "subnet_short_ref": "home",
            "release_key_ref": "artifact-signing:home:key",
            "release_key_fingerprint": "sha256:" + "f" * 64,
            "home_zone": "local",
            "trust_relation": "local",
        },
    )
    observed = {"application": None}
    calls = []
    monkeypatch.setattr(
        applications,
        "_application_for_project",
        lambda _project_id: observed["application"],
    )
    monkeypatch.setattr(
        applications,
        "publisher_context",
        lambda: {"publisher_ref": "subnet:home"},
    )
    monkeypatch.setattr(
        compositions,
        "get",
        lambda _project_id: {
            "catalog": {"title": "Desktop", "description": "Home desktop"}
        },
    )

    def create(project_id, **kwargs):
        calls.append((project_id, kwargs))
        observed["application"] = application
        return {"ok": True, "application": application.to_dict()}

    monkeypatch.setattr(applications, "create_application", create)

    assert applications._ensure_application_for_project(
        "desktop", actor_ref="builder.user"
    ) is application
    assert calls == [
        (
            "desktop",
            {
                "title": "Desktop",
                "summary": "Home desktop",
                "visibility": "private",
                "actor_ref": "builder.user",
                "subnet_ref": "subnet:home",
                "capability": "applications.develop",
                "expected_revision": 0,
                "idempotency_key": "trial-adopt:desktop",
            },
        )
    ]


def test_builder_application_sdk_has_no_raw_authority_parameters() -> None:
    forbidden = {
        "path", "filesystem_path", "command", "process", "git_credentials",
        "registry_path", "private_key", "repository_token",
    }
    for name in applications.__all__:
        function = getattr(applications, name)
        assert forbidden.isdisjoint(inspect.signature(function).parameters), name


def test_delete_application_development_requires_exact_snapshot_and_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    application = Application(
        application_id="notes",
        legacy_project_id="notes",
        publisher_ref="subnet:home",
        slug="notes",
        display={"title": "Notes", "summary": "Local notes"},
        visibility="private",
        entrypoints=(
            {"entrypoint_id": "main", "presentation_ref": "scenario:notes"},
        ),
        publisher={
            "publisher_ref": "subnet:home",
            "display_name": "Home",
            "subnet_short_ref": "home",
            "release_key_ref": "artifact-signing:home:key",
            "release_key_fingerprint": "sha256:" + "f" * 64,
            "home_zone": "local",
            "trust_relation": "local",
        },
    )
    service = ApplicationService(ApplicationStore(tmp_path))
    service.register(application, expected_revision=0)
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    snapshot = {
        "project_id": "notes",
        "manifest_digest": "sha256:" + "a" * 64,
        "primary_ref": "scenario:notes",
        "owned_refs": ["scenario:notes", "skill:notes_skill"],
    }
    effect_calls = []
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(applications, "_admit_builder_mutation", lambda *args, **kwargs: None)
    monkeypatch.setattr(applications, "_development_project_snapshot", lambda _app: snapshot)
    monkeypatch.setattr(
        applications,
        "_delete_application_development_effect",
        lambda *args, **kwargs: effect_calls.append((args, kwargs)) or {"ok": True},
    )

    with pytest.raises(ValueError, match="confirmation"):
        applications.delete_application_development(
            "notes",
            expected_manifest_digest=snapshot["manifest_digest"],
            expected_primary_ref=snapshot["primary_ref"],
            confirmed=False,
            actor_ref="user:owner",
            subnet_ref="subnet:home",
            capability="applications.develop",
            expected_revision=1,
            idempotency_key="delete-notes-no",
        )

    operation = applications.delete_application_development(
        "notes",
        expected_manifest_digest=snapshot["manifest_digest"],
        expected_primary_ref=snapshot["primary_ref"],
        confirmed=True,
        actor_ref="user:owner",
        subnet_ref="subnet:home",
        capability="applications.develop",
        expected_revision=1,
        idempotency_key="delete-notes-yes",
    )

    assert operation["status"] == "succeeded"
    assert effect_calls[0][1]["owned_refs"] == (
        "scenario:notes",
        "skill:notes_skill",
    )


def test_project_access_contract_uses_context_bound_dev_roots(monkeypatch, tmp_path: Path) -> None:
    from adaos.services.builder import application_permissions

    calls = []
    paths = SimpleNamespace(
        dev_dir=lambda: tmp_path / "dev",
        dev_projects_dir=lambda: tmp_path / "dev" / "projects",
        dev_skills_dir=lambda: tmp_path / "dev" / "skills",
    )
    monkeypatch.setattr(applications, "_ctx", lambda: SimpleNamespace(paths=paths))
    monkeypatch.setattr(
        application_permissions,
        "application_permissions_context",
        lambda **kwargs: calls.append(kwargs) or {"status": "present"},
    )

    result = applications.project_access_contract(
        "scenario:roster", project_ref="project:roster"
    )

    assert result == {"status": "present"}
    assert calls == [
        {
            "component_ref": "scenario:roster",
            "requested_project_ref": "project:roster",
            "dev_projects_root": (tmp_path / "dev" / "projects").resolve(),
            "dev_skills_root": (tmp_path / "dev" / "skills").resolve(),
        }
    ]


def test_publisher_owner_role_prefers_declared_default_and_has_bounded_legacy_fallback() -> None:
    viewer = SimpleNamespace(
        role_id="viewer",
        grants=("workspace.read",),
        assignable_to=("owner", "member"),
        default_for={"owner": "viewer"},
    )
    coordinator = SimpleNamespace(
        role_id="coordinator",
        grants=("workspace.read", "workspace.write"),
        assignable_to=("owner", "member"),
        default_for={},
    )

    assert applications._publisher_owner_role_ids(
        SimpleNamespace(application_roles=(viewer, coordinator))
    ) == (("viewer",), "declared_default")
    viewer.default_for = {}
    assert applications._publisher_owner_role_ids(
        SimpleNamespace(application_roles=(viewer, coordinator))
    ) == (("coordinator",), "unique_maximal_compatibility")

    auditor = SimpleNamespace(
        role_id="auditor",
        grants=("audit.read",),
        assignable_to=("owner",),
        default_for={},
    )
    with pytest.raises(ValueError, match="default_for.owner"):
        applications._publisher_owner_role_ids(
            SimpleNamespace(application_roles=(coordinator, auditor))
        )


def test_builder_provisions_and_rebinds_publisher_owner_access(monkeypatch) -> None:
    role = SimpleNamespace(
        role_id="coordinator",
        grants=("workspace.read", "workspace.write"),
        assignable_to=("owner",),
        default_for={"owner": "coordinator"},
    )
    profile = SimpleNamespace(
        flat_permissions=("workspace.read", "workspace.write"),
        digest="sha256:" + "a" * 64,
    )
    release = SimpleNamespace(application_roles=(role,), permission_profile=profile)
    grants = []

    def make_grant(**values):
        return SimpleNamespace(
            grant_id="appgrant.owner",
            status="active",
            application_roles=tuple(values["application_roles"]),
            permission_ceiling=tuple(values["permission_ceiling"]),
            explicit_denies=tuple(values.get("explicit_denies") or ()),
            constraints=dict(values["constraints"]),
            reviewed_permission_profile_digest=profile.digest,
            expires_at=values.get("expires_at"),
            revision=(grants[0].revision + 1 if grants else 1),
        )

    class Access:
        def grant_access(self, _application_id, **values):
            grant = make_grant(**values)
            grants[:] = [grant]
            return grant

        def change_access(self, _grant_id, **values):
            grant = make_grant(**values)
            grants[:] = [grant]
            return grant

    store = SimpleNamespace(
        get_release=lambda *_args: release,
        list_application_access_grants=lambda *_args, **_kwargs: tuple(grants),
    )
    service = SimpleNamespace(store=store)
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(
        applications,
        "_ctx",
        lambda: SimpleNamespace(settings=SimpleNamespace(owner_id="owner")),
    )
    monkeypatch.setattr(
        applications,
        "ApplicationAccessManagementService",
        lambda _service: SimpleNamespace(access=Access()),
    )

    first = applications._ensure_publisher_owner_access(
        "roster", release_digest="sha256:" + "1" * 64
    )
    assert first["subject_ref"] == "user:owner"
    assert first["application_roles"] == ["coordinator"]
    assert first["revision"] == 1

    profile.digest = "sha256:" + "b" * 64
    second = applications._ensure_publisher_owner_access(
        "roster", release_digest="sha256:" + "2" * 64
    )
    assert second["permission_profile_digest"] == profile.digest
    assert second["revision"] == 2


def test_builder_provisions_roleless_publisher_permission_grant(monkeypatch) -> None:
    profile = SimpleNamespace(
        flat_permissions=("workspace.read", "workspace.write"),
        digest="sha256:" + "c" * 64,
    )
    release = SimpleNamespace(application_roles=(), permission_profile=profile)
    grants = []

    class Access:
        def grant_access(self, _application_id, **values):
            grant = SimpleNamespace(
                grant_id="appgrant.roleless-owner",
                status="active",
                application_roles=tuple(values["application_roles"]),
                permission_ceiling=tuple(values["permission_ceiling"]),
                explicit_denies=tuple(values.get("explicit_denies") or ()),
                constraints=dict(values["constraints"]),
                reviewed_permission_profile_digest=profile.digest,
                expires_at=values.get("expires_at"),
                revision=1,
            )
            grants.append(grant)
            return grant

    store = SimpleNamespace(
        get_release=lambda *_args: release,
        list_application_access_grants=lambda *_args, **_kwargs: tuple(grants),
    )
    service = SimpleNamespace(store=store)
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(
        applications,
        "_ctx",
        lambda: SimpleNamespace(settings=SimpleNamespace(owner_id="owner")),
    )
    monkeypatch.setattr(
        applications,
        "ApplicationAccessManagementService",
        lambda _service: SimpleNamespace(access=Access()),
    )

    result = applications._ensure_publisher_owner_access(
        "adaos_drive", release_digest="sha256:" + "3" * 64
    )

    assert result["required"] is True
    assert result["application_roles"] == []
    assert result["role_resolution"] == "permission_profile_only"
    assert result["permission_profile_digest"] == profile.digest
    assert grants[0].permission_ceiling == profile.flat_permissions


@pytest.mark.parametrize("difference", ["selection", "workflow", "publication_unconfirmed"])
def test_local_trial_acceptance_preserves_selection_on_stale_or_unconfirmed_publication(monkeypatch, difference):
    from adaos.sdk.builder import lifecycle, workflow
    selection = SimpleNamespace(release_digest="release", source="local_trial", revision=1)
    release = SimpleNamespace(accepted_candidate_id="different" if difference == "selection" else "candidate")
    app = SimpleNamespace(entrypoints=({"presentation_ref": "scenario:test"},))
    store = SimpleNamespace(get_application=lambda _: app, get_runtime_selection=lambda *args: selection,
                            get_release=lambda *args: release)
    effects = []
    service = SimpleNamespace(store=store, select_runtime=lambda **kwargs: effects.append(kwargs))
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:test")
    monkeypatch.setattr(applications, "_admit_builder_mutation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        applications,
        "_promote_local_trial_final_verification",
        lambda *args, **kwargs: {"publication_allowed": True},
    )
    monkeypatch.setattr(
        applications,
        "ApplicationAccessManagementService",
        lambda _service: SimpleNamespace(
            admit_release_stage=lambda *args, **kwargs: {"status": "passed"}
        ),
    )
    state = {"delivery": {"status": "accepted", "candidate_id": "other" if difference == "workflow" else "candidate",
                          "package_digest": "digest"}, "publication": {"status": "unknown"}}
    monkeypatch.setattr(workflow, "get_state", lambda *args: state)
    monkeypatch.setattr(lifecycle, "publish_candidate", lambda *args, **kwargs: {"ok": False})
    with pytest.raises(ValueError):
        applications.accept_local_trial("test", webspace_id="desktop", candidate_id="candidate", candidate_digest="digest", actor_ref="user:test")
    assert effects == []


def test_local_trial_acceptance_resumes_exact_selected_candidate_when_workflow_is_stale(
    monkeypatch,
):
    from adaos.sdk.builder import workflow
    from adaos.sdk.developer import projects

    selection = SimpleNamespace(
        release_digest="sha256:selected-release",
        source="local_trial",
        revision=14,
    )
    release = SimpleNamespace(accepted_candidate_id="selected-candidate")
    app = SimpleNamespace(entrypoints=({"presentation_ref": "scenario:test"},))
    store = SimpleNamespace(
        get_application=lambda _application_id: app,
        get_runtime_selection=lambda *_args: selection,
        get_release=lambda *_args: release,
    )
    service = SimpleNamespace(store=store)
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:test")
    monkeypatch.setattr(applications, "_admit_builder_mutation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, "get_state", lambda *_args: {"delivery": {
        "candidate_id": "stale-candidate",
        "package_digest": "sha256:stale-package",
        "status": "stale",
    }})
    monkeypatch.setattr(projects, "get_candidate", lambda _candidate_id: {"candidate": {
        "candidate_id": "selected-candidate",
        "package_digest": "sha256:selected-package",
        "release_digest": "sha256:selected-release",
        "status": "accepted",
    }})
    monkeypatch.setattr(
        projects,
        "decide_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("accepted Candidate must not be decided again")
        ),
    )
    promotions = []
    monkeypatch.setattr(
        projects,
        "promote_candidate",
        lambda candidate_id, **kwargs: promotions.append((candidate_id, kwargs)) or {
            "ok": True,
            "candidate_id": candidate_id,
            "release_digest": "sha256:selected-release",
            "package_digest": "sha256:selected-package",
        },
    )
    monkeypatch.setattr(
        applications,
        "_promote_local_trial_final_verification",
        lambda *_args, **kwargs: {
            "publication_allowed": True,
            "allow_completed": kwargs["allow_completed"],
        },
    )
    monkeypatch.setattr(
        applications,
        "ApplicationAccessManagementService",
        lambda _service: SimpleNamespace(
            admit_release_stage=lambda *_args, **_kwargs: {"status": "passed"}
        ),
    )
    placements = []
    monkeypatch.setattr(
        applications,
        "place_local_stable",
        lambda application_id, **kwargs: placements.append((application_id, kwargs)) or {
            "ok": True,
            "runtime_selection": {"source": "stable_installation"},
        },
    )

    result = applications.accept_local_trial(
        "test",
        webspace_id="desktop",
        candidate_id="selected-candidate",
        candidate_digest="sha256:selected-package",
        actor_ref="user:test",
    )

    assert result["ok"] is True
    assert promotions[0][0] == "selected-candidate"
    assert promotions[0][1]["permission_decision"]["approved"] is True
    assert placements[0][1]["publication_result"]["release_digest"] == "sha256:selected-release"
    assert result["publication_verification"]["allow_completed"] is True


def test_local_stable_receipt_cannot_replace_a_different_selected_release(monkeypatch):
    from adaos.sdk.builder import workflow

    store = SimpleNamespace(
        get_application=lambda _application_id: SimpleNamespace(
            entrypoints=({"presentation_ref": "scenario:test"},)
        ),
        get_runtime_selection=lambda *_args: SimpleNamespace(
            release_digest="sha256:selected-release"
        ),
    )
    monkeypatch.setattr(
        applications,
        "_application_service",
        lambda: SimpleNamespace(store=store),
    )
    monkeypatch.setattr(applications, "production_webspace_id", lambda value: value)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:test")
    monkeypatch.setattr(applications, "_admit_builder_mutation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, "get_state", lambda *_args: {})

    with pytest.raises(
        ValueError,
        match="exact Candidate has not been accepted into Workspace",
    ):
        applications.place_local_stable(
            "test",
            webspace_id="desktop",
            candidate_id="selected-candidate",
            candidate_digest="sha256:selected-package",
            actor_ref="user:test",
            publication_result={
                "ok": True,
                "candidate_id": "selected-candidate",
                "release_digest": "sha256:different-release",
                "package_digest": "sha256:selected-package",
            },
        )


def test_builder_updates_application_metadata_through_durable_operation(
    monkeypatch, tmp_path: Path
) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    publisher = {
        "publisher_ref": "subnet:home",
        "display_name": "Home Lab",
        "subnet_short_ref": "home",
        "home_zone": "local",
        "release_key_ref": "artifact-signing:home:key",
        "release_key_fingerprint": "sha256:" + "f" * 64,
        "trust_relation": "local",
    }
    service.register(
        Application(
            application_id="applications",
            legacy_project_id="applications",
            publisher_ref="subnet:home",
            slug="applications",
            display={"title": "Applications", "summary": "Creation prompt"},
            visibility="private",
            entrypoints=(
                {
                    "entrypoint_id": "main",
                    "presentation_ref": "scenario:applications",
                },
            ),
            publisher=publisher,
        )
    )
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        applications, "_admit_builder_mutation", lambda *args, **kwargs: None
    )

    operation = applications.update_application_metadata(
        "applications",
        title="Applications",
        summary="Manage installed applications and available releases.",
        categories=("System", "Management"),
        actor_ref="builder.chat",
        subnet_ref="subnet:home",
        capability="applications.develop",
        expected_revision=1,
        idempotency_key="applications-metadata-1",
    )

    assert operation["status"] == "succeeded"
    assert operation["action"] == "update_metadata"
    updated = service.store.get_application("applications")
    assert updated.revision == 2
    assert updated.display == {
        "title": "Applications",
        "summary": "Manage installed applications and available releases.",
        "categories": ["System", "Management"],
    }


def test_builder_recovers_lost_application_metadata_response(
    monkeypatch, tmp_path: Path
) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    publisher = {
        "publisher_ref": "subnet:home",
        "display_name": "Home Lab",
        "subnet_short_ref": "home",
        "home_zone": "local",
        "release_key_ref": "artifact-signing:home:key",
        "release_key_fingerprint": "sha256:" + "f" * 64,
        "trust_relation": "local",
    }
    service.register(
        Application(
            application_id="applications",
            legacy_project_id="applications",
            publisher_ref="subnet:home",
            slug="applications",
            display={"title": "Applications", "summary": "Creation prompt"},
            visibility="private",
            entrypoints=(
                {
                    "entrypoint_id": "main",
                    "presentation_ref": "scenario:applications",
                },
            ),
            publisher=publisher,
        )
    )
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        applications, "_admit_builder_mutation", lambda *args, **kwargs: None
    )
    intent = {
        "title": "Applications",
        "summary": "Manage installed applications and available releases.",
        "categories": ["System", "Management"],
    }

    def apply_then_lose_response():
        applications._update_application_metadata_effect(
            "applications",
            title=intent["title"],
            summary=intent["summary"],
            categories=intent["categories"],
            expected_revision=1,
        )
        raise RuntimeError("response lost")

    with pytest.raises(RuntimeError, match="response lost"):
        coordinator.execute(
            "update_metadata",
            "applications",
            actor_ref="builder.lifecycle",
            subnet_ref="subnet:home",
            capability="applications.develop",
            expected_revision=1,
            idempotency_key="applications-metadata-lost-response",
            intent=intent,
            callback=apply_then_lose_response,
        )

    operation = coordinator.list("applications")[0]
    recovered = applications.reconcile_development_operation(
        operation["operation_id"],
        actor_ref="builder.lifecycle",
        subnet_ref="subnet:home",
        capability="applications.recover",
    )

    assert recovered["status"] == "succeeded"
    assert recovered["result"]["duplicate"] is True
    assert service.store.get_application("applications").revision == 2


def test_publisher_context_exposes_only_public_signing_identity(monkeypatch, tmp_path: Path) -> None:
    key = tmp_path / "publisher.ed25519"
    key.write_bytes(b"a" * 32)
    monkeypatch.setenv("ADAOS_ARTIFACT_ATTESTATIONS_MODE", "publish")
    monkeypatch.setenv("ADAOS_ARTIFACT_SIGNING_KEY_FILE", str(key))
    monkeypatch.setenv("ADAOS_ARTIFACT_SIGNING_ISSUER", "subnet-home")
    monkeypatch.setattr(
        applications,
        "_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(subnet_id="home", zone_id="local")),
    )

    context = applications.publisher_context()

    assert context["publisher_ref"] == "subnet:home"
    assert context["release_key_fingerprint"].startswith("sha256:")
    assert "private" not in " ".join(context).lower()


def test_builder_application_facade_is_discoverable() -> None:
    metadata = export(
        level="std",
        query="builder create application trial publish stable",
        limit=64,
    )
    names = {item["name"] for item in metadata["tools"]}

    assert "adaos.sdk.builder.applications.create_application" in names
    assert "adaos.sdk.builder.applications.publish_prerelease" in names
    assert "adaos.sdk.builder.applications.promote_stable" in names


def test_builder_application_mutations_require_local_admitted_capability(
    monkeypatch,
) -> None:
    decisions = []
    context = SimpleNamespace(
        config=SimpleNamespace(subnet_id="home"),
        skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name="builder")),
    )
    monkeypatch.setattr(applications, "_ctx", lambda: context)
    monkeypatch.setattr(
        applications,
        "require_skill_capability",
        lambda ctx, capability: decisions.append((ctx, capability)),
    )

    applications._admit_builder_mutation(
        "create",
        "app_test",
        subnet_ref="subnet:home",
        capability="applications.develop",
    )

    assert decisions == [(context, "applications.develop")]
    monkeypatch.setattr(
        applications,
        "_application_service",
        lambda: SimpleNamespace(
            store=SimpleNamespace(
                get_application=lambda _application_id: SimpleNamespace(
                    publisher_ref="subnet:foreign"
                )
            )
        ),
    )
    with pytest.raises(ValueError, match="local Application publisher"):
        applications._admit_builder_mutation(
            "preview",
            "app_foreign",
            subnet_ref="subnet:home",
            capability="applications.develop",
        )
    with pytest.raises(ValueError, match="local publisher identity"):
        applications._admit_builder_mutation(
            "create",
            "app_test",
            subnet_ref="subnet:foreign",
            capability="applications.develop",
        )


def test_builder_application_reconciles_lost_create_response(
    monkeypatch, tmp_path: Path
) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    publisher = {
        "publisher_ref": "subnet:home",
        "display_name": "Home Lab",
        "subnet_short_ref": "home",
        "home_zone": "local",
        "release_key_ref": "artifact-signing:home:key",
        "release_key_fingerprint": "sha256:" + "f" * 64,
        "release_key_algorithm": "ed25519",
        "release_key_issuer": "home",
        "trust_relation": "local",
    }
    service.register(
        Application(
            application_id="applications",
            legacy_project_id="applications",
            publisher_ref="subnet:home",
            slug="applications",
            display={"title": "Applications", "summary": "Application manager"},
            visibility="private",
            entrypoints=(
                {
                    "entrypoint_id": "main",
                    "presentation_ref": "scenario:applications",
                },
            ),
            publisher={
                key: publisher[key]
                for key in (
                    "publisher_ref",
                    "display_name",
                    "subnet_short_ref",
                    "release_key_ref",
                    "release_key_fingerprint",
                    "home_zone",
                    "trust_relation",
                )
            },
        )
    )
    intent = {
        "title": "Applications",
        "summary": "Application manager",
        "template": "empty",
        "visibility": "private",
        "publisher_key_fingerprint": publisher["release_key_fingerprint"],
        "publisher": publisher,
    }
    try:
        coordinator.execute(
            "create",
            "applications",
            actor_ref="user:owner",
            subnet_ref="subnet:home",
            capability="applications.develop",
            expected_revision=0,
            idempotency_key="create-applications-lost-response",
            intent=intent,
            callback=lambda: (_ for _ in ()).throw(TimeoutError("response lost")),
        )
    except TimeoutError:
        pass
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(applications, "publisher_context", lambda: publisher)
    monkeypatch.setattr(applications, "_admit_builder_mutation", lambda *args, **kwargs: None)

    operation = applications.reconcile_development_operation(
        coordinator.list()[0]["operation_id"],
        actor_ref="user:owner",
        subnet_ref="subnet:home",
        capability="applications.recover",
    )

    assert operation["status"] == "succeeded"
    assert operation["result"]["duplicate"] is True
