from __future__ import annotations

import json
from pathlib import Path

import yaml

from adaos.services.builder.governed import builder_change_definition
from adaos.services.conversational_artifacts import validate_conversational_package
from adaos.services.governed_workflow import (
    compile_definition,
    workflow_contract_snapshot,
    workflow_definition_digest,
)
from adaos.services.workflow_static_reports import (
    conversational_package_static_report,
    workflow_static_report_markdown,
    workflow_static_report,
)


def _transition(
    transition_id: str,
    source: str,
    target: str,
    command: str,
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
        "approval": {"required": False, "policy_refs": []},
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
        "commands": [
            {"id": "approve", "input_schema": approve["trigger"]["input_schema"]},
        ],
        "transitions": [approve],
        "subworkflows": [],
        "metadata": {"pilot": "static_report"},
    }


def _write_yaml(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_package(root: Path) -> None:
    (root / "skill.yaml").write_text(
        "name: demo_skill\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\nconversational:\n  manifest: conversational/manifest.yaml\n",
        encoding="utf-8",
    )
    (root / "workflow.json").write_text(json.dumps(_definition()), encoding="utf-8")
    conv = root / "conversational"
    _write_yaml(
        conv / "manifest.yaml",
        {
            "schema": "adaos.conversational.package_manifest.v1",
            "package_id": "demo_skill",
            "package_kind": "skill",
            "owner_ref": {"kind": "skill", "id": "demo_skill"},
            "version": "0.1.0",
            "workflow_refs": [
                {
                    "workflow_type": "builder.change",
                    "definition_ref": "../workflow.json",
                    "definition_version": "1.0.0",
                    "definition_digest": None,
                }
            ],
            "files": {
                "input": "input.yaml",
                "entities": "entities.yaml",
                "examples": "examples.yaml",
                "affordances": "affordances.yaml",
                "repair": "repair.yaml",
                "output": "output.yaml",
                "stories": ["tests/stories/approve.yaml"],
                "locales": ["locale.en.yaml"],
            },
            "locales": ["en"],
            "privacy_defaults": {
                "source_scope": "skill",
                "runtime_overlay_scope": "user",
                "public_promotion": "requires_review",
            },
            "compiled_outputs": [],
            "compatibility_aliases": [],
        },
    )
    _write_yaml(
        conv / "input.yaml",
        {
            "schema": "adaos.conversational.input.v1",
            "package_id": "demo_skill",
            "intents": [
                {
                    "id": "approve_prototype",
                    "description": "Approve the current prototype.",
                    "kind": "workflow_command",
                    "affordance_id": "approve_prototype",
                    "workflow": {
                        "workflow_type": "builder.change",
                        "command_id": "approve",
                        "transition_id": "approve_prototype",
                    },
                    "skill_invocation": None,
                    "example_ids": ["approve.example.1"],
                    "slots": [],
                }
            ],
            "policy": {
                "default_confidence": 0.8,
                "abstain_below": 0.55,
                "protected_action_confirmation": True,
            },
        },
    )
    _write_yaml(
        conv / "entities.yaml",
        {"schema": "adaos.conversational.entities.v1", "package_id": "demo_skill", "entities": []},
    )
    _write_yaml(
        conv / "examples.yaml",
        {
            "schema": "adaos.conversational.examples.v1",
            "package_id": "demo_skill",
            "examples": [
                {
                    "id": "approve.example.1",
                    "intent_id": "approve_prototype",
                    "text": "approve it",
                    "locale": "en",
                    "source": "authored",
                    "entities": [],
                }
            ],
            "hard_negatives": [],
        },
    )
    _write_yaml(
        conv / "affordances.yaml",
        {
            "schema": "adaos.conversational.affordances.v1",
            "package_id": "demo_skill",
            "affordances": [
                {
                    "id": "approve_prototype",
                    "kind": "workflow_command",
                    "label": "Approve",
                    "description": "Approve the prototype and start implementation.",
                    "workflow": {
                        "workflow_type": "builder.change",
                        "command_id": "approve",
                        "transition_id": "approve_prototype",
                    },
                    "skill_invocation": None,
                    "action_policy": {
                        "schema": "adaos.conversation.action_policy.v1",
                        "risk_class": "isolated_write",
                        "side_effect": "reversible",
                        "confirmation": "none",
                    },
                    "required_capabilities": [],
                    "presentation": {"hint": "button", "priority": 10},
                    "output_refs": ["prototype_approved"],
                }
            ],
        },
    )
    _write_yaml(
        conv / "repair.yaml",
        {
            "schema": "adaos.conversational.repair.v1",
            "package_id": "demo_skill",
            "policies": [
                {
                    "id": "no_match",
                    "kind": "no_match",
                    "max_attempts": 2,
                    "output_ref": "repair_no_match",
                    "terminal_outcome": "clarification",
                }
            ],
        },
    )
    _write_yaml(
        conv / "output.yaml",
        {
            "schema": "adaos.conversational.output.v1",
            "package_id": "demo_skill",
            "outputs": [
                {
                    "id": "prototype_approved",
                    "kind": "result",
                    "audience": "user",
                    "risk_level": "medium",
                    "reason_code": "prototype_approved",
                    "explanation": "The workflow accepted the approval.",
                    "summary": "Prototype approved.",
                    "content_parts": [],
                    "details": [],
                    "actions": [
                        {
                            "action_id": "approve_action",
                            "label": "Approve",
                            "affordance_id": "approve_prototype",
                        }
                    ],
                    "next_expected_input": "none",
                    "handoff_target": None,
                },
                {
                    "id": "repair_no_match",
                    "kind": "repair",
                    "audience": "user",
                    "risk_level": "none",
                    "reason_code": "no_match",
                    "explanation": "The input did not match an available intent.",
                    "summary": "Please rephrase.",
                    "content_parts": [],
                    "details": [],
                    "actions": [],
                    "next_expected_input": "text",
                    "handoff_target": None,
                },
            ],
        },
    )
    _write_yaml(
        conv / "locale.en.yaml",
        {"schema": "adaos.conversational.locale.v1", "package_id": "demo_skill", "locale": "en", "messages": {}},
    )
    _write_yaml(
        conv / "tests" / "stories" / "approve.yaml",
        {
            "schema": "adaos.conversational.story.v1",
            "id": "builder.approve.en.happy_path",
            "title": "Approve the prototype",
            "story_kind": "workflow",
            "workflow_type": "builder.change",
            "locale": "en",
            "channel": "web",
            "actor": {
                "id": "user:local",
                "permissions": ["builder.change"],
                "roles": [],
            },
            "start": {
                "instance_id": "change:demo",
                "state": "prototype",
                "generation": 0,
                "context": {},
            },
            "steps": [
                {
                    "user": "approve it",
                    "given": {
                        "proposal": {
                            "kind": "workflow_command",
                            "intent_id": "approve_prototype",
                            "command": "approve",
                            "skill_id": None,
                            "operation_id": None,
                            "arguments": {},
                            "confidence": 0.9,
                            "action_policy": {
                                "schema": "adaos.conversation.action_policy.v1",
                                "risk_class": "isolated_write",
                                "side_effect": "reversible",
                                "confirmation": "none",
                            },
                        },
                        "event": None,
                        "skill_result": None,
                        "output_ref": "prototype_approved",
                    },
                    "expect": {
                        "proposal": {
                            "kind": "workflow_command",
                            "command": "approve",
                            "confidence_at_least": 0.9,
                        },
                        "command": "approve",
                        "transition_id": "approve_prototype",
                        "state": "automation",
                        "reason_code": None,
                        "output": {
                            "kind": "result",
                            "output_ref": "prototype_approved",
                            "summary": "Prototype approved.",
                            "actions": ["approve_action"],
                            "next_expected_input": "none",
                        },
                    },
                }
            ],
        },
    )


def test_workflow_static_report_exports_statechart_and_conformance() -> None:
    compiled = compile_definition(builder_change_definition())

    report = workflow_static_report(
        compiled,
        generated_at="2026-01-01T00:00:00+00:00",
    )

    assert report["definition_digest"] == workflow_definition_digest(compiled)
    assert report["statechart"]["authoritative"] is False
    assert report["definition_review"]["state_count"] == len(compiled.states)
    assert report["conformance"]["case_count"] == (
        report["conformance"]["state_case_count"] + report["conformance"]["transition_case_count"]
    )
    assert report["coverage"]["states_missing_story_coverage"] == report["coverage"]["states_declared"]
    assert (
        workflow_contract_snapshot()["records"]["WorkflowStaticReport"]
        == "adaos.workflow.static_report.v1"
    )


def test_conversational_package_static_report_covers_story_without_chat_prose(tmp_path: Path) -> None:
    _write_package(tmp_path)
    validation = validate_conversational_package(tmp_path, manifest_name="skill.yaml")
    assert validation.package is not None

    report = conversational_package_static_report(
        validation.package,
        validation_result=validation,
        generated_at="2026-01-01T00:00:00+00:00",
    )

    assert report["package_id"] == "demo_skill"
    assert report["package_digest"] == validation.report["package_digest"]
    assert report["coverage"]["states_covered_by_stories"] == ["automation", "prototype"]
    assert report["coverage"]["transitions_covered_by_stories"] == ["approve_prototype"]
    assert report["coverage"]["commands_covered_by_stories"] == ["approve"]
    assert report["coverage"]["outputs_covered_by_stories"] == ["prototype_approved"]
    assert report["coverage"]["outputs_missing_story_coverage"] == ["repair_no_match"]
    assert report["story_reports"][0]["timeline"][0]["output"]["workflow_event_id"]
    assert "approve it" not in json.dumps(report, ensure_ascii=False)

    markdown = workflow_static_report_markdown(report)
    assert "```mermaid" in markdown
    assert "|approve / approve_prototype|" in markdown
    assert "builder.approve.en.happy_path [PASS]" in markdown
    assert "prototype -> automation" in markdown
