"""Focused checks for the live Builder presentation forward-port."""

from scripts import build_builder_workbench_live as live


def test_live_builder_uses_the_current_workbench_layout_contract():
    page = {"layout": {"type": "split"}, "presentation": {"legacy": True}}

    assert live.configure_workbench_page(page) is page
    assert page["surfaceClass"] == "operations"
    assert page["layout"]["pattern"] == "workbench"
    assert page["layout"]["scroll"] == "regions"
    assert page["layout"]["maxContentWidthPx"] == 1600
    assert [(item["id"], item["role"]) for item in page["layout"]["regions"]] == [
        ("main", "main"),
        ("conversation", "detail"),
    ]
    assert page["layout"]["regions"][1]["presentation"]["compact"] == "sheet"
    assert page["layout"]["variants"][0]["id"] == "conversation-focus"
