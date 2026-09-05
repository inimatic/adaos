from __future__ import annotations

from pathlib import Path

import pytest

from adaos.domain.application import (
    Application,
    ApplicationRelease,
)
from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
)
from adaos.services.applications import (
    ApplicationPlanConflict,
    ApplicationRevisionConflict,
    ApplicationService,
    ApplicationServiceError,
    ApplicationStore,
    ApplicationStoreError,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _application(application_id: str = "app_recipes", project_id: str = "recipes") -> Application:
    return Application(
        application_id=application_id,
        legacy_project_id=project_id,
        publisher_ref="subnet:sn_home",
        slug=project_id,
        display={"title": project_id.title(), "summary": None},
        visibility="public",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:recipes"},),
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
    project_release = ProjectRelease(
        project_id=project_id,
        version=version,
        source_ref=source,
        components=(package,),
        validation_evidence=({"status": "passed"},),
    ).seal()
    return ApplicationRelease(
        application_id=application_id,
        publisher_ref="subnet:sn_home",
        project_release=project_release,
        accepted_candidate_id=f"candidate.{project_id}.{version}",
        acceptance_evidence=({"decision": "accepted", "release_digest": project_release.release_digest},),
        provenance_refs=(DIGEST_C,),
        lifecycle=lifecycle,
    )


@pytest.fixture
def service(tmp_path: Path) -> ApplicationService:
    result = ApplicationService(ApplicationStore(tmp_path), executor=lambda _plan: {"ok": True, "status": "succeeded"})
    result.register(_application())
    return result


def test_store_enforces_one_to_one_legacy_project_mapping(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path)
    store.save_application(_application(), expected_revision=0)

    with pytest.raises(ApplicationStoreError, match="already mapped"):
        store.save_application(_application("app_other", "recipes"), expected_revision=0)


def test_channels_require_first_stable_then_exact_prerelease_promotion(service: ApplicationService) -> None:
    first = service.register_release(_release())
    second = service.register_release(_release(version="1.1.0", package_digest=DIGEST_B, lifecycle="prerelease"))

    with pytest.raises(ApplicationServiceError, match="existing stable"):
        service.move_channel(
            "app_recipes", "prerelease", second.release_digest,
            publisher_ref="subnet:sn_home", expected_release_digest=None,
        )

    service.move_channel(
        "app_recipes", "stable", first.release_digest,
        publisher_ref="subnet:sn_home", expected_release_digest=None,
    )
    service.move_channel(
        "app_recipes", "prerelease", second.release_digest,
        publisher_ref="subnet:sn_home", expected_release_digest=None,
    )

    with pytest.raises(ApplicationServiceError, match="exact current prerelease"):
        service.move_channel(
            "app_recipes", "stable", first.release_digest,
            publisher_ref="subnet:sn_home", expected_release_digest=first.release_digest,
        )

    channels = service.move_channel(
        "app_recipes", "stable", second.release_digest,
        publisher_ref="subnet:sn_home", expected_release_digest=first.release_digest,
    )

    assert channels["channels"] == {"stable": second.release_digest}


def test_subscription_keeps_prerelease_intent_when_promoted_digest_becomes_stable(service: ApplicationService) -> None:
    first = service.register_release(_release())
    second = service.register_release(_release(version="1.1.0", package_digest=DIGEST_B, lifecycle="prerelease"))
    service.move_channel("app_recipes", "stable", first.release_digest, publisher_ref="subnet:sn_home", expected_release_digest=None)
    service.move_channel("app_recipes", "prerelease", second.release_digest, publisher_ref="subnet:sn_home", expected_release_digest=None)
    service.set_subscription(
        "app_recipes", update_track="prerelease", update_policy="notify", paused=False, expected_revision=0,
    )

    assert service.effective_release("app_recipes")["effective_channel"] == "prerelease"
    service.move_channel("app_recipes", "stable", second.release_digest, publisher_ref="subnet:sn_home", expected_release_digest=first.release_digest)

    effective = service.effective_release("app_recipes")
    assert effective["effective_channel"] == "stable"
    assert effective["update_track"] == "prerelease"
    assert effective["release_digest"] == second.release_digest


def test_runtime_selection_is_webspace_scoped_and_compare_and_swap(service: ApplicationService) -> None:
    release = service.register_release(_release())

    first = service.select_runtime(
        webspace_id="desktop", application_id="app_recipes", source="local_trial",
        release_digest=release.release_digest, runtime_root_ref="trial:candidate.recipes.1.0.0", expected_revision=0,
    )
    second = service.select_runtime(
        webspace_id="desktop", application_id="app_recipes", source="stable_installation",
        release_digest=release.release_digest, runtime_root_ref="workspace", expected_revision=1,
    )

    assert first.revision == 1
    assert second.revision == 2
    with pytest.raises(ApplicationRevisionConflict):
        service.select_runtime(
            webspace_id="desktop", application_id="app_recipes", source="stable_installation",
            release_digest=release.release_digest, runtime_root_ref="workspace", expected_revision=1,
        )


def test_install_update_snapshot_and_remove_are_reviewed_durable_operations(tmp_path: Path) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    service.register(_application())
    first = service.register_release(_release())
    second = service.register_release(_release(version="1.1.0", package_digest=DIGEST_B))
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
        "app_recipes", "install", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="install-1", expected_revision=0, release_digest=first.release_digest,
    )
    installed = service.apply_operation(install.operation_id, plan_digest=install.plan_digest, idempotency_key="install-1")
    assert installed.status == "succeeded"

    update = service.plan_operation(
        "app_recipes", "update", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="update-1", expected_revision=1, release_digest=second.release_digest,
    )
    updated = service.apply_operation(update.operation_id, plan_digest=update.plan_digest, idempotency_key="update-1")
    assert updated.status == "succeeded"
    assert updated.result["installation"]["snapshot_ref"] == "snapshot:recipes:1"

    simulation = service.simulate_removal("app_recipes", data_policy="retain")
    assert simulation["components"][0]["remove_package"] is True
    remove = service.plan_operation(
        "app_recipes", "remove", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="remove-1", expected_revision=2, data_policy="retain",
    )
    removed = service.apply_operation(remove.operation_id, plan_digest=remove.plan_digest, idempotency_key="remove-1")
    assert removed.status == "succeeded"
    assert removed.result["installation"]["status"] == "removed"


def test_shared_component_conflict_is_reported_before_apply(tmp_path: Path) -> None:
    service = ApplicationService(ApplicationStore(tmp_path), executor=lambda _plan: {"ok": True, "status": "succeeded"})
    service.register(_application())
    service.register(_application("app_other", "other"))
    first = service.register_release(_release())
    conflicting = service.register_release(_release(application_id="app_other", project_id="other", package_digest=DIGEST_B))
    install = service.plan_operation(
        "app_recipes", "install", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="install-recipes", expected_revision=0, release_digest=first.release_digest,
    )
    service.apply_operation(install.operation_id, plan_digest=install.plan_digest, idempotency_key="install-recipes")

    plan = service.plan_operation(
        "app_other", "install", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="install-other", expected_revision=0, release_digest=conflicting.release_digest,
    )

    assert plan.plan["conflicts"][0]["component_ref"] == "scenario:recipes"
    with pytest.raises(ApplicationPlanConflict):
        service.apply_operation(plan.operation_id, plan_digest=plan.plan_digest, idempotency_key="install-other")


def test_unknown_executor_outcome_is_not_replayed_blindly(tmp_path: Path) -> None:
    def _unknown(_plan):
        raise TimeoutError("response lost")

    service = ApplicationService(ApplicationStore(tmp_path), executor=_unknown)
    service.register(_application())
    release = service.register_release(_release())
    plan = service.plan_operation(
        "app_recipes", "install", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="install-unknown", expected_revision=0, release_digest=release.release_digest,
    )

    with pytest.raises(TimeoutError):
        service.apply_operation(plan.operation_id, plan_digest=plan.plan_digest, idempotency_key="install-unknown")

    unknown = service.store.get_operation(plan.operation_id)
    assert unknown.status == "unknown"
    with pytest.raises(ApplicationServiceError, match="cannot apply"):
        service.apply_operation(plan.operation_id, plan_digest=plan.plan_digest, idempotency_key="install-unknown")


def test_failed_update_requires_verified_snapshot_restore_receipt(tmp_path: Path) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    service.register(_application())
    first = service.register_release(_release())
    second = service.register_release(_release(version="1.1.0", package_digest=DIGEST_B))
    service.executor = lambda _plan: {"ok": True, "status": "succeeded"}
    install = service.plan_operation(
        "app_recipes", "install", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="install-before-failure", expected_revision=0, release_digest=first.release_digest,
    )
    service.apply_operation(install.operation_id, plan_digest=install.plan_digest, idempotency_key="install-before-failure")
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
        "app_recipes", "update", actor_ref="user:owner", subnet_ref="subnet:sn_home",
        idempotency_key="failed-update", expected_revision=1, release_digest=second.release_digest,
    )

    result = service.apply_operation(
        update.operation_id, plan_digest=update.plan_digest, idempotency_key="failed-update"
    )

    assert result.status == "failed"
    assert service.store.get_installation("app_recipes").installed_release_digest == first.release_digest


def test_read_models_separate_catalog_and_installed_state(service: ApplicationService) -> None:
    release = service.register_release(_release())
    service.move_channel("app_recipes", "stable", release.release_digest, publisher_ref="subnet:sn_home", expected_release_digest=None)

    model = service.list_models()[0]

    assert model["available"] is True
    assert model["installed"] is False
    assert model["effective_release"]["release_digest"] == release.release_digest
    assert service.list_models(installed_only=True) == []


def test_update_track_is_a_reviewed_operation_and_does_not_require_runtime_executor(tmp_path: Path) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    service.register(_application())
    operation = service.plan_operation(
        "app_recipes",
        "select_track",
        actor_ref="user:owner",
        subnet_ref="subnet:sn_home",
        idempotency_key="track-prerelease-1",
        expected_revision=0,
        update_track="prerelease",
        update_policy="notify",
    )

    result = service.apply_operation(
        operation.operation_id,
        plan_digest=operation.plan_digest,
        idempotency_key="track-prerelease-1",
    )

    assert result.status == "succeeded"
    assert result.result["subscription"]["update_track"] == "prerelease"
    assert service.store.get_subscription("app_recipes").revision == 1
