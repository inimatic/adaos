"""A successor iteration must deliver to its ingress, preserving the old host."""
import copy

import pytest

from adaos.services.builder.automation import BuilderAutomationService
from test_builder_automation import _service


def test_followup_captures_new_host_before_submitting_and_keeps_predecessor(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.start_from_execute(object_type="scenario", object_id="recipes",
        implementation_brief="Implement recipe search.", webspace_id="beta-builder",
        conversation_id="conv.beta")
    previous = copy.deepcopy(service.get_session("scenario", "recipes"))
    captured = []
    capture = BuilderAutomationService._capture_preview_binding

    def observe(self, session):
        captured.append((session["webspace_id"], session["conversation_id"]))
        return capture(self, session)

    monkeypatch.setattr(BuilderAutomationService, "_capture_preview_binding", observe)
    service.submit_turn(text="Implement an isolated alpha assembly editor.",
        object_type="scenario", object_id="recipes", webspace_id="alpha-builder",
        conversation_id="conv.alpha", expected_session_id=previous["session_id"],
        expected_iteration=previous["iteration"])
    current = service.get_session("scenario", "recipes")
    assert captured == [("alpha-builder", "conv.alpha")]
    assert current["webspace_id"] == "alpha-builder"
    assert current["conversation_id"] == "conv.alpha"
    assert current["ingress_history"][-1]["webspace_id"] == "beta-builder"
    assert current["ingress_history"][-1]["task_id"] == previous["current_task_id"]
    assert current["current_task_id"] != previous["current_task_id"]


@pytest.mark.parametrize("mode", ["note", "stale", "implicit", "same"])
def test_non_mutating_or_same_host_turn_does_not_transfer_delivery(tmp_path, mode):
    service = _service(tmp_path)
    service.start_from_execute(object_type="scenario", object_id="recipes",
        implementation_brief="Implement recipe search.", webspace_id="beta-builder")
    previous = copy.deepcopy(service.get_session("scenario", "recipes"))
    arguments = dict(object_type="scenario", object_id="recipes", webspace_id="alpha-builder")
    if mode == "note":
        service.submit_turn(text="/note Keep current beta available.", **arguments)
    elif mode == "stale":
        with pytest.raises(ValueError, match="expected session iteration"):
            service.submit_turn(text="Implement alpha.", expected_session_id=previous["session_id"],
                expected_iteration=previous["iteration"] + 1, **arguments)
    else:
        arguments["webspace_id"] = None if mode == "implicit" else "beta-builder"
        service.submit_turn(text="Add a title.", **arguments)
    current = service.get_session("scenario", "recipes")
    assert current["webspace_id"] == "beta-builder"
    assert not current.get("ingress_history")
