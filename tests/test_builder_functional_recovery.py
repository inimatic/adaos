from __future__ import annotations

import json
from pathlib import Path

from scripts.check_builder_functional_parity import inspect
from scripts.build_builder_functional_recovery import build_recovered_webui
from scripts.restore_builder_functional_baseline import rebind_reference


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (ROOT / "docs" / "architecture" / "builder-functional-parity.json").read_text(
        encoding="utf-8"
    )
)


def test_rebind_reference_isolates_project_and_conversation_identity() -> None:
    webui = {
        "ui": {
            "application": {
                "desktop": {"pageSchema": {"initialState": {}}},
            },
            "user_summary": {},
        }
    }

    rebind_reference(webui, "builder_reference_042", "042")

    state = webui["ui"]["application"]["desktop"]["pageSchema"]["initialState"]
    assert state["selectedProjectId"] == "builder_reference_042"
    assert state["selectedProjectRef"] == "scenario:builder_reference_042"
    assert state["builderTopicId"] == "prompt-project:scenario:builder_reference_042"
    assert state["project"]["title"] == "Builder reference — UI 042"


def test_parity_inspector_accepts_complete_recovered_contract() -> None:
    widget_ids = list(CONTRACT["required_widget_ids"])
    widgets = [{"id": item} for item in widget_ids]
    project_tree = next(item for item in widgets if item["id"] == "project-tree")
    project_tree["inputs"] = {
        "buttons": [{"id": item} for item in CONTRACT["required_lifecycle_buttons"]]
    }
    bindings = list(CONTRACT["required_bindings"]) + list(
        CONTRACT["forward_required_bindings"]
    )
    project_tree["actions"] = [
        (
            {"type": "stream", "kind": "stream", "receiver": item.removeprefix("stream:")}
            if item.startswith("stream:")
            else {"type": "callSkill", "target": item}
        )
        for item in bindings
    ]
    modals = {item: {"schema": {"widgets": []}} for item in CONTRACT["required_modal_ids"]}
    modals["new-project"]["schema"]["widgets"] = [
        {
            "id": "new-project-form",
            "inputs": {
                "fields": [
                    {
                        "id": "object_type",
                        "options": [
                            {"value": item} for item in CONTRACT["required_project_kinds"]
                        ],
                    }
                ]
            },
        }
    ]
    webui = {
        "ui": {
            "application": {
                "desktop": {"pageSchema": {"widgets": widgets}},
                "modals": modals,
            }
        }
    }

    assert not any(inspect(webui, CONTRACT).values())


def test_parity_inspector_reports_schema_valid_control_plane_loss() -> None:
    webui = {
        "ui": {
            "application": {
                "desktop": {"pageSchema": {"widgets": []}},
                "modals": {},
            }
        }
    }

    report = inspect(webui, CONTRACT)

    assert "project-tree" in report["missing_widgets"]
    assert "builder_sdk_control_skill.publish_project" in report["missing_bindings"]
    assert "scenario" in report["missing_project_kinds"]


def test_functional_recovery_forward_ports_only_bounded_project_controls() -> None:
    baseline = {
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "initialState": {},
                        "widgets": [],
                    }
                },
                "modals": {
                    "project-picker": {
                        "schema": {
                            "widgets": [
                                {
                                    "id": "project-picker-list",
                                    "inputs": {},
                                    "actions": [{"on": "add", "type": "openModal"}],
                                }
                            ]
                        }
                    },
                    "new-project": {
                        "schema": {
                            "widgets": [
                                {
                                    "id": "new-project-form",
                                    "inputs": {
                                        "fields": [
                                            {
                                                "id": "object_type",
                                                "options": [
                                                    {"value": "scenario"},
                                                    {"value": "skill"},
                                                ],
                                            }
                                        ]
                                    },
                                    "actions": [],
                                },
                                {
                                    "id": "new-project-templates",
                                    "dataSource": {"kind": "skill", "params": {}},
                                },
                            ]
                        }
                    },
                },
            }
        }
    }
    current = {
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "initialStateSource": {
                            "kind": "y",
                            "path": "data/builder/selection",
                        }
                    }
                }
            }
        }
    }

    recovered = build_recovered_webui(baseline, current)

    page = recovered["ui"]["application"]["desktop"]["pageSchema"]
    picker = recovered["ui"]["application"]["modals"]["project-picker"][
        "schema"
    ]["widgets"][0]
    creation = recovered["ui"]["application"]["modals"]["new-project"][
        "schema"
    ]["widgets"]
    assert page["initialStateSource"]["path"] == "data/builder/selection"
    assert picker["dataSource"]["name"] == "builder_sdk_control_skill.list_projects"
    assert picker["dataSource"]["params"]["include_archived"] == (
        "$state.projectPickerArchived"
    )
    assert picker["inputs"]["addButtonFirst"] is True
    assert creation[0]["inputs"]["fields"][0]["stateKey"] == "newProjectKind"
    assert creation[1]["dataSource"]["params"]["object_type"] == (
        "$state.newProjectKind"
    )
