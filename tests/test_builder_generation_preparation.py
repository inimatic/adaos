import copy
import json

import pytest

from adaos.services.builder.generation_runs import GenerationRuns, retained_run_binding
from adaos.services.builder.interpretation_runs import InterpretationRuns
from adaos.services.builder.provider_jobs import PlannedBuilderJobs
from adaos.services.builder.task_interpretation import TaskInterpretationRejected
from adaos.services.builder.task_pipeline import prepare_generation_request
from adaos.services.builder.repair_context import prepare_repair_context
from adaos.services.builder.generation_plan import GenerationInputRejected
from adaos.services.builder_intent import capture_intent
from adaos.services.policy.invocation import verified_invocation
from test_builder_task_interpretation import prepared, TEXT
from test_builder_task_pipeline import Provider
from test_builder_interpretation_runs import planned
from test_builder_generation_runs import generation_rows
from test_builder_workflow import workflow_project as _workflow_project

workflow_project = _workflow_project


def prepare(
    service,
    folder,
    *,
    provider=None,
    metadata=None,
    previous=None,
    source_ref="m.source",
    observer=True,
    output_mode="semantic_v2",
):
    intent = capture_intent(
        TEXT, project_ref="scenario:recipes", source={"kind": "chat", "ref": source_ref}
    )
    proposal = copy.deepcopy(prepared()[2])
    proposal["source_digest"] = intent["digest"]
    provider = provider or Provider(proposal)
    result = prepare_generation_request(
        {
            "output_mode": output_mode,
            "base_request": {},
            "dynamic_request": {"instruction": TEXT},
        },
        metadata={
            "builder_task_pipeline": "1",
            "prototype_intent": intent,
            "builder_generation_run_protocol": "v1",
            **(metadata or {}),
        },
        model="selected",
        reasoning={"effort": "low"},
        provider=provider,
        journal_dir=folder,
        previous_brief=previous,
        run_observer=InterpretationRuns(service),
        generation_run_observer=GenerationRuns(service) if observer else None,
    )
    return result, provider


def test_preparation_reserves_after_meaning_before_candidate_without_putting_run_ids_in_model_context(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        result, provider = prepare(service, tmp_path / "jobs")
        plan = result["generation_plan"]
        assert plan["execution_run_required"] is True
        path = (
            tmp_path / "jobs" / (plan["generation_plan_id"].replace(":", "-") + ".json")
        )
        before = path.read_bytes()
        record = json.loads(before)
        binding = result["execution_run_binding"]
        assert "execution_run_binding" not in record
        assert (
            retained_run_binding(path.parent, plan, record["execution_origin"])
            == binding
        )
        assert binding["binding_id"] not in result["user_prompt"]
        (row,) = generation_rows(service)
        assert row["status"] == "queued" and row["run_id"] == binding["run_id"]
        assert (
            not {"provider_calls", "llm_calls", "cost_usd"}
            & row["execution_metrics"].keys()
        )
        repeated, _ = prepare(service, path.parent, provider=provider)
        assert repeated == result and path.read_bytes() == before
        assert (
            len(provider.submissions) == 1
        )  # Interpretation only; no candidate submitted.


@pytest.mark.parametrize("protocol", [False, 0, "v2", [], {}])
def test_unknown_protocol_refuses_before_interpretation_without_changing_runs(
    workflow_project, tmp_path, protocol
):
    service, _ = workflow_project
    planned(service)
    provider = Provider(prepared()[2])
    before = service.describe("scenario", "recipes")
    with pytest.raises(TaskInterpretationRejected) as caught:
        prepare(
            service,
            tmp_path / "jobs",
            provider=provider,
            metadata={"builder_generation_run_protocol": protocol},
        )
    assert caught.value.findings[0]["code"] == "task.generation_protocol_unsupported"
    assert (
        not provider.submissions and service.describe("scenario", "recipes") == before
    )


def test_missing_execution_observer_refuses_before_interpretation(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    provider = Provider(prepared()[2])
    with pytest.raises(TaskInterpretationRejected) as caught:
        prepare(service, tmp_path / "jobs", provider=provider, observer=False)
    assert caught.value.findings[0]["code"] == "task.generation_protocol_unavailable"
    assert not provider.submissions and not generation_rows(service)


def test_execution_protocol_does_not_silently_enable_a_legacy_task_pipeline(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    provider = Provider(prepared()[2])
    with pytest.raises(TaskInterpretationRejected) as caught:
        prepare(
            service,
            tmp_path / "jobs",
            provider=provider,
            metadata={"builder_task_pipeline": "0"},
        )
    assert caught.value.findings[0]["code"] == "task.generation_protocol_requires_task"
    assert not provider.submissions and not generation_rows(service)


def test_legacy_preparation_keeps_its_default_plan_and_does_not_reserve_generation_runs(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    result, _ = prepare(
        service, tmp_path / "jobs", metadata={"builder_generation_run_protocol": None}
    )
    assert "execution_run_required" not in result["generation_plan"]
    assert "execution_run_binding" not in result and not generation_rows(service)


@pytest.mark.parametrize("output_mode", [None, "semantic_v1", "full_webui"])
@pytest.mark.parametrize("protocol", ["v1", "v2"])
def test_protocol_rejects_legacy_output_mode_without_silent_unbound_fallback(
    workflow_project, tmp_path, protocol, output_mode
):
    service, _ = workflow_project
    planned(service)
    provider = Provider(prepared()[2])
    before = service.describe("scenario", "recipes")
    with pytest.raises(TaskInterpretationRejected) as caught:
        prepare(service, tmp_path / "jobs", provider=provider, output_mode=output_mode,
            metadata={"builder_generation_run_protocol": protocol})
    assert caught.value.findings[0]["code"] == (
        "task.generation_protocol_requires_semantic_v2" if protocol == "v1"
        else "task.generation_protocol_unsupported")
    assert not provider.submissions and service.describe("scenario", "recipes") == before


def test_s1_reuses_exact_admitted_meaning_but_reserves_the_actual_new_invocation(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    folder = tmp_path / "jobs"
    with verified_invocation(request_id="first-turn", idempotency_key=None):
        first, _ = prepare(service, folder)
    with verified_invocation(request_id="later-turn", idempotency_key=None):
        later, provider = prepare(
            service,
            folder,
            previous=first["prototype_brief"],
            source_ref="m.later",
            metadata={"builder_task_pipeline": "0"},
        )
    assert (
        later["generation_plan"]["route"] == "single_stage" and not provider.submissions
    )
    assert later["interpretation_receipt"]["provider_calls"] == 0
    assert (
        later["execution_run_binding"]["invocation_scope"]
        != first["execution_run_binding"]["invocation_scope"]
    )
    assert len(generation_rows(service)) == 2


def test_repair_preparation_reserves_the_same_invocation_run_with_a_separate_binding(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    folder = tmp_path / "jobs"
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        primary, _ = prepare(service, folder)
        plan = primary["generation_plan"]

        class Transport:
            def submit(self, *args, **options):
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

        jobs = PlannedBuilderJobs(
            Transport(), plan, folder, run_observer=GenerationRuns(service)
        )
        jobs.submit([], request_id="primary-request")
        candidate = {
            "schema": "adaos.builder.semantic_prototype_candidate.v2",
            "title": {"ru": "Invalid"},
        }
        options = {
            "job_id": "primary-job",
            "candidate": candidate,
            "findings": [{"code": "semantic.task_write_scope_exceeded"}],
            "brief": primary["prototype_brief"],
            "journal_dir": folder,
        }
        with pytest.raises(
            GenerationInputRejected, match="repair_execution_unavailable"
        ):
            prepare_repair_context(**options)
        # Repair cannot use a caller's candidate while the actual provider
        # result is still queued. Retain the completed protected output first;
        # do not weaken the production source/Run guard to satisfy this test.
        from adaos.services.builder.interpretation_runs import InterpretationRunRejected
        from test_builder_generation_outputs import recover
        from test_builder_generation_observations import response
        with pytest.raises(InterpretationRunRejected):
            prepare_repair_context(**options,generation_run_observer=GenerationRuns(service))
        recover(service,folder,{**response('primary-job'),'output_text':json.dumps(candidate)})
        child = prepare_repair_context(
            **options, generation_run_observer=GenerationRuns(service)
        )
        assert child["status"] == "admitted"
        assert (
            child["execution_run_binding"]["run_id"]
            == primary["execution_run_binding"]["run_id"]
        )
        assert (
            child["execution_run_binding"]["binding_id"]
            != primary["execution_run_binding"]["binding_id"]
        )
        assert len(generation_rows(service)) == 1
        target = folder / (
            "repair-plan-"
            + child["generation_plan"]["generation_plan_id"].partition(":")[2]
            + ".json"
        )
        before = target.read_bytes()
        assert (
            prepare_repair_context(
                **options, generation_run_observer=GenerationRuns(service)
            )
            == child
        )
        assert target.read_bytes() == before
