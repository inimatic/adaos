from __future__ import annotations

from adaos.sdk.builder import intent as intent_sdk
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from adaos.services.ui_capabilities import selected_ui_capabilities


def test_intent_capture_is_exact_content_addressed_evidence() -> None:
    statement = "Покажи заявки и позволь назначить ответственного."

    first = capture_intent(
        statement,
        source={"kind": "e2e", "turn_id": "turn-01"},
        project_ref="project:test",
    )
    second = capture_intent(
        statement,
        source={"kind": "e2e", "turn_id": "turn-01"},
        project_ref="project:test",
    )

    assert first == second
    assert first["statement"] == statement
    assert first["locale"] == "ru"
    assert first["intent_id"].startswith("intent:")
    assert first["digest"].startswith("sha256:")
    assert first["authority"] == {
        "runtime_scope": "prototype",
        "external_effects": "unknown",
        "destructive_effects": "unknown",
    }


def test_brief_separates_builder_authoring_from_in_application_operations() -> None:
    brief = compile_prototype_brief(
        'Create a new application named "Operations Queue". '
        "Show a useful initial prototype. Team members need to scan work, "
        "open one item, add a request, assign it and move it through New, "
        "In progress, Blocked and Done."
    )

    kinds = {item["kind"] for item in brief["operations"]}
    assert kinds == {"inspect", "create", "assign", "transition"}
    assert all(
        "Create a new application" not in item["statement"]
        for item in brief["operations"]
    )
    assert brief["representative_states"]["state"] == "known"
    assert brief["representative_states"]["value"] == [
        "New",
        "In progress",
        "Blocked",
        "Done",
    ]
    assert brief["entities"]["state"] == "unknown"
    assert "entities" in brief["interpretation"]["unresolved_fields"]


def test_brief_drives_generic_capabilities_without_internal_prompt_terms() -> None:
    selection = selected_ui_capabilities(
        "Team members need to scan work, open one item, add a request, "
        "assign it and move it through New, In progress, Blocked and Done."
    )

    assert selection["qualification"]["surface_kind"] == "interactive_collection"
    assert selection["qualification"]["requirements"]["brief_operation_kinds"] == [
        "inspect",
        "create",
        "assign",
        "transition",
    ]
    assert {
        "recipe.resource_board_workbench",
        "recipe.master_detail",
        "recipe.data_entry",
    } <= set(selection["root_item_ids"])
    assert selection["qualification"]["prototype_brief"]["intent_ref"].startswith(
        "intent:"
    )


def test_public_builder_sdk_exposes_brief_compilation() -> None:
    captured = intent_sdk.capture("List entries and filter them.", locale="en")
    brief = intent_sdk.compile_brief(captured)

    assert [item["kind"] for item in brief["operations"]] == ["filter", "list"]
    assert brief["constraints"]["locale"]["value"] == "en"
