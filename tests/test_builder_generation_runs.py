import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.generation_origin import from_run_binding
from adaos.services.builder.generation_plan import compile_generation_plan
from adaos.services.builder.generation_runs import GenerationRuns, verify_run_binding
from adaos.services.builder.interpretation_runs import (
    InterpretationRuns,
    InterpretationRunRejected,
)
from adaos.services.builder.task_spec import seal
from adaos.services.policy.invocation import verified_invocation
from test_builder_workflow import workflow_project as _workflow_project
from test_builder_generation_origin import setup
from test_builder_interpretation_runs import planned

workflow_project = _workflow_project


def qualified(service, folder):
    request, _, _ = setup(service, folder)
    task = request["task_spec"]
    plan = seal(
        {**request["generation_plan"], "execution_run_required": True},
        id_key="generation_plan_id",
        prefix="generation-plan:",
    )
    origin = from_run_binding(
        task, plan, InterpretationRuns(service).bind(task, plan["generation_plan_id"])
    )
    return task, plan, origin


def invoke(observer, task, plan, origin, turn="trusted-turn"):
    with verified_invocation(request_id=turn, idempotency_key=None):
        return observer.reserve(task, plan, origin)


def generation_rows(service):
    return [
        row
        for row in service.describe("scenario", "recipes")["change"]["runs"]
        if row["run_id"].startswith("generation-run:")
    ]


def test_generation_reservation_is_canonical_but_has_no_authoring_or_billing_claim(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    task, plan, origin = qualified(service, tmp_path / "jobs")
    observer = GenerationRuns(service)
    before = service.describe("scenario", "recipes")
    result = invoke(observer, task, plan, origin)
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
    assert len(generation_rows(service)) == 1
    row = generation_rows(service)[0]
    assert row["run_id"] == result["binding"]["run_id"]
    assert row["status"] == "queued" and row["completed_at"] is None
    assert row["activity"] == "prototype_generation_prepared"
    assert row["execution_metrics"]["pipeline_stage"] == "generation"
    assert (
        not {"llm_calls", "provider_calls", "cost_usd", "source_refs"}
        & row["execution_metrics"].keys()
    )
    assert (
        result["binding"]["invocation_scope"]
        == hashlib.sha256(b"trusted-turn").hexdigest()
    )
    assert "trusted-turn" not in json.dumps(result)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src/adaos/abi/builder.run.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(row)
    verify_run_binding(result["binding"], origin, plan)
    again = invoke(observer, task, plan, origin)
    assert again["binding"] == result["binding"] and generation_rows(service) == [row]


def test_current_execution_identity_is_separate_from_retained_task_owner(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    task, plan, origin = qualified(service, tmp_path / "jobs")
    before = copy.deepcopy(origin)
    observer = GenerationRuns(service)
    first = invoke(observer, task, plan, origin, "turn-one")
    second = invoke(observer, task, plan, origin, "turn-two")
    assert first["run_id"] != second["run_id"]
    assert (
        first["binding"]["origin_ref"]
        == second["binding"]["origin_ref"]
        == origin["origin_id"]
    )
    assert origin == before and len(generation_rows(service)) == 2


def test_automatic_repair_inherits_owner_and_shares_only_its_current_invocation_run(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    task, plan, origin = qualified(service, tmp_path / "jobs")
    observer = GenerationRuns(service)
    first = invoke(observer, task, plan, origin)
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
    child = invoke(observer, task, repair, origin)
    assert child["run_id"] == first["run_id"]
    assert child["binding"]["binding_id"] != first["binding"]["binding_id"]
    row = generation_rows(service)[0]
    assert child["binding"]["binding_id"] in row["evidence_refs"]
    assert len(generation_rows(service)) == 1
    before = service.describe("scenario", "recipes")
    # Another billing turn cannot invent a missing terminal parent observation.
    with pytest.raises(InterpretationRunRejected):
        invoke(observer, task, repair, origin, "new-repair-turn")
    assert service.describe("scenario", "recipes") == before


@pytest.mark.parametrize(
    "fault", ["unqualified", "missing_invocation", "stale_change", "stale_generation"]
)
def test_unqualified_or_stale_reservations_never_create_a_current_run(
    workflow_project, tmp_path, fault
):
    service, _ = workflow_project
    task, plan, origin = qualified(service, tmp_path / "jobs")
    if fault == "unqualified":
        plan = seal(
            {
                key: value
                for key, value in plan.items()
                if key != "execution_run_required"
            },
            id_key="generation_plan_id",
            prefix="generation-plan:",
        )
        origin = from_run_binding(
            task,
            plan,
            InterpretationRuns(service).bind(task, plan["generation_plan_id"]),
        )
    elif fault == "stale_change":
        planned(service, "CH-later")
    elif fault == "stale_generation":
        origin = seal(
            {**origin, "origin_generation": origin["origin_generation"] + 1},
            id_key="origin_id",
            prefix="generation-origin:",
        )
    before = service.describe("scenario", "recipes")
    observer = GenerationRuns(service)
    with pytest.raises(InterpretationRunRejected):
        if fault == "missing_invocation":
            observer.reserve(task, plan, origin)
        else:
            invoke(observer, task, plan, origin)
    assert service.describe("scenario", "recipes") == before


def test_old_plan_identity_is_unchanged_without_explicit_new_protocol(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    task, _, _ = qualified(service, tmp_path / "jobs")
    old = compile_generation_plan(
        task, model="selected", transport="personal", execution_origin_required=True
    )
    default = compile_generation_plan(
        task,
        model="selected",
        transport="personal",
        execution_origin_required=True,
        execution_run_required=False,
    )
    assert default == old and "execution_run_required" not in old
    with pytest.raises(ValueError):
        compile_generation_plan(
            task, model="selected", transport="personal", execution_run_required=True
        )


@pytest.mark.parametrize("fault", ["binding_missing", "run_missing", "wrong_turn"])
@pytest.mark.parametrize("operation", ["submit", "send"])
def test_required_execution_cannot_spend_without_existing_current_reservation(
    workflow_project, tmp_path, fault, operation
):
    from adaos.services.builder.provider_jobs import PlannedBuilderJobs

    service, _ = workflow_project
    task, plan, origin = qualified(service, tmp_path / "old-jobs")
    observer = GenerationRuns(service)
    prepared = invoke(observer, task, plan, origin)
    record = {
        "generation_plan": plan,
        "task_spec": task,
        "execution_origin": origin,
        "execution_run_binding": prepared["binding"],
    }
    if fault == "binding_missing":
        record.pop("execution_run_binding")
    elif fault == "run_missing":
        state = service._read_state("scenario", "recipes")
        state["workflow"]["change"]["runs"] = [
            row
            for row in state["workflow"]["change"]["runs"]
            if row["run_id"] != prepared["run_id"]
        ]
        service._write_state("scenario", "recipes", state)
    directory = tmp_path / "qualified-jobs"
    directory.mkdir()
    path = directory / (plan["generation_plan_id"].replace(":", "-") + ".json")
    path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()

    class Transport:
        def submit(self, *args, **options):
            pytest.fail("An unqualified reservation reached the paid provider")

        send = submit

    jobs = PlannedBuilderJobs(Transport(), plan, directory, run_observer=observer)
    with verified_invocation(
        request_id="different-turn" if fault == "wrong_turn" else "trusted-turn",
        idempotency_key=None,
    ):
        with pytest.raises(InterpretationRunRejected):
            getattr(jobs, operation)([], request_id="primary")
    assert path.read_bytes() == before


def test_paid_wire_retains_execution_after_current_turn_and_change_switch(
    workflow_project, tmp_path
):
    from adaos.services.builder.provider_jobs import PlannedBuilderJobs

    service, _ = workflow_project
    task, plan, origin = qualified(service, tmp_path / "old-jobs")
    observer = GenerationRuns(service)
    prepared = invoke(observer, task, plan, origin)
    directory = tmp_path / "qualified-jobs"
    directory.mkdir()
    (directory / (plan["generation_plan_id"].replace(":", "-") + ".json")).write_text(
        json.dumps(
            {
                "generation_plan": plan,
                "task_spec": task,
                "execution_origin": origin,
                "execution_run_binding": prepared["binding"],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    class Transport:
        def submit(self, *args, **options):
            calls.append(options["request_id"])
            return {
                "job_id": "primary-job",
                "status": "queued",
                "context_admission": {
                    "generation_plan_ref": plan["generation_plan_id"],
                    "parent_generation_plan_ref": None,
                    "execution_origin_required": True,
                    "execution_run_required": True,
                    "required_retrieval_refs": [],
                },
            }

        def wait(self, *args, **options):
            return {"job_id": "primary-job", "status": "failed"}

    jobs = PlannedBuilderJobs(Transport(), plan, directory, run_observer=observer)
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        submitted = jobs.submit([], request_id="primary")
        assert submitted["execution_run_binding"] == prepared["binding"]
        submitted["execution_run_binding"]["run_id"] = "caller-mutation"
        submitted["context_admission"]["execution_run_required"] = False
        assert jobs.wait("primary-job")["execution_run_binding"] == prepared["binding"]
    planned(service, "CH-later")
    recovered = PlannedBuilderJobs(Transport(), None, directory, run_observer=observer).wait("primary-job")
    assert recovered["execution_run_binding"] == prepared["binding"]
    assert calls == ["primary"]
