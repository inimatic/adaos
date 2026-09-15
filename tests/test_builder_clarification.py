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
               "implementation_brief": "Implement the accepted prototype.",
               "turns": [{"iteration": 1, "text": "Correct deletion without changing the accepted layout."}],
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
    assert session["turns"][0]["text"] in continued["text"]
    record = service.complete(session, interaction_id=complete["interaction_id"], expected_generation=complete["generation"], continuation_task_id="task.2")
    assert record["completed_at"] and record["status"] == "completed"
    assert service.complete(session, interaction_id=complete["interaction_id"], expected_generation=complete["generation"], continuation_task_id="task.2") == record


def _answer_batch(service, session, key="answer"):
    first = service.project(session)
    answered = service.answer(session, interaction_id=first["interaction_id"], expected_generation=first["generation"],
        answers={item["id"]: "Explicit decision: " + item["id"] for item in first["questions"]}, idempotency_key=key)
    return dict(interaction_id=first["interaction_id"], expected_generation=answered["generation"], confirmed=True)


def test_repeated_clarification_keeps_paused_correction_and_prior_decisions_flat(batch):
    service, session = batch
    first = service.resume_input(session, **_answer_batch(service, session))
    source = deepcopy(session)
    source.update(iteration=2, current_task_id="task.2",
                  clarification_continuation={key: value for key, value in first.items() if key != "text"})
    source["turns"].append({"iteration": 2, "text": first["text"]})
    source["last_failure"]["details"]["clarification_questions"] = [
        {"id": "audit", "question": "Which audit trail?", "reason": "Required destination."}]
    second = BuilderClarificationService(service.state_dir).resume_input(source, **_answer_batch(service, source))
    assert second["text"].count(session["turns"][0]["text"]) == 1
    assert second["text"].count("## Resolved clarification decisions") == 1
    assert "Explicit decision: scope" in second["text"] and "Explicit decision: audit" in second["text"]
    assert [item["source_run_id"] for item in second["continuation_context"]["resolutions"]] == ["task.1", "task.2"]
    assert len(first["continuation_context"]["resolutions"]) == 1


def test_initial_clarification_retains_original_instruction(batch):
    service, session = batch
    session.update(iteration=0, turns=[])
    continued = service.resume_input(session, **_answer_batch(service, session))
    assert continued["continuation_context"]["paused_instruction"] == session["implementation_brief"]


@pytest.mark.parametrize("turns", [[], [{"iteration": 0, "text": "Wrong iteration"}],
    [{"iteration": 1, "text": "First"}, {"iteration": 1, "text": "Ambiguous"}]])
def test_missing_paused_correction_cannot_silently_resume_initial_brief(batch, turns):
    service, session = batch
    session["turns"] = turns
    with pytest.raises(BuilderClarificationError, match="exact paused"):
        service.resume_input(session, **_answer_batch(service, session))


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
    projected = automation.project_session(session)
    assert projected["status"] == "awaiting_input"
    assert projected["phase"] == "clarification"
    assert projected["run_status"] == "failed" and projected["run_terminal"]
    assert not projected["terminal"] and not projected["busy"] and not projected["can_submit"]
    assert projected["error"] is None
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


def test_canonical_change_switch_rejects_stale_question_form(batch, tmp_path, monkeypatch):
    from test_builder_automation import _service

    questions, session = batch
    automation = _service(tmp_path)
    first = questions.project(session)
    monkeypatch.setattr(type(automation), "get_session", lambda *_args: session)
    monkeypatch.setattr(type(automation), "refresh_session", lambda _self, value: value)
    monkeypatch.setattr(type(automation), "_workflow", lambda _self: SimpleNamespace(describe=lambda *_args: {
        "change_set": {"change_set_id": "successor", "status": "in_progress"}}))
    assert not automation.clarification_state(object_type="scenario", object_id="sample")["pending"]
    with pytest.raises(ValueError, match="Change"):
        automation.answer_clarification(object_type="scenario", object_id="sample", interaction_id=first["interaction_id"],
            expected_generation=first["generation"], answers={"scope": "Owner"}, idempotency_key="late")
    with pytest.raises(ValueError, match="Change"):
        automation.resume_clarification(object_type="scenario", object_id="sample", interaction_id=first["interaction_id"],
            expected_generation=first["generation"], confirmed=True)


def test_worker_repeated_questions_resume_exact_correction_not_initial_brief(batch, tmp_path):
    from test_builder_automation import _service
    from adaos.domain.automation_outcome import outcome_message
    from adaos.services.skill_factory_worker import CodexRunResult

    _service_fixture, fixture = batch
    questions = fixture["last_failure"]["details"]["clarification_questions"]
    automation = _service(tmp_path)
    create_worker = automation.worker_factory
    prompts = []

    def worker_factory():
        worker = create_worker()
        executor = worker.executor

        def execute(**kwargs):
            prompts.append(kwargs["prompt"])
            if len(prompts) in {2, 3}:
                question = questions[len(prompts) - 2]
                return CodexRunResult(returncode=0, final_message=outcome_message(json.dumps({
                    "status": "needs_input", "report": "A required scoped decision is missing.",
                    "questions": [{**question, "options": []}],
                })))
            return executor(**kwargs)

        worker.executor = execute
        return worker

    automation.worker_factory = worker_factory
    automation.start_from_execute(object_type="scenario", object_id="recipes", implementation_brief="Implement the accepted interface.")
    correction = "Preserve the existing identifiers and fix rejected deletion; do not redesign."
    automation.submit_turn(text=correction, object_type="scenario", object_id="recipes")
    for question, answer in (("scope", "Local owner"), ("retention", "30 days")):
        state = automation.clarification_state(object_type="scenario", object_id="recipes")
        assert state["pending"] and state["questions"][0]["id"] == question
        answered = automation.answer_clarification(object_type="scenario", object_id="recipes",
            interaction_id=state["interaction_id"], expected_generation=state["generation"],
            answers={question: answer}, idempotency_key=question)["clarification"]
        automation.resume_clarification(object_type="scenario", object_id="recipes",
            interaction_id=state["interaction_id"], expected_generation=answered["generation"], confirmed=True)
    assert len(prompts) == 4
    assert all(correction in prompt for prompt in prompts[1:])
    assert "Local owner" in prompts[2] and "Local owner" in prompts[3] and "30 days" in prompts[3]
    session = automation.get_session("scenario", "recipes")
    assert session["status"] == "completed"
    context = session["clarification_continuation"]["continuation_context"]
    assert context["paused_instruction"] == correction and len(context["resolutions"]) == 2
