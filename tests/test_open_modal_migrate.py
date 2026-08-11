from __future__ import annotations

import json
from pathlib import Path

from adaos.apps.open_modal_migrate import migrate_paths


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_migrates_skill_modal_contract_and_actions_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "demo_skill" / "webui.json"
    _write(
        path,
        {
            "apps": [
                {
                    "id": "demo",
                    "launchModal": "demo_modal",
                    "action": {"openModal": "demo_modal"},
                }
            ],
            "registry": {
                "modals": {
                    "demo_modal": {
                        "title": "Demo",
                        "schema": {
                            "widgets": [
                                {
                                    "id": "open",
                                    "type": "ui.actions",
                                    "actions": [
                                        {
                                            "on": "click",
                                            "type": "openModal",
                                            "params": {"modalId": "demo_modal"},
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                }
            },
        },
    )

    first = migrate_paths([path], write=True)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    second = migrate_paths([path], write=True)

    assert first["actions"] == 2
    assert first["remaining_open_modal_total"] == 0
    assert second["actions"] == 0
    assert migrated["interface"]["defaultView"] == "demo_skill.demo_modal"
    modal = migrated["registry"]["modals"]["demo_modal"]
    assert modal["implements"] == ["demo_skill.demo_modal"]
    assert modal["schema"]["interface"]["routes"]["demo"]["view"] == "demo_skill.demo_modal"
    assert migrated["apps"][0]["action"] == {"navigate": "demo_skill.demo_modal"}
    action = modal["schema"]["widgets"][0]["actions"][0]
    assert action["type"] == "navigate"
    assert action["params"] == {
        "to": "demo_skill.demo_modal",
        "surface": "modal",
        "modalId": "demo_modal",
    }


def test_migrates_dynamic_desktop_catalog_action(tmp_path: Path) -> None:
    path = tmp_path / "scenarios" / "web_desktop" / "scenario.json"
    _write(
        path,
        {
            "id": "web_desktop",
            "ui": {
                "application": {
                    "desktop": {
                        "pageSchema": {
                            "widgets": [
                                {
                                    "id": "desktop-icons",
                                    "actions": [
                                        {
                                            "on": "select",
                                            "type": "openModal",
                                            "params": {"modalId": "$event.action.openModal"},
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                }
            },
        },
    )

    result = migrate_paths([path], write=True)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    action = migrated["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["actions"][0]

    assert result["actions"] == 1
    assert action == {
        "on": "select",
        "type": "navigate",
        "params": {
            "to": "$event.action.navigate",
            "surface": "modal",
            "modalId": "$event.launchModal",
        },
    }


def test_declares_view_and_navigation_for_launch_modal_catalog_entry(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "launcher_skill" / "webui.json"
    _write(
        path,
        {
            "apps": [
                {
                    "id": "launcher",
                    "launchModal": "launcher_modal",
                }
            ],
            "registry": {
                "modals": {
                    "launcher_modal": {"title": "Launcher", "schema": {"widgets": []}},
                }
            },
        },
    )

    result = migrate_paths([path], write=True)
    migrated = json.loads(path.read_text(encoding="utf-8"))

    assert result["actions"] == 1
    assert migrated["apps"][0]["action"] == {"navigate": "launcher_skill.launcher_modal"}
    assert migrated["interface"]["views"]["launcher_skill.launcher_modal"]["surfaces"] == ["modal"]


def test_recognizes_bundled_skill_webui_outside_registry_layout(tmp_path: Path) -> None:
    path = tmp_path / "bundle" / "weather_skill.webui.json"
    _write(
        path,
        {
            "$schema": "webui.v1.schema.json",
            "apps": [{"id": "weather", "launchModal": "weather_modal"}],
            "registry": {
                "modals": {
                    "weather_modal": {"title": "Weather", "schema": {"widgets": []}},
                }
            },
        },
    )

    migrate_paths([path], write=True)
    migrated = json.loads(path.read_text(encoding="utf-8"))

    assert migrated["apps"][0]["action"] == {"navigate": "weather_skill.weather_modal"}


def test_prefers_document_local_view_when_modal_ids_are_ambiguous(tmp_path: Path) -> None:
    paths = []
    for owner in ("alpha_skill", "beta_skill"):
        path = tmp_path / "skills" / owner / "webui.json"
        _write(
            path,
            {
                "widgets": [
                    {
                        "id": "open",
                        "actions": [
                            {
                                "on": "click",
                                "type": "openModal",
                                "params": {"modalId": "shared_modal"},
                            }
                        ],
                    }
                ],
                "registry": {
                    "modals": {
                        "shared_modal": {"title": owner, "schema": {"widgets": []}},
                    }
                },
            },
        )
        paths.append(path)

    result = migrate_paths(paths, write=True)

    assert result["ambiguous_modal_ids"] == ["shared_modal"]
    assert result["remaining_open_modal_total"] == 0
    for owner, path in zip(("alpha_skill", "beta_skill"), paths, strict=True):
        migrated = json.loads(path.read_text(encoding="utf-8"))
        action = migrated["widgets"][0]["actions"][0]
        assert action["type"] == "navigate"
        assert action["params"]["to"] == f"{owner}.shared_modal"
