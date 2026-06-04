from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Mapping

from adaos.services.nlu.teacher_read_model import preview_interface_action, preview_interface_action_async, preview_template_patch
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings


_HIGH_RISK_SIDE_EFFECTS = {"destructive", "external_io", "device_control", "unsupported"}
_SAFE_SIDE_EFFECTS = {"read_only", "ui_navigation", "local_state_change", "durable_configuration_change", "skill_action"}
_SYSTEM_ACTION_INTENTS = {
    "desktop.open_modal": "host.desktop.modal.open",
    "desktop.open_node_modal": "host.desktop.modal.open",
    "desktop.switch_scenario": "host.desktop.scenario.set",
    "desktop.toggle_app_install": "host.desktop.toggle_install_app",
    "desktop.reload_webspace": "host.desktop.webspace.reload",
    "desktop.reset_webspace": "host.desktop.webspace.reset",
}


def _float_env(name: str, default: float, *, min_value: float, max_value: float) -> float:
    raw = os.getenv(name)
    try:
        value = float(raw) if raw not in (None, "") else float(default)
    except Exception:
        value = float(default)
    return max(float(min_value), min(float(max_value), value))


def _action_preview_timeout_s() -> float:
    return _float_env("ADAOS_NLU_TEACHER_ACTION_PREVIEW_TIMEOUT_S", 4.0, min_value=0.2, max_value=60.0)
_PROMPT_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "jailbreak",
    "prompt injection",
    "disregard instructions",
    "\u0437\u0430\u0431\u0443\u0434\u044c \u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449",
    "\u0438\u0433\u043d\u043e\u0440\u0438\u0440\u0443\u0439 \u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449",
    "\u0441\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0439 \u043f\u0440\u043e\u043c\u043f\u0442",
)
_SYSTEM_COMMAND_ALIASES = {
    "reset",
    "reload",
    "shutdown",
    "delete",
    "remove",
    "wipe",
    "\u0441\u0431\u0440\u043e\u0441",
    "\u043f\u0435\u0440\u0435\u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0430",
    "\u0443\u0434\u0430\u043b\u0438\u0442\u044c",
    "\u0441\u0442\u0435\u0440\u0435\u0442\u044c",
}


def _check(name: str, ok: bool, status: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "status": status, **{k: v for k, v in extra.items() if v not in (None, [], {})}}


def _normalize_target(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    t_type = str(value.get("type") or "").strip()
    t_id = str(value.get("id") or "").strip()
    if t_type and t_id:
        return {"type": t_type, "id": t_id}
    return None


def _candidate_target(candidate: Mapping[str, Any], payload_target: Mapping[str, Any] | None) -> dict[str, str] | None:
    return _normalize_target(payload_target) or _normalize_target(candidate.get("target"))


def _candidate_intent(candidate: Mapping[str, Any]) -> str:
    rr = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
    strategy = candidate.get("strategy_candidate") if isinstance(candidate.get("strategy_candidate"), Mapping) else {}
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    for value in (rr.get("intent"), candidate.get("intent"), strategy.get("intent"), action.get("intent")):
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _candidate_pattern(candidate: Mapping[str, Any]) -> str:
    rr = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
    return str(rr.get("pattern") or "").strip()


def _candidate_slots(candidate: Mapping[str, Any]) -> dict[str, Any]:
    slots = candidate.get("slots") if isinstance(candidate.get("slots"), Mapping) else {}
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    action_slots = action.get("slots") if isinstance(action.get("slots"), Mapping) else {}
    template = candidate.get("template_candidate") if isinstance(candidate.get("template_candidate"), Mapping) else {}
    patch = template.get("patch") if isinstance(template.get("patch"), Mapping) else {}
    patch_slots = patch.get("slots") if isinstance(patch.get("slots"), Mapping) else {}
    return {**dict(slots), **dict(action_slots), **dict(patch_slots)}


def _preview_slots(candidate: Mapping[str, Any]) -> dict[str, Any]:
    preview = candidate.get("preview") if isinstance(candidate.get("preview"), Mapping) else {}
    slots = preview.get("slots") if isinstance(preview.get("slots"), Mapping) else {}
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    phrase = action.get("phrase_preview") if isinstance(action.get("phrase_preview"), Mapping) else {}
    phrase_slots = phrase.get("slots") if isinstance(phrase.get("slots"), Mapping) else {}
    return {**dict(slots), **dict(phrase_slots)}


def _candidate_side_effect(candidate: Mapping[str, Any], intent: str) -> str:
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    value = str(action.get("side_effect_class") or "").strip()
    if value:
        return value
    if intent in {"desktop.open_weather"}:
        return "read_only"
    if intent in {"desktop.open_modal", "desktop.open_node_modal", "desktop.switch_scenario"}:
        return "ui_navigation"
    if intent.startswith("desktop."):
        return "local_state_change"
    return "unknown"


def _side_effect_policy(candidate: Mapping[str, Any], intent: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    side_effect = _candidate_side_effect(candidate, intent)
    checks: list[dict[str, Any]] = []
    if side_effect in _HIGH_RISK_SIDE_EFFECTS:
        checks.append(
            _check(
                "side_effect_policy",
                False,
                "blocked_high_risk",
                side_effect_class=side_effect,
            )
        )
        return (
            {
                "side_effect_class": side_effect,
                "approval": "blocked",
                "reason": "high_risk_side_effect",
            },
            checks,
        )
    if side_effect == "unknown":
        checks.append(_check("side_effect_policy", True, "unknown_requires_operator_review", side_effect_class=side_effect))
        return (
            {
                "side_effect_class": side_effect,
                "approval": "operator_review_required",
                "reason": "unknown_side_effect",
            },
            checks,
        )
    checks.append(_check("side_effect_policy", side_effect in _SAFE_SIDE_EFFECTS, "allowed", side_effect_class=side_effect))
    return (
        {
            "side_effect_class": side_effect,
            "approval": "operator_apply_allowed",
        },
        checks,
    )


def _regex_abuse_checks(*, candidate: Mapping[str, Any], pattern: str, intent: str) -> list[dict[str, Any]]:
    compact = re.sub(r"\s+", "", pattern).lower()
    checks: list[dict[str, Any]] = []
    overbroad = compact in {".*", "^.*$", ".+", "^.+$", "(.*)", "(.+)"} or len(compact) < 4
    non_read_only = _candidate_side_effect(candidate, intent) != "read_only"
    checks.append(
        _check(
            "regex_scope",
            not (overbroad and non_read_only),
            "overbroad_non_read_only" if overbroad and non_read_only else "bounded",
            overbroad=overbroad,
        )
    )
    haystack = "\n".join(
        str(value or "")
        for value in (
            pattern,
            candidate.get("text"),
            candidate.get("notes"),
            coerce_dict(candidate.get("candidate")).get("description"),
        )
    ).lower()
    markers = [marker for marker in _PROMPT_INJECTION_MARKERS if marker in haystack]
    checks.append(_check("prompt_injection_markers", not markers, "found" if markers else "clean", markers=markers))
    return checks


def _alias_abuse_checks(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    strategy = candidate.get("strategy_candidate") if isinstance(candidate.get("strategy_candidate"), Mapping) else {}
    alias_candidate = strategy.get("proposal") if isinstance(strategy.get("proposal"), Mapping) else {}
    aliases = []
    for key in ("alias", "aliases", "label", "labels"):
        value = alias_candidate.get(key)
        if isinstance(value, str):
            aliases.append(value)
        elif isinstance(value, list):
            aliases.extend(str(item) for item in value if str(item).strip())
    normalized = {re.sub(r"\s+", " ", alias.strip().lower()) for alias in aliases if alias.strip()}
    collisions = sorted(normalized.intersection(_SYSTEM_COMMAND_ALIASES))
    return [_check("system_alias_collision", not collisions, "collision" if collisions else "clean", aliases=collisions)]


def _conflict_checks(candidate: Mapping[str, Any], *, intent: str, target: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    action_intent = str(action.get("intent") or "").strip()
    if action_intent:
        checks.append(
            _check(
                "action_intent_match",
                action_intent == intent,
                "matched" if action_intent == intent else "mismatch",
                expected=intent,
                actual=action_intent,
            )
        )
    owner = _normalize_target(action.get("owner"))
    if owner and target:
        matched = owner == target
        checks.append(
            _check(
                "target_owner_match",
                matched,
                "matched" if matched else "mismatch",
                expected=dict(target),
                actual=dict(owner),
            )
        )
    return checks


def _action_preview_params(candidate: Mapping[str, Any], intent: str) -> dict[str, Any]:
    params = {**_candidate_slots(candidate), **_preview_slots(candidate)}
    repair = coerce_dict(coerce_dict(candidate.get("normalization")).get("llm_proposal_repair"))
    for key in ("modal_id", "scenario_id", "app_id", "node_ref", "skill_id", "webspace_id"):
        value = repair.get(key)
        if isinstance(value, str) and value.strip():
            params[key] = value.strip()
    if intent == "desktop.open_modal" and not params.get("modal_id"):
        rr_slots = _preview_slots(candidate)
        token = rr_slots.get("modal_id")
        if isinstance(token, str) and token.strip():
            params["modal_id"] = token.strip()
    return params


def _run_action_preview(*, webspace_id: str, candidate: Mapping[str, Any], intent: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    action_class = str(action.get("class") or "").strip()
    action_id = str(action.get("action_id") or action.get("system_action_id") or "").strip() or None
    host_action = str(action.get("host_action") or "").strip() or None
    if not action_id and intent in _SYSTEM_ACTION_INTENTS:
        action_id = _SYSTEM_ACTION_INTENTS[intent]
    params = _action_preview_params(candidate, intent)

    if action_id or host_action or intent in _SYSTEM_ACTION_INTENTS:
        preview = preview_interface_action(
            webspace_id=webspace_id,
            action_id=action_id,
            intent=intent if not action_id else None,
            host_action=host_action,
            params=params,
        )
        return preview, [_check("action_preview", bool(preview.get("ok")), str(preview.get("status") or "unknown"))]

    if action_class in {"interface_action", "skill_action", "endpoint_action"}:
        return (
            {
                "ok": True,
                "status": "not_applicable",
                "reason": "custom_route_preview_not_available",
                "params": params,
            },
            [_check("action_preview", True, "custom_route_warning", action_class=action_class)],
        )
    return (
        {"ok": True, "status": "not_required", "reason": "candidate_has_no_dispatch_surface"},
        [_check("action_preview", True, "not_required")],
    )


async def _run_action_preview_async(
    *,
    webspace_id: str,
    candidate: Mapping[str, Any],
    intent: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    action_class = str(action.get("class") or "").strip()
    action_id = str(action.get("action_id") or action.get("system_action_id") or "").strip() or None
    host_action = str(action.get("host_action") or "").strip() or None
    if not action_id and intent in _SYSTEM_ACTION_INTENTS:
        action_id = _SYSTEM_ACTION_INTENTS[intent]
    params = _action_preview_params(candidate, intent)

    if action_id or host_action or intent in _SYSTEM_ACTION_INTENTS:
        try:
            preview = await asyncio.wait_for(
                preview_interface_action_async(
                    webspace_id=webspace_id,
                    action_id=action_id,
                    intent=intent if not action_id else None,
                    host_action=host_action,
                    params=params,
                ),
                timeout=_action_preview_timeout_s(),
            )
        except asyncio.TimeoutError:
            preview = {
                "ok": False,
                "status": "timeout",
                "reason": "action_preview_timeout",
                "params": params,
            }
        except Exception as exc:
            preview = {
                "ok": False,
                "status": "error",
                "reason": "action_preview_error",
                "error": str(exc),
                "params": params,
            }
        return preview, [_check("action_preview", bool(preview.get("ok")), str(preview.get("status") or "unknown"))]

    if action_class in {"interface_action", "skill_action", "endpoint_action"}:
        return (
            {
                "ok": True,
                "status": "not_applicable",
                "reason": "custom_route_preview_not_available",
                "params": params,
            },
            [_check("action_preview", True, "custom_route_warning", action_class=action_class)],
        )
    return (
        {"ok": True, "status": "not_required", "reason": "candidate_has_no_dispatch_surface"},
        [_check("action_preview", True, "not_required")],
    )


def _template_preview(
    *,
    webspace_id: str,
    candidate: Mapping[str, Any],
    target: Mapping[str, Any] | None,
    intent: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    kind = str(candidate.get("kind") or "").strip()
    if kind == "regex_rule":
        if not target:
            return None, [_check("template_preview", True, "target_deferred")]
        preview = preview_template_patch(
            webspace_id=webspace_id,
            operation="add_regex_rule",
            target=target,
            intent=intent,
            text=str(candidate.get("text") or ""),
            pattern=_candidate_pattern(candidate),
            slots=_candidate_slots(candidate),
        )
        return preview, [_check("template_preview", bool(preview.get("ok")), str(preview.get("status") or "unknown"))]

    if kind == "training_example":
        if not target:
            return None, [_check("template_preview", True, "target_deferred")]
        strategy = candidate.get("strategy_candidate") if isinstance(candidate.get("strategy_candidate"), Mapping) else {}
        examples = candidate.get("examples") if isinstance(candidate.get("examples"), list) else strategy.get("examples")
        example = ""
        if isinstance(examples, list):
            for item in examples:
                if str(item).strip():
                    example = str(item).strip()
                    break
        if not example:
            example = str(candidate.get("text") or "").strip()
        preview = preview_template_patch(
            webspace_id=webspace_id,
            operation="save_example",
            target=target,
            intent=intent,
            text=example,
            slots=_candidate_slots(candidate),
        )
        return preview, [_check("template_preview", bool(preview.get("ok")), str(preview.get("status") or "unknown"))]

    return None, [_check("template_preview", True, "not_applicable")]


def validate_candidate_apply(
    *,
    webspace_id: str,
    candidate: Mapping[str, Any],
    payload_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Dry-run validation gate for operator-approved NLU Teacher candidate Apply."""
    candidate_id = str(candidate.get("id") or "").strip()
    kind = str(candidate.get("kind") or "").strip()
    intent = _candidate_intent(candidate)
    target = _candidate_target(candidate, payload_target)
    checks: list[dict[str, Any]] = [
        _check("candidate_id", bool(candidate_id), "present" if candidate_id else "missing"),
        _check("candidate_status", candidate.get("status") != "quarantined", str(candidate.get("status") or "unknown")),
    ]
    if kind in {"regex_rule", "training_example"}:
        checks.append(_check("intent", bool(intent), "present" if intent else "missing"))
    checks.extend(_conflict_checks(candidate, intent=intent, target=target))

    side_effect_policy, policy_checks = _side_effect_policy(candidate, intent)
    checks.extend(policy_checks)

    template_preview, template_checks = _template_preview(
        webspace_id=webspace_id,
        candidate=candidate,
        target=target,
        intent=intent,
    )
    checks.extend(template_checks)

    action_preview, action_checks = _run_action_preview(webspace_id=webspace_id, candidate=candidate, intent=intent)
    checks.extend(action_checks)

    abuse_checks: list[dict[str, Any]] = []
    if kind == "regex_rule":
        pattern = _candidate_pattern(candidate)
        checks.append(_check("regex_pattern", bool(pattern), "present" if pattern else "missing"))
        abuse_checks.extend(_regex_abuse_checks(candidate=candidate, pattern=pattern, intent=intent))
    if kind == "entity_alias":
        abuse_checks.extend(_alias_abuse_checks(candidate))
    checks.extend(abuse_checks)

    failed = [item for item in checks if not bool(item.get("ok"))]
    return {
        "ok": not failed,
        "status": "passed" if not failed else "blocked",
        "webspace_id": webspace_id,
        "candidate_id": candidate_id,
        "kind": kind,
        "intent": intent,
        "target": dict(target or {}),
        "checks": checks,
        "failed_checks": failed,
        "side_effect_policy": side_effect_policy,
        "template_preview": template_preview,
        "action_preview": action_preview,
        "authoring_boundaries": {
            "dry_run": True,
            "dispatch": False,
            "training_mutation": False,
        },
    }


async def validate_candidate_apply_async(
    *,
    webspace_id: str,
    candidate: Mapping[str, Any],
    payload_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Async Apply validation variant that uses async YJS-backed action preview."""
    candidate_id = str(candidate.get("id") or "").strip()
    kind = str(candidate.get("kind") or "").strip()
    intent = _candidate_intent(candidate)
    target = _candidate_target(candidate, payload_target)
    checks: list[dict[str, Any]] = [
        _check("candidate_id", bool(candidate_id), "present" if candidate_id else "missing"),
        _check("candidate_status", candidate.get("status") != "quarantined", str(candidate.get("status") or "unknown")),
    ]
    if kind in {"regex_rule", "training_example"}:
        checks.append(_check("intent", bool(intent), "present" if intent else "missing"))
    checks.extend(_conflict_checks(candidate, intent=intent, target=target))

    side_effect_policy, policy_checks = _side_effect_policy(candidate, intent)
    checks.extend(policy_checks)

    template_preview, template_checks = _template_preview(
        webspace_id=webspace_id,
        candidate=candidate,
        target=target,
        intent=intent,
    )
    checks.extend(template_checks)

    action_preview, action_checks = await _run_action_preview_async(webspace_id=webspace_id, candidate=candidate, intent=intent)
    checks.extend(action_checks)

    abuse_checks: list[dict[str, Any]] = []
    if kind == "regex_rule":
        pattern = _candidate_pattern(candidate)
        checks.append(_check("regex_pattern", bool(pattern), "present" if pattern else "missing"))
        abuse_checks.extend(_regex_abuse_checks(candidate=candidate, pattern=pattern, intent=intent))
    if kind == "entity_alias":
        abuse_checks.extend(_alias_abuse_checks(candidate))
    checks.extend(abuse_checks)

    failed = [item for item in checks if not bool(item.get("ok"))]
    return {
        "ok": not failed,
        "status": "passed" if not failed else "blocked",
        "webspace_id": webspace_id,
        "candidate_id": candidate_id,
        "kind": kind,
        "intent": intent,
        "target": dict(target or {}),
        "checks": checks,
        "failed_checks": failed,
        "side_effect_policy": side_effect_policy,
        "template_preview": template_preview,
        "action_preview": action_preview,
        "authoring_boundaries": {
            "dry_run": True,
            "dispatch": False,
            "training_mutation": False,
        },
    }
