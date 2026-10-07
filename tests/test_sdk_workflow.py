from __future__ import annotations

import copy
from pathlib import Path

import pytest

from adaos.sdk import chat as sdk_chat
from adaos.sdk import workflow as sdk_workflow
from adaos.services.eventbus import LocalEventBus


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
        expires_at="2099-01-01T00:00:00+00:00",
        action_labels={"complete": "Complete safely"},
    )
    assert interaction["expires_at"] == "2099-01-01T00:00:00+00:00"
    assert interaction["actions"][0]["label"] == "Complete safely"
    assert interaction["actions"][0]["semantics"]["preset"] == "custom"
    assert interaction["actions"][0]["semantics"]["operation"] == "complete"
    presented = sdk_chat.request(
        interaction,
        conversation_id="conversation:sdk-workflow",
        owner="skill:test",
        webspace_id="desktop",
        bus=LocalEventBus(),
    )
    presentation = presented["presentation"]
    action = presentation["actions"][0]
    answered = sdk_chat.respond(
        interaction["interaction_id"],
        actor_id="user:local",
        expected_generation=interaction["generation"],
        idempotency_key="response:sdk-workflow:complete",
        action_token=action["token"],
        presentation_id=presentation["presentation_id"],
    )
    response = answered["response"]
    result = sdk_workflow.invoke_interaction_response(
        definition,
        "sdk-workflow:1",
        response,
        actor_id="user:local",
    )

    assert result["accepted"] is True
    assert result["dispatch"]["status"] == "succeeded"
    outcome = sdk_workflow.get_interaction_outcome(
        response["response_id"], actor_id="user:local"
    )
    assert outcome["terminal"] is True
    assert outcome["status"] == "succeeded"
    assert outcome["dispatch_id"] == result["dispatch"]["dispatch_id"]
    with pytest.raises(ValueError, match="principal is denied"):
        sdk_workflow.get_interaction_outcome(
            response["response_id"], actor_id="user:other"
        )
    replay = sdk_workflow.invoke_interaction_response(
        definition,
        "sdk-workflow:1",
        response,
        actor_id="user:local",
    )
    assert replay["reconciled"] is True
    assert replay["dispatch"]["status"] == "succeeded"
    assert sdk_workflow.ensure_instance(definition, "sdk-workflow:1")["state"] == "done"


def test_sdk_human_decision_expiry_and_preview_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADAOS_STATE_DIR", str(tmp_path / "state"))
    expired = sdk_chat.request(
        "Continue?",
        conversation_id="conversation:sdk-expired",
        owner="skill:test",
        expires_at="2000-01-01T00:00:00+00:00",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [{"value": "continue", "label": "Continue"}],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "continue",
                "label": "Continue",
                "command": "test.continue",
                "value": "continue",
                "risk": "read",
                "confirmation_required": False,
            }
        ],
        bus=LocalEventBus(),
    )
    action = expired["presentation"]["actions"][0]
    with pytest.raises(ValueError, match="expired"):
        sdk_chat.respond(
            expired["interaction"]["interaction_id"],
            actor_id="user:local",
            expected_generation=expired["interaction"]["generation"],
            idempotency_key="response:sdk-expired",
            action_token=action["token"],
            presentation_id=expired["presentation"]["presentation_id"],
        )

    with pytest.raises(ValueError, match="preview action must be read-only"):
        sdk_chat.request(
            "Preview a destructive operation",
            conversation_id="conversation:sdk-false-preview",
            owner="skill:test",
            actions=[
                {
                    "action_id": "apply",
                    "label": "Preview",
                    "command": "test.apply",
                    "value": "apply",
                    "risk": "destructive",
                    "confirmation_required": True,
                    "preset": "preview",
                }
            ],
            bus=LocalEventBus(),
        )


def test_sdk_workflow_refuses_to_present_without_required_executor(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADAOS_STATE_DIR", str(tmp_path / "state"))
    definition = _definition()
    definition["transitions"][0]["effect"]["activity"] = "builder.codex.run"
    sdk_workflow.ensure_instance(definition, "sdk-workflow:no-executor")

    with pytest.raises(ValueError, match="executor_unavailable"):
        sdk_workflow.create_interaction(
            definition,
            "sdk-workflow:no-executor",
            actor_id="user:local",
            conversation_id="conversation:sdk-no-executor",
            owner="skill:test",
            command_context_id="test:sdk-no-executor",
        )


def test_sdk_refusal_is_durable_without_external_executor(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADAOS_STATE_DIR", str(tmp_path / "state"))
    definition = _definition()
    definition["states"].append(
        {"id": "refused", "label": "Refused", "terminal": True}
    )
    definition["commands"].append(
        {
            "id": "refuse",
            "input_schema": {"type": "object", "additionalProperties": False},
        }
    )
    refusal = copy.deepcopy(definition["transitions"][0])
    refusal["transition_id"] = "refuse"
    refusal["trigger"]["command"] = "refuse"
    refusal["target"] = "refused"
    refusal["risk"] = {
        "class": "read",
        "side_effect": "none",
        "confirmation": "none",
    }
    refusal["events"]["emitted"] = ["test.refused"]
    definition["transitions"].append(refusal)
    sdk_workflow.ensure_instance(definition, "sdk-workflow:refuse")
    interaction = sdk_workflow.create_interaction(
        definition,
        "sdk-workflow:refuse",
        actor_id="user:local",
        conversation_id="conversation:sdk-refuse",
        owner="skill:test",
        command_context_id="test:sdk-refuse",
        action_semantics={"refuse": {"preset": "refuse"}},
    )
    presented = sdk_chat.request(
        interaction,
        conversation_id="conversation:sdk-refuse",
        owner="skill:test",
        bus=LocalEventBus(),
    )
    action = next(
        item for item in presented["presentation"]["actions"]
        if item["command"] == "refuse"
    )
    answered = sdk_chat.respond(
        interaction["interaction_id"],
        actor_id="user:local",
        expected_generation=interaction["generation"],
        idempotency_key="response:sdk-refuse",
        action_token=action["token"],
        presentation_id=presented["presentation"]["presentation_id"],
    )
    result = sdk_workflow.invoke_interaction_response(
        definition,
        "sdk-workflow:refuse",
        answered["response"],
        actor_id="user:local",
    )

    assert result["accepted"] is True
    assert result["dispatch"]["status"] == "succeeded"
    assert result["commit"]["activity_attempt_id"] is None
    assert sdk_workflow.ensure_instance(definition, "sdk-workflow:refuse")["state"] == "refused"
