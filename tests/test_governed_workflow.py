from __future__ import annotations

import copy

import pytest

from adaos.services.governed_workflow import (
    WorkflowDefinitionError,
    WorkflowResolver,
    compile_definition,
    new_instance,
    workflow_contract_snapshot,
)


def _transition(
    transition_id: str,
    source: str,
    target: str,
    command: str,
    *,
    confirmation: str = "none",
    guards: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    input_schema = {
        "type": "object",
        "properties": {
            "confirmed": {"type": "boolean"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    return {
        "schema": "adaos.workflow.transition.v1",
        "transition_id": transition_id,
        "source": source,
        "target": target,
        "trigger": {"kind": "command", "command": command, "input_schema": input_schema},
        "authority": {"actors": ["user"], "permissions": ["builder.change"]},
        "guards": guards or [{"id": "always", "params": {}, "reason_code": "blocked"}],
        "concurrency": {
            "conflict_scope": "change",
            "requires_generation": True,
            "idempotency": "required",
        },
        "risk": {
            "class": "isolated_write",
            "side_effect": "reversible",
            "confirmation": confirmation,
        },
        "effect": {
            "activity": f"builder.{command}",
            "transaction": "outbox",
            "retry": "bounded",
            "compensation": f"builder.undo_{command}",
        },
        "outcomes": {
            "success": "target",
            "failure": "source",
            "input_required": "source",
            "cancelled": "source",
            "unknown": "source",
        },
        "evidence": {"required": False, "minimum": 0},
        "async_reply": {"mode": "progress_and_terminal", "reply_route": "origin"},
        "capability_requirements": {
            "required": [],
            "optional": ["buttons", "progress"],
            "fallback": "numbered_text",
        },
        "explanations": {
            "allowed": f"{command} is available",
            "rejected": f"{command} is blocked",
            "completed": f"{command} completed",
        },
        "events": [f"builder.{command}.accepted"],
    }


def _definition() -> dict[str, object]:
    approve = _transition("approve_prototype", "prototype", "automation", "approve")
    publish = _transition(
        "publish_candidate",
        "automation",
        "published",
        "publish",
        confirmation="required",
        guards=[
            {
                "id": "instance_context_equals",
                "params": {"field": "trial", "value": "accepted"},
                "reason_code": "trial_not_accepted",
            }
        ],
    )
    return {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": "builder.change",
        "definition_version": "1.0.0",
        "aggregate_type": "builder.change",
        "initial_state": "prototype",
        "states": [
            {"id": "prototype", "label": "Prototype", "terminal": False},
            {"id": "automation", "label": "Automation", "terminal": False},
            {"id": "published", "label": "Published", "terminal": True},
        ],
        "commands": [
            {"id": "approve", "input_schema": approve["trigger"]["input_schema"]},
            {"id": "publish", "input_schema": publish["trigger"]["input_schema"]},
        ],
        "transitions": [approve, publish],
        "subworkflows": [],
        "metadata": {"pilot": "builder"},
    }


def test_compiler_builds_deterministic_transition_index() -> None:
    compiled = compile_definition(_definition())

    assert compiled.initial_state == "prototype"
    assert compiled.by_source_command[("prototype", "approve")].target == "automation"
    assert workflow_contract_snapshot()["invariants"]["resolver"] == "pure"


def test_compiler_rejects_ambiguous_edges() -> None:
    definition = _definition()
    definition["transitions"] = [
        *definition["transitions"],
        _transition("approve_again", "prototype", "automation", "approve"),
    ]

    with pytest.raises(WorkflowDefinitionError, match="ambiguous command"):
        compile_definition(definition)


def test_resolver_is_pure_generation_guarded_and_idempotent() -> None:
    compiled = compile_definition(_definition())
    original = new_instance(
        compiled,
        "change:recipes:1",
        context={"trial": "accepted"},
        now="2026-07-30T10:00:00+00:00",
    )
    resolver = WorkflowResolver()

    decision = resolver.apply(
        compiled,
        original,
        "approve",
        actor="user:local",
        permissions=("builder.change",),
        expected_generation=0,
        idempotency_key="request:1",
        now="2026-07-30T10:01:00+00:00",
    )

    assert decision["accepted"] is True
    assert decision["after"]["state"] == "automation"
    assert decision["after"]["generation"] == 1
    assert original["state"] == "prototype"
    assert original["generation"] == 0
    duplicate = resolver.apply(
        compiled,
        decision["after"],
        "approve",
        actor="user:local",
        permissions=("builder.change",),
        expected_generation=1,
        idempotency_key="request:1",
        now="2026-07-30T10:02:00+00:00",
    )
    assert duplicate["status"] == "duplicate"
    assert duplicate["reason_code"] == "already_applied"
    assert duplicate["after"]["generation"] == 1


def test_resolver_explains_authority_guard_confirmation_and_staleness() -> None:
    compiled = compile_definition(_definition())
    resolver = WorkflowResolver()
    instance = new_instance(compiled, "change:recipes:2", context={"trial": "pending"})

    denied = resolver.apply(
        compiled,
        instance,
        "approve",
        actor="skill:builder",
        permissions=("builder.change",),
        expected_generation=0,
        idempotency_key="request:denied",
    )
    assert denied["reason_code"] == "actor_not_authorized"

    stale = resolver.apply(
        compiled,
        instance,
        "approve",
        actor="user:local",
        permissions=("builder.change",),
        expected_generation=4,
        idempotency_key="request:stale",
    )
    assert stale["reason_code"] == "stale_generation"

    automation = copy.deepcopy(instance)
    automation["state"] = "automation"
    automation["generation"] = 1
    description = resolver.describe(
        compiled,
        automation,
        actor="user:local",
        permissions=("builder.change",),
    )
    assert description["allowed_commands"] == []
    assert description["blocked_commands"][0]["reason_code"] == "trial_not_accepted"

    automation["context"]["trial"] = "accepted"
    needs_confirmation = resolver.apply(
        compiled,
        automation,
        "publish",
        input_value={},
        actor="user:local",
        permissions=("builder.change",),
        expected_generation=1,
        idempotency_key="request:publish",
    )
    assert needs_confirmation["reason_code"] == "confirmation_required"


def test_unknown_guard_fails_closed() -> None:
    definition = _definition()
    definition["transitions"][0]["guards"] = [
        {"id": "missing_guard", "params": {}, "reason_code": "blocked"}
    ]
    compiled = compile_definition(definition)
    instance = new_instance(compiled, "change:recipes:3")

    with pytest.raises(Exception, match="guard is not registered"):
        WorkflowResolver().describe(
            compiled,
            instance,
            actor="user:local",
            permissions=("builder.change",),
        )
