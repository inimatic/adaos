from __future__ import annotations

from adaos.sdk.developer import ui


def test_ui_sdk_selects_a_bounded_kanban_contract_without_runtime_context() -> None:
    selected = ui.select("Покажи задачи канбан-доской в трех колонках")

    assert selected["qualification"]["requirements"]["lane_count"] == 3
    assert {item["id"] for item in selected["items"]} == {
        "recipe.kanban_board",
        "collection.board",
        "layout.flow",
    }


def test_ui_sdk_exposes_board_drag_drop_as_a_typed_requirement() -> None:
    qualification = ui.qualify("Добавь перетаскивание карточек на канбан-доске")

    assert qualification["ready"] is True
    assert qualification["requirements"]["drag_drop"] is True
