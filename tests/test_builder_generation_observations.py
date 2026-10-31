import copy
import json

import pytest

from adaos.services.builder.generation_observations import (
    observe_provider_response,
    project_run_measurements,
)
from adaos.services.builder.generation_runs import GenerationRuns
from adaos.services.builder.interpretation_runs import InterpretationRunRejected
from adaos.services.builder.task_spec import seal
from adaos.services.builder_context import digest
from test_builder_generation_runs import qualified, invoke, generation_rows
from test_builder_workflow import workflow_project as _workflow_project
from test_builder_interpretation_runs import planned

workflow_project = _workflow_project


def retained(service, folder):
    task, plan, origin = qualified(service, folder / "seed")
    observer = GenerationRuns(service)
    binding = invoke(observer, task, plan, origin)["binding"]
    folder.mkdir(exist_ok=True)
    wire = write_wire(folder, task, plan, origin, binding)
    return task, plan, origin, binding, wire


def write_wire(
    folder, task, plan, origin, binding, job="primary-job", request="request-primary"
):
    record = {
        "generation_plan": plan,
        "task_spec": task,
        "execution_origin": origin,
        "execution_run_binding": binding,
    }
    name = (
        "repair-plan-" + plan["generation_plan_id"].partition(":")[2]
        if plan.get("context_stage") == "repair"
        else plan["generation_plan_id"].replace(":", "-")
    )
    (folder / (name + ".json")).write_text(json.dumps(record), encoding="utf-8")
    wire = {
        "schema": "adaos.builder.wire_admission_record.v1",
        "request_id": request,
        "job_id": job,
        "execution_origin": origin,
        "execution_run_binding": binding,
        "context_admission": {
            "generation_plan_ref": plan["generation_plan_id"],
            "parent_generation_plan_ref": plan.get("parent_generation_plan_ref"),
            "execution_origin_required": True,
            "execution_run_required": True,
        },
    }
    (folder / ("wire-" + digest(request)[7:39] + ".json")).write_text(
        json.dumps(wire), encoding="utf-8"
    )
    return wire


def response(job="primary-job", status="succeeded", calls=1):
    return {
        "job_id": job,
        "status": status,
        "model": "selected",
        "output_text": "PRIVATE MODEL OUTPUT",
        "error": {"message": "PRIVATE EXCEPTION"},
        "telemetry": {
            "provider_calls": calls,
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "cost": {"basis": "estimate", "estimated_usd": 0.01},
            "private": {"token": "PRIVATE TOKEN"},
        },
    }


def observe(service, folder, plan, origin, binding, wire, result):
    return observe_provider_response(
        folder,
        binding=binding,
        origin=origin,
        plan=plan,
        wire=wire,
        response=result,
        workflow=service,
    )


def test_measurements_retain_actual_job_once_without_authoring_or_completion_claim(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    _, plan, origin, binding, wire = retained(service, tmp_path / "jobs")
    before = service.describe("scenario", "recipes")
    observe(service, tmp_path / "jobs", plan, origin, binding, wire, response())
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
    assert row["status"] == "running" and row["completed_at"] is None
    metrics = row["execution_metrics"]
    assert metrics["llm_calls"] == metrics["provider_calls"] == 1
    assert metrics["cost_usd"] == 0.01 and metrics["repair_attempts"] == 0
    assert metrics["source_refs"] == ["llm_job:primary-job"]
    paths = list((tmp_path / "jobs").glob("generation-observation-*.json"))
    assert len(paths) == 1 and "PRIVATE" not in paths[0].read_text(encoding="utf-8")
    observe(service, tmp_path / "jobs", plan, origin, binding, wire, response())
    assert generation_rows(service) == [row]


def test_unknown_counts_do_not_become_zero_and_terminal_measurements_survive_stale_poll(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    _, plan, origin, binding, wire = retained(service, tmp_path / "jobs")
    observe(
        service,
        tmp_path / "jobs",
        plan,
        origin,
        binding,
        wire,
        {"job_id": "primary-job", "status": "queued"},
    )
    metrics = generation_rows(service)[0]["execution_metrics"]
    assert not metrics["provider_calls_complete"] and not metrics["usage_complete"]
    assert (
        not {"provider_calls", "input_tokens", "output_tokens", "cost_usd"}
        & metrics.keys()
    )
    observe(service, tmp_path / "jobs", plan, origin, binding, wire, response())
    (row,) = generation_rows(service)
    observe(
        service,
        tmp_path / "jobs",
        plan,
        origin,
        binding,
        wire,
        {"job_id": "primary-job", "status": "queued"},
    )
    assert generation_rows(service) == [row]
    before = {
        path: path.read_bytes()
        for path in (tmp_path / "jobs").glob("generation-observation-*.json")
    }
    with pytest.raises(InterpretationRunRejected):
        observe(
            service, tmp_path / "jobs", plan, origin, binding, wire, response(calls=2)
        )
    assert all(path.read_bytes() == content for path, content in before.items())
    assert generation_rows(service) == [row]


def test_same_invocation_repair_is_disjoint_not_primary_plus_aggregate(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    task, plan, origin, binding, wire = retained(service, folder)
    observe(service, folder, plan, origin, binding, wire, response(status="failed"))
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
    child = invoke(GenerationRuns(service), task, repair, origin)["binding"]
    child_wire = write_wire(
        folder, task, repair, origin, child, "repair-job", "request-repair"
    )
    observe(
        service,
        folder,
        repair,
        origin,
        child,
        child_wire,
        response("repair-job", calls=2),
    )
    (row,) = generation_rows(service)
    metrics = row["execution_metrics"]
    assert metrics["llm_calls"] == 2 and metrics["provider_calls"] == 3
    assert metrics["cost_usd"] == 0.02 and metrics["repair_attempts"] == 1
    assert metrics["input_tokens"] == 200 and metrics["output_tokens"] == 40
    assert set(metrics["source_refs"]) == {"llm_job:primary-job", "llm_job:repair-job"}
    bad = response("repair-job")
    bad["telemetry"]["repair"] = {"root_job_id": "primary-job"}
    with pytest.raises(InterpretationRunRejected):
        observe(service, folder, repair, origin, child, child_wire, bad)
    assert generation_rows(service) == [row]


@pytest.mark.parametrize(
    "fault",
    [
        "unretained_wire",
        "wrong_job",
        "wrong_request",
        "wrong_input",
        "wrong_scope",
        "missing_run",
        "missing_binding_evidence",
    ],
)
def test_measurements_cannot_invent_or_rebind_a_legacy_run(
    workflow_project, tmp_path, fault
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding, wire = retained(service, folder)
    result = response()
    if fault == "unretained_wire":
        wire = {**wire, "request_id": "unretained"}
    elif fault == "wrong_job":
        result["job_id"] = "different"
    elif fault == "wrong_request":
        result["request_id"] = "different-request"
    elif fault == "wrong_scope":
        result["tool_telemetry"] = {"turn_scope": "b" * 64}
    elif fault == "wrong_input":
        wire["context_admission"]["provider_attempt_input_digest"] = "expected-input"
        (folder / ("wire-" + digest(wire["request_id"])[7:39] + ".json")).write_text(
            json.dumps(wire), encoding="utf-8"
        )
        result["input_digest"] = "different-input"
    else:
        state = service._read_state("scenario", "recipes")
        row = state["workflow"]["change"]["runs"][-1]
        if fault == "missing_run":
            state["workflow"]["change"]["runs"].remove(row)
        else:
            row["evidence_refs"].remove(binding["binding_id"])
        service._write_state("scenario", "recipes", state)
    before = service.describe("scenario", "recipes")
    with pytest.raises(InterpretationRunRejected):
        observe(service, folder, plan, origin, binding, wire, result)
    assert service.describe("scenario", "recipes") == before


def test_late_measurements_update_original_run_without_moving_current_change(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding, wire = retained(service, folder)
    planned(service, "CH-later")
    before = service.describe("scenario", "recipes")
    state_before = service._read_state("scenario", "recipes")
    result = observe(service, folder, plan, origin, binding, wire, response())
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
    old = state["workflow"]["change_portfolio"][origin["change_id"]]["change"]["runs"]
    row = next(row for row in old if row["run_id"] == binding["run_id"])
    assert row["execution_metrics"]["provider_calls"] == 1
    assert state["workflow"]["change"] == state_before["workflow"]["change"]


def test_projector_never_combines_different_cost_bases_or_duplicate_jobs():
    rows = [
        {
            "job_id": "one",
            "status": "succeeded",
            "stage": "generation",
            "metrics": {
                "source": "builder.llm_result",
                "cost_usd": 1,
                "cost_basis": "estimate",
            },
        },
        {
            "job_id": "two",
            "status": "succeeded",
            "stage": "repair",
            "metrics": {
                "source": "builder.llm_result",
                "cost_usd": 2,
                "cost_basis": "metered",
            },
        },
    ]
    metrics = project_run_measurements(
        rows, origin={"execution_group_ref": "task-execution:" + "a" * 24}
    )
    assert "cost_usd" not in metrics and "provider_calls" not in metrics
    assert all("cost_usd" in row for row in metrics["attempt_observations"])
    with pytest.raises(InterpretationRunRejected):
        project_run_measurements([rows[0], copy.deepcopy(rows[0])], origin={})


@pytest.mark.parametrize("status", ["completed", "canceled"])
def test_equivalent_provider_terminal_spellings_have_one_stable_observation(
    workflow_project, tmp_path, status
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding, wire = retained(service, folder)
    observe(service, folder, plan, origin, binding, wire, response(status=status))
    canonical = "succeeded" if status == "completed" else "cancelled"
    before = generation_rows(service)
    observe(service, folder, plan, origin, binding, wire, response(status=canonical))
    assert generation_rows(service) == before


def test_retry_after_projection_failure_reconciles_retained_measurements_without_provider_replay(
    workflow_project, tmp_path, monkeypatch
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding, wire = retained(service, folder)
    original = type(service).record_generation_observation
    monkeypatch.setattr(
        type(service),
        "record_generation_observation",
        lambda *args, **kwargs: {"ok": False},
    )
    with pytest.raises(InterpretationRunRejected):
        observe(service, folder, plan, origin, binding, wire, response())
    files = list(folder.glob("generation-observation-*.json"))
    assert len(files) == 1
    before = files[0].read_bytes()
    assert generation_rows(service)[0]["status"] == "queued"
    monkeypatch.setattr(type(service), "record_generation_observation", original)
    observe(service, folder, plan, origin, binding, wire, response())
    assert files[0].read_bytes() == before
    assert generation_rows(service)[0]["execution_metrics"]["provider_calls"] == 1


def test_provider_observation_does_not_resolve_unvalidated_wire_plan_paths(monkeypatch):
    from pathlib import Path
    from adaos.services.builder.provider_jobs import PlannedBuilderJobs

    def no_read(*args, **kwargs):
        pytest.fail("Unvalidated provider plan reference reached filesystem reading")

    monkeypatch.setattr(Path, "read_text", no_read)
    jobs = PlannedBuilderJobs(None, None, "unused")
    with pytest.raises(InterpretationRunRejected):
        jobs._observe(
            {},
            {
                "execution_run_binding": {"binding": "exists"},
                "context_admission": {"generation_plan_ref": "../../private"},
            },
        )
