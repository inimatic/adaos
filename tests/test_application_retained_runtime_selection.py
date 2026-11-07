"""Retained edition selection uses the existing reviewed lifecycle journal."""
from dataclasses import replace
import json

import pytest

from test_application_lifecycle_operations import operations, verified_operations, plan, apply
from test_native_trial_runtime import activation_record
from adaos.domain.application import ApplicationInstallation
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.applications import runtime_selection_review as selection
from adaos.services.applications.configuration import ApplicationConfigurationStore, _digest
from adaos.services.applications.local_beta_admission import admission_path
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep
from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root


@pytest.fixture
def editions(verified_operations, monkeypatch):
    ops, subnet, activations, candidate = verified_operations
    current = ops.store.get_release("example", candidate.release_digest)
    stable_project = replace(current.project_release, version="0.0.9", release_digest=None)
    stable = replace(current, project_release=stable_project, lifecycle="stable", accepted_candidate_id="candidate-stable")
    ops.service.register_release(stable)
    ops.store.save_installation(ApplicationInstallation(installation_id="installation:example", application_id="example",
        installed_release_digest=stable.release_digest,
        component_refs=({"component_ref":"skill:example","package_digest":candidate.package_digest,"lifecycle":"bound"},),
        data_policy="retain", status="active", revision=1), expected_revision=0)
    apply(ops, subnet, plan(ops, subnet, "retain_local_beta"))
    original = selection.verify_target
    def verify(ops, application_id, position, release_digest, candidate_id):
        if position == "private_stable":
            return {"ok":True,"release_digest":release_digest,"installation_revision":1,"source_verified":True}
        return original(ops, application_id, position, release_digest, candidate_id)
    monkeypatch.setattr(selection, "verify_target", verify)
    return ops, subnet, activations, candidate, stable


def test_switching_editions_retains_beta_admission_data_and_configurations(editions):
    ops, subnet, activations, candidate, stable = editions
    desktop=ops.store.get_runtime_selection("desktop","example")
    ops.store.save_runtime_selection(replace(desktop,webspace_id="tablet"),expected_revision=0)
    config = ApplicationConfigurationStore(ops.state, "example", "skill:example")
    schema={"type":"object","properties":{"mode":{"type":"string"}},"required":["mode"]}
    saved=config.set_stable(release_digest=stable.release_digest,schema=schema,values={"mode":"stable"},credentials={},expected_revision=0)
    saved=config.prepare_beta(candidate_id=candidate.candidate_id,release_digest=candidate.release_digest,
        source_release_digest=stable.release_digest,schema=schema,defaults={},expected_revision=saved["revision"])
    saved=config.update_beta(candidate_id=candidate.candidate_id,schema=schema,values={"mode":"beta"},credentials={},expected_revision=saved["revision"])
    before_admission=admission_path(ops.state,candidate.candidate_id).read_bytes()
    before_activation=activations.load(candidate.candidate_id)
    planned=plan(ops,subnet,"select_runtime",target_position_id="private_stable",release_digest=stable.release_digest)
    assert planned["plan"]["migration_required"] is False
    result=apply(ops,subnet,planned)
    assert result["status"]=="succeeded"
    assert config.read()["beta"]["active"] is False
    assert config.read()["stable"]["values"]=={"mode":"stable"}
    assert apply(ops,subnet,planned)==result
    assert admission_path(ops.state,candidate.candidate_id).read_bytes()==before_admission
    assert activations.load(candidate.candidate_id)==before_activation
    observed=ops.observer.snapshot("example")
    assert observed["runtime"]["source"]=="stable_installation"
    assert [item["candidate_id"] for item in observed["retained_betas"]]==[candidate.candidate_id]
    assert observed["retained_betas"][0]["availability"]=="ready"
    assert all(item.release_digest==stable.release_digest for item in ops.store.list_runtime_selections())
    # Edits made in Stable do not rebase the retained Beta's settings.
    saved=config.read()
    config.set_stable(release_digest=stable.release_digest,schema=schema,values={"mode":"stable-edited"},credentials={},expected_revision=saved["revision"])
    activations.update(candidate.candidate_id,expires_at="2000-01-01T00:00:00+00:00")
    back=plan(ops,subnet,"select_runtime",idempotency_key="select-back",target_position_id="private_beta",
              candidate_id=candidate.candidate_id,release_digest=candidate.release_digest)
    assert apply(ops,subnet,back)["status"]=="succeeded"
    assert config.read()["beta"]["active"] is True
    assert config.read()["beta"]["values"]=={"mode":"beta"}
    assert config.read()["stable"]["values"]=={"mode":"stable-edited"}
    assert admission_path(ops.state,candidate.candidate_id).read_bytes()==before_admission
    assert all(item.release_digest==candidate.release_digest for item in ops.store.list_runtime_selections())


def test_completed_trial_can_be_installed_while_stable_remains_selected(verified_operations):
    ops, subnet, activations, candidate = verified_operations
    beta = ops.store.get_release("example", candidate.release_digest)
    stable_project = replace(beta.project_release, version="0.0.9", release_digest=None)
    stable = replace(beta, project_release=stable_project, lifecycle="stable",
                     accepted_candidate_id="candidate-stable")
    ops.service.register_release(stable)
    ops.store.save_installation(ApplicationInstallation(
        installation_id="installation:example", application_id="example",
        installed_release_digest=stable.release_digest,
        component_refs=({"component_ref":"skill:example", "package_digest":candidate.package_digest,
                         "lifecycle":"bound"},),
        data_policy="retain", status="active", revision=1), expected_revision=0)

    channel = ApplicationRuntimeChannel(ops.state, "example")
    selected_beta = channel.read()[0]
    selected_stable = selected_beta.advance(expected_revision=selected_beta.revision,
        source="stable_installation", release_digest=stable.release_digest, runtime_root_ref="workspace")
    channel.select(selected_stable, expected_revision=selected_beta.revision)
    expected = channel.read()
    target = expected[0].advance(expected_revision=expected[0].revision, source="local_trial",
        release_digest=candidate.release_digest, runtime_root_ref=f"trial:{candidate.candidate_id}")
    transition_id = f"application-beta:example:{candidate.candidate_id}"
    transition = ApplicationRuntimeTransition(channel).run(
        transition_id, contract_digest="sha256:" + "b" * 64, expected=expected, target=target,
        steps=[TransitionStep("prepare", lambda _key: {"ok": True})])
    root = trial_workspace_root(ops.workspace, candidate.candidate_id)
    binding = {"schema":"adaos.application.local_data_binding.v1", "application_id":"example",
               "candidate_id":candidate.candidate_id, "release_digest":candidate.release_digest,
               "stable_release_digest":stable.release_digest}
    (root / ".adaos").mkdir(parents=True, exist_ok=True)
    (root / ".adaos/data-transition.json").write_text(json.dumps(binding), encoding="utf-8")
    activation = activations.load(candidate.candidate_id)
    activations.update(candidate.candidate_id, safety_evidence={
        **dict(activation.get("safety_evidence") or {}), "status":"verified",
        "data_transition":{"operation_id":transition_id,
                           "contract_digest":transition["intent"]["contract_digest"],
                           "completed":True}})
    current = channel.read()[0]
    channel.select(current.advance(expected_revision=current.revision, source="stable_installation",
        release_digest=stable.release_digest, runtime_root_ref="workspace"),
        expected_revision=current.revision)

    retained = plan(ops, subnet, "retain_local_beta", candidate_id=candidate.candidate_id)
    assert retained["plan"]["retention_mode"] == "inactive_completed_trial"
    assert retained["plan"]["retention_evidence"]["binding_digest"] == canonical_payload_digest(binding)
    assert apply(ops, subnet, retained)["status"] == "succeeded"
    assert channel.read()[0].release_digest == stable.release_digest
    assert admission_path(ops.state, candidate.candidate_id).is_file()

    selected = plan(ops, subnet, "select_runtime", idempotency_key="select-retained-beta",
                    target_position_id="private_beta", candidate_id=candidate.candidate_id,
                    release_digest=candidate.release_digest)
    assert apply(ops, subnet, selected)["status"] == "succeeded"
    assert channel.read()[0].release_digest == candidate.release_digest


def test_selection_rejects_stale_configuration_without_fencing_or_switching(editions):
    ops, subnet, _, _, stable=editions
    config=ApplicationConfigurationStore(ops.state,"example","skill:example")
    schema={"type":"object"}
    state=config.set_stable(release_digest=stable.release_digest,schema=schema,values={},credentials={},expected_revision=0)
    candidate=editions[3]
    config.prepare_beta(candidate_id=candidate.candidate_id,release_digest=candidate.release_digest,
        source_release_digest=stable.release_digest,schema=schema,defaults={},expected_revision=state["revision"])
    reviewed=plan(ops,subnet,"select_runtime",target_position_id="private_stable")
    state=config.read()
    config.update_beta(candidate_id=candidate.candidate_id,schema=schema,values={"new":"value"},credentials={},expected_revision=state["revision"])
    current=ops.store.list_runtime_selections()
    assert apply(ops,subnet,reviewed)["status"]=="failed"
    assert ops.store.list_runtime_selections()==current
    from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
    assert ApplicationRuntimeChannel(ops.state,"example").pending_transitions()==()


def test_selection_is_exact_about_target_and_idempotency(editions):
    ops,subnet,_,candidate,stable=editions
    with pytest.raises(RuntimeChannelConflict,match="TargetChanged"):
        plan(ops,subnet,"select_runtime",target_position_id="private_stable",release_digest=candidate.release_digest)
    first=plan(ops,subnet,"select_runtime",target_position_id="private_stable")
    with pytest.raises(RuntimeChannelConflict,match="DifferentLifecycleIntent"):
        plan(ops,subnet,"select_runtime",target_position_id="private_beta",candidate_id=candidate.candidate_id,
            idempotency_key=first["idempotency_key"],expected_revision=first["plan"]["expected_lifecycle_revision"])


def test_late_settings_write_retires_review_without_losing_that_write(editions,monkeypatch):
    ops,subnet,_,candidate,stable=editions
    config=ApplicationConfigurationStore(ops.state,"example","skill:example")
    state=config.set_stable(release_digest=stable.release_digest,schema={"type":"object"},values={},credentials={},expected_revision=0)
    config.prepare_beta(candidate_id=candidate.candidate_id,release_digest=candidate.release_digest,
        source_release_digest=stable.release_digest,schema={"type":"object"},defaults={},expected_revision=state["revision"])
    reviewed=plan(ops,subnet,"select_runtime",target_position_id="private_stable")
    original=ApplicationRuntimeTransition._begin
    def late_write(*args,**kwargs):
        config.update_beta(candidate_id=candidate.candidate_id,schema={"type":"object"},values={"late":"keep"},
                           credentials={},expected_revision=config.read()["revision"])
        return original(*args,**kwargs)
    monkeypatch.setattr(ApplicationRuntimeTransition,"_begin",late_write)
    assert apply(ops,subnet,reviewed)["status"]=="failed"
    assert config.read()["beta"]["values"]=={"late":"keep"}
    assert ops.store.get_runtime_selection("desktop","example").release_digest==candidate.release_digest
    assert ApplicationRuntimeChannel(ops.state,"example").pending_transitions()==()


def test_interrupted_configuration_selection_recovers_exactly_once(editions,monkeypatch):
    ops,subnet,_,candidate,stable=editions
    config=ApplicationConfigurationStore(ops.state,"example","skill:example")
    state=config.set_stable(release_digest=stable.release_digest,schema={"type":"object"},values={},credentials={},expected_revision=0)
    config.prepare_beta(candidate_id=candidate.candidate_id,release_digest=candidate.release_digest,
        source_release_digest=stable.release_digest,schema={"type":"object"},defaults={},expected_revision=state["revision"])
    reviewed=plan(ops,subnet,"select_runtime",target_position_id="private_stable")
    original=ApplicationRuntimeTransition._write
    def interrupted(self,operation_id,record):
        if "select_retained_configuration" in record["receipts"]:
            raise RuntimeError("lost selection receipt acknowledgement")
        return original(self,operation_id,record)
    with monkeypatch.context() as scoped:
        scoped.setattr(ApplicationRuntimeTransition,"_write",interrupted)
        with pytest.raises(RuntimeError,match="lost selection receipt"):
            apply(ops,subnet,reviewed)
    revision=config.read()["revision"]
    assert config.read()["beta"]["active"] is False
    assert ApplicationRuntimeChannel(ops.state,"example").pending_transitions()
    assert ops.observer.snapshot("example")["transition"]["target_version"]==stable.project_release.version
    result=ops.apply(reviewed["operation_id"],plan_digest=reviewed["plan_digest"],actor_ref="user:owner",
                     subnet_ref=subnet,capability="applications.recover",recover=True)
    assert result["status"]=="succeeded"
    assert config.read()["revision"]==revision
    assert ops.store.get_runtime_selection("desktop","example").release_digest==stable.release_digest
    assert ApplicationRuntimeChannel(ops.state,"example").pending_transitions()==()


def test_selection_does_not_interrupt_component_generation_jobs(editions):
    import sqlite3
    ops,subnet,_,candidate,stable=editions
    path=ops.state/'ai/selection-test/ai.sqlite3'
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE attempts (consumer TEXT,status TEXT)')
        db.execute('INSERT INTO attempts VALUES (?,?)',('skill:example','running'))
    selected=ops.store.get_runtime_selection('desktop','example')
    with pytest.raises(RuntimeChannelConflict,match='ActiveCoreJobs'):
        plan(ops,subnet,'select_runtime',target_position_id='private_stable')
    assert ops.store.get_runtime_selection('desktop','example')==selected
    assert ApplicationRuntimeChannel(ops.state,'example').pending_transitions()==()


def test_stable_channel_is_authority_despite_legacy_trial_envelope_tag(operations,monkeypatch):
    ops,subnet,_,candidate=operations
    assert ops.store.get_release('example',candidate.release_digest).lifecycle=='trial'
    ops.store.save_installation(ApplicationInstallation(installation_id='installation:example',application_id='example',
        installed_release_digest=candidate.release_digest,
        component_refs=({'component_ref':'skill:example','package_digest':candidate.package_digest,'lifecycle':'bound'},),
        data_policy='retain',status='active',revision=1),expected_revision=0)
    ops.store.set_channel('example','stable',candidate.release_digest,expected_release_digest=None)
    monkeypatch.setattr('adaos.services.applications.runtime_integrity._release_observation',lambda *args,**kwargs:{'status':'aligned'})
    assert selection.verify_target(ops,'example','private_stable',candidate.release_digest,None)['ok']
    with pytest.raises(RuntimeChannelConflict,match='AlreadyAcceptedAsStable'):
        selection.verify_target(ops,'example','private_beta',candidate.release_digest,candidate.candidate_id)
