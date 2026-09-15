"""Builder question batches backed by the canonical conversation interaction store."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from adaos.domain.development_feedback import normalize_clarification_questions
from adaos.services import conversation_store
from adaos.services.artifact_pipeline.storage import mutation_lock
from adaos.services.conversation_interactions import create_interaction, submit_response
from adaos.services.personalization_runtime import personalization_access_service
from adaos.services.policy.caller import current_caller


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class BuilderClarificationError(ValueError):
    pass


class BuilderClarificationService:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)

    @staticmethod
    def _binding(session):
        failure = session.get("last_failure") or {}
        if failure.get("failure_class") != "user_input_required":
            raise BuilderClarificationError("Current run has not requested user clarification")
        questions = normalize_clarification_questions((failure.get("details") or {}).get("clarification_questions"))
        binding = {"object_type": session.get("object_type"), "object_id": session.get("object_id"),
                   "session_id": session.get("session_id"), "run_id": session.get("current_task_id"),
                   "change_id": session.get("canonical_change_id") or session.get("change_set_id"),
                   "iteration": session.get("iteration"), "questions_digest": _digest(questions),
                   "prototype_acceptance_digest": _digest(session.get("prototype_acceptance"))}
        if any(not binding[key] for key in ("object_type", "object_id", "session_id", "run_id", "change_id")):
            raise BuilderClarificationError("Clarification requires exact Project, Change, session and run identities")
        return binding, questions

    def _lock(self, interaction_id):
        return mutation_lock(self.state_dir / "builder/clarifications" / (_digest(interaction_id) + ".lock"))

    def ensure(self, session):
        binding, questions = self._binding(session)
        identifier = "interaction.builder_clarification." + _digest(binding)
        owner = personalization_access_service().owner.ref()
        with self._lock(identifier):
            existing = conversation_store.get_interaction(identifier)
            if existing:
                if existing["metadata"].get("binding") != binding or existing["owner"] != owner:
                    raise BuilderClarificationError("Retained clarification owner or binding changed")
                return existing
            # Full questions live in the structured payload, without text truncation.
            prompt = "Builder needs your decisions before continuing this run."
            return create_interaction(interaction_id=identifier, owner=owner,
                conversation_id=session.get("conversation_id") or f"builder:{binding['object_type']}:{binding['object_id']}",
                thread_id=session.get("topic_id"), prompt=prompt,
                task_ref={"kind": "builder_task", "id": binding["run_id"]},
                input_spec={"kind": "form", "required_fields": [item["id"] for item in questions], "choices": [], "sensitive": False},
                actions=[], fallbacks=("plain_text", "unsupported"),
                metadata={"domain": "builder.clarification", "binding": binding, "questions": questions})

    def _current(self, session, identifier, generation=None, *, require_actor=False):
        binding, _questions = self._binding(session)
        record = conversation_store.get_interaction(identifier)
        if not record or (record.get("metadata") or {}).get("binding") != binding:
            raise BuilderClarificationError("Clarification is stale or belongs to another Project/Change/run")
        if generation is not None and (type(generation) is not int or record["generation"] != generation):
            raise BuilderClarificationError("Clarification changed; reopen the current questions")
        caller = current_caller()
        if require_actor and (caller is None or caller.ref() != record["owner"]):
            raise PermissionError("Clarification requires its verified owner")
        return record

    @staticmethod
    def _latest(record):
        reference = record["metadata"].get("latest_response_id")
        return conversation_store.get_interaction_response(reference) if reference else None

    def project(self, session, identifier=None):
        record = self._current(session, identifier) if identifier else self.ensure(session)
        response = self._latest(record)
        values = (response or {}).get("values") or {}
        questions = record["metadata"]["questions"]
        return {"interaction_id": record["interaction_id"], "generation": record["generation"],
                "status": record["status"], "binding": deepcopy(record["metadata"]["binding"]),
                "questions": [{**question, "title": question["question"], "answer": values.get(question["id"], "")}
                              for question in questions],
                "can_resume": record["status"] == "answered" and bool(response) and response["status"] == "answered",
                "response_id": (response or {}).get("response_id")}

    def answer(self, session, *, interaction_id, expected_generation, answers, idempotency_key):
        with self._lock(interaction_id):
            record = self._current(session, interaction_id, require_actor=True)
            request_digest = _digest({"answers": answers, "generation": expected_generation})
            previous = conversation_store.get_interaction_response_by_idempotency(interaction_id, idempotency_key)
            if previous:
                if previous.get("metadata", {}).get("builder_answer_digest") != request_digest:
                    raise BuilderClarificationError("Clarification answer idempotency conflict")
                return self.project(session, interaction_id)
            self._current(session, interaction_id, expected_generation, require_actor=True)
            allowed = {item["id"] for item in record["metadata"]["questions"]}
            if (not isinstance(answers, dict) or set(answers) - allowed
                    or any(not isinstance(value, str) or len(value) > 8000 for value in answers.values())):
                raise BuilderClarificationError("Answer only the current questions with bounded text")
            latest = self._latest(record)
            values = {**((latest or {}).get("values") or {}), **{key: value.strip() for key, value in answers.items()}}
            if sum(len(value) for value in values.values()) > 32000:
                raise BuilderClarificationError("Question batch answers exceed 32000 characters")
            submit_response(interaction_id, actor_id=record["owner"], expected_generation=expected_generation,
                idempotency_key=idempotency_key, values=values,
                metadata={"builder_answer_digest": request_digest},
                supersedes_response_id=latest["response_id"] if latest and record["status"] == "answered" else None)
            return self.project(session, interaction_id)

    def resume_input(self, session, *, interaction_id, expected_generation, confirmed):
        record = self._current(session, interaction_id, expected_generation, require_actor=True)
        response = self._latest(record)
        if confirmed is not True or record["status"] != "answered" or not response or response["status"] != "answered":
            raise BuilderClarificationError("All questions and explicit continuation consent are required")
        # Preserve questions and exact user wording as task input, not inferred requirements.
        values = response["values"]
        prompt = "User clarification for the same accepted requirements. Continue the paused work; do not broaden scope.\n" + json.dumps(
            {"source_run_id": record["metadata"]["binding"]["run_id"], "response_id": response["response_id"],
             "answers": [{"question": item["question"], "answer": values[item["id"]]} for item in record["metadata"]["questions"]]},
            ensure_ascii=False, indent=2)
        return {"text": prompt, "response_id": response["response_id"], "interaction_id": interaction_id,
                "source_run_id": record["metadata"]["binding"]["run_id"]}

    def complete(self, session, *, interaction_id, expected_generation, continuation_task_id):
        with self._lock(interaction_id):
            record = self._current(session, interaction_id, require_actor=True)
            if not isinstance(continuation_task_id, str) or not continuation_task_id.strip():
                raise BuilderClarificationError("Continuation task identity is required")
            if record["status"] == "completed" and record["metadata"].get("continuation_task_id") == continuation_task_id:
                return record
            self._current(session, interaction_id, expected_generation, require_actor=True)
            if record["status"] != "answered":
                raise BuilderClarificationError("Only an answered clarification can be continued")
            record["status"] = "completed"
            record["generation"] += 1
            record["updated_at"] = record["completed_at"] = datetime.now(timezone.utc).isoformat()
            record["metadata"]["continuation_task_id"] = continuation_task_id
            return conversation_store.save_interaction(record, expected_generation=expected_generation)
