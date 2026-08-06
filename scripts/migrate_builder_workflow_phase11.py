"""Regenerate the Builder 1.1 activity/waiting lifecycle deterministically."""

from __future__ import annotations

import copy
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "adaos" / "services" / "builder" / "builder_change.workflow.json"


def _by_id(values: list[dict], key: str, value: str) -> dict:
    return next(item for item in values if str(item.get(key) or "") == value)


def _by_command(values: list[dict], command: str) -> dict:
    return next(
        item
        for item in values
        if str(dict(item.get("trigger") or {}).get("command") or "") == command
    )


def _callback(template: dict, *, command: str, target: str, outcome: str) -> dict:
    item = copy.deepcopy(template)
    item["transition_id"] = command
    item["source"] = "trial_waiting"
    item["target"] = target
    item["trigger"]["command"] = command
    item["effect"] = {
        "activity": None,
        "transaction": "atomic",
        "retry": "never",
        "compensation": None,
    }
    item["outcomes"] = {
        "success": target,
        "failure": "trial_waiting",
        "input_required": "trial_waiting",
        "cancelled": "cancelled",
        "unknown": "reconciliation_required",
    }
    item["risk"] = {
        "class": "local_reversible",
        "side_effect": "reversible",
        "confirmation": "none",
    }
    item["approval"] = {"required": False, "policy_refs": []}
    item["async_reply"] = {"mode": "terminal", "reply_route": "origin"}
    item["explanations"] = {
        "allowed": f"Record the terminal Trial outcome: {outcome}.",
        "rejected": "A Trial outcome can only be recorded for the active waiting activity.",
        "completed": f"The Trial outcome was recorded as {outcome}.",
    }
    item["events"] = {
        "emitted": [f"builder.change.trial.{outcome}"],
        "outbox": True,
    }
    item["migration"] = {"introduced_in": "1.1.0", "aliases": []}
    return item


def migrate(value: dict) -> dict:
    result = copy.deepcopy(value)
    if str(result.get("definition_version")) not in {"1.0.0", "1.1.0"}:
        raise ValueError("unexpected Builder workflow version")
    result["definition_version"] = "1.1.0"
    states = [item for item in result["states"] if item.get("id") != "trial_waiting"]
    trial_index = next(index for index, item in enumerate(states) if item.get("id") == "trial_ready")
    states.insert(
        trial_index + 1,
        {
            "id": "trial_waiting",
            "label": "Trial Waiting",
            "terminal": False,
            "waiting": True,
            "wait_explanation": "Trial activation is running for the exact candidate digest.",
        },
    )
    result["states"] = states

    removed = {"prepare_trial_compatibility", "publish_compatibility"}
    commands = [item for item in result["commands"] if item.get("id") not in removed]
    command_template = copy.deepcopy(_by_id(commands, "id", "record_publication_success"))
    for command in ("record_trial_success", "record_trial_failure", "record_trial_unknown"):
        if not any(item.get("id") == command for item in commands):
            item = copy.deepcopy(command_template)
            item["id"] = command
            commands.append(item)
    result["commands"] = sorted(commands, key=lambda item: str(item.get("id") or ""))

    transitions = [
        item
        for item in result["transitions"]
        if item.get("transition_id") not in removed
        and item.get("transition_id") not in {
            "record_trial_success",
            "record_trial_failure",
            "record_trial_unknown",
        }
    ]
    start_trial = _by_id(transitions, "transition_id", "start_trial")
    start_trial["target"] = "trial_waiting"
    start_trial["outcomes"] = {
        "success": "trial_waiting",
        "failure": "trial_ready",
        "input_required": "trial_ready",
        "cancelled": "cancelled",
        "unknown": "reconciliation_required",
    }
    start_trial["migration"] = {"introduced_in": "1.1.0", "aliases": []}
    template = _by_command(transitions, "record_publication_success")
    transitions.extend(
        [
            _callback(template, command="record_trial_success", target="trial_review", outcome="succeeded"),
            _callback(template, command="record_trial_failure", target="trial_ready", outcome="failed"),
            _callback(template, command="record_trial_unknown", target="reconciliation_required", outcome="unknown"),
        ]
    )
    result["transitions"] = transitions
    # TransitionDescriptor is the authority for admission.  Keep it aligned
    # with the Builder surface risk contract so SDK, Web and chat cannot assign
    # different confirmation semantics to the same command.
    for transition in result["transitions"]:
        risk = dict(transition.get("risk") or {})
        if risk.get("class") in {"isolated_write", "trial_activation"}:
            risk["confirmation"] = "rich_review"
            transition["risk"] = risk
    result.setdefault("metadata", {})["activity_callback_contract"] = "waiting_then_terminal_callback"
    result["metadata"]["compatibility_shortcuts"] = []
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=TARGET)
    args = parser.parse_args()
    target = args.target.expanduser().resolve()
    source = json.loads(target.read_text(encoding="utf-8"))
    target.write_text(
        json.dumps(migrate(source), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
