from __future__ import annotations

import json
from pathlib import Path

import yaml

from adaos.services.conversational_artifacts import (
    run_conversation_story,
    validate_conversational_package,
)
from adaos.services.governed_workflow import compile_definition, new_instance


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
        "metadata": {"pilot": "conversational"},
    }


def _write_yaml(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_package(root: Path, *, command: str = "approve", poisoned_affordance: bool = False) -> None:
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
                "affordances": "affordances.yaml",
                "repair": "repair.yaml",
                "output": "output.yaml",
                "stories": ["tests/stories/approve.yaml"],
                "locales": [],
            },
            "locales": ["en"],
            "privacy_defaults": {
                "source_scope": "skill",
                "runtime_overlay_scope": "user",
                "public_promotion": "requires_review",
            },
            "compiled_outputs": [],
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
                        "command_id": command,
                        "transition_id": "approve_prototype",
                    },
                    "skill_invocation": None,
                    "examples": [{"text": "approve it", "locale": "en", "source": "authored"}],
                    "slots": [],
                }
            ],
            "hard_negatives": [],
            "policy": {
                "default_confidence": 0.8,
                "abstain_below": 0.55,
                "protected_action_confirmation": True,
            },
        },
    )
    affordance: dict[str, object] = {
        "id": "approve_prototype",
        "kind": "workflow_command",
        "label": "Approve",
        "description": "Approve the prototype and start implementation.",
        "workflow": {
            "workflow_type": "builder.change",
            "command_id": command,
            "transition_id": "approve_prototype",
        },
        "skill_invocation": None,
        "side_effect_class": "reversible",
        "required_capabilities": [],
        "presentation": {"hint": "button", "priority": 10},
        "output_refs": ["prototype_approved"],
    }
    if poisoned_affordance:
        affordance["metadata"] = {"states": {"prototype": {"next_state": "automation"}}}
    _write_yaml(
        conv / "affordances.yaml",
        {
            "schema": "adaos.conversational.affordances.v1",
            "package_id": "demo_skill",
            "affordances": [affordance],
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
                    "summary": "Prototype approved.",
                    "details": [],
                    "actions": [
                        {
                            "action_id": "approve_action",
                            "label": "Approve",
                            "affordance_id": "approve_prototype",
                        }
                    ],
                    "next_expected_input": "none",
                },
                {
                    "id": "repair_no_match",
                    "kind": "repair",
                    "audience": "user",
                    "summary": "Please rephrase.",
                    "details": [],
                    "actions": [],
                    "next_expected_input": "text",
                },
            ],
        },
    )
    _write_yaml(
        conv / "tests" / "stories" / "approve.yaml",
        {
            "schema": "adaos.conversational.story.v1",
            "id": "builder.approve.en.happy_path",
            "title": "Approve the prototype",
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
                    "expect": {
                        "proposal": {
                            "kind": "workflow_command",
                            "command": command,
                            "arguments": {},
                            "confidence": 0.9,
                        },
                        "command": command,
                        "transition_id": "approve_prototype",
                        "state": "automation",
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


def test_conversational_package_validates_and_runs_story_with_mocked_activity(tmp_path: Path) -> None:
    _write_package(tmp_path)

    result = validate_conversational_package(tmp_path, manifest_name="skill.yaml")

    assert result.report["valid"] is True
    assert result.package is not None
    assert result.report["metrics"]["affordances"] == 1
    assert result.report["story_reports"][0]["final_state"] == "automation"
    timeline = result.report["story_reports"][0]["timeline"]
    assert timeline[0]["activity"]["mocked"] is True
    assert timeline[0]["activity"]["side_effect_isolated"] is True
    assert timeline[0]["output"]["schema"] == "adaos.conversation.output.v1"
    assert timeline[0]["output"]["correlation"]["command_id"] == "approve"
    assert timeline[0]["output"]["response_envelope_ref"] is None


def test_conversational_package_rejects_unknown_workflow_command(tmp_path: Path) -> None:
    _write_package(tmp_path, command="missing_command")

    result = validate_conversational_package(tmp_path, manifest_name="skill.yaml")

    assert result.report["valid"] is False
    codes = {item["code"] for item in result.report["diagnostics"]}
    assert "conversational.affordance.command_unknown" in codes
    assert "conversational.story.command_unknown" in codes


def test_affordances_cannot_define_a_second_workflow_shape(tmp_path: Path) -> None:
    _write_package(tmp_path, poisoned_affordance=True)

    result = validate_conversational_package(tmp_path, manifest_name="skill.yaml", run_stories=False)

    assert result.report["valid"] is False
    assert "conversational.affordance.workflow_shape" in {
        item["code"] for item in result.report["diagnostics"]
    }


def test_story_runner_can_be_used_directly_without_live_effects() -> None:
    workflow = compile_definition(_definition())
    instance = new_instance(workflow, "change:direct")
    assert instance["state"] == "prototype"
    story = {
        "id": "direct.story",
        "workflow_type": "builder.change",
        "actor": {"id": "user:local", "permissions": ["builder.change"], "roles": []},
        "start": {"instance_id": "change:direct", "state": "prototype", "generation": 0, "context": {}},
        "steps": [
            {
                "expect": {
                    "proposal": {"kind": "workflow_command", "command": "approve", "arguments": {}},
                    "command": "approve",
                    "state": "automation",
                    "output": {"kind": "result", "next_expected_input": "none"},
                }
            }
        ],
    }

    report = run_conversation_story(story, workflow)

    assert report["valid"] is True
    assert report["timeline"][0]["accepted"] is True
    assert report["timeline"][0]["activity"]["mocked"] is True
