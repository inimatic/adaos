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
        assert hasattr(notifications, member["api"].rsplit(".", 1)[1])


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
