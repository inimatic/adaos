from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.ui_resource_queries import widget_resource_queries
from adaos.services.ui_widget_inputs import flatten_widgets, input_contracts, widget_input_findings

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "src/adaos/abi/webui.v1.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize('value,rejected', [
    ('$state.settings_version + 1', True),
    ('$state.settings_version - 1', True),
    ({'kind':'expression','op':'add','args':['$state.settings_version',1]}, False),
    ('$event.version', False),
    (1, False),
])
def test_numeric_state_update_does_not_silently_use_text_interpolation(value,rejected):
    from adaos.services.ui_capabilities import validate_webui_capabilities
    page={'initialState':{'settings_version':0},'widgets':[{'id':'edit','type':'ui.actions',
        'inputs':{'buttons':[{'id':'next','label':'Next'}]},
        'actions':[{'on':'click:next','type':'updateState','params':{'settings_version':value}}]}]}
    result=validate_webui_capabilities({'ui':{'application':{'desktop':{'pageSchema':page}}}})
    findings=result.get('findings',[])
    assert any(row['code']=='ui.action.numeric_binding_is_interpolated_text' for row in findings) is rejected


def test_expression_discriminator_and_record_selection_are_required():
    from adaos.services.ui_capabilities import validate_webui_capabilities
    page={'initialState':{'version':0},'widgets':[
      {'id':'counter','type':'ui.actions','inputs':{'buttons':[{'id':'next','label':'Next'}]},
       'actions':[{'on':'click:next','type':'updateState','params':{'version':{'op':'add','args':['$state.version',1]}}}]},
      {'id':'edit','type':'ui.form','dataSource':{'kind':'skill','name':'app.read','params':{'filters':{'id':'$state.selected_id'}}},
       'inputs':{'fields':[{'id':'title','type':'shortText'}]}}]}
    ui={'ui':{'application':{'desktop':{'pageSchema':page}}}}
    findings=validate_webui_capabilities(ui)['findings']
    codes={f['code'] for f in findings}
    assert {'ui.action.expression_discriminator_missing','ui.form.record_selection_missing'}<=codes
    page['widgets'][0]['actions'][0]['params']['version']['kind']='expression'
    page['widgets'][1]['inputs']['selectedStateKey']='selected_id'
    codes={f['code'] for f in validate_webui_capabilities(ui)['findings']}
    assert not {'ui.action.expression_discriminator_missing','ui.form.record_selection_missing'}&codes


def test_selected_workspace_contract_is_self_contained_for_generation_and_repair():
    from adaos.services.ui_capabilities import selected_ui_capabilities

    selection = selected_ui_capabilities('Use recipe.annotation_workspace', limit=1)
    components = {item['id']: item for item in selection['items']}
    image = components['visual.imageCompare']['manifest']
    assert image['annotation_document_schema'] == json.loads((ROOT/'src/adaos/abi/image.annotations.v1.schema.json').read_text(encoding='utf-8'))
    assert image['input_contract']['additionalProperties'] is False
    assert 'annotationGeneration' in image['annotation_commands']['guard']
    assert 'subtract' in image['annotation_commands']['addAnnotationStrokes']
    assert components['feedback.taskProgress']['manifest']['task_record_schema']['$id'] == 'adaos.ui.task_progress.v1'


def validator(name):
    return Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})


def test_widget_input_projection_is_current_and_matches_server():
    spec = importlib.util.spec_from_file_location("widget_projection", ROOT / "scripts/generate_ui_widget_inputs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TARGET.read_text(encoding="utf-8") == module.render()
    assert set(input_contracts()) == set(module.INPUT_DEFINITIONS)


@pytest.mark.parametrize("type_,inputs,valid", [
    ("visual.imageCompare", {"imaginaryMaskLayers": []}, False),
    ("visual.imageCompare", {"editableMasks": "yes"}, False),
    ("visual.imageCompare", {"editableMasks": True, "labels": {"redo": "Redo"}}, True),
    ("layout.group", {"layout": "grid", "columns": 0}, False),
    ("layout.group", {"layout": "row", "wrap": True, "gap": "sm"}, True),
    ("feedback.taskProgress", {"taskKey": "run", "labels": {"cancel": "Stop"}}, True),
    ("feedback.taskProgress", {"fakeProgress": True}, False),
])
def test_strict_widget_inputs_reach_both_schema_and_builder(type_, inputs, valid):
    widget = {"id": "test", "type": type_, "area": "main", "inputs": inputs}
    assert validator("widgetConfig").is_valid(widget) is valid
    assert (not widget_input_findings({"widgets": [widget]})) is valid


def test_nested_widgets_preserve_resource_dependencies_and_are_checked():
    child = {"id": "photos", "type": "ui.list", "area": "content", "dataSource": {"kind": "resourceQuery", "resourceType": "prototype.photos"}}
    nested = {"id": "nested", "type": "layout.group", "area": "content", "children": [child]}
    group = {"id": "group", "type": "layout.group", "area": "main", "inputs": {"layout": "section"}, "children": [nested]}
    validator("widgetConfig").validate(group)
    assert [item["id"] for item in flatten_widgets([group])] == ["group", "nested", "photos"]
    assert list(widget_resource_queries(group)) == [child["dataSource"]]
    assert widget_input_findings({"widgets": [group]}) == []
    nested["children"].append({"id": "photos", "type": "visual.imageCompare", "area": "content", "inputs": {"imaginary": True}})
    errors = widget_input_findings({"widgets": [group]})
    assert len(errors) == 2
    assert any("unique" in item["message"] for item in errors)
    assert any("imaginary" in item["message"] for item in errors)


def test_composition_is_bounded_and_other_widgets_cannot_silently_own_children():
    bad = {"id": "bad", "type": "ui.list", "area": "main", "children": []}
    assert not validator("widgetConfig").is_valid(bad)
    assert widget_input_findings({"widgets": [bad]})
    group = {"id": "level0", "type": "layout.group", "area": "main", "children": []}
    current = group
    for index in range(1, 11):
        child = {"id": f"level{index}", "type": "layout.group", "area": "content", "children": []}
        current["children"].append(child)
        current = child
    assert any("8 nested" in item["message"] for item in widget_input_findings({"widgets": [group]}))


def test_native_mask_contract_preserves_full_geometry_and_rejects_missing_encoding():
    schema = json.loads((ROOT / "src/adaos/abi/image.annotations.v1.schema.json").read_text())
    mask = {"schema": "adaos.image_annotations.v1", "width": 7141, "height": 4763, "layers": [{"id": "subject-result", "label": "Result contour", "side": "result", "role": "subject", "encoding": "alpha", "mask": {"content_path": "/api/node/media/content/mask.png"}, "strokes": [{"kind": "subtract", "radius": 3, "points": [{"x": 7000.25, "y": 4000.75}]}]}]}
    Draft202012Validator(schema).validate(mask)
    del mask["layers"][0]["encoding"]
    assert not Draft202012Validator(schema).is_valid(mask)


def test_panel_resize_contract_is_available_to_pages_and_modals():
    region = {"id": "inspector", "role": "inspector", "presentation": {"wide": "pane", "compact": "sheet"}, "size": {"minPx": 320, "preferredPx": 370, "maxPx": 560, "resizable": True, "remember": True}}
    validator("layoutRegion").validate(region)
    region["size"]["resizable"] = "yes"
    assert not validator("layoutRegion").is_valid(region)


def test_both_builder_validation_paths_reject_unknown_nested_editor_inputs():
    from adaos.services.ui_capabilities import validate_webui_capabilities
    from adaos.services.builder.domain_packs.application_manager_legacy import validate_webui_capabilities as legacy_validate
    page = {"id": "workspace", "layout": {"version": 2, "pattern": "document", "density": "comfortable", "regions": [{"id": "main", "role": "main", "presentation": {"wide": "pane", "compact": "stack"}}]}, "widgets": [{"id": "group", "type": "layout.group", "area": "main", "children": [{"id": "editor", "type": "visual.imageCompare", "area": "content", "inputs": {"imaginaryLayer": True}}]}]}
    document = {"ui": {"application": {"desktop": {"pageSchema": page}}}}
    for validate in (validate_webui_capabilities, legacy_validate):
        report = validate(document)
        assert any(finding["code"] == "ui.component.contract_invalid" for finding in report["findings"])
