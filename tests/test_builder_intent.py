from __future__ import annotations

import pytest

from adaos.sdk.builder import intent as intent_sdk
from adaos.services.builder_intent import (
    capture_intent,
    compile_prototype_brief,
    merge_prototype_briefs,
)
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
    assert [item["statement"] for item in brief["principal_jobs"]] == [
        "Team members need to scan work",
        "open one item",
        "add a request",
        "assign it",
        "move it through New, In progress, Blocked and Done",
    ]
    for job in brief["principal_jobs"]:
        offsets = job["evidence"][0].removeprefix("intent.statement#char=")
        start, end = (int(value) for value in offsets.split(":"))
        assert brief["problem"]["value"][start:end] == job["statement"]


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
        "transition",
    ]
    assert [item["statement"] for item in brief["principal_jobs"]] == [
        "для учета заявок: добавлять",
        "назначать",
        "менять статус",
        "закрывать",
    ]
    for job in brief["principal_jobs"]:
        offsets = job["evidence"][0].removeprefix("intent.statement#char=")
        start, end = (int(value) for value in offsets.split(":"))
        assert statement[start:end] == job["statement"]
    assert "operations" not in brief["interpretation"]["unresolved_fields"]
    for operation in brief["operations"]:
        offsets = operation["evidence"][0].removeprefix("intent.statement#char=")
        start, end = (int(value) for value in offsets.split(":"))
        assert statement[start:end] == operation["statement"]


def test_ru_builder_authoring_is_not_an_application_create_operation() -> None:
    brief = compile_prototype_brief('Создай новое приложение "Заявки" для команды.')

    assert brief["operations"] == []
    assert brief["principal_jobs"] == []


@pytest.mark.parametrize("prefix,suffix", [
    ('Create a new application named "Create screen [TEST]-001"', 'for a small team'),
    ('Build an app called "Review records"', 'for volunteers'),
    ('Создай приложение "Добавить экран [TEST]-001"', 'для мастерской'),
    ('Сделай приложение с названием «Записать заявки»', 'для команды'),
])
def test_quoted_authoring_title_is_not_a_job_but_audience_is_retained(prefix, suffix) -> None:
    statement = f"{prefix} {suffix}."
    brief = compile_prototype_brief(statement)
    assert brief["problem"]["value"] == statement
    assert brief["operations"] == []
    assert brief["principal_jobs"] == []
    assert [item["statement"] for item in brief["residual_requirements"]] == [suffix]


def test_application_title_changes_do_not_change_residual_requirement_identity() -> None:
    briefs = [compile_prototype_brief(f'Create an application named "App {uid}" for a team.') for uid in ("001", "002")]
    assert briefs[0]["residual_requirements"][0]["id"] == briefs[1]["residual_requirements"][0]["id"]
    assert briefs[0]["problem"] != briefs[1]["problem"]
    business = compile_prototype_brief('Create a record named "Customer" and edit its address.')
    assert {item["kind"] for item in business["operations"]} == {"create", "update"}


def test_ru_actor_and_loading_state_do_not_invent_mutating_operations() -> None:
    brief = compile_prototype_brief('Создай приложение для редактора. Редактор должен открывать файл. Покажи загрузку и недоступный файл.')
    assert {item["kind"] for item in brief["operations"]} == {"inspect", "list"}
    assert brief["information_requirements"] == []
    assert all(item["statement"] != "Редактор должен" for item in brief["principal_jobs"])
    for instruction in ("Загрузи файл", "Прикрепить фото", "Позволь загружать документы"):
        assert compile_prototype_brief(instruction)["information_requirements"]


def test_attachment_capture_does_not_match_comment_about_an_image() -> None:
    for statement in ("Добавить замечание: текст, время в секундах или описание области изображения.", "Add a note about the image."):
        assert not compile_prototype_brief(statement)["information_requirements"]


def test_explicit_exclusions_are_preserved_without_operations_or_automation_debt() -> None:
    from adaos.services.builder.prototype_context import compile_prototype_model_context
    statement = "Покажи записи. Отправка сообщений наружу пока не требуется."
    brief = compile_prototype_brief(statement)
    assert [item["kind"] for item in brief["operations"]] == ["list"]
    assert brief["exclusions"][0]["statement"] == "Отправка сообщений наружу пока не требуется"
    assert brief["problem"]["value"] == statement
    context = compile_prototype_model_context(brief, compact=True)
    assert context["exclusions"]
    assert all("Отправка" not in item["statement"] for item in context["required_references"])
    assert merge_prototype_briefs(brief, compile_prototype_brief("Open a record."))["exclusions"]
    brief = compile_prototype_brief("Show records but uploads are not required.")
    assert [item["kind"] for item in brief["operations"]] == ["list"]
    assert brief["exclusions"][0]["statement"] == "uploads are not required"


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
        "confidence": 0.9,
    }


def test_brief_combines_workflow_and_later_explicit_visual_states() -> None:
    brief = compile_prototype_brief(
        "Move work through New, In progress, Blocked and Done. "
        "Include realistic examples and clear empty and overdue states."
    )

    assert brief["representative_states"]["value"] == [
        "New",
        "In progress",
        "Blocked",
        "Done",
        "empty",
        "overdue states",
    ]
    assert "open one item without losing the queue" not in brief[
        "representative_states"
    ]["value"]



def test_brief_splits_alternative_operations_and_explicit_staffing_states() -> None:
    brief = compile_prototype_brief(
        "A coordinator needs to review volunteers and their availability, see "
        "coverage gaps, assign or remove a person, and open contact details. "
        "Include clear states for no volunteers, an unfilled shift, a fully staffed "
        "day and conflicting availability."
    )

    assert [item["statement"] for item in brief["principal_jobs"]] == [
        "A coordinator needs to review volunteers and their availability",
        "see coverage gaps",
        "assign",
        "remove a person",
        "open contact details",
    ]
    assert brief["representative_states"]["value"] == [
        "no volunteers",
        "unfilled shift",
        "fully staffed day",
        "conflicting availability",
    ]

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


def test_brief_preserves_repeated_collection_cardinality() -> None:
    statement = (
        "Build a repeatable list of checks and let the technician record a result "
        "for each check."
    )

    brief = compile_prototype_brief(statement)

    assert brief["collection_requirements"] == [
        {
            "id": "collection:01",
            "kind": "repeated_collection",
            "interaction": "capture_each",
            "statement": statement.removesuffix("."),
            "evidence": [f"intent.statement#char=0:{len(statement) - 1}"],
            "confidence": 1.0,
        }
    ]
    selection = selected_ui_capabilities(statement)
    assert selection["qualification"]["requirements"][
        "brief_collection_requirements"
    ] == [
        {
            "id": "collection:01",
            "kind": "repeated_collection",
            "interaction": "capture_each",
            "statement": statement.removesuffix("."),
        }
    ]


def test_brief_does_not_turn_ru_nouns_and_field_capture_into_crud() -> None:
    statement = (
        "Техник проходит повторяемый список проверок, отмечает результат каждого "
        "пункта, добавляет измерение и фотографию дефекта. Покажи состояние без "
        "назначенных осмотров и затем заверши осмотр."
    )

    brief = compile_prototype_brief(statement)

    assert [item["kind"] for item in brief["operations"]] == ["list", "transition"]
    assert brief["collection_requirements"][0]["interaction"] == "capture_each"
    assert brief["information_requirements"][0]["kind"] == "attachment"


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
        "Покажи пустой список",
        "запрети завершение без комментария",
    ]


def test_public_builder_sdk_exposes_brief_compilation() -> None:
    captured = intent_sdk.capture("List entries and filter them.", locale="en")
    brief = intent_sdk.compile_brief(captured)

    assert [item["kind"] for item in brief["operations"]] == ["list", "filter"]
    assert brief["constraints"]["locale"]["value"] == "en"


def test_brief_keeps_finding_an_item_independent_of_a_text_search_control() -> None:
    brief = compile_prototype_brief(
        "Пользователь находит нужный элемент и переносит его на другую дату."
    )

    assert [item["kind"] for item in brief["operations"]] == ["inspect", "update"]


def test_brief_does_not_treat_russian_record_noun_as_create_operation() -> None:
    brief = compile_prototype_brief(
        "Пользователь находит нужную запись и просматривает ее содержание."
    )

    assert [item["kind"] for item in brief["operations"]] == ["inspect", "inspect"]


def test_finding_an_outcome_does_not_require_text_search() -> None:
    for statement in (
        "Find available time and view the schedule.",
        "Find the cheapest item and filter by category.",
        "Находить свободное время и просматривать расписание.",
        "Найти подходящий элемент и фильтровать по категории.",
    ):
        brief = compile_prototype_brief(statement)
        assert "inspect" in {item["kind"] for item in brief["operations"]}
        assert "search" not in {item["kind"] for item in brief["operations"]}


def test_explicit_search_remains_a_supported_operation() -> None:
    for statement in ("Search items by title.", "Поиск элементов по названию."):
        brief = compile_prototype_brief(statement)
        assert "search" in {item["kind"] for item in brief["operations"]}


def test_query_state_description_is_not_an_additional_search_operation() -> None:
    for statement in (
        "Search items by title. Include an empty-search state.",
        "Search items by title and include a search error state.",
        "Поиск элементов по названию. Покажи состояние пустого поиска.",
    ):
        brief = compile_prototype_brief(statement)
        assert sum(item['kind'] == 'search' for item in brief['operations']) == 1
        assert brief['representative_states']['state'] == 'known'
        assert brief['representative_states']['value']


def test_brief_preserves_unclassified_explicit_requirements() -> None:
    statement = (
        "Make the first version usable now. We need to record income and expenses; "
        "see the current balance and category totals; compare this month with the "
        "previous month; set monthly category limits."
    )

    brief = compile_prototype_brief(statement)

    assert [item["statement"] for item in brief["residual_requirements"]] == [
        "compare this month with the previous month",
        "set monthly category limits",
    ]
    assert "see the current balance and category totals" in [
        item["statement"] for item in brief["principal_jobs"]
    ]
    for requirement in brief["residual_requirements"]:
        offsets = requirement["evidence"][0].removeprefix(
            "intent.statement#char="
        )
        start, end = (int(value) for value in offsets.split(":"))
        assert statement[start:end] == requirement["statement"]


def test_brief_excludes_russian_prototype_authoring_directive() -> None:
    brief = compile_prototype_brief(
        "Покажи рабочую первую версию. Пользователь сравнивает варианты."
    )

    assert [item["statement"] for item in brief["residual_requirements"]] == [
        "Пользователь сравнивает варианты"
    ]


def test_accepted_brief_merge_preserves_project_origin_and_current_jobs() -> None:
    origin = compile_prototype_brief(
        'Создай новое приложение "Запасы" для небольшой мастерской.'
    )
    design = compile_prototype_brief(
        "Нужно видеть позиции ниже минимального остатка и фильтровать их по категории."
    )

    merged = merge_prototype_briefs(origin, design)
    repeated = merge_prototype_briefs(origin, design, origin)

    assert merged == repeated
    assert merged["brief_id"].startswith("brief:")
    assert [item["kind"] for item in merged["operations"]] == ["filter"]
    assert merged["operations"][0]["id"].startswith("operation:")
    assert any(
        "небольшой мастерской" in item["statement"]
        for item in merged["residual_requirements"]
    )
    assert all(
        evidence.startswith("brief:")
        for item in merged["residual_requirements"]
        for evidence in item["evidence"]
    )
