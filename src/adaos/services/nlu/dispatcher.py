from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.nlu.dispatcher")


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
        async with async_get_ydoc(webspace_id) as ydoc:
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
        return {}
    except Exception:
        _log.warning("failed to read scenario.json for '%s' (nlu)", scenario_id, exc_info=True)
        return {}

    if not isinstance(content, dict):
        return {}
    nlu = content.get("nlu") or {}
    return nlu if isinstance(nlu, dict) else {}


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
    if ctx_vars.get("webspace_id"):
        meta.setdefault("webspace_id", ctx_vars["webspace_id"])
    if ctx_vars.get("scenario_id"):
        meta.setdefault("scenario_id", ctx_vars["scenario_id"])
    if meta:
        resolved["_meta"] = meta

    return resolved


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
    target = str(action.get("target") or "").strip()
    if not target:
        _log.debug("nlu.intent %s: action missing target", intent)
        return

    base_params = action.get("params") or {}
    if not isinstance(base_params, Mapping):
        base_params = {}

    ctx_vars = {"webspace_id": webspace_id, "scenario_id": scenario_id}
    payload = _build_event_payload(base_params=base_params, slots=slots, ctx_vars=ctx_vars, raw=raw)

    # For now callSkill/callHost are both modelled as bus events.
    try:
        bus_emit(ctx.bus, target, payload, source="nlu.dispatcher")
        _log.debug(
            "nlu.intent %s dispatched action type=%s target=%s webspace=%s scenario=%s",
            intent,
            action_type,
            target,
            webspace_id,
            scenario_id,
        )
    except Exception:
        _log.warning(
            "failed to dispatch NLU action intent=%s type=%s target=%s webspace=%s scenario=%s",
            intent,
            action_type,
            target,
            webspace_id,
            scenario_id,
            exc_info=True,
        )


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
    nlu_cfg = _load_scenario_nlu(scenario_id)
    intents_cfg = nlu_cfg.get("intents") if isinstance(nlu_cfg, dict) else None
    if not isinstance(intents_cfg, dict):
        _log.debug("nlu.intent %s: scenario=%s has no nlu.intents section", intent, scenario_id)
        return

    intent_cfg = intents_cfg.get(intent)
    if not isinstance(intent_cfg, Mapping):
        _log.debug("nlu.intent %s: no mapping in scenario=%s", intent, scenario_id)
        return

    actions_cfg = intent_cfg.get("actions") or []
    if not isinstance(actions_cfg, list) or not actions_cfg:
        _log.debug("nlu.intent %s: scenario=%s has no actions", intent, scenario_id)
        return

    for action in actions_cfg:
        if isinstance(action, Mapping):
            _execute_action(
                ctx,
                action=action,
                intent=intent,
                scenario_id=scenario_id,
                webspace_id=webspace_id,
                slots=slots,
                raw=payload,
            )

