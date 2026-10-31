import json

import pytest
from test_builder_generation_observations import response, write_wire
from test_builder_generation_outputs import recover
from test_builder_generation_runs import generation_rows, invoke
from test_builder_interpretation_runs import planned
from test_builder_semantic_prototype import (
    _multi_resource_candidate,
    _multi_resource_fixture,
)
from test_builder_workflow import workflow_project as _workflow_project

from adaos.services.builder.generation_candidates import (
    GenerationCandidateRejected,
    GenerationCandidates,
)
from adaos.services.builder.generation_origin import from_run_binding
from adaos.services.builder.generation_plan import compile_generation_plan
from adaos.services.builder.generation_runs import GenerationRuns
from adaos.services.builder.interpretation_runs import InterpretationRuns
from adaos.services.builder.prototype_context import prototype_requirement_inventory
from adaos.services.builder.semantic_prototype import (
    semantic_prototype_provider_contract,
)
from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_context import digest
from adaos.services.builder_intent import capture_intent, compile_prototype_brief

workflow_project = _workflow_project


def setup(service, folder, *, output=None, project_ref="scenario:recipes"):
    planned(service)
    intent = capture_intent(
        "Покажи список.",
        project_ref=project_ref,
        source={"kind": "chat", "ref": "m.candidate"},
    )
    proposed = compile_task_spec(intent, compile_prototype_brief(intent))
    task, brief = admit_interpretation(
        proposed,
        {
            "schema": "adaos.builder.task_interpretation.v1",
            "source_digest": intent["digest"],
            "clause_roles": [{"clause_id": "clause:001", "roles": ["product"]}],
            "requirements": [
                {
                    "id": "list",
                    "clause_ids": ["clause:001"],
                    "statement": "Покажи список.",
                    "kind": "behavior",
                    "operation": "inspect",
                    "verification_family": "runtime",
                    "supersedes": [],
                }
            ],
            "facts": [],
            "transitions": [],
            "constraints": [],
            "questions": [],
            "design_freedom": [],
        },
        intent,
    )
    _, document = _multi_resource_fixture()
    candidate = _multi_resource_candidate(document)
    # The older canonical compiler fixture intentionally omits nullable
    # provider fields. Supply the full wire grammar in this boundary control;
    # product code must not invent missing fields on behalf of the model.
    candidate.update(
        capability_surfaces=[], automation_requirements=[], representative_states=[]
    )
    for resource in candidate["resources"]:
        resource.setdefault("read_only_when", None)
        for field in resource["fields"]:
            field.setdefault("help_text", None)
            field.setdefault("placeholder", None)
    for relationship in candidate["relationships"]:
        relationship.setdefault("label_field_refs", [])
    for view in candidate["views"]:
        view.setdefault("surface", "inline")
        for key in (
            "media",
            "compact_presentation",
            "presentation_options",
            "section",
            "selection",
            "selection_filter",
        ):
            view.setdefault(key, None)
        view.setdefault("field_display", [])
        view.setdefault("scope_filters", [])
    for command in candidate["commands"]:
        command.setdefault(
            "exposure", {"placement": "editor_primary", "presentation": "button"}
        )
    candidate["requirement_bindings"] = [
        {
            "requirement_ref": row["id"],
            "semantic_refs": [{"kind": "view", "id": candidate["views"][0]["id"]}],
        }
        for row in prototype_requirement_inventory(brief)
    ]
    schema = semantic_prototype_provider_contract(
        version="v2", locales=("en", "ru"), brief=brief
    )
    plan = compile_generation_plan(
        task,
        model="selected",
        transport="personal",
        execution_origin_required=True,
        execution_run_required=True,
        output_schema_digest=digest(schema),
    )
    origin = from_run_binding(
        task, plan, InterpretationRuns(service).bind(task, plan["generation_plan_id"])
    )
    binding = invoke(GenerationRuns(service), task, plan, origin)["binding"]
    folder.mkdir(exist_ok=True)
    wire = write_wire(folder, task, plan, origin, binding)
    parent = folder / (plan["generation_plan_id"].replace(":", "-") + ".json")
    retained = json.loads(parent.read_text(encoding="utf-8"))
    retained.update(brief=brief, provider_output_schema=schema, baseline=None)
    parent.write_text(json.dumps(retained), encoding="utf-8")
    result = response()
    result["output_text"] = (
        output if output is not None else json.dumps(candidate, ensure_ascii=False)
    )
    reference = recover(service, folder, result)["generation_output"]["output_ref"]
    return {
        "output_ref": reference,
        "journal_dir": folder,
        "binding": binding,
        "origin": origin,
        "plan": plan,
        "wire": wire,
    }


def test_compilation_consumes_exact_retained_text_and_admitted_inputs_without_head_or_run_completion(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    inputs = setup(service, tmp_path / "jobs")
    before = service.describe("scenario", "recipes")
    candidates = GenerationCandidates(service.state_dir)
    result = candidates.compile(**inputs)
    assert result["output_ref"] == inputs["output_ref"]
    assert result["run_id"] == inputs["binding"]["run_id"]
    assert not result["head_advanced"] and not result["runtime_accepted"]
    assert result["task_acceptance"]["static_status"] == "passed"
    assert result["task_acceptance"]["status"] == "runtime_acceptance_pending"
    assert candidates.compile(**inputs) == result
    assert service.describe("scenario", "recipes") == before
    assert generation_rows(service)[0]["status"] == "running"


@pytest.mark.parametrize(
    "text,code",
    [
        ('{"schema":"x","schema":"y"}', "duplicate_json_key"),
        ('{"value": NaN}', "non_json_number"),
        ('{"value": Infinity}', "non_json_number"),
        ("[]", "object_required"),
        ("```json\n{}\n```", "invalid_json"),
        ("{}{}", "invalid_json"),
        ('{"schema":"adaos.webui.semantic.v2"}', "full_v2_required"),
        (
            '{"schema":"adaos.builder.semantic_prototype_candidate.v2"}',
            "provider_grammar_mismatch",
        ),
    ],
)
def test_unadmitted_text_cannot_be_repaired_or_promoted_by_parser_guessing(
    workflow_project, tmp_path, text, code
):
    service, _ = workflow_project
    inputs = setup(service, tmp_path / "jobs", output=text)
    before = service.describe("scenario", "recipes")
    store = GenerationCandidates(service.state_dir)
    with pytest.raises(GenerationCandidateRejected, match=code):
        store.compile(**inputs)
    assert service.describe("scenario", "recipes") == before
    assert not store.root.exists()


@pytest.mark.parametrize("fault", ["task", "brief", "baseline", "grammar"])
def test_compilation_cannot_substitute_retained_task_brief_baseline_or_grammar(
    workflow_project, tmp_path, fault
):
    service, _ = workflow_project
    inputs = setup(service, tmp_path / "jobs")
    path = inputs["journal_dir"] / (
        inputs["plan"]["generation_plan_id"].replace(":", "-") + ".json"
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    if fault == "task":
        record["task_spec"]["source"]["statement"] = "different source"
    elif fault == "brief":
        record["brief"]["residual_requirements"].append("different requirement")
    elif fault == "baseline":
        record["baseline"] = {"changed": True}
    elif fault == "grammar":
        record["provider_output_schema"] = {"type": "object"}
    path.write_text(json.dumps(record), encoding="utf-8")
    before = service.describe("scenario", "recipes")
    with pytest.raises(ValueError):
        GenerationCandidates(service.state_dir).compile(**inputs)
    assert service.describe("scenario", "recipes") == before


def test_prepared_candidate_corruption_refuses_without_replacing_evidence(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    inputs = setup(service, tmp_path / "jobs")
    store = GenerationCandidates(service.state_dir)
    result = store.compile(**inputs)
    path = store.root / (result["candidate_id"].partition(":")[2] + ".json")
    path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(GenerationCandidateRejected):
        store.compile(**inputs)
    assert path.read_text(encoding="utf-8") == "{corrupt"


def test_late_compilation_retains_old_origin_without_authoring_in_new_change(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    inputs = setup(service, tmp_path / "jobs")
    store = GenerationCandidates(service.state_dir)
    result = store.compile(**inputs)
    planned(service, "CH-later")
    before = service.describe("scenario", "recipes")
    assert store.compile(**inputs) == result
    assert service.describe("scenario", "recipes") == before


@pytest.mark.parametrize("text", ['{"value":1e999}', '{"value":-1e999}'])
def test_overflowing_json_numbers_cannot_enter_canonical_semantics(text):
    from adaos.services.builder.generation_candidates import _json_object

    with pytest.raises(GenerationCandidateRejected, match="non_json_number"):
        _json_object(text)
