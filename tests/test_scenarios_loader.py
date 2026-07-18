from __future__ import annotations

import json

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
