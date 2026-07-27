from __future__ import annotations

import json
from pathlib import Path

import yaml

from adaos.services.root.service import _sync_scenario_content_metadata


def test_scenario_json_is_derived_from_yaml_and_webui(tmp_path: Path) -> None:
    root = tmp_path / "recipes"
    root.mkdir()
    (root / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "name": "recipes",
                "version": "1.2.0",
                "title": "Recipes",
                "depends": ["shopping_skill"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {"version": "2"}}}),
        encoding="utf-8",
    )
    (root / "scenario.json").write_text(
        json.dumps(
            {
                "id": "old",
                "version": "0.1.0",
                "title": "Stale",
                "ui": {"application": {"version": "1"}},
                "nlu": {"intents": ["recipes.open"]},
            }
        ),
        encoding="utf-8",
    )

    _sync_scenario_content_metadata(
        root,
        "recipes",
        {"version": "1.2.1", "updated_at": "2026-07-24T12:00:00+00:00"},
    )

    materialized = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
    assert materialized["id"] == materialized["name"] == "recipes"
    assert materialized["version"] == "1.2.1"
    assert materialized["title"] == "Recipes"
    assert materialized["depends"] == ["shopping_skill"]
    assert materialized["ui"] == {"application": {"version": "2"}}
    assert materialized["nlu"] == {"intents": ["recipes.open"]}
