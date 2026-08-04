from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.nlu.teacher_overlay_store import (
    create_promotion_candidate,
    list_example_overlays,
    overlay_store_path,
    upsert_example_overlay,
)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def system_action_feedback_path(ctx: AgentContext | None = None) -> Path:
    ctx = ctx or get_ctx()
    return (Path(ctx.paths.state_dir()) / "interpreter" / "system_action_feedback.jsonl").resolve()


def _normalize_target(target: Mapping[str, Any] | None, *, intent: str) -> dict[str, Any]:
    target = target if isinstance(target, Mapping) else {}
    target_type = str(target.get("type") or "").strip()
    target_id = str(target.get("id") or "").strip()
    if target_type == "system_action" and not target_id:
        try:
            from adaos.services.nlu.system_actions_catalog import find_system_action_by_intent

            action = find_system_action_by_intent(intent)
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                target_id = action["id"]
        except Exception:
            target_id = ""
    out = {"type": target_type}
    if target_id:
        out["id"] = target_id
    return out


def _skill_tool_action_from_candidate(
    *,
    skill_name: str,
    intent: str,
    action_candidate: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(action_candidate, Mapping):
        return None
    action_class = str(action_candidate.get("class") or "").strip()
    side_effect = str(action_candidate.get("side_effect_class") or "").strip()
    if action_class != "skill_action" and side_effect != "skill_action":
        return None

    owner = action_candidate.get("owner") if isinstance(action_candidate.get("owner"), Mapping) else {}
    owner_type = str(owner.get("type") or action_candidate.get("owner_type") or "").strip()
    owner_id = str(owner.get("id") or action_candidate.get("skill") or action_candidate.get("skill_id") or "").strip()
    if owner_type and owner_type != "skill":
        return None
    if owner_id and owner_id != skill_name:
        return None

    token = str(action_candidate.get("target") or action_candidate.get("id") or intent or "").strip()
    tool = str(action_candidate.get("tool") or action_candidate.get("tool_name") or action_candidate.get("method") or "").strip()
    if not tool and token:
        tool = token.rsplit(".", 1)[-1].strip()
    if not tool:
        return None

    params = action_candidate.get("params") if isinstance(action_candidate.get("params"), Mapping) else {}
    return {
        "type": "skillTool",
        "skill": skill_name,
        "tool": tool,
        "target": f"{skill_name}.{tool}",
        "params": dict(params),
    }


def _store_example(
    *,
    ctx: AgentContext,
    target: Mapping[str, Any],
    intent: str,
    example: str,
    slots: Mapping[str, Any] | None = None,
    action: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    privacy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        overlay = upsert_example_overlay(
            ctx=ctx,
            target=target,
            intent=intent,
            text=example,
            slots=slots,
            action=action,
            promotion=promotion,
            provenance=provenance,
            privacy=privacy,
        )
        candidate = create_promotion_candidate(overlay, ctx=ctx)
    except Exception as exc:
        return {"ok": False, "reason": "overlay_write_failed", "error": str(exc)}
    result = {
        "ok": True,
        "target": dict(target),
        "path": str(overlay_store_path(ctx)),
        "overlay": overlay,
        "storage": "runtime_overlay",
    }
    if candidate is not None:
        result["promotion_candidate"] = candidate
    if action is not None:
        result["action"] = dict(action)
    return result


def write_scenario_example(
    *,
    scenario_id: str,
    intent: str,
    example: str,
    ctx: AgentContext | None = None,
    slots: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    privacy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = ctx or get_ctx()
    if not str(scenario_id or "").strip() or not str(intent or "").strip() or not str(example or "").strip():
        return {"ok": False, "reason": "missing_scenario_intent_or_example"}
    return _store_example(
        ctx=context,
        target={"type": "scenario", "id": str(scenario_id).strip()},
        intent=str(intent).strip(),
        example=str(example).strip(),
        slots=slots,
        promotion=promotion,
        provenance=provenance,
        privacy=privacy,
    )


def write_skill_example(
    *,
    ctx: AgentContext | None = None,
    skill_name: str,
    intent: str,
    example: str,
    action_candidate: Mapping[str, Any] | None = None,
    slots: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    privacy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = ctx or get_ctx()
    skill_id = str(skill_name or "").strip()
    intent_id = str(intent or "").strip()
    text = str(example or "").strip()
    if not skill_id or not intent_id or not text:
        return {"ok": False, "reason": "missing_skill_intent_or_example"}
    action = _skill_tool_action_from_candidate(
        skill_name=skill_id,
        intent=intent_id,
        action_candidate=action_candidate,
    )
    return _store_example(
        ctx=context,
        target={"type": "skill", "id": skill_id},
        intent=intent_id,
        example=text,
        slots=slots,
        action=action,
        promotion=promotion,
        provenance=provenance,
        privacy=privacy,
    )


def append_system_action_feedback(
    *,
    ctx: AgentContext | None = None,
    action_id: str,
    intent: str,
    example: str,
    slots: Mapping[str, Any] | None = None,
    audit: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    privacy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    action_id = str(action_id or "").strip()
    intent = str(intent or "").strip()
    example = str(example or "").strip()
    if not action_id or not intent or not example:
        return {"ok": False, "reason": "missing_action_intent_or_example"}

    return _store_example(
        ctx=ctx,
        target={"type": "system_action", "id": action_id},
        intent=intent,
        example=example,
        slots=slots,
        promotion=promotion,
        provenance={**dict(provenance or {}), "audit": dict(audit or {})},
        privacy=privacy,
    )


def collect_system_action_feedback_examples(ctx: AgentContext | None = None) -> dict[str, list[str]]:
    context = ctx or get_ctx()
    path = system_action_feedback_path(ctx)
    out: dict[str, list[str]] = {}
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            intent = item.get("intent")
            example = item.get("example")
            if isinstance(intent, str) and isinstance(example, str):
                out.setdefault(intent.strip(), [])
                out[intent.strip()] = _dedupe_keep_order([*out[intent.strip()], example])
    for item in list_example_overlays(context):
        target = item.get("target") if isinstance(item.get("target"), Mapping) else {}
        if target.get("type") != "system_action":
            continue
        intent = str(item.get("intent") or "").strip()
        example = str(item.get("text") or "").strip()
        if intent and example:
            out.setdefault(intent, [])
            out[intent] = _dedupe_keep_order([*out[intent], example])
    return out


def save_feedback_example(
    *,
    ctx: AgentContext | None = None,
    target: Mapping[str, Any] | None,
    intent: str,
    example: str,
    slots: Mapping[str, Any] | None = None,
    audit: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    privacy: Mapping[str, Any] | None = None,
    action_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    intent = str(intent or "").strip()
    example = str(example or "").strip()
    normalized_target = _normalize_target(target, intent=intent)
    target_type = normalized_target.get("type")
    target_id = normalized_target.get("id")
    if target_type in {"scenario", "skill"} and isinstance(target_id, str):
        action = (
            _skill_tool_action_from_candidate(
                skill_name=target_id,
                intent=intent,
                action_candidate=action_candidate,
            )
            if target_type == "skill"
            else None
        )
        result = _store_example(
            ctx=ctx,
            target={"type": target_type, "id": target_id},
            intent=intent,
            example=example,
            slots=slots,
            action=action,
            promotion=promotion,
            provenance=provenance,
            privacy=privacy,
        )
    elif target_type == "system_action" and isinstance(target_id, str):
        result = append_system_action_feedback(
            ctx=ctx,
            action_id=target_id,
            intent=intent,
            example=example,
            slots=slots,
            audit=audit,
            promotion=promotion,
            provenance=provenance,
            privacy=privacy,
        )
    else:
        return {"ok": False, "reason": "unsupported_or_missing_target", "target": normalized_target}
    if result.get("ok"):
        result["intent"] = intent
        result["example"] = example
        if isinstance(promotion, Mapping) and promotion:
            result["promotion"] = dict(promotion)
        if isinstance(provenance, Mapping) and provenance:
            result["provenance"] = dict(provenance)
        if isinstance(privacy, Mapping) and privacy:
            result["privacy"] = dict(privacy)
    return result
