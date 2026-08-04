from __future__ import annotations

import copy

import pytest

from adaos.services.conversational_runtime import (
    ConversationalRuntimeError,
    build_conversation_output,
    build_skill_intent_proposal,
    build_workflow_intent_proposal,
    conversation_output_from_workflow_execution,
    link_conversation_output_to_response_envelope,
    response_envelope_from_conversation_output,
    skill_invocation_from_intent_proposal,
    validate_conversation_output,
    validate_intent_proposal,
    validate_response_envelope,
    validate_skill_invocation,
    workflow_invocation_from_intent_proposal,
)
from adaos.services.governed_workflow import (
    WorkflowResolver,
    compile_definition,
    new_instance,
    workflow_definition_digest,
    workflow_ref,
)


def _definition() -> dict[str, object]:
    input_schema = {
        "type": "object",
        "properties": {"confirmed": {"type": "boolean"}},
        "additionalProperties": False,
    }
    return {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": "builder.change",
        "definition_version": "1.0.0",
        "aggregate_type": "builder.change",
        "initial_state": "prototype",
        "states": [
            {"id": "prototype", "label": "Prototype", "terminal": False},
            {"id": "automation", "label": "Automation", "terminal": True},
        ],
        "commands": [{"id": "approve", "input_schema": input_schema}],
        "transitions": [
            {
                "schema": "adaos.workflow.transition.v1",
                "transition_id": "approve_prototype",
                "source": "prototype",
                "target": "automation",
                "trigger": {"kind": "command", "command": "approve", "input_schema": input_schema},
                "context": {"target_resolution": "instance", "command_context_required": False},
                "authority": {"actors": ["user"], "permissions": ["builder.change"]},
                "guards": [{"id": "always", "params": {}, "reason_code": "blocked"}],
                "concurrency": {
                    "conflict_scope": "change",
                    "requires_generation": True,
                    "idempotency": "required",
                },
                "risk": {
                    "class": "isolated_write",
                    "side_effect": "reversible",
                    "confirmation": "none",
                },
                "effect": {
                    "activity": "builder.approve",
                    "transaction": "outbox",
                    "retry": "bounded",
                    "compensation": "builder.undo_approve",
                },
                "recovery": {
                    "timeout_seconds": 900,
                    "heartbeat_seconds": 30,
                    "cancellation": "cooperative",
                    "reconciliation": "required_on_unknown",
                },
                "outcomes": {
                    "success": "target",
                    "failure": "source",
                    "input_required": "source",
                    "cancelled": "source",
                    "unknown": "source",
                },
                "evidence": {"required": False, "minimum": 0},
                "approval": {"required": False, "policy_refs": []},
                "async_reply": {"mode": "progress_and_terminal", "reply_route": "origin"},
                "capability_requirements": {
                    "required": [],
                    "optional": ["buttons"],
                    "fallback": "numbered_text",
                },
                "explanations": {
                    "allowed": "Approve is available",
                    "rejected": "Approve is blocked",
                    "completed": "approve completed",
                },
                "events": {"emitted": ["builder.approve.accepted"], "outbox": True},
                "observability": {
                    "audit_event": "builder.approve.audit",
                    "redaction": "policy",
                    "metrics": ["workflow_transition_total"],
                    "trace": True,
                },
                "migration": {"introduced_in": "1.0.0", "aliases": []},
            }
        ],
        "subworkflows": [],
        "metadata": {"test": "conversational_runtime"},
    }


def _compiled_and_instance() -> tuple[object, dict[str, object]]:
    compiled = compile_definition(_definition())
    instance = new_instance(compiled, "change:conversation-runtime")
    return compiled, instance


def test_workflow_intent_proposal_converts_to_canonical_invocation() -> None:
    compiled, instance = _compiled_and_instance()
    instance_ref = workflow_ref(
        "workflow",
        instance["instance_id"],
        version=compiled.definition_version,
        generation=instance["generation"],
        digest=workflow_definition_digest(compiled),
    )

    proposal = build_workflow_intent_proposal(
        conversation_id="conversation:builder",
        source_message_id="message:1",
        source_text="approve the prototype",
        workflow_type=compiled.workflow_type,
        command_id="approve",
        instance_ref=instance_ref,
        input_value={"confirmed": True},
        risk="isolated_write",
        now="2026-01-01T00:00:00+00:00",
    )
    invocation = workflow_invocation_from_intent_proposal(
        proposal,
        actor_id="user:local",
        idempotency_key="intent:approve",
    )

    assert validate_intent_proposal(proposal)["semantic_acts"][0]["kind"] == "workflow_command"
    assert invocation["source"] == "intent"
    assert invocation["conversation_id"] == "conversation:builder"
    assert invocation["metadata"]["intent_proposal_id"] == proposal["proposal_id"]
    assert invocation["command"]["command_id"] == "approve"
    assert invocation["command"]["instance_ref"] == instance_ref
    assert invocation["command"]["input"] == {"confirmed": True}


def test_skill_intent_proposal_is_valid_but_not_a_workflow_invocation() -> None:
    proposal = build_skill_intent_proposal(
        conversation_id="conversation:skill",
        source_message_id="message:skill",
        source_text="summarize the current draft",
        skill_id="builder",
        operation_id="summarize_draft",
        arguments={"draft_id": "draft:1"},
        now="2026-01-01T00:00:00+00:00",
    )

    act = validate_intent_proposal(proposal)["semantic_acts"][0]
    assert act["kind"] == "skill_invocation"
    assert act["skill_invocation"]["operation_id"] == "summarize_draft"
    with pytest.raises(ConversationalRuntimeError, match="workflow_command"):
        workflow_invocation_from_intent_proposal(
            proposal,
            actor_id="user:local",
            idempotency_key="intent:skill",
        )

    invocation = skill_invocation_from_intent_proposal(
        proposal,
        actor_id="user:local",
        idempotency_key="intent:skill",
    )
    assert validate_skill_invocation(invocation)["schema"] == "adaos.skill.invocation.v1"
    assert invocation["operation"] == {
        "skill_id": "builder",
        "operation_id": "summarize_draft",
    }
    assert invocation["input"] == {"draft_id": "draft:1"}
    assert invocation["proposal_ref"]["id"] == proposal["proposal_id"]


def test_workflow_execution_result_builds_semantic_output_and_response_envelope() -> None:
    compiled, instance = _compiled_and_instance()
    instance_ref = workflow_ref(
        "workflow",
        instance["instance_id"],
        version=compiled.definition_version,
        generation=instance["generation"],
        digest=workflow_definition_digest(compiled),
    )
    proposal = build_workflow_intent_proposal(
        conversation_id="conversation:exec",
        source_message_id="message:exec",
        source_text="approve",
        workflow_type=compiled.workflow_type,
        command_id="approve",
        instance_ref=instance_ref,
        input_value={"confirmed": True},
        risk="isolated_write",
        now="2026-01-01T00:00:00+00:00",
    )
    invocation = workflow_invocation_from_intent_proposal(
        proposal,
        actor_id="user:local",
        idempotency_key="intent:exec:approve",
    )
    decision = WorkflowResolver().apply(
        compiled,
        instance,
        invocation["command"]["command_id"],
        input_value=invocation["command"]["input"],
        actor="user:local",
        permissions=("builder.change",),
        expected_generation=invocation["command"]["expected_generation"],
        idempotency_key=invocation["command"]["idempotency_key"],
        now="2026-01-01T00:01:00+00:00",
    )
    result = {
        "accepted": decision["accepted"],
        "status": decision["status"],
        "reason_code": decision["reason_code"],
        "invocation": invocation,
        "decision": decision,
        "commit": None,
        "responses": [],
    }

    output = conversation_output_from_workflow_execution(
        result,
        turn_trace_id="turn:exec",
        now="2026-01-01T00:02:00+00:00",
    )
    envelope = response_envelope_from_conversation_output(
        output,
        envelope_id="response:exec:accepted",
        sequence=3,
        reply_route_ids=["route:web"],
        now="2026-01-01T00:03:00+00:00",
    )
    linked = link_conversation_output_to_response_envelope(output, envelope)

    assert validate_conversation_output(output)["kind"] == "accepted"
    assert output["summary"] == "approve completed"
    assert output["risk_level"] == "medium"
    assert output["correlation"]["intent_proposal_id"] == proposal["proposal_id"]
    assert output["correlation"]["workflow_event_id"] == decision["event_records"][0]["event_id"]
    assert output["correlation"]["workflow_ref"]["generation"] == 1
    assert output["reason"]["source"] == "workflow"
    assert output["lifecycle"]["task_status"] == "submitted"
    assert output["content_parts"][0]["kind"] == "text"
    assert validate_response_envelope(envelope)["category"] == "accepted"
    assert envelope["payload"]["data"]["semantic_output_id"] == output["output_id"]
    assert linked["response_envelope_ref"]["id"] == "response:exec:accepted"
    assert linked["response_envelope_ref"]["generation"] == 3


def test_response_envelope_bridge_maps_result_outputs_to_terminal_category() -> None:
    output = build_conversation_output(
        output_id="out:terminal",
        conversation_id="conversation:terminal",
        kind="result",
        summary="Done",
        correlation={"command_id": "approve"},
        now="2026-01-01T00:00:00+00:00",
    )

    envelope = response_envelope_from_conversation_output(
        output,
        sequence=1,
        now="2026-01-01T00:01:00+00:00",
    )

    assert envelope["category"] == "terminal"
    assert envelope["status"] == "undeliverable"
    assert envelope["terminal_key"] == "conversation-output:out:terminal"


def test_workflow_invocation_rejects_ambiguous_workflow_acts() -> None:
    compiled, instance = _compiled_and_instance()
    instance_ref = workflow_ref(
        "workflow",
        instance["instance_id"],
        version=compiled.definition_version,
        generation=instance["generation"],
    )
    proposal = build_workflow_intent_proposal(
        conversation_id="conversation:ambiguous",
        source_message_id="message:ambiguous",
        source_text="approve",
        workflow_type=compiled.workflow_type,
        command_id="approve",
        instance_ref=instance_ref,
        now="2026-01-01T00:00:00+00:00",
    )
    ambiguous = copy.deepcopy(proposal)
    ambiguous["semantic_acts"].append(copy.deepcopy(ambiguous["semantic_acts"][0]))
    ambiguous["semantic_acts"][1]["act_id"] = "act.2"

    with pytest.raises(ConversationalRuntimeError, match="exactly one workflow_command"):
        workflow_invocation_from_intent_proposal(
            ambiguous,
            actor_id="user:local",
            idempotency_key="intent:ambiguous",
        )
