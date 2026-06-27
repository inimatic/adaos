from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import threading
import time
from typing import Any, Mapping

from adaos.services.eventbus import emit as bus_emit


@dataclass
class DialogChannelState:
    webspace_id: str
    channel_id: str
    owner: str
    default_skill: str
    default_tool: str
    conversation_id: str
    active_agent_id: str | None = None
    active_agent_label: str | None = None
    active_agent_owner: str | None = None
    active_agent_kind: str | None = None
    active_agent_gender: str | None = None
    active_agent_voice: str | None = None
    active_agent_icon: str | None = None
    route_id: str | None = None
    source_request_id: str | None = None
    activated_at: float = 0.0
    updated_at: float = 0.0
    ttl_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_LOCK = threading.RLock()
_ACTIVE_BY_WEBSPACE: dict[str, DialogChannelState] = {}

_EXIT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.UNICODE)
    for pattern in (
        r"^\s*(?:stop|exit|general|back to general)\s*[.!?]*\s*$",
        r"^\s*(?:\u0441\u0442\u043e\u043f|\u0445\u0432\u0430\u0442\u0438\u0442)\s+(?:\u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440|\u0434\u0438\u0430\u043b\u043e\u0433)\s*[.!?]*\s*$",
        r"^\s*(?:\u0437\u0430\u043a\u043e\u043d\u0447\u0438|\u0437\u0430\u0432\u0435\u0440\u0448\u0438)\s+(?:\u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440|\u0434\u0438\u0430\u043b\u043e\u0433)\s*[.!?]*\s*$",
        r"^\s*(?:\u0432\u0435\u0440\u043d\u0438\u0441\u044c\s+)?\u0432\s+(?:\u043e\u0431\u0449\u0438\u0439|general)(?:\s+\u043a\u0430\u043d\u0430\u043b|\s+\u0440\u0435\u0436\u0438\u043c)?\s*[.!?]*\s*$",
    )
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _webspace_id(value: str | None) -> str:
    return _clean(value) or "default"


def _target_owner(target: str) -> str:
    skill, _, _tool = _clean(target).partition(".")
    return f"skill:{skill}" if skill else ""


def _split_tool_reference(value: Any, *, fallback_skill: str, fallback_tool: str) -> tuple[str, str]:
    token = _clean(value)
    if "." in token:
        skill, _, tool = token.partition(".")
        return _clean(skill) or fallback_skill, _clean(tool) or fallback_tool
    if token:
        return fallback_skill, token
    return fallback_skill, fallback_tool


def _active_state(state: DialogChannelState, *, now: float | None = None) -> bool:
    if state.ttl_s is None:
        return True
    current = time.time() if now is None else float(now)
    return current - float(state.updated_at or state.activated_at or 0.0) <= float(state.ttl_s)


def reset_all() -> None:
    with _LOCK:
        _ACTIVE_BY_WEBSPACE.clear()


def get_active_channel(webspace_id: str | None) -> DialogChannelState | None:
    ws = _webspace_id(webspace_id)
    with _LOCK:
        state = _ACTIVE_BY_WEBSPACE.get(ws)
        if state is None:
            return None
        if not _active_state(state):
            _ACTIVE_BY_WEBSPACE.pop(ws, None)
            return None
        return DialogChannelState(**state.as_dict())


def activate_channel(
    *,
    webspace_id: str,
    channel_id: str,
    owner: str,
    default_skill: str,
    default_tool: str,
    conversation_id: str | None = None,
    active_agent_id: str | None = None,
    active_agent_label: str | None = None,
    active_agent_owner: str | None = None,
    active_agent_kind: str | None = None,
    active_agent_gender: str | None = None,
    active_agent_voice: str | None = None,
    active_agent_icon: str | None = None,
    route_id: str | None = None,
    source_request_id: str | None = None,
    ttl_s: float | None = None,
    bus: Any | None = None,
    source: str = "dialog.runtime",
) -> DialogChannelState:
    now = time.time()
    ws = _webspace_id(webspace_id)
    state = DialogChannelState(
        webspace_id=ws,
        channel_id=_clean(channel_id) or "general",
        owner=_clean(owner),
        default_skill=_clean(default_skill),
        default_tool=_clean(default_tool),
        conversation_id=_clean(conversation_id) or f"conv.{ws}.{_clean(channel_id) or 'general'}",
        active_agent_id=_clean(active_agent_id) or None,
        active_agent_label=_clean(active_agent_label) or None,
        active_agent_owner=_clean(active_agent_owner) or None,
        active_agent_kind=_clean(active_agent_kind) or None,
        active_agent_gender=_clean(active_agent_gender) or None,
        active_agent_voice=_clean(active_agent_voice) or None,
        active_agent_icon=_clean(active_agent_icon) or None,
        route_id=_clean(route_id) or None,
        source_request_id=_clean(source_request_id) or None,
        activated_at=now,
        updated_at=now,
        ttl_s=ttl_s,
    )
    with _LOCK:
        _ACTIVE_BY_WEBSPACE[ws] = state
    if bus is not None:
        bus_emit(bus, "dialog.channel.activated", state.as_dict(), source=source)
    return DialogChannelState(**state.as_dict())


def deactivate_channel(
    *,
    webspace_id: str,
    channel_id: str | None = None,
    bus: Any | None = None,
    source: str = "dialog.runtime",
    reason: str = "explicit_exit",
) -> DialogChannelState | None:
    ws = _webspace_id(webspace_id)
    with _LOCK:
        state = _ACTIVE_BY_WEBSPACE.get(ws)
        if state is None:
            return None
        if channel_id and state.channel_id != channel_id:
            return None
        removed = _ACTIVE_BY_WEBSPACE.pop(ws, None)
    if removed is not None and bus is not None:
        payload = removed.as_dict()
        payload["reason"] = reason
        bus_emit(bus, "dialog.channel.deactivated", payload, source=source)
    return DialogChannelState(**removed.as_dict()) if removed is not None else None


def is_exit_text(text: str) -> bool:
    value = _clean(text)
    if not value:
        return False
    return any(pattern.search(value) for pattern in _EXIT_PATTERNS)


def apply_tool_result(
    result: Any,
    *,
    webspace_id: str,
    target: str,
    raw_meta: Mapping[str, Any] | None = None,
    payload_meta: Mapping[str, Any] | None = None,
    bus: Any | None = None,
    source: str = "dialog.runtime",
) -> DialogChannelState | None:
    if not isinstance(result, Mapping) or result.get("ok") is False:
        return None
    dialog = result.get("dialog") if isinstance(result.get("dialog"), Mapping) else None
    if dialog is None:
        return None

    meta: dict[str, Any] = {}
    if isinstance(raw_meta, Mapping):
        meta.update(dict(raw_meta))
    if isinstance(payload_meta, Mapping):
        meta.update(dict(payload_meta))

    state = _clean(dialog.get("state") or dialog.get("status") or "active").lower()
    channel_id = _clean(dialog.get("dialog_channel_id") or dialog.get("channel_id") or meta.get("dialog_channel_id"))
    if state in {"inactive", "closed", "exit", "deactivated"}:
        return deactivate_channel(
            webspace_id=webspace_id,
            channel_id=channel_id or None,
            bus=bus,
            source=source,
            reason=state,
        )
    if state not in {"active", "open", "started"}:
        return None

    target_skill, _, target_tool = _clean(target).partition(".")
    fallback_skill = target_skill or _clean(dialog.get("default_skill"))
    fallback_tool = target_tool or _clean(dialog.get("default_tool")) or "talk"
    default_skill, default_tool = _split_tool_reference(
        dialog.get("default_tool_ref") or dialog.get("default_tool"),
        fallback_skill=fallback_skill,
        fallback_tool=fallback_tool,
    )
    owner = _clean(dialog.get("owner")) or _target_owner(target)
    ttl_raw = dialog.get("ttl_s")
    ttl_s = float(ttl_raw) if isinstance(ttl_raw, (int, float)) and float(ttl_raw) > 0 else None
    active_agent = dialog.get("active_agent") if isinstance(dialog.get("active_agent"), Mapping) else {}
    return activate_channel(
        webspace_id=webspace_id,
        channel_id=channel_id or "conversational",
        owner=owner,
        default_skill=default_skill,
        default_tool=default_tool,
        conversation_id=_clean(dialog.get("conversation_id")) or None,
        active_agent_id=_clean(dialog.get("active_agent_id") or result.get("active_character")) or None,
        active_agent_label=_clean(
            dialog.get("active_agent_label")
            or dialog.get("active_agent_name")
            or active_agent.get("label")
            or active_agent.get("name")
        )
        or None,
        active_agent_owner=_clean(active_agent.get("owner") or dialog.get("active_agent_owner") or owner) or None,
        active_agent_kind=_clean(active_agent.get("kind") or dialog.get("active_agent_kind")) or None,
        active_agent_gender=_clean(active_agent.get("gender") or dialog.get("active_agent_gender")) or None,
        active_agent_voice=_clean(
            active_agent.get("voice")
            or dialog.get("active_agent_voice")
            or (
                active_agent.get("voice_profile", {}).get("voice")
                if isinstance(active_agent.get("voice_profile"), Mapping)
                else None
            )
        )
        or None,
        active_agent_icon=_clean(
            active_agent.get("icon")
            or active_agent.get("avatar")
            or dialog.get("active_agent_icon")
            or dialog.get("agent_icon")
        )
        or None,
        route_id=_clean(meta.get("route_id") or meta.get("route")) or None,
        source_request_id=_clean(meta.get("request_id")) or None,
        ttl_s=ttl_s,
        bus=bus,
        source=source,
    )


def resolve_followup_action(
    *,
    webspace_id: str,
    text: str,
    route_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    state = get_active_channel(webspace_id)
    if state is None:
        return None
    route = _clean(route_id or (meta or {}).get("route_id") or (meta or {}).get("route"))
    if route and state.route_id and route != state.route_id:
        return None
    if is_exit_text(text):
        return {
            "kind": "exit",
            "channel": state.as_dict(),
            "message": "\u0412\u0435\u0440\u043d\u0443\u043b\u0441\u044f \u0432 \u043e\u0431\u0449\u0438\u0439 \u0440\u0435\u0436\u0438\u043c.",
        }
    action_meta = dict(meta or {})
    action_meta.setdefault("dialog_channel_id", state.channel_id)
    action_meta.setdefault("conversation_id", state.conversation_id)
    action_meta.setdefault("conversation_owner", state.owner)
    if state.active_agent_id:
        action_meta.setdefault("active_agent_id", state.active_agent_id)
    if state.active_agent_label:
        action_meta.setdefault("active_agent_label", state.active_agent_label)
    if state.active_agent_gender:
        action_meta.setdefault("active_agent_gender", state.active_agent_gender)
    if state.active_agent_voice:
        action_meta.setdefault("active_agent_voice", state.active_agent_voice)
    if state.active_agent_icon:
        action_meta.setdefault("active_agent_icon", state.active_agent_icon)
    return {
        "kind": "skill_tool",
        "skill": state.default_skill,
        "tool": state.default_tool,
        "channel": state.as_dict(),
        "payload": {
            "text": text,
            "webspace_id": state.webspace_id,
            "_meta": action_meta,
        },
    }
