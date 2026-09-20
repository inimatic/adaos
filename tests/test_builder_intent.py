from __future__ import annotations

import pytest

from adaos.sdk.builder import intent as intent_sdk
from adaos.services.builder_intent import (
    capture_intent,
    compile_prototype_brief,
    merge_prototype_briefs,
)
from adaos.services.ui_capabilities import selected_ui_capabilities


@pytest.mark.parametrize("statement", [
    "Reference: https://example.test/api/search?sort=title&filter=open. Search items by title.",
    "Документация: https://example.test/api/search. Поиск по названию.",
    "Reference: scenario:project.search.v2. Search items by title.",
])
def test_reference_punctuation_and_words_are_not_operations(statement):
    from adaos.services.builder_intent import _clauses

    clauses = _clauses(statement)
    assert len(clauses) == 2
    assert all(statement[start:end] == clause for clause, start, end in clauses)
    brief = compile_prototype_brief(statement)
    assert [item["kind"] for item in brief["operations"]] == ["search"]
    assert len(brief["residual_requirements"]) == 1
    assert brief["residual_requirements"][0]["statement"] == clauses[0][0]


def test_inline_and_dotted_abi_identifiers_are_not_sentence_boundaries() -> None:
    from adaos.services.builder_intent import _clauses

    statement = (
        "Use `desktop-icons` / `collection.grid` and `desktop-widgets` / "
        "`desktop.widgets` for the launcher. Open items through "
        "desktop.scenario.set. Keep the existing application state."
    )

    clauses = _clauses(statement)

    assert [item[0] for item in clauses] == [
        "Use `desktop-icons` / `collection.grid` and `desktop-widgets` / "
        "`desktop.widgets` for the launcher",
        "Open items through desktop.scenario.set",
        "Keep the existing application state",
    ]
    assert all(statement[start:end] == clause for clause, start, end in clauses)


@pytest.mark.parametrize("statement", [
    "Stop for Prototype review after applying.",
    "This is a Prototype correction, not an Automation or publication request.",
])
def test_review_stop_is_a_retained_process_boundary(statement):
    from adaos.services.builder.prototype_context import prototype_process_constraints, prototype_requirement_inventory
    brief = compile_prototype_brief(statement)
    constraints = prototype_process_constraints(brief)
    assert constraints and all(item["verification_owner"] == "stage_boundary" for item in constraints)
    assert prototype_requirement_inventory(brief) == []


@pytest.mark.parametrize(
    "statement",
    [
        "The Prototype visibly and coherently satisfies: Preserve the current Users & Access webui.json exactly: keep widgets and actions unchanged.",
        "The Prototype visibly and coherently satisfies: Set dryRun to true on every MCP dataSource, as required by the read-only data-source ABI.",
        "The Prototype visibly and coherently satisfies: Make exactly one copy edit in widget role-guide, item owners: change the English content sentence 'Owner remains unique.' to 'The owner remains unique.' Do not add, remove, reorder, or rename any widget, field, action, binding, resource, locale key, or layout region.",
        "The Prototype visibly and coherently satisfies: In widget role-guide item owners, change only the English sentence 'Owner remains unique.' to 'The owner remains unique.'.",
        "The Prototype visibly and coherently satisfies: Produce one coherent Prototype revision containing only these validation and copy corrections.",
    ],
)
def test_source_revision_instructions_are_process_constraints(statement):
    from adaos.services.builder_intent import process_constraint_kind

    assert process_constraint_kind(statement) == "source_preservation"


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


def test_excluded_feature_list_does_not_become_crud_obligations() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    for statement in (
        "Show the work history. Do not implement storage, create records, or upload files.",
        "Покажи историю работы. Не реализуй хранение, создание записей или загрузку файлов.",
    ):
        qualified = qualify_ui_request(statement)
        brief = qualified["prototype_brief"]
        assert qualified["requirements"]["prototype_resource"] is False
        assert not ({"create", "update", "delete"} & {row["kind"] for row in brief["operations"]})
        assert len(brief["exclusions"]) == 1
        assert brief["problem"]["value"] == statement
        evidence = brief["exclusions"][0]["evidence"][0].split("=")[1]
        start, end = map(int, evidence.split(":"))
        assert statement[start:end] == brief["exclusions"][0]["statement"]
    brief = compile_prototype_brief("Do not implement uploads, but create and edit records.")
    assert {"create", "update"} <= {row["kind"] for row in brief["operations"]}


def test_source_preservation_directive_does_not_become_crud_obligations() -> None:
    statement = (
        "Change the owner guide sentence. Do not add, remove, reorder, or rename "
        "any widget, field, action, binding, resource, locale key, or layout region."
    )

    brief = compile_prototype_brief(statement)

    assert [row["kind"] for row in brief["operations"]] == ["update"]
    assert [row["statement"] for row in brief["exclusions"]] == [
        "Do not add, remove, reorder, or rename any widget, field, action, binding, "
        "resource, locale key, or layout region"
    ]
    assert all(
        "Do not add" not in row["statement"]
        for row in [*brief["principal_jobs"], *brief["residual_requirements"]]
    )


def test_declarative_static_record_correction_does_not_require_resource_crud() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    statement = (
        "Give each static attention record an actionMessage field. Replace two "
        "updateState actions for click:open with one action that writes "
        "lastSystemAction from $event.actionMessage."
    )

    qualified = qualify_ui_request(statement)

    assert qualified["requirements"]["prototype_resource"] is False
    assert qualified["requirements"]["resource_mutations"] is False
    assert qualified["requirements"]["resource_scope_needs_interpretation"] is False


def test_declarative_ui_additions_do_not_require_resource_crud() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    qualified = qualify_ui_request(
        "Redesign the current WebUI prototype. Add category filtering for the "
        "Marketplace and add a separate apps-marketplace ui.list with an iconKey. "
        "Declare a compact sheet with an explicit close/back path. Show installed "
        "applications as one collection and Marketplace as a second catalog."
    )

    assert qualified["requirements"]["prototype_resource"] is False
    assert qualified["requirements"]["resource_scope_needs_interpretation"] is False


def test_explicit_no_persistence_boundary_wins_over_ambiguous_ui_verbs() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    qualified = qualify_ui_request(
        "Complete these remaining prototype UI items. Mark the current endpoint "
        "online and replace the navigation. Do not create domain persistence or "
        "Prototype resources."
    )

    assert qualified["requirements"]["resource_mutations"] is False
    assert qualified["requirements"]["prototype_resource"] is False
    assert qualified["requirements"]["resource_scope_needs_interpretation"] is False


def test_end_user_record_crud_still_requires_prototype_resources() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    qualified = qualify_ui_request(
        "Allow a coordinator to create, edit, and archive inspection records."
    )

    assert qualified["requirements"]["prototype_resource"] is True
    assert qualified["requirements"]["resource_mutations"] is True
    assert qualified["requirements"]["resource_scope_needs_interpretation"] is False


def test_ambiguous_mutation_is_left_for_brief_interpretation() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    qualified = qualify_ui_request("Add a priority and change it later.")

    assert qualified["requirements"]["resource_mutations"] is False
    assert qualified["requirements"]["prototype_resource"] is False
    assert qualified["requirements"]["resource_scope_needs_interpretation"] is True


def test_additional_condition_is_not_parsed_as_add_operation() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    statement = (
        "For each named widget, replace only the activeTab equality value in "
        "visibleIf. Preserve every additional condition."
    )

    qualified = qualify_ui_request(statement)

    assert "create" not in qualified["requirements"]["brief_operation_kinds"]
    assert qualified["requirements"]["prototype_resource"] is False


def test_ui_schema_maintenance_does_not_become_resource_crud() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request

    qualified = qualify_ui_request(
        "The tile itself is the launch action; add no Open button and no static "
        "records. Remove the Applications button from primary-nav and remove "
        "the apps-variant layout. It must not change activeTab or navigate away."
    )

    assert qualified["requirements"]["resource_mutations"] is False
    assert qualified["requirements"]["prototype_resource"] is False
    assert "create" not in qualified["requirements"]["brief_operation_kinds"]
    assert "update" not in qualified["requirements"]["brief_operation_kinds"]


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


def test_no_required_mutations_and_contrast_keep_positive_requests():
    for statement in (
        'Show read-only cards. No editing, calculations or external services are needed.',
        'No editing is needed but show the cards.',
        'Editing is not needed but show the cards.',
        'Show the cards but no editing is needed.',
    ):
        brief = compile_prototype_brief(statement)
        assert 'update' not in {item['kind'] for item in brief['operations']}
        assert 'list' in {item['kind'] for item in brief['operations']}
        assert brief['exclusions']


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


def test_dashboard_refinement_does_not_invent_create_or_transition_jobs() -> None:
    brief = compile_prototype_brief(
        "Refine the dashboard. Do not add another top bar. "
        "A command may update bounded local prototype state; do not represent it "
        "as an unused record field. Show the complete Home hierarchy."
    )

    kinds = {row["kind"] for row in brief["operations"]}
    assert "create" not in kinds
    assert "transition" not in kinds
    assert [row["statement"] for row in brief["exclusions"]] == [
        "Do not add another top bar",
        "do not represent it as an unused record field",
    ]


def test_dashboard_fixture_refinement_does_not_treat_record_nouns_as_create() -> None:
    brief = compile_prototype_brief(
        "Preserve all working content and synthetic records. "
        "Do not merge the peer regions and do not add widgets. "
        "Give every application record separate purpose and lastActivity fields. "
        "Add an actionLabel field to each attention record."
    )

    operations = [(row["kind"], row["statement"]) for row in brief["operations"]]
    assert all("Do not add" not in statement for _kind, statement in operations)
    assert operations == [
        ("create", "Add an actionLabel field to each attention record")
    ]


def test_prototype_copy_does_not_turn_update_nouns_or_negative_effects_into_jobs() -> None:
    brief = compile_prototype_brief(
        "Seed installed records with an update available and an update policy. "
        "Selecting a card reveals that record in the detail region. "
        "Do not imply that prototype actions changed a real installation."
    )

    operations = [(row["kind"], row["statement"]) for row in brief["operations"]]
    assert operations == [
        (
            "inspect",
            "Seed installed records with an update available and an update policy",
        )
    ]
