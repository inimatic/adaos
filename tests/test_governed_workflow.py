from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from adaos.services.governed_workflow import (
    WorkflowDefinitionError,
    WorkflowResolver,
    WorkflowResolutionError,
    apply_workflow_command,
    compile_definition,
    definition_review_report,
    export_statechart,
    generate_conformance_cases,
    migrate_workflow_instance,
    new_instance,
    rebuild_instance,
    rebuild_versioned_instance,
    resolve_workflow_join,
    validate_workflow_composition,
    workflow_command,
    workflow_contract_snapshot,
    workflow_ref,
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
        "context": {"target_resolution": "instance", "command_context_required": False},
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
        "approval": {"required": confirmation != "none", "policy_refs": []},
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
        "events": {"emitted": [f"builder.{command}.accepted"], "outbox": True},
        "observability": {
            "audit_event": f"builder.{command}.audit",
            "redaction": "policy",
            "metrics": ["workflow_transition_total"],
            "trace": True,
        },
        "migration": {"introduced_in": "1.0.0", "aliases": []},
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


def test_transition_roles_are_declared_and_enforced() -> None:
    definition = _definition()
    definition["transitions"][0]["authority"]["roles"] = ["registered"]
    compiled = compile_definition(definition)
    instance = new_instance(compiled, "change:roles")
    resolver = WorkflowResolver()

    guest = resolver.describe(
        compiled,
        instance,
        actor="user:guest",
        permissions=("builder.change",),
        roles=("guest",),
    )
    registered = resolver.describe(
        compiled,
        instance,
        actor="user:registered",
        permissions=("builder.change",),
        roles=("registered",),
    )

    assert guest["allowed_commands"] == []
    assert guest["blocked_commands"][0]["reason_code"] == "role_not_authorized:registered"
    assert registered["allowed_commands"][0]["command"] == "approve"


def test_instance_is_pinned_to_definition_digest_not_only_version() -> None:
    source = compile_definition(_definition())
    instance = new_instance(source, "change:digest")
    changed = _definition()
    changed["metadata"]["revision"] = "different-content-same-version"
    target = compile_definition(changed)

    with pytest.raises(WorkflowResolutionError, match="definition digest"):
        WorkflowResolver().describe(
            target,
            instance,
            actor="user:local",
            permissions=("builder.change",),
        )


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
    assert decision["events"] == ["builder.approve.accepted"]
    assert decision["event_records"][0]["type"] == "workflow.transition.applied"
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
    assert description["blockers"][0]["reason_key"] == "workflow.reason.trial_not_accepted"
    assert description["progress"]["completed_transitions"] == 0

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


def test_compiler_rejects_unreachable_waiting_and_unregistered_handlers() -> None:
    unreachable = _definition()
    unreachable["states"].insert(
        -1,
        {"id": "orphan", "label": "Orphan", "terminal": False},
    )
    with pytest.raises(WorkflowDefinitionError, match="unreachable states: orphan"):
        compile_definition(unreachable)

    waiting = _definition()
    waiting["states"][1]["waiting"] = True
    with pytest.raises(WorkflowDefinitionError, match="requires wait_explanation"):
        compile_definition(waiting)

    with pytest.raises(WorkflowDefinitionError, match="unregistered activity"):
        compile_definition(
            _definition(),
            registered_guards={"always", "instance_context_equals"},
            registered_activities={"builder.approve"},
        )


def test_typed_command_applies_and_event_rebuild_matches_snapshot() -> None:
    compiled = compile_definition(_definition())
    instance = new_instance(
        compiled,
        "change:recipes:4",
        now="2026-07-30T11:00:00+00:00",
    )
    command = workflow_command(
        "approve",
        instance_id=instance["instance_id"],
        workflow_type=compiled.workflow_type,
        definition_version=compiled.definition_version,
        actor_id="user:local",
        expected_generation=0,
        idempotency_key="request:typed",
        created_at="2026-07-30T11:01:00+00:00",
    )

    decision = apply_workflow_command(
        compiled,
        instance,
        command,
        permissions=("builder.change",),
    )
    rebuilt = rebuild_instance(
        compiled,
        instance["instance_id"],
        decision["event_records"],
        created_at=instance["created_at"],
    )

    assert rebuilt["state"] == decision["after"]["state"]
    assert rebuilt["generation"] == decision["after"]["generation"]
    assert rebuilt["history"] == decision["after"]["history"]
    assert workflow_ref("artifact", "scenario:recipes", version="0.1.0")["kind"] == "artifact"


def test_definition_review_and_statechart_are_non_authoritative_projections() -> None:
    compiled = compile_definition(_definition())

    report = definition_review_report(compiled)
    statechart = export_statechart(compiled)

    assert report["reachable_states"] == ["automation", "prototype", "published"]
    assert report["terminal_states"] == ["published"]
    assert report["unused_commands"] == []
    assert statechart["authoritative"] is False
    assert statechart["edges"][0]["command"] == "approve"
    cases = generate_conformance_cases(compiled)
    assert {item["kind"] for item in cases} == {"state_explanation", "transition_admission"}
    assert len(cases) == len(compiled.states) + len(compiled.transitions)


def test_definition_migration_is_explicit_generation_guarded_and_replayable() -> None:
    source = _definition()
    target = copy.deepcopy(source)
    target["definition_version"] = "1.1.0"
    target["states"][1]["id"] = "implementation"
    target["states"][1]["label"] = "Implementation"
    target["transitions"][0]["target"] = "implementation"
    target["transitions"][1]["source"] = "implementation"
    migration = json.loads(
        (Path(__file__).parent / "fixtures" / "workflow_definition_migration.v1.json").read_text(
            encoding="utf-8"
        )
    )
    original = new_instance(
        source,
        "change:recipes:migrated",
        context={"legacy_phase": True},
        now="2026-07-30T12:00:00+00:00",
    )
    approved = WorkflowResolver().apply(
        source,
        original,
        "approve",
        actor="user:local",
        permissions=("builder.change",),
        expected_generation=0,
        idempotency_key="request:approve-before-migration",
        now="2026-07-30T12:01:00+00:00",
    )

    decision = migrate_workflow_instance(
        source,
        target,
        approved["after"],
        migration,
        actor="user:local",
        permissions=("workflow.definition.migrate",),
        expected_generation=1,
        idempotency_key="migration:builder-change:1.1.0",
        now="2026-07-30T12:02:00+00:00",
    )

    assert decision["after"]["definition_version"] == "1.1.0"
    assert decision["after"]["state"] == "implementation"
    assert decision["after"]["generation"] == 2
    assert "legacy_phase" not in decision["after"]["context"]
    assert decision["event_records"][0]["type"] == "workflow.definition.migrated"
    rebuilt = rebuild_versioned_instance(
        {"1.0.0": source, "1.1.0": target},
        original["instance_id"],
        [*approved["event_records"], *decision["event_records"]],
        context={"legacy_phase": True},
        created_at=original["created_at"],
    )
    assert rebuilt["definition_version"] == decision["after"]["definition_version"]
    assert rebuilt["state"] == decision["after"]["state"]
    assert rebuilt["generation"] == decision["after"]["generation"]
    assert rebuilt["context"] == decision["after"]["context"]
    assert rebuilt["history"] == decision["after"]["history"]

    with pytest.raises(Exception, match="stale generation"):
        migrate_workflow_instance(
            source,
            target,
            approved["after"],
            migration,
            actor="user:local",
            permissions=("workflow.definition.migrate",),
            expected_generation=0,
            idempotency_key="migration:stale",
        )


def test_parent_child_composition_never_promotes_a_partial_required_join() -> None:
    composition = {
        "schema": "adaos.workflow.composition.v1",
        "composition_id": "builder_multi_component_change",
        "parent_ref": workflow_ref("workflow", "change:recipes:multi", version="1.0.0", generation=2),
        "parent_authority": {
            "actors": ["user", "builder"],
            "permissions": ["builder.change", "scenario.write", "skill.write"],
        },
        "children": [
            {
                "child_id": "scenario",
                "workflow_type": "builder.component",
                "definition_version": "1.0.0",
                "correlation_key": "change:recipes:multi:scenario",
                "required": True,
                "delegated_authority": {
                    "actors": ["builder"],
                    "permissions": ["scenario.write"],
                },
            },
            {
                "child_id": "skill",
                "workflow_type": "builder.component",
                "definition_version": "1.0.0",
                "correlation_key": "change:recipes:multi:skill",
                "required": True,
                "delegated_authority": {
                    "actors": ["builder"],
                    "permissions": ["skill.write"],
                },
            },
        ],
        "join": {"mode": "all", "quorum": None},
        "partial_outcome": "fail",
        "cancellation": "propagate",
        "compensation": "reverse_completed",
        "evidence_aggregation": "successful_only",
        "late_result": "reconcile",
    }
    validate_workflow_composition(composition)

    waiting = resolve_workflow_join(
        composition,
        [{"child_id": "scenario", "status": "succeeded", "evidence_refs": ["evidence:scenario"]}],
    )
    assert waiting["outcome"] == "waiting"
    assert waiting["promotable"] is False
    failed = resolve_workflow_join(
        composition,
        [
            {"child_id": "scenario", "status": "succeeded", "evidence_refs": ["evidence:scenario"]},
            {"child_id": "skill", "status": "failed", "evidence_refs": ["evidence:failure"]},
        ],
    )
    assert failed["outcome"] == "partial_failed"
    assert failed["promotable"] is False
    completed = resolve_workflow_join(
        composition,
        [
            {"child_id": "scenario", "status": "succeeded", "evidence_refs": ["evidence:scenario"]},
            {"child_id": "skill", "status": "succeeded", "evidence_refs": ["evidence:skill"]},
        ],
    )
    assert completed["outcome"] == "succeeded"
    assert completed["promotable"] is True
    assert completed["evidence_refs"] == ["evidence:scenario", "evidence:skill"]

    invalid = copy.deepcopy(composition)
    invalid["children"][1]["delegated_authority"]["permissions"] = ["root.admin"]
    with pytest.raises(WorkflowDefinitionError, match="outside the parent scope"):
        validate_workflow_composition(invalid)
