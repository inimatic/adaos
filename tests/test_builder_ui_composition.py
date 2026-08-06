from __future__ import annotations

import pytest

from adaos.sdk.builder.prototype import check_spatial_constraint, composition_slice
from adaos.services.builder.workflow import BuilderWorkflowError


def _webui() -> dict:
    return {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "shopping-page",
                        "layout": {
                            "type": "grid",
                            "areas": ["main", "aside"],
                            "responsive": {"compact": "stack", "wide": "grid"},
                        },
                        "widgets": [
                            {
                                "id": "shopping-form",
                                "type": "ui.form",
                                "title": "Add item",
                                "area": "main",
                                "actions": [{"id": "submit", "activity": "shopping.create"}],
                                "inputs": {
                                    "fields": [
                                        {
                                            "id": "title",
                                            "type": "text",
                                            "label": "Item",
                                            "stateKey": "draft.title",
                                        }
                                    ]
                                },
                            },
                            {
                                "id": "shopping-list",
                                "type": "ui.list",
                                "title": "Items",
                                "area": "aside",
                                "binding": {"activity": "shopping.list"},
                            },
                            {"id": "shopping-help", "type": "ui.text", "title": "Help", "area": "aside"},
                        ],
                    }
                }
            }
        },
    }


def test_composition_slice_explains_structure_binding_and_responsive_layout() -> None:
    value = composition_slice(
        _webui(),
        "widget:shopping-list",
        source_revision="004",
        acceptance=[{"relation": "after", "reference_ref": "widget:shopping-form"}],
        evidence_budget=2,
        renderer_snapshots=[
            {"breakpoint": "compact", "visible_order": ["widget:shopping-form", "widget:shopping-list", "widget:shopping-help"], "rects": {}},
            {"breakpoint": "wide", "visible_order": ["widget:shopping-form", "widget:shopping-list", "widget:shopping-help"], "rects": {}},
        ],
    )
    assert value["siblings"] == [
        "widget:shopping-form",
        "widget:shopping-list",
        "widget:shopping-help",
    ]
    assert value["order"] == 1
    assert value["composition"]["responsive"] == {"compact": "stack", "wide": "grid"}
    assert value["bindings"] == {"binding": {"activity": "shopping.list"}}
    assert value["renderer_evidence"]["truncated"] is True
    result = check_spatial_constraint(
        value, {"relation": "after", "reference_ref": "widget:shopping-form", "breakpoints": ["wide"]}
    )
    assert result["passed"] is True
    assert result["evidence_kind"] == "structured_renderer"


def test_field_slice_uses_stable_parent_scoped_reference() -> None:
    value = composition_slice(
        _webui(), "field:shopping-form:title", source_revision="004"
    )
    assert value["target"]["label"] == "Item"
    assert value["parent_ref"] == "widget:shopping-form"
    assert value["bindings"] == {"stateKey": "draft.title"}


def test_spatial_constraint_fails_closed_for_different_collection() -> None:
    value = composition_slice(_webui(), "widget:shopping-list", source_revision="004")
    with pytest.raises(BuilderWorkflowError, match="same stable sibling collection"):
        check_spatial_constraint(
            value, {"relation": "before", "reference_ref": "field:shopping-form:title"}
        )
