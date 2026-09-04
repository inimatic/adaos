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
