from __future__ import annotations

import copy
import re

import pytest

from adaos.sdk.builder import prototype as prototype_sdk
from adaos.sdk.developer import prototypes as developer_prototypes
from adaos.services.builder.semantic_prototype import (
    compile_semantic_prototype_candidate,
    compile_semantic_prototype,
    semantic_prototype_candidate_contract,
    semantic_prototype_provider_contract,
    validate_semantic_prototype,
)
from adaos.services.builder.workflow import BuilderWorkflowError
from adaos.services.builder_intent import compile_prototype_brief


def _text(key: str, en: str, ru: str) -> dict[str, str]:
    return {"key": key, "en": en, "ru": ru}


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

    assert_strict(contract)
    assert contract["properties"]["resource"]["properties"]["records"][
        "maxItems"
    ] == 6

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
            "value_type": "number",
            "required": False,
            "editable": True,
        }
    )
    semantic["views"][0]["query_controls"][1]["field_ref"] = "effort"

    with pytest.raises(
        BuilderWorkflowError,
        match="requires a boolean, choice, date, or short_text field",
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
    assert owner["type"] == "singleChoice"
    assert owner["options"] == [
        {
            "value": "person-1",
            "label": "Alex",
            "label_i18n": {
                "key": "relationship.work_owner.option.person_1",
                "fallback": "Alex",
            },
        },
        {
            "value": "person-2",
            "label": "Sam",
            "label_i18n": {
                "key": "relationship.work_owner.option.person_2",
                "fallback": "Sam",
            },
        },
    ]


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
    assert rendered_owner["type"] == "singleChoice"


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
    with pytest.raises(BuilderWorkflowError, match="automation[_ ]requirement"):
        compile_semantic_prototype_candidate(candidate, brief=brief)
