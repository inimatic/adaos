"""Canonical, executable semantics for ConversationInteraction actions."""

from __future__ import annotations

import copy
from typing import Any, Mapping


ACTION_SEMANTICS_SCHEMA = "adaos.conversation.action_semantics.v1"
ACTION_EFFECT_ASSERTION_SCHEMA = "adaos.conversation.action_effect_assertion.v1"

_NON_MUTATING_RISKS = {"read", "none", "read_only"}
_PRESETS: dict[str, dict[str, Any]] = {
    "details": {
        "effect_class": "observation",
        "operation": "inspect_details",
        "executor": "client",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": False,
        "effect_ref_required": False,
    },
    "preview": {
        "effect_class": "observation",
        "operation": "render_preview",
        "executor": "client",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": False,
        "effect_ref_required": False,
    },
    "open": {
        "effect_class": "navigation",
        "operation": "open_target",
        "executor": "client",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": False,
        "effect_ref_required": False,
    },
    "test": {
        "effect_class": "verification",
        "operation": "run_declared_test",
        "executor": "workflow",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": False,
        "effect_ref_required": True,
    },
    "snooze": {
        "effect_class": "scheduling",
        "operation": "snooze_interaction",
        "executor": "core",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": False,
        "effect_ref_required": False,
    },
    "defer": {
        "effect_class": "decision",
        "operation": "defer_work",
        "executor": "core",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": True,
        "effect_ref_required": False,
    },
    "refuse": {
        "effect_class": "decision",
        "operation": "refuse_request",
        "executor": "core",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": True,
        "effect_ref_required": False,
    },
    "cancel": {
        "effect_class": "workflow_control",
        "operation": "cancel_work",
        "executor": "workflow",
        "mutates_domain": True,
        "records_consent": True,
        "terminal": True,
        "effect_ref_required": True,
    },
    "compensate": {
        "effect_class": "compensation",
        "operation": "compensate_effect",
        "executor": "workflow",
        "mutates_domain": True,
        "records_consent": True,
        "terminal": True,
        "effect_ref_required": True,
    },
}


class ActionSemanticsError(ValueError):
    """Raised when a label is not backed by coherent action semantics."""


def _exact_effect_ref(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ActionSemanticsError("action semantics effect_ref must be an object")
    result = {
        "kind": str(value.get("kind") or "").strip(),
        "id": str(value.get("id") or "").strip(),
        "digest": str(value.get("digest") or "").strip(),
    }
    if not result["kind"] or not result["id"]:
        raise ActionSemanticsError("action semantics effect_ref requires kind and id")
    if not result["digest"].startswith("sha256:") or len(result["digest"]) != 71:
        raise ActionSemanticsError("action semantics effect_ref requires an exact sha256 digest")
    return result


def normalize_action_semantics(action: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an action without guessing semantics from its id or label."""

    declared = dict(action.get("semantics") or {})
    preset = str(action.get("preset") or declared.get("preset") or "custom").strip().lower()
    if preset != "custom" and preset not in _PRESETS:
        raise ActionSemanticsError(f"unsupported action preset: {preset}")
    risk = str(action.get("risk") or "read").strip().lower()
    confirmation = bool(action.get("confirmation_required"))
    if preset == "custom":
        mutates = risk not in _NON_MUTATING_RISKS
        result = {
            "schema": ACTION_SEMANTICS_SCHEMA,
            "preset": "custom",
            "effect_class": str(declared.get("effect_class") or ("workflow" if mutates else "observation")),
            "operation": str(declared.get("operation") or action.get("command") or "custom_action"),
            "executor": str(declared.get("executor") or ("workflow" if mutates else "client")),
            "mutates_domain": bool(declared.get("mutates_domain", mutates)),
            "records_consent": bool(declared.get("records_consent", confirmation)),
            "terminal": bool(declared.get("terminal", True)),
            "effect_ref": _exact_effect_ref(declared.get("effect_ref")),
            "assertion_required": bool(declared.get("assertion_required", False)),
        }
        if not result["effect_class"] or not result["operation"] or not result["executor"]:
            raise ActionSemanticsError("custom action semantics require effect_class, operation, and executor")
        if result["mutates_domain"] and risk in _NON_MUTATING_RISKS:
            raise ActionSemanticsError("mutating custom action semantics cannot declare read-only risk")
        return result

    policy = copy.deepcopy(_PRESETS[preset])
    for field in (
        "effect_class", "operation", "executor", "mutates_domain",
        "records_consent", "terminal",
    ):
        if field in declared and declared[field] != policy[field]:
            raise ActionSemanticsError(f"{preset} action cannot override {field}")
    if not policy["mutates_domain"] and (risk not in _NON_MUTATING_RISKS or confirmation):
        raise ActionSemanticsError(f"{preset} action must be read-only and require no confirmation")
    if policy["mutates_domain"] and risk in _NON_MUTATING_RISKS:
        raise ActionSemanticsError(f"{preset} action must declare a mutating risk")
    effect_ref = _exact_effect_ref(declared.get("effect_ref"))
    if policy.pop("effect_ref_required") and effect_ref is None:
        raise ActionSemanticsError(f"{preset} action requires an exact effect_ref")
    return {
        "schema": ACTION_SEMANTICS_SCHEMA,
        "preset": preset,
        **policy,
        "effect_ref": effect_ref,
        "assertion_required": True,
    }


def validate_effect_assertion(
    semantics: Mapping[str, Any],
    outcome: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Verify that a successful standard action observed its declared effect."""

    if not bool(semantics.get("assertion_required")):
        return None
    assertion = dict(outcome or {}).get("effect_assertion")
    if not isinstance(assertion, Mapping):
        raise ActionSemanticsError("successful action requires an effect_assertion")
    value = dict(assertion)
    if value.get("schema") != ACTION_EFFECT_ASSERTION_SCHEMA:
        raise ActionSemanticsError("action effect assertion schema is invalid")
    for field in ("preset", "operation"):
        if str(value.get(field) or "") != str(semantics.get(field) or ""):
            raise ActionSemanticsError(f"action effect assertion {field} does not match")
    if value.get("observed") is not True:
        raise ActionSemanticsError("action effect was not observed")
    if not bool(semantics.get("mutates_domain")) and value.get("domain_mutated") is not False:
        raise ActionSemanticsError("read-only action effect assertion must prove no domain mutation")
    if copy.deepcopy(value.get("effect_ref")) != copy.deepcopy(semantics.get("effect_ref")):
        raise ActionSemanticsError("action effect assertion effect_ref does not match")
    preset = str(semantics.get("preset") or "")
    required_flag = {
        "test": "test_executed",
        "cancel": "cancelled",
        "compensate": "compensation_applied",
    }.get(preset)
    if required_flag and value.get(required_flag) is not True:
        raise ActionSemanticsError(f"{preset} effect assertion requires {required_flag}=true")
    return value


__all__ = [
    "ACTION_EFFECT_ASSERTION_SCHEMA",
    "ACTION_SEMANTICS_SCHEMA",
    "ActionSemanticsError",
    "normalize_action_semantics",
    "validate_effect_assertion",
]
