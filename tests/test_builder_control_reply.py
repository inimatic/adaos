import copy
import json

import pytest

from adaos.sdk.builder.automation import control_reply


def test_large_history_is_retained_but_control_reply_preserves_only_current_facts():
    source = {"ok": True, "status": "automation_recovery_queued", "model_started": False,
              "session": {"session_id": "automation.scenario.photo", "current_task_id": "task.retained",
                          "status": "failed", "completion_history": ["history" * 1_000_000],
                          "browser_feedback_history": ["screenshots" * 100_000],
                          "last_failure": {"stage": "browser_feedback", "failure_class": "verifier_failure",
                                           "message": "Retained candidate; verifier was corrected"}},
              "automation": {"status": "commit_ready", "task_id": "task.retained", "budget_usage": {
                  "status": "unknown", "usage_state": "partial", "observed": {"cost_usd": None, "attempts": 2}}},
              "worker": {"ok": True, "task_id": "task.retained", "reused_validated_result": True}}
    original = copy.deepcopy(source)
    reply = control_reply(source, object_type="scenario", object_id="photo")
    assert source == original
    assert len(json.dumps(reply, ensure_ascii=False).encode()) < 64 * 1024
    assert reply["task_id"] == "task.retained" and reply["model_started"] is False
    assert reply["next_action"] == "wait" and reply["worker"]["reused_validated_result"]
    assert reply["budget"]["observed"]["cost_usd"] is None
    assert reply["last_failure"]["failure_class"] == "verifier_failure"
    assert "history" not in json.dumps(reply)
    assert "details=true" in reply["evidence"]["full_state_url"]


@pytest.mark.parametrize("status,expected", [("completed", "review_result"), ("awaiting_input", "answer_questions"),
                                           ("failed", "inspect_failure"), ("in_progress", "wait")])
def test_control_does_not_infer_a_model_start_or_hide_the_required_next_action(status, expected):
    value = control_reply({"session": {"status": status}}, object_type="scenario", object_id="photo")
    assert value["next_action"] == expected and value["model_started"] is None


def test_unicode_oversized_messages_have_explicit_shortening_and_full_evidence_reference():
    source = {"ok": False, "message": "Требуется восстановление. " * 10_000,
              "session": {"status": "failed", "last_failure": {"details": {"model_rerun_required": False}}}}
    reply = control_reply(source, object_type="scenario", object_id="photo")
    assert reply["shortened_fields"] == ["result.message"]
    assert reply["next_action"] == "resume_validated"
    assert reply["evidence"]["diagnostics"]["arguments"]["page_size"] == 50


@pytest.mark.parametrize('locale,expected', [('ru','Задача принята'),('en','Task accepted')])
def test_queue_acknowledgement_is_a_localized_stage_instead_of_an_instruction_echo(locale,expected):
    source={'ok':True,'message':'Private instruction '*10000,'session':{'status':'queued'}}
    reply=control_reply(source,object_type='scenario',object_id='photo',locale=locale)
    assert reply['message'].startswith(expected)
    assert len(reply['message']) < 150 and 'Private instruction' not in reply['message']
    assert source['message']=='Private instruction '*10000
    assert reply['model_started'] is None
