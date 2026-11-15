"""Publication phase, placement and recovery contracts across durable restarts."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_application_lifecycle_operations import (
    operations as operations,
    verified_operations as _verified_operations,
)
from test_application_finalization_runtime import confirm
from test_native_trial_runtime import (
    activation_record as activation_record,
    native_paths as native_paths,
)
from adaos.sdk.builder import applications
from adaos.services.applications.finalization_runtime import reconcile_finalizations
from adaos.services.applications.lifecycle_operations import (
    ApplicationLifecycleOperations,
)
from adaos.services.applications.publication_readiness import (
    PublicationPreconditionFailed,
    verify_trial_publication,
)
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
from adaos.services.applications.runtime_transition import (
    ApplicationRuntimeTransition,
    TransitionStep,
)
from adaos.domain.artifact_release import canonical_payload_digest

_WORKSPACE_ADMISSION = ApplicationLifecycleOperations._verify_workspace_admission
verified_operations = _verified_operations


@pytest.mark.parametrize("boundary", [None, "false", 0, "missing"])
def test_untrusted_legacy_phase_metadata_cannot_prove_absence_of_effects(
    verified_operations, monkeypatch, boundary
):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    retained = ops.store.get_operation(admitted["operation_id"])
    execution = {"status": "queued", "attempts": 0}
    if boundary != "missing":
        execution["effects_started"] = boundary
    ops.store.save_operation(
        replace(
            retained,
            status="unknown",
            revision=retained.revision + 1,
            result={**retained.result, "execution": execution},
        ),
        expected_revision=retained.revision,
    )
    monkeypatch.setattr(
        ApplicationLifecycleOperations,
        "_preflight_finalization",
        lambda *_: pytest.fail("Unknown effects were downgraded to a new preflight"),
    )

    def effect(*_):
        raise ValueError("uncertain retained effect")

    monkeypatch.setattr(ApplicationLifecycleOperations, "_effect", effect)
    with pytest.raises(ValueError, match="uncertain retained"):
        ops.apply(
            retained.operation_id,
            plan_digest=retained.plan_digest,
            actor_ref=retained.actor_ref,
            subnet_ref=retained.subnet_ref,
            capability="applications.recover",
            recover=True,
        )
    assert (
        ops.store.get_operation(retained.operation_id).result["execution"][
            "effects_started"
        ]
        is True
    )


def test_workspace_preflight_verifies_real_packages_without_activating_them(tmp_path):
    from test_artifact_workspace_activation import _built_scenario, _plan
    from adaos.services.artifact_pipeline import (
        ContentAddressedPackageStore,
        ReleaseRepository,
    )

    built = _built_scenario(tmp_path, version="1.0.0", marker="publication-preflight")
    plan = _plan(built)
    state, workspace = tmp_path / "state", tmp_path / "workspace"
    packages = ContentAddressedPackageStore(state / "artifact_pipeline/packages")
    packages.put(built.archive_bytes, expected_digest=built.ref.digest)
    ReleaseRepository(state / "artifact_pipeline/release-cache").put_release(plan)
    candidate = SimpleNamespace(
        project_id=plan.release.project_id, release_digest=plan.release.release_digest
    )
    _WORKSPACE_ADMISSION(SimpleNamespace(state=state, workspace=workspace), candidate)
    assert not (workspace / ".adaos/workspace.lock.json").exists()
    assert not (workspace / "scenarios/recipes").exists()
    packages.package_path(built.ref.digest).write_bytes(b"corrupt retained archive")
    from adaos.services.artifact_pipeline import ActivationError

    with pytest.raises(ActivationError, match="package verification failed"):
        _WORKSPACE_ADMISSION(
            SimpleNamespace(state=state, workspace=workspace), candidate
        )
    assert not (workspace / ".adaos/workspace.lock.json").exists()


def test_final_verification_uses_selected_release_not_activation_origin(
    verified_operations, monkeypatch
):
    ops, _, activations, candidate = verified_operations
    activations.update(
        candidate.candidate_id,
        status="completed",
        target={"webspace_id": "original-beta-room"},
    )
    monkeypatch.setattr(applications, "_ctx", lambda: ops.owner)
    monkeypatch.setattr(applications, "_state_dir", lambda: ops.state)
    evidence = []
    management = SimpleNamespace(
        applications=ops.service,
        promote_trial_verification_for_publication=lambda *args,
        **kwargs: evidence.append(kwargs) or {"ok": True},
    )
    result = applications._promote_local_trial_final_verification(
        management,
        application_id="example",
        webspace_id="desktop",
        candidate_id=candidate.candidate_id,
        candidate_digest=candidate.package_digest,
        release_digest=candidate.release_digest,
        actor_ref="user:owner",
        allow_completed=True,
    )
    assert result["ok"] and len(evidence) == 1
    assert evidence[0]["publication_evidence"].startswith(
        "release:trial-runtime:candidate-example#"
    )
    assert (
        activations.load(candidate.candidate_id)["target"]["webspace_id"]
        == "original-beta-room"
    )


@pytest.mark.parametrize(
    "fault", ["package", "release", "health", "authority", "lock", "placement"]
)
def test_placement_reuse_never_relaxes_immutable_trial_checks(
    verified_operations, fault
):
    ops, _, activations, candidate = verified_operations
    activation = activations.load(candidate.candidate_id)
    if fault == "package":
        activation["candidate_ref"]["package_digest"] = "sha256:" + "0" * 64
    if fault == "release":
        activation["candidate_ref"]["release_digest"] = "sha256:" + "0" * 64
    if fault == "health":
        activation["health_evidence"]["status"] = "failed"
    if fault == "authority":
        activation["runtime_binding"]["authority"] = "mutable-dev"
    if fault == "lock":
        activation["runtime_binding"]["workspace_lock_digest"] = "sha256:invalid"
    with pytest.raises(PublicationPreconditionFailed):
        verify_trial_publication(
            ops.store,
            activation,
            application_id="example",
            webspace_id="unselected-room" if fault == "placement" else "desktop",
            candidate_id=candidate.candidate_id,
            package_digest=candidate.package_digest,
            release_digest=candidate.release_digest,
            allow_completed=True,
        )


def test_invalid_review_is_rejected_before_creating_a_confirmed_intent(
    verified_operations,
):
    ops, subnet, activations, candidate = verified_operations
    identity = activations.load(candidate.candidate_id)["candidate_ref"]
    activations.update(
        candidate.candidate_id,
        candidate_ref={**identity, "package_digest": "sha256:" + "0" * 64},
    )
    with pytest.raises(PublicationPreconditionFailed, match="reviewed Candidate"):
        confirm(ops, subnet, candidate)
    assert ops.store.list_operations("example") == ()


def test_preflight_drift_is_terminal_and_does_not_fence_the_retained_beta(
    verified_operations, monkeypatch
):
    ops, subnet, activations, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    identity = activations.load(candidate.candidate_id)["candidate_ref"]
    activations.update(
        candidate.candidate_id,
        candidate_ref={**identity, "package_digest": "sha256:" + "0" * 64},
    )
    monkeypatch.setattr(
        ApplicationLifecycleOperations,
        "_effect",
        lambda *_: pytest.fail("Effect started after failed preflight"),
    )
    assert reconcile_finalizations(ops.owner) == [
        {"operation_id": admitted["operation_id"], "status": "failed"}
    ]
    failed = ops.store.get_operation(admitted["operation_id"])
    assert failed.result["execution"]["effects_started"] is False
    assert failed.result["execution"]["failure"] == {
        "code": "TrialIdentityMismatch",
        "stage": "preflight",
    }
    assert ops.observer.snapshot("example")["actions"]["deactivate_beta"]["eligible"]
    assert reconcile_finalizations(ops.owner) == []
    assert (
        ops.apply(
            failed.operation_id,
            plan_digest=failed.plan_digest,
            actor_ref=failed.actor_ref,
            subnet_ref=failed.subnet_ref,
            capability="applications.publish",
        )["status"]
        == "failed"
    )


def test_workspace_dependency_conflict_is_checked_before_effects(
    verified_operations, monkeypatch
):
    from adaos.services.artifact_pipeline.activation import SharedSkillConflict

    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)

    def conflict(*_):
        raise SharedSkillConflict(
            [{"skill": "skill:shared", "active_consumers": ["scenario:other"]}]
        )

    monkeypatch.setattr(
        ApplicationLifecycleOperations, "_verify_workspace_admission", conflict
    )
    monkeypatch.setattr(
        ApplicationLifecycleOperations,
        "_effect",
        lambda *_: pytest.fail("Data adoption started"),
    )
    assert reconcile_finalizations(ops.owner)[0]["status"] == "failed"
    result = ops.store.get_operation(admitted["operation_id"])
    assert result.result["execution"]["effects_started"] is False
    assert result.result["execution"]["failure"]["code"] == "SharedSkillConflict"
    assert ops.observer.snapshot("example")["actions"]["deactivate_beta"]["eligible"]


def test_typed_conflict_after_effect_start_cannot_clear_uncertainty(
    verified_operations, monkeypatch
):
    from adaos.services.artifact_pipeline.activation import SharedSkillConflict

    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)

    def conflict(*_):
        raise SharedSkillConflict(
            [{"skill": "skill:shared", "active_consumers": ["scenario:other"]}]
        )

    monkeypatch.setattr(ApplicationLifecycleOperations, "_effect", conflict)
    assert reconcile_finalizations(ops.owner)[0]["status"] == "pending"
    retained = ops.store.get_operation(admitted["operation_id"])
    assert (
        retained.status == "unknown"
        and retained.result["execution"]["effects_started"] is True
    )
    assert reconcile_finalizations(ops.owner) == []
    assert not ops.observer.snapshot("example")["actions"]["promote_stable"]["eligible"]


@pytest.mark.parametrize("phase", ["preflight", "effects"])
def test_restart_preserves_the_effect_boundary(verified_operations, monkeypatch, phase):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    method = "_preflight_finalization" if phase == "preflight" else "_effect"

    def interrupted(*_):
        raise SystemExit("process terminated")

    monkeypatch.setattr(ApplicationLifecycleOperations, method, interrupted)
    with pytest.raises(SystemExit):
        reconcile_finalizations(ops.owner)
    saved = ops.store.get_operation(admitted["operation_id"])
    assert saved.result["execution"]["effects_started"] is (phase == "effects")

    def rejected(*_):
        raise PublicationPreconditionFailed("TrialHealthUnverified")

    monkeypatch.setattr(ApplicationLifecycleOperations, method, rejected)
    reconcile_finalizations(ops.owner)
    saved = ops.store.get_operation(admitted["operation_id"])
    assert saved.status == ("failed" if phase == "preflight" else "unknown")


def _recovered_transition(ops, admitted, candidate, *, review=True):
    channel = ApplicationRuntimeChannel(ops.state, "example")
    runner = ApplicationRuntimeTransition(channel)
    source = channel.read()
    target = replace(
        source[0],
        source="stable_installation",
        runtime_root_ref="workspace",
        revision=source[0].revision + 1,
    )
    operation_id = f"application-stable:example:{candidate.candidate_id}"

    def fail(_):
        raise ValueError("preflight rejected")

    with pytest.raises(ValueError):
        runner.run(
            operation_id,
            contract_digest="sha256:" + "a" * 64,
            expected=source,
            target=target,
            steps=[TransitionStep("protect_and_adopt_data", fail)],
            publication_review={"review_id": admitted["operation_id"]}
            if review
            else None,
        )
    record = runner.abort(
        operation_id,
        contract_digest="sha256:" + "a" * 64,
        steps=[
            TransitionStep("prove_no_stable_effects", lambda _: {"ok": True}),
            TransitionStep("verify_source", lambda _: {"ok": True}),
        ],
    )
    retained = ops.store.get_operation(admitted["operation_id"])
    ops.store.save_operation(
        replace(
            retained,
            status="unknown",
            revision=retained.revision + 1,
            result={
                **retained.result,
                "execution": {"status": "blocked", "attempts": 1},
            },
        ),
        expected_revision=retained.revision,
    )
    return runner, record


@pytest.mark.parametrize("legacy", [False, True])
def test_verified_recovery_closes_original_intent_without_replaying_publication(
    verified_operations, monkeypatch, legacy
):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    _recovered_transition(ops, admitted, candidate, review=not legacy)
    monkeypatch.setattr(
        ApplicationLifecycleOperations,
        "_effect",
        lambda *_: pytest.fail("Cancelled publication was replayed"),
    )
    assert reconcile_finalizations(ops.owner) == [
        {"operation_id": admitted["operation_id"], "status": "cancelled"}
    ]
    saved = ops.store.get_operation(admitted["operation_id"])
    assert saved.result["recovery"]["receipt_digest"].startswith("sha256:")
    assert saved.result["confirmation"] == admitted["result"]["confirmation"]
    assert reconcile_finalizations(ops.owner) == []
    assert ops.observer.snapshot("example")["actions"]["promote_stable"]["eligible"]
    assert (
        confirm(ops, subnet, candidate, idempotency_key="new-reviewed-finalize")[
            "status"
        ]
        == "applying"
    )


@pytest.mark.parametrize(
    "fault",
    ["missing_receipt", "running", "release", "review", "fingerprint", "fresh_review"],
)
def test_incomplete_or_foreign_recovery_cannot_clear_an_unknown_outcome(
    verified_operations, monkeypatch, fault
):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    runner, record = _recovered_transition(ops, admitted, candidate)
    if fault == "missing_receipt":
        record["recovery"]["receipts"].pop("verify_source")
    if fault == "running":
        record["recovery"]["running_step"] = "verify_source"
    if fault == "release":
        record["intent"]["target"]["release_digest"] = "sha256:" + "0" * 64
    if fault == "review":
        record["intent"]["publication_review"]["review_id"] = "another-review"
    if fault == "fingerprint":
        record["fingerprint"] = "0" * 64
    if fault == "fresh_review":
        saved = ops.store.get_operation(admitted["operation_id"])
        plan = {
            **saved.plan,
            "recovered_adoption_digest": canonical_payload_digest(record),
        }
        digest = canonical_payload_digest(plan)
        ops.store.save_operation(
            replace(
                saved,
                plan=plan,
                plan_digest=digest,
                revision=saved.revision + 1,
                result={
                    **saved.result,
                    "confirmation": {
                        **saved.result["confirmation"],
                        "plan_digest": digest,
                    },
                },
            ),
            expected_revision=saved.revision,
        )
    runner._write(record["operation_id"], record)
    monkeypatch.setattr(
        ApplicationLifecycleOperations,
        "_effect",
        lambda *_: pytest.fail("Unknown effect replayed"),
    )
    assert reconcile_finalizations(ops.owner) == []
    assert ops.store.get_operation(admitted["operation_id"]).status == "unknown"
