from __future__ import annotations

import copy
from pathlib import Path

from adaos.sdk import workflow as sdk_workflow
from adaos.services import conversation_interactions


def _definition() -> dict[str, object]:
    source = {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": "test.sdk.workflow",
        "definition_version": "1.0.0",
        "aggregate_type": "test.sdk.workflow",
        "initial_state": "draft",
        "states": [
            {"id": "draft", "label": "Draft", "terminal": False},
            {"id": "done", "label": "Done", "terminal": True},
        ],
        "commands": [
            {
                "id": "complete",
                "input_schema": {"type": "object", "additionalProperties": False},
            }
        ],
        "transitions": [],
        "subworkflows": [],
        "metadata": {},
    }
    transition = {
        "schema": "adaos.workflow.transition.v1",
        "transition_id": "complete",
        "source": "draft",
        "target": "done",
        "trigger": {
            "kind": "command",
            "command": "complete",
            "input_schema": {"type": "object", "additionalProperties": False},
        },
        "context": {"target_resolution": "instance", "command_context_required": False},
        "authority": {"actors": ["*"], "permissions": [], "roles": ["registered"]},
        "guards": [{"id": "always", "params": {}, "reason_code": "blocked"}],
        "concurrency": {"conflict_scope": "instance", "requires_generation": True, "idempotency": "required"},
        "risk": {"class": "local_reversible", "side_effect": "reversible", "confirmation": "none"},
        "effect": {"activity": None, "transaction": "atomic", "retry": "never", "compensation": None},
        "recovery": {"timeout_seconds": None, "heartbeat_seconds": None, "cancellation": "not_applicable", "reconciliation": "not_applicable"},
        "outcomes": {"success": "done", "failure": "draft", "input_required": "draft", "cancelled": "draft", "unknown": "draft"},
        "evidence": {"required": False, "minimum": 0},
        "approval": {"required": False, "policy_refs": []},
        "async_reply": {"mode": "terminal", "reply_route": "origin"},
        "capability_requirements": {"required": [], "optional": ["buttons"], "fallback": "numbered_text"},
        "explanations": {"allowed": "Complete.", "rejected": "Blocked.", "completed": "Completed."},
        "events": {"emitted": ["test.completed"], "outbox": True},
        "observability": {"audit_event": "test.complete", "redaction": "policy", "metrics": ["test_total"], "trace": True},
        "migration": {"introduced_in": "1.0.0", "aliases": []},
    }
    source["transitions"] = [transition]
    return copy.deepcopy(source)


def test_sdk_owns_instance_interaction_and_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_STATE_DIR", str(tmp_path / "state"))
    definition = _definition()
    instance = sdk_workflow.ensure_instance(definition, "sdk-workflow:1")
    assert instance["state"] == "draft"

    interaction = sdk_workflow.create_interaction(
        definition,
        "sdk-workflow:1",
        actor_id="user:local",
        conversation_id="conversation:sdk-workflow",
        owner="skill:test",
        command_context_id="test:sdk-workflow",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )
    action = presentation["actions"][0]
    response = conversation_interactions.submit_action_token(
        action["token"],
        actor_id="user:local",
        idempotency_key="response:sdk-workflow:complete",
    )["response"]
    result = sdk_workflow.invoke_interaction_response(
        definition,
        "sdk-workflow:1",
        response,
        actor_id="user:local",
    )

    assert result["accepted"] is True
    assert sdk_workflow.ensure_instance(definition, "sdk-workflow:1")["state"] == "done"
