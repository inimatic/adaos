"""Execute the public SDK-only example across source/Core crash boundaries."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from adaos.sdk import notifications
from adaos.sdk.data import relational
from test_sdk_notifications import sdk_notification_scope


def test_transactional_example_recovers_both_sides_of_admission_without_reexecuting_source(sdk_notification_scope, monkeypatch):
    ctx, package, _ = sdk_notification_scope
    example = Path(__file__).resolve().parents[1] / "examples/notification-skill"
    manifest = yaml.safe_load((package / "skill.yaml").read_text(encoding="utf8"))
    manifest["capabilities"].append("storage.relational")
    (package / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf8")
    (package / "notifications/manifest.yaml").write_text((example / "notifications/manifest.json").read_text(encoding="utf8"), encoding="utf8")
    spec = importlib.util.spec_from_file_location("notification_source_example", example / "handlers/main.py")
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    source.initialize()
    source.begin_task("task1", "Document")
    db = relational.database()
    # Failure after updating source but before its outbox row rolls back both.
    db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON notification_outbox BEGIN SELECT RAISE(ABORT, 'simulated_source_commit_fault'); END")
    with pytest.raises(Exception, match="simulated_source_commit_fault"):
        source.complete_task("task1", title="Completed", expected_revision=0)
    assert db.fetch_one("SELECT status,revision FROM tasks WHERE id='task1'") == {"status": "running", "revision": 0}
    assert notifications.list_notifications()["items"] == []
    db.execute("DROP TRIGGER reject_outbox")
    source.complete_task("task1", title="Completed", expected_revision=0)
    stable = db.fetch_one("SELECT request_json FROM notification_outbox")["request_json"]
    original = notifications.emit_captured
    def unavailable(*args, **kwargs):
        raise notifications.NotificationUnavailable("simulated_Core_offline")
    monkeypatch.setattr(notifications, "emit_captured", unavailable)
    with pytest.raises(notifications.NotificationUnavailable):
        source.reconcile_notifications()
    assert db.fetch_one("SELECT status,revision FROM tasks WHERE id='task1'") == {"status": "completed", "revision": 1}
    assert db.fetch_one("SELECT receipt_json FROM notification_outbox")["receipt_json"] is None
    def lost_acceptance(*args, **kwargs):
        original(*args, **kwargs)
        raise notifications.NotificationUnavailable("simulated_lost_acceptance")
    monkeypatch.setattr(notifications, "emit_captured", lost_acceptance)
    with pytest.raises(notifications.NotificationUnavailable):
        source.reconcile_notifications()
    assert len(notifications.list_notifications()["items"]) == 1
    assert db.fetch_one("SELECT receipt_json FROM notification_outbox")["receipt_json"] is None
    monkeypatch.setattr(notifications, "emit_captured", original)
    assert source.reconcile_notifications() == {"admitted": 1}
    assert source.reconcile_notifications() == {"admitted": 0}
    outbox = db.fetch_one("SELECT * FROM notification_outbox")
    assert outbox["request_json"] == stable
    assert json.loads(outbox["receipt_json"])["status"] == "committed"
    page = notifications.list_notifications()["items"]
    assert len(page) == 1 and page[0]["content_revision"] == 1
    assert db.fetch_one("SELECT status,revision FROM tasks WHERE id='task1'") == {"status": "completed", "revision": 1}
