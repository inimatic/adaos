from __future__ import annotations

from adaos.services.ui_capabilities import (
    evaluate_ui_request,
    get_ui_capability,
    qualify_ui_request,
    search_ui_capabilities,
    selected_ui_capabilities,
    ui_capability_catalog,
    validate_webui_capabilities,
)


def _board_webui(*, cards_per_lane: int = 2) -> dict:
    lanes = [
        {"id": "planned", "label": "Запланировано"},
        {"id": "doing", "label": "В работе"},
        {"id": "done", "label": "Готово"},
    ]
    rows = [
        {"id": f"{lane['id']}-{index}", "title": f"Task {index}", "status": lane["id"]}
        for lane in lanes
        for index in range(cards_per_lane)
    ]
    return {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "kanban",
                        "layout": {
                            "type": "single",
                            "pattern": "stack",
                            "areas": [{"id": "main", "role": "main"}],
                        },
                        "widgets": [
                            {
                                "id": "tasks",
                                "type": "collection.board",
                                "area": "main",
                                "inputs": {
                                    "lanes": lanes,
                                    "laneKey": "status",
                                    "titleKey": "title",
                                },
                                "dataSource": {"kind": "static", "value": rows},
                            }
                        ],
                    }
                }
            }
        },
    }


def test_catalog_covers_every_webui_widget_type() -> None:
    catalog = ui_capability_catalog()

    assert catalog["schema"] == "adaos.ui.capability_catalog.v1"
    assert catalog["coverage"]["complete"] is True
    assert get_ui_capability("collection.board")["kind"] == "component"


def test_multilingual_search_selects_kanban_recipe() -> None:
    result = search_ui_capabilities("Покажи задачи канбан-доской в трех колонках")

    assert any(item["id"] == "recipe.kanban_board" for item in result["items"])
    selected = selected_ui_capabilities("Покажи задачи канбан-доской в трех колонках")
    assert {item["id"] for item in selected["items"]} == {
        "recipe.kanban_board",
        "collection.board",
        "layout.flow",
    }
    assert selected["root_item_ids"] == [
        "recipe.kanban_board",
        "collection.board",
        "layout.flow",
    ]
    assert selected["dependency_closure"] == []


def test_qualification_extracts_bounded_kanban_acceptance() -> None:
    result = qualify_ui_request(
        "Покажи задачи канбан-доской в трех колонках: Запланировано, В работе и Готово. "
        "Добавь по две примерные карточки в каждую колонку."
    )

    assert result["ready"] is True
    assert result["requirements"]["lane_count"] == 3
    assert result["requirements"]["items_per_lane"] == 2
    assert result["requirements"]["images_requested"] is False


def test_qualification_selects_supported_board_drag_drop_contract() -> None:
    result = qualify_ui_request("Add drag and drop to the Kanban board")

    assert result["ready"] is True
    assert result["requirements"]["drag_drop"] is True
    assert result["capability_gaps"] == []


def test_qualification_extracts_exact_russian_column_rename() -> None:
    result = qualify_ui_request(
        "Переименуй колонку Запланировано в Бэклог. Больше ничего не меняй."
    )

    assert result["surface_kind"] == "board"
    assert "ui_text_rename" in result["concepts"]
    assert result["requirements"]["literal_text_change"] == {
        "target_kind": "column",
        "from": "Запланировано",
        "to": "Бэклог",
        "only_change": True,
    }


def test_request_evaluation_requires_exact_requested_literal() -> None:
    request = "Переименуй колонку Запланировано в Бэклог. Больше ничего не меняй."
    webui = _board_webui()
    first_lane = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0][
        "inputs"
    ]["lanes"][0]
    first_lane["label"] = "Backlog"

    rejected = evaluate_ui_request(request, webui)

    literal = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "ui.literal_text_change"
    )
    assert rejected["ok"] is False
    assert literal["actual"] == {"sourceCount": 0, "targetCount": 0}

    first_lane["label"] = "Бэклог"
    accepted = evaluate_ui_request(request, webui)

    literal = next(
        item
        for item in accepted["postconditions"]
        if item["id"] == "ui.literal_text_change"
    )
    assert accepted["ok"] is True
    assert literal["actual"] == {"sourceCount": 0, "targetCount": 1}


def test_qualification_selects_resource_workbench_for_russian_board_crud() -> None:
    request = (
        "Сделай канбан-доску с поиском и фильтрами. "
        "Разреши создавать, редактировать и удалять карточки."
    )

    result = qualify_ui_request(request)
    selected = selected_ui_capabilities(request)

    assert result["ready"] is True
    assert result["requirements"]["resource_query"] is True
    assert result["requirements"]["operation_kinds"] == ["create", "update", "delete"]
    assert "recipe.resource_board_workbench" in {
        item["id"] for item in selected["items"]
    }
    selected_ids = {item["id"] for item in selected["items"]}
    assert {
        "layout.split",
        "input.text",
        "input.selector",
        "ui.form",
        "item.details",
    } <= selected_ids
    assert {
        "layout.split",
        "input.text",
        "input.selector",
        "ui.form",
        "item.details",
    } <= set(selected["dependency_closure"])


def test_capability_validation_rejects_unknown_layout_and_board_lane() -> None:
    webui = _board_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["layout"]["type"] = "masonry"
    page["widgets"][0]["dataSource"]["value"][0]["status"] = "missing"

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert {item["code"] for item in result["findings"]} == {
        "ui.layout.type_unsupported",
        "ui.board.item_lane_unknown",
    }


def test_capability_validation_rejects_decorative_board_drag_drop() -> None:
    webui = _board_webui()
    board = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]
    board["inputs"]["dragDrop"] = True

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert result["findings"][0]["code"] == "ui.board.move_action_missing"


def test_request_evaluation_requires_board_semantics_not_parallel_lists() -> None:
    webui = _board_webui()
    request = (
        "Покажи задачи канбан-доской в трех колонках. "
        "Добавь по две примерные карточки в каждую колонку."
    )

    accepted = evaluate_ui_request(request, webui)
    assert accepted["ok"] is True

    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["widgets"] = [
        {
            "id": f"list-{index}",
            "type": "ui.list",
            "area": "main",
            "dataSource": {"kind": "static", "value": []},
        }
        for index in range(3)
    ]
    rejected = evaluate_ui_request(request, webui)

    assert rejected["ok"] is False
    assert rejected["postconditions"][0] == {
        "id": "kanban.component",
        "ok": False,
        "expected": "one collection.board",
        "actual": 0,
    }


def _resource_board_webui() -> dict:
    webui = _board_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"] = {"searchQuery": "", "selectedRecordId": ""}
    board = page["widgets"][0]
    board["inputs"]["dragDrop"] = True
    board["inputs"]["buttons"] = [
        {"id": "edit", "label": "Edit"},
        {"id": "delete", "label": "Delete"},
    ]
    board["dataSource"] = {
        "kind": "resourceQuery",
        "resourceType": "prototype.work_items",
        "query": {"search": "$state.searchQuery"},
    }
    board["actions"] = [
        {
            "on": "select",
            "type": "updateState",
            "params": {"selectedRecordId": "$event.id"},
        },
        {
            "on": "move",
            "type": "resourceOperation",
            "target": "prototype.work_items",
            "params": {
                "operation_id": "update",
                "record_id": "$event.id",
                "payload": "$event.patch",
            },
        },
        {
            "on": "click:delete",
            "type": "resourceOperation",
            "target": "prototype.work_items",
            "params": {"operation_id": "delete", "record_id": "$event.id"},
        },
    ]
    page["widgets"].extend(
        [
            {
                "id": "search",
                "type": "input.text",
                "area": "main",
                "actions": [
                    {
                        "on": "change",
                        "type": "updateState",
                        "params": {"searchQuery": "$event.value"},
                    }
                ],
            },
            {
                "id": "create",
                "type": "ui.form",
                "area": "main",
                "inputs": {
                    "fields": [
                        {"id": "title", "type": "text"},
                        {
                            "id": "status",
                            "type": "select",
                            "options": [
                                {"label": "Planned", "value": "planned"},
                                {"label": "Doing", "value": "doing"},
                                {"label": "Done", "value": "done"},
                            ],
                        },
                    ]
                },
                "actions": [
                    {
                        "on": "submit",
                        "type": "resourceOperation",
                        "target": "prototype.work_items",
                        "params": {"operation_id": "create", "payload": "$event.values"},
                    }
                ],
            },
            {
                "id": "edit",
                "type": "ui.form",
                "area": "main",
                "inputs": {"fields": [{"id": "title", "type": "text"}]},
                "actions": [
                    {
                        "on": "submit",
                        "type": "resourceOperation",
                        "target": "prototype.work_items",
                        "params": {
                            "operation_id": "update",
                            "record_id": "$state.selectedRecordId",
                            "payload": "$event.values",
                        },
                    }
                ],
            },
        ]
    )
    return webui


def test_resource_board_evaluation_requires_executable_query_and_crud_flows() -> None:
    request = (
        "Show a kanban board with three columns and two cards in each column. "
        "Add search, create, edit, delete, and drag and drop."
    )
    records = [
        {"id": f"{lane}-{index}", "title": f"Task {index}", "status": lane}
        for lane in ("planned", "doing", "done")
        for index in range(2)
    ]
    webui = _resource_board_webui()

    accepted = evaluate_ui_request(request, webui, prototype_records=records)

    assert accepted["ok"] is True
    assert all(item["ok"] for item in accepted["postconditions"])

    board = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]
    board["dataSource"]["query"] = {}
    board["actions"].append(
        {
            "on": "add",
            "type": "resourceOperation",
            "target": "prototype.work_items",
            "params": {"operation_id": "create", "payload": "$event.payload"},
        }
    )
    for widget in webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]:
        if widget.get("id") in {"create", "edit"}:
            widget["actions"] = []

    rejected = evaluate_ui_request(request, webui, prototype_records=records)
    failed_ids = {item["id"] for item in rejected["postconditions"] if not item["ok"]}
    finding_codes = {item["code"] for item in rejected["capability_validation"]["findings"]}

    assert rejected["ok"] is False
    assert {"kanban.query_binding", "kanban.create_form", "kanban.edit_form"} <= failed_ids
    assert "ui.board.create_event_invalid" in finding_codes


def test_resource_board_query_binding_rejects_nested_event_object() -> None:
    webui = _resource_board_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    search = next(widget for widget in page["widgets"] if widget.get("id") == "search")
    search["actions"][0]["params"] = {
        "searchQuery": {"search": "$event.value"}
    }

    result = evaluate_ui_request(
        "Show a kanban board with search, create, edit, delete, and drag and drop.",
        webui,
        prototype_records=[],
    )

    query_binding = next(
        item for item in result["postconditions"] if item["id"] == "kanban.query_binding"
    )
    assert query_binding["ok"] is False
    assert query_binding["actual"]["executableRefs"] == []


def test_resource_query_requires_initial_state_for_query_references() -> None:
    webui = _resource_board_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page.pop("initialState")

    result = validate_webui_capabilities(webui)

    finding = next(
        item
        for item in result["findings"]
        if item["code"] == "ui.resource_query.state_uninitialized"
    )
    assert result["ok"] is False
    assert "searchQuery" in finding["message"]


def test_modal_board_editor_selects_record_on_the_opening_event() -> None:
    webui = _resource_board_webui()
    application = webui["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    edit_form = next(widget for widget in page["widgets"] if widget.get("id") == "edit")
    page["widgets"] = [widget for widget in page["widgets"] if widget.get("id") != "edit"]
    application["modals"] = {
        "edit-item": {
            "id": "edit-item",
            "schema": {
                "id": "edit-item-schema",
                "layout": {
                    "type": "single",
                    "areas": [{"id": "main", "role": "main"}],
                },
                "widgets": [edit_form],
            },
        }
    }
    board = page["widgets"][0]
    board["actions"].append(
        {
            "on": "click:edit",
            "type": "openModal",
            "params": {"modalId": "edit-item"},
        }
    )
    request = "Show a kanban board with search, create, edit, delete, and drag and drop."

    rejected = evaluate_ui_request(request, webui, prototype_records=[])
    rejected_selection = next(
        item for item in rejected["postconditions"] if item["id"] == "kanban.edit_selection"
    )
    assert rejected_selection["ok"] is False

    board["actions"].insert(
        -1,
        {
            "on": "click:edit",
            "type": "updateState",
            "params": {"selectedRecordId": "$event.id"},
        },
    )
    accepted = evaluate_ui_request(request, webui, prototype_records=[])
    accepted_selection = next(
        item for item in accepted["postconditions"] if item["id"] == "kanban.edit_selection"
    )
    assert accepted_selection["ok"] is True
