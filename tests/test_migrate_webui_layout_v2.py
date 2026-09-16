from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_webui_layout_v2.py"
_SPEC = importlib.util.spec_from_file_location("migrate_webui_layout_v2", _SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_migrates_collection_detail_without_product_knowledge() -> None:
    document = {
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "inventory",
                        "layout": {
                            "type": "split",
                            "areas": [
                                {"id": "main", "role": "main"},
                                {"id": "right", "role": "aux", "width": 420},
                            ],
                        },
                        "widgets": [
                            {
                                "id": "records",
                                "type": "ui.table",
                                "area": "main",
                                "actions": [{"on": "select"}],
                            },
                            {"id": "record", "type": "item.details", "area": "right"},
                        ],
                    }
                }
            }
        }
    }

    assert _MODULE.migrate_document(document) == 1
    layout = document["ui"]["application"]["desktop"]["pageSchema"]["layout"]
    assert layout["version"] == 2
    assert layout["pattern"] == "collection-detail"
    assert [region["role"] for region in layout["regions"]] == ["collection", "detail"]
    assert layout["regions"][1]["presentation"] == {"wide": "pane", "compact": "sheet"}
    assert layout["interaction"]["rowActivation"] == "open-detail"


def test_migrates_complete_variants_and_is_idempotent() -> None:
    page = {
        "id": "workbench",
        "layout": {
            "type": "single",
            "areas": [{"id": "main"}],
            "variants": [
                {
                    "id": "detail",
                    "when": "$state.detail === true",
                    "type": "split",
                    "areas": [{"id": "main"}, {"id": "details", "role": "detail"}],
                },
                {
                    "id": "default",
                    "default": True,
                    "type": "single",
                    "areas": [{"id": "main"}],
                },
            ],
        },
        "widgets": [{"id": "content", "type": "static.markdown", "area": "main"}],
    }

    assert _MODULE.migrate_document(page) == 1
    assert all(
        "regions" in variant and "areas" not in variant
        for variant in page["layout"]["variants"]
    )
    assert _MODULE.migrate_document(page) == 0


def test_discovers_embedded_scenario_ui_but_not_llm_working_state(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text("{}", encoding="utf-8")
    webui = tmp_path / "skill.webui.json"
    webui.write_text("{}", encoding="utf-8")
    prompt_state = tmp_path / "prompt_state.json"
    prompt_state.write_text("{}", encoding="utf-8")
    generated = tmp_path / ".tmp" / "scenario.json"
    generated.parent.mkdir()
    generated.write_text("{}", encoding="utf-8")

    assert _MODULE._paths([str(tmp_path)]) == [scenario.resolve(), webui.resolve()]


def test_does_not_claim_collection_detail_when_both_widgets_share_one_region() -> None:
    page = {
        "id": "single-region",
        "layout": {"type": "single", "areas": [{"id": "main"}]},
        "widgets": [
            {"id": "records", "type": "ui.table", "area": "main"},
            {"id": "record", "type": "item.details", "area": "main"},
        ],
    }

    assert _MODULE.migrate_document(page) == 1
    assert page["layout"]["pattern"] == "collection"
