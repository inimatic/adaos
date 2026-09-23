from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema

from adaos.sdk.web import implementation_binding_contract


def test_implementation_binding_guide_uses_current_abi_and_valid_examples():
    guide = implementation_binding_contract()
    root = Path(__file__).resolve().parents[1] / "src/adaos/abi"
    schema = json.loads((root / "webui.v1.schema.json").read_text(encoding="utf-8"))
    for name, receipt in guide["sources"].items():
        raw = (root / name).read_bytes()
        assert receipt == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    for name in (
        "read_collection",
        "record_editor",
        "dynamic_record_editor",
        "board_move",
    ):
        ref = guide["schema_refs"][name].split("#", 1)[1]
        jsonschema.Draft202012Validator({**schema, "$ref": f"#{ref}"}).validate(
            guide["examples"][name]
        )
    assert len(json.dumps(guide, ensure_ascii=False).encode("utf-8")) < 14_000
    assert "workspace.write" in guide["binding_rules"]["authorization"]
    assert (
        "data_routes[*].tool is the LOCAL"
        in guide["binding_rules"]["tool_declarations"]
    )
    assert (
        "callSkill.target are QUALIFIED" in guide["binding_rules"]["tool_declarations"]
    )
    assert "not an installed-skill contract" in guide["binding_rules"]["package_tests"]
    assert "checkpoint/materializer fields" in guide["binding_rules"]["package_tests"]
    assert "equivalence with webui.json" in guide["binding_rules"]["package_tests"]
    assert "invalidates:[]" in guide["binding_rules"]["result"]
    assert "GLOBAL refresh default" in guide["binding_rules"]["result"]
    assert "entity/projection tags" in guide["binding_rules"]["result"]
    assert "derived captions, choices or locks" in guide["binding_rules"]["result"]
    assert "every changed consumer is refreshed" in guide["binding_rules"]["result"]
    assert guide["examples"]["board_move"]["params"]["revision"] == "$event.revision"
    assert "rolls" in guide["binding_rules"]["board_move"]
    assert "on=click:<command>" in guide["binding_rules"]["selection"]
    assert "SAME id" in guide["binding_rules"]["mutation"]
    assert "on=submit alone does not bind" in guide["binding_rules"]["mutation"]
    assert "actions remain package-owned" in guide["binding_rules"]["dynamic_editor"]
    catalog = json.loads(
        (root / "ui.capability_catalog.v1.json").read_text(encoding="utf-8")
    )
    details = next(
        item for item in catalog["components"] if item["id"] == "item.details"
    )
    assert "false/throw" in details["manifest"]["commands"]
    guide["examples"].clear()
    assert implementation_binding_contract()["examples"]


def test_editor_binding_retains_loaded_revision_and_does_not_fake_upload():
    guide = implementation_binding_contract()
    editor = guide["examples"]["record_editor"]
    command = editor["actions"][0]
    assert command["params"]["revision"] == "$event.record.revision"
    assert editor["inputs"]["selectedStateKey"] == "selectedId"
    assert editor["dataSource"]["params"]["id"] == "$state.selectedId"
    assert command["invalidates"] == editor["dataSource"]["invalidationTags"]
    assert command["resultPath"] == "item"
    assert not editor["inputs"]["resetOnSuccess"]
    assert guide["examples"]["rejected_write"]["ok"] is False


def test_attachment_contract_is_loaded_only_for_relevant_automation_context():
    compact = implementation_binding_contract()
    extended = implementation_binding_contract(include_attachments=True)
    assert "production_attachment" not in compact["contracts"]
    assert "attachments" not in compact["binding_rules"]
    contract = extended["contracts"]["production_attachment"]
    assert contract["upload_tool"]["manifest"]["permissions"] == [
        "storage.blob",
        "workspace.write",
    ]
    assert "metadata only" in extended["binding_rules"]["attachments"]


def test_creation_contract_matches_state_hydration_instead_of_dynamic_defaults():
    guide = implementation_binding_contract()
    rule = guide["binding_rules"]["creation"]
    assert "without dataSource" in rule
    assert "form.<widget.id>.<field.id>" in rule
    assert "literal values, not $state expressions" in rule
    assert "empty selected id ignores" in rule
    assert "related_choices" in rule
    root = Path(__file__).resolve().parents[1] / "src/adaos/abi"
    catalog = json.loads(
        (root / "ui.capability_catalog.v1.json").read_text(encoding="utf-8")
    )
    form = next(item for item in catalog["components"] if item["id"] == "ui.form")
    assert "state_initialized_creation" in form["manifest"]


def test_skill_choice_source_is_admitted_but_arbitrary_transports_are_not():
    root = Path(__file__).resolve().parents[1] / "src/adaos/abi"
    schema = json.loads((root / "webui.v1.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/formField"})
    field = {
        "id": "related",
        "type": "dropdown",
        "optionValuePath": "id",
        "optionLabelPaths": ["name"],
        "optionsDataSource": {
            "kind": "skill",
            "name": "sample_skill.list_records",
            "invalidationTags": ["sample.records"],
        },
    }
    assert not list(validator.iter_errors(field))
    field["optionsDataSource"] = {"kind": "api", "url": "https://example.org"}
    assert list(validator.iter_errors(field))


def test_free_form_tag_input_is_a_typed_string_list_contract():
    root = Path(__file__).resolve().parents[1] / "src/adaos/abi"
    schema = json.loads((root / "webui.v1.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/formField"})
    assert not list(
        validator.iter_errors(
            {
                "id": "permission_ceiling",
                "type": "tagInput",
                "label": "Permissions",
                "defaultValue": ["workspace.read"],
            }
        )
    )
    catalog = json.loads(
        (root / "ui.capability_catalog.v1.json").read_text(encoding="utf-8")
    )
    form = next(item for item in catalog["components"] if item["id"] == "ui.form")
    assert "tagInput" in form["manifest"]["supported_field_types"]
    assert "open vocabularies" in form["manifest"]["free_form_string_lists"]
