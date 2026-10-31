import copy

import pytest
from test_builder_authoring_scope import scoped_project as _scoped_project
from test_builder_generation_candidates import setup
from test_builder_generation_runs import generation_rows
from test_builder_interpretation_runs import planned
from test_builder_workflow import workflow_project as _workflow_project

from adaos.sdk.builder import prototype
from adaos.services.builder.generation_authoring import prepare_generation_publication
from adaos.services.builder.generation_submissions import GenerationSubmissions
from adaos.services.builder.interpretation_runs import InterpretationRunRejected
from adaos.services.builder.workflow_authority import WorkflowAuthority

workflow_project = _workflow_project
scoped_project = _scoped_project


def ready_result(service, folder, *, project_ref="scenario:recipes"):
    result = setup(service, folder, project_ref=project_ref)
    scope = GenerationSubmissions(
        folder, binding=result["binding"], origin=result["origin"], plan=result["plan"]
    )
    with scope.submitting(result["wire"]["request_id"]) as register:
        register({"job_id": result["wire"]["job_id"]})
    return {
        "journal_dir": folder,
        "request_id": result["wire"]["request_id"],
        "output_ref": result["output_ref"],
    }


def source_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_sdk_prepares_and_commits_saved_output_without_source_first_writes(
    workflow_project, tmp_path, monkeypatch
):
    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    before = source_bytes(root)
    old = copy.deepcopy(generation_rows(service)[0])
    state = service.describe("scenario", "recipes")
    prepared = prototype.prepare_generation_publication(**arguments)
    assert not prepared["head_advanced"]
    assert (
        prepared["revision"]
        == f"{int(state['prototype'].get('head_revision') or '0') + 1:03d}"
    )
    assert source_bytes(root) == before
    assert service.describe("scenario", "recipes") == state
    assert prototype.prepare_generation_publication(**arguments) == prepared
    result = prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )
    assert result["committed"] and result["run_id"] == old["run_id"]
    assert generation_rows(service)[0]["execution_metrics"] == old["execution_metrics"]
    assert source_bytes(root) == before
    assert prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )["duplicate"]
    assert getattr(
        prototype.prepare_generation_publication, "__adaos_authoring_operation__", False
    )
    assert getattr(
        prototype.commit_generation_publication, "__adaos_authoring_operation__", False
    )


def test_project_sdk_bridge_creates_only_the_component_head(
    scoped_project, tmp_path, monkeypatch
):
    service, _, _ = scoped_project
    arguments = ready_result(
        service, tmp_path / "jobs", project_ref="project:qualification"
    )
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    prepared = prototype.prepare_generation_publication(**arguments)
    result = prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )
    assert result["committed"]
    assert WorkflowAuthority(service.state_dir).resolve("scenario:recipes") is not None
    assert WorkflowAuthority(service.state_dir).resolve("project:qualification") is None


@pytest.mark.parametrize(
    "fault", ["unknown_request", "wrong_output", "missing_wire", "stale_generation"]
)
def test_unqualified_result_cannot_prepare_or_move_source_files(
    workflow_project, tmp_path, fault
):
    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    if fault == "unknown_request":
        arguments["request_id"] = "not-retained"
    elif fault == "wrong_output":
        arguments["output_ref"] = "generation-output:" + "a" * 24
    elif fault == "missing_wire":
        next(arguments["journal_dir"].glob("wire-*.json")).unlink()
    else:
        planned(service, "CH-later")
    before = source_bytes(root)
    state = service.describe("scenario", "recipes")
    with pytest.raises(InterpretationRunRejected):
        prepare_generation_publication(service, **arguments)
    assert source_bytes(root) == before
    assert service.describe("scenario", "recipes") == state


def test_sdk_bridge_does_not_accept_caller_supplied_artifacts(
    workflow_project, tmp_path, monkeypatch
):
    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    before = source_bytes(root)
    with pytest.raises(TypeError):
        prototype.prepare_generation_publication(
            **arguments, document={"title": "untrusted"}
        )
    assert source_bytes(root) == before


def test_completed_publication_replay_does_not_require_the_disposable_webui_source(
    workflow_project, tmp_path, monkeypatch
):
    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    prepared = prototype.prepare_generation_publication(**arguments)
    prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )
    (root / "webui.json").unlink()
    before = service.describe("scenario", "recipes")
    assert prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )["duplicate"]
    assert service.describe("scenario", "recipes") == before
