from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder_domain_packs import (
    domain_pack_ids_for_recipes,
    load_domain_pack,
)
from adaos.services.ui_capabilities import (
    evaluate_ui_request,
    get_ui_capability,
    qualify_ui_request,
    selected_ui_capabilities,
    ui_capability_catalog,
    validate_webui_capabilities,
)


APPLICATION_PACKS = ("applications.compatibility.v1",)


def test_readonly_comparison_does_not_require_excluded_persistence():
    qualification = qualify_ui_request('Show read-only comparison cards. No editing, live calculations or external services are needed.')
    assert 'update' not in qualification['requirements']['brief_operation_kinds']
    assert not qualification['requirements']['prototype_resource']


def test_board_qualification_uses_the_same_exclusions_as_the_brief():
    qualification = qualify_ui_request('Show a kanban board. No editing, search or filters are needed.')
    requirements = qualification['requirements']
    assert requirements['operation_kinds'] == []
    assert not requirements['record_edit']
    assert not requirements['resource_query']


def _empty_webui() -> dict:
    return {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "empty",
                        "layout": {
                            "version": 2,
                            "pattern": "document",
                            "density": "comfortable",
                            "regions": [
                                {
                                    "id": "main",
                                    "role": "main",
                                    "presentation": {"wide": "pane", "compact": "stack"},
                                }
                            ],
                        },
                        "widgets": [],
                    }
                }
            }
        },
    }


def test_modal_resource_query_inherits_desktop_selection_defaults() -> None:
    webui = _empty_webui()
    application = webui["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    page["initialState"] = {"selected_item": ""}
    application["modals"] = {"editor": {"schema": {
        "id": "editor", "layout": page["layout"],
        "widgets": [{"id": "details", "type": "item.details", "area": "main",
                     "dataSource": {"kind": "resourceQuery", "resourceType": "prototype.items", "query": {"id": "$state.selected_item"}}}],
    }}}
    assert validate_webui_capabilities(webui)["ok"] is True
    page["initialState"] = {}
    result = validate_webui_capabilities(webui)
    assert result["ok"] is False
    assert any(item["code"] == "ui.resource_query.state_uninitialized" for item in result["findings"])


def test_static_details_selected_key_must_resolve_directly() -> None:
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"] = {"selectedItemId": "item-1"}
    page["widgets"] = [{
        "id": "details",
        "type": "item.details",
        "area": "main",
        "dataSource": {
            "kind": "static",
            "value": {"records": {"item-1": {"id": "item-1", "title": "One"}}},
        },
        "inputs": {
            "selectedStateKey": "selectedItemId",
            "fields": [{"id": "title", "label": "Title", "value": "{title}"}],
        },
    }]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert any(
        item["code"] == "ui.details.static_selection_unresolvable"
        for item in result["findings"]
    )
    page["widgets"][0]["dataSource"]["value"] = {
        "item-1": {"id": "item-1", "title": "One"}
    }
    assert validate_webui_capabilities(webui)["ok"] is True


def test_static_details_cover_ids_emitted_by_static_collection_selection() -> None:
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"] = {"selectedItemId": ""}
    page["widgets"] = [
        {
            "id": "items",
            "type": "ui.list",
            "area": "main",
            "dataSource": {
                "kind": "static",
                "value": [
                    {"id": "item-1", "title": "One"},
                    {"id": "item-2", "title": "Two"},
                ],
            },
            "inputs": {"titleKey": "title"},
            "actions": [
                {
                    "on": "select",
                    "type": "updateState",
                    "params": {"selectedItemId": "$event.id"},
                }
            ],
        },
        {
            "id": "details",
            "type": "item.details",
            "area": "main",
            "dataSource": {
                "kind": "static",
                "value": {"item-1": {"title": "One"}},
            },
            "inputs": {
                "selectedStateKey": "selectedItemId",
                "fields": [{"id": "title", "label": "Title", "value": "{title}"}],
            },
        },
    ]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    finding = next(
        item
        for item in result["findings"]
        if item["code"] == "ui.details.static_selection_keys_missing"
    )
    assert finding["missing_keys"] == ["item-2"]
    page["widgets"][1]["dataSource"]["value"]["item-2"] = {"title": "Two"}
    assert validate_webui_capabilities(webui)["ok"] is True


def test_details_title_belongs_on_widget_and_action_params_reject_js_expression_strings() -> None:
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"] = {"selectedItemId": "item-1", "selectedTitle": "One"}
    page["widgets"] = [{
        "id": "details",
        "type": "item.details",
        "area": "main",
        "dataSource": {
            "kind": "static",
            "value": {"item-1": {"id": "item-1", "ready": True}},
        },
        "inputs": {
            "title": "$state.selectedTitle",
            "selectedStateKey": "selectedItemId",
            "fields": [{"id": "ready", "label": "Ready", "value": "{ready}"}],
        },
        "actions": [{
            "on": "click:select",
            "type": "updateState",
            "params": {"selectedReady": "$event.status === 'ready'"},
            "label": "Select",
        }],
    }]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert {item["code"] for item in result["findings"]} >= {
        "ui.details.title_input_misplaced",
        "ui.action.expression_string_unsupported",
    }
    widget = page["widgets"][0]
    widget["title"] = widget["inputs"].pop("title")
    widget["actions"][0]["params"]["selectedReady"] = "$event.ready"
    assert validate_webui_capabilities(webui)["ok"] is True


def test_dynamic_details_title_cannot_be_overridden_by_static_i18n() -> None:
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"] = {"selectedId": "row-1", "selectedTitle": "First"}
    page["widgets"] = [{
        "id": "details",
        "type": "item.details",
        "area": "main",
        "title": "$state.selectedTitle",
        "title_i18n": "example.details.title",
        "inputs": {"selectedStateKey": "selectedId"},
        "dataSource": {
            "kind": "static",
            "value": {"row-1": {"id": "row-1", "title": "First"}},
        },
    }]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert any(
        item["code"] == "ui.details.dynamic_title_i18n_conflict"
        for item in result["findings"]
    )
    page["widgets"][0].pop("title_i18n")
    assert validate_webui_capabilities(webui)["ok"] is True


def test_binary_action_expression_requires_args_not_left_and_right() -> None:
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["widgets"] = [{
        "id": "records",
        "type": "ui.list",
        "area": "main",
        "inputs": {"variant": "list"},
        "dataSource": {
            "kind": "static",
            "value": [{"id": "row-1", "hasUpdate": False}],
        },
        "actions": [{
            "on": "select",
            "type": "updateState",
            "params": {
                "selectedHasUpdate": {
                    "kind": "expression",
                    "op": "equals",
                    "left": "$event.hasUpdate",
                    "right": True,
                }
            },
        }],
    }]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert any(
        item["code"] == "ui.action.expression_shape_invalid"
        for item in result["findings"]
    )
    page["widgets"][0]["actions"][0]["params"]["selectedHasUpdate"] = {
        "kind": "expression",
        "op": "equals",
        "args": ["$event.hasUpdate", True],
    }
    assert validate_webui_capabilities(webui)["ok"] is True


def test_static_list_filter_options_must_match_record_values() -> None:
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"] = {"installedFilter": "all"}
    page["widgets"] = [
        {
            "id": "query",
            "type": "ui.queryToolbar",
            "area": "main",
            "inputs": {"controls": [{
                "id": "installed",
                "label": "Installation",
                "kind": "filter",
                "inputType": "select",
                "stateKey": "installedFilter",
                "options": [
                    {"value": "all", "label": "All"},
                    {"value": "installed", "label": "Installed"},
                    {"value": "available", "label": "Available"},
                ],
            }]},
        },
        {
            "id": "items",
            "type": "ui.list",
            "area": "main",
            "dataSource": {
                "kind": "static",
                "value": [
                    {"id": "one", "installed": True},
                    {"id": "two", "installed": False},
                ],
            },
            "inputs": {"filters": [{
                "key": "installed",
                "stateKey": "installedFilter",
                "operator": "equals",
            }]},
        },
    ]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert any(
        item["code"] == "ui.list.filter_options_nonmatching"
        for item in result["findings"]
    )
    page["widgets"][0]["inputs"]["controls"][0]["options"][1]["value"] = True
    page["widgets"][0]["inputs"]["controls"][0]["options"][2]["value"] = False
    assert validate_webui_capabilities(webui)["ok"] is True


def test_generic_catalog_contains_no_subject_recipe() -> None:
    catalog = ui_capability_catalog()

    assert "recipe.application_manager" not in {
        item["id"] for item in catalog["recipes"]
    }
    with pytest.raises(KeyError):
        get_ui_capability("recipe.application_manager")


def test_document_layout_contract_exposes_semantic_shape() -> None:
    catalog = ui_capability_catalog()
    document = next(
        item for item in catalog["layouts"] if item["id"] == "layout.document"
    )

    assert document["manifest"] == {
        "pattern": "document",
        "primary_role": "main",
    }
    assert document["responsive"] == {
        "wide": "ordered pane",
        "compact": "ordered stack",
    }


def test_form_contract_distinguishes_field_layout_from_page_layout() -> None:
    catalog = ui_capability_catalog()
    form = next(item for item in catalog["components"] if item["id"] == "ui.form")

    assert "optional string enum" in form["manifest"]["layout_property"]
    assert "not a page layout object" in form["manifest"]["layout_property"]


def test_resource_board_contract_exposes_exact_query_and_button_shapes() -> None:
    catalog = ui_capability_catalog()
    board = next(
        item for item in catalog["components"] if item["id"] == "collection.board"
    )

    assert board["manifest"]["resource_query_shape"] == {
        "kind": "resourceQuery",
        "resourceType": "prototype.<resource_name>",
        "query": {},
    }
    assert board["manifest"]["button_shape"] == {
        "required": ["id"],
        "kind": ["primary", "secondary", "danger"],
        "fill": ["solid", "outline", "clear"],
    }
    assert board["manifest"]["localizable_inputs"] == {
        "loadingText": "loadingText_i18n",
        "emptyText": "emptyText_i18n",
        "addItemLabel": "addItemLabel_i18n",
        "moveItemLabel": "moveItemLabel_i18n",
    }


def test_generic_request_cannot_select_subject_pack_by_wording_or_id() -> None:
    for request in (
        "Create an Applications marketplace with stable and prerelease versions",
        "Use recipe.application_manager",
    ):
        qualification = qualify_ui_request(request)
        selection = selected_ui_capabilities(request)

        assert qualification["surface_kind"] == "unspecified"
        assert qualification["input_attribution"] == {
            "profile": "generic",
            "domain_packs": [],
        }
        assert "recipe.application_manager" not in selection["root_item_ids"]
        assert selection["input_attribution"]["domain_packs"] == []


def test_explicit_compatibility_pack_preserves_legacy_qualification() -> None:
    request = (
        "Create the Applications marketplace with installed and prerelease versions"
    )
    qualification = qualify_ui_request(request, domain_packs=APPLICATION_PACKS)
    selection = selected_ui_capabilities(request, domain_packs=APPLICATION_PACKS)

    assert qualification["surface_kind"] == "application_manager"
    assert selection["root_item_ids"] == ["recipe.application_manager"]
    assert (
        selection["input_attribution"]["domain_packs"][0]["pack_id"]
        == APPLICATION_PACKS[0]
    )
    assert selection["domain_policy"]["locale_dictionaries_required"] is True
    assert selection["repair_guidance"]["general"]
    assert (
        get_ui_capability("recipe.application_manager", domain_packs=APPLICATION_PACKS)[
            "id"
        ]
        == "recipe.application_manager"
    )


def test_generic_evaluation_records_clean_attribution() -> None:
    result = evaluate_ui_request("Create a simple page", _empty_webui())

    assert result["input_attribution"] == {"profile": "generic", "domain_packs": []}


def test_generic_mutation_selects_resource_collection_contract() -> None:
    request = "Build a list where staff create and update service requests."

    qualification = qualify_ui_request(request)
    selection = selected_ui_capabilities(request)

    assert qualification["requirements"]["prototype_resource"] is True
    assert selection["root_item_ids"][0] == "recipe.resource_collection_workbench"
    assert (
        "resource.persistence_operations"
        in selection["repair_guidance"]["by_postcondition"]
    )
    assert "collection.board" not in {
        item["id"] for item in selection["items"] if isinstance(item, dict)
    }


def test_generic_rich_read_surface_selects_composition_capabilities() -> None:
    selection = selected_ui_capabilities(
        "Provide global search, navigation between sections, lists of applications "
        "and actions that open selected details."
    )

    assert {
        "input.text",
        "input.selector",
        "ui.queryToolbar",
        "ui.list",
        "item.details",
        "navigation.tabs",
        "ui.actions",
    } <= set(selection["root_item_ids"])


def test_list_capability_exposes_renderer_projection_and_command_contract() -> None:
    item = get_ui_capability("ui.list")
    manifest = item["manifest"]

    assert {"previewKey", "imageKey", "badgeKey", "meta", "buttons"} <= set(
        manifest["optional_inputs"]
    )
    assert "primaryAction" in manifest["button_shape"]
    assert "headerActions" in manifest["button_shape"]
    assert "click:<buttonId>" in manifest["button_shape"]
    assert "bare page-state key" in manifest["query_binding"]


def test_query_toolbar_capability_exposes_exact_control_and_binding_contract() -> None:
    manifest = get_ui_capability("ui.queryToolbar")["manifest"]

    assert "stateKey is a bare page-state identifier" in manifest["control_shape"]
    assert "do not accept placeholder" in manifest["control_shape"]
    assert "inputs.filters" in manifest["collection_binding"]


def test_explicit_collection_detail_layout_is_selected_before_components() -> None:
    selection = selected_ui_capabilities(
        "Add a collection-detail section with search, filters, a list, details, "
        "navigation and commands."
    )

    assert "layout.collection-detail" in selection["root_item_ids"]


def test_generic_validation_requires_list_buttons_to_bind_executable_actions() -> None:
    webui = _empty_webui()
    widget = {
        "id": "applications",
        "type": "ui.list",
        "area": "main",
        "inputs": {
            "variant": "cards",
            "buttons": [{"id": "open", "label": "Open"}],
        },
        "dataSource": {
            "kind": "static",
            "value": [{"id": "builder", "title": "Builder"}],
        },
    }
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"] = [widget]

    missing = validate_webui_capabilities(webui)
    widget["actions"] = [
        {
            "id": "open-application",
            "on": "click:open",
            "type": "updateState",
            "params": {"opened": "$event.id"},
        }
    ]
    present = validate_webui_capabilities(webui)

    assert "ui.list.button_action_missing" in {
        item["code"] for item in missing["findings"]
    }
    assert present["ok"] is True


def test_generic_validation_requires_list_header_actions_to_bind_executable_actions() -> None:
    webui = _empty_webui()
    widget = {
        "id": "pinned-applications",
        "type": "ui.list",
        "area": "main",
        "inputs": {
            "variant": "cards",
            "headerActions": [
                {"id": "customize", "label": "Customize layout", "icon": "settings-outline"}
            ],
        },
        "actions": [],
        "dataSource": {
            "kind": "static",
            "value": [{"id": "builder", "title": "Builder"}],
        },
    }
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"] = [widget]

    missing = validate_webui_capabilities(webui)
    widget["actions"] = [
        {
            "id": "customize-layout",
            "on": "click:customize",
            "type": "updateState",
            "params": {"layoutCustomizing": True},
        }
    ]
    present = validate_webui_capabilities(webui)

    assert any(
        item["code"] == "ui.list.button_action_missing"
        and ".inputs.headerActions[0]" in item["path"]
        for item in missing["findings"]
    )
    assert present["ok"] is True


def test_generic_validation_rejects_product_extension_as_content_container() -> None:
    webui = _empty_webui()
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"] = [
        {
            "id": "summary",
            "type": "desktop.widgets",
            "area": "main",
            "inputs": {"kind": "band", "visibleIf": "$state.section === 'home'"},
        }
    ]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert {item["code"] for item in result["findings"]} >= {
        "ui.component.structural_input_misplaced",
        "ui.desktop_widgets.source_invalid",
    }


def test_generic_validation_requires_renderer_consumable_collection_source() -> None:
    webui = _empty_webui()
    widget = {
        "id": "items",
        "type": "ui.list",
        "area": "main",
        "inputs": {"variant": "cards", "rows": [{"id": "one", "title": "One"}]},
    }
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"] = [widget]

    result = validate_webui_capabilities(webui)

    assert result["ok"] is False
    assert {item["code"] for item in result["findings"]} >= {
        "ui.component.source_missing",
        "ui.list.inline_source_unsupported",
    }

    widget["inputs"].pop("rows")
    widget["dataSource"] = {
        "kind": "static",
        "value": [{"id": "one", "title": "One"}],
    }
    assert validate_webui_capabilities(webui)["ok"] is True


def test_generic_mutation_requires_executable_prototype_resource() -> None:
    request = "Build a list where staff create and update service requests."
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"] = {"selectedRecordId": ""}
    page["widgets"] = [
        {
            "id": "requests",
            "type": "ui.list",
            "area": "main",
            "dataSource": {
                "kind": "resourceQuery",
                "resourceType": "prototype.service_requests",
                "query": {},
            },
            "inputs": {"itemIdKey": "id", "titleKey": "title"},
            "actions": [
                {
                    "on": "select",
                    "type": "updateState",
                    "params": {"selectedRecordId": "$event.id"},
                }
            ],
        },
        {
            "id": "create-request",
            "type": "ui.form",
            "area": "main",
            "inputs": {"fields": [{"id": "title", "type": "text"}]},
            "actions": [
                {
                    "on": "submit",
                    "type": "resourceOperation",
                    "target": "prototype.service_requests",
                    "params": {
                        "operation_id": "create",
                        "payload": "$event.values",
                    },
                }
            ],
        },
        {
            "id": "update-request",
            "type": "ui.form",
            "area": "main",
            "inputs": {"fields": [{"id": "title", "type": "text"}]},
            "actions": [
                {
                    "on": "submit",
                    "type": "resourceOperation",
                    "target": "prototype.service_requests",
                    "params": {
                        "operation_id": "update",
                        "record_id": "$state.selectedRecordId",
                        "payload": "$event.values",
                    },
                }
            ],
        },
    ]
    records = [{"id": "request-1", "title": "Inspect pump"}]

    accepted = evaluate_ui_request(request, webui, prototype_records=records)
    assert accepted["ok"] is True

    enveloped = evaluate_ui_request(
        request,
        webui,
        prototype_records=[
            {"resourceType": "prototype.service_requests", "records": records}
        ],
    )
    prototype_records_check = next(
        item
        for item in enveloped["postconditions"]
        if item["id"] == "resource.prototype_records"
    )
    assert prototype_records_check["ok"] is False
    assert prototype_records_check["actual"]["direct_records"] is False

    page["widgets"].append(
        {
            "id": "people",
            "type": "ui.table",
            "area": "main",
            "dataSource": {
                "kind": "resourceQuery",
                "resourceType": "prototype.people",
                "query": {},
            },
            "inputs": {"columns": [{"key": "name", "label": "Name"}]},
        }
    )
    multi_resource = evaluate_ui_request(
        request,
        webui,
        prototype_resources=[
            {
                "resource_type": "prototype.service_requests",
                "records": records,
            },
            {
                "resource_type": "prototype.people",
                "records": [{"id": "person-1", "name": "Alex"}],
            },
        ],
    )
    assert multi_resource["ok"] is True
    page["widgets"].pop()

    page["widgets"][0]["dataSource"] = {"kind": "static", "value": records}
    page["widgets"][1]["actions"] = [
        {"on": "submit", "type": "updateState", "params": {"draft": "$event.values"}}
    ]
    rejected = evaluate_ui_request(request, webui, prototype_records=records)
    failed = {item["id"] for item in rejected["postconditions"] if not item["ok"]}
    assert {"resource.prototype_source", "resource.persistence_operations"} <= failed


def test_generic_attachment_capture_requires_editable_form_field() -> None:
    request = "A technician uploads a defect photo."
    webui = _empty_webui()
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["widgets"] = [
        {
            "id": "inspection",
            "type": "ui.form",
            "area": "main",
            "inputs": {
                "fields": [
                    {
                        "id": "photo-note",
                        "type": "staticContent",
                        "markdown": "Attach later",
                    }
                ]
            },
        }
    ]

    missing = evaluate_ui_request(request, webui)
    check = next(
        item
        for item in missing["postconditions"]
        if item["id"] == "ui.information_capture"
    )
    assert check == {
        "id": "ui.information_capture",
        "ok": False,
        "expected": ["attachment"],
        "actual": [],
    }

    page["widgets"][0]["inputs"]["fields"].append(
        {"id": "defect-photo", "type": "fileUpload"}
    )
    present = evaluate_ui_request(request, webui)
    check = next(
        item
        for item in present["postconditions"]
        if item["id"] == "ui.information_capture"
    )
    assert check["ok"] is True


def test_resource_move_diagnostic_exposes_the_inconsistent_source_and_action() -> None:
    webui = _empty_webui()
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"] = [
        {
            "id": "work",
            "type": "collection.board",
            "area": "main",
            "inputs": {
                "lanes": [{"id": "new", "label": "New"}],
                "laneKey": "status",
                "titleKey": "title",
                "dragDrop": True,
            },
            "dataSource": {
                "kind": "static",
                "value": [{"id": "one", "title": "One", "status": "new"}],
            },
            "actions": [
                {
                    "on": "move",
                    "type": "resourceOperation",
                    "target": "prototype.work",
                    "params": {
                        "operation_id": "update",
                        "record_id": "$event.id",
                        "payload": "$event.patch",
                    },
                }
            ],
        }
    ]

    result = validate_webui_capabilities(webui)

    finding = next(
        item
        for item in result["findings"]
        if item["code"] == "ui.board.resource_move_invalid"
    )
    assert finding["actual"]["dataSource.kind"] == "static"
    assert finding["actual"]["dataSource.resourceType"] == ""
    assert finding["actual"]["action.target"] == "prototype.work"
    assert finding["expected"]["dataSource.kind"] == "resourceQuery"


def test_generic_core_sources_have_no_subject_vocabulary() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "src" / "adaos" / "services" / "ui_capabilities.py",
        root / "src" / "adaos" / "abi" / "ui.capability_catalog.v1.json",
        root / "src" / "adaos" / "services" / "builder" / "prompt_rule_capsules.json",
    )
    forbidden = (
        "recipe.application_manager",
        "applications.list",
        "applications.show",
        "applications.plan",
        "applications.apply",
        "shopping_list",
        "recipe_book",
        "adaos.application.lifecycle",
        "adaos.research.",
    )

    for path in paths:
        content = path.read_text(encoding="utf-8").casefold()
        assert not [term for term in forbidden if term.casefold() in content], path


def test_domain_pack_is_versioned_and_content_addressed() -> None:
    pack = load_domain_pack(APPLICATION_PACKS[0])

    assert pack["schema"] == "adaos.builder.domain_pack.v1"
    assert pack["classification"] == "compatibility"
    assert pack["digest"].startswith("sha256:")
    assert json.dumps(pack["ui_recipes"], ensure_ascii=False)
    assert domain_pack_ids_for_recipes(["recipe.application_manager"]) == [
        APPLICATION_PACKS[0]
    ]
    assert domain_pack_ids_for_recipes(["recipe.unknown"]) == []


def test_dashboard_and_local_view_state_do_not_select_kanban_or_resource_crud() -> None:
    request = (
        "Refine the dashboard. Do not add another top bar. "
        "A list button may update bounded local prototype state; do not represent "
        "it as an unused record field. Show the complete Home hierarchy."
    )

    qualification = qualify_ui_request(request)
    selected = selected_ui_capabilities(request)

    assert qualification["surface_kind"] != "board"
    assert qualification["requirements"]["prototype_resource"] is False
    assert "kanban_board" not in qualification["concepts"]
    assert "recipe.kanban_board" not in {
        item["id"] for item in selected["items"]
    }
    assert "recipe.master_detail" not in selected["root_item_ids"]


def test_dashboard_authoring_refinement_does_not_require_resource_crud() -> None:
    request = (
        "Polish Web Desktop prototype revision 005. Change the dashboard toolbar "
        "region semantic role and compact card sizing. Give every application "
        "record separate purpose and lastActivity fields. Add an actionLabel field "
        "to each attention record and set the repeated button itemLabelKey. "
        "Render Space health using the item.details section presentation."
    )

    qualification = qualify_ui_request(request)
    selection = selected_ui_capabilities(request)

    assert qualification["requirements"]["prototype_resource"] is False
    assert "recipe.resource_collection_workbench" not in selection["root_item_ids"]
    assert "recipe.data_entry" not in selection["root_item_ids"]


def test_explicit_local_prototype_scope_overrides_crud_words() -> None:
    request = (
        "Extend the prototype with an Applications catalog. This remains a "
        "prototype with synthetic records and bounded local state only. "
        "Selecting a card updates selectedAppId. Add Install and Update commands "
        "that update bounded local prototype state without runtime APIs."
    )

    qualification = qualify_ui_request(request)
    selection = selected_ui_capabilities(request)

    assert qualification["requirements"]["prototype_resource"] is False
    assert "recipe.resource_collection_workbench" not in selection["root_item_ids"]


def test_hyphenated_local_state_scope_overrides_ui_authoring_crud_words() -> None:
    request = (
        "Correct only the Applications section. This is a bounded local-state "
        "prototype with synthetic data. Remove one duplicate detail field, "
        "change query option values, and update the select action."
    )

    qualification = qualify_ui_request(request)
    selection = selected_ui_capabilities(request)

    assert qualification["requirements"]["prototype_resource"] is False
    assert "recipe.resource_collection_workbench" not in selection["root_item_ids"]
