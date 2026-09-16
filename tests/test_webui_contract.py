from __future__ import annotations

from pathlib import Path

from adaos.sdk.web import (
    diagnostic_catalog,
    modal_domain_contract,
    modal_domain_state,
    modal_interface,
    modal_ownership_contract,
    modal_route,
    navigate_modal_action,
    param_schema,
    skill_interface,
    skill_view,
    validate_webui,
)
from adaos.services.webui_contract import (
    validate_form_action_bindings,
    validate_production_attachment_fields,
    validate_skill_tool_references,
    validate_webui_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_named_form_commands_reject_orphan_submit_steps_in_nested_modals():
    form = {"type": "ui.form", "inputs": {"buttons": [{"id": "remove"}]}, "actions": [
        {"id": "remove", "on": "submit", "type": "callSkill", "target": "sample.remove"},
        {"id": "clear_selection", "on": "submit", "type": "updateState", "params": {"selected": ""}},
    ]}
    document = {"ui": {"registry": {"modals": {"editor": {"schema": {"widgets": [form]}}}}}}
    issues = validate_form_action_bindings(document)
    assert [issue.code for issue in issues] == ["webui.form.submit_action_unreachable"]
    assert "actions[1]" in issues[0].where
    assert any(issue.code == issues[0].code for issue in validate_webui_contract(document))
    form["actions"][1]["id"] = "remove"
    assert not validate_form_action_bindings(document)
    form["actions"].append({"on": "change:name", "type": "updateState", "params": {"dirty": True}})
    assert not validate_form_action_bindings(document)
    form["inputs"].pop("buttons")
    form["actions"][1]["id"] = "separate_legacy_submit_step"
    assert not validate_form_action_bindings(document)


def test_same_skill_tool_reference_validation_covers_actions_and_data_sources() -> None:
    issues = validate_skill_tool_references(
        {
            "widgets": [
                {
                    "id": "items",
                    "dataSource": {
                        "kind": "skill",
                        "name": "demo_skill.list_items",
                    },
                    "actions": [
                        {
                            "type": "callSkill",
                            "target": "demo_skill:refresh_items",
                        },
                        {
                            "type": "callSkill",
                            "target": "other_skill.refresh_items",
                        },
                    ],
                }
            ]
        },
        skill_id="demo_skill",
        declared_tools=[],
    )

    assert [issue.code for issue in issues] == [
        "webui.data_source.skill_tool_unknown",
        "webui.action.skill_tool_unknown"
    ]


def test_sdk_helpers_build_valid_addressed_modal_contract() -> None:
    webui = {
        "skill": "demo_skill",
        "interface": skill_interface(
            "demo.notes.list",
            {
                "demo.notes.list": skill_view("Notes", surfaces=["modal"]),
                "demo.note.edit": skill_view(
                    "Edit note",
                    surfaces=["modal"],
                    params={"note_id": param_schema(required=True)},
                ),
            },
        ),
        "registry": {
            "modals": {
                "demo_modal": {
                    "implements": ["demo.notes.list", "demo.note.edit"],
                    "schema": {
                        "id": "demo_modal",
                        "interface": modal_interface(
                            "notes.list",
                            {
                                "notes.list": modal_route(
                                    "demo.notes.list",
                                    state={"mode": "list"},
                                ),
                                "note.edit": modal_route(
                                    "demo.note.edit",
                                    params={"note_id": param_schema(required=True)},
                                    state={"mode": "edit", "selectedId": "$params.note_id"},
                                ),
                            },
                            domain=modal_domain_contract(
                                "notes.list",
                                {
                                    "notes.list": modal_domain_state(
                                        "notes.list",
                                        view="demo.notes.list",
                                        kind="collection",
                                    ),
                                    "note.edit": modal_domain_state(
                                        "note.edit",
                                        view="demo.note.edit",
                                        kind="entity",
                                        entity_type="note",
                                        entity_id_param="note_id",
                                        entity_id_state_key="selectedId",
                                    ),
                                },
                            ),
                            ownership=modal_ownership_contract(
                                "demo_skill",
                                domain_store="skill_memory",
                                projection="webio:demo_skill.notes",
                                route_keys=["mode", "selectedId"],
                                persistence_ack="tool:demo_skill.save_note",
                                durability="skill_local_memory",
                            ),
                        ),
                        "widgets": [
                            {
                                "id": "notes",
                                "type": "ui.list",
                                "actions": [
                                    navigate_modal_action(
                                        "note.edit",
                                        params={"note_id": "$event.id"},
                                    )
                                ],
                            }
                        ],
                    },
                }
            }
        },
    }

    assert validate_webui(webui, skill_id="demo_skill") == []
    assert validate_webui_contract(webui, skill_id="demo_skill") == []


def test_webui_diagnostic_catalog_exposes_modal_domain_codes() -> None:
    catalog = diagnostic_catalog()

    assert catalog["webui.modal.domain.state_route_unknown"]["severity"] == "error"
    assert catalog["webui.modal.ownership_owner_missing"]["owner"] == "skill"


def test_webui_typescript_contract_artifact_covers_modal_domain_and_diagnostics() -> None:
    text = (REPO_ROOT / "src" / "adaos" / "abi" / "webui.v1.types.d.ts").read_text(encoding="utf-8")

    assert "type WebUiFormFieldType" in text
    assert "interface WebUiFormField" in text
    assert "interface WebUiFormInputs" in text
    assert "interface WebUiFormWidgetConfig" in text
    assert "interface WebUiModalDomainContract" in text
    assert "interface WebUiOwnershipContract" in text
    assert "interface WebUiModalHistoryContract" in text
    assert "interface WebUiContractDiagnosticsPayload" in text


def test_validator_rejects_broken_modal_domain_contract() -> None:
    issues = validate_webui(
        {
            "skill": "demo_skill",
            "interface": skill_interface(
                "demo.notes.list",
                {
                    "demo.notes.list": skill_view(surfaces=["modal"]),
                },
            ),
            "registry": {
                "modals": {
                    "demo_modal": {
                        "implements": ["demo.notes.list"],
                        "schema": {
                            "id": "demo_modal",
                            "interface": modal_interface(
                                "notes.list",
                                {
                                    "notes.list": modal_route("demo.notes.list"),
                                },
                                domain=modal_domain_contract(
                                    "missing.state",
                                    {
                                        "notes.list": modal_domain_state(
                                            "missing.route",
                                            view="demo.notes.list",
                                            kind="collection",
                                        )
                                    },
                                ),
                            ),
                            "widgets": [],
                        },
                    }
                }
            },
        },
        skill_id="demo_skill",
    )

    codes = {issue["code"] for issue in issues}
    assert "webui.modal.domain.default_state_unknown" in codes
    assert "webui.modal.domain.state_route_unknown" in codes
    assert "webui.modal.domain.ownership_missing" in codes


def test_production_attachment_field_requires_exact_browser_contract() -> None:
    field = {
        "id": "photo",
        "type": "fileUpload",
        "fileStorage": "skill",
        "uploadTarget": "roster.upload_attachment",
        "readTarget": "roster.read_attachment",
        "maxBytes": 5 * 1024 * 1024,
    }
    webui = {"widgets": [{"id": "editor", "type": "ui.form", "inputs": {"fields": [field]}}]}

    assert validate_production_attachment_fields(webui) == []
    assert validate_webui_contract(webui) == []

    field["readTarget"] = "other.read_attachment"
    issues = validate_production_attachment_fields(webui)
    assert [issue.code for issue in issues] == [
        "webui.form.production_attachment_config_invalid"
    ]
    field["readTarget"] = "roster.upload_attachment"
    assert validate_production_attachment_fields(webui)[0].code == (
        "webui.form.production_attachment_config_invalid"
    )
    field["readTarget"] = "roster.read_attachment"
    field["maxBytes"] = 10485761
    assert validate_production_attachment_fields(webui)[0].code == (
        "webui.form.production_attachment_config_invalid"
    )


def test_production_attachment_tools_must_be_declared_by_owned_skill() -> None:
    webui = {
        "widgets": [
            {
                "id": "editor",
                "type": "ui.form",
                "inputs": {
                    "fields": [
                        {
                            "id": "photo",
                            "type": "fileUpload",
                            "fileStorage": "skill",
                            "uploadTarget": "roster.upload_attachment",
                            "readTarget": "roster.read_attachment",
                            "maxBytes": 1024,
                        }
                    ]
                },
            }
        ]
    }

    issues = validate_skill_tool_references(
        webui,
        skill_id="roster",
        declared_tools=["upload_attachment"],
    )

    assert [issue.code for issue in issues] == [
        "webui.form.production_attachment_tool_unknown"
    ]
    assert "roster.read_attachment" in issues[0].message
