import pytest

from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_generation_completion import observed
from test_builder_interpretation_runs import planned, prepare, observations
from test_builder_task_interpretation import prepared
from test_builder_task_pipeline import Provider
from test_builder_workflow import workflow_project as _workflow_project

workflow_project = _workflow_project


@pytest.mark.parametrize("namespace", ["generation", "interpretation"])
@pytest.mark.parametrize("writer", ["revision", "semantic"])
def test_compatibility_writer_cannot_clobber_a_bound_provider_run(
    workflow_project,
    tmp_path,
    namespace,
    writer,
):
    service, _ = workflow_project
    if namespace == "generation":
        _, _, origin, binding = observed(service, tmp_path / "jobs", "completed")
        run_id, change_id = binding["run_id"], origin["change_id"]
    else:
        planned(service)
        prepare(service, tmp_path / "jobs", Provider(prepared()[2]))
        state, rows = observations(service)
        run_id, change_id = rows[0]["run_id"], state["change"]["change_id"]
    before = service._state_path("scenario", "recipes").read_bytes()
    current = service.describe("scenario", "recipes")
    generation = current["generation"]
    metrics = next(row for row in current["change"]["runs"] if row["run_id"] == run_id)[
        "execution_metrics"
    ]
    with pytest.raises(BuilderWorkflowError, match="reserved provider Run"):
        if writer == "revision":
            service.transition(
                "scenario",
                "recipes",
                "prototype_revision_recorded",
                expected_generation=generation,
                metadata={
                    "revision": "002",
                    "run_id": run_id,
                    "change_id": change_id,
                },
            )
        else:
            service.record_semantic_model_observation(
                "recipes",
                change_id=change_id,
                run_id=run_id,
                activity="prototype_semantic_model_ready",
                execution_metrics=metrics,
                source_message_id="unrelated-source",
            )
    assert service._state_path("scenario", "recipes").read_bytes() == before
