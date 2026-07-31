from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.services.governed_workflow import compile_definition, workflow_definition_digest


class LegacyWorkflowTranslationError(ValueError):
    """Raised when legacy state/action data cannot be translated deterministically."""


def _id(value: Any, *, field: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "").strip()).strip("_")
    if not token or len(token) > 160 or not token[0].isalnum():
        raise LegacyWorkflowTranslationError(f"legacy workflow {field} is not a stable id")
    return token


def _input_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}


def translate_legacy_scenario_workflow(
    legacy: Mapping[str, Any],
    *,
    scenario_id: str,
    definition_version: str = "1.0.0-legacy",
) -> dict[str, Any]:
    """Translate scenario.yaml states/actions without adding semantic defaults."""

    states_value = legacy.get("states")
    if not isinstance(states_value, Mapping) or not states_value:
        raise LegacyWorkflowTranslationError("legacy workflow states must be a non-empty object")
    state_ids = [_id(key, field="state") for key in states_value]
    if len(state_ids) != len(set(state_ids)):
        raise LegacyWorkflowTranslationError("legacy workflow state ids collide after normalization")
    initial_state = _id(legacy.get("initial_state") or state_ids[0], field="initial_state")
    if initial_state not in state_ids:
        raise LegacyWorkflowTranslationError("legacy workflow initial_state is not declared")

    commands: dict[str, dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    governed_states: list[dict[str, Any]] = []
    for raw_state_id, raw_state in states_value.items():
        state_id = _id(raw_state_id, field="state")
        state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
        actions = state.get("actions") or []
        if not isinstance(actions, list):
            raise LegacyWorkflowTranslationError(f"legacy state {state_id} actions must be a list")
        governed_states.append(
            {
                "id": state_id,
                "label": str(state.get("label") or state_id),
                "terminal": not actions,
                "description": "Translated from scenario.yaml legacy workflow.",
            }
        )
        for raw_action in actions:
            if not isinstance(raw_action, Mapping):
                raise LegacyWorkflowTranslationError(
                    f"legacy state {state_id} contains a non-object action"
                )
            action = dict(raw_action)
            command = _id(action.get("id"), field="action")
            target = _id(action.get("next_state") or state_id, field="next_state")
            if target not in state_ids:
                raise LegacyWorkflowTranslationError(
                    f"legacy action {command} targets unknown state {target}"
                )
            commands.setdefault(
                command,
                {
                    "id": command,
                    "input_schema": _input_schema(),
                    "description": str(action.get("label") or command),
                },
            )
            tool = str(action.get("tool") or "").strip()
            activity = f"legacy.tool.{_id(tool, field='tool')}" if tool else None
            risk_class = "isolated_write" if activity else "local_reversible"
            side_effect = "external" if activity else "reversible"
            transitions.append(
                {
                    "schema": "adaos.workflow.transition.v1",
                    "transition_id": f"legacy.{state_id}.{command}",
                    "source": state_id,
                    "target": target,
                    "trigger": {
                        "kind": "command",
                        "command": command,
                        "input_schema": _input_schema(),
                    },
                    "context": {
                        "target_resolution": "instance",
                        "command_context_required": False,
                    },
                    "authority": {
                        "actors": ["*"],
                        "permissions": [],
                        "roles": ["registered"],
                    },
                    "guards": [{"id": "always", "params": {}, "reason_code": "legacy_blocked"}],
                    "concurrency": {
                        "conflict_scope": f"scenario:{scenario_id}",
                        "requires_generation": True,
                        "idempotency": "required",
                    },
                    "risk": {
                        "class": risk_class,
                        "side_effect": side_effect,
                        "confirmation": "none",
                    },
                    "effect": {
                        "activity": activity,
                        "transaction": "outbox" if activity else "atomic",
                        "retry": "never",
                        "compensation": None,
                    },
                    "recovery": {
                        "timeout_seconds": None,
                        "heartbeat_seconds": None,
                        "cancellation": "not_applicable",
                        "reconciliation": "required_on_unknown" if activity else "not_applicable",
                    },
                    "outcomes": {
                        "success": target,
                        "failure": state_id,
                        "input_required": state_id,
                        "cancelled": state_id,
                        "unknown": state_id,
                    },
                    "evidence": {"required": False, "minimum": 0},
                    "approval": {"required": False, "policy_refs": []},
                    "async_reply": {
                        "mode": "terminal" if activity else "none",
                        "reply_route": "origin" if activity else "none",
                    },
                    "capability_requirements": {
                        "required": [],
                        "optional": ["buttons"],
                        "fallback": "numbered_text",
                    },
                    "explanations": {
                        "allowed": f"{command} is available in legacy state {state_id}.",
                        "rejected": f"{command} is not available in the current state.",
                        "completed": f"{command} moved the workflow to {target}.",
                    },
                    "events": {
                        "emitted": [str(action.get("emit_event") or "scenario.workflow.transition")],
                        "outbox": bool(activity or action.get("emit_event")),
                    },
                    "observability": {
                        "audit_event": "scenario.workflow.legacy_transition",
                        "redaction": "input",
                        "metrics": ["scenario_legacy_workflow_transition_total"],
                        "trace": True,
                    },
                    "migration": {"introduced_in": definition_version, "aliases": []},
                }
            )
    definition = {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": f"scenario.{_id(scenario_id, field='scenario')}",
        "definition_version": definition_version,
        "aggregate_type": "scenario.workflow",
        "initial_state": initial_state,
        "states": governed_states,
        "commands": list(commands.values()),
        "transitions": transitions,
        "metadata": {
            "source": "scenario.yaml.workflow",
            "compatibility": "legacy_read_authority",
        },
    }
    compile_definition(definition)
    return definition


def shadow_compare_legacy_workflow(
    legacy: Mapping[str, Any],
    definition: Mapping[str, Any],
    *,
    scenario_id: str,
) -> dict[str, Any]:
    translated = translate_legacy_scenario_workflow(legacy, scenario_id=scenario_id)

    def edges(value: Mapping[str, Any]) -> list[tuple[str, str, str]]:
        return sorted(
            (
                str(source),
                str(item["trigger"]["command"]),
                str(item["target"]),
            )
            for item in value.get("transitions") or []
            for source in (
                item["source"] if isinstance(item.get("source"), list) else [item["source"]]
            )
        )

    expected_states = sorted(str(item["id"]) for item in translated["states"])
    observed_states = sorted(str(item["id"]) for item in definition.get("states") or [])
    expected_edges = edges(translated)
    observed_edges = edges(definition)
    return {
        "schema": "adaos.workflow.legacy_shadow_report.v1",
        "status": (
            "match"
            if expected_states == observed_states and expected_edges == observed_edges
            else "diverged"
        ),
        "legacy_definition_digest": workflow_definition_digest(translated),
        "governed_definition_digest": workflow_definition_digest(definition),
        "states": {"legacy": expected_states, "governed": observed_states},
        "edges": {"legacy": expected_edges, "governed": observed_edges},
    }


def inventory_scenario_workflows(root: Path | str) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    entries: list[dict[str, Any]] = []
    for manifest_path in sorted(base.glob("*/scenario.yaml")):
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            entries.append(
                {"scenario": manifest_path.parent.name, "mode": "invalid", "error": str(exc)}
            )
            continue
        workflow = manifest.get("workflow") if isinstance(manifest, Mapping) else None
        if isinstance(workflow, Mapping) and workflow.get("manifest") == "workflow.json":
            mode = "governed_manifest"
        elif isinstance(workflow, Mapping) and isinstance(workflow.get("states"), Mapping):
            mode = "legacy_inline"
        else:
            mode = "none"
        entries.append({"scenario": manifest_path.parent.name, "mode": mode})
    counts = {
        mode: sum(1 for item in entries if item["mode"] == mode)
        for mode in ("governed_manifest", "legacy_inline", "none", "invalid")
    }
    return {"schema": "adaos.workflow.inventory.v1", "root": str(base), "counts": counts, "entries": entries}


__all__ = [
    "LegacyWorkflowTranslationError",
    "inventory_scenario_workflows",
    "shadow_compare_legacy_workflow",
    "translate_legacy_scenario_workflow",
]
