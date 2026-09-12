from __future__ import annotations

import copy
import re

import pytest
from jsonschema import ValidationError

from adaos.sdk.builder import prototype as prototype_sdk
from adaos.sdk.developer import prototypes as developer_prototypes
from adaos.services.builder.semantic_prototype import (
    compile_semantic_prototype_candidate,
    compile_semantic_prototype,
    semantic_prototype_candidate_contract,
    semantic_prototype_provider_contract,
    semantic_prototype_generation_guidance,
    validate_semantic_prototype,
)
from adaos.services.builder.workflow import BuilderWorkflowError
from adaos.services.builder_intent import compile_prototype_brief


def _text(key: str, en: str, ru: str) -> dict[str, str]:
    return {"key": key, "en": en, "ru": ru}


def test_nonempty_command_guards_respect_number_and_boolean_field_types() -> None:
    brief, document = _fixture()
    for identifier, kind, value in [("count", "number", 0), ("checked", "boolean", False)]:
        document["resource"]["fields"].append({
            "id": identifier, "label": _text(identifier, identifier, identifier),
            "value_type": kind, "editable": True, "required": False,
        })
        for record in document["resource"]["records"]:
            record[identifier] = value
        document["views"][2]["field_refs"].append(identifier)
        document["commands"][1]["input_field_refs"].append(identifier)
        document["commands"][1]["guard"]["require_nonempty"].append(identifier)
    compiled = compile_semantic_prototype(document, brief=brief)
    editor = next(widget for widget in compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
                  if widget["id"] == "work-editor")
    guard = next(action["enabledIf"] for action in editor["actions"] if action["id"] == "complete")
    assert "count.length" not in guard
    assert "checked.length" not in guard
    assert "$state.count != null" in guard
    assert "$state.checked != null" in guard
    assert "$state.comment.length > 0" in guard


def test_declared_types_survive_empty_resources_and_null_only_seeds() -> None:
    from jsonschema import Draft202012Validator

    _, document = _fixture()
    document["resource"]["records"] = []
    document["resource"]["fields"].append({
        "id": "measure", "label": _text("measure", "Measure", "Measure"),
        "value_type": "number", "editable": False, "required": False,
    })
    document["requirement_bindings"] = []
    compiled = compile_semantic_prototype(document)
    spec = developer_prototypes.derive_record_resource_spec(compiled["webui"], [])
    validator = Draft202012Validator(spec["data_definition"]["record_schema"])
    for value in (None, 0, 12.5):
        validator.validate({"id": "one", "revision": 1, "measure": value})
    assert not validator.is_valid({"id": "one", "revision": 1, "measure": "twelve"})
    assert not validator.is_valid({"id": "one", "revision": 1, "undeclared": "value"})
    seeded = developer_prototypes.derive_record_resource_spec(compiled["webui"], [{"id": "one", "measure": None}])
    assert seeded["data_definition"]["record_schema"] == spec["data_definition"]["record_schema"]


def test_fixed_transition_editor_does_not_require_artificial_editable_input() -> None:
    _, document = _fixture()
    for field in document["resource"]["fields"]:
        field["editable"] = False
    document["commands"] = [{
        **document["commands"][1], "input_field_refs": [], "fixed_values": {"status": "completed"},
    }]
    document["commands"][0].pop("guard", None)
    document["requirement_bindings"] = []
    assert compile_semantic_prototype(document)["validation"]["ok"]


def test_collection_evidence_closes_only_unambiguous_declared_ownership() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    binding = next(item for item in candidate["requirement_bindings"] if item["requirement_ref"] == "collection:01")
    binding["semantic_refs"] = [{"kind": "resource", "id": "work_items"}]
    result = compile_semantic_prototype_candidate(candidate, brief=brief)
    derived = {item["to"] for item in result["normalizations"] if item["kind"] == "binding_ownership"}
    assert {"view:work-list", "view:work-editor"} <= derived
    other = copy.deepcopy(next(view for view in candidate["views"] if view["id"] == "work-editor"))
    other["id"] = "other-editor"
    candidate["views"].append(other)
    with pytest.raises(BuilderWorkflowError, match="matching editor view"):
        compile_semantic_prototype_candidate(candidate, brief=brief)


def test_relationship_choice_options_are_derived_before_record_validation() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    owner = next(field for field in candidate["resources"][0]["fields"] if field["id"] == "work_owner_id")
    owner["value_type"] = "choice"
    owner["options"] = []
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    field = next(field for field in compiled["semantic_document"]["resources"][0]["fields"] if field["id"] == "work_owner_id")
    assert {option["value"] for option in field["options"]} == {"person-1", "person-2"}


@pytest.mark.parametrize("surface", ["inline", "modal"])
def test_lookup_only_resource_materializes_without_inventing_a_collection(surface) -> None:
    from adaos.sdk.developer.ui import evaluate

    brief, semantic = _multi_resource_fixture()
    semantic["views"] = [view for view in semantic["views"] if view["resource_ref"] != "people"]
    editor = next(view for view in semantic["views"] if view["role"] == "editor")
    editor["surface"] = surface
    editor["field_refs"].append("work_owner_id")
    next(command for command in semantic["commands"] if command["kind"] == "update")["input_field_refs"].append("work_owner_id")
    semantic["relationships"][0]["label_field_refs"] = ["person_name"]
    candidate = _multi_resource_candidate(semantic)
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    page = compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    assert all(widget.get("dataSource", {}).get("resourceType") != "prototype.people" for widget in page["widgets"])
    widgets = developer_prototypes._surface_widgets(compiled["webui"])
    field = next(field for widget in widgets if widget["type"] == "ui.form"
                 for field in widget["inputs"]["fields"] if field["id"] == "work_owner_id")
    assert field["optionLabelPaths"] == ["person_name"]
    assert field["optionsDataSource"]["resourceType"] == "prototype.people"
    resource = next(resource for resource in compiled["prototype_resources"] if resource["resource_ref"] == "people")
    spec = developer_prototypes.derive_record_resource_spec(compiled["webui"], resource["records"], resource_type=resource["resource_type"])
    assert {operation["id"] for operation in spec["resource_definition"]["operations"]} == {"list", "show"}
    assert "person_phone" in spec["data_definition"]["record_schema"]["properties"]
    assert compiled["source_map"]["resource:people"]
    def resource_conditions(sidecars):
        result = evaluate("Create and update work items in a list", compiled["webui"],
                          prototype_resources=sidecars, locale_dictionaries=compiled["locale_dictionaries"], domain_packs=[])
        return {item["id"]: item["ok"] for item in result["postconditions"] if item["id"] in {"resource.prototype_source", "resource.prototype_records"}}
    assert resource_conditions(compiled["prototype_resources"]) == {"resource.prototype_source": True, "resource.prototype_records": True}
    assert not resource_conditions(compiled["prototype_resources"][:1])["resource.prototype_source"]
    orphan = {**resource, "resource_type": "prototype.unused"}
    page["meta"]["unused_example"] = {"kind": "resourceQuery", "resourceType": "prototype.unused"}
    assert not resource_conditions([*compiled["prototype_resources"], orphan])["resource.prototype_source"]
    candidate["resources"][1]["records"][0]["values"][0] = 123
    with pytest.raises(BuilderWorkflowError, match="invalid short_text"):
        compile_semantic_prototype_candidate(candidate, brief=brief)


def test_explicit_relationship_labels_keep_all_declared_fields() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["relationships"][0]["label_field_refs"] = ["person_name", "person_phone"]
    next(view for view in semantic["views"] if view["role"] == "editor")["field_refs"].append("work_owner_id")
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    fields = [field for widget in developer_prototypes._surface_widgets(result["webui"]) if widget["type"] == "ui.form" for field in widget["inputs"]["fields"]]
    assert next(field for field in fields if field["id"] == "work_owner_id")["optionLabelPaths"] == ["person_name", "person_phone"]


def test_repeated_local_fields_are_scoped_without_rewriting_values_or_identity() -> None:
    brief, semantic = _multi_resource_fixture()
    work, people = semantic["resources"]
    for field_id in ("title", "status", "comment"):
        people["fields"].append(copy.deepcopy(next(field for field in work["fields"] if field["id"] == field_id)))
    people["fields"][-1]["visible_when"] = {"field_ref": "status", "operator": "equals", "value": "open"}
    for record in people["records"]:
        record.update(title="title", status="open", comment="status")
    people["read_only_when"] = {"field_ref": "status", "operator": "equals", "value": "complete"}
    semantic["views"][-1]["field_refs"].extend(["title", "status", "comment"])
    semantic["relationships"][0]["label_field_refs"] = ["title", "person_name"]
    next(view for view in semantic["views"] if view["role"] == "editor")["field_refs"].append("work_owner_id")
    candidate = _multi_resource_candidate(semantic)
    original = copy.deepcopy(candidate)
    result = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert candidate == original
    assert {item["to"] for item in result["normalizations"] if item["kind"] == "field_owner_namespace"} == {
        "work_items.title", "work_items.status", "work_items.comment", "people.title", "people.status", "people.comment",
    }
    document = result["semantic_document"]
    assert document["resources"][1]["read_only_when"]["field_ref"] == "people.status"
    assert document["resources"][1]["records"][0]["people.comment"] == "status"
    assert document["relationships"][0]["to_field_ref"] == "id"
    assert document["relationships"][0]["label_field_refs"] == ["people.title", "person_name"]
    assert "work_items.title" in document["views"][0]["field_refs"]
    assert "people.title" in document["views"][-1]["field_refs"]
    guard = next(command["guard"] for command in document["commands"] if command.get("guard"))
    assert guard["when"]["field_ref"] == "result"
    assert "work_items.comment" in guard["require_nonempty"]
    assert document["resources"][1]["fields"][-1]["visible_when"]["field_ref"] == "people.status"
    assert document["commands"][0]["fixed_values"] == {"work_items.status": "open"}


@pytest.mark.parametrize("owner_refs,expected", [
    ([{"kind": "resource", "id": "people"}], "people.title"),
    ([{"kind": "view", "id": "work-list"}], "work_items.title"),
    ([], None),
    ([{"kind": "resource", "id": "people"}, {"kind": "resource", "id": "work_items"}], None),
])
def test_ambiguous_binding_field_needs_one_explicit_owner(owner_refs, expected) -> None:
    from adaos.services.builder.semantic_prototype import _canonicalize_semantic_prototype_candidate_v2

    _, semantic = _multi_resource_fixture()
    semantic["resources"][1]["fields"].append(copy.deepcopy(semantic["resources"][0]["fields"][0]))
    candidate = _multi_resource_candidate(semantic)
    candidate["requirement_bindings"] = [{"requirement_ref": "collection:01", "semantic_refs": [*owner_refs, {"kind": "field", "id": "title"}]}]
    if expected is None:
        with pytest.raises(BuilderWorkflowError, match="ambiguous field reference"):
            _canonicalize_semantic_prototype_candidate_v2(candidate)
    else:
        document, _ = _canonicalize_semantic_prototype_candidate_v2(candidate)
        assert document["requirement_bindings"][0]["semantic_refs"][-1] == f"field:{expected}"


@pytest.mark.parametrize("resources,pattern", [
    ([{"id": "a", "fields": [{"id": "name"}, {"id": "name"}]}], "resource-local field"),
    ([{"id": "a", "fields": [{"id": "name"}, {"id": "a.name"}]}, {"id": "b", "fields": [{"id": "name"}]}], "qualified field"),
    ([{"id": "a", "fields": [{"id": "revision"}]}, {"id": "b", "fields": [{"id": "revision"}]}], "record metadata"),
])
def test_field_namespace_does_not_hide_identity_or_declaration_collisions(resources, pattern) -> None:
    from adaos.services.builder.semantic_prototype import _candidate_v2_field_namespaces
    with pytest.raises(BuilderWorkflowError, match=pattern):
        _candidate_v2_field_namespaces(resources)


def test_explicit_record_identity_survives_normalization_across_resources_and_predicates() -> None:
    brief, semantic = _multi_resource_fixture()
    for resource in semantic["resources"]:
        resource["fields"].insert(0, {"id": "id", "label": _text("record.id", "ID", "ID"),
                                      "value_type": "short_text", "editable": False, "required": False})
    target = semantic["resources"][1]
    for index, record in enumerate(target["records"], 1):
        record["id"] = f"Person {index}"
        semantic["resources"][0]["records"][index - 1]["work_owner_id"] = record["id"]
    target["read_only_when"] = {"field_ref": "id", "operator": "equals", "value": "Person 1"}
    view = next(view for view in semantic["views"] if view["resource_ref"] == target["id"])
    view["field_refs"].append("id")
    semantic["representative_states"].append({"id": "first-person", "label": _text("first", "First", "First"),
        "view_ref": view["id"], "filters": [{"field_ref": "id", "operator": "eq", "value": "Person 1"}],
        "min_items": 1, "max_items": 1, "proof": {"kind": "field_predicate", "visible_field_refs": ["id"]}})
    candidate = _multi_resource_candidate(semantic)
    original = copy.deepcopy(candidate)
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert candidate == original
    document = compiled["semantic_document"]
    assert [record["id"] for record in document["resources"][1]["records"]] == ["Person.1", "Person.2"]
    assert [record["work_owner_id"] for record in document["resources"][0]["records"]] == ["Person.1", "Person.2"]
    assert document["resources"][1]["read_only_when"]["value"] == "Person.1"
    assert document["representative_states"][-1]["filters"][0]["value"] == "Person.1"
    assert len([item for item in compiled["normalizations"] if item["kind"] == "record_identity_value"]) == 2
    assert compile_semantic_prototype(document)["prototype_resources"] == compiled["prototype_resources"]


@pytest.mark.parametrize("invalid", ["mismatch", "editable", "number"])
def test_explicit_identity_cannot_disagree_with_or_mutate_metadata(invalid) -> None:
    brief, semantic = _multi_resource_fixture()
    resource = semantic["resources"][0]
    resource["fields"].insert(0, {"id": "id", "label": _text("record.id", "ID", "ID"),
                                  "value_type": "number" if invalid == "number" else "short_text",
                                  "editable": invalid == "editable", "required": False})
    candidate = _multi_resource_candidate(semantic)
    if invalid == "mismatch":
        for record in candidate["resources"][0]["records"]:
            record["values"][0] = "different identity"
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    if invalid == "mismatch":
        assert len([item for item in caught.value.findings if item["code"] == "semantic.record_identity_mismatch"]) == len(resource["records"])


def test_fixture_arity_reports_every_resource_and_expected_field_order() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    for resource in candidate["resources"]:
        resource["records"][0]["values"].pop()
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    findings = [item for item in caught.value.findings if item["code"] == "semantic.record_arity"]
    assert len(findings) == len(candidate["resources"])
    for index, finding in enumerate(findings):
        assert finding["expected_field_refs"] == [field["id"] for field in candidate["resources"][index]["fields"]]


def test_editor_only_exposes_writable_inputs_and_preserves_fixed_readonly_context() -> None:
    brief, semantic = _multi_resource_fixture()
    next(field for field in semantic["resources"][0]["fields"] if field["id"] == "title")["editable"] = True
    editor = next(view for view in semantic["views"] if view["role"] == "editor")
    editor["field_refs"].extend(["title", "status", "work_owner_id"])
    for command in semantic["commands"]:
        if command["view_ref"] == editor["id"]:
            command["input_field_refs"] = ["title"]
            command["fixed_values"] = {"status": "open"}
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    form = next(widget for widget in compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]["widgets"] if widget["id"] == editor["id"])
    fields = {field["id"]: field for field in form["inputs"]["fields"]}
    assert not fields["title"].get("readOnly")
    assert fields["status"]["readOnly"] is True
    assert fields["status"]["defaultValue"] == "open"
    assert fields["status"]["required"] is False
    assert fields["work_owner_id"]["readOnly"] is True


def test_filter_binding_closes_over_one_explicit_collection_not_ambiguous_owners() -> None:
    from adaos.services.builder.semantic_bindings import close_bindings
    def view(identifier):
        return {"id": identifier, "resource_ref": "items", "role": "collection", "query_controls": [
            {"id": f"{identifier}-a", "kind": "filter"}, {"id": f"{identifier}-b", "kind": "filter"}]}
    brief = {"operations": [{"id": "operation:filter", "kind": "filter"}]}
    document = {"views": [view("main"), view("secondary")], "commands": [],
                "requirement_bindings": [{"requirement_ref": "operation:filter", "semantic_refs": ["view:main"]}]}
    close_bindings(document, brief)
    assert set(document["requirement_bindings"][0]["semantic_refs"]) == {"view:main", "resource:items", "query:main-a", "query:main-b"}
    document["requirement_bindings"][0]["semantic_refs"] = ["resource:items"]
    close_bindings(document, brief)
    assert document["requirement_bindings"][0]["semantic_refs"] == ["resource:items"]


def test_qualified_unique_fields_resolve_in_bindings_views_commands_and_relationships() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["relationships"][0]["label_field_refs"] = ["person_name"]
    candidate = _multi_resource_candidate(semantic)
    original = copy.deepcopy(candidate)
    by_view = {view["id"]: view["resource_ref"] for view in candidate["views"]}
    for view in candidate["views"]:
        view["field_refs"] = [f"{view['resource_ref']}.{ref}" for ref in view["field_refs"]]
    for command in candidate["commands"]:
        command["input_field_refs"] = [f"{by_view[command['view_ref']]}.{ref}" for ref in command["input_field_refs"]]
    relation = candidate["relationships"][0]
    relation["from_field_ref"] = f"{relation['from_resource_ref']}.{relation['from_field_ref']}"
    relation["label_field_refs"] = ["people.person_name"]
    for state in candidate["representative_states"]:
        state["proof"]["visible_field_refs"] = [f"{by_view[state['view_ref']]}.{ref}" for ref in state["proof"]["visible_field_refs"]]
    owners = {field["id"]: resource["id"] for resource in candidate["resources"] for field in resource["fields"]}
    for binding in candidate["requirement_bindings"]:
        for ref in binding["semantic_refs"]:
            if ref["kind"] == "field":
                ref["id"] = f"{owners[ref['id']]}.{ref['id']}"
    result = compile_semantic_prototype_candidate(candidate, brief=brief)
    expected = compile_semantic_prototype_candidate(original, brief=brief)
    assert result["semantic_document"] == expected["semantic_document"]
    assert any(item["kind"] == "candidate_reference" for item in result["normalizations"])


def test_preflight_reports_all_independent_missing_binding_fields() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    for index, binding in enumerate(candidate["requirement_bindings"][:2]):
        binding["semantic_refs"].append({"kind": "field", "id": f"absent_{index}"})
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    missing = [item for item in caught.value.findings if item["code"] == "semantic.binding_reference_missing"]
    assert len(missing) == 2


@pytest.mark.parametrize("field,visible", [("source", True), ("poster", True), ("kind", False), ("other", False)])
def test_state_proof_counts_rendered_details_media_not_hidden_dispatch_fields(field, visible) -> None:
    from adaos.services.builder.semantic_prototype import _state_proof_findings
    state = {"id": "s", "min_items": 1, "max_items": 1, "filters": [],
             "proof": {"kind": "field_predicate", "visible_field_refs": [field]}}
    view = {"id": "v", "role": "details", "field_refs": [],
            "media": {"source_field_ref": "source", "poster_field_ref": "poster", "kind_field_ref": "kind"}}
    findings = _state_proof_findings(state, view, index=0)
    assert any(item["code"] == "semantic.state_proof_hidden" for item in findings) is not visible


@pytest.mark.parametrize("owner_refs,expected", [
    ([], None),
    ([{"kind": "resource", "id": "people"}], "work_items.title"),
    ([{"kind": "resource", "id": "work_items"}], "title"),
])
def test_literal_dotted_id_does_not_silently_override_qualified_alias(owner_refs, expected) -> None:
    from adaos.services.builder.semantic_prototype import _canonicalize_semantic_prototype_candidate_v2
    _, semantic = _multi_resource_fixture()
    people = semantic["resources"][1]
    field = copy.deepcopy(semantic["resources"][0]["fields"][0])
    field["id"] = "work_items.title"
    people["fields"].append(field)
    for record in people["records"]:
        record[field["id"]] = "Untouched"
    candidate = _multi_resource_candidate(semantic)
    candidate["requirement_bindings"] = [{"requirement_ref": "collection:01", "semantic_refs": [*owner_refs, {"kind": "field", "id": "work_items.title"}]}]
    if expected is None:
        with pytest.raises(BuilderWorkflowError, match="ambiguous field reference"):
            _canonicalize_semantic_prototype_candidate_v2(candidate)
    else:
        document, _ = _canonicalize_semantic_prototype_candidate_v2(candidate)
        actual = document["requirement_bindings"][0]["semantic_refs"][-1]
        assert actual == f"field:{expected}"


def test_unreachable_resource_cannot_be_excused_as_lookup_only() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["views"] = [view for view in semantic["views"] if view["resource_ref"] != "people"]
    with pytest.raises(BuilderWorkflowError, match="has no inspectable view"):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)


def test_query_role_findings_are_reported_alongside_missing_collections() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["views"] = [view for view in semantic["views"] if view["resource_ref"] != "people"]
    detail = next(view for view in semantic["views"] if view["role"] == "details")
    detail["query_controls"] = [{"id": "find", "kind": "search", "field_ref": None, "label": _text("find", "Find", "Find")}]
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    assert {"semantic.query_view_role", "semantic.resource_view_missing"}.issubset({item["code"] for item in caught.value.findings})


def test_provider_grammar_limits_requirement_refs_to_the_active_inventory() -> None:
    brief, _ = _multi_resource_fixture()
    defs = semantic_prototype_provider_contract(version="v2", locales=("ru",), brief=brief)["$defs"]
    binding_refs = defs["requirementBinding"]["properties"]["requirement_ref"]["enum"]
    automation_refs = defs["automationRequirement"]["properties"]["requirement_ref"]["enum"]
    assert brief["operations"][0]["id"] in binding_refs
    assert brief["operations"][0]["id"] not in automation_refs
    assert brief["principal_jobs"][0]["id"] in automation_refs
    assert defs["localizedText"]["required"] == ["ru"]


def _fixture() -> tuple[dict, dict]:
    brief = compile_prototype_brief(
        "Show a repeatable list of work items, record a result for each item, "
        "upload an attachment, update the selected item and complete it."
    )
    semantic = {
        "schema": "adaos.webui.semantic.v1",
        "document_id": "work-review",
        "brief_ref": brief["brief_id"],
        "brief_digest": brief["digest"],
        "title": _text("work.title", "Work review", "Проверка работ"),
        "layout": {
            "pattern": "split",
        },
        "resource": {
            "id": "work_items",
            "identity_field_refs": ["id"],
            "item_semantics": "One record is one independently editable work item.",
            "item_label": _text("work.item", "Work item", "Пункт работы"),
            "fields": [
                {
                    "id": "title",
                    "label": _text("work.field.title", "Item", "Пункт"),
                    "value_type": "short_text",
                    "required": True,
                    "editable": False,
                },
                {
                    "id": "result",
                    "label": _text("work.field.result", "Result", "Результат"),
                    "value_type": "choice",
                    "required": True,
                    "editable": True,
                    "options": [
                        {
                            "value": "ok",
                            "label": _text("work.result.ok", "OK", "Исправно"),
                        },
                        {
                            "value": "issue",
                            "label": _text(
                                "work.result.issue", "Issue", "Есть замечание"
                            ),
                        },
                    ],
                },
                {
                    "id": "comment",
                    "label": _text("work.field.comment", "Comment", "Комментарий"),
                    "value_type": "long_text",
                    "required": True,
                    "editable": True,
                    "visible_when": {
                        "field_ref": "result",
                        "operator": "equals",
                        "value": "issue",
                    },
                },
                {
                    "id": "evidence",
                    "label": _text("work.field.evidence", "Evidence", "Подтверждение"),
                    "value_type": "attachment",
                    "required": False,
                    "editable": True,
                },
                {
                    "id": "status",
                    "label": _text("work.field.status", "Status", "Статус"),
                    "value_type": "short_text",
                    "required": True,
                    "editable": False,
                },
            ],
            "records": [
                {
                    "id": "work-1",
                    "title": "Pressure check",
                    "result": "issue",
                    "comment": "Outside tolerance",
                    "evidence": "fixture://pressure.jpg",
                    "status": "open",
                },
                {
                    "id": "work-2",
                    "title": "Guard check",
                    "result": "ok",
                    "comment": "",
                    "evidence": "",
                    "status": "complete",
                },
            ],
        },
        "views": [
            {
                "id": "work-list",
                "role": "collection",
                "region_role": "primary",
                "presentation": "list",
                "title": _text("work.list", "Items", "Пункты"),
                "field_refs": ["title", "result", "status"],
                "empty_state": {
                    "title": _text("work.empty", "No work items", "Нет пунктов")
                },
            },
            {
                "id": "work-details",
                "role": "details",
                "region_role": "supporting",
                "title": _text("work.details", "Selected item", "Выбранный пункт"),
                "field_refs": ["title", "result", "status"],
            },
            {
                "id": "work-editor",
                "role": "editor",
                "region_role": "supporting",
                "title": _text("work.editor", "Record result", "Заполнить результат"),
                "field_refs": ["result", "comment", "evidence"],
            },
        ],
        "commands": [
            {
                "id": "save",
                "kind": "update",
                "view_ref": "work-editor",
                "label": _text("work.save", "Save", "Сохранить"),
                "input_field_refs": ["result", "comment", "evidence"],
                "fixed_values": {"status": "open"},
            },
            {
                "id": "complete",
                "kind": "transition",
                "view_ref": "work-editor",
                "label": _text("work.complete", "Complete", "Завершить"),
                "input_field_refs": ["result", "comment", "evidence"],
                "fixed_values": {"status": "complete"},
                "guard": {
                    "when": {
                        "field_ref": "result",
                        "operator": "equals",
                        "value": "issue",
                    },
                    "require_nonempty": ["comment"],
                },
            },
        ],
        "representative_states": [
            {
                "id": "empty",
                "label": _text("work.state.empty", "Empty", "Пусто"),
                "view_ref": "work-list",
                "filters": [],
                "min_items": 0,
                "max_items": 0,
            }
        ],
        "requirement_bindings": [],
        "capability_gaps": [],
    }
    for requirement in brief["principal_jobs"]:
        semantic["requirement_bindings"].append(
            {
                "requirement_ref": requirement["id"],
                "semantic_refs": [
                    "resource:work_items",
                    "view:work-list",
                    "view:work-editor",
                ],
            }
        )
    for requirement in brief["collection_requirements"]:
        semantic["requirement_bindings"].append(
            {
                "requirement_ref": requirement["id"],
                "semantic_refs": [
                    "resource:work_items",
                    "view:work-list",
                    "view:work-editor",
                    "field:result",
                ],
            }
        )
    for requirement in brief["information_requirements"]:
        semantic["requirement_bindings"].append(
            {
                "requirement_ref": requirement["id"],
                "semantic_refs": ["field:evidence", "view:work-editor"],
            }
        )
    for operation in brief["operations"]:
        semantic_ref = (
            "view:work-list"
            if operation["kind"] in {"list", "inspect", "search", "filter", "sort"}
            else "command:complete"
            if operation["kind"] == "transition"
            else "command:save"
        )
        semantic["requirement_bindings"].append(
            {
                "requirement_ref": operation["id"],
                "semantic_refs": [semantic_ref],
            }
        )
    return brief, semantic


def _candidate(semantic: dict) -> dict:
    candidate = copy.deepcopy(semantic)
    candidate["schema"] = "adaos.builder.semantic_prototype_candidate.v1"
    candidate.pop("brief_ref")
    candidate.pop("brief_digest")
    candidate["layout"] = candidate["layout"]["pattern"]
    candidate["resource"].pop("identity_field_refs")
    field_ids = [field["id"] for field in candidate["resource"]["fields"]]
    for field in candidate["resource"]["fields"]:
        if field.get("value_type") == "attachment" and field.pop("multiple", False):
            field["value_type"] = "attachments"
        field.pop("max_items", None)
        field.setdefault("options", [])
        field.setdefault("visible_when", None)
    candidate["resource"]["records"] = [
        {
            "id": record["id"],
            "values": [record.get(field_id) for field_id in field_ids],
        }
        for record in candidate["resource"]["records"]
    ]
    for view in candidate["views"]:
        view.setdefault(
            "presentation", "list" if view["role"] == "collection" else None
        )
        view.setdefault("filter", None)
        view.setdefault("query_controls", [])
        view.setdefault("empty_state", None)
        if view["empty_state"] is not None:
            view["empty_state"].setdefault("detail", None)
        for control in view["query_controls"]:
            control.setdefault("field_ref", None)
    for command in candidate["commands"]:
        command.setdefault("confirmation", None)
        command["fixed_values"] = [
            {"field_ref": field_ref, "value": value}
            for field_ref, value in command.get("fixed_values", {}).items()
        ]
        command.setdefault("guard", None)
        if command["guard"] is not None:
            command["guard"]["when"].setdefault("value", None)
    for state in candidate["representative_states"]:
        state.setdefault("max_items", None)
        for predicate in state["filters"]:
            compare_field_ref = predicate.pop("compare_field_ref", None)
            value = predicate.pop("value", None)
            predicate["operand"] = {
                "kind": "field" if compare_field_ref is not None else "value",
                "value": value,
                "field_ref": compare_field_ref,
            }
    for binding in candidate["requirement_bindings"]:
        binding["semantic_refs"] = [
            {"kind": kind, "id": identifier}
            for semantic_ref in binding["semantic_refs"]
            for kind, identifier in [semantic_ref.split(":", 1)]
        ]

    def strip_localization_keys(value: object) -> None:
        if isinstance(value, dict):
            if {"key", "en", "ru"}.issubset(value):
                value.pop("key")
            for child in value.values():
                strip_localization_keys(child)
        elif isinstance(value, list):
            for child in value:
                strip_localization_keys(child)

    strip_localization_keys(candidate)
    return candidate


def _multi_resource_fixture() -> tuple[dict, dict]:
    brief, work = _fixture()
    work_resource = copy.deepcopy(work.pop("resource"))
    work_resource["fields"].append(
        {
            "id": "work_owner_id",
            "label": _text("work.field.owner", "Owner", "Ответственный"),
            "value_type": "short_text",
            "required": True,
            "editable": True,
        }
    )
    work_resource["records"][0]["work_owner_id"] = "person-1"
    work_resource["records"][1]["work_owner_id"] = "person-2"
    work["schema"] = "adaos.webui.semantic.v2"
    work["resources"] = [
        work_resource,
        {
            "id": "people",
            "identity_field_refs": ["id"],
            "item_semantics": "One record is one independently inspectable person.",
            "item_label": _text("people.item", "Person", "Человек"),
            "fields": [
                {
                    "id": "person_name",
                    "label": _text("people.name", "Name", "Имя"),
                    "value_type": "short_text",
                    "required": True,
                    "editable": False,
                },
                {
                    "id": "person_phone",
                    "label": _text("people.phone", "Phone", "Телефон"),
                    "value_type": "short_text",
                    "required": False,
                    "editable": False,
                },
            ],
            "records": [
                {"id": "person-1", "person_name": "Alex", "person_phone": "+1"},
                {"id": "person-2", "person_name": "Sam", "person_phone": "+2"},
            ],
        },
    ]
    work["relationships"] = [
        {
            "id": "work_owner",
            "from_resource_ref": "work_items",
            "from_field_ref": "work_owner_id",
            "to_resource_ref": "people",
            "to_field_ref": "id",
            "cardinality": "many_to_one",
        }
    ]
    for view in work["views"]:
        view["resource_ref"] = "work_items"
    work["views"].append(
        {
            "id": "people-list",
            "resource_ref": "people",
            "role": "collection",
            "region_role": "supporting",
            "presentation": "table",
            "title": _text("people.list", "People", "Люди"),
            "field_refs": ["person_name", "person_phone"],
        }
    )
    work["representative_states"][0]["proof"] = {
        "kind": "collection_empty",
        "visible_field_refs": [],
    }
    return brief, work


def _multi_resource_candidate(semantic: dict) -> dict:
    candidate = copy.deepcopy(semantic)
    candidate["schema"] = "adaos.builder.semantic_prototype_candidate.v2"
    candidate.pop("brief_ref")
    candidate.pop("brief_digest")
    candidate["layout"] = candidate["layout"]["pattern"]
    for resource in candidate["resources"]:
        resource.pop("identity_field_refs")
        field_ids = [field["id"] for field in resource["fields"]]
        for field in resource["fields"]:
            field.setdefault("options", [])
            field.setdefault("visible_when", None)
        resource["records"] = [
            {
                "id": record["id"],
                "values": [record.get(field_id) for field_id in field_ids],
            }
            for record in resource["records"]
        ]
    for view in candidate["views"]:
        view.setdefault("presentation", None)
        view.setdefault("filter", None)
        view.setdefault("query_controls", [])
        view.setdefault("empty_state", None)
        if view["empty_state"] is not None:
            view["empty_state"].setdefault("detail", None)
    for command in candidate["commands"]:
        command.setdefault("confirmation", None)
        command["fixed_values"] = [
            {"field_ref": field_ref, "value": value}
            for field_ref, value in command.get("fixed_values", {}).items()
        ]
        command.setdefault("guard", None)
        if command["guard"] is not None:
            command["guard"]["when"].setdefault("value", None)
    for state in candidate["representative_states"]:
        state.setdefault("max_items", None)
        for predicate in state["filters"]:
            compare_field_ref = predicate.pop("compare_field_ref", None)
            value = predicate.pop("value", None)
            predicate["operand"] = {
                "kind": "field" if compare_field_ref is not None else "value",
                "value": value,
                "field_ref": compare_field_ref,
            }
    for binding in candidate["requirement_bindings"]:
        binding["semantic_refs"] = [
            {"kind": kind, "id": identifier}
            for semantic_ref in binding["semantic_refs"]
            for kind, identifier in [semantic_ref.split(":", 1)]
        ]

    def strip_localization_keys(value: object) -> None:
        if isinstance(value, dict):
            if {"key", "en", "ru"}.issubset(value):
                value.pop("key")
            for child in value.values():
                strip_localization_keys(child)
        elif isinstance(value, list):
            for child in value:
                strip_localization_keys(child)

    strip_localization_keys(candidate)
    return candidate


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_provider_references_have_no_sibling_keywords(version) -> None:
    def visit(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert set(node) == {"$ref"}
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(semantic_prototype_provider_contract(version=version))
    if version == "v2":
        assert semantic_prototype_generation_guidance()["relationships"]


def test_semantic_model_contract_is_strict_and_bounded() -> None:
    contract = semantic_prototype_candidate_contract()

    def assert_strict(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node.get("additionalProperties") is False
                assert set(node.get("required") or []) == set(node["properties"])
            for child in node.values():
                assert_strict(child)
        elif isinstance(node, list):
            for child in node:
                assert_strict(child)

    assert_strict(semantic_prototype_provider_contract())
    assert contract["properties"]["resource"]["properties"]["records"][
        "maxItems"
    ] == 12

    provider_contract = semantic_prototype_provider_contract()
    unsupported_provider_keywords = {
        "allOf",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "uniqueItems",
    }

    schema_map_keys = {"$defs", "definitions", "properties"}
    schema_list_keys = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_value_keys = {
        "additionalProperties",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
    }

    def keywords(node: object) -> set[str]:
        if isinstance(node, dict):
            result = set(node)
            for key, child in node.items():
                if key in schema_map_keys and isinstance(child, dict):
                    result.update(
                        *(keywords(property_schema) for property_schema in child.values())
                    )
                elif key in schema_list_keys and isinstance(child, list):
                    result.update(*(keywords(branch) for branch in child))
                elif key in schema_value_keys and isinstance(child, dict):
                    result.update(keywords(child))
            return result
        return set()

    assert not unsupported_provider_keywords.intersection(keywords(provider_contract))
    assert provider_contract["properties"]["layout"] == {
        "type": "string",
        "enum": ["flow", "split", "grid", "focus_detail"],
    }
    assert "identity_field_refs" not in contract["properties"]["resource"][
        "properties"
    ]
    assert "brief_ref" not in contract["properties"]
    assert "brief_digest" not in contract["properties"]
    assert set(contract["$defs"]["localizedText"]["properties"]) == {"en", "ru"}
    assert set(contract["$defs"]["record"]["properties"]) == {"id", "values"}
    assert contract["$defs"]["id"]["pattern"]


@pytest.mark.parametrize(
    ("role", "presentation", "message"),
    [
        ("collection", None, "collection view 'work-list' requires a presentation"),
        ("details", "list", "details view 'work-details' presentation must be null"),
    ],
)
def test_semantic_candidate_presentation_matches_view_role(
    role: str,
    presentation: str | None,
    message: str,
) -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)
    view = next(item for item in candidate["views"] if item["role"] == role)
    view["presentation"] = presentation

    with pytest.raises(BuilderWorkflowError, match=re.escape(message)):
        compile_semantic_prototype_candidate(candidate, brief=brief)


def test_semantic_model_candidate_compiles_to_canonical_document() -> None:
    brief, semantic = _fixture()

    result = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)

    assert result["schema"] == "adaos.builder.semantic_compile_result.v1"
    assert result["prototype_records"][0]["id"] == "work-1"
    assert result["semantic_document"]["brief_ref"] == brief["brief_id"]
    assert result["semantic_document"]["brief_digest"] == brief["digest"]
    assert any(
        item["kind"] == "authoritative_brief_provenance"
        for item in result["normalizations"]
    )


def test_semantic_candidate_normalizes_unambiguous_localized_choice_value() -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)
    result_index = next(
        index
        for index, field in enumerate(candidate["resource"]["fields"])
        if field["id"] == "result"
    )
    candidate["resource"]["records"][0]["values"][result_index] = "Issue"

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    assert result["prototype_records"][0]["result"] == "issue"
    assert any(
        item["kind"] == "localized_choice_value"
        for item in result["normalizations"]
    )


def test_semantic_model_candidate_reports_all_requirement_contract_findings() -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)
    overlap_ref = candidate["requirement_bindings"][0]["requirement_ref"]
    candidate["capability_gaps"].append(
        {
            "requirement_ref": overlap_ref,
            "code": "constraint.unsupported",
            "detail": "The requirement cannot be enforced.",
        }
    )
    candidate["requirement_bindings"].append(
        {
            "requirement_ref": "q_search_bind",
            "semantic_refs": copy.deepcopy(
                candidate["requirement_bindings"][0]["semantic_refs"]
            ),
        }
    )

    with pytest.raises(BuilderWorkflowError) as captured:
        compile_semantic_prototype_candidate(candidate, brief=brief)

    findings = captured.value.findings
    assert [item["code"] for item in findings] == [
        "requirement.binding_and_gap",
        "requirement.reference_unknown",
    ]
    assert findings[0]["requirement_refs"] == [overlap_ref]
    assert findings[1]["requirement_refs"] == ["q_search_bind"]


def test_semantic_table_presentation_compiles_all_declared_columns() -> None:
    brief, semantic = _fixture()
    collection = semantic["views"][0]
    collection["presentation"] = "table"

    result = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)

    table = result["webui"]["ui"]["application"]["desktop"]["pageSchema"][
        "widgets"
    ][0]
    assert table["type"] == "ui.table"
    assert [column["key"] for column in table["inputs"]["columns"]] == collection[
        "field_refs"
    ]
    assert table["inputs"]["emptyText"] == "No work items"


def test_semantic_list_presentation_exposes_all_declared_fields() -> None:
    brief, semantic = _fixture()

    result = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)

    collection = result["webui"]["ui"]["application"]["desktop"]["pageSchema"][
        "widgets"
    ][0]
    assert collection["type"] == "ui.list"
    assert collection["inputs"]["titleKey"] == "title"
    assert [entry["key"] for entry in collection["inputs"]["meta"]] == [
        "result",
        "status",
    ]


def test_semantic_model_candidate_merges_duplicate_requirement_bindings() -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)
    binding = copy.deepcopy(candidate["requirement_bindings"][0])
    binding["semantic_refs"] = [
        {"kind": "view", "id": candidate["views"][1]["id"]}
    ]
    candidate["requirement_bindings"].append(binding)

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    matching = [
        item
        for item in result["semantic_document"]["requirement_bindings"]
        if item["requirement_ref"] == binding["requirement_ref"]
    ]
    assert len(matching) == 1
    assert f"view:{candidate['views'][1]['id']}" in matching[0]["semantic_refs"]
    assert any(
        item["kind"] == "duplicate_requirement_binding"
        for item in result["normalizations"]
    )


def test_semantic_model_candidate_canonicalizes_identifiers_and_references() -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)

    field_ids = {
        field["id"]: f"f:{field['id']}" for field in candidate["resource"]["fields"]
    }
    view_ids = {view["id"]: f"view:{view['id']}" for view in candidate["views"]}
    command_ids = {
        command["id"]: f"cmd:{command['id']}" for command in candidate["commands"]
    }
    state_ids = {
        state["id"]: f"state:{state['id']}"
        for state in candidate["representative_states"]
    }
    resource_id = candidate["resource"]["id"]
    candidate["document_id"] = f"proto:{candidate['document_id']}"
    candidate["resource"]["id"] = f"res:{resource_id}"
    for field in candidate["resource"]["fields"]:
        original = field["id"]
        field["id"] = field_ids[original]
        condition = field.get("visible_when")
        if condition is not None:
            condition["field_ref"] = field_ids[condition["field_ref"]]
    for view in candidate["views"]:
        original = view["id"]
        view["id"] = view_ids[original]
        view["field_refs"] = [field_ids[item] for item in view["field_refs"]]
        for control in view["query_controls"]:
            control["id"] = f"query:{control['id']}"
            if control["field_ref"] is not None:
                control["field_ref"] = field_ids[control["field_ref"]]
    for command in candidate["commands"]:
        original = command["id"]
        command["id"] = command_ids[original]
        command["view_ref"] = view_ids[command["view_ref"]]
        command["input_field_refs"] = [
            field_ids[item] for item in command["input_field_refs"]
        ]
        for entry in command["fixed_values"]:
            entry["field_ref"] = field_ids[entry["field_ref"]]
        if command["guard"] is not None:
            command["guard"]["when"]["field_ref"] = field_ids[
                command["guard"]["when"]["field_ref"]
            ]
            command["guard"]["require_nonempty"] = [
                field_ids[item] for item in command["guard"]["require_nonempty"]
            ]
    for state in candidate["representative_states"]:
        original = state["id"]
        state["id"] = state_ids[original]
        state["view_ref"] = view_ids[state["view_ref"]]
        for predicate in state["filters"]:
            predicate["field_ref"] = field_ids[predicate["field_ref"]]
            operand = predicate["operand"]
            if operand["kind"] == "field":
                operand["field_ref"] = field_ids[operand["field_ref"]]
    for binding in candidate["requirement_bindings"]:
        normalized_refs: list[dict[str, str]] = []
        for semantic_ref in binding["semantic_refs"]:
            kind = semantic_ref["kind"]
            identifier = semantic_ref["id"]
            if kind == "resource":
                normalized_refs.append({"kind": kind, "id": f"res:{identifier}"})
            elif kind == "field":
                normalized_refs.append({"kind": kind, "id": field_ids[identifier]})
            elif kind == "view":
                normalized_refs.append({"kind": kind, "id": view_ids[identifier]})
            elif kind == "command":
                normalized_refs.append({"kind": kind, "id": command_ids[identifier]})
            elif kind == "state":
                normalized_refs.append({"kind": kind, "id": state_ids[identifier]})
            else:
                normalized_refs.append(semantic_ref)
        binding["semantic_refs"] = normalized_refs
    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    document = result["semantic_document"]
    assert document["document_id"] == "proto.work-review"
    assert document["resource"]["id"] == "res.work_items"
    assert document["resource"]["fields"][0]["id"] == "f.title"
    assert document["views"][0]["id"] == "view.work-list"
    assert document["commands"][0]["id"] == "cmd.save"
    assert document["representative_states"][0]["id"] == "state.empty"
    assert document["title"]["key"] == "prototype.proto.work-review.title"
    assert any(
        item["kind"] == "candidate_semantic_reference"
        for item in result["normalizations"]
    )


def test_semantic_model_candidate_rejects_identifier_normalization_collision() -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)
    candidate["resource"]["fields"][0]["id"] = "f:title"
    candidate["resource"]["fields"][1]["id"] = "f.title"

    with pytest.raises(BuilderWorkflowError, match="normalize to the same id"):
        compile_semantic_prototype_candidate(candidate, brief=brief)


def test_semantic_model_candidate_normalizes_runtime_state_reference() -> None:
    brief, semantic = _fixture()
    semantic["views"][0]["filter"] = {
        "field_ref": "status",
        "state_ref": "state:status.filter",
    }

    result = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)

    document_filter = result["semantic_document"]["views"][0]["filter"]
    assert document_filter["state_ref"] == "state_status_filter"
    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    assert page["initialState"]["state_status_filter"] == ""


def test_semantic_model_candidate_compiles_tagged_field_operand() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["fields"].extend(
        [
            {
                "id": "actual",
                "label": _text("work.actual", "Actual", "Факт"),
                "value_type": "number",
                "required": False,
                "editable": False,
            },
            {
                "id": "threshold",
                "label": _text("work.threshold", "Threshold", "Порог"),
                "value_type": "number",
                "required": False,
                "editable": False,
            },
        ]
    )
    semantic["resource"]["records"][0].update({"actual": 2, "threshold": 3})
    semantic["resource"]["records"][1].update({"actual": 4, "threshold": 3})
    semantic["representative_states"][0] = {
        "id": "below-threshold",
        "label": _text("work.below", "Below", "Ниже"),
        "view_ref": "work-list",
        "filters": [
            {
                "field_ref": "actual",
                "operator": "lt",
                "compare_field_ref": "threshold",
            }
        ],
        "min_items": 1,
        "max_items": 1,
    }

    result = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)

    assert result["representative_state_checks"][0]["matching_record_ids"] == [
        "work-1"
    ]


def test_semantic_model_candidate_maps_attachment_cardinality_from_type() -> None:
    brief, semantic = _fixture()
    evidence = next(
        field for field in semantic["resource"]["fields"] if field["id"] == "evidence"
    )
    evidence["multiple"] = True
    semantic["resource"]["records"][0]["evidence"] = [
        "fixture://pressure.jpg",
        "fixture://gauge.jpg",
    ]
    semantic["resource"]["records"][1]["evidence"] = []

    candidate = _candidate(semantic)
    candidate_evidence = next(
        field for field in candidate["resource"]["fields"] if field["id"] == "evidence"
    )
    assert candidate_evidence["value_type"] == "attachments"

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    semantic_evidence = next(
        field
        for field in result["semantic_document"]["resource"]["fields"]
        if field["id"] == "evidence"
    )
    assert semantic_evidence["value_type"] == "attachment"
    assert semantic_evidence["multiple"] is True


def test_semantic_multi_choice_compiles_to_existing_form_control() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["fields"].append(
        {
            "id": "skills",
            "label": _text("work.field.skills", "Skills", "Навыки"),
            "value_type": "multi_choice",
            "required": False,
            "editable": True,
            "options": [
                {"value": "review", "label": _text("skill.review", "Review", "Ревью")},
                {"value": "repair", "label": _text("skill.repair", "Repair", "Ремонт")},
            ],
        }
    )
    semantic["resource"]["records"][0]["skills"] = ["review", "repair"]
    semantic["resource"]["records"][1]["skills"] = ["review"]
    semantic["views"][2]["field_refs"].append("skills")

    result = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)

    editor = next(
        widget
        for widget in result["webui"]["ui"]["application"]["desktop"][
            "pageSchema"
        ]["widgets"]
        if widget["id"] == "work-editor"
    )
    field = next(item for item in editor["inputs"]["fields"] if item["id"] == "skills")
    assert field["type"] == "multiChoice"
    assert result["prototype_records"][0]["skills"] == ["review", "repair"]


def test_semantic_multi_choice_rejects_unknown_or_duplicate_options() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["fields"].append(
        {
            "id": "skills",
            "label": _text("work.field.skills", "Skills", "Навыки"),
            "value_type": "multi_choice",
            "required": False,
            "editable": True,
            "options": [
                {"value": "review", "label": _text("skill.review", "Review", "Ревью")},
                {"value": "repair", "label": _text("skill.repair", "Repair", "Ремонт")},
            ],
        }
    )
    semantic["resource"]["records"][0]["skills"] = ["review", "unknown"]
    semantic["resource"]["records"][1]["skills"] = ["review", "review"]

    with pytest.raises(BuilderWorkflowError, match="invalid multi_choice value"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_model_candidate_rejects_record_value_cardinality_mismatch() -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)
    candidate["resource"]["records"][0]["values"].append("Duplicate")

    with pytest.raises(BuilderWorkflowError, match="6 values for 5 fields"):
        compile_semantic_prototype_candidate(candidate, brief=brief)


def test_semantic_model_candidate_drops_guard_without_required_fields() -> None:
    brief, semantic = _fixture()
    candidate = _candidate(semantic)
    candidate["commands"][1]["guard"]["require_nonempty"] = []

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    assert "guard" not in result["semantic_document"]["commands"][1]


def test_semantic_model_candidate_derives_stable_localization_keys() -> None:
    brief, semantic = _fixture()

    first = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)
    second = compile_semantic_prototype_candidate(_candidate(semantic), brief=brief)

    assert first["locale_dictionaries"] == second["locale_dictionaries"]
    assert "field.result.option.issue" in first["locale_dictionaries"]["en"]


def test_option_localization_keys_preserve_distinct_non_ascii_values() -> None:
    from adaos.services.builder.semantic_prototype import _materialize_candidate_localization_keys

    _, semantic = _fixture()
    candidate = _candidate(semantic)
    field = candidate["resource"]["fields"][0]
    field["options"] = [{"value": value, "label": {"ru": value}} for value in ("Видео", "Изображение", "A B", "A-B")]
    _materialize_candidate_localization_keys(candidate)
    keys = [option["label"]["key"] for option in field["options"]]
    assert len(set(keys)) == 4
    assert [option["value"] for option in field["options"]] == ["Видео", "Изображение", "A B", "A-B"]


def test_semantic_prototype_compiles_to_valid_webui_with_source_maps() -> None:
    brief, semantic = _fixture()

    result = compile_semantic_prototype(semantic, brief=brief)

    assert result["schema"] == "adaos.builder.semantic_compile_result.v1"
    assert result["validation"]["ok"] is True
    assert result["prototype_records"] == semantic["resource"]["records"]
    assert (
        result["locale_dictionaries"]["en"].keys()
        == result["locale_dictionaries"]["ru"].keys()
    )
    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    assert [widget["type"] for widget in page["widgets"]] == [
        "ui.list",
        "item.details",
        "ui.form",
    ]
    editor = page["widgets"][2]
    assert [field["type"] for field in editor["inputs"]["fields"]] == [
        "singleChoice",
        "longText",
        "fileUpload",
    ]
    assert editor["actions"][1]["params"]["operation_id"] == "update"
    assert "comment.length > 0" in editor["actions"][1]["enabledIf"]
    assert "confirmation" not in editor["actions"][0]
    assert editor["actions"][1]["confirmation"] == {
        "message": "Confirm Complete?",
        "message_i18n": {
            "key": "work.complete.confirmation",
            "fallback": "Confirm Complete?",
        },
        "confirmLabel": "Complete",
        "confirmLabel_i18n": {
            "key": "work.complete",
            "fallback": "Complete",
        },
    }
    assert result["requirement_runtime_map"]["collection:01"]
    assert result["representative_state_checks"] == [
        {
            "state_id": "empty",
            "view_ref": "work-list",
            "filters": [],
            "matching_record_ids": [],
            "matching_record_count": 0,
            "min_items": 0,
            "max_items": 0,
            "fixture_mode": "empty",
            "ok": True,
        }
    ]


def _add_query_controls(semantic: dict) -> None:
    semantic["views"][0]["query_controls"] = [
        {
            "id": "item-search",
            "kind": "search",
            "label": _text("work.search", "Search", "Поиск"),
        },
        {
            "id": "result-filter",
            "kind": "filter",
            "label": _text("work.filter.result", "Result", "Результат"),
            "field_ref": "result",
        },
    ]


def test_semantic_query_controls_compile_to_typed_runtime_wiring() -> None:
    brief, semantic = _fixture()
    _add_query_controls(semantic)

    result = compile_semantic_prototype(semantic, brief=brief)

    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    assert [widget["type"] for widget in page["widgets"][:3]] == [
        "input.text",
        "input.selector",
        "ui.list",
    ]
    collection = page["widgets"][2]
    assert collection["dataSource"]["query"] == {
        "search": "$state.query_item_search",
        "filters": {"result": "$state.query_result_filter"},
    }
    assert page["initialState"]["query_item_search"] == ""
    assert page["initialState"]["query_result_filter"] == ""
    assert result["source_map"]["query:item-search"] == [
        "ui.application.desktop.pageSchema.widgets.@query-item-search"
    ]


def test_search_and_filter_require_matching_query_bindings() -> None:
    brief, semantic = _fixture()
    brief = compile_prototype_brief(
        "Show a repeatable list of work items, search items by title, filter "
        "items by result, and update the selected item."
    )
    semantic["brief_ref"] = brief["brief_id"]
    semantic["brief_digest"] = brief["digest"]
    _add_query_controls(semantic)
    semantic["requirement_bindings"] = [
        {
            "requirement_ref": brief["collection_requirements"][0]["id"],
            "semantic_refs": [
                "resource:work_items",
                "view:work-list",
                "view:work-editor",
            ],
        },
    ]
    semantic["requirement_bindings"].extend(
        {
            "requirement_ref": job["id"],
            "semantic_refs": ["resource:work_items", "view:work-list"],
        }
        for job in brief["principal_jobs"]
    )
    operation_refs = {
        "search": "query:item-search",
        "filter": "query:result-filter",
        "list": "view:work-list",
        "update": "command:save",
    }
    semantic["requirement_bindings"].extend(
        {
            "requirement_ref": operation["id"],
            "semantic_refs": [operation_refs[operation["kind"]]],
        }
        for operation in brief["operations"]
    )

    result = compile_semantic_prototype(semantic, brief=brief)

    assert result["requirement_runtime_map"]["operation:search"] == [
        "ui.application.desktop.pageSchema.widgets.@query-item-search"
    ]
    search_binding = next(
        item
        for item in semantic["requirement_bindings"]
        if item["requirement_ref"] == "operation:search"
    )
    search_binding["semantic_refs"] = ["view:work-list"]
    with pytest.raises(BuilderWorkflowError, match="must bind a search query control"):
        validate_semantic_prototype(semantic, brief=brief)


def test_query_controls_reject_duplicate_and_non_collection_ownership() -> None:
    brief, semantic = _fixture()
    _add_query_controls(semantic)
    semantic["views"][1]["query_controls"] = [
        copy.deepcopy(semantic["views"][0]["query_controls"][0])
    ]
    with pytest.raises(BuilderWorkflowError, match="duplicate query control id"):
        validate_semantic_prototype(semantic, brief=brief)

    del semantic["views"][1]["query_controls"]
    semantic["views"][1]["query_controls"] = [
        semantic["views"][0]["query_controls"].pop(0)
    ]
    with pytest.raises(BuilderWorkflowError, match="must belong to a collection view"):
        validate_semantic_prototype(semantic, brief=brief)


def test_filter_query_control_requires_supported_field_type() -> None:
    brief, semantic = _fixture()
    _add_query_controls(semantic)
    semantic["resource"]["fields"].append(
        {
            "id": "effort",
            "label": _text("work.field.effort", "Effort", "Трудоемкость"),
            "value_type": "long_text",
            "required": False,
            "editable": True,
        }
    )
    semantic["views"][0]["query_controls"][1]["field_ref"] = "effort"

    with pytest.raises(
        BuilderWorkflowError,
        match="requires a boolean, choice, date, number, or short_text field",
    ):
        validate_semantic_prototype(semantic, brief=brief)


def test_boolean_filter_compiles_to_typed_tristate_selector() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["fields"].append(
        {
            "id": "requires_attention",
            "label": _text(
                "work.field.requires_attention",
                "Requires attention",
                "Требует внимания",
            ),
            "value_type": "boolean",
            "required": True,
            "editable": True,
        }
    )
    semantic["resource"]["records"][0]["requires_attention"] = True
    semantic["resource"]["records"][1]["requires_attention"] = False
    semantic["views"][0]["query_controls"] = [
        {
            "id": "attention-filter",
            "kind": "filter",
            "label": _text(
                "work.filter.requires_attention",
                "Requires attention",
                "Требует внимания",
            ),
            "field_ref": "requires_attention",
        }
    ]

    result = compile_semantic_prototype(semantic, brief=brief)

    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    control = page["widgets"][0]
    assert control["type"] == "input.selector"
    assert [item["value"] for item in control["inputs"]["options"]] == [
        "",
        True,
        False,
    ]
    assert page["widgets"][1]["dataSource"]["query"]["filters"] == {
        "requires_attention": "$state.query_attention_filter"
    }


def test_short_text_filter_compiles_to_native_text_input() -> None:
    brief, semantic = _fixture()
    semantic["views"][0]["query_controls"] = [
        {
            "id": "owner-filter",
            "kind": "filter",
            "label": _text("work.filter.owner", "Owner", "Ответственный"),
            "field_ref": "title",
        }
    ]

    result = compile_semantic_prototype(semantic, brief=brief)

    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    control = page["widgets"][0]
    assert control["type"] == "input.text"
    assert control["inputs"]["inputType"] == "text"
    assert page["widgets"][1]["dataSource"]["query"]["filters"] == {
        "title": "$state.query_owner_filter"
    }


def test_date_filter_compiles_to_native_date_input() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["fields"].append(
        {
            "id": "scheduled_on",
            "label": _text("work.field.date", "Date", "Дата"),
            "value_type": "date",
            "required": True,
            "editable": False,
        }
    )
    semantic["resource"]["records"][0]["scheduled_on"] = "2026-09-10"
    semantic["resource"]["records"][1]["scheduled_on"] = "2026-09-11"
    semantic["views"][0]["query_controls"] = [
        {
            "id": "date-filter",
            "kind": "filter",
            "label": _text("work.filter.date", "Date", "Дата"),
            "field_ref": "scheduled_on",
        }
    ]

    result = compile_semantic_prototype(semantic, brief=brief)

    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    control = page["widgets"][0]
    assert control["type"] == "input.text"
    assert control["inputs"]["inputType"] == "date"
    assert page["widgets"][1]["dataSource"]["query"]["filters"] == {
        "scheduled_on": "$state.query_date_filter"
    }


def test_semantic_prototype_rejects_unbound_accepted_requirement() -> None:
    brief, semantic = _fixture()
    semantic["requirement_bindings"] = semantic["requirement_bindings"][1:]

    with pytest.raises(BuilderWorkflowError, match="no semantic binding or gap"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_prototype_requires_residual_requirement_binding() -> None:
    brief, semantic = _fixture()
    brief["residual_requirements"] = [
        {
            "id": "residual:01",
            "statement": "compare this period with the previous period",
            "evidence": ["intent.statement#char=0:44"],
            "confidence": 1.0,
        }
    ]

    with pytest.raises(BuilderWorkflowError, match="residual:01"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_prototype_accepts_stable_representative_state_requirements() -> None:
    brief, semantic = _fixture()
    brief = compile_prototype_brief(
        "Move work through New, In progress, Blocked and Done."
    )
    semantic["brief_ref"] = brief["brief_id"]
    semantic["brief_digest"] = brief["digest"]
    semantic["requirement_bindings"] = [
        {
            "requirement_ref": item["id"],
            "semantic_refs": ["field:status"],
        }
        for item in prototype_sdk.model_context(brief)["state_requirements"]
        if "id" in item
    ]
    semantic["requirement_bindings"].extend(
        {
            "requirement_ref": item["id"],
            "semantic_refs": ["command:complete"],
        }
        for item in brief["principal_jobs"] + brief["operations"]
    )

    assert validate_semantic_prototype(semantic, brief=brief)["document_id"] == (
        "work-review"
    )


def test_capture_each_requires_collection_and_editor_bindings() -> None:
    brief, semantic = _fixture()
    binding = next(
        item
        for item in semantic["requirement_bindings"]
        if item["requirement_ref"] == "collection:01"
    )
    binding["semantic_refs"] = ["resource:work_items", "view:work-list"]

    with pytest.raises(BuilderWorkflowError, match="must bind an editor view"):
        validate_semantic_prototype(semantic, brief=brief)


def test_capture_each_expands_bound_editor_to_editable_item_fields() -> None:
    brief, semantic = _fixture()
    binding = next(
        item
        for item in semantic["requirement_bindings"]
        if item["requirement_ref"] == "collection:01"
    )
    binding["semantic_refs"] = [
        "resource:work_items",
        "view:work-list",
        "view:work-editor",
    ]

    result = compile_semantic_prototype(semantic, brief=brief)

    assert "field:result" in result["binding_expansions"]["collection:01"]
    assert any(
        ".inputs.fields.@result" in runtime_ref
        for runtime_ref in result["requirement_runtime_map"]["collection:01"]
    )


def test_semantic_prototype_rejects_dangling_refs_before_compilation() -> None:
    brief, semantic = _fixture()
    invalid = copy.deepcopy(semantic)
    invalid["views"][0]["field_refs"].append("missing")

    with pytest.raises(BuilderWorkflowError, match="unknown fields"):
        validate_semantic_prototype(invalid, brief=brief)


def test_semantic_command_has_one_editor_owner() -> None:
    brief, semantic = _fixture()
    semantic["commands"][0]["view_ref"] = "work-list"

    with pytest.raises(BuilderWorkflowError, match="owned by an editor view"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_command_compiles_explicit_localized_confirmation() -> None:
    brief, semantic = _fixture()
    semantic["commands"][0]["confirmation"] = _text(
        "work.save.confirmation",
        "Save these changes?",
        "Сохранить эти изменения?",
    )

    result = compile_semantic_prototype(semantic, brief=brief)
    action = result["webui"]["ui"]["application"]["desktop"]["pageSchema"][
        "widgets"
    ][2]["actions"][0]

    assert action["confirmation"] == {
        "message": "Save these changes?",
        "message_i18n": {
            "key": "work.save.confirmation",
            "fallback": "Save these changes?",
        },
        "confirmLabel": "Save",
        "confirmLabel_i18n": {"key": "work.save", "fallback": "Save"},
    }


def test_semantic_prototype_rejects_invalid_representative_record() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["records"][0]["result"] = "not-an-option"

    with pytest.raises(BuilderWorkflowError, match="invalid choice value"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_prototype_rejects_unproven_representative_state() -> None:
    brief, semantic = _fixture()
    semantic["representative_states"][0].update(
        {
            "filters": [
                {"field_ref": "status", "operator": "eq", "value": "closed"}
            ],
            "min_items": 1,
            "max_items": 1,
        }
    )

    with pytest.raises(BuilderWorkflowError, match="expected 1..1.*found 0"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_prototype_rejects_unbounded_zero_minimum_for_filtered_state() -> None:
    brief, semantic = _fixture()
    semantic["representative_states"][0] = {
        "id": "open",
        "label": _text("work.state.open", "Open", "Открыто"),
        "view_ref": "work-list",
        "filters": [{"field_ref": "status", "operator": "eq", "value": "open"}],
        "min_items": 0,
    }

    with pytest.raises(BuilderWorkflowError, match="requires max_items=0"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_prototype_accepts_exact_filtered_empty_state() -> None:
    brief, semantic = _fixture()
    semantic["representative_states"][0] = {
        "id": "no-archived",
        "label": _text("work.state.no_archived", "No archived", "Нет архивных"),
        "view_ref": "work-list",
        "filters": [
            {"field_ref": "status", "operator": "eq", "value": "archived"}
        ],
        "min_items": 0,
        "max_items": 0,
    }

    validated = validate_semantic_prototype(semantic, brief=brief)

    assert validated["representative_states"][0]["id"] == "no-archived"


def test_empty_fixture_requires_a_rendered_empty_state() -> None:
    brief, semantic = _fixture()
    del semantic["views"][0]["empty_state"]

    with pytest.raises(BuilderWorkflowError, match="requires an empty_state"):
        validate_semantic_prototype(semantic, brief=brief)


def test_representative_state_supports_typed_date_ranges() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["fields"].append(
        {
            "id": "due_on",
            "label": _text("work.field.due_on", "Due on", "Срок"),
            "value_type": "date",
            "required": False,
            "editable": True,
        }
    )
    semantic["resource"]["records"][0]["due_on"] = "2026-09-11"
    semantic["representative_states"][0] = {
        "id": "current-week",
        "label": _text("work.state.current_week", "Current week", "Текущая неделя"),
        "view_ref": "work-list",
        "filters": [
            {"field_ref": "due_on", "operator": "gte", "value": "2026-09-07"},
            {"field_ref": "due_on", "operator": "lte", "value": "2026-09-13"},
        ],
        "min_items": 1,
    }

    result = compile_semantic_prototype(semantic, brief=brief)

    assert result["representative_state_checks"][0]["matching_record_count"] == 1


def test_representative_state_supports_typed_field_comparison() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["fields"].extend(
        [
            {
                "id": "on_hand",
                "label": _text("work.field.on_hand", "On hand", "В наличии"),
                "value_type": "number",
                "required": False,
                "editable": False,
            },
            {
                "id": "minimum",
                "label": _text("work.field.minimum", "Minimum", "Минимум"),
                "value_type": "number",
                "required": False,
                "editable": False,
            },
        ]
    )
    semantic["resource"]["records"][0].update({"on_hand": 4, "minimum": 10})
    semantic["resource"]["records"][1].update({"on_hand": 12, "minimum": 10})
    semantic["representative_states"][0] = {
        "id": "below-minimum",
        "label": _text("work.state.below_minimum", "Below minimum", "Ниже минимума"),
        "view_ref": "work-list",
        "filters": [
            {
                "field_ref": "on_hand",
                "operator": "lt",
                "compare_field_ref": "minimum",
            }
        ],
        "min_items": 1,
        "max_items": 1,
    }

    result = compile_semantic_prototype(semantic, brief=brief)

    check = result["representative_state_checks"][0]
    assert check["matching_record_count"] == 1
    assert check["matching_record_ids"] == ["work-1"]


def test_representative_state_rejects_incompatible_field_comparison() -> None:
    brief, semantic = _fixture()
    semantic["representative_states"][0]["filters"] = [
        {
            "field_ref": "status",
            "operator": "eq",
            "compare_field_ref": "result",
        }
    ]

    with pytest.raises(BuilderWorkflowError, match="compares incompatible fields"):
        validate_semantic_prototype(semantic, brief=brief)


def test_multiple_attachments_compile_to_file_upload_cardinality() -> None:
    brief, semantic = _fixture()
    evidence = next(
        field for field in semantic["resource"]["fields"] if field["id"] == "evidence"
    )
    evidence["multiple"] = True
    evidence["max_items"] = 3
    semantic["resource"]["records"][0]["evidence"] = [
        "fixture://pressure.jpg",
        "fixture://gauge.jpg",
    ]
    semantic["resource"]["records"][1]["evidence"] = []

    result = compile_semantic_prototype(semantic, brief=brief)

    fields = result["webui"]["ui"]["application"]["desktop"]["pageSchema"][
        "widgets"
    ][2]["inputs"]["fields"]
    rendered = next(field for field in fields if field["id"] == "evidence")
    assert rendered["multiple"] is True
    assert rendered["maxFiles"] == 3


def test_non_attachment_field_cannot_be_multiple() -> None:
    brief, semantic = _fixture()
    title = next(
        field for field in semantic["resource"]["fields"] if field["id"] == "title"
    )
    title["multiple"] = True

    with pytest.raises(BuilderWorkflowError, match="cannot be multiple"):
        validate_semantic_prototype(semantic, brief=brief)


def test_multiple_attachment_record_respects_max_items() -> None:
    brief, semantic = _fixture()
    evidence = next(
        field for field in semantic["resource"]["fields"] if field["id"] == "evidence"
    )
    evidence["multiple"] = True
    evidence["max_items"] = 1
    semantic["resource"]["records"][0]["evidence"] = ["first", "second"]

    with pytest.raises(BuilderWorkflowError, match="invalid attachment value"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_composite_identity_compiles_to_runtime_id() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["identity_field_refs"] = ["status", "title"]

    result = compile_semantic_prototype(semantic, brief=brief)

    assert result["prototype_records"][0]["id"] == "open::Pressure check"
    assert result["prototype_records"][1]["id"] == "complete::Guard check"


def test_public_sdk_exposes_semantic_compilation() -> None:
    brief, semantic = _fixture()

    result = prototype_sdk.compile_semantic(semantic, brief=brief)

    assert result["validation"]["ok"] is True
    assert result["webui"]["generated_by"] == "builder.semantic_compiler.v1"
    assert prototype_sdk.semantic_contract()["$id"] == "adaos.webui.semantic.v1"


def test_semantic_runtime_resource_is_scoped_by_project() -> None:
    brief, semantic = _fixture()

    result = prototype_sdk.compile_semantic(
        semantic,
        brief=brief,
        project_ref="project:work-review",
    )

    widgets = result["webui"]["ui"]["application"]["desktop"]["pageSchema"][
        "widgets"
    ]
    resource_types = {
        widget["dataSource"]["resourceType"] for widget in widgets
    }
    assert resource_types == {"prototype.project.work-review.work_items"}


@pytest.mark.parametrize(
    ("semantic_pattern", "runtime_type", "runtime_pattern"),
    [
        ("flow", "stack", "stack"),
        ("split", "split", "split"),
        ("grid", "grid", "grid"),
        ("focus_detail", "split", "focus-detail"),
    ],
)
def test_semantic_layout_maps_to_runtime_abi(
    semantic_pattern: str,
    runtime_type: str,
    runtime_pattern: str,
) -> None:
    brief, semantic = _fixture()
    semantic["layout"]["pattern"] = semantic_pattern

    result = compile_semantic_prototype(semantic, brief=brief)

    layout = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]["layout"]
    assert layout["type"] == runtime_type
    assert layout["pattern"] == runtime_pattern
    assert [area["id"] for area in layout["areas"]] == ["primary", "supporting"]


def test_semantic_prototype_requires_a_primary_view_region() -> None:
    brief, semantic = _fixture()
    for view in semantic["views"]:
        view["region_role"] = "supporting"

    with pytest.raises(BuilderWorkflowError, match="view in the primary region"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_v2_compiles_independent_resources_and_relationships() -> None:
    brief, semantic = _multi_resource_fixture()

    result = compile_semantic_prototype(semantic, brief=brief, project_ref="project:test")

    assert [item["resource_ref"] for item in result["prototype_resources"]] == [
        "work_items",
        "people",
    ]
    assert {item["resource_type"] for item in result["prototype_resources"]} == {
        "prototype.project.test.work_items",
        "prototype.project.test.people",
    }
    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    assert page["meta"]["builder"]["semantic_source"] == "adaos.webui.semantic.v2"
    assert page["meta"]["builder"]["relationships"][0]["id"] == "work_owner"
    assert result["representative_state_checks"][0]["proof"]["kind"] == (
        "collection_empty"
    )
    specs = developer_prototypes.derive_resource_specs(
        result["webui"], result["prototype_resources"]
    )
    assert {
        item["resource_definition"]["resource_type"] for item in specs
    } == {
        "prototype.project.test.work_items",
        "prototype.project.test.people",
    }


def test_semantic_v2_provider_candidate_compiles_through_public_sdk() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)

    result = prototype_sdk.compile_semantic_candidate(
        candidate, brief=brief, project_ref="project:test"
    )

    assert result["semantic_document"]["schema"] == "adaos.webui.semantic.v2"
    assert len(result["prototype_resources"]) == 2
    assert prototype_sdk.semantic_provider_contract(version="v2")["$id"] == (
        "adaos.builder.semantic_prototype_candidate.v2"
    )


def test_generation_guidance_matches_capacity_and_executable_state_contract() -> None:
    from adaos.services.builder.semantic_prototype import semantic_prototype_generation_guidance, semantic_prototype_contract
    from adaos.services.builder.prototype_contracts import STATE_PROOF_RULES
    guidance = semantic_prototype_generation_guidance()
    candidate = semantic_prototype_candidate_contract(version="v2")
    canonical = semantic_prototype_contract(version="v2")
    for group in ("resources", "relationships", "views", "commands"):
        assert guidance["limits"][group]["maxItems"] == canonical["properties"][group]["maxItems"]
    assert set(guidance["state_proofs"]) == set(candidate["$defs"]["stateProof"]["properties"]["kind"]["enum"]) == set(STATE_PROOF_RULES)
    assert "array" in guidance["fixture_values"]["attachments"]
    assert "record.id" in guidance["relationships"]


def test_candidate_capacity_is_enforced_after_provider_projection() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    candidate["resources"] = candidate["resources"] * 5
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert caught.value.findings[0]["code"] == "semantic.candidate_bounds"
    assert caught.value.findings[0]["path"] == "$.resources"


def test_state_repair_preserves_fixtures_commands_and_other_states() -> None:
    from adaos.sdk.builder.prototype import prepare_state_repair, apply_state_repair
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    valid = copy.deepcopy(candidate)
    state = candidate["representative_states"][0]
    first_field = candidate["resources"][0]["fields"][0]["id"]
    state["filters"] = [{"field_ref": first_field, "operator": "eq", "operand": {"kind": "value", "value": candidate["resources"][0]["records"][0]["values"][0], "field_ref": None}}]
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    findings = caught.value.findings
    plan = prepare_state_repair(candidate, findings)
    assert plan is not None
    assert "command" not in plan["output_schema"]["$defs"]
    assert "resource" not in plan["output_schema"]["$defs"]
    repair = {"schema": "adaos.builder.state_repair.v1", "base_sha256": plan["base_sha256"], "states": [valid["representative_states"][0]], "views": []}
    repaired = apply_state_repair(candidate, repair, findings)
    assert repaired == valid
    assert candidate != valid
    compile_semantic_prototype_candidate(repaired, brief=brief)
    with pytest.raises(ValidationError):
        apply_state_repair(candidate, {**repair, "base_sha256": "different"}, findings)
    changed_view = copy.deepcopy(candidate["views"][0])
    changed_view["surface"] = "side_sheet"
    with pytest.raises(BuilderWorkflowError, match="unrelated view change"):
        apply_state_repair(candidate, {**repair, "views": [changed_view]}, findings)
    assert prepare_state_repair(candidate, [{"code": "semantic.compiler_contract_invalid"}]) is None
    repair["states"].append(copy.deepcopy(repair["states"][0]))
    with pytest.raises(BuilderWorkflowError, match="duplicate"):
        apply_state_repair(candidate, repair, findings)


def test_compiler_contract_failure_is_not_a_model_repair(monkeypatch) -> None:
    import adaos.services.builder.semantic_prototype as compiler
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    monkeypatch.setattr(compiler, "validate_webui_capabilities", lambda *_args: {"ok": False, "findings": ["renderer contract defect"]})
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert caught.value.findings[0]["code"] == "semantic.compiler_contract_invalid"


def test_view_only_state_repair_does_not_require_unchanged_state_echo() -> None:
    brief, semantic = _multi_resource_fixture()
    view = semantic["views"][0]
    state = semantic["representative_states"][0]
    state["proof"] = {"kind": "query_empty", "visible_field_refs": ["title"]}
    state["filters"] = [{"field_ref": "title", "operator": "eq", "value": "No matching record"}]
    candidate = _multi_resource_candidate(semantic)
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    findings = caught.value.findings
    plan = prototype_sdk.prepare_state_repair(candidate, findings)
    replacement = copy.deepcopy(candidate["views"][0])
    replacement["surface"] = "inline"
    replacement["query_controls"] = [{"id": "title-filter", "kind": "filter", "field_ref": "title", "label": {"en": "Title", "ru": "Название"}}]
    repaired = prototype_sdk.apply_state_repair(candidate, {
        "schema": "adaos.builder.state_repair.v1", "base_sha256": plan["base_sha256"],
        "states": [], "views": [replacement],
    }, findings)
    assert repaired["representative_states"] == candidate["representative_states"]
    compile_semantic_prototype_candidate(repaired, brief=brief)


def test_state_repair_v2_cannot_echo_or_change_immutable_view_properties() -> None:
    _, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    state = candidate["representative_states"][0]
    findings = [{"code": "semantic.state_fixture_mismatch", "semantic_refs": [f"state:{state['id']}"]}]
    plan = prototype_sdk.prepare_state_repair(candidate, findings)
    properties = plan["output_schema"]["$defs"]["view"]["properties"]
    assert set(properties) == {"id", "empty_state", "field_refs", "query_controls"}
    view = next(view for view in candidate["views"] if view["id"] == state["view_ref"])
    patch = {name: copy.deepcopy(view.get(name)) for name in properties}
    patch["query_controls"] = []
    repair = {"schema": "adaos.builder.state_repair.v2", "base_sha256": plan["base_sha256"], "states": [], "views": [patch]}
    repaired = prototype_sdk.apply_state_repair(candidate, repair, findings)
    updated = next(item for item in repaired["views"] if item["id"] == view["id"])
    assert updated == {**view, **patch}
    patch["resource_ref"] = ""
    with pytest.raises(ValidationError):
        prototype_sdk.apply_state_repair(candidate, repair, findings)


def test_numeric_filter_compiles_to_number_input() -> None:
    brief, semantic = _multi_resource_fixture()
    resource = semantic["resources"][0]
    number = {"id": "quantity", "label": _text("quantity", "Quantity", "Количество"), "value_type": "number", "editable": True, "required": False}
    resource["fields"].append(number)
    view = next(view for view in semantic["views"] if view["resource_ref"] == resource["id"] and view["role"] == "collection")
    view["query_controls"] = [{"id": "number-filter", "kind": "filter", "field_ref": number["id"], "label": _text("quantity", "Quantity", "Количество")}]
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    widget = next(widget for widget in compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]["widgets"] if widget["id"] == "query-number-filter")
    assert widget["inputs"]["inputType"] == "number"


def test_record_lock_and_attachment_capture_share_typed_provider_contracts() -> None:
    from adaos.sdk.developer.prototypes import derive_record_resource_spec
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    resource = candidate["resources"][0]
    resource["read_only_when"] = {"field_ref": "result", "operator": "equals", "value": "issue"}
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    page = compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    form = next(widget for widget in page["widgets"] if widget["type"] == "ui.form")
    assert form["inputs"]["readOnlyIf"] == '$state.result === "issue"'
    attachment = next(field for field in form["inputs"]["fields"] if field["type"] == "fileUpload")
    assert attachment["fileStorage"] == "prototype"
    runtime = compiled["prototype_resources"][0]
    specification = derive_record_resource_spec(compiled["webui"], runtime["records"], resource_type=runtime["resource_type"])
    definition = specification["resource_definition"]
    assert definition["metadata"]["prototype_policy"]["read_only_when"] == resource["read_only_when"]
    assert definition["record_schema"]["properties"][attachment["id"]]["format"] == "adaos-attachment"


def test_markdown_remains_typed_text_with_explicit_formatted_details() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    resource = candidate["resources"][0]
    field = next(field for field in resource["fields"] if field["value_type"] == "long_text")
    field["value_type"] = "markdown"
    details = next(view for view in candidate["views"] if view["role"] == "details" and view["resource_ref"] == resource["id"])
    if field["id"] not in details["field_refs"]:
        details["field_refs"].append(field["id"])
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    widget = next(widget for widget in compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]["widgets"] if widget["id"] == details["id"])
    assert next(item for item in widget["inputs"]["fields"] if item["id"] == field["id"])["kind"] == "markdown"


def test_qualified_record_identity_alias_is_unambiguous() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    relationship = candidate["relationships"][0]
    relationship["to_field_ref"] = relationship["to_resource_ref"] + ".id"
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert any(item["kind"] == "qualified_record_identity" for item in compiled["normalizations"])


def test_typed_fixture_literals_normalize_only_exact_json_scalars() -> None:
    from adaos.services.builder.semantic_prototype import _normalize_candidate_fixture_values
    fields = [{"value_type": "number", "required": False}, {"value_type": "boolean", "required": False}, {"value_type": "short_text"}]
    records = [{"values": ["12.0", "false", "0012"]}, {"values": ["", "", ""]}, {"values": ["00:12", "no", "unchanged"]}]
    changes = []
    _normalize_candidate_fixture_values(fields=fields, records=records, path="$.records", normalizations=changes)
    assert [record["values"] for record in records] == [[12.0, False, "0012"], [None, None, ""], ["00:12", "no", "unchanged"]]
    assert len(changes) == 4
    assert all(change["kind"] == "typed_json_scalar" for change in changes)


def test_collection_empty_is_a_fixture_of_the_same_populated_resource() -> None:
    brief, semantic = _multi_resource_fixture()
    resource = semantic["resources"][0]
    view = next(view for view in semantic["views"] if view["role"] == "collection" and view["resource_ref"] == resource["id"])
    view["presentation"] = "table"
    state = copy.deepcopy(semantic["representative_states"][0])
    state.update(id="empty-example", view_ref=view["id"], filters=[], min_items=0, max_items=0, proof={"kind": "collection_empty", "visible_field_refs": []})
    semantic["representative_states"].append(state)
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    assert compiled["prototype_resources"][0]["records"]
    check = next(check for check in compiled["representative_state_checks"] if check["state_id"] == "empty-example")
    assert check["fixture_mode"] == "empty"
    assert check["matching_record_count"] == 0
    assert compiled["source_map"]["state:empty-example"][0].endswith(".inputs.emptyText")


def test_typed_fixture_arrays_do_not_interpret_text_or_malformed_values() -> None:
    from adaos.services.builder.semantic_prototype import _normalize_candidate_fixture_values
    fields = [{"value_type": "attachments"}, {"value_type": "multi_choice", "options": [{"value": "a", "label": {"en": "Alpha"}}]}, {"value_type": "short_text"}]
    records = [{"values": ['["sample://document"]', '["Alpha"]', '[]']},
               {"values": ['[]', '[1]', '["unchanged"]']}, {"values": ['not json', '{"a":1}', '001']}]
    changes = []
    _normalize_candidate_fixture_values(fields=fields, records=records, path="$.records", normalizations=changes)
    assert [record["values"] for record in records] == [
        [["sample://document"], ["a"], '[]'], [[], '[1]', '["unchanged"]'], ['not json', '{"a":1}', '001'],
    ]
    assert [change["kind"] for change in changes].count("typed_json_array") == 3
    assert changes[2]["kind"] == "localized_choice_value"


@pytest.mark.parametrize("role", ["details", "editor"])
def test_field_predicate_can_be_observed_in_selected_record_views(role: str) -> None:
    brief, semantic = _multi_resource_fixture()
    view = next(view for view in semantic["views"] if view["role"] == role)
    if "result" not in view["field_refs"]:
        view["field_refs"].append("result")
    state = copy.deepcopy(semantic["representative_states"][0])
    state.update(id="record-state", view_ref=view["id"], min_items=1, max_items=None,
                 filters=[{"field_ref": "result", "operator": "eq", "value": "issue"}],
                 proof={"kind": "field_predicate", "visible_field_refs": ["result"]})
    semantic["representative_states"].append(state)
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    check = next(check for check in compiled["representative_state_checks"] if check["state_id"] == "record-state")
    assert check["fixture_mode"] == "selected_record"
    assert check["matching_record_ids"]
    assert compiled["source_map"]["state:record-state"]


def test_state_view_errors_are_included_in_first_pass_findings() -> None:
    brief, semantic = _multi_resource_fixture()
    editor = next(view for view in semantic["views"] if view["role"] == "editor")
    first = copy.deepcopy(semantic["representative_states"][0])
    first.update(id="empty-editor", view_ref=editor["id"], filters=[], min_items=0, max_items=0,
                 proof={"kind": "collection_empty", "visible_field_refs": []})
    semantic["representative_states"].append(first)
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    codes = {item["code"] for item in caught.value.findings}
    assert "semantic.state_view_role_invalid" in codes
    from adaos.services.builder.semantic_prototype import _semantic_v2_model_findings
    semantic["representative_states"][-1]["view_ref"] = "absent"
    assert "semantic.state_view_missing" in {item["code"] for item in _semantic_v2_model_findings(semantic)}


def test_cross_record_guard_is_reported_before_repair() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["commands"][0]["guard"] = {"when": {"field_ref": "result", "operator": "equals", "value": "issue"}, "require_nonempty": ["person_phone"]}
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    assert "semantic.command_field_missing" in {item["code"] for item in caught.value.findings}


def test_filter_context_is_generated_from_validator_types() -> None:
    from adaos.services.builder.semantic_prototype import FILTER_VALUE_TYPES, semantic_prototype_generation_guidance
    guidance = semantic_prototype_generation_guidance()
    assert set(guidance["query_filters"]["field_types"]) == FILTER_VALUE_TYPES
    assert {"number", "boolean"} <= FILTER_VALUE_TYPES


def test_all_invalid_state_predicates_are_reported_before_repair_scope() -> None:
    brief, semantic = _multi_resource_fixture()
    state = semantic["representative_states"][0]
    state.update(min_items=1, max_items=None, proof={"kind": "field_predicate", "visible_field_refs": ["result"]},
                 filters=[{"field_ref": "result", "operator": "eq", "value": "unknown-first"}])
    second = copy.deepcopy(state)
    second.update(id="second-invalid", filters=[{"field_ref": "result", "operator": "eq", "value": "unknown-second"}])
    semantic["representative_states"].append(second)
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    invalid = [item for item in caught.value.findings if item["code"] == "semantic.state_predicate_invalid"]
    assert len(invalid) == 2


def test_assignment_can_create_a_relationship_but_not_an_unrelated_record() -> None:
    from adaos.services.ui_capabilities import evaluate_ui_request
    _, semantic = _multi_resource_fixture()
    editor = next(view for view in semantic["views"] if view["role"] == "editor")
    editor["field_refs"].append("work_owner_id")
    for command in semantic["commands"]:
        command["kind"] = "create"
        command.setdefault("input_field_refs", []).append("work_owner_id")
    compiled = compile_semantic_prototype(semantic)
    request = "Show items and assign their owner."
    accepted = evaluate_ui_request(request, compiled["webui"], prototype_resources=compiled["prototype_resources"])
    assert next(item for item in accepted["postconditions"] if item["id"] == "resource.assignment_operation")["ok"]
    page = compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    form = next(widget for widget in page["widgets"] if widget["type"] == "ui.form")
    form["inputs"]["fields"] = [field for field in form["inputs"]["fields"] if field["id"] != "work_owner_id"]
    rejected = evaluate_ui_request(request, compiled["webui"], prototype_resources=compiled["prototype_resources"])
    assert not next(item for item in rejected["postconditions"] if item["id"] == "resource.assignment_operation")["ok"]


def test_compiled_regions_use_client_placement_roles() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["layout"]["pattern"] = "focus_detail"
    semantic["views"][0]["region_role"] = "primary"
    semantic["views"][1]["region_role"] = "supporting"
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    areas = compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]["layout"]["areas"]
    assert {item["id"]: item["role"] for item in areas}["supporting"] == "aux"
    assert all(item["role"] in {"main", "aux", "footer"} for item in areas)


@pytest.mark.parametrize("locale", ["en", "ru"])
def test_single_locale_candidate_keeps_keys_without_fabricating_translations(locale) -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)

    def keep_language(node):
        if isinstance(node, dict):
            if "en" in node and "ru" in node:
                node.pop("ru" if locale == "en" else "en")
            for child in node.values():
                keep_language(child)
        elif isinstance(node, list):
            for child in node:
                keep_language(child)

    keep_language(candidate)
    editor = next(view for view in candidate["views"] if view["role"] == "editor")
    editor["surface"] = "modal"
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert set(compiled["locale_dictionaries"]) == {locale}
    assert compiled["locale_dictionaries"][locale]
    assert compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]["title"] == candidate["title"][locale]
    provider = semantic_prototype_provider_contract(version="v2", locales=(locale,))
    assert provider["$defs"]["localizedText"]["required"] == [locale]
    assert set(provider["$defs"]["localizedText"]["properties"]) == {locale}


def test_media_binding_renders_actual_media_and_resolves_only_explicit_samples() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    details = next(view for view in candidate["views"] if view["role"] == "details")
    details["media"] = {"source_field_ref": "evidence", "kind_field_ref": None, "poster_field_ref": None}
    resource = candidate["resources"][0]
    index = next(i for i, field in enumerate(resource["fields"]) if field["id"] == "evidence")
    resource["records"][0]["values"][index] = "sample://image"
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    widget = next(w for w in compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]["widgets"] if w["id"] == details["id"])
    assert widget["inputs"]["mediaKey"] == "evidence"
    assert compiled["prototype_resources"][0]["records"][0]["evidence"] == "/assets/prototype/sample-image.jpg"
    assert candidate["resources"][0]["records"][0]["values"][index] == "sample://image"


def test_query_empty_proof_has_no_records_and_requires_a_reachable_filter() -> None:
    brief, semantic = _multi_resource_fixture()
    view = semantic["views"][0]
    view["query_controls"] = [{"id": "title-filter", "kind": "filter", "field_ref": "title", "label": _text("filter.title", "Title", "Название")}]
    state = semantic["representative_states"][0]
    state["proof"] = {"kind": "query_empty", "visible_field_refs": ["title"]}
    state["filters"] = [{"field_ref": "title", "operator": "eq", "value": "No matching record"}]
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    assert result["representative_state_checks"][0]["proof"]["kind"] == "query_empty"
    view["query_controls"] = []
    with pytest.raises(BuilderWorkflowError, match="matching equality filter controls"):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)


def test_state_findings_include_unreachable_queries_before_bounded_repair() -> None:
    brief, semantic = _multi_resource_fixture()
    state = semantic["representative_states"][0]
    state["proof"] = {"kind": "query_empty", "visible_field_refs": ["title"]}
    state["filters"] = [{"field_ref": "title", "operator": "eq", "value": "No matching record"}]
    second = copy.deepcopy(state)
    second["id"] = "also-invalid"
    second["min_items"] = 1
    second["max_items"] = None
    second["proof"]["kind"] = "field_predicate"
    semantic["representative_states"].append(second)
    candidate = _multi_resource_candidate(semantic)
    with pytest.raises(BuilderWorkflowError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    codes = {item["code"] for item in caught.value.findings}
    assert {"semantic.state_query_unreachable", "semantic.state_fixture_mismatch"} <= codes
    plan = prototype_sdk.prepare_state_repair(candidate, caught.value.findings)
    assert set(plan["allowed_state_ids"]) == {state["id"], second["id"]}


def test_semantic_v2_rejects_state_proof_for_hidden_fields() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["representative_states"][0]["proof"] = {
        "kind": "field_predicate",
        "visible_field_refs": ["comment"],
    }
    semantic["representative_states"][0]["filters"] = [
        {"field_ref": "comment", "operator": "eq", "value": "Outside tolerance"}
    ]
    semantic["representative_states"][0]["min_items"] = 1
    semantic["representative_states"][0].pop("max_items")

    with pytest.raises(BuilderWorkflowError, match="claims fields not visible"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_v2_rejects_ambiguous_field_namespaces() -> None:
    brief, semantic = _multi_resource_fixture()
    semantic["resources"][1]["fields"][0]["id"] = "title"
    semantic["resources"][1]["records"][0]["title"] = semantic["resources"][1][
        "records"
    ][0].pop("person_name")
    semantic["resources"][1]["records"][1]["title"] = semantic["resources"][1][
        "records"
    ][1].pop("person_name")
    semantic["views"][-1]["field_refs"][0] = "title"

    with pytest.raises(BuilderWorkflowError, match="duplicate field id"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_v2_candidate_reports_record_and_state_defects_together() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    result_field_index = next(
        index
        for index, field in enumerate(candidate["resources"][0]["fields"])
        if field["id"] == "result"
    )
    candidate["resources"][0]["records"][0]["values"][result_field_index] = (
        "INVALID"
    )
    candidate["representative_states"][0]["proof"] = {
        "kind": "field_predicate",
        "visible_field_refs": ["status"],
    }
    candidate["representative_states"][0]["filters"] = [
        {
            "field_ref": "status",
            "operator": "eq",
            "operand": {"kind": "value", "value": "missing", "field_ref": None},
        }
    ]
    candidate["representative_states"][0]["min_items"] = 1
    candidate["representative_states"][0]["max_items"] = None
    collection_view = next(
        item for item in candidate["views"] if item["role"] == "collection"
    )
    candidate["commands"][0]["view_ref"] = collection_view["id"]

    with pytest.raises(BuilderWorkflowError) as captured:
        compile_semantic_prototype_candidate(candidate, brief=brief)

    codes = {item["code"] for item in captured.value.findings}
    assert "semantic.record_value_invalid" in codes
    assert "semantic.state_fixture_mismatch" in codes
    assert "semantic.command_editor_required" in codes


def test_semantic_v2_candidate_normalizes_presentation_derived_from_role() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    collection = next(
        item for item in candidate["views"] if item["role"] == "collection"
    )
    editor = next(item for item in candidate["views"] if item["role"] == "editor")
    collection["presentation"] = None
    editor["presentation"] = "cards"

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    normalized_views = {
        item["id"]: item for item in result["semantic_document"]["views"]
    }
    assert normalized_views[collection["id"]]["presentation"] == "list"
    assert "presentation" not in normalized_views[editor["id"]]
    assert [
        item for item in result["normalizations"]
        if item["kind"] == "view_presentation_for_role"
    ] == [
        {
            "kind": "view_presentation_for_role",
            "from": "None",
            "to": "list",
            "target": f"$.views.@{collection['id']}.presentation",
        },
        {
            "kind": "view_presentation_for_role",
            "from": "cards",
            "to": "None",
            "target": f"$.views.@{editor['id']}.presentation",
        },
    ]


def test_semantic_v2_capability_gap_conservatively_overrides_binding() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    requirement_ref = candidate["requirement_bindings"][0]["requirement_ref"]
    candidate["capability_gaps"].append(
        {
            "requirement_ref": requirement_ref,
            "code": "runtime_enforcement_unavailable",
            "detail": "The prototype can show the data but cannot enforce the rule.",
        }
    )

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    assert requirement_ref not in {
        item["requirement_ref"]
        for item in result["semantic_document"]["requirement_bindings"]
    }
    assert any(
        item["kind"] == "capability_gap_precedence"
        and item["to"] == requirement_ref
        for item in result["normalizations"]
    )
    builder_meta = result["webui"]["ui"]["application"]["desktop"][
        "pageSchema"
    ]["meta"]["builder"]
    assert builder_meta["capability_gaps"] == result["capability_gaps"]


@pytest.mark.parametrize("surface,presentation", [("modal", "modal"), ("side_sheet", "sideSheet")])
def test_semantic_v2_editor_surface_preserves_commands_and_source_map(surface, presentation) -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    editor = next(item for item in candidate["views"] if item["role"] == "editor")
    editor["surface"] = surface
    create = copy.deepcopy(candidate["commands"][0])
    create.update(id="create-item", kind="create", guard=None, fixed_values=[])
    candidate["commands"].append(create)
    result = compile_semantic_prototype_candidate(candidate, brief=brief)
    application = result["webui"]["ui"]["application"]
    modal = application["modals"][f"editor-{editor['id']}"]
    assert modal["presentation"]["kind"] == presentation
    assert "pageSchema" not in modal
    form = modal["schema"]["widgets"][0]
    assert form["inputs"]["closeOnSuccess"] is True
    assert {button["id"] for button in form["inputs"]["buttons"]} == {action["id"] for action in form["actions"]}
    assert all("selected_" in action["enabledIf"] for action in form["actions"])
    assert all(widget["id"] != editor["id"] for widget in application["desktop"]["pageSchema"]["widgets"])
    assert all("ui.application.modals." in ref for ref in result["source_map"][f"view:{editor['id']}"])
    assert any(widget["id"] == f"open-{editor['id']}" for widget in application["desktop"]["pageSchema"]["widgets"])
    widgets = application["desktop"]["pageSchema"]["widgets"]
    opener = next(widget for widget in widgets if widget["id"] == f"open-{editor['id']}")
    assert [button["id"] for button in opener["inputs"]["buttons"]] == ["new"]
    details = next(widget for widget in widgets if widget["type"] == "item.details")
    assert any(action["type"] == "openModal" for action in details["actions"])
    assert result["locale_dictionaries"]["ru"]["prototype.editor.new"] == "Добавить"
    from jsonschema import ValidationError
    import adaos.services.builder.semantic_prototype as compiler
    modal["pageSchema"] = modal.pop("schema")
    with pytest.raises(ValidationError):
        compiler._validator("webui.v1.schema.json").validate(result["webui"])


def test_semantic_v2_relationship_identity_compiles_editor_selector() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    candidate["relationships"][0]["to_field_ref"] = "person_name"
    editor = next(item for item in candidate["views"] if item["role"] == "editor")
    editor["field_refs"].append("work_owner_id")

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    assert result["semantic_document"]["relationships"][0]["to_field_ref"] == "id"
    assert any(
        item["kind"] == "relationship_identity_target"
        for item in result["normalizations"]
    )
    editor_widget = next(
        item
        for item in result["webui"]["ui"]["application"]["desktop"][
            "pageSchema"
        ]["widgets"]
        if item["id"] == "work-editor"
    )
    owner = next(
        item
        for item in editor_widget["inputs"]["fields"]
        if item["id"] == "work_owner_id"
    )
    assert owner["type"] == "dropdown"
    assert owner["optionsDataSource"] == {"kind": "resourceQuery", "resourceType": "prototype.people", "query": {"limit": 100}}
    assert owner["optionValuePath"] == "id"
    assert owner["optionLabelPaths"] == ["person_name"]
    assert "options" not in owner
    schema = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]["meta"]["builder"]["prototype_record_schemas"]["prototype.work_items"]
    assert schema["properties"]["work_owner_id"] == {"type": ["string", "null"]}


def test_semantic_v2_accepts_choice_foreign_key_to_string_identity() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    owner = next(
        field
        for field in candidate["resources"][0]["fields"]
        if field["id"] == "work_owner_id"
    )
    owner["value_type"] = "choice"
    owner["options"] = [
        {"value": "person-1", "label": {"en": "Alex", "ru": "Алекс"}},
        {"value": "person-2", "label": {"en": "Sam", "ru": "Сэм"}},
    ]
    editor_view = next(
        item for item in candidate["views"] if item["role"] == "editor"
    )
    editor_view["field_refs"].append("work_owner_id")

    result = compile_semantic_prototype_candidate(candidate, brief=brief)

    assert result["semantic_document"]["relationships"][0]["to_field_ref"] == "id"
    editor = next(
        item
        for item in result["webui"]["ui"]["application"]["desktop"][
            "pageSchema"
        ]["widgets"]
        if item["id"] == "work-editor"
    )
    rendered_owner = next(
        item for item in editor["inputs"]["fields"] if item["id"] == "work_owner_id"
    )
    assert rendered_owner["type"] == "dropdown"
    assert rendered_owner["optionValuePath"] == "id"


def test_semantic_v2_normalizes_choice_relationship_values_consistently() -> None:
    brief, semantic = _multi_resource_fixture()
    source, target = semantic["resources"]
    owner = next(field for field in source["fields"] if field["id"] == "work_owner_id")
    owner["value_type"] = "choice"
    owner["options"] = []
    for index, record in enumerate(target["records"], 1):
        record["id"] = f"person:{index}"
        source["records"][index - 1]["work_owner_id"] = record["id"]
        owner["options"].append({"value": record["id"], "label": _text(f"person.{index}", record["person_name"], record["person_name"])})
    source["fields"][0]["visible_when"] = {"field_ref": "work_owner_id", "operator": "equals", "value": "person:1"}
    editor = next(view for view in semantic["views"] if view["role"] == "editor")
    editor["field_refs"].append("work_owner_id")
    command = semantic["commands"][0]
    command.setdefault("fixed_values", {})["work_owner_id"] = "person:1"
    command["guard"] = {"when": {"field_ref": "work_owner_id", "operator": "equals", "value": "person:1"}, "require_nonempty": [source["fields"][0]["id"]]}
    collection = next(view for view in semantic["views"] if view["role"] == "collection" and view["resource_ref"] == source["id"])
    collection["field_refs"].append("work_owner_id")
    semantic["representative_states"].append({
        "id": "owner-items", "label": _text("owner.items", "Owner items", "Записи владельца"),
        "view_ref": collection["id"], "min_items": 1, "max_items": 1,
        "proof": {"kind": "field_predicate", "visible_field_refs": ["work_owner_id"]},
        "filters": [{"field_ref": "work_owner_id", "operator": "eq", "value": "person:1"}],
    })
    candidate = _multi_resource_candidate(semantic)
    original = copy.deepcopy(candidate)
    result = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert candidate == original
    document = result["semantic_document"]
    resource = document["resources"][0]
    assert [record["work_owner_id"] for record in resource["records"]] == ["person.1", "person.2"]
    owner = next(field for field in resource["fields"] if field["id"] == "work_owner_id")
    assert [option["value"] for option in owner["options"]] == ["person.1", "person.2"]
    assert resource["fields"][0]["visible_when"]["value"] == "person.1"
    assert document["commands"][0]["fixed_values"]["work_owner_id"] == "person.1"
    assert document["commands"][0]["guard"]["when"]["value"] == "person.1"
    assert document["representative_states"][-1]["filters"][0]["value"] == "person.1"
    assert result["normalizations"]


def test_semantic_v2_reports_incompatible_relationship_type() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    owner = next(
        field
        for field in candidate["resources"][0]["fields"]
        if field["id"] == "work_owner_id"
    )
    owner["value_type"] = "number"
    candidate["resources"][0]["records"][0]["values"][-1] = 1
    candidate["resources"][0]["records"][1]["values"][-1] = 2

    with pytest.raises(BuilderWorkflowError) as captured:
        compile_semantic_prototype_candidate(candidate, brief=brief)

    assert "semantic.relationship_type_incompatible" in {
        item["code"] for item in captured.value.findings
    }


def test_semantic_v2_reports_broken_relationship_fixture() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    owner_index = next(
        index
        for index, field in enumerate(candidate["resources"][0]["fields"])
        if field["id"] == "work_owner_id"
    )
    candidate["resources"][0]["records"][0]["values"][owner_index] = (
        "missing-person"
    )

    with pytest.raises(BuilderWorkflowError) as captured:
        compile_semantic_prototype_candidate(candidate, brief=brief)

    assert "semantic.relationship_target_missing" in {
        item["code"] for item in captured.value.findings
    }


@pytest.mark.parametrize("key", ["from_resource_ref", "to_resource_ref", "from_field_ref", "to_field_ref"])
def test_semantic_v2_reports_missing_relationship_reference_before_normalizing(key: str) -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    candidate["relationships"][0][key] = "missing"
    with pytest.raises(BuilderWorkflowError) as captured:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert any(item["code"] == "semantic.relationship_reference_missing"
               and item["path"] == f"$.relationships[0].{key}"
               for item in captured.value.findings)


def _automation_requirement(requirement_ref: str) -> dict:
    return {
        "requirement_ref": requirement_ref,
        "reason": "business_rule",
        "disclosure": {"en": "The rule is illustrated, not enforced.", "ru": "Правило показано, но не исполняется."},
        "acceptance": "Reject invalid completion without changing the saved record; allow a valid completion.",
    }


def test_semantic_v2_preserves_automation_obligation_without_claiming_implementation() -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    ref = brief["principal_jobs"][0]["id"]
    candidate["automation_requirements"] = [_automation_requirement(ref)]
    next(item for item in candidate["requirement_bindings"] if item["requirement_ref"] == ref)["semantic_refs"] = [
        {"kind": "view", "id": candidate["views"][0]["id"]}
    ]
    result = compile_semantic_prototype_candidate(candidate, brief=brief)
    obligation = result["automation_requirements"][0]
    assert obligation["status"] == "pending_automation"
    assert obligation["statement"] == brief["principal_jobs"][0]["statement"]
    assert obligation["brief_digest"] == brief["digest"]
    assert obligation["prototype_refs"]
    meta = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]["meta"]["builder"]
    assert meta["acceptance_stage"] == "prototype"
    assert meta["automation_requirements"] == result["automation_requirements"]
    replay = compile_semantic_prototype(result["semantic_document"])
    assert replay["automation_requirements"][0]["statement"] == obligation["statement"]


@pytest.mark.parametrize("invalid", ["missing_binding", "duplicate", "ui_operation", "hidden_evidence"])
def test_semantic_v2_does_not_allow_unbound_or_ui_deferrals(invalid: str) -> None:
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    ref = brief["principal_jobs"][0]["id"]
    if invalid == "ui_operation":
        ref = brief["operations"][0]["id"]
    candidate["automation_requirements"] = [_automation_requirement(ref)]
    binding = next(item for item in candidate["requirement_bindings"] if item["requirement_ref"] == ref)
    binding["semantic_refs"] = [{"kind": "view", "id": candidate["views"][0]["id"]}]
    if invalid == "missing_binding":
        candidate["automation_requirements"][0]["requirement_ref"] = "job:missing"
    elif invalid == "duplicate":
        candidate["automation_requirements"] *= 2
    elif invalid == "hidden_evidence":
        binding["semantic_refs"] = [{"kind": "resource", "id": candidate["resources"][0]["id"]}]
    with pytest.raises(BuilderWorkflowError, match="automation[_ ]requirement") as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    if invalid == "ui_operation":
        assert caught.value.findings[0]["code"] == "requirement.automation_reference_ineligible"
        assert "Eligible refs:" in caught.value.findings[0]["detail"]
        assert "statement" not in str(caught.value)
