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


def test_google_gmail_contract_is_loaded_only_for_relevant_automation_context():
    compact = implementation_binding_contract()
    extended = implementation_binding_contract(include_google_gmail=True)
    assert "google_gmail" not in compact["contracts"]
    assert "google_gmail" not in compact["binding_rules"]
    contract = extended["contracts"]["google_gmail"]
    assert contract["provider_id"] == "google.gmail"
    assert contract["semantic_capability"] == "mail.messages.manage"
    assert contract["project_permission_profile"]["external_provider"]["scopes"] == [
        "https://www.googleapis.com/auth/gmail.modify"
    ]
    native = contract["skill_manifest"]["native_cbs"]
    assert native["semantic_intent"]["owner"] == "accepted_prototype"
    assert native["semantic_intent"]["source"].endswith(
        "/ui/application/pageSchema/meta/builder/cbs_intent"
    )
    assert "production resolution and evidence checks" in native["lifecycle"][
        "trusted_worker_owns"
    ]
    assert "adaos.sdk.developer.validation" in native["lifecycle"]["validation"]
    example = native["authoring_example"]
    assert example["schema"] == "adaos.cbs.provider_authoring.v1"
    cbs_schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src/adaos/abi/cbs.provider_authoring.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(cbs_schema).validate(example)
    assert example["capability"]["ref"] == "capability:mail.messages.manage"
    assert example["binding"]["logical_entrypoint"] == (
        "mail.messages.manage.google-gmail"
    )
    assert {item["tool"] for item in example["capability"]["operations"]} == {
        "begin_connection",
        "connection_status",
        "get_message",
        "list_labels",
        "list_messages",
        "mutate_message",
        "prepare_send",
        "send_message",
    }
    assert native["path"] == "contracts/provider.cbs.yaml"
    assert native["capability_ref"] == "capability:mail.messages.manage"
    assert native["compiler_owned_outputs"] == [
        "contracts/capability.contract.json",
        "contracts/binding.definition.json",
    ]
    assert "adaos.sdk.providers" in contract["sdk"]["import"]
    assert "gmail.GmailProviderError" in contract["sdk"]["error"]
    assert "result:<decoded Gmail REST object>" in contract["sdk"][
        "success_envelopes"
    ]["operation"]["shape"]
    assert "gmail_account_not_connected" in contract["sdk"]["success_envelopes"][
        "connection_status"
    ]["missing"]
    assert "Do not retain ok:false when adding items" in contract[
        "failure_contract"
    ]["read_projection_rule"]
    assert "list_messages" in contract["failure_contract"]["read_projection_rule"]
    assert "successful mutation" in contract["failure_contract"]["rule"]
    schemas = contract["sdk"]["response_schemas"]
    examples = contract["sdk"]["synthetic_examples"]
    jsonschema.Draft202012Validator(schemas["connection_status"]).validate(
        examples["connection_status"]
    )
    operation_validator = jsonschema.Draft202012Validator(schemas["operation"])
    for name in (
        "list_messages",
        "get_message",
        "list_labels",
        "modify_message",
        "trash_message",
        "send_confirmed",
    ):
        operation_validator.validate(examples[name])
    assert examples["send_uncertain"]["delivery_status"] == "unknown"
    assert "do not automatically" in examples["send_uncertain"]["rule"]


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
