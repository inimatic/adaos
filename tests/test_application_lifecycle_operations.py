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


def test_multi_webspace_admission_compares_exact_identities_not_storage_order(operations):
    ops, subnet, _, candidate = operations
    selected = ops.store.get_runtime_selection("desktop", "example")
    ops.store.save_runtime_selection(replace(selected, webspace_id="another-room", revision=1), expected_revision=0)
    # The raw channel preserves insertion order; the public store is sorted.
    assert ApplicationRuntimeChannel(ops.state, "example").read()[0].webspace_id == "desktop"
    assert apply(ops, subnet, plan(ops, subnet, "retain_local_beta"))["status"] == "succeeded"


def test_sdk_returns_durable_unknown_receipt_but_never_masks_wrong_actor(operations, monkeypatch):
    ops, subnet, _, _ = operations
    from adaos.sdk import applications as sdk
    planned = plan(ops, subnet, "retain_local_beta")

    def interrupted(*_args, **_kwargs):
        raise RuntimeChannelConflict("SyntheticUnconfirmedEffect")

    monkeypatch.setattr(NativeTrialRuntime, "resolve", interrupted)
    result = sdk.apply_lifecycle_operation(planned["operation_id"], plan_digest=planned["plan_digest"],
        actor_ref="user:owner", subnet_ref=subnet, capability="applications.apply", idempotency_key=planned["idempotency_key"])
    assert result["status"] == "unknown"
    with pytest.raises(PermissionError):
        sdk.apply_lifecycle_operation(planned["operation_id"], plan_digest=planned["plan_digest"],
            actor_ref="user:other", subnet_ref=subnet, capability="applications.apply", idempotency_key=planned["idempotency_key"])


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
    (root / ".adaos/data-transition.json").write_text('{"stable_release_digest":null}', encoding="utf-8")
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


def test_admission_only_preserves_live_execution_and_exact_channel(operations):
    ops, subnet, _, candidate = operations
    planned = plan(ops, subnet, "retain_local_beta")
    channel = ApplicationRuntimeChannel(ops.state, "example")
    before = channel.read()
    with channel.execution("trial:candidate-example", candidate.release_digest):
        assert apply(ops, subnet, planned)["status"] == "succeeded"
        assert admission_path(ops.state, candidate.candidate_id).is_file()
    assert channel.read() == before


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


def test_candidate_decision_recovers_lost_activation_ack_without_new_trial(operations, monkeypatch):
    ops, subnet, activations, candidate = operations
    from adaos.services.applications.candidate_decisions import decide_local_candidate, recover_local_candidate_decision
    from adaos.services.applications.development import ApplicationDevelopmentCoordinator
    from adaos.services.artifact_pipeline.candidates import begin_trial
    running = begin_trial(replace(candidate, status="validated"), trial_id="lost-ack",
        audience="owner", data_mode="empty", lock_digest=candidate.package_digest,
        now="2026-01-01T00:00:00+00:00", health_receipt={"status": "passed"})
    CandidateStore(ops.state / "artifact_pipeline/candidates").save(running)
    original = type(activations).update

    def interrupted(self, candidate_id, **changes):
        if changes.get("status") == "completed":
            raise OSError("synthetic activation acknowledgement lost")
        return original(self, candidate_id, **changes)

    with monkeypatch.context() as scoped:
        scoped.setattr(type(activations), "update", interrupted)
        with pytest.raises(OSError):
            decide_local_candidate(ops.owner, "example", candidate.candidate_id, accepted=True,
                expected_candidate_digest=running.digest, actor_ref="user:owner", subnet_ref=subnet,
                capability="applications.develop", idempotency_key="lost-ack")
    op = ApplicationDevelopmentCoordinator(ops.state).list("example")[0]
    assert op["status"] == "unknown"
    result = recover_local_candidate_decision(ops.owner, op["operation_id"], actor_ref="user:owner",
        subnet_ref=subnet, capability="applications.recover")
    assert result["ok"] and activations.load(candidate.candidate_id)["status"] == "completed"
    assert len(CandidateStore(ops.state / "artifact_pipeline/candidates").load(candidate.candidate_id).trials) == 1


def test_inventory_protects_retained_sources_and_explicit_holds(operations):
    ops, _, activations, candidate = operations
    ApplicationRuntimeChannel(ops.state, "example").retire()
    activations.update(candidate.candidate_id, retention_holds=["synthetic-retain"])
    inventory = ops.trials("example", limit=1, include_storage=True)
    row = inventory["trials"][0]
    assert not row["deletion_review_available"]
    assert "explicit_retention_hold" in row["references"]
    assert row["storage_complete"] and row["storage_bytes"] > 0
    assert "archive" not in row


def test_inventory_batches_reference_scans_and_skips_jobs_for_referenced_trials(operations, monkeypatch):
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
    import adaos.services.applications.lifecycle_operations as lifecycle_operations
    ops, _, activations, candidate = operations
    counts = {"activations": 0, "operations": 0}
    original = TrialActivationStore.list_all
    original_operations = ops.store.list_operations
    def list_all(store):
        counts["activations"] += 1
        return original(store)
    def list_operations(*args, **kwargs):
        counts["operations"] += 1
        return original_operations(*args, **kwargs)
    def forbidden(*args):
        raise AssertionError("A selected Trial cannot become deletable by inspecting jobs")
    monkeypatch.setattr(TrialActivationStore, "list_all", list_all)
    monkeypatch.setattr(ops.store, "list_operations", list_operations)
    monkeypatch.setattr(lifecycle_operations, "assert_idle_jobs", forbidden)
    result = ops.trials("example")
    assert counts == {"activations": 1, "operations": 1}
    assert not result["trials"][0]["deletion_review_available"]


def test_archived_trial_keeps_its_retained_data_source_protected(operations):
    ops, subnet, activations, candidate = operations
    ApplicationRuntimeChannel(ops.state, "example").retire()
    other = activations.load(candidate.candidate_id)
    other_id = "candidate-archived-dependent"
    other["candidate_ref"]["candidate_id"] = other_id
    operation_id = "applifecycle." + "f" * 32
    archive = ops.state / "artifact_pipeline/trial-deletions" / operation_id / "runtime"
    binding = archive / ".adaos/data-transition.json"
    binding.parent.mkdir(parents=True)
    binding.write_text(json.dumps({"beta_source": {"runtime_root_ref": "trial:" + candidate.candidate_id}}), encoding="utf-8")
    other["lifecycle_deletion"] = {"operation_id": operation_id, "archive": str(archive), "data_policy": "retain"}
    activations.save(other)
    assert "retained_data_source:" + other_id in ops._references("example", candidate.candidate_id)
    with pytest.raises(RuntimeChannelConflict, match="TrialReferenced"):
        plan(ops, subnet, "delete_trial", candidate_id=candidate.candidate_id)
    assert trial_workspace_root(ops.workspace, candidate.candidate_id).is_dir()


def test_unknown_archived_reference_fails_closed_without_reading_marker_paths(operations):
    ops, _, activations, candidate = operations
    other = activations.load(candidate.candidate_id)
    other["candidate_ref"]["candidate_id"] = "candidate-unknown-archive"
    other["lifecycle_deletion"] = {"operation_id": "../../outside", "archive": "C:/private", "data_policy": "retain"}
    activations.save(other)
    assert "unresolved_archive:candidate-unknown-archive" in ops._references("example", candidate.candidate_id)


def test_core_ai_skill_consumer_blocks_lifecycle_effects(operations):
    import sqlite3
    ops, subnet, _, candidate = operations
    path = ops.state / "ai/local/ai.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE attempts(consumer TEXT,status TEXT)")
        db.execute("INSERT INTO attempts VALUES('skill:example','running')")
    ApplicationRuntimeChannel(ops.state, "example").retire()
    with pytest.raises(RuntimeChannelConflict, match="ActiveCoreJobs"):
        plan(ops, subnet, "delete_trial", candidate_id=candidate.candidate_id)


def test_core_clock_observes_retirement_and_reuses_durable_cursor(operations):
    ops, _, _, _ = operations
    from adaos.services.applications.lifecycle_runtime import observe_lifecycle_leases
    from adaos.services.applications.lifecycle_events import LifecycleEvents
    observe_lifecycle_leases(ops.owner)
    events = LifecycleEvents(ops.state).poll()
    ApplicationRuntimeChannel(ops.state, "example").retire()
    assert observe_lifecycle_leases(ops.owner)["changed"] == 1
    latest = LifecycleEvents(ops.state).poll(cursor=events["cursor"])
    assert latest["events"][0]["observation"]["admission"] == "not_selected"


def test_source_resolution_and_history_are_core_owned_and_redacted(operations):
    ops, subnet, _, _ = operations
    assert ops.observer.for_source("project", "example")["application_id"] == "example"
    assert ops.observer.for_source("skill", "example")["application_id"] == "example"
    assert ops.observer.for_source("project", "missing")["reason"] == "ApplicationNotRegistered"
    planned = plan(ops, subnet, "retain_local_beta")
    ops.service._transition_operation(ops.store.get_operation(planned["operation_id"]), "unknown",
                                      recovery_reason="exception contains private synthetic secret")
    history = ops.observer.operations("example")
    assert history["operations"][0]["recoverable"]
    assert "synthetic secret" not in json.dumps(history)


def _stable_uninstall_plan(ops, subnet, candidate):
    from adaos.domain.application import ApplicationInstallation
    ops.store.save_installation(ApplicationInstallation(installation_id="installation:example", application_id="example",
        installed_release_digest=candidate.release_digest,
        component_refs=({"component_ref":"skill:example","package_digest":candidate.package_digest,"lifecycle":"bound"},),
        data_policy="retain", status="active", revision=1),
        expected_revision=0)
    return plan(ops, subnet, "uninstall")


def test_stable_uninstall_fences_before_executor_and_can_recover_before_any_effect(operations):
    ops, subnet, _, candidate = operations
    planned = _stable_uninstall_plan(ops, subnet, candidate)
    calls = []
    ops.service.executor = lambda p: calls.append(p) or {"ok": True, "status": "removed"}
    channel = ApplicationRuntimeChannel(ops.state, "example")
    with channel.execution("trial:" + candidate.candidate_id, candidate.release_digest):
        with pytest.raises(RuntimeChannelConflict):
            ops.service.apply_operation(planned["operation_id"], plan_digest=planned["plan_digest"],
                idempotency_key=planned["idempotency_key"], actor_ref="user:owner", subnet_ref=subnet, capability="applications.apply")
        assert calls == []
    from adaos.services.applications.operation_recovery import recover_core_operation
    recovered = recover_core_operation(ops.owner, planned["operation_id"], plan_digest=planned["plan_digest"],
        actor_ref="user:owner", subnet_ref=subnet, capability="applications.recover")
    assert recovered["status"] == "succeeded" and len(calls) == 1 and channel.read() == ()


def test_stable_uninstall_recovers_metadata_without_repeating_executor(operations, monkeypatch):
    ops, subnet, _, candidate = operations
    planned = _stable_uninstall_plan(ops, subnet, candidate)
    calls = []
    ops.service.executor = lambda p: calls.append(p) or {"ok": True, "status": "removed"}
    original = type(ops.store).save_installation

    def interrupted(self, installation, **kwargs):
        if installation.status == "removed":
            raise OSError("synthetic metadata write interrupted")
        return original(self, installation, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(type(ops.store), "save_installation", interrupted)
        with pytest.raises(OSError):
            ops.service.apply_operation(planned["operation_id"], plan_digest=planned["plan_digest"],
                idempotency_key=planned["idempotency_key"], actor_ref="user:owner", subnet_ref=subnet, capability="applications.apply")
    from adaos.services.applications.operation_recovery import recover_core_operation
    recovered = recover_core_operation(ops.owner, planned["operation_id"], plan_digest=planned["plan_digest"],
        actor_ref="user:owner", subnet_ref=subnet, capability="applications.recover")
    assert recovered["status"] == "succeeded" and len(calls) == 1


def test_workspace_prerelease_is_not_misrepresented_as_a_stable_baseline(operations):
    ops, subnet, _, candidate = operations
    _stable_uninstall_plan(ops, subnet, candidate)
    selected = ops.store.get_runtime_selection("desktop", "example")
    ops.store.save_runtime_selection(replace(selected, source="stable_installation", runtime_root_ref="workspace",
        revision=selected.revision + 1), expected_revision=selected.revision)
    observed = ops.observer.snapshot("example")
    assert observed["runtime"]["admission_kind"] == "installed_prerelease"
    assert observed["installation"]["workspace_baseline_present"]
    assert not observed["installation"]["stable_baseline_present"]
    assert observed["summary"].startswith("Beta:")


def test_unknown_removal_executor_is_not_replayed_by_core_recovery(operations):
    ops, subnet, _, candidate = operations
    planned = _stable_uninstall_plan(ops, subnet, candidate)
    calls = []

    def executor(_plan):
        calls.append("effect")
        raise TimeoutError("synthetic lost external acknowledgement")

    ops.service.executor = executor
    with pytest.raises(TimeoutError):
        ops.service.apply_operation(planned["operation_id"], plan_digest=planned["plan_digest"],
            idempotency_key=planned["idempotency_key"], actor_ref="user:owner", subnet_ref=subnet, capability="applications.apply")
    from adaos.services.applications.operation_recovery import recover_core_operation
    with pytest.raises(RuntimeChannelConflict, match="ExecutorOutcomeUnknown"):
        recover_core_operation(ops.owner, planned["operation_id"], plan_digest=planned["plan_digest"],
            actor_ref="user:owner", subnet_ref=subnet, capability="applications.recover")
    assert calls == ["effect"]
    assert ApplicationRuntimeChannel(ops.state, "example").pending_transitions()


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


def test_operation_read_model_cache_is_defensive_and_revision_sensitive(operations, monkeypatch):
    import adaos.services.applications.store as module

    ops, subnet, *_ = operations
    planned = plan(ops, subnet, "retain_local_beta")
    module._OPERATION_VIEW_CACHE.clear()
    reads = []
    original = module._read

    def read(path):
        if "operations" in path.parts and path.name == "current.json":
            reads.append(path)
        return original(path)

    monkeypatch.setattr(module, "_read", read)
    first = ops.store.list_operations("example")
    assert reads
    count = len(reads)
    first[0].plan["untrusted_view_change"] = True
    second = ops.store.list_operations("example")
    assert "untrusted_view_change" not in second[0].plan
    assert len(reads) == count
    # Actual execution authority does not use the read-model cache.
    assert ops.store.get_operation(planned["operation_id"]).revision == second[0].revision
    assert len(reads) == count + 1
    advanced = replace(second[0], revision=second[0].revision + 1)
    ops.store.save_operation(advanced, expected_revision=second[0].revision)
    assert ops.store.list_operations("example")[0].revision == advanced.revision


def test_single_application_selection_read_does_not_scan_catalog_or_resurrect_tombstones(operations, monkeypatch):
    from adaos.services.artifact_pipeline.storage import atomic_write_json

    ops, *_ = operations
    monkeypatch.setattr(ApplicationRuntimeChannel, "read_catalog_snapshot", lambda *_args, **_kwargs:
        pytest.fail("one Application observation must not scan unrelated channels"))
    selected = ops.store.list_application_runtime_selections("example")
    assert len(selected) == 1
    legacy = ops.store._current_path("runtime_selections", ops.store._selection_identity("desktop", "example"))
    atomic_write_json(legacy, selected[0].to_dict())
    channel = ApplicationRuntimeChannel(ops.state, "example")
    with channel._connection() as connection:
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("UPDATE channel SET document=? WHERE id=1", (channel._encode(()),))
        connection.commit()
    assert ops.store.list_application_runtime_selections("example") == ()
