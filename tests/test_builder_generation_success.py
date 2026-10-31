import copy
import json
from contextvars import copy_context

import pytest
from test_builder_authoring_scope import scoped_project as _scoped_project
from test_builder_generation_candidates import setup
from test_builder_generation_runs import generation_rows
from test_builder_workflow import workflow_project as _workflow_project

from adaos.services.builder.generation_candidates import GenerationCandidates
from adaos.services.builder.generation_submissions import GenerationSubmissions
from adaos.services.builder.generation_success import complete_generation_success
from adaos.services.builder.interpretation_runs import InterpretationRunRejected
from adaos.services.builder.prototype_publications import PrototypePublications

workflow_project = _workflow_project
scoped_project = _scoped_project


def prepared(service, folder, *, project_ref="scenario:recipes"):
    inputs = setup(service, folder, project_ref=project_ref)
    candidate = GenerationCandidates(service.state_dir).compile(**inputs)
    origin, binding, plan = (inputs[key] for key in ("origin", "binding", "plan"))
    parent = json.loads(
        (
            folder / (origin["generation_plan_ref"].replace(":", "-") + ".json")
        ).read_text(encoding="utf-8")
    )
    current = service.describe("scenario", "recipes")
    base = current["prototype"].get("head_revision")
    revision = f"{int(base or '0') + 1:03d}"
    store = PrototypePublications(service.state_dir)
    value = store.prepare(
        candidate["semantic_document"],
        project_ref="scenario:recipes",
        change_id=origin["change_id"],
        revision=revision,
        base_revision=current["prototype"].get("head_revision"),
        brief=parent["brief"],
        source_scope={
            key: origin[key]
            for key in ("project_ref", "object_type", "object_id", "manifest_digest")
        },
        compiler_digest=candidate["compiler_digest"],
    )
    store.materialize(value["publication_ref"])
    submissions = GenerationSubmissions(
        folder, binding=binding, origin=origin, plan=plan
    )
    with submissions.submitting(inputs["wire"]["request_id"]) as register:
        register({"job_id": inputs["wire"]["job_id"]})
    return {**inputs, "publication_ref": value["publication_ref"]}, candidate


def test_success_reuses_the_reserved_run_and_commits_with_head_in_one_state_write(
    workflow_project, tmp_path, monkeypatch
):
    service, _ = workflow_project
    inputs, candidate = prepared(service, tmp_path / "jobs")
    before = service.describe("scenario", "recipes")
    old_run = generation_rows(service)[0]
    original = type(service)._write_state
    commits = []

    def write(instance, kind, object_id, state):
        workflow = state.get("workflow") or {}
        if (
            workflow.get("prototype", {}).get("publication_ref")
            == inputs["publication_ref"]
        ):
            row = next(
                row
                for row in workflow["change"]["runs"]
                if row["run_id"] == inputs["binding"]["run_id"]
            )
            assert row["status"] == "succeeded"
            commits.append(copy.deepcopy(row))
        return original(instance, kind, object_id, state)

    monkeypatch.setattr(type(service), "_write_state", write)
    result = complete_generation_success(service, **inputs)
    after = service.describe("scenario", "recipes")
    assert result["committed"] and len(commits) == 1
    assert after["generation"] == before["generation"] + 1
    assert after["prototype"]["publication_ref"] == inputs["publication_ref"]
    assert len(after["change"]["runs"]) == len(before["change"]["runs"])
    row = generation_rows(service)[0]
    assert row["run_id"] == old_run["run_id"] and row["status"] == "succeeded"
    assert row["execution_metrics"] == old_run["execution_metrics"]
    assert row["input_refs"] == old_run["input_refs"]
    assert set(old_run["evidence_refs"]) <= set(row["evidence_refs"])
    assert {
        candidate["candidate_id"],
        inputs["output_ref"],
        result["completion_ref"],
    } <= set(row["evidence_refs"])
    assert inputs["publication_ref"] in row["output_refs"]
    assert complete_generation_success(service, **inputs)["duplicate"]
    assert service.describe("scenario", "recipes") == after
    assert len(commits) == 1


def test_failure_before_state_replace_leaves_both_head_and_run_unaccepted_then_retries(
    workflow_project, tmp_path, monkeypatch
):
    service, _ = workflow_project
    inputs, _ = prepared(service, tmp_path / "jobs")
    original = type(service)._write_state

    def fail(instance, kind, object_id, state):
        if (
            state.get("workflow", {}).get("prototype", {}).get("publication_ref")
            == inputs["publication_ref"]
        ):
            raise OSError("controlled compound head replace failure")
        return original(instance, kind, object_id, state)

    monkeypatch.setattr(type(service), "_write_state", fail)
    with pytest.raises(OSError, match="compound head replace"):
        complete_generation_success(service, **inputs)
    assert (
        service.describe("scenario", "recipes")["prototype"].get("publication_ref")
        != inputs["publication_ref"]
    )
    row = generation_rows(service)[0]
    assert (
        row["status"] == "running" and row["execution_metrics"]["provider_calls"] == 1
    )
    monkeypatch.setattr(type(service), "_write_state", original)
    assert complete_generation_success(service, **inputs)["committed"]
    assert generation_rows(service)[0]["status"] == "succeeded"


def test_failure_after_state_replace_is_pending_projection_not_failed_generation(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    inputs, _ = prepared(service, tmp_path / "jobs")

    def fail(event):
        raise OSError("controlled event projection failure")

    service.event_sink = fail
    result = complete_generation_success(service, **inputs)
    assert result["committed"] and result["projection_pending"]
    assert (
        service.describe("scenario", "recipes")["prototype"]["publication_ref"]
        == inputs["publication_ref"]
    )
    assert generation_rows(service)[0]["status"] == "succeeded"
    assert complete_generation_success(service, **inputs)["duplicate"]


@pytest.mark.parametrize(
    "fault", ["wrong_document", "metrics", "missing_run", "forged_capability"]
)
def test_unproven_success_never_advances_head(workflow_project, tmp_path, fault):
    service, _ = workflow_project
    inputs, candidate = prepared(service, tmp_path / "jobs")
    if fault == "wrong_document":
        store = PrototypePublications(service.state_dir)
        original = store.load(inputs["publication_ref"])
        document = copy.deepcopy(candidate["semantic_document"])
        document["title"]["ru"] = "Unrelated candidate"
        value = store.prepare(
            document,
            project_ref="scenario:recipes",
            change_id=inputs["origin"]["change_id"],
            revision=original["seed"]["revision"],
            base_revision=original["seed"]["base_revision"],
            brief=original["brief"],
            source_scope=original["seed"]["source_scope"],
            compiler_digest=candidate["compiler_digest"],
        )
        store.materialize(value["publication_ref"])
        inputs["publication_ref"] = value["publication_ref"]
    elif fault in {"metrics", "missing_run"}:
        state = service._read_state("scenario", "recipes")
        rows = state["workflow"]["change"]["runs"]
        row = next(row for row in rows if row["run_id"] == inputs["binding"]["run_id"])
        if fault == "metrics":
            row["execution_metrics"]["provider_calls"] += 1
        else:
            rows.remove(row)
        service._write_state("scenario", "recipes", state)
    before = service.describe("scenario", "recipes")
    with pytest.raises(InterpretationRunRejected):
        if fault == "forged_capability":
            service.commit_prepared_prototype(
                "scenario",
                "recipes",
                inputs["publication_ref"],
                expected_generation=inputs["origin"]["origin_generation"],
                _generation_success=object(),
            )
        else:
            complete_generation_success(service, **inputs)
    assert service.describe("scenario", "recipes") == before


def test_expired_success_capability_cannot_outlive_its_issued_scope(workflow_project):
    from adaos.services.builder.generation_success import _issued, admitted_success

    service, _ = workflow_project
    payload = {"completion": {"publication_ref": "prototype-publication:" + "a" * 24}}
    with _issued(service, payload) as capability:
        copied = copy_context()
    with pytest.raises(InterpretationRunRejected):
        copied.run(
            admitted_success,
            capability,
            service,
            payload["completion"]["publication_ref"],
        )


def test_project_owned_data_and_scenario_run_commit_without_a_second_head(
    scoped_project, tmp_path
):
    from adaos.services.builder.workflow_authority import WorkflowAuthority
    from adaos.services.resources.prototype import PrototypeResourceService

    service, _, _ = scoped_project
    inputs, _ = prepared(
        service, tmp_path / "jobs", project_ref="project:qualification"
    )
    assert complete_generation_success(service, **inputs)["committed"]
    publication = PrototypePublications(service.state_dir).load(
        inputs["publication_ref"]
    )
    runtime = PrototypeResourceService(state_dir=service.state_dir)
    for spec in publication["resource_specs"]:
        state = runtime.snapshot(spec["resource_definition"]["resource_type"])
        assert state["project_ref"] == "project:qualification"
        assert state["workflow_ref"] == "scenario:recipes"
    assert generation_rows(service)[0]["status"] == "succeeded"
    assert WorkflowAuthority(service.state_dir).resolve("project:qualification") is None


def test_manifest_drift_blocks_authoring_without_erasing_the_paid_run(
    scoped_project, tmp_path
):
    service, path, manifest = scoped_project
    inputs, _ = prepared(
        service, tmp_path / "jobs", project_ref="project:qualification"
    )
    manifest["catalog"]["title"] = "Changed after paid generation"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = service.describe("scenario", "recipes")
    with pytest.raises(ValueError):
        complete_generation_success(service, **inputs)
    assert service.describe("scenario", "recipes") == before
    assert generation_rows(service)[0]["status"] == "running"


def test_actual_atomic_replace_failure_cannot_accept_only_half_the_transaction(
    workflow_project, tmp_path, monkeypatch
):
    from pathlib import Path

    from adaos.services.artifact_pipeline import storage
    from adaos.services.builder.workflow_authority import WorkflowAuthority

    service, _ = workflow_project
    inputs, _ = prepared(service, tmp_path / "jobs")
    authority = WorkflowAuthority(service.state_dir)
    authority.adopt(
        "scenario:recipes", service._legacy_state_path("scenario", "recipes")
    )
    head = authority.head_path("scenario:recipes")
    before = head.read_bytes()
    original = storage.replace_with_retry

    def fail(source, target):
        if Path(target) == head:
            raise OSError("controlled actual atomic replacement failure")
        return original(source, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(storage, "replace_with_retry", fail)
        with pytest.raises(OSError, match="actual atomic replacement"):
            complete_generation_success(service, **inputs)
    assert head.read_bytes() == before
    assert generation_rows(service)[0]["status"] == "running"
    assert complete_generation_success(service, **inputs)["committed"]


def test_unpinned_publication_lowering_cannot_claim_current_candidate_compilation(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    inputs, candidate = prepared(service, tmp_path / "jobs")
    store = PrototypePublications(service.state_dir)
    original = store.load(inputs["publication_ref"])
    unqualified = store.prepare(
        candidate["semantic_document"],
        project_ref="scenario:recipes",
        change_id=inputs["origin"]["change_id"],
        revision=original["seed"]["revision"],
        base_revision=original["seed"]["base_revision"],
        brief=original["brief"],
        source_scope=original["seed"]["source_scope"],
    )
    store.materialize(unqualified["publication_ref"])
    inputs["publication_ref"] = unqualified["publication_ref"]
    before = service.describe("scenario", "recipes")
    with pytest.raises(InterpretationRunRejected):
        complete_generation_success(service, **inputs)
    assert service.describe("scenario", "recipes") == before


@pytest.mark.parametrize("fault", ["wrong_pin", "changed_during_preparation"])
def test_pinned_preparation_refuses_compiler_mismatch_without_writing_a_preparation(
    workflow_project, tmp_path, monkeypatch, fault
):
    from adaos.services.builder import generation_candidates as module
    from adaos.services.builder.prototype_publications import (
        PrototypePublicationRejected,
    )

    service, _ = workflow_project
    inputs, candidate = prepared(service, tmp_path / "jobs")
    store = PrototypePublications(service.state_dir)
    original = store.load(inputs["publication_ref"])
    before = {path: path.read_bytes() for path in store.root.glob("*.json")}
    pin = "sha256:" + "a" * 64
    if fault == "changed_during_preparation":
        versions = iter([candidate["compiler_digest"], pin])
        monkeypatch.setattr(module, "_compiler_identity", lambda: next(versions))
        pin = candidate["compiler_digest"]
    with pytest.raises(PrototypePublicationRejected, match="compiler"):
        store.prepare(
            candidate["semantic_document"],
            project_ref="scenario:recipes",
            change_id=inputs["origin"]["change_id"],
            revision=original["seed"]["revision"],
            base_revision=original["seed"]["base_revision"],
            brief=original["brief"],
            source_scope=original["seed"]["source_scope"],
            compiler_digest=pin,
        )
    assert {path: path.read_bytes() for path in store.root.glob("*.json")} == before
    assert generation_rows(service)[0]["status"] == "running"
