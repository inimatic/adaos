# tests/test_exporter_descriptions.py
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from adaos.sdk.core.exporter import export as sdk_export
from adaos.sdk.core import exporter as sdk_exporter
from adaos.sdk.core.capability_packs import (
    HUMAN_DECISION_PACK_ID,
    validate_human_decision_capability_pack,
)
from adaos.services.root_mcp.registry import get_descriptor_set
from adaos.services.root_mcp.descriptor_search import get_descriptor_item, search_descriptors


_BUILD_METADATA_PATH = Path(__file__).resolve().parents[1] / "tools/build_sdk_metadata.py"
_BUILD_METADATA_SPEC = importlib.util.spec_from_file_location(
    "adaos_build_sdk_metadata_test",
    _BUILD_METADATA_PATH,
)
assert _BUILD_METADATA_SPEC is not None and _BUILD_METADATA_SPEC.loader is not None
build_sdk_metadata = importlib.util.module_from_spec(_BUILD_METADATA_SPEC)
_BUILD_METADATA_SPEC.loader.exec_module(build_sdk_metadata)


def test_sdk_export_std():
    data = sdk_export(level="std")
    assert "tools" in data and isinstance(data["tools"], list)


def test_sdk_compatibility_report_rejects_removal_and_required_input() -> None:
    previous = {
        "tools": [
            {
                "name": "adaos.sdk.example.read",
                "input_schema": {"required": []},
                "contract": {"permissions": [], "pagination": {"supported": True}},
            },
            {"name": "adaos.sdk.example.removed"},
        ]
    }
    current = {
        "tools": [
            {
                "name": "adaos.sdk.example.read",
                "input_schema": {"required": ["scope"]},
                "contract": {"permissions": [], "pagination": {"supported": True}},
            }
        ]
    }

    report = sdk_exporter.compatibility_report(previous, current)

    assert report["compatible"] is False
    assert {item["kind"] for item in report["breaking"]} == {
        "removed",
        "required_inputs_added",
    }


def test_sdk_compatibility_report_checks_nested_input_and_output_contracts() -> None:
    previous = {
        "tools": [
            {
                "name": "adaos.sdk.example.decide",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "request": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string", "enum": ["safe", "fast"]},
                                "note": {"type": "string"},
                            },
                            "required": ["mode"],
                            "additionalProperties": True,
                        }
                    },
                    "required": ["request"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok"]}},
                },
                "contract": {},
            }
        ]
    }
    current = {
        "tools": [
            {
                "name": "adaos.sdk.example.decide",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "request": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string", "enum": ["safe"]},
                                "reason": {"type": "string"},
                            },
                            "required": ["mode", "reason"],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["request"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok", "partial"]}},
                },
                "contract": {},
            }
        ]
    }

    report = sdk_exporter.compatibility_report(previous, current)

    assert report["compatible"] is False
    findings = {(item["kind"], item.get("path")) for item in report["breaking"]}
    assert ("input_schema_enum_incompatible", "$.request.mode") in findings
    assert ("input_schema_property_removed", "$.request.note") in findings
    assert ("input_schema_required_added", "$.request") in findings
    assert ("input_schema_closed", "$.request") in findings
    assert ("output_schema_enum_incompatible", "$.status") in findings


def test_sdk_descriptor_applies_consumer_field_masks() -> None:
    builder = get_descriptor_set(
        "sdk_metadata",
        level="std",
        query="adaos.sdk.control_plane.list_quota_objects",
        consumer="builder",
    )["payload"]
    diagnostics = get_descriptor_set(
        "sdk_metadata",
        level="std",
        query="adaos.sdk.control_plane.list_quota_objects",
        consumer="diagnostics",
    )["payload"]

    assert builder["consumer"] == "builder"
    assert diagnostics["consumer"] == "diagnostics"
    assert "permissions" in builder["field_mask"]["contract"]
    assert "permissions" not in diagnostics["field_mask"]["contract"]


def test_sdk_export_mini_lines():
    # mini нужен для LLM ранней стадии
    data = sdk_export(level="mini")
    lines = data.get("__mini_lines__", [])
    # экспортер может возвращать сразу строки или контейнер — проверим оба варианта
    assert isinstance(lines, (list, tuple))


def test_sdk_export_mini_selects_public_quota_sdk_from_task_language():
    data = sdk_export(
        level="mini",
        query=(
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u0440\u0430\u0441\u0445\u043e\u0434 "
            "\u0442\u043e\u043a\u0435\u043d\u043e\u0432 \u0438 \u043e\u0441\u0442\u0430\u0442\u043e\u043a "
            "\u043a\u0432\u043e\u0442\u044b"
        ),
    )

    names = {str(item.get("n") or "") for item in data["items"]}
    assert "adaos.sdk.control_plane.list_quota_objects" in names
    assert len(data["items"]) <= 24


def test_human_decision_sdk_contract_is_discoverable_in_english_and_russian():
    english = sdk_export(level="std", query="human decision approval", limit=12)
    russian = sdk_export(
        level="std",
        query="решение человека и подтверждение",
        limit=12,
    )
    expected = {
        "adaos.sdk.chat.request",
        "adaos.sdk.workflow.create_interaction",
    }

    assert expected.issubset({item["name"] for item in english["tools"]})
    assert expected.issubset({item["name"] for item in russian["tools"]})
    request = next(
        item for item in english["tools"] if item["name"] == "adaos.sdk.chat.request"
    )
    assert "human_decision.present" in request["contract"]["capabilities"]
    assert request["contract"]["runtime_support"]["owner"] == "conversation_runtime"
    closure = request["contract"]["action_closure"]["requires"]
    assert "standard_action_preset_or_custom_semantics" in closure
    assert "presentation_bound_action_token" in closure
    assert "semantic_digest" in closure
    assert "expiry" in closure
    assert request["input_schema"]["required"] == [
        "interaction",
        "conversation_id",
        "owner",
    ]
    interaction_variants = request["input_schema"]["properties"]["interaction"]["anyOf"]
    typed_variants = [item for item in interaction_variants if item.get("type") == "object"]
    assert len(typed_variants) == 2
    assert all(item["additionalProperties"] is False for item in typed_variants)
    assert "prompt" in typed_variants[0]["properties"]
    assert "interaction_id" in typed_variants[1]["properties"]
    assert request["output_schema"]["additionalProperties"] is False
    assert request["output_schema"]["required"] == [
        "ok",
        "handle",
        "interaction",
        "presentation",
        "materialization",
    ]
    assert request["contract"]["schema_refs"]["origin"] == "signature_annotations"
    Draft202012Validator.check_schema(request["input_schema"])
    Draft202012Validator.check_schema(request["output_schema"])

    workflow = next(
        item
        for item in english["tools"]
        if item["name"] == "adaos.sdk.workflow.create_interaction"
    )
    workflow_closure = workflow["contract"]["action_closure"]["requires"]
    assert "executor_readiness" in workflow_closure
    assert "durable_dispatch" in workflow_closure
    assert "effect_assertion" in workflow_closure
    assert "durable_outcome" in workflow_closure
    definition = workflow["input_schema"]["properties"]["definition"]
    typed_definition = next(
        item
        for item in definition["anyOf"]
        if item.get("type") == "object" and "schema" in item.get("properties", {})
    )
    assert typed_definition["additionalProperties"] is False
    assert typed_definition["required"] == [
        "schema",
        "workflow_type",
        "definition_version",
        "initial_state",
        "states",
        "commands",
    ]
    executor_items = workflow["input_schema"]["properties"]["executor_registrations"]["items"]
    assert executor_items["required"] == ["adapter_id", "contract_digest", "executor_id"]
    assert executor_items["additionalProperties"] is False


def test_human_decision_capability_pack_closes_sdk_lifecycle() -> None:
    exported = sdk_export(level="std")
    pack = next(
        item for item in exported["tools"] if item["name"] == HUMAN_DECISION_PACK_ID
    )

    report = validate_human_decision_capability_pack(pack, exported["tools"])

    assert report["ok"] is True
    assert report["findings"] == []
    assert report["coverage"] == {
        "members": 4,
        "positive_examples": 1,
        "negative_examples": 5,
    }
    assert pack["contract"]["digest"].startswith("sha256:")
    assert [stage["stage"] for stage in pack["examples"][0]["stages"]] == [
        "publish",
        "present",
        "verified_answer",
        "dispatch_effect_outcome",
    ]

    search = search_descriptors(
        "complete human decision lifecycle publish effect outcome",
        descriptor_ids=["sdk_metadata"],
        limit=6,
    )
    assert search["items"][0]["item_id"] == HUMAN_DECISION_PACK_ID
    detail = get_descriptor_item("sdk_metadata", HUMAN_DECISION_PACK_ID)
    assert detail["item"]["validators"] == pack["validators"]
    assert detail["receipt"]["source_digests"]["examples"].startswith("sha256:")


def test_human_decision_capability_pack_validator_rejects_missing_closure() -> None:
    exported = sdk_export(level="std")
    pack = next(
        item for item in exported["tools"] if item["name"] == HUMAN_DECISION_PACK_ID
    )
    request = next(
        item for item in exported["tools"] if item["name"] == "adaos.sdk.chat.request"
    )
    request["contract"]["action_closure"]["requires"].remove("expiry")

    report = validate_human_decision_capability_pack(pack, exported["tools"])

    assert report["ok"] is False
    assert {
        "code": "sdk_action_closure_incomplete",
        "api": "adaos.sdk.chat.request",
        "missing": ["expiry"],
    } in report["findings"]


def test_sdk_contract_is_generated_from_one_metadata_source() -> None:
    def sample(limit: int = 10, cursor: str | None = None):
        return None

    contract = sdk_exporter._sdk_contract(
        "manage.sample.list",
        sample,
        {
            "permissions": ["workspace.read"],
            "effects": ["read_only"],
            "errors": ["not_found"],
            "stability": "beta",
            "since": "1.4.0",
            "deprecated": True,
            "removed_in": "2.0.0",
            "replacement": "manage.sample.search",
            "migration_recipe": "Replace list with bounded search.",
        },
    )

    assert contract["permissions"] == ["workspace.read"]
    assert contract["boundedness"]["kind"] == "bounded_page"
    assert contract["pagination"]["supported"] is True
    assert contract["removedIn"] == "2.0.0"
    assert contract["authoring_visibility"] == "migration_only"
    assert contract["digest"].startswith("sha256:")


def test_deprecated_sdk_members_are_hidden_from_authoring_but_visible_to_migration(monkeypatch) -> None:
    item = {
        "kind": "sdk_function",
        "name": "adaos.sdk.example.legacy",
        "module": "adaos.sdk.example",
        "summary": "Legacy API.",
        "meta": {"stability": "deprecated"},
        "contract": {"deprecated": True, "authoring_visibility": "migration_only"},
    }
    monkeypatch.setattr(sdk_exporter, "_filter_tools", lambda: [])
    monkeypatch.setattr(sdk_exporter, "_public_facade_symbols", lambda _level: [item])

    authoring = sdk_exporter.export(
        level="std", query="legacy", include_deprecated=False
    )
    migration = sdk_exporter.export(
        level="std", query="legacy", include_deprecated=True
    )

    assert authoring["tools"] == []
    assert [entry["name"] for entry in migration["tools"]] == [
        "adaos.sdk.example.legacy"
    ]


def test_pending_action_sdk_exposes_bounded_read_and_migration_only_publish() -> None:
    authoring = sdk_export(level="std", query="pending action human decision", limit=24)
    migration = sdk_export(
        level="std",
        query="pending action human decision legacy publish",
        limit=24,
        include_deprecated=True,
    )
    authoring_names = {item["name"] for item in authoring["tools"]}
    migration_names = {item["name"] for item in migration["tools"]}

    assert "adaos.sdk.data.pending_actions.list_pending_actions" in authoring_names
    assert "adaos.sdk.data.pending_actions.publish_pending_action" not in authoring_names
    assert "adaos.sdk.data.pending_actions.publish_pending_action" in migration_names
    detail = next(
        item
        for item in authoring["tools"]
        if item["name"] == "adaos.sdk.data.pending_actions.list_pending_actions"
    )
    assert detail["contract"]["boundedness"]["kind"] == "bounded_page"
    assert detail["contract"]["pagination"]["supported"] is True


def test_canonical_sdk_bundle_excludes_wall_clock_metadata(monkeypatch) -> None:
    generated = iter(("2026-10-03T05:00:00+00:00", "2026-10-03T05:00:01+00:00"))

    monkeypatch.setattr(
        build_sdk_metadata,
        "export",
        lambda **_kwargs: {
            "meta": {
                "generated_at": next(generated),
                "git_sha": "revision-1",
                "py": "3.11",
            },
            "tools": [],
        },
    )

    bundle = build_sdk_metadata.build_bundle()

    assert bundle["source_revision"] == "revision-1"
    assert "generated_at" not in bundle["authoring"]["meta"]
    assert "generated_at" not in bundle["migration"]["meta"]
    assert str(bundle["digest"]).startswith("sha256:")


def test_canonical_sdk_bundle_contains_typed_public_facades() -> None:
    bundle = build_sdk_metadata.build_bundle()
    authoring = {
        item["name"]: item
        for item in bundle["authoring"]["tools"]
        if isinstance(item, dict) and item.get("name")
    }
    migration = {
        item["name"]: item
        for item in bundle["migration"]["tools"]
        if isinstance(item, dict) and item.get("name")
    }

    request = authoring["adaos.sdk.chat.request"]
    assert request["input_schema"]["required"] == [
        "interaction",
        "conversation_id",
        "owner",
    ]
    assert request["contract"]["schema_refs"]["input"].startswith("sha256:")
    assert "adaos.sdk.data.pending_actions.publish_pending_action" not in authoring
    assert "adaos.sdk.data.pending_actions.publish_pending_action" in migration


def test_sdk_metadata_mini_is_a_bounded_nonduplicated_mcp_projection():
    descriptor = get_descriptor_set(
        "sdk_metadata",
        level="mini",
        query="Show token usage and remaining quota",
    )
    payload = descriptor["payload"]

    assert "items" not in payload
    assert payload["overview_rows"]
    assert any(
        row["row_id"] == "adaos.sdk.control_plane.list_quota_objects"
        for row in payload["overview_rows"]
    )
    quota_row = next(
        row
        for row in payload["overview_rows"]
        if row["row_id"] == "adaos.sdk.control_plane.list_quota_objects"
    )
    assert quota_row["metadata"]["args"] == ["webspace_id?"]
    assert not any(
        row["row_id"] == "adaos.sdk.research.apply_projection_patch"
        for row in payload["overview_rows"]
    )
    assert len(json.dumps(descriptor, ensure_ascii=False).encode("utf-8")) < 12_000


def test_sdk_descriptor_drilldown_states_quota_contract_boundary():
    detail = get_descriptor_item(
        "sdk_metadata",
        "adaos.sdk.control_plane.list_quota_objects",
        level="std",
    )

    item = detail["item"]
    description = " ".join(item["description"].split())
    assert "CanonicalObject.to_dict()" in description
    assert "does not return subscription-plan LLM or Codex token usage" in description
    receipt = detail["receipt"]
    assert receipt["selection_reason"] == "exact_item_id"
    assert receipt["source_digests"]["item"].startswith("sha256:")
    assert receipt["source_digests"]["input_schema"].startswith("sha256:")
    assert receipt["descriptor_etag"].startswith("sha256:")
    assert receipt["provenance"]["published_by"] == "root"


def test_sdk_resource_and_persistent_data_contracts_are_discoverable():
    for query, expected in (
        ("skill_data_root", "adaos.sdk.data.skill_env.skill_data_root"),
        ("adaos.sdk.resources.operate", "adaos.sdk.resources.operate"),
        ("adaos.sdk.access.caller", "adaos.sdk.access.caller"),
        ("adaos.sdk.access.invocation", "adaos.sdk.access.invocation"),
        ("adaos.sdk.access.require", "adaos.sdk.access.require"),
        ("adaos.sdk.data.lifecycle.ensure_database", "adaos.sdk.data.lifecycle.ensure_database"),
        ("adaos.sdk.data.blob.put_upload", "adaos.sdk.data.blob.put_upload"),
        ("adaos.sdk.llm.images.generate", "adaos.sdk.llm.images.generate"),
    ):
        result = search_descriptors(query, descriptor_ids=["sdk_metadata"], limit=6)
        assert result["items"][0]["item_id"] == expected
        contract = get_descriptor_item("sdk_metadata", expected)["item"]
        assert contract["name"] == expected
        assert contract["description"] and "signature_detail" in contract


def test_automation_inventory_contract_is_discoverable() -> None:
    expected = "adaos.sdk.automation.inventory"
    result = search_descriptors(expected, descriptor_ids=["sdk_metadata"], limit=6)

    assert result["items"][0]["item_id"] == expected
    contract = get_descriptor_item("sdk_metadata", expected)["item"]
    assert contract["name"] == expected
    assert "bounded fleet snapshot" in contract["description"]


def test_sdk_catalog_is_navigation_not_an_empty_method_contract():
    result = search_descriptors("public SDK skill_data_root", limit=6)
    assert result["items"][0]["item_id"] == "adaos.sdk.data.skill_env.skill_data_root"
    catalog = get_descriptor_item("sdk_metadata", "sdk_metadata")
    assert catalog["item"]["discovery"]["descriptor_ids"] == ["sdk_metadata"]
    assert "payload" not in catalog["item"]


def test_configuration_descriptor_states_runtime_and_secret_boundaries():
    detail = get_descriptor_item("sdk_metadata", "adaos.sdk.data.configuration.read")["item"]
    assert "revision" in detail["description"]
    assert "runtime_scope" in detail["description"]
    assert "development" in detail["description"]
    assert "installed" in detail["description"]
    write = get_descriptor_item("sdk_metadata", "adaos.sdk.data.configuration.write")["item"]
    assert "Credential bindings are preserved" in write["description"]


def test_blob_upload_descriptor_states_binary_and_permission_boundaries():
    detail = get_descriptor_item(
        "sdk_metadata",
        "adaos.sdk.data.blob.put_upload",
    )["item"]
    description = " ".join(detail["description"].split())

    assert "one-use context" in description
    assert "storage.blob" in description
    assert "workspace.write" in description
    assert "workspace.read" in description
    assert "fileStorage: skill" in description
    assert "/api/tools/.../attachments/..." in description
    assert "persist" in description.lower()
    assert detail["signature_detail"]["args"][0]["name"] == "name"
