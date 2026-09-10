from __future__ import annotations

import copy

import pytest

from adaos.sdk.builder import prototype as prototype_sdk
from adaos.services.builder.semantic_prototype import (
    compile_semantic_prototype,
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
            "requirement_ref": brief["principal_jobs"][0]["id"],
            "semantic_refs": ["resource:work_items", "view:work-list"],
        },
        {
            "requirement_ref": brief["collection_requirements"][0]["id"],
            "semantic_refs": [
                "resource:work_items",
                "view:work-list",
                "view:work-editor",
            ],
        },
    ]
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
        BuilderWorkflowError, match="requires a choice, date, or short_text field"
    ):
        validate_semantic_prototype(semantic, brief=brief)


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


def test_semantic_prototype_rejects_invalid_representative_record() -> None:
    brief, semantic = _fixture()
    semantic["resource"]["records"][0]["result"] = "not-an-option"

    with pytest.raises(BuilderWorkflowError, match="invalid choice value"):
        validate_semantic_prototype(semantic, brief=brief)


def test_semantic_prototype_rejects_unproven_representative_state() -> None:
    brief, semantic = _fixture()
    semantic["representative_states"][0]["filters"] = [
        {"field_ref": "status", "operator": "eq", "value": "open"}
    ]

    with pytest.raises(BuilderWorkflowError, match="expected 0..0.*found 1"):
        validate_semantic_prototype(semantic, brief=brief)


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
