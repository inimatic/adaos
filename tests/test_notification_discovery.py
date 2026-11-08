import inspect

import pytest

from adaos.sdk import notifications
from adaos.sdk.core.exporter import export
from adaos.sdk.core.notification_capabilities import notification_capability_pack
from adaos.services.conversational_communication_contract import automation_contract
from test_sdk_notifications import sdk_notification_scope


@pytest.mark.parametrize("brief", [
    "remind me tomorrow", "let me know when the report is ready", "too many notifications quiet hours",
    "send completion to MAX", "напомни завтра", "дай знать когда готово",
    "слишком много сообщений", "сводка в Telegram",
])
def test_held_out_notification_briefs_discover_capability(brief):
    result = export(level="brief", query=brief, limit=12)
    names = {tool["name"] for tool in result["tools"]}
    assert "adaos.sdk.capability.notifications.v1" in names or "adaos.sdk.notifications" in names


def test_exact_pack_preserves_authority_and_async_signatures():
    pack = notification_capability_pack()
    assert "notifications.manage" in pack["contract"]["permissions"]
    assert "no_production_preview" in pack["contract"]["action_closure"]["requires"]
    assert inspect.signature(notifications.a_emit) == inspect.signature(notifications.emit)
    for member in pack["members"]:
        import importlib
        module, name = member["api"].rsplit(".", 1)
        assert hasattr(importlib.import_module(module), name)


def test_capability_examples_resolve_to_complete_sdk_only_sources():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    pack = notification_capability_pack()
    for example in pack["examples"]:
        source = root / example["source"]
        assert source.is_file(), example["id"]
        imports = [node for node in ast.walk(ast.parse(source.read_text(encoding="utf8"))) if isinstance(node, ast.ImportFrom)]
        assert all(not (node.module or "").startswith("adaos.services") for node in imports), example["id"]
        if example.get("declaration"):
            assert (root / example["declaration"]).is_file(), example["id"]


def test_worker_channels_and_notification_obligations_have_one_owner():
    packet = automation_contract()
    assert "max" in {item["id"] for item in packet["channels"]}
    assert packet["notifications"]["pack"] == "adaos.sdk.capability.notifications.v1"
    assert "preview" in packet["notifications"]


def test_wire_schemas_validate_real_sdk_results(sdk_notification_scope):
    from jsonschema import Draft202012Validator
    from test_sdk_notifications import values
    receipt = notifications.emit(**values())
    Draft202012Validator(notifications.contract("receipt")).validate(receipt)
    record = notifications.get(receipt["notification_id"])
    Draft202012Validator(notifications.contract("record")).validate(record)
    read = notifications.mark_read(record["notification_id"], content_revision=record["content_revision"],
        expected_revision=record["recipient_revision"], operation_key="mark-test")
    assert read["unread"] is False
    Draft202012Validator(notifications.contract("record")).validate(read)
    Draft202012Validator(notifications.contract("preferences")).validate(notifications.get_preferences())


def test_exact_pack_fingerprint_covers_schema_and_runtime_authority_changes(monkeypatch):
    from copy import deepcopy
    original = notification_capability_pack()
    assert {"publisher_capture", "notification_withdrawal", "isolated_test_state"} <= set(original["contract"]["effects"])
    assert "notification_backpressure" in original["contract"]["errors"]
    metadata = deepcopy(notifications.emit.__adaos_public_contract__)
    metadata["runtime_support"]["min_contract"] = 2
    with monkeypatch.context() as scope:
        scope.setattr(notifications.emit, "__adaos_public_contract__", metadata)
        changed = notification_capability_pack()
        assert changed["contract"]["digest"] != original["contract"]["digest"]
    real_contract = notifications.contract
    def changed_schema(name):
        schema = real_contract(name)
        if name == "manifest":
            schema["description"] = "Changed release admission obligation"
        return schema
    with monkeypatch.context() as scope:
        scope.setattr(notifications, "contract", changed_schema)
        assert notification_capability_pack()["contract"]["schemas_digest"] != original["contract"]["schemas_digest"]
    assert notification_capability_pack()["contract"]["digest"] == original["contract"]["digest"]
