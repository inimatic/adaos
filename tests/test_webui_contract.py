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
    validate_skill_tool_references,
    validate_webui_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


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
