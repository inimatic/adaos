from __future__ import annotations

import json
from types import SimpleNamespace

from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.scenarios.loader import _resolve_ui_manifest


def test_scenario_root_resolves_manifest_id_through_workspace_registry(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    scenarios = workspace / "scenarios"
    installed = scenarios / "new_face_vision"
    installed.mkdir(parents=True)
    (installed / "scenario.yaml").write_text(
        "id: new_face_vision_scenario\nversion: 0.2.0\n",
        encoding="utf-8",
    )
    (workspace / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [],
                "projects": [],
                "scenarios": [
                    {
                        "kind": "scenario",
                        "id": "new_face_vision_scenario",
                        "name": "new_face_vision",
                        "path": "scenarios/new_face_vision",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    paths = SimpleNamespace(
        scenarios_dir=lambda: scenarios,
        dev_scenarios_dir=lambda: tmp_path / "dev" / "scenarios",
    )
    monkeypatch.setattr(
        scenarios_loader, "get_ctx", lambda: SimpleNamespace(paths=paths)
    )

    assert (
        scenarios_loader.scenario_root_for_space(
            "new_face_vision_scenario", "workspace"
        )
        == installed.resolve()
    )
    assert scenarios_loader.scenario_exists(
        "new_face_vision_scenario", space="workspace"
    )


def test_resolve_ui_manifest_materializes_application_and_data_defaults(
    tmp_path,
) -> None:
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {"desktop": {"pageSchema": {"widgets": [{"id": "recipes"}]}}}
        },
        "ydoc_defaults": {"data/recipes/items": [{"id": "one"}]},
    }
    (tmp_path / "webui.json").write_text(json.dumps(webui), encoding="utf-8")

    resolved = _resolve_ui_manifest(
        {"id": "recipes", "ui": {"manifest": "webui.json"}}, scenario_root=tmp_path
    )

    assert resolved["ui"] == webui["ui"]
    assert resolved["data"]["recipes"]["items"] == [{"id": "one"}]


def test_resolve_ui_manifest_rejects_parent_path(tmp_path) -> None:
    outside = tmp_path.parent / "outside-webui.json"
    outside.write_text(
        json.dumps({"ui": {"application": {"desktop": {}}}}), encoding="utf-8"
    )

    content = {"id": "recipes", "ui": {"manifest": "../outside-webui.json"}}

    assert _resolve_ui_manifest(content, scenario_root=tmp_path) == content


def test_read_content_uses_yaml_manifest_without_legacy_scenario_json(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "scenario.yaml").write_text(
        "id: workflow_lab\nversion: 0.1.0\nui:\n  manifest: webui.json\n",
        encoding="utf-8",
    )
    (tmp_path / "webui.json").write_text(
        json.dumps(
            {
                "schema": "adaos.webui.v1",
                "ui": {
                    "application": {
                        "desktop": {"pageSchema": {"id": "workflow-lab", "widgets": []}}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scenarios_loader, "_candidate_roots", lambda *_args, **_kwargs: (tmp_path,)
    )
    scenarios_loader.invalidate_cache(scenario_id="workflow_lab", space="workspace")

    content = scenarios_loader.read_content("workflow_lab", space="workspace")

    assert content["id"] == "workflow_lab"
    assert content["ui"]["application"]["desktop"]["pageSchema"]["id"] == "workflow-lab"


def test_yaml_manifest_fingerprint_tracks_adjacent_webui(monkeypatch, tmp_path) -> None:
    (tmp_path / "scenario.yaml").write_text(
        "id: workflow_lab\nversion: 0.1.0\nui:\n  manifest: webui.json\n",
        encoding="utf-8",
    )
    webui_path = tmp_path / "webui.json"
    webui_path.write_text(
        json.dumps({"ui": {"application": {"desktop": {}}}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        scenarios_loader, "_candidate_roots", lambda *_args, **_kwargs: (tmp_path,)
    )

    first = scenarios_loader.scenario_source_fingerprint(
        "workflow_lab", space="workspace"
    )
    webui_path.write_text(
        json.dumps(
            {"ui": {"application": {"desktop": {"pageSchema": {"id": "changed"}}}}}
        ),
        encoding="utf-8",
    )
    second = scenarios_loader.scenario_source_fingerprint(
        "workflow_lab", space="workspace"
    )

    assert first
    assert second
    assert first != second
