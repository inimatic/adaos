import pytest

# Application/worker-facing tests deliberately import only the public SDK.
from adaos.sdk import notifications
from adaos.sdk.testing import notification_story


def declaration():
    return {"schema": "adaos.notifications.manifest.v1", "categories": [{
        "id": "task.completed", "title_key": "task.completed.title", "purpose_key": "task.completed.purpose",
        "source_event": "task.completed.v1", "payload_schema": {"type": "object",
        "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False},
        "recipient_policy": "current_user", "default_mode": "center_only", "maximum_urgency": "important",
        "sensitivity": "private", "template": {"en": "Ready: {title}"}, "lifetime_seconds": 86400}]}


def event(now):
    return dict(category_id="task.completed", event_id="E1", source_id="task:1", source_revision=1,
                payload={"title": "Report"}, occurred_at=now)


def test_virtual_story_uses_real_sdk_and_policy_without_external_effects():
    with notification_story(declaration(), management=True) as story:
        endpoint = story.endpoint("max")
        settings = notifications.get_preferences()
        settings["defaults"] = {"mode": "immediate", "endpoint_ids": [endpoint["endpoint_id"]]}
        settings["quiet_hours"].update(enabled=True, timezone="Europe/Moscow")
        notifications.set_preferences(settings, expected_revision=0)
        values = event(story.now)
        first = notifications.emit(**values)
        assert notifications.emit(**values) == first
        assert notifications.get(first["notification_id"])["decision"]["reason"] == "preview"
        assert story.evaluate(first["notification_id"])["reason"] == "quiet_hours"
        story.advance(9*3600)
        assert story.evaluate(first["notification_id"])["alert"]
        assert story.evaluate(first["notification_id"])["provider_calls"] == 0
        notifications.withdraw("task:1")
        assert story.evaluate(first["notification_id"])["reason"] == "withdrawn"


def test_fixture_roles_and_handles_cannot_escape_or_mutate_another_story():
    with notification_story(declaration(), seed="one") as story:
        captured = notifications.capture_publisher(lifetime_seconds=10)
        receipt = notifications.emit_captured(captured["publisher_ref"], **event(story.now))
        with pytest.raises(PermissionError): notifications.get_preferences()
        story.advance(11)
        with pytest.raises(PermissionError): notifications.emit_captured(captured["publisher_ref"], **event(story.now))
    with notification_story(declaration(), seed="two", management=True) as other:
        assert notifications.list_notifications()["items"] == []
        with pytest.raises(PermissionError): other.evaluate(receipt["notification_id"])
        with pytest.raises(PermissionError): notifications.emit_captured(captured["publisher_ref"], **event(other.now))
        prefs = notifications.get_preferences()
        prefs["defaults"] = {"mode": "immediate", "endpoint_ids": ["endpoint:production"]}
        with pytest.raises(PermissionError): notifications.set_preferences(prefs, expected_revision=0)
