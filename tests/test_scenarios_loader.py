from __future__ import annotations

import json

from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.scenarios.loader import _resolve_ui_manifest


def test_resolve_ui_manifest_materializes_application_and_data_defaults(tmp_path) -> None:
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {"application": {"desktop": {"pageSchema": {"widgets": [{"id": "recipes"}]}}}},
        "ydoc_defaults": {"data/recipes/items": [{"id": "one"}]},
    }
    (tmp_path / "webui.json").write_text(json.dumps(webui), encoding="utf-8")

    resolved = _resolve_ui_manifest({"id": "recipes", "ui": {"manifest": "webui.json"}}, scenario_root=tmp_path)

    assert resolved["ui"] == webui["ui"]
    assert resolved["data"]["recipes"]["items"] == [{"id": "one"}]


def test_resolve_ui_manifest_rejects_parent_path(tmp_path) -> None:
    outside = tmp_path.parent / "outside-webui.json"
    outside.write_text(json.dumps({"ui": {"application": {"desktop": {}}}}), encoding="utf-8")

    content = {"id": "recipes", "ui": {"manifest": "../outside-webui.json"}}

    assert _resolve_ui_manifest(content, scenario_root=tmp_path) == content


def test_read_content_uses_yaml_manifest_without_legacy_scenario_json(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr(scenarios_loader, "_candidate_roots", lambda *_args, **_kwargs: (tmp_path,))
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
    webui_path.write_text(json.dumps({"ui": {"application": {"desktop": {}}}}), encoding="utf-8")
    monkeypatch.setattr(scenarios_loader, "_candidate_roots", lambda *_args, **_kwargs: (tmp_path,))

    first = scenarios_loader.scenario_source_fingerprint("workflow_lab", space="workspace")
    webui_path.write_text(
        json.dumps({"ui": {"application": {"desktop": {"pageSchema": {"id": "changed"}}}}}),
        encoding="utf-8",
    )
    second = scenarios_loader.scenario_source_fingerprint("workflow_lab", space="workspace")

    assert first
    assert second
    assert first != second
