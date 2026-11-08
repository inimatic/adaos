import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.domain.personalization_access import SubjectRef
from adaos.sdk import notifications
from adaos.services.agent_context import get_ctx
from adaos.services.personalization_runtime import current_user_id
from adaos.services.policy.application import bind_application, clear_application
from adaos.services.policy.caller import verified_caller
from adaos.services.user.attention_preferences import AttentionPreferences
from test_notification_contracts import manifest


@pytest.fixture
def sdk_notification_scope(tmp_path):
    ctx = get_ctx()
    root = tmp_path / "owned"
    (root / "notifications").mkdir(parents=True)
    (root / "notifications/manifest.yaml").write_text(yaml.safe_dump(manifest(), allow_unicode=True), encoding="utf8")
    (root / "skill.yaml").write_text(yaml.safe_dump({
        "id": "reviewer", "capabilities": ["notifications.send", "notifications.manage"],
        "notifications": {"manifest": "notifications/manifest.yaml"},
    }), encoding="utf8")
    user_id = current_user_id(ctx)
    application = {
        "application_id": "reviews", "subject_ref": "user:" + user_id,
        "release_digest": "development:reviews", "runtime_source": "dev",
        "component_ref": "skill:reviewer", "application_title": "Reviewer",
        "permission_profile": {"required": [{"id": "notifications.send"}, {"id": "notifications.manage"}], "optional": []},
    }
    ctx.skill_ctx.set_loaded("reviewer", root)
    bind_application(application)
    try:
        with verified_caller(SubjectRef("user", user_id)):
            yield ctx, root, application
    finally:
        ctx.skill_ctx.clear()
        clear_application()


def values():
    import time
    return dict(category_id="review.completed", event_id="E1", source_id="R1", source_revision=1,
                occurred_at=time.time(), payload={"title": "Document"})


def test_sdk_dev_recipient_and_preferences_are_synthetic(sdk_notification_scope):
    ctx, root, application = sdk_notification_scope
    original = AttentionPreferences(ctx).read(application["subject_ref"], "subnet:local-subnet")
    receipt = notifications.emit(**values())
    page = notifications.list_notifications()
    assert len(page["items"]) == 1
    assert page["items"][0]["principal_ref"].startswith("user:preview.")
    assert page["items"][0]["decision"]["reason"] == "preview"
    prefs = notifications.get_preferences()
    prefs["defaults"]["mode"] = "off"
    saved = notifications.set_preferences(prefs, expected_revision=0)
    assert saved["revision"] == 1
    assert AttentionPreferences(ctx).read(application["subject_ref"], "subnet:local-subnet") == original
    assert notifications.endpoints(discover=True) == []


def test_send_only_component_cannot_manage_user_rules(sdk_notification_scope):
    ctx, root, app = sdk_notification_scope
    data = yaml.safe_load((root / "skill.yaml").read_text(encoding="utf8"))
    data["capabilities"] = ["notifications.send"]
    (root / "skill.yaml").write_text(yaml.safe_dump(data), encoding="utf8")
    assert notifications.emit(**values())["status"] == "committed"
    with pytest.raises(PermissionError):
        notifications.get_preferences()


def test_captured_publisher_survives_focus_change_but_not_component_substitution(sdk_notification_scope, tmp_path):
    ctx, root, app = sdk_notification_scope
    captured = notifications.capture_publisher()
    original = values()
    clear_application()
    # Background continuations intentionally have no current interactive actor.
    with verified_caller(None):
        receipt = notifications.emit_captured(captured["publisher_ref"], **original)
        assert notifications.emit_captured(captured["publisher_ref"], **original) == receipt
    other = tmp_path / "other"
    other.mkdir()
    (other / "skill.yaml").write_text(yaml.safe_dump({"capabilities": ["notifications.send"]}), encoding="utf8")
    ctx.skill_ctx.set_loaded("other", other)
    with pytest.raises(PermissionError):
        notifications.emit_captured(captured["publisher_ref"], **original)


def test_sdk_never_accepts_a_forged_user_or_missing_verified_context(sdk_notification_scope):
    _, _, app = sdk_notification_scope
    bind_application({**app, "subject_ref": "user:foreign"})
    with pytest.raises(PermissionError):
        notifications.emit(**values())
    clear_application()
    with pytest.raises(PermissionError):
        notifications.emit(**values())


def test_async_sdk_preserves_invocation_without_blocking_loop(sdk_notification_scope):
    async def run():
        with pytest.raises(RuntimeError, match="a_"):
            notifications.emit(**values())
        receipt = await notifications.a_emit(**values())
        page = await notifications.a_list_notifications()
        assert page["items"][0]["notification_id"] == receipt["notification_id"]
    asyncio.run(run())


def test_pure_compile_grants_nothing():
    with verified_caller(None):
        result = notifications.validate_declaration(manifest())
        assert result["permission_disclosure"][0]["id"] == "review.completed"
