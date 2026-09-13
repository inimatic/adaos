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
    for name in ("read_collection", "record_editor"):
        ref = guide["schema_refs"][name].split("#", 1)[1]
        jsonschema.Draft202012Validator({**schema, "$ref": f"#{ref}"}).validate(guide["examples"][name])
    assert len(json.dumps(guide, ensure_ascii=False).encode("utf-8")) < 12_000
    assert "Prototype" in guide["binding_rules"]["attachments"] or "Preview" in guide["binding_rules"]["attachments"]
    assert "metadata only" in guide["binding_rules"]["attachments"]
    assert "workspace.write" in guide["binding_rules"]["authorization"]
    assert "data_routes[*].tool is the LOCAL" in guide["binding_rules"]["tool_declarations"]
    assert "callSkill.target are QUALIFIED" in guide["binding_rules"]["tool_declarations"]
    assert "not an installed-skill contract" in guide["binding_rules"]["package_tests"]
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


def test_creation_contract_matches_state_hydration_instead_of_dynamic_defaults():
    guide = implementation_binding_contract()
    rule = guide["binding_rules"]["creation"]
    assert "without dataSource" in rule
    assert "form.<widget.id>.<field.id>" in rule
    assert "literal values, not $state expressions" in rule
    assert "empty selected id ignores" in rule
    assert "related_choices" in rule
    root = Path(__file__).resolve().parents[1] / "src/adaos/abi"
    catalog = json.loads((root / "ui.capability_catalog.v1.json").read_text(encoding="utf-8"))
    form = next(item for item in catalog["components"] if item["id"] == "ui.form")
    assert "state_initialized_creation" in form["manifest"]


def test_skill_choice_source_is_admitted_but_arbitrary_transports_are_not():
    root = Path(__file__).resolve().parents[1] / "src/adaos/abi"
    schema = json.loads((root / "webui.v1.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/formField"})
    field = {"id": "related", "type": "dropdown", "optionValuePath": "id", "optionLabelPaths": ["name"],
             "optionsDataSource": {"kind": "skill", "name": "sample_skill.list_records",
                                   "invalidationTags": ["sample.records"]}}
    assert not list(validator.iter_errors(field))
    field["optionsDataSource"] = {"kind": "api", "url": "https://example.org"}
    assert list(validator.iter_errors(field))
