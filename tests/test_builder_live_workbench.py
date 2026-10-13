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


def test_bounded_edit_control_is_opt_in_bilingual_and_shared_by_both_composers():
    application = {"desktop": {"pageSchema": {"initialState": {}, "widgets": [
        {"id": f"design-conversation-{position}-task", "inputs": {"meta": {"dialog_channel_id": "builder"}}}
        for position in ("side", "full")]}}, "modals": {"design-settings": {"schema": {"widgets": []}}}}
    live.configure_semantic_edit_controls(application)
    live.configure_semantic_edit_controls(application)
    page = application["desktop"]["pageSchema"]
    assert page["initialState"]["builderSemanticEditMode"] == "full_candidate"
    for widget in page["widgets"]:
        assert widget["inputs"]["meta"]["builder_semantic_edit_mode"] == "$state.builderSemanticEditMode"
        assert widget["inputs"]["meta"]["dialog_channel_id"] == "builder"
    controls = application["modals"]["design-settings"]["schema"]["widgets"]
    assert len(controls) == 2
    scope, control = controls
    assert scope['inputs']['stateBindings']['builderSemanticRefs'] == 'refs'
    assert control['inputs']['autoCommit'] is True
    field = control["inputs"]["fields"][0]
    assert field["stateKey"] == "builderSemanticEditMode"
    assert [option["value"] for option in field["options"]] == ["full_candidate", "bounded_ops"]
    assert set(field["label_i18n"]["locales"]) == {"en", "ru"}


def test_workspace_schema_delivers_the_control_in_settings_not_another_modal():
    import json
    application = json.loads((live.ROOT / '.adaos/workspace/scenarios/builder/webui.json').read_text(
        encoding='utf-8'))['ui']['application']
    matches = [(name, widget) for name, modal in application['modals'].items()
        for widget in modal.get('schema', {}).get('widgets', [])
        if widget.get('id') == 'design-semantic-edit-settings']
    assert len(matches) == 1 and matches[0][0] == 'design-settings'
