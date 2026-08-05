from __future__ import annotations

from adaos.services import conversation_interactions, durable_delivery, workflow_persistence
from adaos.services.builder.governed import compiled_builder_change_definition
from adaos.services.governed_workflow import (
    WorkflowResolver,
    new_instance,
    verified_workflow_principal,
    workflow_ref,
)
from adaos.services.workflow_execution import (
    WorkflowExecutorRegistration,
    WorkflowExecutorRegistry,
    cross_channel_ingress_conformance,
    description_with_executor_readiness,
    execute_invocation,
    prepare_interaction_invocation,
    prepare_sdk_invocation,
)
from adaos.services.conversational_runtime import build_workflow_intent_proposal
from adaos.services.workflow_registry import platform_workflow_adapter_registry


def _principal():
    return verified_workflow_principal(
        "user:local",
        authenticated=True,
        issuer="tests.workflow_execution",
    )


def _description(instance: dict[str, object]) -> dict[str, object]:
    return WorkflowResolver(require_verified_principal=True).describe(
        compiled_builder_change_definition(),
        instance,
        actor="user:local",
        principal=_principal(),
    )


def test_executor_readiness_blocks_valid_command_until_exact_contract_is_ready() -> None:
    definition = compiled_builder_change_definition()
    instance = new_instance(definition, "change:executor-readiness")
    instance["state"] = "automation_ready"
    adapters = platform_workflow_adapter_registry()
    executors = WorkflowExecutorRegistry(adapters)

    blocked = description_with_executor_readiness(_description(instance), definition, executors)
    assert "start_automation" not in {
        item["command"] for item in blocked["allowed_commands"]
    }
    unavailable = next(
        item for item in blocked["blocked_commands"] if item["command"] == "start_automation"
    )
    assert unavailable["reason_code"] == "executor_unavailable"

    contract = adapters.get("activity", "builder.codex.run")
    executors.register(
        WorkflowExecutorRegistration(
            adapter_id="builder.codex.run",
            contract_digest=contract["contract_digest"],
            executor_id="builder.codex.worker",
        )
    )
    ready = description_with_executor_readiness(_description(instance), definition, executors)
    command = next(
        item for item in ready["allowed_commands"] if item["command"] == "start_automation"
    )
    assert command["executor"]["executor_id"] == "builder.codex.worker"


def test_web_interaction_and_sdk_share_one_invocation_and_durable_reply_boundary() -> None:
    definition = compiled_builder_change_definition()
    instance = new_instance(definition, "change:cross-channel")
    instance["state"] = "prototype_editing"
    adapters = platform_workflow_adapter_registry()
    executors = WorkflowExecutorRegistry(adapters)
    description = description_with_executor_readiness(_description(instance), definition, executors)
    route = durable_delivery.create_reply_route(
        "conversation:cross-channel",
        route_id="route:cross-channel:web",
        transport="web",
        destination_ref={"webspace_id": "dev-local", "channel_id": "builder"},
        principal_scope=["user:local"],
    )
    interaction = conversation_interactions.interaction_from_workflow_description(
        description,
        conversation_id="conversation:cross-channel",
        owner="skill:builder_skill",
        interaction_id="interaction:cross-channel:web",
        workflow_ref=workflow_ref(
            "workflow",
            instance["instance_id"],
            version=definition.definition_version,
            generation=instance["generation"],
        ),
        command_context_ref=workflow_ref("command_context", "webspace:dev-local"),
        reply_route_ref=workflow_ref("reply_route", route["route_id"]),
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )
    action = next(
        item for item in presentation["actions"] if item["command"] == "accept_prototype"
    )
    response = conversation_interactions.submit_action_token(
        action["token"],
        actor_id="user:local",
        idempotency_key="web:accept-prototype",
        values={"confirmed": True},
        metadata={"io_type": "web"},
    )["response"]
    invocation = prepare_interaction_invocation(response)
    sdk_invocation = prepare_sdk_invocation(
        workflow_type=definition.workflow_type,
        instance_ref=workflow_ref(
            "workflow",
            instance["instance_id"],
            version=definition.definition_version,
            generation=instance["generation"],
        ),
        actor_id="user:local",
        command_id="accept_prototype",
        expected_generation=0,
        idempotency_key="sdk:accept-prototype",
        input_value={"confirmed": True},
        target_ref=invocation["target_ref"],
        context_ref=workflow_ref("command_context", "sdk:test"),
        risk="isolated_write",
        confirmation_required=True,
    )

    assert invocation["source"] == "web"
    assert invocation["command"]["command_id"] == sdk_invocation["command"]["command_id"]
    assert invocation["command"]["workflow_type"] == sdk_invocation["command"]["workflow_type"]
    assert invocation["command"]["expected_generation"] == sdk_invocation["command"]["expected_generation"]
    assert invocation["target_ref"] == sdk_invocation["target_ref"]

    result = execute_invocation(
        invocation,
        definition,
        instance,
        principal=_principal(),
        adapters=adapters,
        executors=executors,
    )
    assert result["accepted"] is True
    assert result["decision"]["after"]["state"] == "automation_ready"
    assert workflow_persistence.get_instance(instance["instance_id"])["state"] == "automation_ready"
    assert [item["category"] for item in result["responses"]] == ["accepted", "terminal"]
    assert durable_delivery.terminal_result("conversation:cross-channel")["payload"]["data"][
        "state"
    ] == "automation_ready"

    attempt = durable_delivery.claim_delivery(
        result["responses"][-1]["envelope_id"],
        route["route_id"],
        presentation_id=presentation["presentation_id"],
    )
    durable_delivery.complete_delivery(attempt["attempt_id"], delivered=False, error="offline")
    recovered = durable_delivery.recover_delivery(conversation_id="conversation:cross-channel")
    assert result["responses"][-1]["envelope_id"] in {
        item["envelope_id"] for item in recovered["resumable"]
    }
    assert workflow_persistence.get_instance(instance["instance_id"])["generation"] == 1


def _cross_channel_invocations(
    instance: dict[str, object],
) -> tuple[dict[str, dict[str, object]], object, object]:
    definition = compiled_builder_change_definition()
    adapters = platform_workflow_adapter_registry()
    contract = adapters.get("activity", "builder.codex.run")
    presentation_executors = WorkflowExecutorRegistry(
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
        _description(instance), definition, presentation_executors
    )
    workflow = workflow_ref(
        "workflow",
        instance["instance_id"],
        version=definition.definition_version,
        generation=instance["generation"],
    )
    invocations: dict[str, dict[str, object]] = {}
    selected_target = None
    for channel in ("web", "telegram", "text"):
        interaction = conversation_interactions.interaction_from_workflow_description(
            description,
            conversation_id=f"conversation:ingress:{channel}",
            owner="skill:builder_skill",
            interaction_id=f"interaction:ingress:{channel}",
            workflow_ref=workflow,
            command_context_ref=workflow_ref("command_context", "builder:ingress"),
        )
        presentation = conversation_interactions.negotiate_presentation(
            interaction,
            conversation_interactions.standard_capability_profile(channel),
        )
        action = next(
            item for item in presentation["actions"] if item["command"] == "start_automation"
        )
        selected_target = action["target_ref"]
        if channel == "text":
            proposal = build_workflow_intent_proposal(
                conversation_id=interaction["conversation_id"],
                source_message_id="message:ingress:text",
                source_text="start automation",
                workflow_type=definition.workflow_type,
                command_id="start_automation",
                instance_ref=workflow,
                target_ref=selected_target,
                interaction_id=interaction["interaction_id"],
                action_id=action["action_id"],
                risk="isolated_write",
                channel="text",
            )
            response = conversation_interactions.submit_response(
                interaction["interaction_id"],
                actor_id="user:local",
                expected_generation=0,
                idempotency_key="text:start-automation",
                original_text="start automation",
                values={"confirmed": True},
                proposed_action_id=action["action_id"],
                intent_proposal=proposal,
                metadata={"io_type": "text"},
            )["response"]
        else:
            response = conversation_interactions.submit_action_token(
                action["token"],
                actor_id="user:local",
                idempotency_key=f"{channel}:start-automation",
                values={"confirmed": True},
                metadata={"io_type": channel},
            )["response"]
        invocations[channel] = prepare_interaction_invocation(response)
    invocations["sdk"] = prepare_sdk_invocation(
        workflow_type=definition.workflow_type,
        instance_ref=workflow,
        actor_id="user:local",
        command_id="start_automation",
        expected_generation=int(instance["generation"]),
        idempotency_key="sdk:start-automation",
        input_value={"confirmed": True},
        target_ref=selected_target,
        context_ref=workflow_ref("command_context", "builder:ingress"),
        risk="isolated_write",
        confirmation_required=True,
    )
    return invocations, definition, adapters


def test_cross_channel_ingress_harness_proves_same_guard_target_and_executor_failure() -> None:
    instance = new_instance(compiled_builder_change_definition(), "change:ingress-harness")
    instance["state"] = "automation_ready"
    invocations, definition, adapters = _cross_channel_invocations(instance)

    report = cross_channel_ingress_conformance(
        invocations,
        definition,
        instance,
        principal=_principal(),
        adapters=adapters,
        executors=WorkflowExecutorRegistry(adapters),
        now="2026-08-04T00:00:00+00:00",
    )

    assert report["valid"] is True
    assert report["diagnostics"] == []
    assert {item["expected_generation"] for item in report["channels"]} == {0}
    assert len({_digest_target(item["target_ref"]) for item in report["channels"]}) == 1
    assert {item["guard"]["accepted"] for item in report["channels"]} == {True}
    assert {item["executor"]["reason_code"] for item in report["channels"]} == {
        "executor_unavailable"
    }
    assert {item["execution"]["reason_code"] for item in report["channels"]} == {
        "executor_unavailable"
    }


def _digest_target(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))
