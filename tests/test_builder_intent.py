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


def test_brief_preserves_ru_operations_after_authoring_prefix_in_same_clause() -> None:
    statement = (
        "Создай приложение для учета заявок: добавлять, назначать, "
        "менять статус и закрывать."
    )

    brief = compile_prototype_brief(statement)

    assert [item["kind"] for item in brief["operations"]] == [
        "create",
        "assign",
        "transition",
    ]
    assert brief["principal_jobs"] == [
        {
            "id": "job:01",
            "statement": (
                "для учета заявок: добавлять, назначать, менять статус и закрывать"
            ),
            "evidence": ["intent.statement"],
            "confidence": 1.0,
        }
    ]
    assert "operations" not in brief["interpretation"]["unresolved_fields"]
    for operation in brief["operations"]:
        offsets = operation["evidence"][0].removeprefix("intent.statement#char=")
        start, end = (int(value) for value in offsets.split(":"))
        assert statement[start:end] == operation["statement"]


def test_ru_builder_authoring_is_not_an_application_create_operation() -> None:
    brief = compile_prototype_brief('Создай новое приложение "Заявки" для команды.')

    assert brief["operations"] == []
    assert brief["principal_jobs"] == []


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
        "recipe.resource_collection_workbench",
        "recipe.master_detail",
        "recipe.data_entry",
    } <= set(selection["root_item_ids"])
    assert "recipe.resource_board_workbench" not in selection["root_item_ids"]
    assert selection["qualification"]["prototype_brief"]["intent_ref"].startswith(
        "intent:"
    )


def test_brief_preserves_implicit_lifecycle_and_exception_states() -> None:
    brief = compile_prototype_brief(
        "A coordinator saves an unfinished request and then completes it. "
        "Show an empty queue and prevent completion without an owner."
    )

    assert brief["representative_states"] == {
        "state": "known",
        "value": [
            "A coordinator saves an unfinished request and then completes it",
            "Show an empty queue and prevent completion without an owner",
        ],
        "evidence": ["intent.statement"],
        "confidence": 0.8,
    }


def test_brief_preserves_explicit_attachment_capture_in_en_and_ru() -> None:
    statements = (
        "A technician adds a measurement and uploads a defect photo.",
        "\u0422\u0435\u0445\u043d\u0438\u043a \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0435\u0442 \u0438\u0437\u043c\u0435\u0440\u0435\u043d\u0438\u0435, \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439 \u0438 \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u044e \u0434\u0435\u0444\u0435\u043a\u0442\u0430.",
    )

    for statement in statements:
        brief = compile_prototype_brief(statement)
        requirement = brief["information_requirements"][0]
        assert requirement["kind"] == "attachment"
        assert requirement["interaction"] == "capture"
        start, end = (
            int(value)
            for value in requirement["evidence"][0]
            .removeprefix("intent.statement#char=")
            .split(":")
        )
        assert statement[start:end] == requirement["statement"]


def test_brief_preserves_ru_lifecycle_and_exception_states_without_domain_terms() -> (
    None
):
    brief = compile_prototype_brief(
        "Пользователь сохраняет незавершенную запись, а затем завершает ее. "
        "Покажи пустой список и запрети завершение без комментария."
    )

    assert brief["representative_states"]["state"] == "known"
    assert brief["representative_states"]["value"] == [
        "Пользователь сохраняет незавершенную запись, а затем завершает ее",
        "Покажи пустой список и запрети завершение без комментария",
    ]


def test_public_builder_sdk_exposes_brief_compilation() -> None:
    captured = intent_sdk.capture("List entries and filter them.", locale="en")
    brief = intent_sdk.compile_brief(captured)

    assert [item["kind"] for item in brief["operations"]] == ["filter", "list"]
    assert brief["constraints"]["locale"]["value"] == "en"
