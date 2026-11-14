"""A reconciled Trial failure must not replay a verified implementation."""
import copy
from types import SimpleNamespace

import pytest

from adaos.services.builder.automation import BuilderAutomationService
from test_builder_automation import _service


def fixture_state():
    primary = dict(ok=True, kind="skill", name="experiment_skill", version="0.1.1",
                   source_revision="forge-1", package_digest="sha256:" + "1" * 64)
    session = dict(object_type="skill", object_id="experiment_skill",
        change_id="change-1", current_task_id="task.1", status="commit_ready",
        task={"status": "completed"}, last_result={"summary": "ready"},
        reuse_confirmed_checkpoints=True, rebind_confirmed_checkpoint=True,
        completion_readiness={"ok": True, "task_id": "task.1", "vcs_checkpoints": [primary],
                              "workflow_checkpoint": {"ok": True}})
    workflow = dict(generation=8, automation={"status": "completed", "head_task_id": "task.1"},
        delivery=dict(status="idle", reconciled_at="2026-11-13T00:00:00+00:00",
            checkpoint_change_id="change-1", package_digest=primary["package_digest"],
            source_revision=primary["source_revision"]))
    return session, workflow


def test_rebind_restores_exact_receipt_without_repeating_finalization(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session, workflow = fixture_state()
    transitions, saved, notified = [], [], []

    def transition(kind, name, action, **kwargs):
        assert (kind, name, action) == ("skill", "experiment_skill", "checkpoint_recorded")
        assert kwargs["expected_generation"] == 8
        transitions.append(copy.deepcopy(kwargs))
        workflow["delivery"].update(status="checkpoint")
        return {"ok": True, "workflow": copy.deepcopy(workflow)}

    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self:
        SimpleNamespace(describe=lambda *_:copy.deepcopy(workflow), transition=transition))
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self,value:saved.append(copy.deepcopy(value)))
    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", lambda self,value:notified.append(value))
    monkeypatch.setattr(BuilderAutomationService, "_session_requires_aprobation_trial", lambda self,value:False)
    monkeypatch.setattr(BuilderAutomationService, "_prepare_and_activate_dev_skill",
                        lambda *args,**kwargs:pytest.fail("verified activation must not run again"))
    monkeypatch.setattr(BuilderAutomationService, "_checkpoint_completed_artifacts",
                        lambda *args,**kwargs:pytest.fail("confirmed Forge writes must not run again"))
    service._finalize_completed_session(session)
    assert saved[-1]["status"] == "completed" and len(notified) == 1
    assert len(transitions) == 1
    metadata = transitions[0]["metadata"]
    assert metadata["source_revision"] == "forge-1"
    assert metadata["package_digest"] == session["completion_readiness"]["vcs_checkpoints"][0]["package_digest"]
    assert metadata["change_id"] == "change-1" and metadata["task_id"] == "task.1"
    assert "rebind_confirmed_checkpoint" not in saved[-1]
    assert "reuse_confirmed_checkpoints" not in saved[-1]


@pytest.mark.parametrize("fault", ["source", "package", "task", "flag", "readiness", "unreconciled", "unconfirmed"])
def test_rebind_refuses_changed_or_unverified_checkpoint(tmp_path, monkeypatch, fault):
    service = _service(tmp_path)
    session, workflow = fixture_state()
    if fault == "source": workflow["delivery"]["source_revision"] = "another-source"
    if fault == "package": workflow["delivery"]["package_digest"] = "sha256:" + "2" * 64
    if fault == "task": workflow["automation"]["head_task_id"] = "task.2"
    if fault == "flag": session.pop("rebind_confirmed_checkpoint")
    if fault == "readiness": session["completion_readiness"]["ok"] = False
    if fault == "unreconciled": workflow["delivery"].pop("reconciled_at")
    if fault == "unconfirmed": session["completion_readiness"]["vcs_checkpoints"][0]["ok"] = False
    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self:SimpleNamespace(
        describe=lambda *_:copy.deepcopy(workflow),
        transition=lambda *a,**kw:pytest.fail("unverified checkpoint must not be rebound")))
    assert service._reconcile_completed_workflow(session) is None
