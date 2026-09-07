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


def test_application_manager_selection_exposes_mcp_master_detail_contract() -> None:
    selected = selected_ui_capabilities(
        "Create Applications, a full-screen application lifecycle manager with an "
        "Extensions-style catalog backed by MCP."
    )
    selected_ids = {item["id"] for item in selected["items"]}

    assert selected["root_item_ids"][0] == "recipe.application_manager"
    assert {
        "layout.split", "input.toggle", "input.selector", "ui.list",
        "item.details", "input.commandBar", "ui.actions",
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
        "planned", "applying", "succeeded", "failed", "unknown", "reconciling", "cancelled"
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
    assert fixture_model["canonical_shape"]["application"]["cases"][0]["result"].startswith(
        "$state.prototypeFixtures.samples."
    )
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
            "applications.list_releases": "releases",
            "applications.list_operations": "operations",
            "applications.list_development_reports": "reports",
        }[tool_id]
        return {
            "kind": "mcp",
            "toolId": tool_id,
            "arguments": {"application_id": "$state.selectedApplicationId"} if selected else {},
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
                            "reviewedPlan": {},
                            "prototypeFixtures": {
                                "applications": {"result": []},
                                "developments": {"result": development_fixtures},
                                "application": {
                                    "cases": [
                                        *[
                                            {
                                                "when": {
                                                    "application_id": fixture["application"]["application_id"]
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
                                                "result": {"prototype_state_id": state_id},
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
                                                        "permissions": ["workspace.read"],
                                                    },
                                                }
                                            },
                                        }
                                        for kind in ("install", "update", "select_track", "remove")
                                    ]
                                },
                                "apply": {"result": {"status": "succeeded"}},
                            },
                        },
                        "layout": {
                            "type": "split",
                            "pattern": "sidebar-content",
                            "sidebarWidth": 380,
                            "auxWidth": 300,
                            "areas": [
                                {"id": "master", "role": "sidebar"},
                                {"id": "detail", "role": "main"},
                                {"id": "metadata", "role": "aux"},
                            ],
                        },
                        "widgets": [
                            {
                                "id": "catalog-sections",
                                "type": "input.commandBar",
                                "area": "master",
                                "inputs": {
                                    "variant": "segmented",
                                    "size": "small",
                                    "stretch": True,
                                    "selectedStateKey": "catalogSection",
                                    "buttons": [
                                        {"id": "applications", "label": "Applications"},
                                        {"id": "developments", "label": "My developments"},
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
                                "dataSource": {"kind": "static", "value": "$state.installedOnly"},
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
                                                {"key": "local_development.phase", "label": "Phase", "kind": "badge"},
                                                {"key": "local_development.status", "label": "Status", "kind": "badge"},
                                                {"key": "local_development.publication_status", "label": "Publication", "kind": "badge"},
                                            ]
                                            if section == "developments"
                                            else [
                                                {"key": "installed", "label": "Installation", "kind": "boolean", "trueLabel": "Installed", "falseLabel": "Not installed"},
                                                {"key": "update_available", "label": "Update", "kind": "boolean", "trueLabel": "Update available", "falseLabel": "Current"},
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
                                "type": "input.commandBar",
                                "area": "detail",
                                "inputs": {
                                    "variant": "segmented",
                                    "selectedStateKey": "activeTab",
                                    "buttons": [
                                        {"id": value, "label": value.title()}
                                        for value in ("details", "versions", "operations", "reports")
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
                            {
                                "id": "details",
                                "type": "item.details",
                                "area": "detail",
                                "visibleIf": "$state.selectedApplicationId",
                                "dataSource": source("applications.show", "response.result.application", selected=True),
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
                                    }
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
                                        {"label": "Summary", "path": "application.display.summary"},
                                        {"label": "Publisher", "path": "application.publisher.display_name"},
                                        {"label": "Installed", "path": "installed_release.version"},
                                        {"label": "Marketplace", "path": "marketplace_release.version"},
                                    ],
                                },
                            },
                            {
                                "id": "releases",
                                "type": "ui.list",
                                "area": "detail",
                                "visibleIf": "$state.activeTab == 'versions' && $state.selectedApplicationId",
                                "dataSource": source("applications.list_releases", "response.result.releases", selected=True),
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
                                "dataSource": source("applications.list_operations", "response.result.operations", selected=True),
                                "inputs": {
                                    "itemIdKey": "operation_id",
                                    "titleKey": "summary",
                                    "subtitleKey": "kind",
                                    "previewKey": "kind",
                                    "meta": [
                                        {"key": "status", "label": "Status", "kind": "badge"},
                                    ],
                                    "emptyText": "No operations yet.",
                                },
                            },
                            {
                                "id": "reports",
                                "type": "ui.list",
                                "area": "detail",
                                "visibleIf": "$state.activeTab == 'reports' && $state.selectedApplicationId",
                                "dataSource": source("applications.list_development_reports", "response.result.reports"),
                                "inputs": {
                                    "itemIdKey": "report_id",
                                    "titleKey": "title",
                                    "subtitleKey": "summary",
                                    "previewKey": "summary",
                                    "meta": [
                                        {"key": "status", "label": "Status", "kind": "badge"},
                                    ],
                                    "filters": [
                                        {"key": "application_id", "stateKey": "selectedApplicationId"}
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
                                        {"label": "Snapshot and delete", "value": "snapshot_then_delete"},
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
                                "dataSource": {"kind": "static", "value": "$state.reviewedPlan"},
                                "inputs": {
                                    "presentation": "section",
                                    "fields": [
                                        {"label": "Operation", "path": "operation.kind"},
                                        {"label": "Summary", "path": "operation.plan.review_summary"},
                                        {"label": "Requested permissions", "path": "operation.plan.permissions"},
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
                                    ]
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
                                        {"id": "confirm-install", "label": "Install", "icon": "download-outline", "visibleIf": "$state.reviewedPlan.operation.kind == 'install'"},
                                        {"id": "confirm-update", "label": "Update", "icon": "refresh-outline", "visibleIf": "$state.reviewedPlan.operation.kind == 'update'"},
                                        {"id": "confirm-select-track", "label": "Save settings", "icon": "checkmark-outline", "visibleIf": "$state.reviewedPlan.operation.kind == 'select_track'"},
                                        {"id": "confirm-remove", "label": "Uninstall", "icon": "trash-outline", "visibleIf": "$state.reviewedPlan.operation.kind == 'remove'"},
                                        {"id": "cancel-review", "label": "Cancel", "icon": "close-outline"},
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
                                    "area": "detail" if title == "Details" else "metadata",
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
                                            {"label": "Identifier", "path": "application.application_id"},
                                            {"label": "Publisher", "path": "application.publisher.display_name"},
                                            {"label": "Lifecycle", "path": "application.lifecycle"},
                                        ],
                                        "",
                                    ),
                                    (
                                        "installation-section",
                                        "Installation",
                                        [
                                            {"label": "Installed version", "path": "installed_release.version"},
                                            {"label": "Status", "path": "installation.status"},
                                            {"label": "Updated", "path": "installation.updated_at", "format": "datetime"},
                                            {"label": "Update track", "path": "subscription.update_track"},
                                            {"label": "Update policy", "path": "subscription.update_policy"},
                                        ],
                                        "",
                                    ),
                                    (
                                        "marketplace-section",
                                        "Marketplace",
                                        [
                                            {"label": "Stable version", "path": "marketplace_release.version"},
                                            {"label": "Pre-release version", "path": "prerelease_release.version"},
                                            {"label": "Last released", "path": "marketplace_release.published_at", "format": "datetime"},
                                            {"label": "Visibility", "path": "application.visibility"},
                                        ],
                                        "",
                                    ),
                                    (
                                        "categories-section",
                                        "Categories",
                                        [
                                            {"label": "Categories", "path": "application.display.categories"},
                                        ],
                                        "",
                                    ),
                                    (
                                        "development-section",
                                        "My development",
                                        [
                                            {"label": "Phase", "path": "local_development.phase"},
                                            {"label": "Status", "path": "local_development.status"},
                                            {"label": "Publication", "path": "local_development.publication_status"},
                                            {"label": "Revision", "path": "local_development.revision"},
                                            {"label": "Updated", "path": "local_development.updated_at", "format": "datetime"},
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
                if not fallback or fallback.startswith("$state.") or (
                    fallback.startswith("{") and fallback.endswith("}")
                ):
                    continue
                additions[f"{key}_i18n"] = {
                    "key": "test.applications." + ".".join((*path, key)),
                    "translations": {"en": fallback, "ru": f"ru: {fallback}"},
                }
            value.update(additions)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                add_localizations(
                    item,
                    path=(*path, str(index)),
                    inside_fixtures=inside_fixtures,
                )

    add_localizations(webui)
    return webui


def test_application_manager_evaluation_enforces_mcp_and_review_boundary() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()

    accepted = evaluate_ui_request(request, webui)

    assert accepted["ok"] is True
    assert all(item["ok"] for item in accepted["postconditions"])

    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    actions = next(widget for widget in widgets if widget["id"] == "review-actions")["actions"]
    actions.pop(0)
    rejected = evaluate_ui_request(request, webui)

    assert rejected["ok"] is False
    boundary = next(
        item for item in rejected["postconditions"]
        if item["id"] == "applications.reviewed_plan_apply"
    )
    assert boundary["ok"] is False


def test_application_manager_evaluation_requires_bilingual_prototype_text() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
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


def test_application_manager_evaluation_accepts_scenario_locale_assets() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    application = webui["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    sections = next(widget for widget in page["widgets"] if widget["id"] == "catalog-sections")
    sections["inputs"]["buttons"][0]["label_i18n"] = "applications.navigation.applications"
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
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    developments = next(widget for widget in widgets if widget["id"] == "catalog-developments")
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
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
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


def test_application_manager_evaluation_rejects_named_fixture_placeholders() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    fixtures = webui["ui"]["application"]["desktop"]["pageSchema"]["initialState"]["prototypeFixtures"]
    fixtures.update({key: f"{key}-fixture" for key in (
        "applications", "developments", "application", "releases",
        "operations", "reports", "plan", "apply",
    )})

    rejected = evaluate_ui_request(request, webui)

    fixture_check = next(
        item for item in rejected["postconditions"]
        if item["id"] == "applications.prototype_fixtures"
    )
    assert fixture_check["ok"] is False
    assert fixture_check["actual"]["executableProfiles"] == []


def test_application_manager_evaluation_requires_fixture_on_every_mcp_widget() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    metadata = next(widget for widget in widgets if widget["id"] == "marketplace-section")
    metadata["dataSource"].pop("prototypeFixture")

    rejected = evaluate_ui_request(request, webui)

    fixture_check = next(
        item for item in rejected["postconditions"]
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


def test_application_manager_evaluation_requires_detail_case_for_every_selectable_fixture() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
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
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    catalog = next(widget for widget in widgets if widget["id"] == "catalog-applications")
    catalog["actions"][0]["params"].pop("reviewedPlan")
    operations = next(widget for widget in widgets if widget["id"] == "operations")
    operations["inputs"].pop("titleKey")
    review_actions = next(widget for widget in widgets if widget["id"] == "review-actions")
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


def test_application_manager_evaluation_rejects_non_runtime_event_paths() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    catalog = next(widget for widget in widgets if widget["id"] == "catalog-applications")
    catalog["actions"][0]["params"]["selectedApplicationId"] = "$event.item.id"
    tabs = next(widget for widget in widgets if widget["id"] == "tabs")
    tabs["actions"][0]["params"]["activeTab"] = "$event.buttonId"

    rejected = evaluate_ui_request(request, webui)

    assert rejected["ok"] is False
    by_id = {item["id"]: item for item in rejected["postconditions"]}
    assert by_id["applications.master_selection"]["ok"] is False
    assert by_id["applications.tabs"]["ok"] is False


def test_application_manager_evaluation_reports_catalog_widget_mismatches() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    catalog = next(widget for widget in widgets if widget["id"] == "catalog-applications")
    catalog["inputs"].pop("meta")

    rejected = evaluate_ui_request(request, webui)

    catalog_check = next(
        item for item in rejected["postconditions"]
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


def test_application_manager_evaluation_rejects_wide_aux_layout_and_unguarded_install() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["layout"] = {
        "type": "split",
        "pattern": "split",
        "areas": [
            {"id": "master", "role": "main"},
            {"id": "detail", "role": "aux"},
        ],
    }
    lifecycle = next(widget for widget in page["widgets"] if widget["id"] == "lifecycle-actions")
    lifecycle["inputs"]["buttons"][0]["visibleIf"] = "$state.applicationInstalled != true"

    rejected = evaluate_ui_request(request, webui)

    assert rejected["ok"] is False
    by_id = {item["id"]: item for item in rejected["postconditions"]}
    assert by_id["applications.sidebar_layout"]["ok"] is False
    assert by_id["applications.detail_lifecycle_binding"]["ok"] is False
    assert by_id["applications.detail_lifecycle_binding"]["actual"]["installReleaseGuarded"] is False


def test_application_manager_evaluation_rejects_technical_lifecycle_commands() -> None:
    request = "Build Applications lifecycle manager with Extensions, installed, and MCP."
    webui = _application_manager_webui()
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    lifecycle = next(widget for widget in widgets if widget["id"] == "lifecycle-actions")
    lifecycle["inputs"]["buttons"][0]["label"] = "Plan install"
    review_actions = next(widget for widget in widgets if widget["id"] == "review-actions")
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
