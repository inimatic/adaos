from __future__ import annotations

import copy

import pytest

from adaos.sdk.builder.prototype import validate_workflow_slice
from adaos.services.builder.workflow import BuilderWorkflowError
from adaos.services.governed_workflow import workflow_definition_digest


INPUT_SCHEMA = {
    "type": "object",
    "properties": {"confirmed": {"type": "boolean"}},
    "additionalProperties": False,
}


def _transition(identifier: str, source: str, target: str, command: str, activity: str) -> dict:
    return {
        "schema": "adaos.workflow.transition.v1",
        "transition_id": identifier,
        "source": source,
        "target": target,
        "trigger": {"kind": "command", "command": command, "input_schema": INPUT_SCHEMA},
        "context": {"target_resolution": "instance", "command_context_required": False},
        "authority": {"actors": ["user"], "permissions": []},
        "guards": [{"id": "always", "params": {}, "reason_code": "blocked"}],
        "concurrency": {"conflict_scope": "prototype", "requires_generation": True, "idempotency": "required"},
        "risk": {"class": "local_reversible", "side_effect": "reversible", "confirmation": "none"},
        "effect": {"activity": activity, "transaction": "outbox", "retry": "bounded", "compensation": None},
        "recovery": {"timeout_seconds": 30, "heartbeat_seconds": 5, "cancellation": "cooperative", "reconciliation": "required_on_unknown"},
        "outcomes": {"success": "target", "failure": "source", "input_required": "source", "cancelled": "source", "unknown": "source"},
        "evidence": {"required": False, "minimum": 0},
        "approval": {"required": False, "policy_refs": []},
        "async_reply": {"mode": "terminal", "reply_route": "origin"},
        "capability_requirements": {"required": [], "optional": ["buttons"], "fallback": "numbered_text"},
        "explanations": {"allowed": f"{command} available", "rejected": f"{command} blocked", "completed": f"{command} completed"},
        "events": {"emitted": [f"prototype.{command}.completed"], "outbox": True},
        "observability": {"audit_event": f"prototype.{command}", "redaction": "policy", "metrics": [], "trace": True},
        "migration": {"introduced_in": "1.0.0", "aliases": []},
    }


def _definition() -> dict:
    start = _transition("collect", "new", "review", "start", "request.collect")
    finish = _transition("confirm", "review", "done", "confirm", "request.create")
    retry = _transition("retry_collect", "review", "review", "retry", "request.collect")
    cancel = _transition("cancel_request", "review", "cancelled", "cancel", "request.cancel")
    return {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": "prototype.request",
        "definition_version": "1.0.0",
        "aggregate_type": "prototype.request",
        "initial_state": "new",
        "states": [
            {"id": "new", "label": "New", "terminal": False},
            {"id": "review", "label": "Review", "terminal": False, "waiting": True, "wait_explanation": "Confirm request"},
            {"id": "done", "label": "Done", "terminal": True},
            {"id": "cancelled", "label": "Cancelled", "terminal": True},
        ],
        "commands": [
            {"id": "start", "input_schema": start["trigger"]["input_schema"]},
            {"id": "confirm", "input_schema": finish["trigger"]["input_schema"]},
            {"id": "retry", "input_schema": retry["trigger"]["input_schema"]},
            {"id": "cancel", "input_schema": cancel["trigger"]["input_schema"]},
        ],
        "transitions": [start, finish, retry, cancel],
        "subworkflows": [],
        "metadata": {"prototype": True},
    }


def _story(story_id: str, *, command: str, state: str, accepted: bool, reason: str | None, executor: bool | None = None) -> dict:
    start_state = "new" if command in {"start", "unknown"} else "review"
    generation = 0
    runtime = {
        "expected_generation": generation,
        "executor_available": executor,
        "retry_of_step": None,
        "concurrent_command": None,
    }
    return {
        "schema": "adaos.conversational.story.v1",
        "id": story_id,
        "story_kind": "workflow",
        "workflow_type": "prototype.request",
        "locale": "en",
        "channel": "web",
        "actor": {"id": "user:local", "permissions": [], "roles": ["registered"]},
        "start": {"instance_id": f"request:{story_id}", "state": start_state, "generation": generation, "context": {}},
        "steps": [
            {
                "user": command,
                "given": {
                    "proposal": {
                        "kind": "workflow_command", "intent_id": command, "command": command,
                        "skill_id": None, "operation_id": None, "arguments": {}, "confidence": 1.0,
                        "action_policy": {
                            "schema": "adaos.conversation.action_policy.v1", "risk_class": "local_reversible",
                            "side_effect": "reversible", "confirmation": "none"
                        }
                    },
                    "event": None, "skill_result": None, "output_ref": None, "runtime": runtime
                },
                "expect": {
                    "proposal": {"kind": "workflow_command", "command": command, "confidence_at_least": 1.0},
                    "command": command,
                    "transition_id": "collect" if accepted and command == "start" else None,
                    "state": state,
                    "reason_code": reason,
                    "accepted": accepted,
                    "output": {"kind": "accepted" if accepted else "repair", "output_ref": None, "summary": None, "actions": [], "next_expected_input": "none"}
                }
            }
        ]
    }


def _slice(source: dict) -> dict:
    definition = copy.deepcopy(source)
    stories = [
        _story("request.success", command="start", state="review", accepted=True, reason=None),
        _story("request.failure", command="start", state="new", accepted=False, reason="executor_unavailable", executor=False),
        _story("request.input", command="unknown", state="new", accepted=False, reason="command_not_allowed"),
    ]
    return {
        "schema": "adaos.builder.prototype_workflow_slice.v1",
        "slice_id": "request-conversation-v1",
        "source_definition_ref": "workflow.json@1.0.0",
        "source_definition_digest": workflow_definition_digest(source),
        "source_generation": 4,
        "profile": "conversational_mvp",
        "entry_command": "start",
        "cancel_command": "cancel",
        "definition": definition,
        "activity_requirements": [
            {"activity_id": "request.collect", "input_schema": INPUT_SCHEMA, "output_schema": {"type": "object"}, "implementation_status": "prototype_only", "implementation_ref": None},
            {"activity_id": "request.create", "input_schema": INPUT_SCHEMA, "output_schema": {"type": "object"}, "implementation_status": "missing", "implementation_ref": None},
            {"activity_id": "request.cancel", "input_schema": INPUT_SCHEMA, "output_schema": {"type": "object"}, "implementation_status": "prototype_only", "implementation_ref": None},
        ],
        "retry_transition_ids": ["retry_collect"],
        "locales": {"en": {"start": "Start"}, "ru": {"start": "Начать"}},
        "stories": stories,
        "story_outcomes": {"success": "request.success", "failure": "request.failure", "input_required": "request.input"},
    }


def test_conversational_slice_is_validated_as_candidate_without_mutating_source() -> None:
    source = _definition()
    before = copy.deepcopy(source)
    report = validate_workflow_slice(_slice(source), source_definition=source)
    assert report["valid"] is True
    assert len(report["story_reports"]) == 3
    assert report["candidate_patch"]["activation"] == "automation_only"
    assert source == before


def test_conversational_slice_fails_when_source_generation_digest_is_stale() -> None:
    source = _definition()
    value = _slice(source)
    value["source_definition_digest"] = "sha256:" + "0" * 64
    with pytest.raises(BuilderWorkflowError, match="stale"):
        validate_workflow_slice(value, source_definition=source)


def test_conversational_slice_fails_for_undeclared_activity() -> None:
    source = _definition()
    value = _slice(source)
    value["activity_requirements"] = value["activity_requirements"][:1]
    with pytest.raises(BuilderWorkflowError, match="requirement is missing"):
        validate_workflow_slice(value, source_definition=source)
