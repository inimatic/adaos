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
    assert prepared["source_scope"]["project_ref"] == "scenario:recipes"
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
    assert prototype.prepare_generation_publication(**arguments) == prepared
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
    assert prepared["source_scope"]["project_ref"] == "project:qualification"
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


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_sdk_terminal_failure_closes_original_run_without_source_or_head_change(
    workflow_project, tmp_path, monkeypatch, status
):
    from test_builder_generation_completion import observed

    service, root = workflow_project
    folder = tmp_path / "jobs"
    observed(service, folder, status)
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    before = source_bytes(root)
    head = copy.deepcopy(service.describe("scenario", "recipes")["prototype"])
    result = prototype.finish_generation_failure(
        journal_dir=folder, request_id="request-primary", outcome=status
    )
    assert "finish_generation_failure" in prototype.__all__
    assert getattr(
        prototype.finish_generation_failure, "__adaos_authoring_operation__", False
    )
    assert result["ok"] and generation_rows(service)[0]["status"] == status
    assert service.describe("scenario", "recipes")["prototype"] == head
    assert source_bytes(root) == before


def test_successful_provider_result_cannot_be_relabelled_as_provider_failure(
    workflow_project, tmp_path, monkeypatch
):
    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    before = source_bytes(root)
    with pytest.raises(InterpretationRunRejected):
        prototype.finish_generation_failure(
            journal_dir=arguments["journal_dir"],
            request_id=arguments["request_id"],
            outcome="failed",
        )
    assert source_bytes(root) == before
    assert generation_rows(service)[0]["status"] == "running"


def test_uncommitted_preparation_cannot_be_read_as_an_accepted_projection(
    workflow_project, tmp_path, monkeypatch
):
    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    prepared = prototype.prepare_generation_publication(**arguments)
    before = source_bytes(root)
    with pytest.raises(InterpretationRunRejected):
        prototype.generation_publication_projection(
            publication_ref=prepared["publication_ref"]
        )
    assert source_bytes(root) == before


def test_committed_projection_uses_core_artifacts_and_survives_source_loss(
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
    projection = prototype.generation_publication_projection(
        publication_ref=prepared["publication_ref"]
    )
    assert projection["is_current"] and projection["prototype_stage"] == "alpha"
    assert (
        projection["webui"]
        == prototype.read_revision("scenario", "recipes", prepared["revision"])[
            "after_webui"
        ]
    )
    assert (
        projection["semantic_document"]["brief_ref"]
        == projection["prototype_brief"]["brief_id"]
    )
    (root / "webui.json").unlink()
    service._legacy_state_path("scenario", "recipes").unlink()
    before = source_bytes(root)
    assert (
        prototype.generation_publication_projection(
            publication_ref=prepared["publication_ref"]
        )
        == projection
    )
    assert source_bytes(root) == before


def test_historical_projection_cannot_claim_to_be_the_current_head(
    workflow_project, tmp_path, monkeypatch
):
    from adaos.services.builder.prototype_publications import PrototypePublications

    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    prepared = prototype.prepare_generation_publication(**arguments)
    prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )
    store = PrototypePublications(service.state_dir)
    first = store.load(prepared["publication_ref"])
    current = service.lifecycle_state("scenario", "recipes")
    successor = store.prepare(
        first["document"],
        project_ref="scenario:recipes",
        change_id=first["seed"]["change_id"],
        revision=f"{int(prepared['revision']) + 1:03d}",
        base_revision=prepared["revision"],
        previous_publication_ref=prepared["publication_ref"],
        brief=first["brief"],
        source_scope=first["seed"]["source_scope"],
    )
    store.materialize(successor["publication_ref"])
    service.commit_prepared_prototype(
        "scenario",
        "recipes",
        successor["publication_ref"],
        expected_generation=current["generation"],
    )
    before = source_bytes(root)
    previous = prototype.generation_publication_projection(
        publication_ref=prepared["publication_ref"]
    )
    latest = prototype.generation_publication_projection(
        publication_ref=successor["publication_ref"]
    )
    assert previous["is_current"] is False and latest["is_current"] is True
    assert previous["revision"] == prepared["revision"]
    assert latest["revision"] == successor["seed"]["revision"]
    assert source_bytes(root) == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_completion",
        "corrupt_completion",
        "missing_output",
        "wrong_output",
        "metrics",
        "input_refs",
    ],
)
def test_completed_preparation_recovery_requires_retained_evidence_without_recompiling(
    workflow_project, tmp_path, monkeypatch, fault
):
    from adaos.services.builder.generation_candidates import GenerationCandidates

    service, root = workflow_project
    arguments = ready_result(service, tmp_path / "jobs")
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: service)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    prepared = prototype.prepare_generation_publication(**arguments)
    prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )
    if fault in {"missing_completion", "corrupt_completion"}:
        path = next((service.state_dir / "builder/generation_success").glob("*.json"))
        if fault == "missing_completion":
            path.unlink()
        else:
            path.write_text("{}", encoding="utf-8")
    elif fault == "missing_output":
        next((service.state_dir / "builder/generation_outputs").glob("*.json")).unlink()
    elif fault == "wrong_output":
        arguments["output_ref"] = "generation-output:" + "a" * 24
    else:
        state = service._read_state("scenario", "recipes")
        row = next(
            row
            for row in state["workflow"]["change"]["runs"]
            if row["run_id"] == prepared["run_id"]
        )
        if fault == "metrics":
            row["execution_metrics"]["provider_calls"] += 1
        else:
            row["input_refs"] = []
        service._write_state("scenario", "recipes", state)
    before, current = source_bytes(root), service.describe("scenario", "recipes")
    monkeypatch.setattr(
        GenerationCandidates,
        "compile",
        lambda *args, **kwargs: pytest.fail(
            "Recovery recompiled or entered new-generation fallback"
        ),
    )
    with pytest.raises((OSError, ValueError)):
        prototype.prepare_generation_publication(**arguments)
    assert service.describe("scenario", "recipes") == current
    assert source_bytes(root) == before
