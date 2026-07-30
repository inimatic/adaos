from __future__ import annotations

"""
Chat IO -> canonical dialog bridge.

This helper subscribes to generic io.input envelopes (e.g. from Telegram)
and publishes neutral ``dialog.user_message`` events. RouterService then owns
conversation selection, Builder/channel dispatch, NLU fallback, and response
projection. Transports never invoke Builder or NLU directly.
"""

import logging
import os
from typing import Any, Dict, Mapping, Optional, Tuple

from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import LocalEventBus
from adaos.services import conversation_store
from adaos.services import conversation_interactions
from adaos.domain import Event

_log = logging.getLogger("adaos.chat_io.nlu_bridge")

def _text(value: Any) -> str:
    return str(value or "").strip()


def _extract_text_io_input(env: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """
    Extract (text, explicitly routed webspace_id, transport meta).

    Telegram normally targets the hub's default dialog webspace. A trusted
    bridge may include an explicit ``route.webspace_id``; arbitrary message
    text is never parsed as a webspace identifier.
    """
    # The primary NATS path wraps ChatInputEvent in an IO envelope. The
    # fallback HTTP webhook forwards ChatInputEvent directly. Normalize both
    # representations here so transport failover cannot change dialog
    # semantics or Unicode handling.
    direct_input = _text(env.get("type")) == "text" and bool(_text(env.get("source")))
    payload = env if direct_input else (env.get("payload") or {})
    if not isinstance(payload, Mapping):
        return None, None, {}
    if (payload.get("type") or "").strip() != "text":
        return None, None, {}
    inner = payload.get("payload") or {}
    if not isinstance(inner, Mapping):
        return None, None, {}
    text = inner.get("text") or ""
    if not isinstance(text, str) or not text.strip():
        return None, None, {}

    # Preserve chat routing context so responses can be sent back to the same chat.
    # Envelope schema (from Root/Telegram): payload has bot_id/chat_id/user_id/hub_id,
    # plus optional meta/route blocks.
    source = _text(payload.get("source"))
    bot_id = _text(payload.get("bot_id"))
    hub_id = _text(payload.get("hub_id"))
    chat_id = _text(payload.get("chat_id"))
    user_id = _text(payload.get("user_id"))
    update_id = _text(payload.get("update_id"))
    meta: Dict[str, Any] = {
        key: value
        for key, value in {
            "io_type": source,
            "bot_id": bot_id,
            "hub_id": hub_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "update_id": update_id,
        }.items()
        if value
    }
    if source == "telegram" and chat_id:
        meta.update(
            {
                "route_id": "telegram",
                "channel_capability_profile": "limited_chat",
                "channel_capabilities": {
                    "text": True,
                    "compact_status": True,
                    "deterministic_actions": True,
                    "rich_views": False,
                    "deep_links": True,
                },
            }
        )

    env_meta = (
        env.get("meta")
        if not direct_input and isinstance(env.get("meta"), Mapping)
        else {}
    )
    trace_id = _text(env_meta.get("trace_id"))
    if trace_id:
        meta["trace_id"] = trace_id
    event_id = _text(env.get("event_id")) or _text(payload.get("event_id"))
    if event_id:
        meta["transport_event_id"] = event_id
    dedup_key = _text(env.get("dedup_key")) or _text(payload.get("dedup_key")) or ":".join(
        value for value in ("telegram", bot_id, chat_id, update_id) if value
    )
    if dedup_key:
        meta["dedup_key"] = dedup_key
        meta["idempotency_key"] = f"transport:{dedup_key}"
    request_id = update_id or event_id
    if request_id:
        meta["request_id"] = f"telegram:{bot_id or 'bot'}:{chat_id or 'chat'}:{request_id}"

    msg_meta = inner.get("meta") if isinstance(inner.get("meta"), Mapping) else {}
    msg_id = msg_meta.get("msg_id")
    try:
        if msg_id is not None and str(msg_id).strip():
            meta["reply_to"] = int(msg_id)
    except (TypeError, ValueError):
        pass
    lang = _text(msg_meta.get("lang"))
    if lang:
        meta["lang"] = lang

    route = payload.get("route") if isinstance(payload.get("route"), Mapping) else {}
    if not route and isinstance(env_meta.get("route"), Mapping):
        route = env_meta.get("route")
    if route:
        meta["transport_route"] = {
            key: route.get(key)
            for key in ("via", "alias", "session_id", "webspace_id", "dialog_channel_id")
            if route.get(key) is not None
        }
    webspace_id = _text(route.get("webspace_id")) or _text(env_meta.get("webspace_id")) or None
    dialog_channel_id = _text(route.get("dialog_channel_id")) or _text(env_meta.get("dialog_channel_id"))
    if dialog_channel_id:
        meta["dialog_channel_id"] = dialog_channel_id
    return text.strip(), webspace_id, meta


def _extract_action_io_input(env: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
    direct_input = _text(env.get("type")) == "action" and bool(_text(env.get("source")))
    payload = env if direct_input else (env.get("payload") or {})
    if not isinstance(payload, Mapping) or _text(payload.get("type")) != "action":
        return None, {}
    inner = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else {}
    action = inner.get("action") if isinstance(inner.get("action"), Mapping) else {}
    token = _text(action.get("id") or action.get("token"))
    if not token:
        return None, {}
    source = _text(payload.get("source")) or "chat"
    bot_id = _text(payload.get("bot_id"))
    chat_id = _text(payload.get("chat_id"))
    user_id = _text(payload.get("user_id"))
    update_id = _text(payload.get("update_id"))
    event_id = _text(env.get("event_id")) or _text(payload.get("event_id"))
    dedup_key = _text(env.get("dedup_key")) or _text(payload.get("dedup_key")) or ":".join(
        value for value in (source, bot_id, chat_id, update_id, token) if value
    )
    return token, {
        "io_type": source,
        "bot_id": bot_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "update_id": update_id,
        "transport_event_id": event_id,
        "idempotency_key": f"transport:{dedup_key}" if dedup_key else f"transport-action:{token}",
    }


def register_chat_nlu_bridge(bus: LocalEventBus | None = None) -> None:
    """
    Attach a handler to Telegram input and dispatch canonical dialog messages.
    """
    ctx = get_ctx()
    bus = bus or ctx.bus

    def _on_io_input(evt: Event) -> None:
        try:
            env = evt.payload or {}
            if not isinstance(env, Mapping):
                return
            action_token, action_meta = _extract_action_io_input(env)
            if action_token:
                idempotency_key = _text(action_meta.get("idempotency_key"))
                receipt = conversation_store.claim_transport_ingress(
                    idempotency_key=idempotency_key,
                    transport=_text(action_meta.get("io_type")) or "chat",
                    event_id=_text(action_meta.get("transport_event_id")) or None,
                    payload={"action_token": action_token, "meta": action_meta},
                    meta={"source": "chat_io", "policy": "no_automatic_retry"},
                )
                if not receipt.get("claimed"):
                    return
                result = conversation_interactions.submit_action_token(
                    action_token,
                    actor_id=f"transport:{_text(action_meta.get('io_type')) or 'chat'}:{_text(action_meta.get('user_id')) or 'unknown'}",
                    idempotency_key=idempotency_key,
                    metadata=action_meta,
                )
                bus.publish(
                    Event(
                        type="conversation.interaction.responded",
                        source="chat_io.interaction",
                        ts=evt.ts,
                        payload=result,
                    )
                )
                conversation_store.mark_transport_ingress_dispatched(idempotency_key)
                return
            text, webspace_id, meta = _extract_text_io_input(env)
            if not text:
                return
            idempotency_key = _text(meta.get("idempotency_key"))
            if idempotency_key:
                receipt = conversation_store.claim_transport_ingress(
                    idempotency_key=idempotency_key,
                    transport=_text(meta.get("io_type")) or "chat",
                    event_id=_text(env.get("event_id")) or None,
                    payload={"text": text, "webspace_id": webspace_id, "meta": meta},
                    meta={"source": "chat_io", "policy": "no_automatic_retry"},
                )
                if not receipt.get("claimed"):
                    if receipt.get("conflict"):
                        _log.error("transport idempotency conflict key=%s", idempotency_key)
                    else:
                        _log.info("duplicate transport update suppressed key=%s", idempotency_key)
                    return
            try:
                if os.getenv("HUB_TG_DEBUG", "0") == "1" and isinstance(meta, dict) and meta.get("io_type") == "telegram":
                    _log.info(
                        "tg.input received hub_id=%s chat_id=%s text=%r",
                        meta.get("hub_id"),
                        meta.get("chat_id"),
                        text[:200],
                    )
            except Exception:
                pass
            payload: Dict[str, Any] = {"text": text}
            if webspace_id:
                payload["webspace_id"] = webspace_id
            if meta:
                payload["_meta"] = meta
            bus.publish(
                Event(
                    type="dialog.user_message",
                    source="chat_io.telegram",
                    ts=evt.ts,
                    payload=payload,
                )
            )
            if idempotency_key:
                conversation_store.mark_transport_ingress_dispatched(idempotency_key)
        except Exception:
            # Best-effort bridge; do not crash on malformed envelopes.
            return

    # Subscribe to the tg.input.<hub_id> subject emitted by bootstrap.
    bus.subscribe(f"tg.input.{ctx.config.subnet_id}", _on_io_input)
