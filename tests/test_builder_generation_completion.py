import json

import pytest

from adaos.services.builder.generation_runs import GenerationRuns
from adaos.services.builder.interpretation_runs import InterpretationRunRejected
from adaos.services.builder.task_spec import seal
from adaos.services.builder_context import digest
from adaos.services.policy.invocation import verified_invocation
from test_builder_generation_submissions import setup
from test_builder_generation_observations import observe, response
from test_builder_generation_runs import generation_rows
from test_builder_interpretation_runs import planned
from test_builder_workflow import workflow_project as _workflow_project

workflow_project = _workflow_project


def observed(service, folder, status="failed"):
    scope, plan, origin, binding, wire = setup(service, folder)
    with scope.submitting(wire["request_id"]) as register:
        register(response(status=status))
        observe(service, folder, plan, origin, binding, wire, response(status=status))
    return scope, plan, origin, binding


def finish(
    service,
    folder,
    plan,
    origin,
    binding,
    outcome="failed",
    cause_ref="llm_job:primary-job",
):
    return GenerationRuns(service).finish_failure(
        binding, origin, plan, journal_dir=folder, outcome=outcome, cause_ref=cause_ref
    )


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_negative_run_completion_is_evidence_owned_idempotent_and_non_authoring(
    workflow_project, tmp_path, outcome
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding = observed(service, folder, outcome)
    before = service.describe("scenario", "recipes")
    result = finish(service, folder, plan, origin, binding, outcome)
    after = service.describe("scenario", "recipes")
    for key in (
        "generation",
        "prototype",
        "automation",
        "active_phase",
        "governed",
        "history",
    ):
        assert after.get(key) == before.get(key), key
    (row,) = generation_rows(service)
    assert row["run_id"] == binding["run_id"] and row["status"] == outcome
    assert row["completed_at"] and row["execution_metrics"]["provider_calls"] == 1
    assert row["activity"] == "prototype_generation_" + outcome
    assert result["completion"]["completion_id"] in row["evidence_refs"]
    assert result["completion"]["inference_performed_by_completion"] is False
    assert result["completion"]["whole_turn_accounting_complete"] is False
    (receipt,) = folder.glob("generation-completion-*.json")
    saved = receipt.read_bytes()
    assert (
        finish(service, folder, plan, origin, binding, outcome)["completion"]
        == result["completion"]
    )
    assert receipt.read_bytes() == saved and generation_rows(service) == [row]
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        with pytest.raises(InterpretationRunRejected):
            GenerationRuns(service).assert_submission(binding, origin, plan)


@pytest.mark.parametrize(
    "outcome,cause",
    [
        ("succeeded", "llm_job:primary-job"),
        ("cancelled", "llm_job:primary-job"),
        ("failed", "llm_job:unrelated"),
        ("failed", "untyped user prose"),
    ],
)
def test_missing_or_contradictory_cause_does_not_close_or_relabel_a_run(
    workflow_project, tmp_path, outcome, cause
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    scope, plan, origin, binding = observed(service, folder)
    before = service.describe("scenario", "recipes")
    stored = scope.path.read_bytes()
    with pytest.raises(InterpretationRunRejected):
        finish(service, folder, plan, origin, binding, outcome, cause)
    assert service.describe("scenario", "recipes") == before
    assert scope.path.read_bytes() == stored and not list(
        folder.glob("generation-completion-*.json")
    )


def test_paid_success_can_finish_failed_only_with_a_retained_core_diagnosis(
    workflow_project, tmp_path
):
    from adaos.services.builder.repair_diagnosis import compile_repair_diagnosis

    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding = observed(service, folder, "succeeded")
    with pytest.raises(InterpretationRunRejected):
        finish(service, folder, plan, origin, binding)
    diagnosis = compile_repair_diagnosis(
        candidate_digest=digest({"candidate": "invalid"}),
        findings=[
            {"code": "task.fixture_value_mismatch", "gate": "G8", "owner": "candidate"}
        ],
        parent=plan,
        compiler_digest="compiler-control",
    )
    (folder / (diagnosis["diagnosis_id"].replace(":", "-") + ".json")).write_text(
        json.dumps(diagnosis), encoding="utf-8"
    )
    finish(service, folder, plan, origin, binding, cause_ref=diagnosis["diagnosis_id"])
    (row,) = generation_rows(service)
    assert row["status"] == "failed" and row["execution_metrics"]["provider_calls"] == 1
    assert diagnosis["diagnosis_id"] in row["evidence_refs"]


def test_late_completion_keeps_current_authoring_and_finishes_the_original_portfolio_run(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding = observed(service, folder)
    planned(service, "CH-later")
    before = service.describe("scenario", "recipes")
    result = finish(service, folder, plan, origin, binding)
    assert not result["ok"] and result["observation_recorded"]
    after = service.describe("scenario", "recipes")
    for key in (
        "change",
        "generation",
        "prototype",
        "automation",
        "active_phase",
        "governed",
        "history",
    ):
        assert after.get(key) == before.get(key), key
    state = service._read_state("scenario", "recipes")
    runs = state["workflow"]["change_portfolio"][origin["change_id"]]["change"]["runs"]
    old = next(row for row in runs if row["run_id"] == binding["run_id"])
    assert old["status"] == "failed" and old["execution_metrics"]["provider_calls"] == 1


def test_new_explicit_repair_turn_links_a_genuine_failed_parent_without_recharging_it(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding = observed(service, folder)
    finish(service, folder, plan, origin, binding)
    (old,) = generation_rows(service)
    record = json.loads(
        (folder / (plan["generation_plan_id"].replace(":", "-") + ".json")).read_text(
            encoding="utf-8"
        )
    )
    repair = seal(
        {
            **plan,
            "context_stage": "repair",
            "parent_generation_plan_ref": plan["generation_plan_id"],
            "parent_provider_job_id": "primary-job",
        },
        id_key="generation_plan_id",
        prefix="generation-plan:",
    )
    with verified_invocation(
        request_id="new-explicit-repair-turn", idempotency_key=None
    ):
        new = GenerationRuns(service).reserve(
            record["task_spec"], repair, origin, journal_dir=folder
        )
    rows = generation_rows(service)
    parent = next(row for row in rows if row["run_id"] == old["run_id"])
    child = next(row for row in rows if row["run_id"] == new["run_id"])
    assert parent == old and child["status"] == "queued"
    assert old["run_id"] in child["input_refs"]
    assert (
        not {"provider_calls", "llm_calls", "cost_usd", "source_refs"}
        & child["execution_metrics"].keys()
    )


def test_completion_reconciles_after_projection_failure_without_reopening_or_model_calls(
    workflow_project, tmp_path, monkeypatch
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    scope, plan, origin, binding = observed(service, folder)
    method = type(service).record_generation_observation
    monkeypatch.setattr(
        type(service),
        "record_generation_observation",
        lambda *args, **kwargs: {"ok": False},
    )
    with pytest.raises(InterpretationRunRejected):
        finish(service, folder, plan, origin, binding)
    (receipt,) = folder.glob("generation-completion-*.json")
    stored = receipt.read_bytes()
    assert json.loads(scope.path.read_text(encoding="utf-8"))["phase"] == "closed"
    assert generation_rows(service)[0]["status"] == "running"
    monkeypatch.setattr(type(service), "record_generation_observation", method)
    finish(service, folder, plan, origin, binding)
    assert (
        receipt.read_bytes() == stored
        and generation_rows(service)[0]["status"] == "failed"
    )
