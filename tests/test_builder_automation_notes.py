from copy import deepcopy
from types import SimpleNamespace

import pytest

from adaos.services.builder.automation import BuilderAutomationService


@pytest.mark.parametrize("status", ["completed", "failed", "in_progress", "awaiting_input"])
def test_informational_note_keeps_accepted_result_and_never_dispatches(status):
    session = {"session_id": "automation.scenario.retouch", "status": status, "iteration": 19,
               "current_task_id": "task.19", "last_execution_brief": "Retouch the original.",
               "completion_readiness": {"ok": True}, "last_failure": {"failure_class": "user_input_required"}}
    before = deepcopy(session)
    def forbidden(*args, **kwargs):
        pytest.fail("A note must not refresh, submit or mutate Automation")
    service = SimpleNamespace(get_session=lambda *_: session, refresh_session=forbidden,
                              project_session=lambda current: {"status": current["status"], "task_id": current["current_task_id"]})
    result = BuilderAutomationService.submit_turn(service, text="/note\nИнформационный итог Beta. Не запускай новую итерацию.",
                                                  object_type="scenario", object_id="retouch")
    assert result["ok"] and result["status"] == "automation_note_recorded" and result["model_calls"] == 0
    assert result["automation"]["task_id"] == "task.19" and session == before


def test_note_obeys_the_selected_session_fence():
    service = SimpleNamespace(get_session=lambda *_: {"session_id": "actual", "iteration": 19})
    with pytest.raises(ValueError, match="expected session"):
        BuilderAutomationService.submit_turn(service, text="/note Report", object_type="scenario", object_id="retouch",
                                             expected_session_id="another", expected_iteration=19)
