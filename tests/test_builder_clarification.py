from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from adaos.domain.development_feedback import normalize_clarification_questions, required_user_questions
from adaos.domain.personalization_access import SubjectRef
from adaos.services import conversation_store
from adaos.services.builder import clarification
from adaos.services.builder.clarification import BuilderClarificationError, BuilderClarificationService
from adaos.services.policy.caller import verified_caller


@pytest.fixture
def batch(tmp_path, monkeypatch):
    owner = SubjectRef("user", "owner")
    monkeypatch.setattr(clarification, "personalization_access_service", lambda: SimpleNamespace(owner=owner))
    questions = [
        {"id": "scope", "question": "Which group owns the records?", "reason": "Determines write authorization."},
        {"id": "retention", "question": "How long must deleted records be retained?", "reason": "Required before choosing a deletion policy."},
    ]
    session = {"object_type": "scenario", "object_id": "sample", "session_id": "automation.scenario.sample",
               "current_task_id": "task.1", "canonical_change_id": "change.1", "iteration": 1,
               "prototype_acceptance": {"digest": "accepted.1"},
               "last_failure": {"failure_class": "user_input_required", "details": {"clarification_questions": questions}}}
    service = BuilderClarificationService(tmp_path)
    with verified_caller(owner):
        yield service, session


def test_questions_survive_restart_partial_answers_and_explicit_continuation(batch):
    service, session = batch
    first = service.project(session)
    assert len(first["questions"]) == 2
    partial = service.answer(session, interaction_id=first["interaction_id"], expected_generation=first["generation"],
                             answers={"scope": "Local owner"}, idempotency_key="one")
    assert not partial["can_resume"]
    resumed = BuilderClarificationService(service.state_dir).project(session)
    assert resumed == partial
    with pytest.raises(BuilderClarificationError, match="All questions"):
        service.resume_input(session, interaction_id=partial["interaction_id"], expected_generation=partial["generation"], confirmed=True)
    complete = service.answer(session, interaction_id=partial["interaction_id"], expected_generation=partial["generation"],
                              answers={"retention": "30 days"}, idempotency_key="two")
    with pytest.raises(BuilderClarificationError, match="consent"):
        service.resume_input(session, interaction_id=complete["interaction_id"], expected_generation=complete["generation"], confirmed=False)
    continued = service.resume_input(session, interaction_id=complete["interaction_id"], expected_generation=complete["generation"], confirmed=True)
    assert "30 days" in continued["text"] and "Local owner" in continued["text"]
    assert continued["source_run_id"] == "task.1"
    record = service.complete(session, interaction_id=complete["interaction_id"], expected_generation=complete["generation"], continuation_task_id="task.2")
    assert record["completed_at"] and record["status"] == "completed"
    assert service.complete(session, interaction_id=complete["interaction_id"], expected_generation=complete["generation"], continuation_task_id="task.2") == record


def test_lost_answer_ack_is_idempotent_but_reused_key_cannot_change_answer(batch):
    service, session = batch
    first = service.project(session)
    request = dict(interaction_id=first["interaction_id"], expected_generation=first["generation"], answers={"scope": "Owner"}, idempotency_key="retry")
    result = service.answer(session, **request)
    assert service.answer(session, **request) == result
    with pytest.raises(BuilderClarificationError, match="idempotency"):
        service.answer(session, **{**request, "answers": {"scope": "Guests"}})
    assert len(conversation_store.list_interaction_responses(first["interaction_id"])) == 1


@pytest.mark.parametrize("field,value", [("object_id", "other"), ("current_task_id", "task.2"),
    ("canonical_change_id", "change.2"), ("prototype_acceptance", {"digest": "changed"}), ("iteration", 2)])
def test_answers_are_bound_to_exact_project_change_run_and_acceptance(batch, field, value):
    service, session = batch
    first = service.project(session)
    stale = {**session, field: value}
    with pytest.raises(BuilderClarificationError, match="stale"):
        service.answer(stale, interaction_id=first["interaction_id"], expected_generation=first["generation"], answers={"scope": "Owner"}, idempotency_key="one")


def test_answer_requires_verified_owner_and_current_generation(batch):
    service, session = batch
    first = service.project(session)
    request = dict(interaction_id=first["interaction_id"], expected_generation=first["generation"], answers={"scope": "Owner"}, idempotency_key="one")
    with verified_caller(SubjectRef("user", "guest")), pytest.raises(PermissionError):
        service.answer(session, **request)
    with pytest.raises(BuilderClarificationError, match="changed"):
        service.answer(session, **{**request, "expected_generation": first["generation"] + 1})


def test_batch_keeps_complete_question_text(batch):
    service, session = batch
    session = deepcopy(session)
    session["last_failure"]["details"]["clarification_questions"] = [
        {"id": f"question_{index}", "question": "Q" * 1000, "reason": "R" * 1000} for index in range(8)
    ]
    projected = service.project(session)
    assert len(projected["questions"]) == 8
    assert all(len(item["reason"]) == len(item["question"]) == 1000 for item in projected["questions"])


def test_mixed_platform_blocker_cannot_become_user_clarification(batch):
    _service, session = batch
    questions = session["last_failure"]["details"]["clarification_questions"]
    user = {"blocking": True, "clarification_questions": questions}
    assert required_user_questions([user]) == normalize_clarification_questions(questions)
    assert not required_user_questions([user, {"blocking": True, "category": "missing_capability"}])
    assert required_user_questions([user, {"blocking": False}])


def test_question_ids_and_unknown_fields_fail_closed():
    question = {"id": "scope", "question": "Which owner?", "reason": "Authorization"}
    with pytest.raises(ValueError, match="unique"):
        normalize_clarification_questions([question, question])
    with pytest.raises(ValueError, match="fields"):
        normalize_clarification_questions([{**question, "answer": "guessed"}])


def test_worker_questions_pause_and_resume_same_change_once(batch, tmp_path):
    from test_builder_automation import _service
    from adaos.services.skill_factory_worker import CodexRunResult

    _questions, fixture_session = batch
    questions = fixture_session["last_failure"]["details"]["clarification_questions"]
    automation = _service(tmp_path)
    create_worker = automation.worker_factory
    calls = []

    def worker_factory():
        worker = create_worker()
        executor = worker.executor

        def execute(**kwargs):
            calls.append(kwargs["prompt"])
            if len(calls) == 1:
                payload = {"schema": "adaos.development_feedback_output.v1", "items": [{
                    "category": "insufficient_context", "summary": "Two required ownership and retention decisions",
                    "blocking": True, "impact": ["correctness"], "target_refs": ["scenario:recipes"],
                    "details": "User decisions are required before implementing data deletion.",
                    "recommendation": "Answer the questions, then continue.", "clarification_questions": questions,
                }]}
                return CodexRunResult(returncode=0, final_message="```adaos-development-feedback\n" + json.dumps(payload) + "\n```")
            return executor(**kwargs)

        worker.executor = execute
        return worker

    automation.worker_factory = worker_factory
    automation.start_from_execute(object_type="scenario", object_id="recipes", implementation_brief="Implement the accepted interface.")
    state = automation.clarification_state(object_type="scenario", object_id="recipes")
    assert state["pending"] and len(state["questions"]) == 2
    session = automation.get_session("scenario", "recipes")
    assert session["last_failure"]["failure_class"] == "user_input_required"
    source_run = session["current_task_id"]
    change = session["canonical_change_id"]
    with pytest.raises(ValueError, match="clarification"):
        automation.submit_turn(text="Retry", object_type="scenario", object_id="recipes")
    with pytest.raises(ValueError, match="clarification"):
        automation.retry_failed(object_type="scenario", object_id="recipes")
    answered = automation.answer_clarification(object_type="scenario", object_id="recipes", interaction_id=state["interaction_id"],
        expected_generation=state["generation"], answers={"scope": "Local owner", "retention": "30 days"}, idempotency_key="answers")["clarification"]
    resumed = automation.resume_clarification(object_type="scenario", object_id="recipes", interaction_id=state["interaction_id"],
        expected_generation=answered["generation"], confirmed=True)
    assert resumed["ok"]
    duplicate = automation.resume_clarification(object_type="scenario", object_id="recipes", interaction_id=state["interaction_id"],
        expected_generation=answered["generation"], confirmed=True)
    assert duplicate["duplicate"]
    current = automation.get_session("scenario", "recipes")
    assert current["status"] == "completed"
    assert current["canonical_change_id"] == change
    assert current["clarification_continuation"]["source_run_id"] == source_run
    assert len(calls) == 2
    assert "30 days" in calls[1] and "Local owner" in calls[1]
