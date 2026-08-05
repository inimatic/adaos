from __future__ import annotations

import copy

from adaos.services import conversation_interactions, durable_delivery, workflow_persistence
from adaos.services.builder.governed import compiled_builder_change_definition
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
    verified_workflow_principal,
)
from adaos.services.workflow_execution import (
    WorkflowExecutorRegistration,
    WorkflowExecutorRegistry,
    description_with_executor_readiness,
    execute_invocation,
    prepare_interaction_invocation,
)
from adaos.services.workflow_registry import platform_workflow_adapter_registry
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


def test_trace_spine_propagates_through_interaction_activity_and_delivery() -> None:
    turn_trace_id = "turn:builder:automation"
    trace = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": None,
        "traceparent": f"00-{'a' * 32}-{'b' * 16}-01",
    }
    definition = compiled_builder_change_definition()
    instance = new_instance(definition, "change:trace-spine")
    instance["state"] = "automation_ready"
    principal = verified_workflow_principal(
        "user:local",
        authenticated=True,
        issuer="tests.workflow_trace_identity",
    )
    adapters = platform_workflow_adapter_registry()
    contract = adapters.get("activity", "builder.codex.run")
    executors = WorkflowExecutorRegistry(
        adapters,
        (
            WorkflowExecutorRegistration(
                adapter_id="builder.codex.run",
                contract_digest=contract["contract_digest"],
                executor_id="builder.codex.worker",
            ),
        ),
    )
    description = description_with_executor_readiness(
        WorkflowResolver(require_verified_principal=True).describe(
            definition,
            instance,
            actor="user:local",
            principal=principal,
        ),
        definition,
        executors,
    )
    route = durable_delivery.create_reply_route(
        "conversation:trace-spine",
        route_id="route:trace-spine:web",
        transport="web",
        destination_ref={"webspace_id": "dev-local", "channel_id": "builder"},
        principal_scope=["user:local"],
    )
    workflow = workflow_ref(
        "workflow",
        instance["instance_id"],
        version=definition.definition_version,
        generation=instance["generation"],
        digest=workflow_definition_digest(definition),
    )
    interaction = conversation_interactions.interaction_from_workflow_description(
        description,
        conversation_id="conversation:trace-spine",
        owner="skill:builder_skill",
        interaction_id="interaction:trace-spine",
        workflow_ref=workflow,
        command_context_ref=workflow_ref("command_context", "builder:trace-spine"),
        reply_route_ref=workflow_ref("reply_route", route["route_id"]),
        turn_trace_id=turn_trace_id,
        trace=trace,
    )
    action = next(
        item for item in interaction["actions"] if item["command"] == "start_automation"
    )
    proposal = build_workflow_intent_proposal(
        conversation_id=interaction["conversation_id"],
        source_message_id="message:trace-spine",
        source_text="start automation",
        workflow_type=definition.workflow_type,
        command_id="start_automation",
        instance_ref=workflow,
        target_ref=action["target_ref"],
        interaction_id=interaction["interaction_id"],
        action_id=action["action_id"],
        reply_route_ref=workflow_ref("reply_route", route["route_id"]),
        risk="isolated_write",
        turn_trace_id=turn_trace_id,
        trace=trace,
    )
    response = conversation_interactions.submit_response(
        interaction["interaction_id"],
        actor_id="user:local",
        expected_generation=0,
        idempotency_key="trace-spine:start-automation",
        values={"confirmed": True},
        original_text="start automation",
        proposed_action_id=action["action_id"],
        intent_proposal=proposal,
        metadata={"io_type": "text"},
    )["response"]
    invocation = prepare_interaction_invocation(response)
    result = execute_invocation(
        invocation,
        definition,
        instance,
        principal=principal,
        adapters=adapters,
        executors=executors,
    )
    activity_run = workflow_persistence.claim_activity(
        result["commit"]["activity_attempt_id"]
    )
    envelope = result["responses"][0]
    output = conversation_output_from_workflow_execution(
        result,
        intent_proposal_id=proposal["proposal_id"],
        response_envelope_ref_value={
            "schema": "adaos.workflow.ref.v1",
            "kind": "response_envelope",
            "id": envelope["envelope_id"],
            "version": None,
            "generation": envelope["sequence"],
            "digest": None,
        },
    )
    attempt = durable_delivery.claim_delivery(envelope["envelope_id"], route["route_id"])

    report = workflow_trace_identity_report(
        intent_proposal=proposal,
        interaction=interaction,
        interaction_response=response,
        invocation=invocation,
        execution_result=result,
        activity_run=activity_run,
        conversation_output=output,
        response_envelope=envelope,
        delivery_attempt=attempt,
    )

    assert report["valid"] is True, report["diagnostics"]
    assert report["trace_id"] == trace["trace_id"]
    assert report["turn_trace_id"] == turn_trace_id
    assert report["activity_run_id"] == activity_run["attempt_id"]
    for record in (
        proposal,
        interaction,
        response,
        invocation,
        invocation["command"],
        result["decision"]["event_records"][0],
        activity_run,
        output,
        envelope,
        attempt,
    ):
        assert record["turn_trace_id"] == turn_trace_id
        assert record["trace"]["trace_id"] == trace["trace_id"]
