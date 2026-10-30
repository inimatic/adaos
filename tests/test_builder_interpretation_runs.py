import copy
import json

import pytest

from test_builder_workflow import workflow_project
from test_builder_task_interpretation import prepared, TEXT
from test_builder_task_pipeline import Provider
from adaos.services.builder_intent import capture_intent
from adaos.services.builder.interpretation_runs import (
    InterpretationRuns,
    InterpretationRunRejected,
)
from adaos.services.builder.task_pipeline import (
    prepare_generation_request,
    TaskInterpretationProviderFailed,
)
from adaos.services.builder.run_observability import project_run


def planned(service, change="CH-origin"):
    previous = service.prototype_edit_context("scenario", "recipes")["change"].get(
        "change_id"
    )
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": change,
            "supersedes_change_set_id": previous,
            "request": "Build the local Prototype",
            "issues": [
                {
                    "issue_id": "local",
                    "title": "Local executable UI",
                    "lane": "prototype",
                    "acceptance_criteria": ["The local collection remains executable."],
                }
            ],
        },
    )


def prepare(service, path, provider, *, observer=True, project_ref="scenario:recipes"):
    intent = capture_intent(
        TEXT, project_ref=project_ref, source={"kind": "chat", "ref": "m.source"}
    )
    if provider.proposal:
        provider.proposal["source_digest"] = intent["digest"]
    return prepare_generation_request(
        {
            "output_mode": "semantic_v2",
            "base_request": {},
            "dynamic_request": {"instruction": TEXT},
        },
        metadata={"builder_task_pipeline": "1", "prototype_intent": intent},
        model="selected",
        reasoning={"effort": "low"},
        provider=provider,
        journal_dir=path,
        run_observer=InterpretationRuns(service) if observer else None,
    )


def observations(service):
    state = service.describe("scenario", "recipes")
    rows = [
        row
        for row in state["change"]["runs"]
        if row["activity"].startswith("prototype_interpretation_")
    ]
    return state, rows


def test_new_interpretation_has_one_source_attributed_run_without_authoring_transition(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    before = service.describe("scenario", "recipes")
    provider = Provider(prepared()[2])
    result = prepare(service, tmp_path / "jobs", provider)
    after, rows = observations(service)
    assert (
        after["generation"] == before["generation"]
        and after["prototype"] == before["prototype"]
    )
    assert after["active_phase"] == "prototype" and len(rows) == 1
    row = rows[0]
    assert (
        row["status"] == "succeeded"
        and row["activity"] == "prototype_interpretation_ready"
    )
    metrics = row["execution_metrics"]
    assert (
        metrics["llm_calls"] == 1
        and metrics["repair_attempts"] == 0
        and metrics["provider_calls"] == 1
    )
    assert (
        metrics["pipeline_stage"] == "interpretation" and metrics["input_tokens"] == 12
    )
    assert "cost_usd" not in metrics and "message:m.source" in row["input_refs"]
    assert project_run(row)["pipeline_stage_label"] == "interpretation"
    retained = json.loads(
        next((tmp_path / "jobs").glob("builder-interpretation-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert retained["execution_run_binding"]["change_id"] == "CH-origin"
    assert retained["execution_run_binding"]["run_id"] == row["run_id"]
    assert result["interpretation_receipt"]["job_id"] == "same-job"
    assert prepare(service, tmp_path / "jobs", provider) == result
    assert len(provider.submissions) == 1 and len(observations(service)[1]) == 1


def test_failed_provider_is_visible_without_revision_and_terminal_replay_never_adds_cost(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)

    class Failed(Provider):
        def wait(self, job_id, **options):
            self.observations.append(job_id)
            return {
                "job_id": job_id,
                "status": "failed",
                "error": "PRIVATE",
                "telemetry": {"provider_calls": 1},
                "tool_telemetry": {
                    "provider_rejection": {"kind": "schema", "http_status": 400}
                },
            }

    provider = Failed(None)
    for _ in range(2):
        with pytest.raises(TaskInterpretationProviderFailed):
            prepare(service, tmp_path / "jobs", provider)
    state, rows = observations(service)
    assert len(rows) == 1 and rows[0]["status"] == "failed"
    assert rows[0]["execution_metrics"]["provider_calls"] == 1
    assert rows[0][
        "error"
    ] == "task.interpretation_failed" and "PRIVATE" not in json.dumps(rows)
    assert provider.observations == ["same-job"] and len(provider.submissions) == 1


def test_changed_origin_is_not_silently_rebound_to_current_change(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)

    class Changed(Provider):
        def wait(self, job_id, **options):
            result = super().wait(job_id, **options)
            planned(service, "CH-another")
            return result

    provider = Changed(prepared()[2])
    with pytest.raises(InterpretationRunRejected):
        prepare(service, tmp_path / "jobs", provider)
    state, rows = observations(service)
    assert state["change"]["change_id"] == "CH-another" and rows == []
    record = json.loads(
        next((tmp_path / "jobs").glob("builder-interpretation-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert (
        record["execution_run_binding"]["change_id"] == "CH-origin"
        and record["result"]["status"] == "succeeded"
    )
    assert len(provider.submissions) == 1


def test_absent_origin_stops_before_submission(workflow_project, tmp_path):
    service, _ = workflow_project
    provider = Provider(prepared()[2])
    with pytest.raises(InterpretationRunRejected):
        prepare(service, tmp_path / "jobs", provider)
    assert provider.submissions == []
    record = json.loads(
        next((tmp_path / "jobs").glob("builder-interpretation-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "execution_run_blocked" and record["job_id"] is None
    with pytest.raises(InterpretationRunRejected):
        prepare(service, tmp_path / "jobs", provider)
    assert provider.submissions == []


def test_legacy_terminal_journal_cannot_be_retroactively_assigned_to_a_change(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    provider = Provider(prepared()[2])
    prepare(service, tmp_path / "jobs", provider, observer=False)
    record_path = next((tmp_path / "jobs").glob("builder-interpretation-*.json"))
    before = record_path.read_bytes()
    prepare(service, tmp_path / "jobs", provider)
    assert observations(service)[1] == [] and len(provider.submissions) == 1
    assert record_path.read_bytes() == before


def test_cancellation_is_a_cancelled_stage_not_success_or_an_extra_model_attempt(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)

    class Cancelled(Provider):
        def wait(self, job_id, **options):
            return {"job_id": job_id, "status": "cancelled", "telemetry": {}}

    with pytest.raises(TaskInterpretationProviderFailed):
        prepare(service, tmp_path / "jobs", Cancelled(None))
    row = observations(service)[1][0]
    assert row["status"] == "cancelled" and row["execution_metrics"]["llm_calls"] == 1
    assert "provider_calls" not in row["execution_metrics"]


def test_project_primary_is_explicit_unique_and_bound_before_the_job(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)
    root = service.dev_projects_root / "qualification"
    root.mkdir(parents=True)
    manifest = {
        "schema": "adaos.project.v1",
        "kind": "project",
        "id": "qualification",
        "version": "0.1.0",
        "profiles": [],
        "components": {
            "owned": [
                {
                    "ref": "scenario:recipes",
                    "role": "primary",
                    "exposure": "application",
                    "lifecycle": "bound",
                    "relations": ["uses"],
                }
            ],
            "dependencies": [],
        },
        "entrypoints": [
            {
                "id": "main",
                "presentation": "scenario:recipes",
                "default": True,
                "bindings": {},
            }
        ],
        "catalog": {
            "title": "Fixture",
            "description": "",
            "categories": [],
            "tags": [],
        },
        "lifecycle": {
            "uninstall": {
                "components": "remove_if_unreferenced",
                "runtime_data": "retain",
                "source_artifacts": "retain",
            }
        },
    }
    (root / "project.yaml").write_text(json.dumps(manifest), encoding="utf-8")
    provider = Provider(prepared()[2])
    prepare(service, tmp_path / "jobs", provider, project_ref="project:qualification")
    assert observations(service)[1][0]["status"] == "succeeded"
    record = json.loads(
        next((tmp_path / "jobs").glob("builder-interpretation-*.json")).read_text(
            encoding="utf-8"
        )
    )
    binding = record["execution_run_binding"]
    assert (
        binding["object_type"] == "scenario"
        and binding["object_id"] == "recipes"
        and binding["manifest_digest"]
    )
    manifest["catalog"]["title"] = "Unrelated metadata update"
    (root / "project.yaml").write_text(json.dumps(manifest), encoding="utf-8")
    assert InterpretationRuns(service).observe(binding, record, "admitted")["ok"]
    corrupted = copy.deepcopy(binding)
    corrupted["object_id"] = "other"
    with pytest.raises(InterpretationRunRejected):
        InterpretationRuns(service).observe(corrupted, record, "admitted")
    corrupted_record = copy.deepcopy(record)
    corrupted_record["result"]["job_id"] = "another-job"
    with pytest.raises(InterpretationRunRejected):
        InterpretationRuns(service).observe(binding, corrupted_record, "admitted")
    corrupted_record = copy.deepcopy(record)
    corrupted_record["task_spec"]["scope"]["project_ref"] = "project:other"
    with pytest.raises(InterpretationRunRejected):
        InterpretationRuns(service).observe(binding, corrupted_record, "admitted")


def test_unknown_submission_has_a_failed_observation_without_fabricated_model_usage(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)

    class Unknown(Provider):
        def submit(self, *args, **options):
            self.submissions.append(options)
            raise TimeoutError("Acknowledgement lost")

    provider = Unknown(None)
    with pytest.raises(TimeoutError):
        prepare(service, tmp_path / "jobs", provider)
    row = observations(service)[1][0]
    assert (
        row["status"] == "failed"
        and row["error"] == "task.interpretation_submission_unknown"
    )
    assert (
        "llm_calls" not in row["execution_metrics"]
        and "provider_calls" not in row["execution_metrics"]
    )
    with pytest.raises(ValueError, match="outcome is unknown"):
        prepare(service, tmp_path / "jobs", provider)
    assert len(provider.submissions) == 1


def test_invalid_json_proposal_is_retained_and_observed_without_candidate_work(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    planned(service)

    class Invalid(Provider):
        def wait(self, *args, **kwargs):
            result = super().wait(*args, **kwargs)
            result["output_text"] = '{"incomplete"'
            return result

    from adaos.services.builder.task_interpretation import TaskInterpretationRejected

    with pytest.raises(TaskInterpretationRejected) as caught:
        prepare(service, tmp_path / "jobs", Invalid(prepared()[2]))
    assert caught.value.findings[0]["code"] == "task.interpretation_response_invalid"
    assert observations(service)[1][0]["status"] == "failed"


def test_native_sdk_preparation_uses_owned_run_observation_and_development_authority(
    workflow_project, tmp_path, monkeypatch
):
    from adaos.sdk.builder import prototype
    from adaos.sdk import access
    from adaos.services.builder.workflow import BuilderWorkflowService

    service, _ = workflow_project
    planned(service)
    required = []
    monkeypatch.setattr(access, "require", required.append)
    monkeypatch.setattr(
        BuilderWorkflowService, "from_context", classmethod(lambda cls: service)
    )
    intent = capture_intent(
        TEXT, project_ref="scenario:recipes", source={"kind": "chat", "ref": "m.source"}
    )
    proposal = prepared()[2]
    proposal["source_digest"] = intent["digest"]
    provider = Provider(proposal)
    prototype.prepare_generation_request(
        {
            "output_mode": "semantic_v2",
            "base_request": {},
            "dynamic_request": {"instruction": TEXT},
        },
        metadata={"builder_task_pipeline": "1", "prototype_intent": intent},
        model="selected",
        reasoning=None,
        provider=provider,
        journal_dir=tmp_path / "jobs",
    )
    assert "applications.develop" in required and len(observations(service)[1]) == 1
    assert len(provider.submissions) == 1


def test_sdk_cannot_create_run_or_spend_before_development_authority(monkeypatch):
    from adaos.sdk.builder import prototype
    from adaos.sdk import access

    def deny(_):
        raise PermissionError("Development authority missing")

    monkeypatch.setattr(access, "require", deny)
    with pytest.raises(PermissionError):
        prototype.prepare_generation_request({})
