import importlib.util
import json
from pathlib import Path
import time

import pytest
import yaml

from adaos.sdk import notifications, workflow
from test_sdk_notifications import sdk_notification_scope


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/notification-decision"


def test_linked_decision_example_read_is_not_approval_and_exact_response_executes(sdk_notification_scope):
    _, root, _ = sdk_notification_scope
    declaration = json.loads((EXAMPLE / "notifications.json").read_text(encoding="utf8"))
    (root / "notifications/manifest.yaml").write_text(yaml.safe_dump(declaration), encoding="utf8")
    specification = importlib.util.spec_from_file_location("notification_decision_example", EXAMPLE / "decision.py")
    example = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(example)
    result = example.ask(task_id="test1", title="Approve the report", conversation_id="notify-decision-test", occurred_at=time.time())
    receipt = result["notification"]
    record = notifications.get(receipt["notification_id"])
    assert record["actions"] == [{"kind": "interaction", "target": result["interaction"]["interaction_id"]}]
    notifications.mark_read(record["notification_id"], content_revision=record["content_revision"],
        expected_revision=record["recipient_revision"], operation_key="read-does-not-approve")
    assert workflow.ensure_instance(example.DEFINITION, "decision-example:test1")["state"] == "draft"
    presentation = result["presentation"]["presentation"]
    answer = dict(task_id="test1", interaction_id=result["interaction"]["interaction_id"],
        generation=result["interaction"]["generation"], action_token=presentation["actions"][0]["token"],
        presentation_id=presentation["presentation_id"], operation_key="approve-report")
    completed = example.answer(**answer)
    assert completed["outcome"]["status"] == "succeeded"
    assert workflow.ensure_instance(example.DEFINITION, "decision-example:test1")["state"] == "done"
    assert notifications.list_notifications(view="needs_action")["items"] == []
