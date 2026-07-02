from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any, Mapping, Sequence
import time
import uuid

from adaos.domain import Event
from adaos.services import conversation_store


DEFAULT_RENDER_TARGETS = ("text_tail",)
CARD_CONTENT_TYPES = {"card", "card_list", "table", "form", "webui", "builder_evidence"}


def materialize_response(
    response: Any,
    *,
    webspace_id: str,
    conversation_id: str,
    channel_id: str = "general",
    owner: str = "core",
    bus: Any | None = None,
    route_id: str = "dialog",
    actor_id: str | None = None,
    actor_label: str | None = None,
    actor_icon: str | None = None,
    actor_avatar_ref: str | None = None,
    request_id: str | None = None,
    turn_trace_id: str | None = None,
    thread_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
    source: str = "conversation.response",
    materialized_chat_appends: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    envelope = normalize_response_envelope(
        response,
        conversation_id=conversation_id,
        request_id=request_id,
        meta={**dict(meta or {}), "route_id": route_id, "dialog_channel_id": channel_id},
    )
    text = _envelope_text(envelope)
    speech_text = str(envelope.get("speech_text") or text or "").strip()
    targets = _targets(envelope.get("render_targets"))
    clean_meta = {
        **dict(meta or {}),
        **dict(envelope.get("meta") if isinstance(envelope.get("meta"), Mapping) else {}),
    }
    clean_meta.setdefault("webspace_id", webspace_id)
    clean_meta.setdefault("route_id", route_id)
    clean_meta.setdefault("conversation_id", conversation_id)
    clean_meta.setdefault("dialog_channel_id", channel_id)
    if actor_id:
        clean_meta.setdefault("active_agent_id", actor_id)
    if actor_label:
        clean_meta.setdefault("active_agent_label", actor_label)
    if actor_icon:
        clean_meta.setdefault("active_agent_icon", actor_icon)
    if actor_avatar_ref:
        clean_meta.setdefault("active_agent_avatar_ref", actor_avatar_ref)
    if request_id:
        clean_meta.setdefault("request_id", request_id)
    if turn_trace_id:
        clean_meta.setdefault("turn_trace_id", turn_trace_id)
    if thread_id:
        clean_meta.setdefault("thread_id", thread_id)

    published: list[dict[str, Any]] = []
    stored: dict[str, Any] | None = None
    if text and "text_tail" in targets:
        existing = _matching_chat_append(
            materialized_chat_appends or (),
            text=text,
            route_id=route_id,
            webspace_id=webspace_id,
            request_id=request_id,
            turn_trace_id=turn_trace_id,
        )
        if existing is not None:
            published.append(dict(existing))
        else:
            payload = {
                "id": str(envelope.get("message_id") or _make_message_id()),
                "from": "hub",
                "text": text,
                "ts": time.time(),
                "conversation_id": conversation_id,
                "dialog_channel_id": channel_id,
                "_meta": clean_meta,
            }
            if thread_id:
                payload["thread_id"] = thread_id
            if actor_id:
                payload["active_agent_id"] = actor_id
            if actor_label:
                payload["active_agent_label"] = actor_label
            if actor_icon:
                payload["active_agent_icon"] = actor_icon
            if actor_avatar_ref:
                payload["active_agent_avatar_ref"] = actor_avatar_ref
            _attach_voice_metadata(payload, clean_meta)
            stored = _append_ledger_message(
                payload,
                webspace_id=webspace_id,
                conversation_id=conversation_id,
                channel_id=channel_id,
                owner=owner,
                route_id=route_id,
                actor_id=actor_id,
                actor_label=actor_label,
                actor_icon=actor_icon,
                request_id=request_id,
                turn_trace_id=turn_trace_id,
                thread_id=thread_id,
                meta=clean_meta,
            )
            if bus is not None:
                _publish(bus, "io.out.chat.append", payload, source=source)
            published.append(payload)

    if speech_text and "speech_text" in targets and bus is not None:
        say_payload = {
            "id": _make_tts_id(),
            "text": speech_text,
            "ts": time.time(),
            "lang": str(clean_meta.get("lang") or "ru-RU"),
            "_meta": clean_meta,
        }
        _attach_voice_metadata(say_payload, clean_meta)
        _publish(bus, "io.out.say", say_payload, source=source)

    return {
        "ok": True,
        "envelope": envelope,
        "text": text,
        "render_targets": targets,
        "published_chat": published,
        "stored_message": stored,
        "materialized": bool(published),
    }


def materialize_tool_result(
    result: Any,
    *,
    webspace_id: str,
    conversation_id: str,
    channel_id: str,
    owner: str,
    bus: Any | None = None,
    route_id: str = "dialog",
    actor_id: str | None = None,
    actor_label: str | None = None,
    actor_icon: str | None = None,
    actor_avatar_ref: str | None = None,
    request_id: str | None = None,
    turn_trace_id: str | None = None,
    thread_id: str | None = None,
    raw_meta: Mapping[str, Any] | None = None,
    payload_meta: Mapping[str, Any] | None = None,
    source: str = "conversation.response",
    materialized_chat_appends: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    meta = {**dict(raw_meta or {}), **dict(payload_meta or {})}
    response = _tool_result_response(result)
    if response is None:
        return {
            "ok": True,
            "materialized": False,
            "published_chat": [],
            "reason": "no_response_content",
        }
    return materialize_response(
        response,
        webspace_id=webspace_id,
        conversation_id=conversation_id,
        channel_id=channel_id,
        owner=owner,
        bus=bus,
        route_id=route_id,
        actor_id=actor_id,
        actor_label=actor_label,
        actor_icon=actor_icon,
        actor_avatar_ref=actor_avatar_ref,
        request_id=request_id,
        turn_trace_id=turn_trace_id,
        thread_id=thread_id,
        meta=meta,
        source=source,
        materialized_chat_appends=materialized_chat_appends,
    )


def normalize_response_envelope(
    response: Any,
    *,
    conversation_id: str,
    request_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = _as_mapping(response)
    if value is None:
        text = str(response or "").strip()
        value = {"content": [{"type": "text", "text": text}]} if text else {}
    if "response_envelope" in value and isinstance(value.get("response_envelope"), Mapping):
        value = dict(value["response_envelope"])
    elif "response" in value and isinstance(value.get("response"), Mapping):
        value = dict(value["response"])
    elif "message" in value and "content" not in value:
        value = {
            "content": [{"type": "text", "text": str(value.get("message") or "")}],
            "render_targets": value.get("render_targets") or DEFAULT_RENDER_TARGETS,
            "speech_text": value.get("speech_text"),
            "meta": value.get("meta") if isinstance(value.get("meta"), Mapping) else {},
        }
    else:
        value = dict(value)

    merged_meta = {
        **dict(meta or {}),
        **dict(value.get("meta") if isinstance(value.get("meta"), Mapping) else {}),
    }
    value["conversation_id"] = str(value.get("conversation_id") or conversation_id or "").strip()
    if request_id and not value.get("request_id"):
        value["request_id"] = request_id
    value["content"] = _content_parts(value.get("content"))
    value["meta"] = merged_meta
    response_plan = plan_response_targets(value, route_id=str(merged_meta.get("route_id") or ""))
    value["render_targets"] = tuple(response_plan["targets"])
    value["response_plan"] = response_plan
    return value


def plan_response_targets(
    response: Mapping[str, Any],
    *,
    route_id: str = "",
    channel_id: str = "",
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    envelope = dict(response or {})
    merged_meta = {
        **dict(meta or {}),
        **dict(envelope.get("meta") if isinstance(envelope.get("meta"), Mapping) else {}),
    }
    explicit = _targets_or_none(envelope.get("render_targets"))
    if explicit is not None:
        return {
            "schema": "adaos.conversation.response_plan.v1",
            "targets": list(explicit),
            "reason": "explicit_render_targets",
            "policy": str(merged_meta.get("response_policy") or "explicit"),
        }
    content = envelope.get("content")
    parts = _content_parts(content)
    text = _envelope_text({"content": parts})
    targets: list[str] = []
    reasons: list[str] = []
    if text:
        targets.append("text_tail")
        reasons.append("text_content")
    if str(envelope.get("speech_text") or "").strip() or str(merged_meta.get("speech_text") or "").strip():
        targets.append("speech_text")
        reasons.append("speech_text_present")
    elif str(route_id or channel_id or merged_meta.get("route_id") or "").strip() == "voice_chat" and text:
        policy = str(merged_meta.get("response_policy") or "").strip()
        if policy in {"speech_text_first", "voice_reply", "ask"}:
            targets.append("speech_text")
            reasons.append(f"voice_policy:{policy}")
    if any(str(part.get("type") or "").strip() in CARD_CONTENT_TYPES for part in parts):
        targets.append("card")
        reasons.append("structured_card_content")
    if isinstance(envelope.get("pending_action"), Mapping) or any(str(part.get("type") or "").strip() == "pending_action" for part in parts):
        targets.append("pending_action")
        reasons.append("pending_action_content")
    if not targets:
        targets.extend(DEFAULT_RENDER_TARGETS)
        reasons.append("default_text_tail")
    return {
        "schema": "adaos.conversation.response_plan.v1",
        "targets": list(dict.fromkeys(targets)),
        "reason": "+".join(reasons),
        "policy": str(merged_meta.get("response_policy") or "structure_inferred"),
    }


def _tool_result_response(result: Any) -> Any | None:
    value = _as_mapping(result)
    if value is None:
        text = str(result or "").strip()
        return text or None
    if value.get("ok") is False:
        return None
    for key in ("response_envelope", "response"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            return nested
    if str(value.get("message") or "").strip():
        return {
            "conversation_id": value.get("conversation_id"),
            "content": [{"type": "text", "text": str(value.get("message") or "").strip()}],
            "speech_text": value.get("speech_text"),
            "render_targets": value.get("render_targets") or DEFAULT_RENDER_TARGETS,
            "meta": value.get("meta") if isinstance(value.get("meta"), Mapping) else {},
        }
    return None


def _append_ledger_message(
    payload: Mapping[str, Any],
    *,
    webspace_id: str,
    conversation_id: str,
    channel_id: str,
    owner: str,
    route_id: str,
    actor_id: str | None,
    actor_label: str | None,
    actor_icon: str | None,
    request_id: str | None,
    turn_trace_id: str | None,
    thread_id: str | None,
    meta: Mapping[str, Any],
) -> dict[str, Any] | None:
    try:
        conversation_store.upsert_conversation(
            conversation_id=conversation_id,
            webspace_id=webspace_id,
            owner=owner,
            kind="dialog",
            title="General" if channel_id == "general" else channel_id,
            active_agent_id=actor_id,
            meta={"route_id": route_id, "channel_id": channel_id},
        )
        return conversation_store.append_message(
            conversation_id=conversation_id,
            thread_id=thread_id,
            webspace_id=webspace_id,
            channel_id=channel_id,
            owner=owner,
            role=str(payload.get("from") or "hub"),
            text=str(payload.get("text") or ""),
            payload=payload,
            meta=meta,
            actor_id=actor_id,
            actor_label=actor_label,
            actor_icon=actor_icon,
            route_id=route_id,
            request_id=request_id,
            turn_trace_id=turn_trace_id,
            idempotency_key=str(meta.get("idempotency_key") or "").strip() or None,
            ts=float(payload.get("ts") or time.time()),
        )
    except Exception:
        return None


def _matching_chat_append(
    items: Sequence[Mapping[str, Any]],
    *,
    text: str,
    route_id: str,
    webspace_id: str,
    request_id: str | None,
    turn_trace_id: str | None,
) -> Mapping[str, Any] | None:
    for item in items:
        item_meta = item.get("_meta") if isinstance(item.get("_meta"), Mapping) else {}
        item_text = str(item.get("text") or "").strip()
        if item_text != text.strip():
            continue
        item_route = str(item.get("route_id") or item_meta.get("route_id") or item_meta.get("route") or "").strip()
        if route_id and item_route and item_route != route_id:
            continue
        item_ws = str(item.get("webspace_id") or item_meta.get("webspace_id") or "").strip()
        if item_ws and item_ws != webspace_id:
            continue
        item_request = str(item.get("request_id") or item_meta.get("request_id") or "").strip()
        item_trace = str(item.get("turn_trace_id") or item_meta.get("turn_trace_id") or "").strip()
        if request_id and item_request and item_request != request_id:
            continue
        if turn_trace_id and item_trace and item_trace != turn_trace_id:
            continue
        return item
    return None


def _content_parts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    parts: list[dict[str, Any]] = []
    for item in value:
        mapping = _as_mapping(item)
        if mapping is None:
            text = str(item or "").strip()
            if text:
                parts.append({"type": "text", "text": text})
            continue
        part_type = str(mapping.get("type") or "text").strip() or "text"
        part = {"type": part_type}
        if mapping.get("text") is not None:
            part["text"] = str(mapping.get("text") or "")
        data = mapping.get("data")
        if isinstance(data, Mapping):
            part["data"] = dict(data)
        parts.append(part)
    return parts


def _envelope_text(envelope: Mapping[str, Any]) -> str:
    parts = envelope.get("content")
    if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes, bytearray)):
        return ""
    texts = []
    for item in parts:
        mapping = _as_mapping(item)
        if mapping is None:
            continue
        if str(mapping.get("type") or "text") == "text":
            text = str(mapping.get("text") or "").strip()
            if text:
                texts.append(text)
    return "\n".join(texts).strip()


def _targets(value: Any) -> tuple[str, ...]:
    targets = _targets_or_none(value)
    return targets or DEFAULT_RENDER_TARGETS


def _targets_or_none(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, str):
        token = value.strip()
        return (token,) if token else None
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return None
    targets = tuple(str(item or "").strip() for item in value if str(item or "").strip())
    return targets or None


def _voice_profile(meta: Mapping[str, Any]) -> dict[str, Any]:
    profile = meta.get("voice_profile")
    if isinstance(profile, Mapping):
        return dict(profile)
    agent = meta.get("active_agent")
    if isinstance(agent, Mapping) and isinstance(agent.get("voice_profile"), Mapping):
        return dict(agent["voice_profile"])
    return {}


def _voice_gender_from_voice(voice: Any) -> str:
    token = str(voice or "").strip().lower()
    if not token:
        return ""
    if token in {"female", "ru-female"} or token.endswith("-female") or token.endswith("_female"):
        return "female"
    if token in {"male", "ru-male"} or token.endswith("-male") or token.endswith("_male"):
        return "male"
    return ""


def _attach_voice_metadata(payload: dict[str, Any], meta: Mapping[str, Any]) -> None:
    profile = _voice_profile(meta)
    voice = str(meta.get("voice") or meta.get("active_agent_voice") or profile.get("voice") or "").strip()
    gender = str(
        meta.get("voice_gender")
        or meta.get("active_agent_gender")
        or profile.get("gender")
        or _voice_gender_from_voice(voice)
        or ""
    ).strip()
    if voice:
        payload["voice"] = voice
    if gender:
        payload["voice_gender"] = gender
    if profile:
        if gender and not profile.get("gender"):
            profile["gender"] = gender
        if voice and not profile.get("voice"):
            profile["voice"] = voice
        if not profile.get("browser_voice_hint"):
            profile["browser_voice_hint"] = gender or _voice_gender_from_voice(voice) or voice
        payload["voice_profile"] = profile
    for key in (
        "active_agent_id",
        "active_agent_label",
        "active_agent_gender",
        "active_agent_voice",
        "active_agent_icon",
        "active_agent_avatar_ref",
        "agent_avatar_ref",
        "agent_icon",
    ):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            payload.setdefault(key, value.strip())


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        if hasattr(value, "to_dict"):
            data = value.to_dict()
            return dict(data) if isinstance(data, Mapping) else None
        return None
    if hasattr(value, "to_dict"):
        data = value.to_dict()
        return dict(data) if isinstance(data, Mapping) else None
    return None


def _publish(bus: Any, type_: str, payload: dict[str, Any], *, source: str) -> None:
    bus.publish(Event(type=type_, source=source, ts=time.time(), payload=payload))


def _make_message_id() -> str:
    return f"m.{uuid.uuid4().hex}"


def _make_tts_id() -> str:
    return f"tts.{uuid.uuid4().hex}"
