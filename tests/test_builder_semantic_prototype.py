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
            "regions": [
                {"id": "primary", "role": "primary"},
                {"id": "supporting", "role": "supporting"},
            ],
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
                "region_ref": "primary",
                "title": _text("work.list", "Items", "Пункты"),
                "field_refs": ["title", "result", "status"],
                "empty_state": {
                    "title": _text("work.empty", "No work items", "Нет пунктов")
                },
            },
            {
                "id": "work-details",
                "role": "details",
                "region_ref": "supporting",
                "title": _text("work.details", "Selected item", "Выбранный пункт"),
                "field_refs": ["title", "result", "status"],
            },
            {
                "id": "work-editor",
                "role": "editor",
                "region_ref": "supporting",
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
                "evidence": ["intent.statement"],
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
