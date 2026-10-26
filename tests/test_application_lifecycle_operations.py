from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from test_native_trial_runtime import activation_record, native_paths  # shared synthetic immutable runtime
from adaos.domain.application import Application, ApplicationRelease, RuntimeSelection
from adaos.services.agent_context import get_ctx
from adaos.services.applications.lifecycle_operations import (
    ApplicationLifecycleOperations, cancel_planned_operation,
)
from adaos.services.applications.local_beta_admission import admission_path
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from adaos.services.applications.trial_runtime import NativeTrialRuntime, TrialRuntimeUnavailable
from adaos.services.artifact_pipeline.candidates import CandidateRecord, CandidateStore
from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root


@pytest.fixture
def operations(activation_record, monkeypatch):
    activations, release, package = activation_record
    owner = get_ctx()
    monkeypatch.setattr(owner, "config", SimpleNamespace(subnet_id_value="sn_lifecycle"))
    ops = ApplicationLifecycleOperations(owner)
    config = owner.config
    subnet = str(config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id)
    subnet = subnet if subnet.startswith("subnet:") else "subnet:" + subnet
    app = Application(application_id="example", legacy_project_id="example", publisher_ref=subnet,
        slug="example", display={"title": "Synthetic lifecycle"}, visibility="private",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "skill:example"},),
        publisher={"publisher_ref": subnet, "display_name": "Synthetic", "subnet_short_ref": "sn_lifecycle",
            "release_key_ref": "subnet-key:release-signing:1", "release_key_fingerprint": package.digest,
            "home_zone": "local-dev", "trust_relation": "local"})
    ops.service.register(app)
    envelope = ApplicationRelease(application_id="example", publisher_ref=subnet,
        project_release=release, accepted_candidate_id="candidate-example", lifecycle="trial",
        acceptance_evidence=({"status": "passed"},), provenance_refs=(release.release_digest,))
    ops.service.register_release(envelope)
    source = release.source_ref
    candidate = CandidateRecord(candidate_id="candidate-example", project_id="example", version="0.1.0",
        source_ref=source, base_release="genesis", base_release_digest="sha256:" + "0" * 64,
        base_source_ref=source, package_digest=package.digest, release_digest=release.release_digest,
        change_ids=("synthetic-lifecycle",), created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00", status="accepted")
    CandidateStore(ops.state / "artifact_pipeline/candidates").save(candidate)
    activations.update("candidate-example", health_evidence={"status": "passed"},
                       expires_at="2040-01-01T00:00:00+00:00")
    ops.store.save_runtime_selection(RuntimeSelection(webspace_id="desktop", application_id="example",
        source="local_trial", release_digest=release.release_digest, runtime_root_ref="trial:candidate-example", revision=1),
        expected_revision=0)
    return ops, subnet, activations, candidate


def plan(ops, subnet, action, **extra):
    return ops.plan("example", action, actor_ref="user:owner", subnet_ref=subnet,
        capability="applications.plan", idempotency_key=extra.pop("idempotency_key", "test:" + action),
        expected_revision=extra.pop("expected_revision", ops.observer.snapshot("example")["revision"]), **extra)


def apply(ops, subnet, planned, **extra):
    return ops.apply(planned["operation_id"], plan_digest=planned["plan_digest"], actor_ref="user:owner",
                     subnet_ref=subnet, capability="applications.apply", **extra)


def test_installed_local_beta_does_not_edit_trial_lease_or_publish(operations):
    ops, subnet, activations, candidate = operations
    before = activations.load(candidate.candidate_id)
    planned = plan(ops, subnet, "retain_local_beta")
    result = apply(ops, subnet, planned)
    assert result["status"] == "succeeded"
    assert activations.load(candidate.candidate_id) == before
    assert ops.observer.snapshot("example")["runtime"]["admission_kind"] == "installed_local_beta"
    assert ops.observer.snapshot("example")["runtime"]["expires_at"] is None
    assert apply(ops, subnet, planned) == result
    # The old temporary lease can expire; explicit installation is now the
    # distinct authority. No application code is invoked by this test.
    activations.update(candidate.candidate_id, expires_at="2000-01-01T00:00:00+00:00")
    assert NativeTrialRuntime.resolve(ops.owner, candidate.candidate_id, candidate.release_digest)


def test_accepted_expired_trial_is_not_silently_installed(operations):
    ops, subnet, activations, candidate = operations
    activations.update(candidate.candidate_id, expires_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(RuntimeChannelConflict, match="HealthyAccepted"):
        plan(ops, subnet, "retain_local_beta")
    with pytest.raises(TrialRuntimeUnavailable, match="expired"):
        NativeTrialRuntime.resolve(ops.owner, candidate.candidate_id, candidate.release_digest)


def test_admission_rechecks_candidate_and_immutable_runtime(operations):
    ops, subnet, _, candidate = operations
    apply(ops, subnet, plan(ops, subnet, "retain_local_beta"))
    CandidateStore(ops.state / "artifact_pipeline/candidates").save(replace(candidate, status="rejected"))
    with pytest.raises(ValueError, match="accepted Candidate"):
        NativeTrialRuntime.resolve(ops.owner, candidate.candidate_id, candidate.release_digest)
    recovery = NativeTrialRuntime._resolve_immutable(ops.owner, candidate.candidate_id,
                                                    candidate.release_digest, for_data_recovery=True)
    with pytest.raises(TrialRuntimeUnavailable, match="cannot execute"):
        recovery.context()


def test_plans_are_exact_idempotent_and_stale_apply_is_rejected(operations):
    ops, subnet, activations, candidate = operations
    revision = ops.observer.snapshot("example")["revision"]
    first = plan(ops, subnet, "retain_local_beta", expected_revision=revision)
    assert plan(ops, subnet, "retain_local_beta", expected_revision=revision) == first
    with pytest.raises(RuntimeChannelConflict, match="DifferentLifecycleIntent"):
        plan(ops, subnet, "deactivate_beta", idempotency_key="test:retain_local_beta", expected_revision=revision)
    activations.update(candidate.candidate_id, expires_at="2039-01-01T00:00:00+00:00")
    with pytest.raises(RuntimeChannelConflict, match="RevisionChanged"):
        apply(ops, subnet, first)
    assert not admission_path(ops.state, candidate.candidate_id).exists()


def test_cancel_is_durable_and_cannot_cancel_execution(operations):
    ops, subnet, _, _ = operations
    planned = plan(ops, subnet, "retain_local_beta")
    cancelled = cancel_planned_operation(ops.owner, planned["operation_id"], plan_digest=planned["plan_digest"],
        actor_ref="user:owner", subnet_ref=subnet, capability="applications.plan")
    assert cancelled["status"] == "cancelled"
    with pytest.raises(RuntimeChannelConflict):
        apply(ops, subnet, planned)


def test_legacy_apply_cannot_misinterpret_lifecycle_operation_as_remove(operations):
    ops, subnet, _, _ = operations
    planned = plan(ops, subnet, "retain_local_beta")
    from adaos.services.applications.service import ApplicationServiceError
    with pytest.raises(ApplicationServiceError, match="Core lifecycle"):
        ops.service.apply_operation(planned["operation_id"], plan_digest=planned["plan_digest"],
            idempotency_key=planned["idempotency_key"], actor_ref="user:owner", subnet_ref=subnet,
            capability="applications.apply")


def test_selected_trial_cannot_be_deleted_and_actor_cannot_be_substituted(operations):
    ops, subnet, _, _ = operations
    with pytest.raises(RuntimeChannelConflict, match="TrialReferenced"):
        plan(ops, subnet, "delete_trial")
    planned = plan(ops, subnet, "retain_local_beta")
    with pytest.raises(PermissionError):
        ops.apply(planned["operation_id"], plan_digest=planned["plan_digest"], actor_ref="user:other",
                  subnet_ref=subnet, capability="applications.apply")


def test_deactivation_retains_candidate_decision_and_beta_data(operations, monkeypatch):
    ops, subnet, _, candidate = operations
    from adaos.services.applications.data_lifecycle import LocalApplicationDataLifecycle
    from adaos.services.applications import local_release_transition
    channel = ApplicationRuntimeChannel(ops.state, "example")
    channel.retire()
    data = LocalApplicationDataLifecycle(state_root=ops.state,
        private_root=ops.workspace.parent, application_id="example", candidate_id=candidate.candidate_id,
        release_digest=candidate.release_digest, stable_digest=None, components=())
    data.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    monkeypatch.setattr(local_release_transition, "bind_local_data_lifecycle", lambda *_, **kw: data)
    root = trial_workspace_root(ops.workspace, candidate.candidate_id)
    payload = root / "retained-test-data.json"
    payload.write_text('{"synthetic":true}', encoding="utf-8")
    planned = plan(ops, subnet, "deactivate_beta", acknowledge_write_loss=True)
    assert apply(ops, subnet, planned)["status"] == "succeeded"
    assert channel.read() == () and payload.is_file()
    assert CandidateStore(ops.state / "artifact_pipeline/candidates").load(candidate.candidate_id).status == "accepted"


def test_unused_trial_deletion_is_recoverable_and_keeps_data(operations):
    ops, subnet, activations, candidate = operations
    ApplicationRuntimeChannel(ops.state, "example").retire()
    root = trial_workspace_root(ops.workspace, candidate.candidate_id)
    (root / "retained-test-data.json").write_text('{"synthetic":true}', encoding="utf-8")
    planned = plan(ops, subnet, "delete_trial", candidate_id=candidate.candidate_id)
    assert apply(ops, subnet, planned)["status"] == "succeeded"
    assert not root.exists()
    deletion = activations.load(candidate.candidate_id)["lifecycle_deletion"]
    from pathlib import Path
    assert (Path(deletion["archive"]) / "retained-test-data.json").is_file()


def test_live_execution_fences_admission_and_exact_recovery_does_not_resubmit(operations):
    ops, subnet, _, candidate = operations
    planned = plan(ops, subnet, "retain_local_beta")
    channel = ApplicationRuntimeChannel(ops.state, "example")
    with channel.execution("trial:candidate-example", candidate.release_digest):
        with pytest.raises(RuntimeChannelConflict, match="executing"):
            apply(ops, subnet, planned)
        assert not admission_path(ops.state, candidate.candidate_id).exists()
    assert ops.store.get_operation(planned["operation_id"]).status == "unknown"
    result = ops.apply(planned["operation_id"], plan_digest=planned["plan_digest"], actor_ref="user:owner",
                       subnet_ref=subnet, capability="applications.recover", recover=True)
    assert result["status"] == "succeeded"


def test_unresolved_generation_jobs_block_trial_deletion(operations):
    ops, subnet, _, candidate = operations
    ApplicationRuntimeChannel(ops.state, "example").retire()
    root = trial_workspace_root(ops.workspace, candidate.candidate_id)
    path = root / ".runtime/state/content-generation" / ("a" * 64) / (("b" * 64) + ".json")
    path.parent.mkdir(parents=True)
    path.write_text('{"status":"queued","request_id":"synthetic"}', encoding="utf-8")
    with pytest.raises(RuntimeChannelConflict, match="UnresolvedGenerationJobs"):
        plan(ops, subnet, "delete_trial", candidate_id=candidate.candidate_id)


def test_lost_deletion_metadata_ack_is_reconciled_without_overwrite(operations, monkeypatch):
    ops, subnet, activations, candidate = operations
    ApplicationRuntimeChannel(ops.state, "example").retire()
    planned = plan(ops, subnet, "delete_trial", candidate_id=candidate.candidate_id)
    original = type(activations).update

    def interrupt(self, candidate_id, **changes):
        if "lifecycle_deletion" in changes:
            raise OSError("synthetic interrupted metadata write")
        return original(self, candidate_id, **changes)

    with monkeypatch.context() as scoped:
        scoped.setattr(type(activations), "update", interrupt)
        with pytest.raises(OSError):
            apply(ops, subnet, planned)
    assert ops.store.get_operation(planned["operation_id"]).status == "unknown"
    result = ops.apply(planned["operation_id"], plan_digest=planned["plan_digest"], actor_ref="user:owner",
                       subnet_ref=subnet, capability="applications.recover", recover=True)
    assert result["status"] == "succeeded"
    assert activations.load(candidate.candidate_id)["lifecycle_deletion"]["operation_id"] == planned["operation_id"]


def test_core_clock_observes_expiry_without_opening_any_application(operations):
    ops, _, _, _ = operations
    from adaos.services.applications.lifecycle_runtime import observe_lifecycle_leases
    from adaos.services.applications.lifecycle_events import LifecycleEvents
    events = LifecycleEvents(ops.state)
    assert observe_lifecycle_leases(ops.owner, now=datetime(2039, 12, 30, tzinfo=timezone.utc))["changed"] == 1
    first = events.poll(limit=1)
    assert first["events"][0]["observation"]["admission"] == "expiring"
    assert observe_lifecycle_leases(ops.owner, now=datetime(2039, 12, 30, 0, 1, tzinfo=timezone.utc))["changed"] == 0
    assert observe_lifecycle_leases(ops.owner, now=datetime(2040, 1, 2, tzinfo=timezone.utc))["changed"] == 1
    resumed = LifecycleEvents(ops.state).poll(cursor=first["cursor"])
    assert len(resumed["events"]) == 1
    assert resumed["events"][0]["observation"]["reason"] == "TrialExpired"


def test_candidate_acceptance_is_core_owned_and_has_no_scenario_or_root_dependency(operations):
    ops, subnet, _, candidate = operations
    from adaos.services.applications.candidate_decisions import decide_local_candidate
    from adaos.services.artifact_pipeline.candidates import begin_trial
    running = begin_trial(replace(candidate, status="validated"), trial_id="synthetic-running",
        audience="owner", data_mode="empty", lock_digest=candidate.package_digest,
        now="2026-01-01T00:00:00+00:00", health_receipt={"status": "passed"})
    CandidateStore(ops.state / "artifact_pipeline/candidates").save(running)
    result = decide_local_candidate(ops.owner, "example", candidate.candidate_id, accepted=True,
        expected_candidate_digest=running.digest, actor_ref="user:owner", subnet_ref=subnet,
        capability="applications.develop", idempotency_key="core-accept-synthetic")
    assert result["candidate"]["status"] == "accepted"
    assert ops.observer.snapshot("example")["candidate"]["accepted"]


@pytest.mark.asyncio
async def test_materialization_failure_is_pending_and_recoverable_without_migration(operations, monkeypatch):
    ops = operations[0]
    from adaos.services.applications.materialization import request_materialization, reconcile_materializations, request_path
    from adaos.services.component_updates import ComponentUpdateService
    from adaos.services.scenario import webspace_runtime
    monkeypatch.setattr(ComponentUpdateService, "reconcile_local_trials", lambda *a, **k: None)
    calls = []

    async def refresh(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise TimeoutError("synthetic private transport detail")
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime, "rebuild_webspace_from_sources", refresh)
    request_materialization(ops.owner, "example")
    assert (await reconcile_materializations(ops.owner))[0]["status"] == "pending"
    assert "private transport" not in request_path(ops.state, "example").read_text()
    assert (await reconcile_materializations(ops.owner))[0]["status"] == "completed"
    assert ops.observer.snapshot("example")["materialization"]["status"] == "completed"
