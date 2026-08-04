from __future__ import annotations

import copy

from adaos.services.conversational_runtime import (
    build_workflow_intent_proposal,
    conversation_output_from_workflow_execution,
    link_conversation_output_to_response_envelope,
    response_envelope_from_conversation_output,
    workflow_invocation_from_intent_proposal,
)
from adaos.services.governed_workflow import (
    WorkflowResolver,
    compile_definition,
    new_instance,
    validate_workflow_record,
    workflow_definition_digest,
    workflow_ref,
)
from adaos.services.workflow_trace_identity import (
    WORKFLOW_TRACE_IDENTITY_SCHEMA,
    workflow_trace_identity_report,
)


def _definition() -> dict[str, object]:
    input_schema = {
        "type": "object",
        "properties": {"confirmed": {"type": "boolean"}},
        "additionalProperties": False,
    }
    return {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": "trace.change",
        "definition_version": "1.0.0",
        "aggregate_type": "trace.change",
        "initial_state": "draft",
        "states": [
            {"id": "draft", "label": "Draft", "terminal": False},
            {"id": "accepted", "label": "Accepted", "terminal": True},
        ],
        "commands": [{"id": "accept", "input_schema": input_schema}],
        "transitions": [
            {
                "schema": "adaos.workflow.transition.v1",
                "transition_id": "accept_draft",
                "source": "draft",
                "target": "accepted",
                "trigger": {
                    "kind": "command",
                    "command": "accept",
                    "input_schema": input_schema,
                },
                "context": {
                    "target_resolution": "instance",
                    "command_context_required": False,
                },
                "authority": {"actors": ["user"], "permissions": ["trace.change"]},
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
                    "activity": "trace.accept",
                    "transaction": "outbox",
                    "retry": "bounded",
                    "compensation": "trace.undo_accept",
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
                "async_reply": {
                    "mode": "progress_and_terminal",
                    "reply_route": "origin",
                },
                "capability_requirements": {
                    "required": [],
                    "optional": ["buttons"],
                    "fallback": "numbered_text",
                },
                "explanations": {
                    "allowed": "Accept is available",
                    "rejected": "Accept is blocked",
                    "completed": "accepted",
                },
                "events": {"emitted": ["trace.accepted"], "outbox": True},
                "observability": {
                    "audit_event": "trace.accept.audit",
                    "redaction": "policy",
                    "metrics": ["workflow_transition_total"],
                    "trace": True,
                },
                "migration": {"introduced_in": "1.0.0", "aliases": []},
            }
        ],
        "subworkflows": [],
        "metadata": {"test": "trace_identity"},
    }


def _trace_parts() -> dict[str, object]:
    compiled = compile_definition(_definition())
    instance = new_instance(compiled, "change:trace")
    instance_ref = workflow_ref(
        "workflow",
        instance["instance_id"],
        version=compiled.definition_version,
        generation=instance["generation"],
        digest=workflow_definition_digest(compiled),
    )
    route_ref = workflow_ref("reply_route", "route:trace:web")
    proposal = build_workflow_intent_proposal(
        conversation_id="conversation:trace",
        source_message_id="message:trace",
        source_text="accept",
        workflow_type=compiled.workflow_type,
        command_id="accept",
        instance_ref=instance_ref,
        input_value={"confirmed": True},
        reply_route_ref=route_ref,
        risk="isolated_write",
        now="2026-01-01T00:00:00+00:00",
    )
    invocation = workflow_invocation_from_intent_proposal(
        proposal,
        actor_id="user:local",
        idempotency_key="intent:trace:accept",
    )
    decision = WorkflowResolver().apply(
        compiled,
        instance,
        invocation["command"]["command_id"],
        input_value=invocation["command"]["input"],
        actor="user:local",
        permissions=("trace.change",),
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
        turn_trace_id="turn:trace",
        now="2026-01-01T00:02:00+00:00",
    )
    envelope = response_envelope_from_conversation_output(
        output,
        envelope_id="response:trace",
        sequence=1,
        reply_route_ids=["route:trace:web"],
        now="2026-01-01T00:03:00+00:00",
    )
    delivery_attempt = {
        "schema": "adaos.conversation.delivery_attempt.v1",
        "attempt_id": "delivery:trace",
        "envelope_id": envelope["envelope_id"],
        "route_id": "route:trace:web",
        "presentation_id": None,
        "transport": "web",
        "idempotency_key": "deliver:trace",
        "attempt_number": 1,
        "status": "claimed",
        "error": None,
        "receipt": None,
        "claimed_at": "2026-01-01T00:04:00+00:00",
        "completed_at": None,
    }
    linked_output = link_conversation_output_to_response_envelope(output, envelope)
    return {
        "proposal": proposal,
        "invocation": invocation,
        "result": result,
        "output": linked_output,
        "envelope": envelope,
        "delivery_attempt": delivery_attempt,
    }


def test_trace_identity_report_links_intent_workflow_output_envelope_and_delivery() -> None:
    parts = _trace_parts()

    report = workflow_trace_identity_report(
        turn_trace_id="turn:trace",
        intent_proposal=parts["proposal"],
        invocation=parts["invocation"],
        execution_result=parts["result"],
        conversation_output=parts["output"],
        response_envelope=parts["envelope"],
        delivery_attempt=parts["delivery_attempt"],
        now="2026-01-01T00:05:00+00:00",
    )

    assert validate_workflow_record(WORKFLOW_TRACE_IDENTITY_SCHEMA, report)["valid"] is True
    assert report["conversation_id"] == "conversation:trace"
    assert report["turn_trace_id"] == "turn:trace"
    assert report["intent_proposal_id"] == parts["proposal"]["proposal_id"]
    assert report["invocation_id"] == parts["invocation"]["invocation_id"]
    assert report["command_id"] == "accept"
    assert report["workflow_ref"]["id"] == "change:trace"
    assert report["workflow_ref"]["generation"] == 1
    assert report["workflow_event_id"] == parts["result"]["decision"]["event_records"][0]["event_id"]
    assert report["conversation_output_id"] == parts["output"]["output_id"]
    assert report["response_envelope_id"] == "response:trace"
    assert report["delivery_attempt_id"] == "delivery:trace"
    assert report["reply_route_id"] == "route:trace:web"
    assert report["diagnostics"] == []


def test_trace_identity_report_fails_when_delivery_route_breaks_identity() -> None:
    parts = _trace_parts()
    attempt = copy.deepcopy(parts["delivery_attempt"])
    attempt["route_id"] = "route:other"

    report = workflow_trace_identity_report(
        turn_trace_id="turn:trace",
        intent_proposal=parts["proposal"],
        invocation=parts["invocation"],
        execution_result=parts["result"],
        conversation_output=parts["output"],
        response_envelope=parts["envelope"],
        delivery_attempt=attempt,
        now="2026-01-01T00:05:00+00:00",
    )

    assert report["valid"] is False
    assert {item["code"] for item in report["diagnostics"]} == {
        "workflow.trace.reply_route_mismatch"
    }
