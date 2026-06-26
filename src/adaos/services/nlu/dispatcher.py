from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.yjs.doc import async_read_ydoc
from adaos.services.yjs.webspace import default_webspace_id
from adaos.services.nlu.baseline_content import merge_default_desktop_nlu
from adaos.services.nlu.voice_surface import decode_activation_plan

_log = logging.getLogger("adaos.nlu.dispatcher")
_CONFIDENCE_MIN = float(os.getenv("ADAOS_NLU_CONFIDENCE_MIN", "0.7") or "0.7")
_DISPATCH_DEDUP_TTL_S = float(os.getenv("ADAOS_NLU_DISPATCH_DEDUP_TTL_S", "60") or "60")
_DISPATCHED_REQUESTS: dict[str, float] = {}


def _payload(evt: Any) -> Dict[str, Any]:
    """
    Local event bus passes the payload dict directly into handlers.
    Keep a small adapter for future changes or external bridges.
    """
    if isinstance(evt, dict):
        return evt
    if hasattr(evt, "payload"):
        data = getattr(evt, "payload")  # type: ignore[no-any-return]
        return data if isinstance(data, dict) else {}
    return {}


def _resolve_webspace_id(payload: Mapping[str, Any]) -> str:
    token = (
        payload.get("webspace_id")
        or payload.get("workspace_id")
        or (payload.get("_meta") or {}).get("webspace_id")
        or (payload.get("_meta") or {}).get("workspace_id")
    )
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


async def _resolve_scenario_id(ctx: AgentContext, webspace_id: str) -> str:
    """
    Resolve current scenario id for the given webspace from YDoc.
    Falls back to ``web_desktop`` as the default desktop scenario.
    """
    scenario_id = "web_desktop"
    try:
        async with async_read_ydoc(webspace_id) as ydoc:
            ui_map = ydoc.get_map("ui")
            current = ui_map.get("current_scenario")
            if isinstance(current, str) and current.strip():
                scenario_id = current.strip()
    except Exception:
        _log.debug("failed to resolve current_scenario for webspace=%s", webspace_id, exc_info=True)
    return scenario_id


def _load_scenario_nlu(scenario_id: str) -> Dict[str, Any]:
    """
    Load ``nlu`` section from scenario.json for a given scenario id.
    """
    try:
        content = scenarios_loader.read_content(scenario_id)
    except FileNotFoundError:
        _log.debug("scenario '%s' has no scenario.json content for NLU", scenario_id)
        return merge_default_desktop_nlu(scenario_id, {})
    except Exception:
        _log.warning("failed to read scenario.json for '%s' (nlu)", scenario_id, exc_info=True)
        return merge_default_desktop_nlu(scenario_id, {})

    if not isinstance(content, dict):
        return merge_default_desktop_nlu(scenario_id, {})
    nlu = content.get("nlu") or {}
    return merge_default_desktop_nlu(scenario_id, nlu if isinstance(nlu, Mapping) else {})


def _emit_not_obtained(
    ctx: AgentContext,
    *,
    webspace_id: str,
    scenario_id: str,
    payload: Mapping[str, Any],
    reason: str,
) -> None:
    try:
        out: Dict[str, Any] = {
            "reason": reason,
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
        }
        meta = payload.get("_meta")
        if isinstance(meta, Mapping):
            out["_meta"] = dict(meta)
        if isinstance(payload.get("text"), str) and payload.get("text"):
            out["text"] = payload.get("text")
        if isinstance(payload.get("request_id"), str) and payload.get("request_id"):
            out["request_id"] = payload.get("request_id")
        if isinstance(payload.get("via"), str) and payload.get("via"):
            out["via"] = payload.get("via")
        if isinstance(payload.get("intent"), str) and payload.get("intent"):
            out["intent"] = payload.get("intent")
        if isinstance(payload.get("confidence"), (int, float)):
            out["confidence"] = float(payload.get("confidence"))
        raw = payload.get("_raw")
        if isinstance(raw, Mapping):
            out["_raw"] = raw
            ranking = raw.get("intent_ranking")
            if isinstance(ranking, list):
                out["candidates"] = ranking[:5]
        bus_emit(ctx.bus, "nlp.intent.not_obtained", out, source="nlu.dispatcher")
    except Exception:
        _log.debug("failed to emit nlp.intent.not_obtained", exc_info=True)


def _emit_stage(
    ctx: AgentContext,
    *,
    stage: str,
    status: str,
    webspace_id: str,
    scenario_id: str,
    payload: Mapping[str, Any],
    reason: str | None = None,
    action_target: str | None = None,
) -> None:
    out: Dict[str, Any] = {
        "stage": stage,
        "status": status,
        "webspace_id": webspace_id,
        "reason": reason,
    }
    if scenario_id:
        out["scenario_id"] = scenario_id
    for key in ("text", "intent", "confidence", "via", "request_id"):
        value = payload.get(key)
        if value not in (None, ""):
            out[key] = value
    slots = payload.get("slots")
    if isinstance(slots, Mapping):
        out["slots"] = dict(slots)
    meta = payload.get("_meta")
    if isinstance(meta, Mapping):
        out["_meta"] = dict(meta)
    if action_target:
        out["action_target"] = action_target
    try:
        bus_emit(ctx.bus, "nlu.trace.stage", out, source="nlu.dispatcher")
    except Exception:
        pass


def _resolve_template(value: Any, *, slots: Mapping[str, Any], ctx_vars: Mapping[str, Any], raw: Mapping[str, Any]) -> Any:
    """
    Very small template helper for params:

      - "$slot.city" / "$slots.city" -> slots["city"]
      - "$ctx.webspace_id"           -> ctx_vars["webspace_id"]
      - "$ctx.scenario_id"           -> ctx_vars["scenario_id"]
      - "$text"                      -> raw.get("text") / raw.get("utterance")
    """
    if not isinstance(value, str):
        return value
    if not value.startswith("$"):
        return value

    token = value.strip()
    if token.startswith("$slot.") or token.startswith("$slots."):
        key = token.split(".", 1)[1]
        return slots.get(key)
    if token == "$ctx.webspace_id":
        return ctx_vars.get("webspace_id")
    if token == "$ctx.scenario_id":
        return ctx_vars.get("scenario_id")
    if token == "$text":
        return raw.get("text") or raw.get("utterance")
    return None


def _build_event_payload(
    *,
    base_params: Mapping[str, Any],
    slots: Mapping[str, Any],
    ctx_vars: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Apply simple templating to params and attach minimal context metadata.
    """
    resolved: Dict[str, Any] = {}
    for key, val in base_params.items():
        resolved[key] = _resolve_template(val, slots=slots, ctx_vars=ctx_vars, raw=raw)

    # Attach slots / text for consumers that want them.
    if slots:
        resolved.setdefault("slots", json.loads(json.dumps(slots)))
    text_val = raw.get("text") or raw.get("utterance")
    if isinstance(text_val, str) and text_val:
        resolved.setdefault("text", text_val)

    # Minimal _meta for webspace-aware skills.
    meta = dict(resolved.get("_meta") or {})
    raw_meta = raw.get("_meta")
    if isinstance(raw_meta, Mapping):
        for k, v in raw_meta.items():
            meta.setdefault(k, v)
    if ctx_vars.get("webspace_id"):
        meta.setdefault("webspace_id", ctx_vars["webspace_id"])
    if ctx_vars.get("scenario_id"):
        meta.setdefault("scenario_id", ctx_vars["scenario_id"])
    target_node_id = (
        raw.get("target_node_id")
        or raw.get("node_id")
        or (raw_meta or {}).get("target_node_id")
        or (raw_meta or {}).get("node_id")
    )
    if isinstance(target_node_id, str) and target_node_id.strip():
        resolved.setdefault("target_node_id", target_node_id.strip())
        meta.setdefault("target_node_id", target_node_id.strip())
    if meta:
        resolved["_meta"] = meta

    return resolved


def _route_id(raw: Mapping[str, Any]) -> str:
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), Mapping) else {}
    return str(meta.get("route_id") or meta.get("route") or "").strip()


def _request_id(raw: Mapping[str, Any]) -> str:
    return str(raw.get("request_id") or raw.get("id") or "").strip()


def _dispatch_dedup_key(*, request_id: str, webspace_id: str, route_id: str) -> str:
    rid = str(request_id or "").strip()
    if not rid:
        return ""
    return "\0".join(
        (
            str(webspace_id or default_webspace_id()).strip() or default_webspace_id(),
            str(route_id or "").strip(),
            rid,
        )
    )


def _prune_dispatched_requests(now: float | None = None) -> None:
    if not _DISPATCHED_REQUESTS:
        return
    current = time.time() if now is None else float(now)
    expired = [key for key, ts in _DISPATCHED_REQUESTS.items() if current - float(ts or 0) > _DISPATCH_DEDUP_TTL_S]
    for key in expired:
        _DISPATCHED_REQUESTS.pop(key, None)


def has_dispatched_request(*, request_id: str | None, webspace_id: str | None = None, route_id: str | None = None) -> bool:
    key = _dispatch_dedup_key(
        request_id=str(request_id or "").strip(),
        webspace_id=str(webspace_id or default_webspace_id()).strip() or default_webspace_id(),
        route_id=str(route_id or "").strip(),
    )
    if not key:
        return False
    now = time.time()
    _prune_dispatched_requests(now)
    return key in _DISPATCHED_REQUESTS


def mark_dispatched_request(*, request_id: str | None, webspace_id: str | None = None, route_id: str | None = None) -> bool:
    key = _dispatch_dedup_key(
        request_id=str(request_id or "").strip(),
        webspace_id=str(webspace_id or default_webspace_id()).strip() or default_webspace_id(),
        route_id=str(route_id or "").strip(),
    )
    if not key:
        return True
    now = time.time()
    _prune_dispatched_requests(now)
    if key in _DISPATCHED_REQUESTS:
        return False
    _DISPATCHED_REQUESTS[key] = now
    return True


def _claim_detected_dispatch(ctx: AgentContext, *, webspace_id: str, scenario_id: str, payload: Mapping[str, Any]) -> bool:
    request_id = _request_id(payload)
    if not request_id:
        return True
    route_id = _route_id(payload)
    if mark_dispatched_request(request_id=request_id, webspace_id=webspace_id, route_id=route_id):
        return True
    _emit_stage(
        ctx,
        stage="dispatcher",
        status="duplicate_suppressed",
        webspace_id=webspace_id,
        scenario_id=scenario_id,
        payload=payload,
        reason="request_already_dispatched",
    )
    return False


def _trusted_teacher_dispatch(raw: Mapping[str, Any]) -> bool:
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), Mapping) else {}
    return bool(meta.get("nlu_teacher_dispatch"))


def _teacher_skill_action(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    if not _trusted_teacher_dispatch(raw):
        return None
    action = raw.get("action_candidate") if isinstance(raw.get("action_candidate"), Mapping) else {}
    if not action:
        return None
    action_class = str(action.get("class") or "").strip()
    side_effect = str(action.get("side_effect_class") or "").strip()
    if action_class != "skill_action" and side_effect != "skill_action":
        return None

    owner = action.get("owner") if isinstance(action.get("owner"), Mapping) else {}
    owner_type = str(owner.get("type") or action.get("owner_type") or "").strip()
    skill = str(
        owner.get("id")
        if owner_type == "skill"
        else action.get("skill") or action.get("skill_id") or action.get("skill_name") or ""
    ).strip()
    explicit_target = str(action.get("target") or "").strip()
    action_id = str(action.get("id") or raw.get("intent") or "").strip()
    tool = str(action.get("tool") or action.get("tool_name") or action.get("method") or "").strip()
    if not tool:
        token = explicit_target or action_id
        tool = token.rsplit(".", 1)[-1].strip() if token else ""
    if not skill or not tool:
        return None

    params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
    if not params:
        params = action.get("payload") if isinstance(action.get("payload"), Mapping) else {}
    return {
        "type": "callSkill",
        "skill": skill,
        "tool": tool,
        "target": f"{skill}.{tool}",
        "params": dict(params),
    }


def _normalize_skill_nlu_action(action: Mapping[str, Any], *, skill_name: str) -> dict[str, Any] | None:
    action_type = str(action.get("type") or "").strip()
    skill = str(action.get("skill") or action.get("skill_id") or skill_name or "").strip()
    tool = str(action.get("tool") or action.get("tool_name") or action.get("method") or "").strip()
    target = str(action.get("target") or "").strip()
    if not tool and skill and target.startswith(f"{skill}."):
        tool = target.rsplit(".", 1)[-1].strip()

    if action_type in {"skillTool", "callSkillTool"} or (skill and tool):
        if not skill or not tool:
            return None
        params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
        return {
            "type": "skillTool",
            "skill": skill,
            "tool": tool,
            "target": target or f"{skill}.{tool}",
            "params": dict(params),
        }

    if action_type in {"callSkill", "callHost"} and target:
        params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
        return {"type": action_type, "target": target, "params": dict(params)}
    return None


def _skill_nlu_actions_for_intent(ctx: AgentContext, intent: str) -> list[dict[str, Any]]:
    intent = str(intent or "").strip()
    if not intent:
        return []
    try:
        skill_yamls = list(Path(ctx.paths.skills_dir()).glob("*/skill.yaml"))
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for path in skill_yamls:
        skill_name = path.parent.name
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(payload, Mapping):
            continue
        nlu = payload.get("nlu") if isinstance(payload.get("nlu"), Mapping) else {}
        intents = nlu.get("intents") if isinstance(nlu, Mapping) else None
        specs: list[Mapping[str, Any]] = []
        if isinstance(intents, Mapping):
            spec = intents.get(intent)
            if isinstance(spec, Mapping):
                specs.append(spec)
        elif isinstance(intents, list):
            for item in intents:
                if not isinstance(item, Mapping):
                    continue
                name = item.get("name") or item.get("intent")
                if isinstance(name, str) and name.strip() == intent:
                    specs.append(item)
        for spec in specs:
            actions = spec.get("actions")
            if not isinstance(actions, list):
                continue
            for action in actions:
                if not isinstance(action, Mapping):
                    continue
                normalized = _normalize_skill_nlu_action(action, skill_name=skill_name)
                if normalized:
                    out.append(normalized)
    return out


def _humanize_action_label(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    token = token.removesuffix(".query")
    token = token.removesuffix("_modal")
    token = token.replace(".", " ").replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", token).strip()


def _emit_voice_action_ack(
    ctx: AgentContext,
    *,
    target: str,
    payload: Mapping[str, Any],
    webspace_id: str,
    raw: Mapping[str, Any],
) -> None:
    if _route_id(raw) != "voice_chat":
        return
    text = ""
    if target == "desktop.modal.open":
        label = _humanize_action_label(payload.get("modal_id") or payload.get("modalId"))
        text = f"Открываю {label}." if label else "Открываю окно."
    if not text:
        return
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), Mapping) else {}
    bus_emit(
        ctx.bus,
        "io.out.chat.append",
        {
            "id": "",
            "from": "hub",
            "text": text,
            "ts": None,
            "_meta": {"webspace_id": webspace_id, **dict(meta), "route_id": "voice_chat"},
        },
        source="nlu.dispatcher",
    )


def _emit_action_outcome(
    ctx: AgentContext,
    *,
    event_type: str,
    intent: str,
    action_type: str,
    target: str,
    webspace_id: str,
    scenario_id: str,
    payload: Mapping[str, Any] | None,
    raw: Mapping[str, Any],
    reason: str | None = None,
) -> None:
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), Mapping) else {}
    out: Dict[str, Any] = {
        "intent": intent,
        "action_type": action_type,
        "target": target,
        "webspace_id": webspace_id,
        "scenario_id": scenario_id,
        "status": "failed" if event_type.endswith("failed") else "emitted",
        "_meta": dict(meta),
    }
    if payload is not None:
        out["action_payload"] = dict(payload)
    for key in ("text", "request_id", "via", "confidence"):
        value = raw.get(key)
        if value not in (None, ""):
            out[key] = value
    slots = raw.get("slots")
    if isinstance(slots, Mapping):
        out["slots"] = dict(slots)
    if reason:
        out["reason"] = reason
    try:
        bus_emit(ctx.bus, event_type, out, source="nlu.dispatcher")
    except Exception:
        _log.debug("failed to emit %s", event_type, exc_info=True)


def _parse_plan_payload(value: Any) -> list[dict[str, Any]]:
    return decode_activation_plan(value)


def _activation_event_type(step_type: str) -> str | None:
    if step_type == "desktop.open_modal":
        return "desktop.modal.open"
    if step_type == "ui.state.set":
        return "ui.state.set"
    if step_type == "ui.focus_widget":
        return "ui.focus_widget"
    if step_type == "ui.affordance.activate":
        return "ui.affordance.activate"
    if step_type in {"callSkill", "callHost"}:
        return "__nlu_call__"
    return None


def _emit_voice_capability_ack(
    ctx: AgentContext,
    *,
    payload: Mapping[str, Any],
    webspace_id: str,
) -> None:
    if _route_id(payload) != "voice_chat":
        return
    label = _humanize_action_label(payload.get("voice_label") or payload.get("capability_id") or payload.get("affordance_id"))
    text = f"Открываю {label}." if label else "Открываю запрошенный инструмент."
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    try:
        bus_emit(
            ctx.bus,
            "io.out.chat.append",
            {
                "id": "",
                "from": "hub",
                "text": text,
                "ts": None,
                "_meta": {"webspace_id": webspace_id, **dict(meta), "route_id": "voice_chat"},
            },
            source="nlu.dispatcher",
        )
    except Exception:
        _log.debug("failed to emit voice capability ack", exc_info=True)


@subscribe("voice.capability.activate")
def _on_voice_capability_activate(evt: Any) -> None:
    payload = _payload(evt)
    ctx = get_ctx()
    webspace_id = _resolve_webspace_id(payload)
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    slots = payload.get("slots") if isinstance(payload.get("slots"), Mapping) else {}
    plan = _parse_plan_payload(payload.get("activation_plan") or slots.get("activation_plan"))
    scenario_id = str(meta.get("scenario_id") or payload.get("scenario_id") or "").strip()
    emitted = 0
    failures: list[dict[str, Any]] = []
    if not plan:
        failures.append({"reason": "activation_plan_empty"})

    for index, step in enumerate(plan):
        step_type = str(step.get("type") or "").strip()
        event_type = _activation_event_type(step_type)
        params = step.get("params") if isinstance(step.get("params"), Mapping) else {}
        if not event_type:
            failures.append({"index": index, "type": step_type, "reason": "unsupported_activation_step"})
            continue
        step_payload = {
            **dict(params),
            "webspace_id": webspace_id,
            "activation_step": index,
            "activation_type": step_type,
            "capability_id": payload.get("capability_id") or slots.get("capability_id"),
            "affordance_id": payload.get("affordance_id") or slots.get("affordance_id"),
            "_meta": {
                **dict(meta),
                "webspace_id": webspace_id,
                "voice_capability_activation": True,
                "activation_step": index,
                "activation_type": step_type,
            },
        }
        if payload.get("text") not in (None, ""):
            step_payload.setdefault("text", payload.get("text"))
        if slots:
            step_payload.setdefault("slots", dict(slots))
        try:
            if event_type == "__nlu_call__":
                target = str(step.get("target") or params.get("target") or "").strip()
                if not target:
                    failures.append({"index": index, "type": step_type, "reason": "missing_target"})
                    continue
                bus_emit(ctx.bus, target, step_payload, source="nlu.dispatcher.voice_capability")
            else:
                bus_emit(ctx.bus, event_type, step_payload, source="nlu.dispatcher.voice_capability")
            emitted += 1
        except Exception:
            failures.append({"index": index, "type": step_type, "reason": "bus_emit_failed"})
            _log.warning("failed to emit voice capability step type=%s", step_type, exc_info=True)

    if emitted > 0:
        _emit_voice_capability_ack(ctx, payload=payload, webspace_id=webspace_id)
    _emit_action_outcome(
        ctx,
        event_type="nlu.action.dispatch_failed" if failures and not emitted else "nlu.action.dispatched",
        intent="voice.capability.activate",
        action_type="callHost",
        target="voice.capability.activate",
        webspace_id=webspace_id,
        scenario_id=scenario_id,
        payload={
            "capability_id": payload.get("capability_id") or slots.get("capability_id"),
            "affordance_id": payload.get("affordance_id") or slots.get("affordance_id"),
            "activation_steps": len(plan),
            "emitted_steps": emitted,
            "failures": failures,
        },
        raw=payload,
        reason="activation_plan_empty" if failures and not plan else ("activation_step_failed" if failures and not emitted else None),
    )


def _execute_action(
    ctx: AgentContext,
    *,
    action: Mapping[str, Any],
    intent: str,
    scenario_id: str,
    webspace_id: str,
    slots: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> None:
    """
    Execute a single NLU action mapping. For MVP we support:

      - type: "callSkill" | "callHost"
        target: event type (e.g. "desktop.toggleInstall", "weather.city_changed")
        params: dict with optional templates.
    """
    action_type = str(action.get("type") or "").strip() or "callSkill"
    if action_type in {"skillTool", "callSkillTool"} or (
        action_type == "callSkill" and action.get("skill") and action.get("tool")
    ):
        _execute_skill_tool_action(
            ctx,
            action=action,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            raw=raw,
        )
        return

    target = str(action.get("target") or "").strip()
    if not target:
        _log.debug("nlu.intent %s: action missing target", intent)
        _emit_action_outcome(
            ctx,
            event_type="nlu.action.dispatch_failed",
            intent=intent,
            action_type=action_type,
            target="",
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=None,
            raw=raw,
            reason="missing_target",
        )
        return

    base_params = action.get("params") or {}
    if not isinstance(base_params, Mapping):
        base_params = {}

    ctx_vars = {"webspace_id": webspace_id, "scenario_id": scenario_id}
    payload = _build_event_payload(base_params=base_params, slots=slots, ctx_vars=ctx_vars, raw=raw)
    if _route_id(raw) == "voice_chat" and target == "desktop.modal.open":
        meta = dict(payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {})
        meta["_voice_chat_ack_suppressed"] = True
        payload["_meta"] = meta

    # For now callSkill/callHost are both modelled as bus events.
    try:
        bus_emit(ctx.bus, target, payload, source="nlu.dispatcher")
        _emit_voice_action_ack(ctx, target=target, payload=payload, webspace_id=webspace_id, raw=raw)
        _emit_action_outcome(
            ctx,
            event_type="nlu.action.dispatched",
            intent=intent,
            action_type=action_type,
            target=target,
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=payload,
            raw=raw,
        )
        _log.debug(
            "nlu.intent %s dispatched action type=%s target=%s webspace=%s scenario=%s",
            intent,
            action_type,
            target,
            webspace_id,
            scenario_id,
        )
    except Exception:
        _emit_action_outcome(
            ctx,
            event_type="nlu.action.dispatch_failed",
            intent=intent,
            action_type=action_type,
            target=target,
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=payload,
            raw=raw,
            reason="bus_emit_failed",
        )
        _log.warning(
            "failed to dispatch NLU action intent=%s type=%s target=%s webspace=%s scenario=%s",
            intent,
            action_type,
            target,
            webspace_id,
            scenario_id,
            exc_info=True,
        )


def _run_skill_tool(ctx: AgentContext, skill: str, tool: str, payload: Mapping[str, Any]) -> Any:
    from adaos.adapters.db import SqliteSkillRegistry
    from adaos.services.skill.manager import SkillManager

    mgr = SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )
    return mgr.run_tool(skill, tool, dict(payload))


def _execute_skill_tool_action(
    ctx: AgentContext,
    *,
    action: Mapping[str, Any],
    intent: str,
    scenario_id: str,
    webspace_id: str,
    slots: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> None:
    skill = str(action.get("skill") or "").strip()
    tool = str(action.get("tool") or "").strip()
    target = str(action.get("target") or f"{skill}.{tool}").strip()
    params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
    payload = _build_event_payload(
        base_params=params,
        slots=slots,
        ctx_vars={"webspace_id": webspace_id, "scenario_id": scenario_id},
        raw=raw,
    )
    payload.setdefault("webspace_id", webspace_id)
    try:
        result = _run_skill_tool(ctx, skill, tool, payload)
    except Exception as exc:
        _emit_action_outcome(
            ctx,
            event_type="nlu.action.dispatch_failed",
            intent=intent,
            action_type="callSkill",
            target=target,
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=payload,
            raw=raw,
            reason=f"tool_run_failed:{type(exc).__name__}",
        )
        _log.warning(
            "failed to execute NLU teacher skill action intent=%s target=%s webspace=%s scenario=%s",
            intent,
            target,
            webspace_id,
            scenario_id,
            exc_info=True,
        )
        return

    if isinstance(result, Mapping) and result.get("ok") is False:
        reason = str(result.get("error") or result.get("reason") or "tool_returned_not_ok").strip()
        _emit_action_outcome(
            ctx,
            event_type="nlu.action.dispatch_failed",
            intent=intent,
            action_type="callSkill",
            target=target,
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=payload,
            raw=raw,
            reason=reason or "tool_returned_not_ok",
        )
        return

    _emit_action_outcome(
        ctx,
        event_type="nlu.action.dispatched",
        intent=intent,
        action_type="callSkill",
        target=target,
        webspace_id=webspace_id,
        scenario_id=scenario_id,
        payload=payload,
        raw=raw,
    )


def _dispatch_teacher_skill_action_if_available(
    ctx: AgentContext,
    *,
    intent: str,
    scenario_id: str,
    webspace_id: str,
    slots: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    action = _teacher_skill_action(payload)
    if not action:
        return False
    target = str(action.get("target") or "").strip()
    _emit_stage(
        ctx,
        stage="dispatcher",
        status="action",
        webspace_id=webspace_id,
        scenario_id=scenario_id,
        payload=payload,
        reason="nlu_teacher_skill_action",
        action_target=target or None,
    )
    _execute_skill_tool_action(
        ctx,
        action=action,
        intent=intent,
        scenario_id=scenario_id,
        webspace_id=webspace_id,
        slots=slots,
        raw=payload,
    )
    return True


def _dispatch_skill_nlu_action_if_available(
    ctx: AgentContext,
    *,
    intent: str,
    scenario_id: str,
    webspace_id: str,
    slots: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    actions = _skill_nlu_actions_for_intent(ctx, intent)
    if not actions:
        return False
    for action in actions:
        target = str(action.get("target") or "").strip()
        _emit_stage(
            ctx,
            stage="dispatcher",
            status="action",
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=payload,
            reason="skill_nlu_action",
            action_target=target or None,
        )
        _execute_action(
            ctx,
            action=action,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            raw=payload,
        )
    return True


@subscribe("nlp.intent.detected")
async def _on_nlp_intent_detected(evt: Any) -> None:
    """
    Entry point for generic NLU results coming from external interpreters.

    Payload (see docs/concepts/event_mgmnt.md, nlp.intent.detected.v1):
      - intent: string
      - slots: dict
      - locale: string
      - text / utterance: original text (optional)
      - webspace_id / workspace_id / _meta.webspace_id: optional
    """
    payload = _payload(evt)
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        return

    slots_raw = payload.get("slots") or {}
    slots: Dict[str, Any] = slots_raw if isinstance(slots_raw, dict) else {}

    ctx = get_ctx()
    webspace_id = _resolve_webspace_id(payload)
    scenario_id = await _resolve_scenario_id(ctx, webspace_id)

    confidence = payload.get("confidence")
    if isinstance(confidence, (int, float)) and float(confidence) < _CONFIDENCE_MIN:
        _emit_stage(
            ctx,
            stage="dispatcher",
            status="reject",
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=payload,
            reason=f"low_confidence<{_CONFIDENCE_MIN}",
        )
        _emit_not_obtained(
            ctx,
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            payload=payload,
            reason=f"low_confidence<{_CONFIDENCE_MIN}",
        )
        return

    nlu_cfg = _load_scenario_nlu(scenario_id)
    intents_cfg = nlu_cfg.get("intents") if isinstance(nlu_cfg, dict) else None
    if not isinstance(intents_cfg, dict):
        _log.debug("nlu.intent %s: scenario=%s has no nlu.intents section", intent, scenario_id)
        if _teacher_skill_action(payload):
            if not _claim_detected_dispatch(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload):
                return
        if _dispatch_teacher_skill_action_if_available(
            ctx,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            payload=payload,
        ):
            return
        if _skill_nlu_actions_for_intent(ctx, intent):
            if not _claim_detected_dispatch(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload):
                return
        if _dispatch_skill_nlu_action_if_available(
            ctx,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            payload=payload,
        ):
            return
        _emit_stage(ctx, stage="dispatcher", status="reject", webspace_id=webspace_id, scenario_id=scenario_id, payload=payload, reason="no_intents_config")
        _emit_not_obtained(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload, reason="no_intents_config")
        return

    intent_cfg = intents_cfg.get(intent)
    if not isinstance(intent_cfg, Mapping):
        _log.debug("nlu.intent %s: no mapping in scenario=%s", intent, scenario_id)
        if _teacher_skill_action(payload):
            if not _claim_detected_dispatch(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload):
                return
        if _dispatch_teacher_skill_action_if_available(
            ctx,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            payload=payload,
        ):
            return
        if _skill_nlu_actions_for_intent(ctx, intent):
            if not _claim_detected_dispatch(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload):
                return
        if _dispatch_skill_nlu_action_if_available(
            ctx,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            payload=payload,
        ):
            return
        _emit_stage(ctx, stage="dispatcher", status="reject", webspace_id=webspace_id, scenario_id=scenario_id, payload=payload, reason="no_intent_mapping")
        _emit_not_obtained(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload, reason="no_intent_mapping")
        return

    actions_cfg = intent_cfg.get("actions") or []
    if not isinstance(actions_cfg, list) or not actions_cfg:
        _log.debug("nlu.intent %s: scenario=%s has no actions", intent, scenario_id)
        if _teacher_skill_action(payload):
            if not _claim_detected_dispatch(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload):
                return
        if _dispatch_teacher_skill_action_if_available(
            ctx,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            payload=payload,
        ):
            return
        if _skill_nlu_actions_for_intent(ctx, intent):
            if not _claim_detected_dispatch(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload):
                return
        if _dispatch_skill_nlu_action_if_available(
            ctx,
            intent=intent,
            scenario_id=scenario_id,
            webspace_id=webspace_id,
            slots=slots,
            payload=payload,
        ):
            return
        _emit_stage(ctx, stage="dispatcher", status="reject", webspace_id=webspace_id, scenario_id=scenario_id, payload=payload, reason="no_actions")
        _emit_not_obtained(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload, reason="no_actions")
        return

    if not _claim_detected_dispatch(ctx, webspace_id=webspace_id, scenario_id=scenario_id, payload=payload):
        return

    for action in actions_cfg:
        if isinstance(action, Mapping):
            target = str(action.get("target") or "").strip()
            _emit_stage(
                ctx,
                stage="dispatcher",
                status="action",
                webspace_id=webspace_id,
                scenario_id=scenario_id,
                payload=payload,
                reason=str(action.get("type") or "callSkill"),
                action_target=target or None,
            )
            _execute_action(
                ctx,
                action=action,
                intent=intent,
                scenario_id=scenario_id,
                webspace_id=webspace_id,
                slots=slots,
                raw=payload,
            )
