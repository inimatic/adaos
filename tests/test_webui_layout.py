from __future__ import annotations

import pytest

from adaos.services.webui_layout import LAYOUT_PATTERNS, layout_v2_findings


def _region(region_id: str, role: str, *, compact: str = "stack") -> dict:
    return {
        "id": region_id,
        "role": role,
        "presentation": {"wide": "pane", "compact": compact},
    }


@pytest.mark.parametrize("pattern", sorted(LAYOUT_PATTERNS))
def test_every_layout_pattern_has_a_conformant_minimal_shape(pattern: str) -> None:
    regions = [_region("main", "main")]
    if pattern == "collection":
        regions = [_region("items", "collection")]
    elif pattern == "collection-detail":
        regions = [
            _region("items", "collection"),
            _region("details", "detail", compact="sheet"),
        ]
    page = {
        "id": "example",
        "layout": {
            "version": 2,
            "pattern": pattern,
            "density": "comfortable",
            "regions": regions,
        },
        "widgets": [],
    }

    assert layout_v2_findings(page, schema_path="$.page") == []


def test_layout_findings_reject_ambiguous_and_unreachable_regions() -> None:
    page = {
        "id": "broken",
        "layout": {
            "version": 2,
            "pattern": "collection-detail",
            "density": "comfortable",
            "regions": [
                _region("items", "collection"),
                _region("items", "main"),
                _region("details", "detail", compact="hidden"),
            ],
        },
        "widgets": [{"id": "missing", "type": "static.text", "area": "other"}],
    }

    codes = {
        item["code"] for item in layout_v2_findings(page, schema_path="$.page")
    }
    assert codes == {
        "ui.layout.region_id_duplicate",
        "ui.layout.required_region_hidden",
        "ui.layout.primary_region_invalid",
        "ui.layout.widget_region_missing",
    }
