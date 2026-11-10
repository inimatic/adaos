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


def test_async_preview_work_retains_isolation_after_fixture_exit(monkeypatch):
    import asyncio
    import socket
    def forbidden_network(*args, **kwargs):
        pytest.fail("isolated notification story attempted a real network connection")
    async def run():
        # Windows creates its event-loop self-pipe using a local TCP socketpair.
        # Install the tripwire after loop construction; all SDK work is covered.
        with monkeypatch.context() as network:
            network.setattr(socket.socket, "connect", forbidden_network)
            released = asyncio.Event()
            async def continuation(publisher, request):
                await released.wait()
                return await notifications.a_emit_captured(publisher, **request)
            with notification_story(declaration(), management=True) as story:
                publisher = await notifications.a_capture_publisher()
                first = await notifications.a_emit(**event(story.now))
                assert (await notifications.a_get(first["notification_id"]))["test_only"]
                pending = asyncio.create_task(continuation(publisher["publisher_ref"], event(story.now)))
            released.set()
            with pytest.raises(PermissionError, match="closed"):
                await pending
    asyncio.run(run())


def test_real_fixture_endpoint_cannot_cross_stories_and_virtual_gc_cannot_restore_content(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Preview made a network connection"))
    with notification_story(declaration(), management=True, seed="one") as story:
        endpoint = story.endpoint("max")
        first = notifications.emit(**event(story.now))
        story.advance(32*86400)
        assert notifications.get(first["notification_id"]) is None
        assert notifications.emit(**event(story.now-32*86400)) == first
    with notification_story(declaration(), management=True, seed="two") as story:
        prefs = notifications.get_preferences()
        prefs["defaults"] = {"mode": "immediate", "endpoint_ids": [endpoint["endpoint_id"]]}
        with pytest.raises(PermissionError):
            notifications.set_preferences(prefs, expected_revision=0)
        with pytest.raises(notifications.NotificationUnavailable, match="Preview"):
            notifications.emit_to_source(**event(story.now))
