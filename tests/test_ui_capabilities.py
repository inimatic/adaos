from __future__ import annotations

import copy

from adaos.services.builder.domain_packs.application_manager_legacy import (
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
                            "version": 2,
                            "pattern": "board",
                            "density": "comfortable",
                            "regions": [
                                {
                                    "id": "main",
                                    "role": "main",
                                    "presentation": {"wide": "pane", "compact": "overflow"},
                                }
                            ],
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


def test_catalog_admits_navigation_disclosure_and_typed_form_controls() -> None:
    catalog = ui_capability_catalog()
    component_ids = {item["id"] for item in catalog["components"]}

    assert catalog["catalog_version"] == "3.3.9"
    assert {
        "navigation.tabs",
        "navigation.breadcrumbs",
        "disclosure.accordion",
    } <= component_ids
    form = get_ui_capability("ui.form")
    assert {"dateRange", "chips", "checkboxGrid", "staticContent"} <= set(
        form["manifest"]["supported_field_types"]
    )
    actions = get_ui_capability("ui.actions")
    assert "autoOverflow" in actions["interactions"]["overflow"]
    assert actions["manifest"]["button_kind_values"] == [
        "primary",
        "secondary",
        "danger",
    ]
    assert actions["manifest"]["button_overflow_values"] == ["auto", "never"]
    assert "passive status text" in actions["manifest"]["scope"]
    list_capability = get_ui_capability("ui.list")
    assert "separate sibling ui.actions" in list_capability["manifest"]["button_shape"]
    assert "object keyed by the literal modal id" in form["manifest"]["modal_composition"]
    assert "params:{modalId:'<literal-id>'}" in form["manifest"]["modal_composition"]


def test_natural_typed_form_request_selects_form_capability() -> None:
    selected = selected_ui_capabilities(
        "Add a typed form in an invite-person modal with email and role fields."
    )

    assert "ui.form" in selected["root_item_ids"]


def test_dashboard_status_informers_select_metric_tile_and_dashboard() -> None:
    from adaos.services.ui_capabilities import (
        selected_ui_capabilities as select_generic_capabilities,
    )

    selected = select_generic_capabilities(
        "Replace the tall summary with four compact status informers in separate dashboard regions."
    )

    assert "visual.metricTile" in selected["root_item_ids"]
    assert "layout.dashboard" in selected["root_item_ids"]
    metric_tile = get_ui_capability("visual.metricTile")
    assert "without relying on color" in metric_tile["manifest"]["static_data"]


def test_ui_revision_correction_does_not_require_domain_persistence() -> None:
    from adaos.services.ui_capabilities import qualify_ui_request as qualify_generic

    request = (
        "Correct only the current Web Desktop revision. Add five keys to the page "
        "initialState, remove Invite button metadata, add one sibling ui.actions "
        "widget, and make both form submit steps use the same action id."
    )

    qualification = qualify_generic(request)
    assert qualification["requirements"]["prototype_resource"] is False


def test_developer_ui_contract_validation_exposes_unreachable_form_step() -> None:
    from adaos.sdk.developer import ui as developer_ui

    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "main",
                        "layout": {
                            "version": 2,
                            "pattern": "document",
                            "density": "comfortable",
                            "contentWidth": "fluid",
                            "scroll": "page",
                            "regions": [],
                            "interaction": {},
                        },
                        "widgets": [],
                    }
                },
                "modals": {
                    "invite": {
                        "schema": {
                            "id": "invite",
                            "layout": {
                                "version": 2,
                                "pattern": "document",
                                "density": "comfortable",
                                "contentWidth": "fluid",
                                "scroll": "page",
                                "regions": [],
                                "interaction": {},
                            },
                            "widgets": [
                                {
                                    "id": "invite-form",
                                    "type": "ui.form",
                                    "inputs": {
                                        "fields": [{"id": "email", "type": "email"}],
                                        "buttons": [{"id": "submit", "label": "Submit"}],
                                    },
                                    "actions": [
                                        {"id": "submit", "on": "submit", "type": "updateState", "params": {"saved": True}},
                                        {"id": "submit-close", "on": "submit", "type": "closeModal"},
                                    ],
                                }
                            ],
                        }
                    }
                },
            }
        },
    }

    result = developer_ui.validate_contract(webui)
    assert result["ok"] is False
    assert result["error"] == "webui_contract_invalid"
    assert result["findings"][0]["code"] == "webui.form.submit_action_unreachable"


def test_capability_validation_rejects_conflicting_state_writes_for_one_event() -> None:
    from adaos.services.ui_capabilities import (
        validate_webui_capabilities as validate_generic_capabilities,
    )

    webui = _board_webui()
    board = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]
    board["actions"] = [
        {
            "id": "open-first",
            "on": "click:open",
            "type": "updateState",
            "params": {"lastAction": "first"},
        },
        {
            "id": "open-second",
            "on": "click:open",
            "type": "updateState",
            "params": {"lastAction": "second"},
        },
    ]

    result = validate_generic_capabilities(webui)

    assert result["ok"] is False
    finding = next(
        item
        for item in result["findings"]
        if item["code"] == "ui.action.conflicting_state_writes"
    )
    assert finding["action_indexes"] == [0, 1]


def test_capability_validation_allows_composed_actions_for_one_event() -> None:
    from adaos.services.ui_capabilities import (
        validate_webui_capabilities as validate_generic_capabilities,
    )

    webui = _board_webui()
    board = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]
    board["actions"] = [
        {
            "id": "save",
            "on": "submit",
            "type": "updateState",
            "params": {"saved": True},
        },
        {"id": "close", "on": "submit", "type": "closeModal"},
    ]

    result = validate_generic_capabilities(webui)

    assert not any(
        item["code"] == "ui.action.conflicting_state_writes"
        for item in result["findings"]
    )


def test_capability_validation_rejects_unreachable_layout_variant_state() -> None:
    from adaos.services.ui_capabilities import (
        validate_webui_capabilities as validate_generic_capabilities,
    )

    webui = _board_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["layout"]["variants"] = [
        {
            "id": "development",
            "when": "$state.activeTab === 'development'",
            "pattern": "document",
            "density": "comfortable",
            "regions": [],
        }
    ]
    page["widgets"].insert(
        0,
        {
            "id": "navigation",
            "type": "navigation.tabs",
            "inputs": {
                "selectedStateKey": "$state.activeTab",
                "buttons": [
                    {"id": "home", "label": "Home"},
                    {"id": "dev", "label": "Development"},
                ],
            },
            "actions": [
                {
                    "on": "click:home",
                    "type": "updateState",
                    "params": {"activeTab": "home"},
                },
                {
                    "on": "click:dev",
                    "type": "updateState",
                    "params": {"activeTab": "dev"},
                },
            ],
        },
    )
    page["widgets"][1]["visibleIf"] = "$state.activeTab === 'development'"

    result = validate_generic_capabilities(webui)

    finding = next(
        item
        for item in result["findings"]
        if item["code"] == "ui.layout.variant_state_unreachable"
    )
    assert finding["expected"] == "development"
    assert finding["reachable_values"] == ["dev", "home"]
    assert any(
        item["code"] == "ui.component.visible_state_unreachable"
        for item in result["findings"]
    )

    page["layout"]["variants"][0]["when"] = "$state.activeTab === 'dev'"
    page["widgets"][1]["visibleIf"] = "$state.activeTab === 'dev'"
    fixed = validate_generic_capabilities(webui)
    assert not {
        "ui.layout.variant_state_unreachable",
        "ui.component.visible_state_unreachable",
    } & {item["code"] for item in fixed["findings"]}


def test_multilingual_search_selects_kanban_recipe() -> None:
    result = search_ui_capabilities("Покажи задачи канбан-доской в трех колонках")

    assert any(item["id"] == "recipe.kanban_board" for item in result["items"])
    selected = selected_ui_capabilities("Покажи задачи канбан-доской в трех колонках")
    assert {item["id"] for item in selected["items"]} == {
        "recipe.kanban_board",
        "collection.board",
        "layout.board",
    }
    assert selected["root_item_ids"] == [
        "recipe.kanban_board",
        "collection.board",
        "layout.board",
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
        "layout.collection-detail",
        "input.text",
        "input.selector",
        "ui.form",
        "item.details",
    } <= selected_ids
    assert {
        "layout.collection-detail",
        "input.text",
        "input.selector",
        "ui.form",
        "item.details",
    } <= set(selected["dependency_closure"])


def test_application_manager_selection_exposes_mcp_master_detail_contract() -> None:
    selected = selected_ui_capabilities(
        "Create Applications, a full-screen application lifecycle manager with an "
        "Extensions-style catalog backed by MCP."
    )
    selected_ids = {item["id"] for item in selected["items"]}

    assert selected["root_item_ids"][0] == "recipe.application_manager"
    assert {
        "layout.collection-detail",
        "input.toggle",
        "input.selector",
        "ui.list",
        "item.details",
        "navigation.tabs",
        "ui.actions",
    } <= selected_ids
    recipe = get_ui_capability("recipe.application_manager")
    assert recipe["composition"]["reads"]["developments"] == {
        "kind": "mcp",
        "toolId": "applications.list",
        "arguments": {"developed_only": True},
        "dryRun": True,
        "resultPath": "response.result.applications",
        "prototypeFixture": "$state.prototypeFixtures.developments",
    }
    workflow = recipe["composition"]["workflow_model"]
    assert workflow["status_vocabularies"]["operation"] == [
        "planned",
        "applying",
        "succeeded",
        "failed",
        "unknown",
        "reconciling",
        "cancelled",
    ]
    assert {item["id"] for item in workflow["representative_states"]} >= {
        "marketplace-uninstalled",
        "installed-update",
        "local-development",
        "protected-system",
    }
    fixture_model = recipe["composition"]["prototype_fixture_model"]
    localization = recipe["composition"]["localization"]
    development_widget = recipe["composition"]["development_catalog_widget"]
    assert development_widget["inputs"]["subtitleKey"] == "application.display.summary"
    assert fixture_model["canonical_shape"]["applications"]["profile"] == "applications"
    assert len(fixture_model["canonical_shape"]["developments"]["result"]) >= 3
    assert fixture_model["canonical_shape"]["application"]["cases"][0][
        "result"
    ].startswith("$state.prototypeFixtures.samples.")
    assert {
        value["prototype_state_id"]
        for value in fixture_model["canonical_shape"]["samples"].values()
    } == set(fixture_model["required_state_ids"])
    assert localization["locales"] == ["en", "ru"]
    assert localization["fallback_locale"] == "en"
    assert recipe["composition"]["initial_state"]["automaticUpdates"] is True
    assert recipe["composition"]["initial_state"]["prereleaseFollowing"] is False
    assert recipe["composition"]["initial_state"]["updatePolicy"] == "auto_compatible"
    assert selected["qualification"]["requirements"]["application_manager"] is True


def test_explicit_application_manager_recipe_is_a_typed_selection_hint() -> None:
    request = (
        "Add every canonical valueI18nPrefix required by "
        "recipe.application_manager and change nothing else."
    )

    selected = selected_ui_capabilities(request)

    assert selected["qualification"]["surface_kind"] == "application_manager"
    assert selected["qualification"]["requirements"]["application_manager"] is True
    assert selected["root_item_ids"] == ["recipe.application_manager"]
    assert "recipe.kanban_board" not in {item["id"] for item in selected["items"]}


def test_application_manager_iteration_focuses_the_selected_recipe() -> None:
    request = (
        "Prototype phase 2/6: build Applications as an Extensions-style "
        "application lifecycle manager backed by MCP."
    )

    selected = selected_ui_capabilities(request)
    qualification = selected["qualification"]
    recipe = next(
        item for item in selected["items"] if item["id"] == "recipe.application_manager"
    )

    assert qualification["requirements"]["prototype_iteration"] == {
        "index": 2,
        "total": 6,
        "completion_required": False,
    }
    assert recipe["active_implementation_phase"]["id"] == "application_detail"
    assert set(recipe["composition"]) == {
        "details",
        "details_widget",
        "application_header",
        "detail_sections",
            "detail_section_requirement",
            "ordering",
            "access_management",
            "access_widgets",
            "tab_action",
        "reads",
        "release_widget",
        "operations_widget",
        "reports_widget",
        "release_selection",
    }
    assert recipe["implementation_workflow"]["current_phase_index"] == 2
    assert recipe["implementation_workflow"]["total_phases"] == 6


def test_application_manager_iteration_defers_later_postconditions() -> None:
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    sections = next(widget for widget in widgets if widget["id"] == "catalog-sections")
    sections["inputs"]["buttons"][0].pop("label_i18n")

    intermediate = evaluate_ui_request(
        "Prototype phase 1/6: build Applications with its MCP catalog shell.",
        webui,
    )
    final = evaluate_ui_request(
        "Prototype phase 6/6: complete Applications with MCP and localization.",
        webui,
    )

    intermediate_by_id = {item["id"]: item for item in intermediate["postconditions"]}
    assert intermediate["ok"] is True
    assert intermediate_by_id["applications.localization"]["required"] is False
    assert "applications.localization" in intermediate["outstanding_postconditions"]
    assert final["ok"] is False


def test_application_manager_iteration_enforces_accumulated_postconditions() -> None:
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    applications = next(
        widget for widget in widgets if widget["id"] == "catalog-applications"
    )
    applications["dataSource"]["kind"] = "static"

    result = evaluate_ui_request(
        "Prototype phase 2/6: build Applications with MCP and its details.",
        webui,
    )

    mcp_reads = next(
        item
        for item in result["postconditions"]
        if item["id"] == "applications.mcp_reads"
    )
    assert result["ok"] is False
    assert mcp_reads["required"] is True
    assert "applications.mcp_reads" not in result["outstanding_postconditions"]


def test_application_manager_detail_phase_requires_the_state_resolver() -> None:
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    details = next(widget for widget in widgets if widget["id"] == "details")
    details["inputs"].pop("stateBindings")

    result = evaluate_ui_request(
        "Prototype phase 2/6: build Applications with MCP and its details.",
        webui,
    )

    by_id = {item["id"]: item for item in result["postconditions"]}
    assert result["ok"] is False
    assert by_id["applications.concise_operable_detail"]["ok"] is True
    assert by_id["applications.concise_operable_detail"]["required"] is True
    assert by_id["applications.detail_state_binding"]["ok"] is False
    assert by_id["applications.detail_state_binding"]["required"] is True
    assert by_id["applications.detail_lifecycle_binding"]["required"] is False


def test_application_manager_lifecycle_phase_defers_review_operations() -> None:
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    widgets.remove(
        next(widget for widget in widgets if widget["id"] == "review-actions")
    )
    lifecycle = next(
        widget for widget in widgets if widget["id"] == "lifecycle-actions"
    )
    lifecycle["actions"] = [
        action
        for action in lifecycle["actions"]
        if action.get("target") != "applications.plan"
    ]

    lifecycle_result = evaluate_ui_request(
        "Prototype phase 3/6: build Applications lifecycle controls with MCP.",
        webui,
    )
    reviewed = evaluate_ui_request(
        "Prototype phase 4/6: build Applications reviewed operations with MCP.",
        webui,
    )

    lifecycle_by_id = {item["id"]: item for item in lifecycle_result["postconditions"]}
    reviewed_by_id = {item["id"]: item for item in reviewed["postconditions"]}
    assert lifecycle_result["ok"] is True
    assert lifecycle_by_id["applications.lifecycle_controls"]["required"] is True
    assert lifecycle_by_id["applications.reviewed_plan_apply"]["required"] is False
    assert reviewed["ok"] is False
    assert reviewed_by_id["applications.reviewed_plan_apply"]["required"] is True


def test_application_manager_lifecycle_phase_rejects_shadow_state_and_late_toolbar() -> (
    None
):
    webui = _application_manager_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["state"] = copy.deepcopy(page["initialState"])
    widgets = page["widgets"]
    lifecycle = next(
        widget for widget in widgets if widget["id"] == "lifecycle-actions"
    )
    widgets.remove(lifecycle)
    tabs_index = next(
        index for index, widget in enumerate(widgets) if widget["id"] == "tabs"
    )
    widgets.insert(tabs_index + 1, lifecycle)

    result = evaluate_ui_request(
        "Prototype phase 3/6: build Applications lifecycle controls with MCP.",
        webui,
    )

    lifecycle_result = next(
        item
        for item in result["postconditions"]
        if item["id"] == "applications.lifecycle_controls"
    )
    assert lifecycle_result["ok"] is False
    assert lifecycle_result["actual"]["lifecycleBeforeDetails"] is False
    assert lifecycle_result["actual"]["casDefaults"] is False
    assert lifecycle_result["actual"]["shadowStatePages"]
    assert any(
        "pageSchema.state" in item
        for item in lifecycle_result["actual"]["missingRequirements"]
    )


def _application_manager_webui() -> dict:
    def source(
        tool_id: str,
        result_path: str,
        *,
        selected: bool = False,
        fixture_key: str | None = None,
    ) -> dict:
        inferred_fixture = {
            "applications.list": "applications",
            "applications.show": "application",
            "applications.access.show": "applicationAccess",
            "applications.access.users": "usersAccess",
            "applications.access.reviews": "accessReviews",
            "applications.access.privacy": "privacyReport",
            "applications.access.profile": "permissionProfiler",
            "applications.list_releases": "releases",
            "applications.list_operations": "operations",
            "applications.list_development_reports": "reports",
        }[tool_id]
        return {
            "kind": "mcp",
            "toolId": tool_id,
            "arguments": {"application_id": "$state.selectedApplicationId"}
            if selected
            else {},
            "dryRun": True,
            "resultPath": result_path,
            "prototypeFixture": f"$state.prototypeFixtures.{fixture_key or inferred_fixture}",
        }

    development_fixtures = [
        {
            "prototype_state_id": "local-development",
            "application": {
                "application_id": f"development-{index}",
                "display": {"summary": "Representative development"},
            },
            "installed": True,
            "prerelease_following": False,
            "auto_update_enabled": True,
            "subscription": {
                "revision": index,
                "update_track": "stable",
                "update_policy": "auto_compatible",
            },
            "local_development": {
                "exists": True,
                "phase": phase,
                "status": status,
                "publication_status": publication,
                "builder": {
                    "selected_object_type": "scenario",
                    "selected_object_id": f"development-{index}",
                    "source_webspace_id": "desktop",
                    "preview_webspace_id": "desktop-dev",
                },
            },
        }
        for index, (phase, status, publication) in enumerate(
            (
                ("prototype", "working", "not_started"),
                ("automation", "working", "not_started"),
                ("automation", "completed", "published"),
            ),
            start=1,
        )
    ]

    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "applications",
                        "initialState": {
                            "selectedApplicationId": "",
                            "selectedReleaseDigest": "",
                            "effectiveReleaseDigest": "",
                            "installationRevision": 0,
                            "subscriptionRevision": 0,
                            "applicationInstalled": False,
                            "applicationRemovable": False,
                            "updateAvailable": False,
                            "prereleaseFollowing": False,
                            "automaticUpdates": True,
                            "localDevelopmentAvailable": False,
                            "developmentObjectType": "",
                            "developmentObjectId": "",
                            "developmentSourceWebspaceId": "",
                            "developmentPreviewWebspaceId": "",
                            "catalogSection": "applications",
                            "installedOnly": False,
                            "updateTrack": "stable",
                            "updatePolicy": "auto_compatible",
                            "removeDataPolicy": "retain",
                            "activeTab": "details",
                            "usersAccessTab": "people",
                            "selectedAccessGrantId": "",
                            "selectedAccessGrantRevision": 0,
                            "selectedApplicationRoles": [],
                            "selectedPermissionCeiling": [],
                            "observedCapabilities": [],
                            "inferredCapabilities": [],
                            "applicationVerification": {},
                            "reviewedPlan": {},
                            "prototypeFixtures": {
                                "applications": {
                                    "result": [
                                        {
                                            "application": {
                                                "application_id": "family_tasks",
                                                "display": {
                                                    "title": "Family Tasks",
                                                    "summary": "Shared household tasks",
                                                },
                                                "publisher": {"display_name": "Home"},
                                            },
                                            "installed": True,
                                            "update_available": True,
                                            "prerelease_following": False,
                                            "auto_update_enabled": True,
                                            "subscription": {
                                                "revision": 1,
                                                "update_track": "stable",
                                                "update_policy": "auto_compatible",
                                            },
                                            "local_development": {"exists": False},
                                        }
                                    ]
                                },
                                "developments": {"result": development_fixtures},
                                "application": {
                                    "cases": [
                                        {
                                            "when": {"application_id": "family_tasks"},
                                            "result": {
                                                "application": {
                                                    "application_id": "family_tasks",
                                                    "display": {
                                                        "title": "Family Tasks",
                                                        "summary": "Shared household tasks",
                                                    },
                                                    "publisher": {"display_name": "Home"},
                                                },
                                                "installed": True,
                                                "update_available": True,
                                                "prerelease_following": False,
                                                "auto_update_enabled": True,
                                                "subscription": {
                                                    "revision": 1,
                                                    "update_track": "stable",
                                                    "update_policy": "auto_compatible",
                                                },
                                                "local_development": {"exists": False},
                                            },
                                        },
                                        *[
                                            {
                                                "when": {
                                                    "application_id": fixture[
                                                        "application"
                                                    ]["application_id"]
                                                },
                                                "result": fixture,
                                            }
                                            for fixture in development_fixtures
                                        ],
                                        *[
                                            {
                                                "when": {
                                                    "application_id": f"sample-{state_id}"
                                                },
                                                "result": {
                                                    "prototype_state_id": state_id
                                                },
                                            }
                                            for state_id in (
                                                "marketplace-uninstalled",
                                                "installed-current",
                                                "installed-update",
                                                "prerelease-following",
                                                "local-development",
                                                "protected-system",
                                                "operation-recovery",
                                            )
                                        ],
                                    ]
                                },
                                "applicationAccess": {
                                    "permissions": {
                                        "result": {
                                                "digest": "sha256:reviewed-profile",
                                                "profile": {
                                                    "required": [
                                                        {"id": "workspace.read", "purpose": "Read assigned tasks."},
                                                        {"id": "workspace.write", "purpose": "Complete assigned tasks."},
                                                        {"id": "llm.generate", "purpose": "Suggest task wording."},
                                                        {"id": "network.egress", "purpose": "Sync calendar due dates."},
                                                    ],
                                                    "data_practices": {
                                                        "collected": ["task_metadata"],
                                                        "sent_off_device": ["task_metadata"],
                                                        "tracking": False,
                                                    },
                                                },
                                                "badges": ["Uses AI", "External calendar", "No tracking"],
                                        }
                                    },
                                    "access": {
                                        "result": [
                                                {
                                                    "grant_id": "appgrant.member",
                                                    "subject_ref": "user:masha",
                                                    "application_roles": ["member"],
                                                    "permission_ceiling": ["workspace.read", "workspace.write"],
                                                    "status": "active",
                                                    "revision": 1,
                                                },
                                                {
                                                    "grant_id": "appgrant.guest",
                                                    "subject_ref": "session:guest-review",
                                                    "application_roles": ["guest"],
                                                    "permission_ceiling": ["workspace.read"],
                                                    "status": "active",
                                                    "expires_at": "2026-09-17T12:00:00+00:00",
                                                    "revision": 1,
                                                },
                                        ]
                                    },
                                    "roles": {
                                        "result": [
                                                {"id": "owner", "title": "Owner", "grants": ["application.manage"], "assignable_to": ["owner"], "sensitive": True},
                                                {"id": "member", "title": "Member", "grants": ["task.read", "task.complete"], "assignable_to": ["member"], "sensitive": False},
                                                {"id": "child", "title": "Child participant", "grants": ["task.read", "task.complete"], "assignable_to": ["child"], "sensitive": False},
                                                {"id": "guest", "title": "Guest reader", "grants": ["task.read"], "assignable_to": ["guest"], "sensitive": False},
                                        ]
                                    },
                                    "connected_accounts": {
                                        "result": [
                                                {
                                                    "account_id": "calendar-user",
                                                    "provider_id": "calendar",
                                                    "mode": "delegated_user",
                                                    "scopes": ["calendar.read"],
                                                    "status": "connected",
                                                    "token_expires_at": "2026-10-01T12:00:00+00:00",
                                                }
                                        ]
                                    },
                                    "release_readiness": {
                                        "result": {
                                                "overall": "passed",
                                                "release_scope": "trial",
                                                "checks": ["permissions", "roles", "regression", "redaction"],
                                                "report_digest": "sha256:verification-report",
                                        }
                                    },
                                    "activity": {
                                        "result": [
                                                {
                                                    "occurred_at": "2026-09-16T07:00:00+00:00",
                                                    "action": "application.permission.allow",
                                                    "subject_ref": "user:masha",
                                                    "decision": "allow",
                                                    "reason_code": "allowed",
                                                }
                                        ]
                                    },
                                },
                                "usersAccess": {
                                    "people": {"result": [{"subject_ref": "user:masha", "kind": "user", "application_access": ["family_tasks:member"]}]},
                                    "guests": {"result": [{"subject_ref": "session:guest-review", "kind": "guest", "application_access": ["family_tasks:guest"]}]},
                                    "children": {"result": [{"subject_ref": "child:petya", "kind": "child", "application_access": ["family_tasks:child"]}]},
                                    "devices": {"result": [{"device_id": "phone-owner", "status": "trusted", "user_id": "owner"}]},
                                    "sessions": {"result": [{"session_id": "session-owner", "status": "active", "subject": "user:owner"}]},
                                    "application_access": {"result": [{"grant_id": "appgrant.member", "subject_ref": "user:masha", "application_roles": ["member"]}]},
                                    "activity": {"result": [{"occurred_at": "2026-09-16T07:00:00+00:00", "action": "application.permission.allow", "reason_code": "allowed"}]},
                                },
                                "accessReviews": {"result": [{"finding_id": "review.guest", "recommended_action": "review_guest_expiry", "subject_ref": "session:guest-review", "reasons": ["long_lived_guest"]}]},
                                "privacyReport": {"result": {"declared": {"data_categories": ["task_metadata"]}, "observed": {"data_categories": ["task_metadata"]}}},
                                "permissionProfiler": {
                                    "result": {
                                        "declared": ["workspace.read"],
                                        "statically_inferred": ["workspace.read"],
                                        "observed": ["workspace.read"],
                                        "undeclared_observed": [],
                                        "unused": [],
                                        "role_matrix": [{"role_id": "member", "compatible": {"owner": True, "member": True, "child": False, "guest": False}}],
                                        "preview_modes": {"owner": ["owner"], "member": ["member"], "child": ["child"], "guest": ["guest"]},
                                    }
                                },
                                "applicationVerification": {
                                    "result": {
                                        "report": {"overall": "passed"},
                                        "checklist": [
                                            {"id": "permissions.declared_vs_observed", "result": "passed", "gate": "hard_gate"},
                                            {"id": "access.role_matrix", "result": "passed", "gate": "hard_gate"},
                                        ],
                                        "ci_status": "passed",
                                    }
                                },
                                "releases": {"result": []},
                                "operations": {"result": []},
                                "reports": {"result": []},
                                "plan": {
                                    "cases": [
                                        {
                                            "when": {"kind": kind},
                                            "result": {
                                                "operation": {
                                                    "operation_id": f"prototype.plan.{kind}",
                                                    "plan_digest": f"sha256:fixture-{kind}",
                                                    "application_id": "sample-application",
                                                    "kind": kind,
                                                    "plan": {
                                                        "review_summary": f"Review {kind}",
                                                        "permissions": [
                                                            "workspace.read"
                                                        ],
                                                    },
                                                }
                                            },
                                        }
                                        for kind in (
                                            "install",
                                            "update",
                                            "select_track",
                                            "remove",
                                        )
                                    ]
                                },
                                "apply": {"result": {"status": "succeeded"}},
                            },
                        },
                        "layout": {
                            "version": 2,
                            "pattern": "collection-detail",
                            "density": "comfortable",
                            "contentWidth": "fluid",
                            "scroll": "regions",
                            "regions": [
                                {
                                    "id": "master",
                                    "role": "collection",
                                    "size": {"minPx": 240, "preferredPx": 380, "maxPx": 520},
                                    "presentation": {"wide": "pane", "compact": "drawer"},
                                },
                                {
                                    "id": "detail",
                                    "role": "detail",
                                    "presentation": {"wide": "pane", "compact": "route"},
                                },
                                {
                                    "id": "metadata",
                                    "role": "inspector",
                                    "size": {"minPx": 220, "preferredPx": 300, "maxPx": 480},
                                    "presentation": {"wide": "pane", "compact": "sheet"},
                                },
                            ],
                        },
                        "widgets": [
                            {
                                "id": "catalog-sections",
                                "type": "navigation.tabs",
                                "area": "master",
                                "inputs": {
                                    "size": "small",
                                    "stretch": True,
                                    "selectedStateKey": "catalogSection",
                                    "buttons": [
                                        {"id": "applications", "label": "Applications"},
                                        {
                                            "id": "developments",
                                            "label": "My developments",
                                        },
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "click",
                                        "type": "updateState",
                                        "params": {"catalogSection": "$event.id"},
                                    }
                                ],
                            },
                            {
                                "id": "installed-only",
                                "type": "input.toggle",
                                "area": "master",
                                "visibleIf": "$state.catalogSection == 'applications'",
                                "dataSource": {
                                    "kind": "static",
                                    "value": "$state.installedOnly",
                                },
                                "inputs": {"label": "Installed only"},
                                "actions": [
                                    {
                                        "on": "change",
                                        "type": "updateState",
                                        "params": {"installedOnly": "$event.checked"},
                                    }
                                ],
                            },
                            *[
                                {
                                    "id": f"catalog-{section}",
                                    "type": "ui.list",
                                    "area": "master",
                                    "visibleIf": f"$state.catalogSection == '{section}'",
                                    "dataSource": {
                                        **source(
                                            "applications.list",
                                            "response.result.applications",
                                            fixture_key=section,
                                        ),
                                        "arguments": arguments,
                                    },
                                    "inputs": {
                                        "variant": "list",
                                        "itemIdKey": "application.application_id",
                                        "search": True,
                                        "titleKey": "application.display.title",
                                        "subtitleKey": (
                                            "application.display.summary"
                                            if section == "developments"
                                            else "application.publisher.display_name"
                                        ),
                                        "previewKey": (
                                            "application.publisher.display_name"
                                            if section == "developments"
                                            else "application.display.summary"
                                        ),
                                        "meta": (
                                            [
                                                {
                                                    "key": "local_development.phase",
                                                    "label": "Phase",
                                                    "kind": "badge",
                                                },
                                                {
                                                    "key": "local_development.status",
                                                    "label": "Status",
                                                    "kind": "badge",
                                                },
                                                {
                                                    "key": "local_development.publication_status",
                                                    "label": "Publication",
                                                    "kind": "badge",
                                                },
                                            ]
                                            if section == "developments"
                                            else [
                                                {
                                                    "key": "installed",
                                                    "label": "Installation",
                                                    "kind": "boolean",
                                                    "trueLabel": "Installed",
                                                    "falseLabel": "Not installed",
                                                },
                                                {
                                                    "key": "update_available",
                                                    "label": "Update",
                                                    "kind": "boolean",
                                                    "trueLabel": "Update available",
                                                    "falseLabel": "Current",
                                                },
                                            ]
                                        ),
                                        "emptyText": "No applications found.",
                                    },
                                    "actions": [
                                        {
                                            "on": "select",
                                            "type": "updateState",
                                            "params": {
                                                "selectedApplicationId": "$event.application.application_id",
                                                "selectedReleaseDigest": "",
                                                "reviewedPlan": {},
                                            },
                                        }
                                    ],
                                }
                                for section, arguments in (
                                    (
                                        "applications",
                                        {
                                            "available_only": True,
                                            "installed_only": "$state.installedOnly",
                                        },
                                    ),
                                    ("developments", {"developed_only": True}),
                                )
                            ],
                            {
                                "id": "tabs",
                                "type": "navigation.tabs",
                                "area": "detail",
                                "inputs": {
                                    "selectedStateKey": "activeTab",
                                    "buttons": [
                                        {"id": value, "label": label, "icon": icon}
                                        for value, label, icon in (
                                            ("details", "Details", "options-outline"),
                                            ("versions", "Versions", "download-outline"),
                                            ("operations", "Operations", "construct-outline"),
                                            ("reports", "Reports", "folder-open-outline"),
                                            ("permissions", "Permissions", "checkmark-outline"),
                                            ("access", "Access", "ticket-outline"),
                                            ("roles", "Roles", "folder-open-outline"),
                                            ("connected_accounts", "Accounts", "refresh-outline"),
                                            ("release_readiness", "Readiness", "checkmark-outline"),
                                            ("activity", "Activity", "refresh-outline"),
                                            ("users_access", "Users & Access", "ticket-outline"),
                                        )
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "click",
                                        "type": "updateState",
                                        "params": {"activeTab": "$event.id"},
                                    }
                                ],
                            },
                            *[
                                {
                                    "id": f"application-access-{section}",
                                    "type": "ui.list" if list_keys else "item.details",
                                    "area": "detail",
                                    "title": title,
                                    "visibleIf": f"$state.activeTab == '{section}' && $state.selectedApplicationId",
                                    "dataSource": {
                                        "kind": "mcp",
                                        "toolId": "applications.access.show",
                                        "arguments": {
                                            "application_id": "$state.selectedApplicationId",
                                            "release_digest": "$state.selectedReleaseDigest",
                                        },
                                        "dryRun": True,
                                        "resultPath": f"response.result.access.sections.{section}",
                                        "prototypeFixture": f"$state.prototypeFixtures.applicationAccess.{section}",
                                    },
                                    "inputs": (
                                        {
                                            "variant": "list",
                                            "itemIdKey": list_keys[0],
                                            "titleKey": list_keys[1],
                                            "subtitleKey": list_keys[2],
                                            "previewKey": list_keys[3],
                                            "emptyText": empty_text,
                                        }
                                        if list_keys
                                        else {
                                            "presentation": "section",
                                            "emptyText": empty_text,
                                            "fields": [
                                                {"label": label, "path": path}
                                                for label, path in fields
                                            ],
                                        }
                                    ),
                                    **(
                                        {
                                            "actions": [
                                                {
                                                    "on": "select",
                                                    "type": "updateState",
                                                    "params": {
                                                        "selectedAccessGrantId": "$event.grant_id",
                                                        "selectedAccessGrantRevision": "$event.revision",
                                                        "selectedApplicationRoles": "$event.application_roles",
                                                        "selectedPermissionCeiling": "$event.permission_ceiling",
                                                    },
                                                }
                                            ]
                                        }
                                        if section == "access"
                                        else {}
                                    ),
                                }
                                for section, title, empty_text, fields, list_keys in (
                                    (
                                        "permissions",
                                        "Permissions",
                                        "No permission profile.",
                                        (
                                            ("Profile digest", "digest"),
                                            ("Required", "profile.required"),
                                            ("Optional", "profile.optional"),
                                            ("Data practices", "profile.data_practices"),
                                            ("Model use", "profile.llm_model_use"),
                                            ("Notifications", "profile.notifications"),
                                            ("Background work", "profile.background_actions"),
                                            ("Secrets", "profile.secrets"),
                                            ("Providers", "profile.external_providers"),
                                        ),
                                        None,
                                    ),
                                    (
                                        "access",
                                        "Access",
                                        "No access grants.",
                                        (
                                            ("Subject", "subject_ref"),
                                            ("Roles", "application_roles"),
                                            ("Permissions", "permission_ceiling"),
                                            ("Constraints", "constraints"),
                                            ("Status", "status"),
                                            ("Expires", "expires_at"),
                                        ),
                                        ("grant_id", "subject_ref", "status", "application_roles"),
                                    ),
                                    (
                                        "roles",
                                        "Roles",
                                        "No Application roles.",
                                        (
                                            ("Role", "id"),
                                            ("Capabilities", "grants"),
                                            ("Assignable to", "assignable_to"),
                                            ("Required permissions", "requires_permissions"),
                                            ("Sensitive", "sensitive"),
                                        ),
                                        ("id", "title", "assignable_to", "grants"),
                                    ),
                                    (
                                        "connected_accounts",
                                        "Connected accounts",
                                        "No connected accounts.",
                                        (
                                            ("Provider", "provider_id"),
                                            ("Mode", "mode"),
                                            ("Scopes", "scopes"),
                                            ("Status", "status"),
                                            ("Token expiry", "token_expires_at"),
                                        ),
                                        ("account_id", "provider_id", "status", "scopes"),
                                    ),
                                    (
                                        "release_readiness",
                                        "Release readiness",
                                        "No verification report.",
                                        (
                                            ("Result", "overall"),
                                            ("Scope", "release_scope"),
                                            ("Checks", "checks"),
                                            ("Warnings", "warnings"),
                                            ("Residual risks", "residual_risks"),
                                        ),
                                        None,
                                    ),
                                    (
                                        "activity",
                                        "Activity",
                                        "No access activity.",
                                        (
                                            ("Time", "occurred_at"),
                                            ("Action", "action"),
                                            ("Subject", "subject_ref"),
                                            ("Decision", "decision"),
                                            ("Reason", "reason_code"),
                                        ),
                                        ("occurred_at", "action", "subject_ref", "reason_code"),
                                    ),
                                )
                            ],
                            {
                                "id": "application-permission-profiler",
                                "type": "item.details",
                                "area": "metadata",
                                "title": "Permission profiler",
                                "visibleIf": "$state.activeTab == 'permissions' && $state.selectedApplicationId",
                                "dataSource": {
                                    "kind": "mcp",
                                    "toolId": "applications.access.profile",
                                    "arguments": {
                                        "application_id": "$state.selectedApplicationId",
                                        "release_digest": "$state.selectedReleaseDigest",
                                        "observed_capabilities": "$state.observedCapabilities",
                                        "inferred_capabilities": "$state.inferredCapabilities",
                                    },
                                    "dryRun": True,
                                    "resultPath": "response.result",
                                    "prototypeFixture": "$state.prototypeFixtures.permissionProfiler",
                                },
                                "inputs": {
                                    "presentation": "section",
                                    "fields": [
                                        {"label": "Declared", "path": "declared"},
                                        {"label": "Inferred", "path": "statically_inferred"},
                                        {"label": "Observed", "path": "observed"},
                                        {"label": "Undeclared", "path": "undeclared_observed"},
                                        {"label": "Unused", "path": "unused"},
                                        {"label": "Role matrix", "path": "role_matrix"},
                                        {"label": "Preview modes", "path": "preview_modes"},
                                    ],
                                },
                            },
                            {
                                "id": "application-privacy-observation",
                                "type": "item.details",
                                "area": "metadata",
                                "title": "Privacy report",
                                "visibleIf": "$state.activeTab == 'permissions' && $state.selectedApplicationId",
                                "dataSource": {
                                    "kind": "mcp",
                                    "toolId": "applications.access.privacy",
                                    "arguments": {
                                        "application_id": "$state.selectedApplicationId",
                                        "release_digest": "$state.selectedReleaseDigest",
                                    },
                                    "dryRun": True,
                                    "resultPath": "response.result.privacy_report",
                                    "prototypeFixture": "$state.prototypeFixtures.privacyReport",
                                },
                                "inputs": {
                                    "presentation": "section",
                                    "fields": [
                                        {"label": "Declared", "path": "declared"},
                                        {"label": "Observed", "path": "observed"},
                                    ],
                                },
                            },
                            {
                                "id": "application-access-reviews",
                                "type": "ui.list",
                                "area": "metadata",
                                "title": "Access reviews",
                                "visibleIf": "$state.activeTab == 'activity' && $state.selectedApplicationId",
                                "dataSource": {
                                    "kind": "mcp",
                                    "toolId": "applications.access.reviews",
                                    "arguments": {
                                        "application_id": "$state.selectedApplicationId"
                                    },
                                    "dryRun": True,
                                    "resultPath": "response.result.findings",
                                    "prototypeFixture": "$state.prototypeFixtures.accessReviews",
                                },
                                "inputs": {
                                    "itemIdKey": "finding_id",
                                    "titleKey": "recommended_action",
                                    "subtitleKey": "subject_ref",
                                    "previewKey": "reasons",
                                    "emptyText": "No reviews due.",
                                },
                            },
                            {
                                "id": "users-access-tabs",
                                "type": "navigation.tabs",
                                "area": "detail",
                                "visibleIf": "$state.activeTab == 'users_access'",
                                "inputs": {
                                    "selectedStateKey": "usersAccessTab",
                                    "buttons": [
                                        {"id": value, "label": label, "icon": icon}
                                        for value, label, icon in (
                                            ("people", "People", "folder-open-outline"),
                                            ("guests", "Guests", "ticket-outline"),
                                            ("children", "Children", "checkmark-outline"),
                                            ("devices", "Devices", "options-outline"),
                                            ("sessions", "Sessions", "refresh-outline"),
                                            ("application_access", "App access", "construct-outline"),
                                            ("activity", "Activity", "download-outline"),
                                        )
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "click",
                                        "type": "updateState",
                                        "params": {"usersAccessTab": "$event.id"},
                                    }
                                ],
                            },
                            *[
                                {
                                    "id": f"users-access-{section}",
                                    "type": "ui.list",
                                    "area": "detail",
                                    "visibleIf": f"$state.activeTab == 'users_access' && $state.usersAccessTab == '{section}'",
                                    "dataSource": {
                                        "kind": "mcp",
                                        "toolId": "applications.access.users",
                                        "arguments": {},
                                        "dryRun": True,
                                        "resultPath": f"response.result.users_access.{section}",
                                        "prototypeFixture": f"$state.prototypeFixtures.usersAccess.{section}",
                                    },
                                    "inputs": {
                                        "itemIdKey": identity,
                                        "titleKey": identity,
                                        "subtitleKey": subtitle,
                                        "previewKey": preview,
                                        "emptyText": empty_text,
                                    },
                                }
                                for section, identity, subtitle, preview, empty_text in (
                                    ("people", "subject_ref", "kind", "application_access", "No people with Application access."),
                                    ("guests", "subject_ref", "kind", "application_access", "No guest access."),
                                    ("children", "subject_ref", "kind", "application_access", "No child access."),
                                    ("devices", "device_id", "status", "user_id", "No devices."),
                                    ("sessions", "session_id", "status", "subject", "No sessions."),
                                    ("application_access", "grant_id", "subject_ref", "application_roles", "No Application access."),
                                    ("activity", "occurred_at", "action", "reason_code", "No access activity."),
                                )
                            ],
                            {
                                "id": "access-grant-form",
                                "type": "ui.form",
                                "area": "metadata",
                                "visibleIf": "$state.activeTab == 'access' && $state.selectedApplicationId",
                                "inputs": {
                                    "showSubmit": True,
                                    "submitLabel": "Grant access",
                                    "fields": [
                                        {"id": "subject_ref", "type": "text", "label": "Subject", "required": True},
                                        {"id": "application_roles", "type": "text", "label": "Roles", "required": True, "placeholder": "role-id, role-id"},
                                        {"id": "permission_ceiling", "type": "text", "label": "Permissions", "placeholder": "permission.id, permission.id"},
                                        {"id": "explicit_denies", "type": "text", "label": "Denied permissions", "placeholder": "permission.id, permission.id"},
                                        {"id": "expires_at", "type": "dateTime", "label": "Expires"},
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "submit",
                                        "type": "callMcp",
                                        "target": "applications.access.grant",
                                        "idempotencyKey": "auto",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "release_digest": "$state.selectedReleaseDigest",
                                            "subject_ref": "$event.values.subject_ref",
                                            "application_roles": "$event.values.application_roles",
                                            "permission_ceiling": "$event.values.permission_ceiling",
                                            "explicit_denies": "$event.values.explicit_denies",
                                            "expires_at": "$event.values.expires_at",
                                            "expected_revision": 0,
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "access-change-form",
                                "type": "ui.form",
                                "area": "metadata",
                                "visibleIf": "$state.activeTab == 'access' && $state.selectedAccessGrantId",
                                "inputs": {
                                    "showSubmit": True,
                                    "submitLabel": "Change access",
                                    "fields": [
                                        {"id": "application_roles", "type": "text", "label": "New roles", "required": True, "placeholder": "role-id, role-id"},
                                        {"id": "permission_ceiling", "type": "text", "label": "Permissions", "placeholder": "permission.id, permission.id"},
                                        {"id": "explicit_denies", "type": "text", "label": "Denied permissions", "placeholder": "permission.id, permission.id"},
                                        {"id": "expires_at", "type": "dateTime", "label": "Expires"},
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "submit",
                                        "type": "callMcp",
                                        "target": "applications.access.change",
                                        "idempotencyKey": "auto",
                                        "params": {
                                            "grant_id": "$state.selectedAccessGrantId",
                                            "release_digest": "$state.selectedReleaseDigest",
                                            "application_roles": "$event.values.application_roles",
                                            "permission_ceiling": "$event.values.permission_ceiling",
                                            "explicit_denies": "$event.values.explicit_denies",
                                            "expires_at": "$event.values.expires_at",
                                            "expected_revision": "$state.selectedAccessGrantRevision",
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "access-revoke-actions",
                                "type": "ui.actions",
                                "area": "metadata",
                                "visibleIf": "$state.activeTab == 'access' && $state.selectedAccessGrantId",
                                "inputs": {
                                    "variant": "toolbar",
                                    "buttons": [
                                        {"id": "revoke-access", "label": "Revoke access", "icon": "trash-outline"},
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "click:revoke-access",
                                        "type": "callMcp",
                                        "target": "applications.access.revoke",
                                        "idempotencyKey": "auto",
                                        "params": {
                                            "grant_id": "$state.selectedAccessGrantId",
                                            "expected_revision": "$state.selectedAccessGrantRevision",
                                        },
                                    },
                                ],
                            },
                            {
                                "id": "connected-account-form",
                                "type": "ui.form",
                                "area": "metadata",
                                "visibleIf": "$state.activeTab == 'connected_accounts' && $state.selectedApplicationId",
                                "inputs": {
                                    "showSubmit": True,
                                    "submitLabel": "Update account",
                                    "fields": [
                                        {"id": "account_id", "type": "text", "label": "Account", "required": True},
                                        {"id": "provider_id", "type": "text", "label": "Provider", "required": True},
                                        {"id": "subject_ref", "type": "text", "label": "Subject", "required": True},
                                        {"id": "mode", "type": "select", "label": "Mode", "required": True, "defaultValue": "delegated_user", "options": [{"label": "Delegated user", "value": "delegated_user"}, {"label": "Application service", "value": "app_service"}]},
                                        {"id": "scopes", "type": "text", "label": "Scopes", "placeholder": "scope.read, scope.write"},
                                        {"id": "status", "type": "select", "label": "Status", "required": True, "defaultValue": "missing", "options": [{"label": "Missing", "value": "missing"}, {"label": "Connected", "value": "connected"}, {"label": "Expired", "value": "expired"}, {"label": "Revoked", "value": "revoked"}, {"label": "Denied", "value": "denied"}]},
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "submit",
                                        "type": "callMcp",
                                        "target": "applications.access.connected_account",
                                        "idempotencyKey": "auto",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "release_digest": "$state.selectedReleaseDigest",
                                            "account_id": "$event.values.account_id",
                                            "provider_id": "$event.values.provider_id",
                                            "subject_ref": "$event.values.subject_ref",
                                            "mode": "$event.values.mode",
                                            "scopes": "$event.values.scopes",
                                            "status": "$event.values.status",
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "access-simulation-form",
                                "type": "ui.form",
                                "area": "metadata",
                                "visibleIf": "$state.activeTab == 'access' && $state.selectedApplicationId",
                                "inputs": {
                                    "showSubmit": True,
                                    "submitLabel": "Simulate",
                                    "fields": [
                                        {"id": "subject_ref", "type": "text", "label": "Subject", "required": True},
                                        {"id": "permission_id", "type": "text", "label": "Permission", "required": True},
                                        {"id": "app_capability", "type": "text", "label": "Application capability", "required": True, "defaultValue": "application.use"},
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "submit",
                                        "type": "callMcp",
                                        "target": "applications.access.simulate",
                                        "idempotencyKey": "auto",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "release_digest": "$state.selectedReleaseDigest",
                                            "subject_ref": "$event.values.subject_ref",
                                            "permission_id": "$event.values.permission_id",
                                            "app_capability": "$event.values.app_capability",
                                            "application_roles": "$state.selectedApplicationRoles",
                                            "permission_ceiling": "$state.selectedPermissionCeiling",
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "application-final-verification-form",
                                "type": "ui.form",
                                "area": "metadata",
                                "visibleIf": "$state.activeTab == 'release_readiness' && $state.selectedApplicationId",
                                "inputs": {
                                    "showSubmit": True,
                                    "submitLabel": "Run final verification",
                                    "fields": [
                                        {"id": "source_commit", "type": "text", "label": "Source commit", "required": True},
                                        {
                                            "id": "release_scope",
                                            "type": "select",
                                            "label": "Release scope",
                                            "required": True,
                                            "defaultValue": "candidate",
                                            "options": [
                                                {"label": "Candidate", "value": "candidate"},
                                                {"label": "Trial", "value": "trial"},
                                                {"label": "Publication", "value": "publication"},
                                            ],
                                        },
                                        {"id": "regression_evidence", "type": "text", "label": "Regression evidence", "required": True},
                                        {"id": "access_matrix_evidence", "type": "text", "label": "Access matrix evidence", "required": True},
                                        {"id": "pending_action_evidence", "type": "text", "label": "Pending Action evidence", "required": True},
                                        {"id": "audit_evidence", "type": "text", "label": "Audit evidence", "required": True},
                                        {"id": "disclosure_evidence", "type": "text", "label": "Disclosure evidence", "required": True},
                                        {"id": "redaction_evidence", "type": "text", "label": "Redaction evidence", "required": True},
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "submit",
                                        "type": "callMcp",
                                        "target": "applications.access.verify_release",
                                        "idempotencyKey": "auto",
                                        "resultStateKey": "applicationVerification",
                                        "prototypeFixture": "$state.prototypeFixtures.applicationVerification",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "release_digest": "$state.selectedReleaseDigest",
                                            "source_commit": "$event.values.source_commit",
                                            "release_scope": "$event.values.release_scope",
                                            "observed_capabilities": "$state.observedCapabilities",
                                            "inferred_capabilities": "$state.inferredCapabilities",
                                            "regression_evidence": "$event.values.regression_evidence",
                                            "access_matrix_evidence": "$event.values.access_matrix_evidence",
                                            "pending_action_evidence": "$event.values.pending_action_evidence",
                                            "audit_evidence": "$event.values.audit_evidence",
                                            "disclosure_evidence": "$event.values.disclosure_evidence",
                                            "redaction_evidence": "$event.values.redaction_evidence",
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "details",
                                "type": "item.details",
                                "area": "detail",
                                "visibleIf": "$state.selectedApplicationId",
                                "dataSource": source(
                                    "applications.show",
                                    "response.result.application",
                                    selected=True,
                                ),
                                "inputs": {
                                    "stateOnly": True,
                                    "stateBindings": {
                                        "installationRevision": {
                                            "path": "installation.revision",
                                            "default": 0,
                                        },
                                        "subscriptionRevision": {
                                            "path": "subscription.revision",
                                            "default": 0,
                                        },
                                        "applicationInstalled": "installed",
                                        "applicationRemovable": "application.protection.active_installation_removable",
                                        "updateAvailable": "update_available",
                                        "prereleaseFollowing": {
                                            "path": "prerelease_following",
                                            "default": False,
                                        },
                                        "automaticUpdates": {
                                            "path": "auto_update_enabled",
                                            "default": True,
                                        },
                                        "updateTrack": {
                                            "path": "subscription.update_track",
                                            "default": "stable",
                                        },
                                        "updatePolicy": {
                                            "path": "subscription.update_policy",
                                            "default": "auto_compatible",
                                        },
                                        "effectiveReleaseDigest": {
                                            "path": "effective_release.release_digest",
                                            "default": "",
                                        },
                                        "localDevelopmentAvailable": {
                                            "path": "local_development.exists",
                                            "default": False,
                                        },
                                        "developmentObjectType": {
                                            "path": "local_development.builder.selected_object_type",
                                            "default": "",
                                        },
                                        "developmentObjectId": {
                                            "path": "local_development.builder.selected_object_id",
                                            "default": "",
                                        },
                                        "developmentSourceWebspaceId": {
                                            "path": "local_development.builder.source_webspace_id",
                                            "default": "",
                                        },
                                        "developmentPreviewWebspaceId": {
                                            "path": "local_development.builder.preview_webspace_id",
                                            "default": "",
                                        },
                                    },
                                },
                            },
                            {
                                "id": "application-header",
                                "type": "item.details",
                                "area": "detail",
                                "title": "{application.display.title}",
                                "visibleIf": "$state.selectedApplicationId",
                                "dataSource": source(
                                    "applications.show",
                                    "response.result.application",
                                    selected=True,
                                ),
                                "inputs": {
                                    "presentation": "header",
                                    "fields": [
                                        {
                                            "label": "Summary",
                                            "path": "application.display.summary",
                                        },
                                        {
                                            "label": "Publisher",
                                            "path": "application.publisher.display_name",
                                        },
                                        {
                                            "label": "Installed",
                                            "path": "installed_release.version",
                                        },
                                        {
                                            "label": "Marketplace",
                                            "path": "marketplace_release.version",
                                        },
                                    ],
                                },
                            },
                            {
                                "id": "releases",
                                "type": "ui.list",
                                "area": "detail",
                                "visibleIf": "$state.activeTab == 'versions' && $state.selectedApplicationId",
                                "dataSource": source(
                                    "applications.list_releases",
                                    "response.result.releases",
                                    selected=True,
                                ),
                                "inputs": {
                                    "itemIdKey": "release_digest",
                                    "titleKey": "version",
                                    "subtitleKey": "lifecycle",
                                    "previewKey": "release_digest",
                                    "emptyText": "No versions available.",
                                },
                                "actions": [
                                    {
                                        "on": "select",
                                        "type": "updateState",
                                        "params": {
                                            "selectedReleaseDigest": "$event.release_digest",
                                            "reviewedPlan": {},
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "operations",
                                "type": "ui.list",
                                "area": "detail",
                                "visibleIf": "$state.activeTab == 'operations' && $state.selectedApplicationId",
                                "dataSource": source(
                                    "applications.list_operations",
                                    "response.result.operations",
                                    selected=True,
                                ),
                                "inputs": {
                                    "itemIdKey": "operation_id",
                                    "titleKey": "summary",
                                    "subtitleKey": "kind",
                                    "previewKey": "kind",
                                    "meta": [
                                        {
                                            "key": "status",
                                            "label": "Status",
                                            "kind": "badge",
                                        },
                                    ],
                                    "emptyText": "No operations yet.",
                                },
                            },
                            {
                                "id": "reports",
                                "type": "ui.list",
                                "area": "detail",
                                "visibleIf": "$state.activeTab == 'reports' && $state.selectedApplicationId",
                                "dataSource": source(
                                    "applications.list_development_reports",
                                    "response.result.reports",
                                ),
                                "inputs": {
                                    "itemIdKey": "report_id",
                                    "titleKey": "title",
                                    "subtitleKey": "summary",
                                    "previewKey": "summary",
                                    "meta": [
                                        {
                                            "key": "status",
                                            "label": "Status",
                                            "kind": "badge",
                                        },
                                    ],
                                    "filters": [
                                        {
                                            "key": "application_id",
                                            "stateKey": "selectedApplicationId",
                                        }
                                    ],
                                    "emptyText": "No reports yet.",
                                },
                            },
                            {
                                "id": "prerelease-following",
                                "type": "input.toggle",
                                "area": "detail",
                                "visibleIf": "$state.applicationInstalled == true",
                                "dataSource": {
                                    "kind": "static",
                                    "value": "$state.prereleaseFollowing",
                                },
                                "inputs": {"label": "Use pre-release version"},
                                "actions": [
                                    {
                                        "on": "change",
                                        "type": "updateState",
                                        "params": {
                                            "prereleaseFollowing": "$event.checked",
                                            "updateTrack": {
                                                "kind": "expression",
                                                "op": "if",
                                                "condition": "$event.checked",
                                                "then": "prerelease",
                                                "else": "stable",
                                            },
                                            "reviewedPlan": {},
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "automatic-updates",
                                "type": "input.toggle",
                                "area": "detail",
                                "visibleIf": "$state.applicationInstalled == true",
                                "dataSource": {
                                    "kind": "static",
                                    "value": "$state.automaticUpdates",
                                },
                                "inputs": {"label": "Auto update"},
                                "actions": [
                                    {
                                        "on": "change",
                                        "type": "updateState",
                                        "params": {
                                            "automaticUpdates": "$event.checked",
                                            "updatePolicy": {
                                                "kind": "expression",
                                                "op": "if",
                                                "condition": "$event.checked",
                                                "then": "auto_compatible",
                                                "else": "notify",
                                            },
                                            "reviewedPlan": {},
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "remove-data-policy",
                                "type": "input.selector",
                                "area": "metadata",
                                "visibleIf": "$state.applicationInstalled == true && $state.applicationRemovable == true",
                                "inputs": {
                                    "label": "Data on uninstall",
                                    "defaultValue": "$state.removeDataPolicy",
                                    "options": [
                                        {"label": "Retain data", "value": "retain"},
                                        {"label": "Delete data", "value": "delete"},
                                        {
                                            "label": "Snapshot and delete",
                                            "value": "snapshot_then_delete",
                                        },
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "change",
                                        "type": "updateState",
                                        "params": {
                                            "removeDataPolicy": "$event.value",
                                            "reviewedPlan": {},
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "reviewed-plan",
                                "type": "item.details",
                                "area": "detail",
                                "title": "Review",
                                "visibleIf": "$state.reviewedPlan.operation.operation_id || $state.reviewedPlan.status",
                                "dataSource": {
                                    "kind": "static",
                                    "value": "$state.reviewedPlan",
                                },
                                "inputs": {
                                    "presentation": "section",
                                    "fields": [
                                        {
                                            "label": "Operation",
                                            "path": "operation.kind",
                                        },
                                        {
                                            "label": "Summary",
                                            "path": "operation.plan.review_summary",
                                        },
                                        {
                                            "label": "Requested permissions",
                                            "path": "operation.plan.permissions",
                                        },
                                    ],
                                },
                            },
                            {
                                "id": "lifecycle-actions",
                                "type": "ui.actions",
                                "area": "detail",
                                "visibleIf": "$state.selectedApplicationId",
                                "inputs": {
                                    "variant": "toolbar",
                                    "buttons": [
                                        {
                                            "id": "install",
                                            "label": "Install",
                                            "icon": "download-outline",
                                            "visibleIf": "$state.applicationInstalled != true && ($state.selectedReleaseDigest || $state.effectiveReleaseDigest)",
                                        },
                                        {
                                            "id": "update",
                                            "label": "Update",
                                            "icon": "refresh-outline",
                                            "visibleIf": "$state.applicationInstalled == true && ($state.updateAvailable == true || $state.selectedReleaseDigest)",
                                        },
                                        {
                                            "id": "select-track",
                                            "label": "Save update settings",
                                            "icon": "options-outline",
                                            "visibleIf": "$state.applicationInstalled == true",
                                        },
                                        {
                                            "id": "remove",
                                            "label": "Uninstall",
                                            "icon": "trash-outline",
                                            "visibleIf": "$state.applicationInstalled == true && $state.applicationRemovable == true",
                                        },
                                        {
                                            "id": "preview",
                                            "label": "Preview",
                                            "icon": "open-outline",
                                            "visibleIf": "$state.localDevelopmentAvailable == true && $state.developmentPreviewWebspaceId && $state.developmentObjectId",
                                        },
                                        {
                                            "id": "open-builder",
                                            "label": "Open in Builder",
                                            "icon": "construct-outline",
                                            "visibleIf": "$state.localDevelopmentAvailable == true && $state.developmentObjectId",
                                        },
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "click:install",
                                        "type": "callMcp",
                                        "target": "applications.plan",
                                        "idempotencyKey": "auto",
                                        "resultStateKey": "reviewedPlan",
                                        "prototypeFixture": "$state.prototypeFixtures.plan",
                                        "enabledIf": "$state.selectedApplicationId",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "kind": "install",
                                            "expected_revision": "$state.installationRevision",
                                            "release_digest": "$state.selectedReleaseDigest",
                                            "data_policy": "retain",
                                        },
                                    },
                                    {
                                        "on": "click:update",
                                        "type": "callMcp",
                                        "target": "applications.plan",
                                        "idempotencyKey": "auto",
                                        "resultStateKey": "reviewedPlan",
                                        "prototypeFixture": "$state.prototypeFixtures.plan",
                                        "enabledIf": "$state.selectedApplicationId",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "kind": "update",
                                            "expected_revision": "$state.installationRevision",
                                            "release_digest": "$state.selectedReleaseDigest",
                                        },
                                    },
                                    {
                                        "on": "click:select-track",
                                        "type": "callMcp",
                                        "target": "applications.plan",
                                        "idempotencyKey": "auto",
                                        "resultStateKey": "reviewedPlan",
                                        "prototypeFixture": "$state.prototypeFixtures.plan",
                                        "enabledIf": "$state.selectedApplicationId",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "kind": "select_track",
                                            "expected_revision": "$state.subscriptionRevision",
                                            "update_track": "$state.updateTrack",
                                            "update_policy": "$state.updatePolicy",
                                            "paused": False,
                                        },
                                    },
                                    {
                                        "on": "click:remove",
                                        "type": "callMcp",
                                        "target": "applications.plan",
                                        "idempotencyKey": "auto",
                                        "resultStateKey": "reviewedPlan",
                                        "prototypeFixture": "$state.prototypeFixtures.plan",
                                        "enabledIf": "$state.selectedApplicationId",
                                        "params": {
                                            "application_id": "$state.selectedApplicationId",
                                            "kind": "remove",
                                            "expected_revision": "$state.installationRevision",
                                            "data_policy": "$state.removeDataPolicy",
                                        },
                                    },
                                    {
                                        "on": "click:preview",
                                        "type": "openWorkspace",
                                        "params": {
                                            "newWindow": True,
                                            "workspaceId": "$state.developmentPreviewWebspaceId",
                                            "expectedScenarioId": "$state.developmentObjectId",
                                        },
                                    },
                                    {
                                        "on": "click:open-builder",
                                        "type": "openWorkspace",
                                        "params": {
                                            "ensureBuilderWorkbench": True,
                                            "newWindow": True,
                                            "selectedObjectType": "$state.developmentObjectType",
                                            "selectedObjectId": "$state.developmentObjectId",
                                            "sourceWebspaceId": "$state.developmentSourceWebspaceId",
                                        },
                                    },
                                ],
                            },
                            {
                                "id": "review-actions",
                                "type": "ui.actions",
                                "area": "detail",
                                "visibleIf": "$state.reviewedPlan.operation.operation_id && $state.reviewedPlan.operation.plan_digest",
                                "inputs": {
                                    "variant": "toolbar",
                                    "buttons": [
                                        {
                                            "id": "confirm-install",
                                            "label": "Install",
                                            "icon": "download-outline",
                                            "visibleIf": "$state.reviewedPlan.operation.kind == 'install'",
                                        },
                                        {
                                            "id": "confirm-update",
                                            "label": "Update",
                                            "icon": "refresh-outline",
                                            "visibleIf": "$state.reviewedPlan.operation.kind == 'update'",
                                        },
                                        {
                                            "id": "confirm-select-track",
                                            "label": "Save settings",
                                            "icon": "checkmark-outline",
                                            "visibleIf": "$state.reviewedPlan.operation.kind == 'select_track'",
                                        },
                                        {
                                            "id": "confirm-remove",
                                            "label": "Uninstall",
                                            "icon": "trash-outline",
                                            "visibleIf": "$state.reviewedPlan.operation.kind == 'remove'",
                                        },
                                        {
                                            "id": "cancel-review",
                                            "label": "Cancel",
                                            "icon": "close-outline",
                                        },
                                    ],
                                },
                                "actions": [
                                    *[
                                        {
                                            "on": f"click:confirm-{button_kind}",
                                            "type": "callMcp",
                                            "target": "applications.apply",
                                            "idempotencyKey": "auto",
                                            "prototypeFixture": "$state.prototypeFixtures.apply",
                                            "resultStateKey": "reviewedPlan",
                                            "enabledIf": f"$state.reviewedPlan.operation.kind == '{operation_kind}' && $state.reviewedPlan.operation.operation_id && $state.reviewedPlan.operation.plan_digest",
                                            "params": {
                                                "operation_id": "$state.reviewedPlan.operation.operation_id",
                                                "plan_digest": "$state.reviewedPlan.operation.plan_digest",
                                            },
                                        }
                                        for button_kind, operation_kind in (
                                            ("install", "install"),
                                            ("update", "update"),
                                            ("select-track", "select_track"),
                                            ("remove", "remove"),
                                        )
                                    ],
                                    {
                                        "on": "click:cancel-review",
                                        "type": "updateState",
                                        "params": {"reviewedPlan": {}},
                                    },
                                ],
                            },
                            *[
                                {
                                    "id": section_id,
                                    "type": "item.details",
                                    "area": "detail"
                                    if title == "Details"
                                    else "metadata",
                                    "title": title,
                                    "visibleIf": (
                                        "$state.activeTab == 'details' && "
                                        "$state.selectedApplicationId"
                                        + extra_visibility
                                    ),
                                    "dataSource": source(
                                        "applications.show",
                                        "response.result.application",
                                        selected=True,
                                    ),
                                    "inputs": {
                                        "presentation": "section",
                                        "fields": fields,
                                        **(
                                            {"emptyText": "Not installed."}
                                            if title == "Installation"
                                            else {"emptyText": "No categories."}
                                            if title == "Categories"
                                            else {}
                                        ),
                                    },
                                }
                                for section_id, title, fields, extra_visibility in (
                                    (
                                        "application-section",
                                        "Details",
                                        [
                                            {
                                                "label": "Identifier",
                                                "path": "application.application_id",
                                            },
                                            {
                                                "label": "Publisher",
                                                "path": "application.publisher.display_name",
                                            },
                                            {
                                                "label": "Lifecycle",
                                                "path": "application.lifecycle",
                                            },
                                        ],
                                        "",
                                    ),
                                    (
                                        "installation-section",
                                        "Installation",
                                        [
                                            {
                                                "label": "Installed version",
                                                "path": "installed_release.version",
                                            },
                                            {
                                                "label": "Status",
                                                "path": "installation.status",
                                            },
                                            {
                                                "label": "Updated",
                                                "path": "installation.updated_at",
                                                "format": "datetime",
                                            },
                                            {
                                                "label": "Update track",
                                                "path": "subscription.update_track",
                                            },
                                            {
                                                "label": "Update policy",
                                                "path": "subscription.update_policy",
                                            },
                                        ],
                                        "",
                                    ),
                                    (
                                        "marketplace-section",
                                        "Marketplace",
                                        [
                                            {
                                                "label": "Stable version",
                                                "path": "marketplace_release.version",
                                            },
                                            {
                                                "label": "Pre-release version",
                                                "path": "prerelease_release.version",
                                            },
                                            {
                                                "label": "Last released",
                                                "path": "marketplace_release.published_at",
                                                "format": "datetime",
                                            },
                                            {
                                                "label": "Visibility",
                                                "path": "application.visibility",
                                            },
                                        ],
                                        "",
                                    ),
                                    (
                                        "categories-section",
                                        "Categories",
                                        [
                                            {
                                                "label": "Categories",
                                                "path": "application.display.categories",
                                            },
                                        ],
                                        "",
                                    ),
                                    (
                                        "development-section",
                                        "My development",
                                        [
                                            {
                                                "label": "Phase",
                                                "path": "local_development.phase",
                                            },
                                            {
                                                "label": "Status",
                                                "path": "local_development.status",
                                            },
                                            {
                                                "label": "Publication",
                                                "path": "local_development.publication_status",
                                            },
                                            {
                                                "label": "Revision",
                                                "path": "local_development.revision",
                                            },
                                            {
                                                "label": "Updated",
                                                "path": "local_development.updated_at",
                                                "format": "datetime",
                                            },
                                        ],
                                        " && $state.localDevelopmentAvailable == true",
                                    ),
                                )
                            ],
                        ],
                    }
                }
            }
        },
    }

    value_prefixes = {
        "application.lifecycle": "applications.lifecycle.",
        "application.visibility": "applications.visibility.",
        "application.display.categories": "applications.category.",
        "installation.status": "applications.installation.status.",
        "subscription.update_track": "applications.update_track.",
        "subscription.update_policy": "applications.update_policy.",
        "local_development.phase": "applications.development.phase.",
        "local_development.status": "applications.development.status.",
        "local_development.publication_status": "applications.development.publication_status.",
        "operation.kind": "applications.operation.kind.",
        "operation.status": "applications.operation.status.",
        "lifecycle": "applications.release.lifecycle.",
        "kind": "applications.operation.kind.",
        "status": "applications.operation.status.",
    }
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    for widget in widgets:
        inputs = widget.get("inputs") or {}
        for key_name, prefix_name in (
            ("titleKey", "titleI18nPrefix"),
            ("subtitleKey", "subtitleI18nPrefix"),
            ("previewKey", "previewI18nPrefix"),
            ("badgeKey", "badgeI18nPrefix"),
        ):
            path_value = str(inputs.get(key_name) or "")
            if path_value in value_prefixes:
                inputs[prefix_name] = value_prefixes[path_value]
        for collection_name in ("meta", "fields"):
            for field in inputs.get(collection_name) or []:
                path_value = str(field.get("path") or field.get("key") or "")
                if path_value in value_prefixes:
                    field["valueI18nPrefix"] = value_prefixes[path_value]

    fixed_fields = {
        "title",
        "label",
        "searchPlaceholder",
        "emptyText",
        "loadingText",
        "trueLabel",
        "falseLabel",
        "addItemLabel",
        "moveItemLabel",
    }
    fixture_fields = {"title", "summary", "review_summary"}
    application_recipe = next(
        item
        for item in ui_capability_catalog()["recipes"]
        if item["id"] == "recipe.application_manager"
    )
    canonical_russian = application_recipe["composition"]["localization"]["glossary"]

    def add_localizations(
        value: object,
        *,
        path: tuple[str, ...] = (),
        inside_fixtures: bool = False,
    ) -> None:
        if isinstance(value, dict):
            nested_inside_fixtures = inside_fixtures or (
                bool(path) and path[-1] == "prototypeFixtures"
            )
            additions: dict[str, object] = {}
            for key, raw in value.items():
                add_localizations(
                    raw,
                    path=(*path, key),
                    inside_fixtures=nested_inside_fixtures,
                )
                required = key in fixed_fields or (
                    nested_inside_fixtures
                    and "when" not in path
                    and key in fixture_fields
                )
                if not required or key.endswith("_i18n"):
                    continue
                if isinstance(raw, str):
                    fallback = raw.strip()
                elif key == "categories" and isinstance(raw, list):
                    fallback = ", ".join(str(item).strip() for item in raw)
                else:
                    continue
                if (
                    not fallback
                    or fallback.startswith("$state.")
                    or (fallback.startswith("{") and fallback.endswith("}"))
                ):
                    continue
                additions[f"{key}_i18n"] = {
                    "key": "test.applications." + ".".join((*path, key)),
                    "translations": {
                        "en": fallback,
                        "ru": canonical_russian.get(fallback, f"ru: {fallback}"),
                    },
                }
            value.update(additions)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                add_localizations(
                    item,
                    path=(*path, str(index)),
                    inside_fixtures=inside_fixtures,
                )

    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    widgets = page["widgets"]
    lifecycle = next(
        widget for widget in widgets if widget["id"] == "lifecycle-actions"
    )
    tabs = next(widget for widget in widgets if widget["id"] == "tabs")
    widgets.remove(lifecycle)
    widgets.remove(tabs)
    header_index = next(
        index
        for index, widget in enumerate(widgets)
        if widget["id"] == "application-header"
    )
    widgets.insert(header_index + 1, lifecycle)
    widgets.insert(header_index + 2, tabs)

    detail_order = [
        "details",
        "application-header",
        "lifecycle-actions",
        "prerelease-following",
        "automatic-updates",
        "reviewed-plan",
        "review-actions",
        "tabs",
        "application-section",
        "releases",
        "operations",
        "reports",
        "application-access-permissions",
        "application-access-access",
        "application-access-roles",
        "application-access-connected_accounts",
        "application-access-release_readiness",
        "application-access-activity",
        "users-access-tabs",
        "users-access-people",
        "users-access-guests",
        "users-access-children",
        "users-access-devices",
        "users-access-sessions",
        "users-access-application_access",
        "users-access-activity",
    ]
    detail_rank = {widget_id: index for index, widget_id in enumerate(detail_order)}
    widgets.sort(
        key=lambda widget: (
            {"master": 0, "detail": 1, "metadata": 2}.get(widget.get("area"), 3),
            detail_rank.get(str(widget.get("id") or ""), len(detail_rank))
            if widget.get("area") == "detail"
            else 0,
        )
    )

    add_localizations(webui)
    lifecycle = next(
        widget
        for widget in webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
        if widget["id"] == "lifecycle-actions"
    )
    expected_ru = {
        "install": "Установить",
        "update": "Обновить",
        "select-track": "Сохранить настройки обновлений",
        "remove": "Удалить",
        "preview": "Предпросмотр",
        "open-builder": "Открыть в Builder",
    }
    for button in lifecycle["inputs"]["buttons"]:
        button["label_i18n"]["translations"]["ru"] = expected_ru[button["id"]]
    return webui


def test_application_manager_evaluation_enforces_mcp_and_review_boundary() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()

    accepted = evaluate_ui_request(request, webui)

    assert accepted["ok"] is True
    assert all(item["ok"] for item in accepted["postconditions"])

    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    actions = next(widget for widget in widgets if widget["id"] == "review-actions")[
        "actions"
    ]
    actions.pop(0)
    rejected = evaluate_ui_request(request, webui)

    assert rejected["ok"] is False
    boundary = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.reviewed_plan_apply"
    )
    assert boundary["ok"] is False


def test_application_access_prototype_fixtures_match_widget_shapes() -> None:
    webui = _application_manager_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    fixtures = page["initialState"]["prototypeFixtures"]
    widgets = {widget["id"]: widget for widget in page["widgets"]}
    widget_ids = [widget["id"] for widget in page["widgets"]]

    assert widget_ids.index("application-header") < widget_ids.index(
        "prerelease-following"
    ) < widget_ids.index("tabs") < widget_ids.index(
        "application-access-permissions"
    )

    for section in (
        "permissions",
        "access",
        "roles",
        "connected_accounts",
        "release_readiness",
        "activity",
    ):
        widget = widgets[f"application-access-{section}"]
        assert widget["dataSource"]["prototypeFixture"] == (
            f"$state.prototypeFixtures.applicationAccess.{section}"
        )
        assert "result" in fixtures["applicationAccess"][section]

    for section in (
        "people",
        "guests",
        "children",
        "devices",
        "sessions",
        "application_access",
        "activity",
    ):
        widget = widgets[f"users-access-{section}"]
        assert widget["dataSource"]["prototypeFixture"] == (
            f"$state.prototypeFixtures.usersAccess.{section}"
        )
        assert isinstance(fixtures["usersAccess"][section]["result"], list)

    access = widgets["application-access-access"]
    assert access["type"] == "ui.list"
    assert access["actions"][0]["params"]["selectedAccessGrantId"] == (
        "$event.grant_id"
    )
    assert widgets["access-change-form"]["actions"][0]["target"] == (
        "applications.access.change"
    )
    for tab_widget_id in ("tabs", "users-access-tabs"):
        tab_widget = widgets[tab_widget_id]
        assert tab_widget["type"] == "navigation.tabs"
        assert all(
            button.get("icon") for button in tab_widget["inputs"]["buttons"]
        )


def test_application_manager_evaluation_requires_bilingual_prototype_text() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    sections = next(widget for widget in widgets if widget["id"] == "catalog-sections")
    sections["inputs"]["buttons"][0].pop("label_i18n")

    rejected = evaluate_ui_request(request, webui)

    localization = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.localization"
    )
    assert localization["ok"] is False
    assert localization["actual"]["missing"] == [
        "pages.0.widgets.0.inputs.buttons.0.label"
    ]


def test_application_manager_evaluation_requires_canonical_russian_glossary() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    sections = next(widget for widget in widgets if widget["id"] == "catalog-sections")
    descriptor = sections["inputs"]["buttons"][0]["label_i18n"]
    descriptor["translations"]["ru"] = "Applications"

    rejected = evaluate_ui_request(request, webui)

    localization = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.localization"
    )
    assert localization["ok"] is False
    assert localization["actual"]["localeValueMismatches"] == [
        {
            "path": "pages.0.widgets.0.inputs.buttons.0.label",
            "key": descriptor["key"],
            "locale": "ru",
            "expected": "Приложения",
            "actual": "Applications",
        }
    ]


def test_application_manager_evaluation_rejects_placeholder_locale_entries() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()

    rejected = evaluate_ui_request(
        request,
        webui,
        locale_dictionaries={
            "en": {"applications.unused.placeholder": "placeholder"},
            "ru": {"applications.unused.placeholder": "placeholder"},
        },
    )

    localization = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.localization"
    )
    assert localization["ok"] is False
    assert localization["actual"]["invalidLocaleEntries"] == [
        {"key": "applications.unused.placeholder", "locales": ["en", "ru"]}
    ]


def test_application_manager_evaluation_accepts_scenario_locale_assets() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    application = webui["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    sections = next(
        widget for widget in page["widgets"] if widget["id"] == "catalog-sections"
    )
    sections["inputs"]["buttons"][0]["label_i18n"] = (
        "applications.navigation.applications"
    )
    application["resources"] = {
        "applications.i18n.en": {
            "kind": "data",
            "role": "i18n",
            "locale": "en",
            "path": "assets/i18n/en.json",
        },
        "applications.i18n.ru": {
            "kind": "data",
            "role": "i18n",
            "locale": "ru",
            "path": "assets/i18n/ru.json",
        },
    }

    accepted = evaluate_ui_request(
        request,
        webui,
        locale_dictionaries={
            "en": {"applications.navigation.applications": "Applications"},
            "ru": {"applications.navigation.applications": "Приложения"},
        },
    )

    localization = next(
        item
        for item in accepted["postconditions"]
        if item["id"] == "applications.localization"
    )
    assert localization["ok"] is True


def test_application_manager_evaluation_requires_canonical_value_prefixes() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    developments = next(
        widget for widget in widgets if widget["id"] == "catalog-developments"
    )
    developments["inputs"]["meta"][0].pop("valueI18nPrefix")

    rejected = evaluate_ui_request(request, webui)

    localization = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.localization"
    )
    assert localization["ok"] is False
    assert localization["actual"]["missingValuePrefixes"][0]["field"] == (
        "local_development.phase"
    )


def test_application_manager_evaluation_enforces_update_defaults() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"]["automaticUpdates"] = False
    fixture = page["initialState"]["prototypeFixtures"]["developments"]["result"][0]
    fixture["auto_update_enabled"] = False

    rejected = evaluate_ui_request(request, webui)

    by_id = {item["id"]: item for item in rejected["postconditions"]}
    assert by_id["applications.detail_lifecycle_binding"]["ok"] is False
    assert by_id["applications.prototype_fixtures"]["ok"] is False
    assert by_id["applications.prototype_fixtures"]["actual"][
        "nonDefaultInstalledApplications"
    ] == ["development-1"]


def test_application_manager_evaluation_requires_permissions_for_every_plan_kind() -> (
    None
):
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    cases = webui["ui"]["application"]["desktop"]["pageSchema"]["initialState"][
        "prototypeFixtures"
    ]["plan"]["cases"]
    remove = next(case for case in cases if case["when"]["kind"] == "remove")
    remove["result"]["operation"]["plan"]["permissions"] = []

    rejected = evaluate_ui_request(request, webui)

    fixture_check = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.prototype_fixtures"
    )
    assert fixture_check["ok"] is False
    assert fixture_check["actual"]["planKinds"] == [
        "install",
        "select_track",
        "update",
    ]
    assert fixture_check["actual"]["invalidPlanCases"] == [
        {"kind": "remove", "missing": ["non-empty plan.permissions"]}
    ]
    assert (
        "non-empty plan.permissions"
        in fixture_check["actual"]["missingRequirements"][0]
    )


def test_application_manager_evaluation_rejects_named_fixture_placeholders() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    fixtures = webui["ui"]["application"]["desktop"]["pageSchema"]["initialState"][
        "prototypeFixtures"
    ]
    fixtures.update(
        {
            key: f"{key}-fixture"
            for key in (
                "applications",
                "developments",
                "application",
                "releases",
                "operations",
                "reports",
                "plan",
                "apply",
            )
        }
    )

    rejected = evaluate_ui_request(request, webui)

    fixture_check = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.prototype_fixtures"
    )
    assert fixture_check["ok"] is False
    assert fixture_check["actual"]["executableProfiles"] == []


def test_application_manager_evaluation_requires_fixture_on_every_mcp_widget() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    metadata = next(
        widget for widget in widgets if widget["id"] == "marketplace-section"
    )
    metadata["dataSource"].pop("prototypeFixture")

    rejected = evaluate_ui_request(request, webui)

    fixture_check = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.prototype_fixtures"
    )
    assert fixture_check["ok"] is False
    assert fixture_check["actual"]["invalidSourceFixtures"] == [
        {
            "widgetId": "marketplace-section",
            "toolId": "applications.show",
            "expected": "$state.prototypeFixtures.application",
            "actual": "",
        }
    ]


def test_application_manager_evaluation_requires_detail_case_for_every_selectable_fixture() -> (
    None
):
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    fixtures = webui["ui"]["application"]["desktop"]["pageSchema"]["initialState"][
        "prototypeFixtures"
    ]
    fixtures["application"]["cases"] = [
        case
        for case in fixtures["application"]["cases"]
        if case["when"]["application_id"] != "development-2"
    ]

    rejected = evaluate_ui_request(request, webui)

    fixture_check = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.prototype_fixtures"
    )
    assert fixture_check["ok"] is False
    assert fixture_check["actual"]["uncoveredApplicationIds"] == ["development-2"]


def test_application_manager_evaluation_rejects_stale_review_context() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    catalog = next(
        widget for widget in widgets if widget["id"] == "catalog-applications"
    )
    catalog["actions"][0]["params"].pop("reviewedPlan")
    operations = next(widget for widget in widgets if widget["id"] == "operations")
    operations["inputs"].pop("titleKey")
    review_actions = next(
        widget for widget in widgets if widget["id"] == "review-actions"
    )
    apply = next(
        action
        for action in review_actions["actions"]
        if action.get("target") == "applications.apply"
    )
    apply.pop("resultStateKey")

    rejected = evaluate_ui_request(request, webui)

    by_id = {item["id"]: item for item in rejected["postconditions"]}
    assert by_id["applications.master_selection"]["ok"] is False
    assert by_id["applications.reviewed_plan_apply"]["ok"] is False
    assert by_id["applications.detail_lifecycle_binding"]["ok"] is False


def test_application_manager_accepts_canonical_context_reset_actions() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    for widget_id in ("catalog-sections", "installed-only"):
        widget = next(widget for widget in widgets if widget["id"] == widget_id)
        widget["actions"][0]["params"]["reviewedPlan"] = {}

    evaluated = evaluate_ui_request(request, webui)

    by_id = {item["id"]: item for item in evaluated["postconditions"]}
    assert by_id["applications.catalog_sections"]["ok"] is True
    assert by_id["applications.detail_state_binding"]["ok"] is True
    assert by_id["applications.detail_lifecycle_binding"]["ok"] is True


def test_application_manager_evaluation_rejects_non_runtime_event_paths() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    catalog = next(
        widget for widget in widgets if widget["id"] == "catalog-applications"
    )
    catalog["actions"][0]["params"]["selectedApplicationId"] = "$event.item.id"
    tabs = next(widget for widget in widgets if widget["id"] == "tabs")
    tabs["actions"][0]["params"]["activeTab"] = "$event.buttonId"

    rejected = evaluate_ui_request(request, webui)

    assert rejected["ok"] is False
    by_id = {item["id"]: item for item in rejected["postconditions"]}
    assert by_id["applications.master_selection"]["ok"] is False
    assert by_id["applications.tabs"]["ok"] is False


def test_application_manager_evaluation_reports_catalog_widget_mismatches() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    catalog = next(
        widget for widget in widgets if widget["id"] == "catalog-applications"
    )
    catalog["inputs"].pop("meta")

    rejected = evaluate_ui_request(request, webui)

    catalog_check = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.catalog_sections"
    )
    applications = catalog_check["actual"]["sectionCandidates"]["applications"]
    assert applications == [
        {"widgetId": "catalog-applications", "mismatches": ["inputs.meta"]},
        {
            "widgetId": "catalog-developments",
            "mismatches": [
                "dataSource.arguments",
                "visibleIf",
                "inputs.subtitleKey",
                "inputs.previewKey",
                "inputs.meta",
            ],
        },
    ]


def test_application_manager_evaluation_rejects_wide_aux_layout_and_unguarded_install() -> (
    None
):
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["layout"] = {
        "version": 2,
        "pattern": "workbench",
        "density": "comfortable",
        "regions": [
            {
                "id": "master",
                "role": "main",
                "presentation": {"wide": "pane", "compact": "stack"},
            },
            {
                "id": "detail",
                "role": "utility",
                "presentation": {"wide": "pane", "compact": "stack"},
            },
        ],
    }
    lifecycle = next(
        widget for widget in page["widgets"] if widget["id"] == "lifecycle-actions"
    )
    lifecycle["inputs"]["buttons"][0]["visibleIf"] = (
        "$state.applicationInstalled != true"
    )

    rejected = evaluate_ui_request(request, webui)

    assert rejected["ok"] is False
    by_id = {item["id"]: item for item in rejected["postconditions"]}
    assert by_id["applications.sidebar_layout"]["ok"] is False
    assert by_id["applications.detail_lifecycle_binding"]["ok"] is False
    assert (
        by_id["applications.detail_lifecycle_binding"]["actual"][
            "installReleaseGuarded"
        ]
        is False
    )


def test_application_manager_evaluation_rejects_technical_lifecycle_commands() -> None:
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    lifecycle = next(
        widget for widget in widgets if widget["id"] == "lifecycle-actions"
    )
    lifecycle["inputs"]["buttons"][0]["label"] = "Plan install"
    review_actions = next(
        widget for widget in widgets if widget["id"] == "review-actions"
    )
    lifecycle["inputs"]["buttons"].append(
        {"id": "apply", "label": "Apply reviewed plan", "icon": "checkmark-outline"}
    )
    lifecycle["actions"].append(review_actions["actions"][0])

    rejected = evaluate_ui_request(request, webui)

    review = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.review_composition"
    )
    assert review["ok"] is False
    assert review["actual"]["technicalLabels"] == [
        "Apply reviewed plan",
        "Plan install",
    ]
    assert review["actual"]["confirmationSeparated"] is False


def test_application_manager_evaluation_rejects_technical_russian_lifecycle_commands() -> (
    None
):
    request = (
        "Build Applications lifecycle manager with Extensions, installed, and MCP."
    )
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    lifecycle = next(
        widget for widget in widgets if widget["id"] == "lifecycle-actions"
    )
    install = next(
        button for button in lifecycle["inputs"]["buttons"] if button["id"] == "install"
    )
    install["label_i18n"]["translations"]["ru"] = "Спланировать установку"

    rejected = evaluate_ui_request(request, webui)

    review = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "applications.review_composition"
    )
    assert review["ok"] is False
    assert review["actual"]["localeValueMismatches"] == [
        {
            "path": f"pages.0.widgets.{widgets.index(lifecycle)}.inputs.buttons.0.label",
            "key": f"test.applications.ui.application.desktop.pageSchema.widgets.{widgets.index(lifecycle)}.inputs.buttons.0.label",
            "locale": "ru",
            "expected": "Установить",
            "actual": "Спланировать установку",
        }
    ]


def test_capability_validation_rejects_unknown_layout_and_board_lane() -> None:
    webui = _board_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["layout"]["pattern"] = "masonry"
    page["widgets"][0]["dataSource"]["value"][0]["status"] = "missing"

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert {item["code"] for item in result["findings"]} == {
        "ui.layout.pattern_unsupported",
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
                        "params": {
                            "operation_id": "create",
                            "payload": "$event.values",
                        },
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
    finding_codes = {
        item["code"] for item in rejected["capability_validation"]["findings"]
    }

    assert rejected["ok"] is False
    assert {
        "kanban.query_binding",
        "kanban.create_form",
        "kanban.edit_form",
    } <= failed_ids
    assert "ui.board.create_event_invalid" in finding_codes


def test_resource_board_query_binding_rejects_nested_event_object() -> None:
    webui = _resource_board_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    search = next(widget for widget in page["widgets"] if widget.get("id") == "search")
    search["actions"][0]["params"] = {"searchQuery": {"search": "$event.value"}}

    result = evaluate_ui_request(
        "Show a kanban board with search, create, edit, delete, and drag and drop.",
        webui,
        prototype_records=[],
    )

    query_binding = next(
        item
        for item in result["postconditions"]
        if item["id"] == "kanban.query_binding"
    )
    assert query_binding["ok"] is False
    assert query_binding["actual"]["executableRefs"] == []


def test_resource_board_query_toolbar_writes_only_declared_owned_keys() -> None:
    from adaos.services.ui_capabilities import evaluate_ui_request

    webui = _resource_board_webui()
    page = webui['ui']['application']['desktop']['pageSchema']
    search = next(widget for widget in page['widgets'] if widget.get('id') == 'search')
    search.update(type='ui.queryToolbar', actions=[], inputs={'controls': [
        {'id': 'search', 'kind': 'search', 'inputType': 'search', 'label': 'Search', 'stateKey': 'searchQuery'},
    ]})
    for key, expected in [('searchQuery', True), ('unrelated', False)]:
        search['inputs']['controls'][0]['stateKey'] = key
        result = evaluate_ui_request('A kanban board with search and edit', webui, prototype_records=[])
        condition = next(item for item in result['postconditions'] if item['id'] == 'kanban.query_binding')
        assert condition['ok'] is expected


def test_board_selection_can_open_guarded_editor_from_details() -> None:
    from adaos.services.ui_capabilities import evaluate_ui_request

    webui = _resource_board_webui()
    application = webui['ui']['application']
    page = application['desktop']['pageSchema']
    editor = next(widget for widget in page['widgets'] if widget.get('id') == 'edit')
    page['widgets'].remove(editor)
    application['modals'] = {'edit-item': {'schema': {
        'id': 'edit-item', 'layout': {'type': 'stack', 'areas': [{'id': 'main'}]}, 'widgets': [editor],
    }}}
    action = {'on': 'click:edit', 'type': 'openModal', 'params': {'modalId': 'edit-item'},
              'enabledIf': "$state.selectedRecordId !== ''"}
    page['widgets'].append({'id': 'details', 'type': 'item.details', 'area': 'main', 'actions': [action]})
    for guard, expected in [("$state.selectedRecordId !== ''", True), ("$state.unrelated !== ''", False)]:
        action['enabledIf'] = guard
        result = evaluate_ui_request('A kanban board with search and edit', webui, prototype_records=[])
        condition = next(item for item in result['postconditions'] if item['id'] == 'kanban.edit_selection')
        assert condition['ok'] is expected


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
    page["widgets"] = [
        widget for widget in page["widgets"] if widget.get("id") != "edit"
    ]
    application["modals"] = {
        "edit-item": {
            "id": "edit-item",
            "schema": {
                "id": "edit-item-schema",
                "layout": {
                    "version": 2,
                    "pattern": "task-flow",
                    "density": "comfortable",
                    "regions": [
                        {
                            "id": "main",
                            "role": "main",
                            "presentation": {"wide": "pane", "compact": "stack"},
                        }
                    ],
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
    request = (
        "Show a kanban board with search, create, edit, delete, and drag and drop."
    )

    rejected = evaluate_ui_request(request, webui, prototype_records=[])
    rejected_selection = next(
        item
        for item in rejected["postconditions"]
        if item["id"] == "kanban.edit_selection"
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
        item
        for item in accepted["postconditions"]
        if item["id"] == "kanban.edit_selection"
    )
    assert accepted_selection["ok"] is True
