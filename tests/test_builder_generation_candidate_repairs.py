"""Real protected output -> bounded reducer -> full admission and original Run."""
import copy
import json

import pytest
from test_builder_generation_candidates import setup
from test_builder_generation_observations import response, write_wire
from test_builder_generation_outputs import recover
from test_builder_generation_runs import generation_rows
from test_builder_workflow import workflow_project as _workflow_project

from adaos.sdk.builder import prototype
from adaos.services.builder.generation_authoring import _result_context
from adaos.services.builder.generation_candidate_sources import (
    GenerationCandidateSources,
)
from adaos.services.builder.generation_candidates import GenerationCandidates
from adaos.services.builder.generation_outputs import GenerationOutputs
from adaos.services.builder.generation_runs import GenerationRuns
from adaos.services.builder.generation_submissions import GenerationSubmissions
from adaos.services.builder.repair_context import prepare_repair_context
from adaos.services.policy.invocation import verified_invocation

workflow_project = _workflow_project


def make_repair(workflow, folder, *, patch_transform=None):
    def missing_binding(candidate):
        candidate["requirement_bindings"][0]["semantic_refs"][0]["id"] = "not-a-view"
        return candidate

    inputs = setup(workflow, folder, candidate_transform=missing_binding)
    source = GenerationCandidateSources(workflow.state_dir).resolve(**inputs)
    record = GenerationOutputs._read(folder / (inputs["plan"]["generation_plan_id"].replace(":", "-") + ".json"))
    candidate = source["candidate"]
    try:
        prototype.compile_semantic_candidate(candidate, brief=record["brief"], project_ref=inputs["origin"]["project_ref"])
    except ValueError as error:
        findings = copy.deepcopy(error.findings)
    else:
        pytest.fail("The initial candidate must have a real compiler defect")
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        context = prepare_repair_context(
            job_id=inputs["wire"]["job_id"], candidate=candidate, findings=findings,
            brief=record["brief"], journal_dir=folder, generation_run_observer=GenerationRuns(workflow))
    assert context["kind"] == "semantic_binding_repair"
    scope = context["scope"]
    patch = {"schema": context["output_schema"]["properties"]["schema"]["enum"][0],
             "base_sha256": scope["base_sha256"], "bindings": [{
                 "requirement_ref": candidate["requirement_bindings"][0]["requirement_ref"],
                 "add_semantic_refs": [{"kind": "view", "id": candidate["views"][0]["id"]}]}]}
    if patch_transform:
        patch = patch_transform(patch)
    child = retain_patch(workflow, folder, inputs, record, context, patch)
    return inputs, child, candidate, context, patch, record


def retain_patch(workflow, folder, inputs, record, context, patch, *, suffix=""):
    # The wire helper writes a minimal admission; preserve the actual Core
    # repair evidence around it, exactly as a real provider adapter does.
    plan = context["generation_plan"]
    path = folder / ("repair-plan-" + plan["generation_plan_id"].partition(":")[2] + ".json")
    evidence = path.read_bytes()
    wire = write_wire(folder, record["task_spec"], plan, inputs["origin"], context["execution_run_binding"],
                      job="repair-job" + suffix, request="repair-request" + suffix)
    path.write_bytes(evidence)
    result = {**response("repair-job" + suffix), "output_text": json.dumps(patch, ensure_ascii=False)}
    output = recover(workflow, folder, result)["generation_output"]["output_ref"]
    child = _result_context(folder, "repair-request" + suffix, output)
    assert child["wire"] == wire
    return child


def test_scoped_patch_replays_protected_parent_and_passes_full_admission(workflow_project, tmp_path):
    workflow, _ = workflow_project
    primary, child, original, _, _, _ = make_repair(workflow, tmp_path / "jobs")
    before = copy.deepcopy(workflow.describe("scenario", "recipes"))
    source = GenerationCandidateSources(workflow.state_dir).resolve(**child)
    assert source["candidate"]["requirement_bindings"][0]["semantic_refs"] == [
        {"kind": "view", "id": original["views"][0]["id"]}]
    assert source["candidate"]["resources"] == original["resources"]
    assert len(source["source_receipt"]["lineage"]) == 1
    assert source["source_receipt"]["lineage"][0]["kind"] == "semantic_binding_repair"
    candidate = GenerationCandidates(workflow.state_dir).compile(**child)
    assert candidate["source_receipt"] == source["source_receipt"]
    assert candidate["task_acceptance"]["static_status"] == "passed"
    assert candidate["run_id"] == primary["binding"]["run_id"]
    assert workflow.describe("scenario", "recipes") == before
    assert GenerationCandidates(workflow.state_dir).compile(**child) == candidate


def test_explicit_followup_closes_a_rejected_candidate_run_before_reserving_its_repair(workflow_project, tmp_path):
    workflow, _ = workflow_project
    folder = tmp_path/'jobs'
    primary, child, _, _, _, record = make_repair(workflow, folder,
        patch_transform=lambda patch: {**patch, 'bindings': []})
    head = workflow.describe('scenario', 'recipes')['prototype']['head_revision']
    for inputs in (primary, child):
        with GenerationSubmissions(folder, binding=inputs['binding'], origin=inputs['origin'],
                                   plan=inputs['plan']).submitting(inputs['wire']['request_id']) as register:
            register({'job_id': inputs['wire']['job_id']})
    source = GenerationCandidateSources(workflow.state_dir).resolve(**child)
    with pytest.raises(ValueError) as rejected:
        prototype.compile_semantic_candidate(source['candidate'], brief=record['brief'], project_ref=primary['origin']['project_ref'])
    with verified_invocation(request_id='explicit-follow-up', idempotency_key=None):
        followup = prepare_repair_context(job_id=child['wire']['job_id'], candidate=source['candidate'],
            findings=rejected.value.findings, brief=record['brief'], journal_dir=folder,
            generation_run_observer=GenerationRuns(workflow))
    assert followup['status'] == 'admitted'
    assert followup['execution_run_binding']['run_id'] != child['binding']['run_id']
    rows = {row['run_id']: row for row in generation_rows(workflow)}
    assert rows[child['binding']['run_id']]['status'] == 'failed'
    assert rows[followup['execution_run_binding']['run_id']]['status'] == 'queued'
    assert child['binding']['run_id'] in rows[followup['execution_run_binding']['run_id']]['input_refs']
    assert workflow.describe('scenario', 'recipes')['prototype']['head_revision'] == head


def test_caller_cannot_substitute_the_pre_repair_candidate(workflow_project, tmp_path):
    workflow, _ = workflow_project
    primary, _, original, context, _, record = make_repair(workflow, tmp_path / "jobs")
    before = copy.deepcopy(workflow.describe("scenario", "recipes"))
    original["title"]["en"] = "Unpaid caller substitution"
    with (verified_invocation(request_id="trusted-turn", idempotency_key=None),
          pytest.raises(ValueError, match="repair_source_conflict")):
        prepare_repair_context(
            job_id=primary["wire"]["job_id"], candidate=original,
            findings=context["diagnosis"]["findings"], brief=record["brief"],
            journal_dir=tmp_path / "jobs", generation_run_observer=GenerationRuns(workflow))
    assert workflow.describe("scenario", "recipes") == before


def test_scoped_repair_commits_original_run_with_both_paid_observations(workflow_project, tmp_path, monkeypatch):
    workflow, _ = workflow_project
    primary, child, _, _, _, _ = make_repair(workflow, tmp_path / "jobs")
    for inputs in (primary, child):
        with GenerationSubmissions(inputs["journal_dir"], binding=inputs["binding"],
                                   origin=inputs["origin"], plan=inputs["plan"]).submitting(inputs["wire"]["request_id"]) as register:
            register({"job_id": inputs["wire"]["job_id"]})
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: workflow)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    options = {key: child[key] for key in ("journal_dir", "output_ref")}
    options["request_id"] = child["wire"]["request_id"]
    prepared = prototype.prepare_generation_publication(**options)
    result = prototype.commit_generation_publication(**options, publication_ref=prepared["publication_ref"])
    assert result["committed"] and result["run_id"] == primary["binding"]["run_id"]
    rows = generation_rows(workflow)
    assert len(rows) == 1 and rows[0]["status"] == "succeeded"
    assert rows[0]["execution_metrics"]["provider_calls"] == 2
    assert prototype.prepare_generation_publication(**options) == prepared
    assert prototype.commit_generation_publication(**options, publication_ref=prepared["publication_ref"])["duplicate"]


@pytest.mark.parametrize("fault", ["scope", "findings", "parent", "source", "reducer", "missing_parent_output"])
def test_retained_repair_tampering_cannot_compile_or_move_head(workflow_project, tmp_path, monkeypatch, fault):
    workflow, _ = workflow_project
    primary, child, _, _, _, _ = make_repair(workflow, tmp_path / "jobs")
    before = copy.deepcopy(workflow.describe("scenario", "recipes"))
    path = tmp_path / "jobs" / ("repair-plan-" + child["plan"]["generation_plan_id"].partition(":")[2] + ".json")
    admission = json.loads(path.read_text(encoding="utf-8"))
    if fault == "scope":
        admission["scope"]["task"] = "Broaden all writes"
    elif fault == "findings":
        admission["findings"] = []
    elif fault == "parent":
        admission["generation_plan"]["parent_request_id"] = "other"
    elif fault == "source":
        admission["source_receipt"]["candidate_digest"] = "other"
    elif fault == "reducer":
        monkeypatch.setattr("adaos.services.builder.generation_candidate_sources.reducer_digest", lambda: "changed")
    else:
        store = GenerationOutputs(workflow.state_dir)
        parent_path, _ = store._context(primary["journal_dir"], primary["binding"], primary["origin"], primary["plan"], primary["wire"])
        parent_path.unlink()
    path.write_text(json.dumps(admission), encoding="utf-8")
    with pytest.raises(ValueError):
        GenerationCandidates(workflow.state_dir).compile(**child)
    assert workflow.describe("scenario", "recipes") == before


@pytest.mark.parametrize("fault", ["base", "extra_write", "duplicate_binding", "unresolved"])
def test_protected_but_invalid_patch_cannot_be_accepted(workflow_project, tmp_path, fault):
    workflow, _ = workflow_project

    def corrupt(patch):
        if fault == "base":
            patch["base_sha256"] = "0" * 64
        elif fault == "extra_write":
            patch["resources"] = []
        elif fault == "duplicate_binding":
            patch["bindings"].append(copy.deepcopy(patch["bindings"][0]))
        else:
            patch["bindings"] = []
        return patch

    _, child, _, _, _, _ = make_repair(workflow, tmp_path / "jobs", patch_transform=corrupt)
    before = copy.deepcopy(workflow.describe("scenario", "recipes"))
    with pytest.raises(ValueError):
        GenerationCandidates(workflow.state_dir).compile(**child)
    assert workflow.describe("scenario", "recipes") == before


def test_second_scope_reconstructs_first_patch_and_keeps_root_task(workflow_project, tmp_path):
    workflow, _ = workflow_project
    folder = tmp_path / "jobs"
    saved = {}

    def two_defects(candidate):
        saved["record"] = copy.deepcopy(candidate["resources"][0]["records"][0])
        candidate["resources"][0]["records"][0]["values"].pop()
        candidate["requirement_bindings"][0]["semantic_refs"][0]["id"] = "not-a-view"
        return candidate

    inputs = setup(workflow, folder, candidate_transform=two_defects)
    record = GenerationOutputs._read(folder / (inputs["plan"]["generation_plan_id"].replace(":", "-") + ".json"))
    source = GenerationCandidateSources(workflow.state_dir).resolve(**inputs)
    candidate = source["candidate"]
    findings = [{"code": "semantic.record_arity", "path": "$.resources[0].records[0].values"}]
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        first = prepare_repair_context(job_id=inputs["wire"]["job_id"], candidate=candidate,
            findings=findings, brief=record["brief"], journal_dir=folder, generation_run_observer=GenerationRuns(workflow))
    patch = {"schema": "adaos.builder.record_repair.v1", "base_sha256": first["scope"]["base_sha256"],
             "corrections": [{"resource_ref": candidate["resources"][0]["id"],
                              "record_id": saved["record"]["id"], "values": saved["record"]["values"]}]}
    child = retain_patch(workflow, folder, inputs, record, first, patch, suffix="-1")
    intermediate = GenerationCandidateSources(workflow.state_dir).resolve(**child)
    with pytest.raises(ValueError) as rejected:
        prototype.compile_semantic_candidate(intermediate["candidate"], brief=record["brief"], project_ref=inputs["origin"]["project_ref"])
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        second = prepare_repair_context(job_id=child["wire"]["job_id"], candidate=intermediate["candidate"],
            findings=rejected.value.findings, brief=record["brief"], journal_dir=folder, generation_run_observer=GenerationRuns(workflow))
    assert second["parent_request_id"] == "repair-request-1"
    assert second["generation_plan"]["parent_generation_plan_ref"] == inputs["plan"]["generation_plan_id"]
    patch = {"schema": second["output_schema"]["properties"]["schema"]["enum"][0],
        "base_sha256": second["scope"]["base_sha256"], "bindings": [{
            "requirement_ref": candidate["requirement_bindings"][0]["requirement_ref"],
            "add_semantic_refs": [{"kind": "view", "id": candidate["views"][0]["id"]}]}]}
    last = retain_patch(workflow, folder, inputs, record, second, patch, suffix="-2")
    compiled = GenerationCandidates(workflow.state_dir).compile(**last)
    assert len(compiled["source_receipt"]["lineage"]) == 2
    assert compiled["task_acceptance"]["static_status"] == "passed"
