from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.application import (
    Application,
    ApplicationInstallation,
    ApplicationRelease,
)
from adaos.domain.application_access import (
    ApplicationPermissionProfile,
    normalize_application_roles,
)
from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
    ResolvedDependency,
)
from adaos.services.applications import (
    ApplicationPlanConflict,
    ApplicationRevisionConflict,
    ApplicationRolloutError,
    ApplicationRolloutService,
    ApplicationService,
    ApplicationServiceError,
    ApplicationStore,
    ApplicationStoreError,
    GitStableSourcePublisher,
    StableSourceProjectionService,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _application(
    application_id: str = "app_recipes", project_id: str = "recipes"
) -> Application:
    return Application(
        application_id=application_id,
        legacy_project_id=project_id,
        publisher_ref="subnet:sn_home",
        slug=project_id,
        display={"title": project_id.title(), "summary": None},
        visibility="public",
        entrypoints=(
            {"entrypoint_id": "main", "presentation_ref": "scenario:recipes"},
        ),
        publisher={
            "publisher_ref": "subnet:sn_home",
            "display_name": "Home",
            "subnet_short_ref": "sn_home",
            "release_key_ref": "subnet-key:release-signing:1",
            "release_key_fingerprint": DIGEST_C,
            "home_zone": "local-dev",
            "trust_relation": "local",
        },
    )


def _release(
    *,
    application_id: str = "app_recipes",
    project_id: str = "recipes",
    version: str = "1.0.0",
    package_digest: str = DIGEST_A,
    lifecycle: str = "trial",
    permissions: tuple[str, ...] = ("workspace.read", "workspace.write"),
    with_worker: bool = False,
    with_shared_dependency: bool = False,
) -> ApplicationRelease:
    source = ArtifactSourceRef(
        forge="github",
        repository=f"inimatic/{project_id}",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=(f"projects/{project_id}/",),
    )
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version=version,
        digest=package_digest,
        manifest_digest=DIGEST_C,
        source_ref=source,
    )
    components = (package,)
    if with_worker:
        components = (
            package,
            ArtifactPackageRef(
                kind="skill",
                artifact_id="recipes-worker",
                version=version,
                digest=DIGEST_B,
                manifest_digest=DIGEST_C,
                source_ref=source,
            ),
        )
    project_release = ProjectRelease(
        project_id=project_id,
        version=version,
        source_ref=source,
        components=components,
        resolved_dependencies=(
            (
                ResolvedDependency(
                    kind="skill",
                    artifact_id="shared-mail-provider",
                    version="1.0.0",
                    package_digest=DIGEST_C,
                    version_spec="^1",
                ),
            )
            if with_shared_dependency
            else ()
        ),
        permissions=permissions,
        validation_evidence=({"status": "passed"},),
    ).seal()
    return ApplicationRelease(
        application_id=application_id,
        publisher_ref="subnet:sn_home",
        project_release=project_release,
        accepted_candidate_id=f"candidate.{project_id}.{version}",
        acceptance_evidence=(
            {"decision": "accepted", "release_digest": project_release.release_digest},
        ),
        provenance_refs=(DIGEST_C,),
        lifecycle=lifecycle,
    )


@pytest.fixture
def service(tmp_path: Path) -> ApplicationService:
    result = ApplicationService(
        ApplicationStore(tmp_path),
        executor=lambda _plan: {"ok": True, "status": "succeeded"},
    )
    result.register(_application())
    return result


def test_native_workspace_publication_adoption_requires_exact_closure(service):
    from adaos.domain.artifact_release import WorkspaceLock, WorkspaceSlot

    release = service.register_release(_release())
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-09-15T00:00:00Z",
        slots=(
            WorkspaceSlot(
                slot_id="main",
                project_id="recipes",
                release="recipes@1.0.0",
                release_digest=release.release_digest,
            ),
        ),
        components=release.project_release.components,
    )
    installed = service.reconcile_workspace_installation(
        "app_recipes", release.release_digest, lock
    )
    assert installed.status == "active" and installed.revision == 1
    assert installed.component_refs[0]["lifecycle"] == "bound"
    assert (
        service.reconcile_workspace_installation(
            "app_recipes", release.release_digest, lock
        )
        == installed
    )
    assert not service.store.get_channels("app_recipes")["channels"]
    from dataclasses import replace

    with pytest.raises(ApplicationServiceError, match="closure"):
        service.reconcile_workspace_installation(
            "app_recipes", release.release_digest, replace(lock, components=())
        )
    with pytest.raises(ApplicationServiceError, match="exact"):
        service.reconcile_workspace_installation(
            "app_recipes", release.release_digest, replace(lock, slots=())
        )


def test_store_enforces_one_to_one_legacy_project_mapping(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path)
    store.save_application(_application(), expected_revision=0)

    with pytest.raises(ApplicationStoreError, match="already mapped"):
        store.save_application(
            _application("app_other", "recipes"), expected_revision=0
        )


def test_store_deletes_only_exact_unpublished_unreferenced_application(
    tmp_path: Path,
) -> None:
    store = ApplicationStore(tmp_path)
    store.save_application(_application(), expected_revision=0)

    with pytest.raises(ApplicationRevisionConflict):
        store.delete_unpublished_application("app_recipes", expected_revision=0)

    result = store.delete_unpublished_application("app_recipes", expected_revision=1)

    assert result["definition_removed"] is True
    with pytest.raises(FileNotFoundError):
        store.get_application("app_recipes")


def test_store_refuses_to_delete_application_with_release(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path)
    store.save_application(_application(), expected_revision=0)
    store.put_release(_release())

    with pytest.raises(ApplicationStoreError, match="releases"):
        store.delete_unpublished_application("app_recipes", expected_revision=1)


def test_catalog_summary_omits_full_release_closure(service: ApplicationService) -> None:
    release = service.register_release(_release())
    service.move_channel(
        "app_recipes",
        "stable",
        release.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )

    summary = service.list_models(summary=True)[0]
    full = service.list_models()[0]

    assert summary["schema"] == "adaos.application.catalog_summary.v1"
    assert summary["marketplace_release"]["version"] == "1.0.0"
    assert "components" not in summary["marketplace_release"]["project_release"]
    assert set(summary["marketplace_release"]["project_release"]["catalog"]) <= {
        "icon"
    }
    assert "release" not in summary["effective_release"]
    assert full["effective_release"]["release"]["release_digest"] == release.release_digest


def test_operation_plan_projects_structured_permission_review(
    service: ApplicationService,
) -> None:
    release = service.register_release(_release())

    install = service.plan_operation(
        "app_recipes",
        "install",
        release_digest=release.release_digest,
        expected_revision=0,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="permission-review-install",
    )

    review = install.plan["permission_review"]
    assert review["profile_digest"] == release.permission_profile.digest
    assert review["approval_required"] is True
    assert review["approval_permissions"] == ["workspace.read", "workspace.write"]
    assert [item["requirement"] for item in review["items"]] == ["required", "required"]


def test_component_placement_changes_are_reviewed_and_revision_guarded(
    tmp_path: Path,
) -> None:
    class PlacementExecutor:
        revision = 7
        placements = [
            {"component_ref": "scenario:recipes", "mode": "singleton"},
            {"component_ref": "skill:recipes-worker", "mode": "singleton"},
        ]

        def deployment_snapshot(self, application_id: str):
            assert application_id == "app_recipes"
            return {
                "deployment_id": "application-deployment:app_recipes",
                "release_digest": self.release_digest,
                "revision": self.revision,
                "placements": list(self.placements),
            }

        def __call__(self, plan):
            assert plan["expected_revision"] == self.revision
            change = plan["placement_change"]
            if plan["kind"] in {"relocate_component", "install_component"}:
                for placement in self.placements:
                    if placement["component_ref"] == change["component_ref"]:
                        placement.update(
                            mode="selected_nodes",
                            selected_node_ids=[change["target_node_id"]],
                        )
            else:
                for placement in self.placements:
                    if placement["component_ref"] == change["component_ref"]:
                        placement.update(mode="disabled", selected_node_ids=[])
            self.revision += 1
            return {
                "ok": True,
                "status": "active",
                "deployment": self.deployment_snapshot("app_recipes"),
            }

    executor = PlacementExecutor()
    application_service = ApplicationService(
        ApplicationStore(tmp_path),
        executor=executor,
    )
    application_service.register(_application())
    release = application_service.register_release(_release(with_worker=True))
    executor.release_digest = release.release_digest
    application_service.store.save_installation(
        ApplicationInstallation(
            installation_id="installation:app_recipes",
            application_id="app_recipes",
            installed_release_digest=release.release_digest,
            component_refs=(
                {
                    "component_ref": "scenario:recipes",
                    "package_digest": DIGEST_A,
                    "lifecycle": "bound",
                },
                {
                    "component_ref": "skill:recipes-worker",
                    "package_digest": DIGEST_B,
                    "lifecycle": "bound",
                },
            ),
            data_policy="retain",
            status="active",
            revision=1,
        ),
        expected_revision=0,
    )

    with pytest.raises(ApplicationRevisionConflict):
        application_service.plan_operation(
            "app_recipes",
            "relocate_component",
            component_ref="scenario:recipes",
            target_node_id="node-office",
            expected_revision=6,
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.plan",
            idempotency_key="stale-relocation",
        )

    relocation = application_service.plan_operation(
        "app_recipes",
        "relocate_component",
        component_ref="scenario:recipes",
        target_node_id="node-office",
        expected_revision=7,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="relocation-7",
    )
    assert relocation.plan["placement_change"]["target_node_id"] == "node-office"
    relocated = application_service.apply_operation(
        relocation.operation_id,
        plan_digest=relocation.plan_digest,
        idempotency_key=relocation.idempotency_key,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    assert relocated.status == "succeeded"

    removal = application_service.plan_operation(
        "app_recipes",
        "remove_component",
        component_ref="skill:recipes-worker",
        expected_revision=8,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="component-removal-8",
    )
    removed = application_service.apply_operation(
        removal.operation_id,
        plan_digest=removal.plan_digest,
        idempotency_key=removal.idempotency_key,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    assert removed.status == "succeeded"
    installation = application_service.store.get_installation("app_recipes")
    assert installation.revision == 2
    assert [item["component_ref"] for item in installation.component_refs] == [
        "scenario:recipes"
    ]

    component_install = application_service.plan_operation(
        "app_recipes",
        "install_component",
        component_ref="skill:recipes-worker",
        target_node_id="node-office",
        expected_revision=9,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="component-install-9",
    )
    installed = application_service.apply_operation(
        component_install.operation_id,
        plan_digest=component_install.plan_digest,
        idempotency_key=component_install.idempotency_key,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    assert installed.status == "succeeded"
    installation = application_service.store.get_installation("app_recipes")
    assert installation.revision == 3
    assert [item["component_ref"] for item in installation.component_refs] == [
        "scenario:recipes",
        "skill:recipes-worker",
    ]


def test_update_permission_review_requires_only_added_or_elevated_permissions(
    service: ApplicationService,
) -> None:
    first = service.register_release(_release())
    install = service.plan_operation(
        "app_recipes",
        "install",
        release_digest=first.release_digest,
        expected_revision=0,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="permission-review-base-install",
    )
    service.apply_operation(
        install.operation_id,
        plan_digest=install.plan_digest,
        idempotency_key=install.idempotency_key,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    unchanged = service.register_release(
        _release(version="1.0.1", package_digest=DIGEST_B)
    )
    expanded = service.register_release(
        _release(
            version="1.1.0",
            package_digest=DIGEST_C,
            permissions=("network.fetch", "workspace.read", "workspace.write"),
        )
    )

    unchanged_plan = service.plan_operation(
        "app_recipes",
        "update",
        release_digest=unchanged.release_digest,
        expected_revision=1,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="permission-review-unchanged",
    )
    expanded_plan = service.plan_operation(
        "app_recipes",
        "update",
        release_digest=expanded.release_digest,
        expected_revision=1,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="permission-review-expanded",
    )

    assert unchanged_plan.plan["permission_review"]["approval_required"] is False
    assert expanded_plan.plan["permission_review"]["approval_permissions"] == [
        "network.fetch"
    ]


def test_local_builder_beta_updates_display_flag_without_joining_public_testing(
    service,
):
    release = service.register_release(_release())
    subscription = service.set_subscription(
        "app_recipes",
        update_track="stable",
        update_policy="auto_compatible",
        paused=False,
        expected_revision=0,
    )
    service.select_runtime(
        webspace_id="desktop",
        application_id="app_recipes",
        source="local_trial",
        release_digest=release.release_digest,
        runtime_root_ref="trial:candidate",
        expected_revision=0,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    beta = service.list_models()[0]
    assert beta["use_prerelease"] and beta["local_beta_active"]
    assert beta["installed"] is True
    assert beta["installation"] is None
    assert beta["installed_release"] is None
    assert beta["local_beta_release"]["release_digest"] == release.release_digest
    assert beta["active_release"] == beta["local_beta_release"]
    assert beta["local_beta_releases"] == [beta["local_beta_release"]]
    assert (
        service.list_models(installed_only=True)[0]["application"]["application_id"]
        == "app_recipes"
    )
    assert not beta["prerelease_following"]
    assert service.store.get_subscription("app_recipes") == subscription
    service.select_runtime(
        webspace_id="desktop",
        application_id="app_recipes",
        source="stable_installation",
        release_digest=release.release_digest,
        runtime_root_ref="workspace",
        expected_revision=1,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    assert not service.list_models()[0]["use_prerelease"]
    service.set_subscription(
        "app_recipes",
        update_track="prerelease",
        update_policy="auto_compatible",
        paused=False,
        expected_revision=1,
    )
    stable = service.list_models()[0]
    assert (
        stable["use_prerelease"]
        and stable["prerelease_following"]
        and not stable["local_beta_active"]
    )


def test_channels_require_first_stable_then_exact_prerelease_promotion(
    service: ApplicationService,
) -> None:
    first = service.register_release(_release())
    second = service.register_release(
        _release(version="1.1.0", package_digest=DIGEST_B, lifecycle="prerelease")
    )

    with pytest.raises(ApplicationServiceError, match="existing stable"):
        service.move_channel(
            "app_recipes",
            "prerelease",
            second.release_digest,
            publisher_ref="subnet:sn_home",
            expected_release_digest=None,
        )

    service.move_channel(
        "app_recipes",
        "stable",
        first.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    service.move_channel(
        "app_recipes",
        "prerelease",
        second.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )

    with pytest.raises(ApplicationServiceError, match="exact current prerelease"):
        service.move_channel(
            "app_recipes",
            "stable",
            first.release_digest,
            publisher_ref="subnet:sn_home",
            expected_release_digest=first.release_digest,
        )

    channels = service.move_channel(
        "app_recipes",
        "stable",
        second.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=first.release_digest,
    )

    assert channels["channels"] == {"stable": second.release_digest}


def test_subscription_keeps_prerelease_intent_when_promoted_digest_becomes_stable(
    service: ApplicationService,
) -> None:
    first = service.register_release(_release())
    second = service.register_release(
        _release(version="1.1.0", package_digest=DIGEST_B, lifecycle="prerelease")
    )
    service.move_channel(
        "app_recipes",
        "stable",
        first.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    service.move_channel(
        "app_recipes",
        "prerelease",
        second.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    service.set_subscription(
        "app_recipes",
        update_track="prerelease",
        update_policy="notify",
        paused=False,
        expected_revision=0,
    )

    assert service.effective_release("app_recipes")["effective_channel"] == "prerelease"
    service.move_channel(
        "app_recipes",
        "stable",
        second.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=first.release_digest,
    )

    effective = service.effective_release("app_recipes")
    assert effective["effective_channel"] == "stable"
    assert effective["update_track"] == "prerelease"
    assert effective["release_digest"] == second.release_digest


def test_prerelease_rollout_is_sticky_and_falls_back_to_stable(
    service: ApplicationService,
) -> None:
    stable = service.register_release(_release())
    prerelease = service.register_release(
        _release(version="1.1.0", package_digest=DIGEST_B, lifecycle="prerelease")
    )
    service.move_channel(
        "app_recipes",
        "stable",
        stable.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    service.move_channel(
        "app_recipes",
        "prerelease",
        prerelease.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    service.set_subscription(
        "app_recipes",
        update_track="prerelease",
        update_policy="notify",
        paused=False,
        expected_revision=0,
    )
    rollout = ApplicationRolloutService(service)
    rollout.set_policy(
        "app_recipes",
        release_digest=prerelease.release_digest,
        publisher_ref="subnet:sn_home",
        percentage=50,
        paused=False,
        minimum_health_subnets=3,
        failure_threshold=0.5,
        expected_revision=0,
        idempotency_key="stage-half",
    )
    assignments = {
        subnet: rollout.assignment(
            "app_recipes",
            prerelease.release_digest,
            subscriber_subnet_ref=subnet,
        )
        for subnet in (f"subnet:guest-{index}" for index in range(100))
    }
    selected = next(
        subnet for subnet, value in assignments.items() if value["eligible"]
    )
    excluded = next(
        subnet for subnet, value in assignments.items() if not value["eligible"]
    )

    assert (
        rollout.assignment(
            "app_recipes", prerelease.release_digest, subscriber_subnet_ref=selected
        )
        == assignments[selected]
    )
    assert (
        service.effective_release("app_recipes", subscriber_subnet_ref=selected)[
            "release_digest"
        ]
        == prerelease.release_digest
    )
    fallback = service.effective_release("app_recipes", subscriber_subnet_ref=excluded)
    assert fallback["release_digest"] == stable.release_digest
    assert fallback["reason"] == "stable_rollout_fallback"


def test_rollout_health_counts_distinct_subnets_and_halts(
    service: ApplicationService,
) -> None:
    stable = service.register_release(_release())
    prerelease = service.register_release(
        _release(version="1.1.0", package_digest=DIGEST_B, lifecycle="prerelease")
    )
    service.move_channel(
        "app_recipes",
        "stable",
        stable.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    service.move_channel(
        "app_recipes",
        "prerelease",
        prerelease.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    rollout = ApplicationRolloutService(service)
    rollout.set_policy(
        "app_recipes",
        release_digest=prerelease.release_digest,
        publisher_ref="subnet:sn_home",
        percentage=100,
        paused=False,
        minimum_health_subnets=2,
        failure_threshold=0.5,
        expected_revision=0,
        idempotency_key="health-policy",
    )
    evidence = "sha256:" + "9" * 64
    for key, timestamp in (
        ("guest-failed-1", "2026-09-05T12:00:00+00:00"),
        ("guest-failed-2", "2026-09-05T12:01:00+00:00"),
    ):
        result = rollout.record_health(
            "app_recipes",
            prerelease.release_digest,
            subscriber_subnet_ref="subnet:guest-a",
            outcome="failed",
            installation_revision=1,
            evidence_digest=evidence,
            observed_at=timestamp,
            idempotency_key=key,
        )
    assert result["summary"]["distinct_subnets"] == 1
    halted = rollout.record_health(
        "app_recipes",
        prerelease.release_digest,
        subscriber_subnet_ref="subnet:guest-b",
        outcome="healthy",
        installation_revision=2,
        evidence_digest=evidence,
        observed_at="2026-09-05T12:02:00+00:00",
        idempotency_key="guest-healthy",
    )
    assert halted["halted"] is True
    assert halted["summary"]["failure_rate"] == 0.5
    policy = rollout.get_policy("app_recipes")
    assert policy is not None and policy["status"] == "halted"
    repeated = rollout.record_health(
        "app_recipes",
        prerelease.release_digest,
        subscriber_subnet_ref="subnet:guest-b",
        outcome="healthy",
        installation_revision=2,
        evidence_digest=evidence,
        observed_at="2026-09-05T12:02:00Z",
        idempotency_key="guest-healthy",
    )
    assert repeated["event"] == halted["event"]
    assert repeated["halted"] is True
    rollout_schema = json.loads(
        (
            Path(__file__).parents[1]
            / "src"
            / "adaos"
            / "abi"
            / "application.prerelease-rollout.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(rollout_schema).validate(policy)
    assert (
        rollout.set_policy(
            "app_recipes",
            release_digest=prerelease.release_digest,
            publisher_ref="subnet:sn_home",
            percentage=100,
            paused=False,
            minimum_health_subnets=2,
            failure_threshold=0.5,
            expected_revision=0,
            idempotency_key="health-policy",
        )["revision"]
        == 1
    )
    with pytest.raises(ApplicationRolloutError, match="explicit resume"):
        rollout.set_policy(
            "app_recipes",
            release_digest=prerelease.release_digest,
            publisher_ref="subnet:sn_home",
            percentage=25,
            paused=False,
            minimum_health_subnets=3,
            failure_threshold=0.5,
            expected_revision=policy["revision"],
            idempotency_key="resume-denied",
        )
    resumed = rollout.set_policy(
        "app_recipes",
        release_digest=prerelease.release_digest,
        publisher_ref="subnet:sn_home",
        percentage=25,
        paused=False,
        minimum_health_subnets=3,
        failure_threshold=0.5,
        expected_revision=policy["revision"],
        idempotency_key="resume-explicit",
        resume_after_halt=True,
    )
    assert resumed["status"] == "active"


def test_runtime_selection_projection_is_compare_and_swap(
    service: ApplicationService,
) -> None:
    release = service.register_release(_release())

    first = service.select_runtime(
        webspace_id="desktop",
        application_id="app_recipes",
        source="local_trial",
        release_digest=release.release_digest,
        runtime_root_ref="trial:candidate.recipes.1.0.0",
        expected_revision=0,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    second = service.select_runtime(
        webspace_id="desktop",
        application_id="app_recipes",
        source="stable_installation",
        release_digest=release.release_digest,
        runtime_root_ref="workspace",
        expected_revision=1,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    assert first.revision == 1
    assert second.revision == 2
    with pytest.raises(ApplicationRevisionConflict):
        service.select_runtime(
            webspace_id="desktop",
            application_id="app_recipes",
            source="stable_installation",
            release_digest=release.release_digest,
            runtime_root_ref="workspace",
            expected_revision=1,
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.apply",
        )


def test_runtime_channel_switch_updates_all_existing_webspaces(service, monkeypatch):
    release = service.register_release(_release())
    common = dict(
        application_id="app_recipes",
        release_digest=release.release_digest,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    for webspace in ("desktop", "office"):
        service.select_runtime(
            webspace_id=webspace,
            source="local_trial",
            runtime_root_ref="trial:candidate.recipes.1.0.0",
            expected_revision=0,
            **common,
        )
    service.select_runtime(
        webspace_id="desktop",
        source="stable_installation",
        runtime_root_ref="workspace",
        expected_revision=1,
        **common,
    )
    assert (
        service.store.get_runtime_selection("office", "app_recipes").runtime_root_ref
        == "workspace"
    )
    assert service.store.get_runtime_selection("office", "app_recipes").revision == 2
    monkeypatch.setattr(
        service.store,
        "list_applications",
        lambda: pytest.fail("Runtime reads must not scan the entire catalog"),
    )
    assert len(service.store.list_runtime_selections()) == 2
    with pytest.raises(ApplicationRevisionConflict):
        service.select_runtime(
            webspace_id="office",
            source="local_trial",
            runtime_root_ref="trial:candidate.recipes.1.0.0",
            expected_revision=1,
            **common,
        )


def test_install_update_snapshot_and_remove_are_reviewed_durable_operations(
    tmp_path: Path,
) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    service.register(_application())
    first = service.register_release(_release())
    second = service.register_release(
        _release(version="1.1.0", package_digest=DIGEST_B)
    )
    executor_results = [
        {"ok": True, "status": "succeeded"},
        {
            "ok": True,
            "status": "succeeded",
            "snapshot_receipt": {
                "snapshot_ref": "snapshot:recipes:1",
                "source_release_digest": first.release_digest,
                "consistency_boundary": "artifact_activation_transaction",
            },
        },
        {"ok": True, "status": "removed"},
    ]
    service.executor = lambda _plan: executor_results.pop(0)

    install = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-1",
        expected_revision=0,
        release_digest=first.release_digest,
    )
    replay = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-1",
        expected_revision=0,
        release_digest=first.release_digest,
    )
    assert replay.operation_id == install.operation_id
    installed = service.apply_operation(
        install.operation_id,
        plan_digest=install.plan_digest,
        idempotency_key="install-1",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    assert installed.status == "succeeded"
    assert install.plan["subscription_default"]["update_track"] == "stable"
    assert install.plan["subscription_default"]["update_policy"] == "auto_compatible"
    assert (
        install.plan["review_summary"] == "Review install for Application app_recipes."
    )
    assert install.plan["permissions"] == ["workspace.read", "workspace.write"]
    assert installed.result["subscription"]["update_track"] == "stable"
    assert installed.result["subscription"]["update_policy"] == "auto_compatible"
    assert (
        service.store.get_subscription("app_recipes").update_policy == "auto_compatible"
    )

    update = service.plan_operation(
        "app_recipes",
        "update",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="update-1",
        expected_revision=1,
        release_digest=second.release_digest,
    )
    updated = service.apply_operation(
        update.operation_id,
        plan_digest=update.plan_digest,
        idempotency_key="update-1",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    assert updated.status == "succeeded"
    assert updated.result["installation"]["snapshot_ref"] == "snapshot:recipes:1"

    simulation = service.simulate_removal("app_recipes", data_policy="retain")
    assert simulation["components"][0]["remove_package"] is True
    remove = service.plan_operation(
        "app_recipes",
        "remove",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="remove-1",
        expected_revision=2,
        data_policy="retain",
    )
    removed = service.apply_operation(
        remove.operation_id,
        plan_digest=remove.plan_digest,
        idempotency_key="remove-1",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    assert removed.status == "succeeded"
    assert removed.result["installation"]["status"] == "removed"


def test_install_materializes_only_declared_grant_on_install_access(
    tmp_path: Path,
) -> None:
    service = ApplicationService(
        ApplicationStore(tmp_path),
        executor=lambda _plan: {"ok": True, "status": "succeeded"},
    )
    service.register(_application())
    profile = ApplicationPermissionProfile.from_mapping(
        {
            "schema": "adaos.application.permission_profile.v1",
            "required": [
                {
                    "id": "workspace.read",
                    "purpose": "Load the installed Application",
                    "approval_policy": "grant_on_install",
                },
                {
                    "id": "workspace.write",
                    "purpose": "Modify shared workspace data",
                    "approval_policy": "explicit",
                },
            ],
            "optional": [],
        }
    )
    roles = normalize_application_roles(
        (
            {
                "id": "owner",
                "title": "Owner",
                "grants": ["app.view"],
                "assignable_to": ["owner"],
                "default_for": {"owner": "owner"},
                "requires_permissions": ["workspace.read"],
            },
        ),
        known_permissions=profile.flat_permissions,
    )
    base = _release()
    release = service.register_release(
        ApplicationRelease(
            application_id=base.application_id,
            publisher_ref=base.publisher_ref,
            project_release=base.project_release,
            accepted_candidate_id=base.accepted_candidate_id,
            acceptance_evidence=base.acceptance_evidence,
            provenance_refs=base.provenance_refs,
            permission_profile=profile,
            application_roles=roles,
            lifecycle=base.lifecycle,
        )
    )
    operation = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-access",
        expected_revision=0,
        release_digest=release.release_digest,
    )

    applied = service.apply_operation(
        operation.operation_id,
        plan_digest=operation.plan_digest,
        idempotency_key=operation.idempotency_key,
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    access = applied.result["install_access"]
    assert access["status"] == "ready"
    assert access["created"] is True
    assert access["permissions"] == ["workspace.read"]
    assert access["application_roles"] == ["owner"]
    grants = service.store.list_application_access_grants(
        "app_recipes", subject_ref="user:sn_home"
    )
    assert len(grants) == 1
    assert grants[0].permission_ceiling == ("workspace.read",)
    assert grants[0].application_roles == ("owner",)
    assert grants[0].reviewed_permission_profile_digest == profile.digest

    replay = service.ensure_install_access(
        "app_recipes",
        release_digest=release.release_digest,
        subnet_ref="subnet:sn_home",
        issuer_ref="system:test",
    )
    assert replay["status"] == "ready"
    assert replay["created"] is False
    assert len(
        service.store.list_application_access_grants(
            "app_recipes", subject_ref="user:sn_home"
        )
    ) == 1


def test_install_plans_exact_shared_dependencies_and_reuses_active_reference(
    service: ApplicationService,
) -> None:
    publisher_release = service.register_release(
        _release(with_shared_dependency=True)
    )
    publisher_plan = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="shared-provider-publisher-install",
        expected_revision=0,
        release_digest=publisher_release.release_digest,
    )
    publisher_dependency = next(
        item
        for item in publisher_plan.plan["components"]
        if item["component_ref"] == "skill:shared-mail-provider"
    )
    assert publisher_dependency == {
        "component_ref": "skill:shared-mail-provider",
        "package_digest": DIGEST_C,
        "lifecycle": "shared",
        "materialization": "activate",
        "reused_from_application_ids": [],
    }
    service.apply_operation(
        publisher_plan.operation_id,
        plan_digest=publisher_plan.plan_digest,
        idempotency_key="shared-provider-publisher-install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    service.register(_application("app_consumer", "consumer"))
    consumer_release = service.register_release(
        _release(
            application_id="app_consumer",
            project_id="consumer",
            with_shared_dependency=True,
        )
    )
    consumer_plan = service.plan_operation(
        "app_consumer",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="shared-provider-consumer-install",
        expected_revision=0,
        release_digest=consumer_release.release_digest,
    )
    consumer_dependency = next(
        item
        for item in consumer_plan.plan["components"]
        if item["component_ref"] == "skill:shared-mail-provider"
    )
    assert consumer_dependency["lifecycle"] == "shared"
    assert consumer_dependency["materialization"] == "reuse"
    assert consumer_dependency["reused_from_application_ids"] == ["app_recipes"]


def test_workspace_adoption_requires_resolved_dependency_closure(
    service: ApplicationService,
) -> None:
    from adaos.domain.artifact_release import WorkspaceLock, WorkspaceSlot

    release = service.register_release(_release(with_shared_dependency=True))
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-09-15T00:00:00Z",
        slots=(
            WorkspaceSlot(
                slot_id="main",
                project_id="recipes",
                release="recipes@1.0.0",
                release_digest=release.release_digest,
            ),
        ),
        components=release.project_release.components,
    )

    with pytest.raises(ApplicationServiceError, match="dependency closure"):
        service.reconcile_workspace_installation(
            "app_recipes", release.release_digest, lock
        )


def test_protected_system_application_rejects_remove_before_plan(
    tmp_path: Path,
) -> None:
    payload = _application().to_dict()
    payload["protection"] = {
        "system_application": True,
        "bootstrap_capable": True,
        "active_installation_removable": False,
        "recovery_surfaces": ["cli", "mcp"],
    }
    service = ApplicationService(
        ApplicationStore(tmp_path),
        executor=lambda _plan: {"ok": True, "status": "succeeded"},
    )
    service.register(Application.from_mapping(payload))
    release = service.register_release(_release())
    install = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-protected",
        expected_revision=0,
        release_digest=release.release_digest,
    )
    service.apply_operation(
        install.operation_id,
        plan_digest=install.plan_digest,
        idempotency_key="install-protected",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    with pytest.raises(ApplicationServiceError, match="cannot remove itself"):
        service.plan_operation(
            "app_recipes",
            "remove",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.plan",
            idempotency_key="remove-protected",
            expected_revision=1,
        )


def test_install_default_does_not_replace_an_explicit_subscription(
    tmp_path: Path,
) -> None:
    service = ApplicationService(
        ApplicationStore(tmp_path),
        executor=lambda _plan: {"ok": True, "status": "succeeded"},
    )
    service.register(_application())
    release = service.register_release(_release())
    service.set_subscription(
        "app_recipes",
        update_track="prerelease",
        update_policy="notify",
        paused=False,
        expected_revision=0,
    )

    install = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-explicit-subscription",
        expected_revision=0,
        release_digest=release.release_digest,
    )
    result = service.apply_operation(
        install.operation_id,
        plan_digest=install.plan_digest,
        idempotency_key="install-explicit-subscription",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    assert install.plan["subscription_default"] is None
    assert "subscription" not in result.result
    subscription = service.store.get_subscription("app_recipes")
    assert subscription.update_track == "prerelease"
    assert subscription.update_policy == "notify"


def test_shared_component_conflict_is_reported_before_apply(tmp_path: Path) -> None:
    service = ApplicationService(
        ApplicationStore(tmp_path),
        executor=lambda _plan: {"ok": True, "status": "succeeded"},
    )
    service.register(_application())
    service.register(_application("app_other", "other"))
    first = service.register_release(_release())
    conflicting = service.register_release(
        _release(
            application_id="app_other", project_id="other", package_digest=DIGEST_B
        )
    )
    install = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-recipes",
        expected_revision=0,
        release_digest=first.release_digest,
    )
    service.apply_operation(
        install.operation_id,
        plan_digest=install.plan_digest,
        idempotency_key="install-recipes",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    plan = service.plan_operation(
        "app_other",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-other",
        expected_revision=0,
        release_digest=conflicting.release_digest,
    )

    assert plan.plan["conflicts"][0]["component_ref"] == "scenario:recipes"
    with pytest.raises(ApplicationPlanConflict):
        service.apply_operation(
            plan.operation_id,
            plan_digest=plan.plan_digest,
            idempotency_key="install-other",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.apply",
        )


def test_unknown_executor_outcome_is_not_replayed_blindly(tmp_path: Path) -> None:
    def _unknown(_plan):
        raise TimeoutError("response lost")

    service = ApplicationService(ApplicationStore(tmp_path), executor=_unknown)
    service.register(_application())
    release = service.register_release(_release())
    plan = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-unknown",
        expected_revision=0,
        release_digest=release.release_digest,
    )

    with pytest.raises(TimeoutError):
        service.apply_operation(
            plan.operation_id,
            plan_digest=plan.plan_digest,
            idempotency_key="install-unknown",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.apply",
        )

    unknown = service.store.get_operation(plan.operation_id)
    assert unknown.status == "unknown"
    with pytest.raises(ApplicationServiceError, match="cannot apply"):
        service.apply_operation(
            plan.operation_id,
            plan_digest=plan.plan_digest,
            idempotency_key="install-unknown",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.apply",
        )


def test_failed_update_requires_verified_snapshot_restore_receipt(
    tmp_path: Path,
) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    service.register(_application())
    first = service.register_release(_release())
    second = service.register_release(
        _release(version="1.1.0", package_digest=DIGEST_B)
    )
    service.executor = lambda _plan: {"ok": True, "status": "succeeded"}
    install = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-before-failure",
        expected_revision=0,
        release_digest=first.release_digest,
    )
    service.apply_operation(
        install.operation_id,
        plan_digest=install.plan_digest,
        idempotency_key="install-before-failure",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    service.executor = lambda _plan: {
        "ok": False,
        "status": "failed",
        "reason": "migration_failed",
        "snapshot_receipt": {
            "snapshot_ref": "snapshot:recipes:failure",
            "source_release_digest": first.release_digest,
            "consistency_boundary": "artifact_activation_transaction",
        },
        "restore_receipt": {
            "snapshot_ref": "snapshot:recipes:failure",
            "restored_release_digest": first.release_digest,
            "status": "restored",
        },
    }
    update = service.plan_operation(
        "app_recipes",
        "update",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="failed-update",
        expected_revision=1,
        release_digest=second.release_digest,
    )

    result = service.apply_operation(
        update.operation_id,
        plan_digest=update.plan_digest,
        idempotency_key="failed-update",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    assert result.status == "failed"
    assert (
        service.store.get_installation("app_recipes").installed_release_digest
        == first.release_digest
    )


def test_read_models_separate_catalog_and_installed_state(
    service: ApplicationService,
) -> None:
    release = service.register_release(_release())
    service.move_channel(
        "app_recipes",
        "stable",
        release.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )

    model = service.list_models()[0]

    assert model["available"] is True
    assert model["installed"] is False
    assert model["effective_release"]["release_digest"] == release.release_digest
    assert model["installed_release"] is None
    assert model["marketplace_release"]["version"] == "1.0.0"
    assert model["prerelease_release"] is None
    assert model["auto_update_enabled"] is False
    assert service.list_models(installed_only=True) == []


def test_update_track_is_a_reviewed_operation_and_does_not_require_runtime_executor(
    tmp_path: Path,
) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    service.register(_application())
    operation = service.plan_operation(
        "app_recipes",
        "select_track",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="track-prerelease-1",
        expected_revision=0,
        update_track="prerelease",
        update_policy="notify",
    )

    result = service.apply_operation(
        operation.operation_id,
        plan_digest=operation.plan_digest,
        idempotency_key="track-prerelease-1",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )

    assert result.status == "succeeded"
    assert result.result["subscription"]["update_track"] == "prerelease"
    assert service.store.get_subscription("app_recipes").revision == 1


def test_operation_authority_and_reconnect_cursor_are_enforced(tmp_path: Path) -> None:
    published: list[dict] = []
    service = ApplicationService(
        ApplicationStore(tmp_path),
        executor=lambda _plan: {"ok": True, "status": "succeeded"},
        operation_publisher=published.append,
    )
    service.register(_application())
    release = service.register_release(_release())
    with pytest.raises(ApplicationServiceError, match="applications.plan"):
        service.plan_operation(
            "app_recipes",
            "install",
            actor_ref="user:owner",
            subnet_ref="subnet:sn_home",
            capability="applications.apply",
            idempotency_key="bad-authority",
            expected_revision=0,
            release_digest=release.release_digest,
        )
    operation = service.plan_operation(
        "app_recipes",
        "install",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-events",
        expected_revision=0,
        release_digest=release.release_digest,
    )
    first_page, cursor = service.store.list_operation_events(limit=1)
    assert [item["status"] for item in first_page] == ["planned"]
    assert cursor
    with pytest.raises(ApplicationServiceError, match="authority"):
        service.apply_operation(
            operation.operation_id,
            plan_digest=operation.plan_digest,
            idempotency_key="install-events",
            actor_ref="user:other",
            subnet_ref="subnet:sn_home",
            capability="applications.apply",
        )

    service.apply_operation(
        operation.operation_id,
        plan_digest=operation.plan_digest,
        idempotency_key="install-events",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
    )
    resumed, checkpoint = service.store.list_operation_events(cursor=cursor)

    assert [item["status"] for item in resumed] == ["applying", "succeeded"]
    assert checkpoint != cursor
    assert [item["status"] for item in published] == [
        "planned",
        "applying",
        "succeeded",
    ]


def test_public_stable_source_projection_is_exact_and_idempotent(
    service: ApplicationService,
) -> None:
    release = service.register_release(_release())
    service.move_channel(
        "app_recipes",
        "stable",
        release.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )
    calls = []

    def publish(**kwargs):
        calls.append(kwargs)
        return {
            "repository": "inimatic/recipes",
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "source_revision": kwargs["release"]["project_release"]["source_ref"][
                "revision"
            ],
        }

    projection = StableSourceProjectionService(service, publisher=publish)
    first = projection.publish(
        "app_recipes",
        release.release_digest,
        publisher_ref="subnet:sn_home",
        release_notes="Initial stable",
    )
    repeated = projection.publish(
        "app_recipes",
        release.release_digest,
        publisher_ref="subnet:sn_home",
        release_notes="Initial stable",
    )

    assert first == repeated
    assert first["source_revision"] == release.project_release.source_ref.revision
    assert projection.inspect("app_recipes", release.release_digest) == first
    assert len(calls) == 1


def test_public_application_projection_requires_exact_semantic_and_catalog_receipts(
    service: ApplicationService,
) -> None:
    release = service.register_release(_release())
    service.move_channel(
        "app_recipes",
        "stable",
        release.release_digest,
        publisher_ref="subnet:sn_home",
        expected_release_digest=None,
    )

    def incomplete(**kwargs):
        return {
            "repository": "inimatic/recipes",
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "source_revision": kwargs["release"]["project_release"]["source_ref"][
                "revision"
            ],
        }

    projection = StableSourceProjectionService(service, publisher=incomplete)
    with pytest.raises(ApplicationServiceError, match="public Application catalog"):
        projection.publish(
            "app_recipes",
            release.release_digest,
            publisher_ref="subnet:sn_home",
            release_notes="Public stable",
            require_application_catalog=True,
        )

    def complete(**kwargs):
        return {
            **incomplete(**kwargs),
            "semantic_publication": {
                "status": "prepared",
                "record_count": 3,
            },
            "application_catalog_publication": {
                "status": "prepared",
                "application_id": "app_recipes",
                "release_digest": release.release_digest,
                "projection_digest": DIGEST_C,
            },
        }

    projection = StableSourceProjectionService(service, publisher=complete)
    receipt = projection.publish(
        "app_recipes",
        release.release_digest,
        publisher_ref="subnet:sn_home",
        release_notes="Public stable",
        require_application_catalog=True,
    )

    assert receipt["semantic_publication"]["status"] == "prepared"
    assert receipt["application_catalog_publication"]["application_id"] == (
        "app_recipes"
    )


def test_git_stable_source_publisher_binds_candidate_release_and_registry() -> None:
    application = _application().to_dict()
    release = _release().to_dict()
    calls = []

    def publish_candidate(candidate_id: str, **kwargs):
        calls.append((candidate_id, kwargs))
        return {
            "candidate_id": candidate_id,
            "release_digest": release["release_digest"],
            "publication": {
                "repository": "registry",
                "branch": "main",
                "commit": "fedcba9876543210fedcba9876543210fedcba98",
            },
        }

    publisher = GitStableSourcePublisher(
        publish_candidate,
        repository="https://github.com/inimatic/adaos-registry.git",
        remote="registry",
        branch="main",
    )

    result = publisher(
        application=application,
        release=release,
        release_notes="Stable release notes",
    )

    assert calls == [
        (
            release["accepted_candidate_id"],
            {"remote": "registry", "branch": "main", "message": "Stable release notes"},
        )
    ]
    assert result == {
        "repository": "https://github.com/inimatic/adaos-registry.git",
        "commit": "fedcba9876543210fedcba9876543210fedcba98",
        "source_revision": release["project_release"]["source_ref"]["revision"],
    }
