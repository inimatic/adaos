"""Unified IO output helpers for web/native frontends.

These helpers do not write to Yjs directly. They only publish events onto the
local bus. The RouterService is responsible for projecting them into concrete
outputs (chat history, TTS queues, etc.) based on `_meta`.
"""

from __future__ import annotations

import hashlib
import base64
import json
import mimetypes
import os
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from adaos.sdk.core.decorators import tool
from adaos.sdk.data.context import get_current_skill
from adaos.sdk.io.context import get_current_meta
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as _emit
from adaos.services.node_config import load_config
from adaos.services.webspace_id import coerce_webspace_id

__all__ = ["chat_append", "say", "media_route", "telegram_photo", "stream_publish", "stream_variable_publish"]


def _publish(topic: str, payload: dict, *, source: str) -> None:
    ctx = get_ctx()
    bus = getattr(ctx, "bus", None)
    if bus is None:
        raise RuntimeError("AgentContext.bus is not initialized")
    _emit(bus, topic, payload, source)


def _normalize_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(meta)
    if "webspace_id" in normalized:
        normalized["webspace_id"] = coerce_webspace_id(normalized.get("webspace_id"), fallback="default")
    if "workspace_id" in normalized:
        normalized["workspace_id"] = coerce_webspace_id(normalized.get("workspace_id"), fallback="default")
    raw_ids = normalized.get("webspace_ids")
    if isinstance(raw_ids, (list, tuple)):
        out: list[str] = []
        for item in raw_ids:
            token = coerce_webspace_id(item, fallback="default")
            if token and token not in out:
                out.append(token)
        if out:
            normalized["webspace_ids"] = out
    return normalized


def _merged_meta(_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    meta = get_current_meta()
    if _meta:
        meta.update(dict(_meta))
    try:
        current = get_current_skill()
        skill_name = str(getattr(current, "name", "") or "").strip()
    except Exception:
        skill_name = ""
    if skill_name:
        meta.setdefault("skill_name", skill_name)
        meta.setdefault("owner", f"skill:{skill_name}")
    return _normalize_meta(meta) if meta else meta


def _local_node_id() -> str:
    try:
        conf = load_config()
        node_id = str(getattr(conf, "node_id", "") or "").strip()
        if node_id:
            return node_id
        nested = str(getattr(getattr(conf, "node_settings", None), "id", "") or "").strip()
        if nested:
            return nested
    except Exception:
        pass
    return ""


def _local_subnet_id() -> str:
    try:
        conf = load_config()
        subnet_id = str(getattr(conf, "subnet_id", "") or "").strip()
        if subnet_id:
            return subnet_id
    except Exception:
        pass
    try:
        subnet_id = str(getattr(get_ctx().settings, "subnet_id", "") or "").strip()
        if subnet_id:
            return subnet_id
    except Exception:
        pass
    return ""


def _unique_texts(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip().rstrip("/")
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _root_base_candidates(explicit: str | None = None) -> list[str]:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(
        [
            os.getenv("PUBLIC_ROOT_BASE") or "",
            os.getenv("ADAOS_API_BASE") or "",
        ]
    )
    try:
        settings = get_ctx().settings
        candidates.append(str(getattr(settings, "api_base", "") or ""))
    except Exception:
        pass
    try:
        conf = load_config()
        candidates.append(str(getattr(getattr(conf, "root_settings", None), "base_url", "") or ""))
    except Exception:
        pass
    candidates.extend(
        [
            os.getenv("ROOT_BASE_URL") or "",
            "https://api.inimatic.com",
        ]
    )
    return _unique_texts(candidates) or ["https://api.inimatic.com"]


def _root_base_url(explicit: str | None = None) -> str:
    return _root_base_candidates(explicit)[0]


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _jpeg_payload_from_image(path: Path, *, max_bytes: int, max_dim: int) -> tuple[bytes, str, str, dict[str, Any]]:
    filename = path.with_suffix(".jpg").name
    meta: dict[str, Any] = {"encoded": False, "source_bytes": 0, "bytes": 0}
    try:
        meta["source_bytes"] = int(path.stat().st_size)
    except Exception:
        pass
    try:
        from PIL import Image, ImageOps  # type: ignore

        with Image.open(path) as original:
            original = ImageOps.exif_transpose(original)
            if getattr(original, "is_animated", False):
                original.seek(0)
            dims = [max_dim]
            for dim in (1600, 1280, 1024, 768, 640):
                if dim < max_dim and dim not in dims:
                    dims.append(dim)
            for dim in dims:
                image = original.copy()
                image.thumbnail((dim, dim), Image.Resampling.LANCZOS)
                if image.mode in {"RGBA", "LA"}:
                    canvas = Image.new("RGB", image.size, "white")
                    canvas.paste(image, mask=image.getchannel("A"))
                    image = canvas
                else:
                    image = image.convert("RGB")
                for quality in (86, 80, 74, 68, 62, 56, 50):
                    out = BytesIO()
                    image.save(out, "JPEG", quality=quality, optimize=True)
                    data = out.getvalue()
                    if len(data) <= max_bytes:
                        meta.update({"encoded": True, "bytes": len(data), "max_dim": dim, "quality": quality})
                        return data, "image/jpeg", filename, meta
    except Exception as exc:
        meta["encode_error"] = f"{type(exc).__name__}: {exc}"

    data = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if len(data) > max_bytes:
        raise ValueError(f"telegram_photo_too_large:{len(data)}>{max_bytes}")
    meta.update({"bytes": len(data), "mime": mime})
    return data, mime, path.name, meta


def _json_fingerprint(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        raw = repr(value)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@tool(
    "io.out.chat.append",
    summary="Append a chat message (router decides where it renders).",
    stability="experimental",
    examples=[
        "io.out.chat.append('Hello', from_='user', _meta={'webspace_id':'default'})",
        "io.out.chat.append('Hi!', from_='hub')",
    ],
)
def chat_append(
    text: str | None,
    *,
    from_: str = "hub",
    msg_id: str | None = None,
    ts: float | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> Mapping[str, bool]:
    if not isinstance(text, str) or not text.strip():
        return {"ok": False}

    payload: dict[str, Any] = {
        "text": text.strip(),
        "from": str(from_ or "hub"),
        "id": str(msg_id) if msg_id else "",
        "ts": float(ts) if ts is not None else time.time(),
    }
    meta = _merged_meta(_meta)
    if meta:
        payload["_meta"] = meta
    _publish("io.out.chat.append", payload, source="sdk.io.out")
    return {"ok": True}


@tool(
    "io.out.say",
    summary="Enqueue a TTS message (router decides which devices/webspaces play it).",
    stability="experimental",
    examples=[
        "io.out.say('Weather is sunny', lang='en-US', _meta={'webspace_id':'default'})",
    ],
)
def say(
    text: str | None,
    *,
    lang: str | None = None,
    voice: str | None = None,
    rate: float | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> Mapping[str, bool]:
    if not isinstance(text, str) or not text.strip():
        return {"ok": False}

    payload: dict[str, Any] = {
        "text": text.strip(),
        "ts": time.time(),
    }
    if isinstance(lang, str) and lang.strip():
        payload["lang"] = lang.strip()
    if isinstance(voice, str) and voice.strip():
        payload["voice"] = voice.strip()
    if isinstance(rate, (int, float)):
        payload["rate"] = float(rate)
    meta = _merged_meta(_meta)
    if meta:
        payload["_meta"] = meta

    _publish("io.out.say", payload, source="sdk.io.out")
    return {"ok": True}


@tool(
    "io.out.media.route",
    summary="Publish a media route intent or normalized route contract for router-owned projection.",
    stability="experimental",
    examples=[
        "io.out.media.route(need='scenario_response_media', _meta={'webspace_id':'default'})",
        "io.out.media.route(route={'route_intent':'live_stream','active_route':'hub_webrtc_loopback'})",
    ],
)
def media_route(
    *,
    need: str | None = None,
    route: Mapping[str, Any] | None = None,
    producer_preference: str | None = None,
    preferred_member_id: str | None = None,
    direct_local_ready: bool | None = None,
    root_routed_ready: bool | None = None,
    hub_webrtc_ready: bool | None = None,
    member_browser_direct_possible: bool | None = None,
    member_browser_direct_admitted: bool | None = None,
    member_browser_direct_reason: str | None = None,
    candidate_member_total: int | None = None,
    browser_session_total: int | None = None,
    observed_failure: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> Mapping[str, bool]:
    if not isinstance(route, Mapping) and (not isinstance(need, str) or not need.strip()):
        return {"ok": False}

    payload: dict[str, Any] = {
        "ts": time.time(),
    }
    if isinstance(need, str) and need.strip():
        payload["need"] = need.strip()
    if isinstance(route, Mapping):
        payload["route"] = dict(route)
    if isinstance(producer_preference, str) and producer_preference.strip():
        payload["producer_preference"] = producer_preference.strip()
    if isinstance(preferred_member_id, str) and preferred_member_id.strip():
        payload["preferred_member_id"] = preferred_member_id.strip()
    if direct_local_ready is not None:
        payload["direct_local_ready"] = bool(direct_local_ready)
    if root_routed_ready is not None:
        payload["root_routed_ready"] = bool(root_routed_ready)
    if hub_webrtc_ready is not None:
        payload["hub_webrtc_ready"] = bool(hub_webrtc_ready)
    member_browser_direct: dict[str, Any] = {}
    if member_browser_direct_possible is not None:
        member_browser_direct["possible"] = bool(member_browser_direct_possible)
    if member_browser_direct_admitted is not None:
        member_browser_direct["admitted"] = bool(member_browser_direct_admitted)
    if isinstance(member_browser_direct_reason, str) and member_browser_direct_reason.strip():
        member_browser_direct["reason"] = member_browser_direct_reason.strip()
    if isinstance(candidate_member_total, int):
        member_browser_direct["candidate_member_total"] = candidate_member_total
    if isinstance(browser_session_total, int):
        member_browser_direct["browser_session_total"] = browser_session_total
    if member_browser_direct:
        payload["member_browser_direct"] = member_browser_direct
    if isinstance(observed_failure, str) and observed_failure.strip():
        payload["observed_failure"] = observed_failure.strip()

    meta = _merged_meta(_meta)
    if meta:
        payload["_meta"] = meta

    _publish("io.out.media.route", payload, source="sdk.io.out")
    return {"ok": True}


@tool(
    "io.out.telegram.photo",
    summary="Send a local image to a configured Telegram chat through the hub/root Telegram outbox.",
    stability="experimental",
    examples=[
        "io.out.telegram.photo('/tmp/photo.jpg', chat_id='123456')",
    ],
)
def telegram_photo(
    image_path: str | None,
    *,
    caption: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
    hub_id: str | None = None,
    root_base: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    path = str(image_path or "").strip()
    if not path:
        return {"ok": False, "error": "missing_image_path"}
    image_file = Path(path)
    if not image_file.exists() or not image_file.is_file():
        return {"ok": False, "error": "image_path_not_found", "image_path": path}

    target_chat = str(
        chat_id
        or os.getenv("SLIDESHOW_TG_CHAT_ID")
        or os.getenv("TG_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or ""
    ).strip()
    target_bot = str(bot_id or os.getenv("SLIDESHOW_TG_BOT_ID") or os.getenv("TG_BOT_ID") or "").strip()
    try:
        max_bytes = _int_env("ADAOS_TG_PHOTO_MAX_BYTES", 900 * 1024, minimum=32 * 1024, maximum=1024 * 1024)
        max_dim = _int_env("ADAOS_TG_PHOTO_MAX_DIM", 1600, minimum=256, maximum=4096)
        photo_bytes, mime, filename, media_meta = _jpeg_payload_from_image(image_file, max_bytes=max_bytes, max_dim=max_dim)
    except Exception as exc:
        return {"ok": False, "error": "telegram_photo_encode_failed", "detail": str(exc), "image_path": path}

    target_hub = str(hub_id or "").strip() or _local_subnet_id()
    if not target_hub and not target_chat:
        return {"ok": False, "error": "hub_id_or_chat_id_required"}
    message: dict[str, Any] = {
        "type": "photo",
        "image_base64": base64.b64encode(photo_bytes).decode("ascii"),
        "filename": filename,
        "mime": mime,
    }
    if isinstance(caption, str) and caption.strip():
        message["caption"] = caption.strip()
    body: dict[str, Any] = {"messages": [message]}
    if target_hub:
        body["hub_id"] = target_hub
    if target_chat:
        body["chat_id"] = target_chat
    if target_bot:
        body["bot_id"] = target_bot
    meta = _merged_meta(_meta)
    if meta:
        body["_meta"] = meta

    result: dict[str, Any] | None = None
    tried_roots: list[str] = []
    try:
        import requests

        for root_url in _root_base_candidates(root_base):
            tried_roots.append(root_url)
            try:
                resp = requests.post(
                    f"{root_url.rstrip('/')}/io/tg/send",
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=(2.0, 8.0),
                )
                try:
                    data = resp.json() if resp.content else {}
                except Exception:
                    data = {"body": (resp.text or "")[:300]}
                if 200 <= int(resp.status_code or 0) < 300 and bool(data.get("ok", True)):
                    return {
                        "ok": True,
                        "transport": "root_tg_send",
                        "root_url": root_url,
                        "tried_roots": tried_roots,
                        "hub_id": target_hub,
                        "chat_id": target_chat,
                        "bot_id": target_bot,
                        "media": media_meta,
                        "result": data,
                    }
                error = str(data.get("error") or f"root_tg_send_http_{resp.status_code}")
                result = {
                    "ok": False,
                    "error": error,
                    "status": int(resp.status_code or 0),
                    "root_url": root_url,
                    "tried_roots": list(tried_roots),
                    "hub_id": target_hub,
                    "chat_id": target_chat,
                    "bot_id": target_bot,
                    "media": media_meta,
                    "result": data,
                }
                if error != "pairing_not_found" and int(resp.status_code or 0) not in {404, 503}:
                    break
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": "root_tg_send_failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "root_url": root_url,
                    "tried_roots": list(tried_roots),
                    "hub_id": target_hub,
                    "chat_id": target_chat,
                    "bot_id": target_bot,
                    "media": media_meta,
                }
    except Exception as exc:
        result = {
            "ok": False,
            "error": "root_tg_send_failed",
            "detail": f"{type(exc).__name__}: {exc}",
            "tried_roots": tried_roots,
            "hub_id": target_hub,
            "chat_id": target_chat,
            "bot_id": target_bot,
            "media": media_meta,
        }
    if result is None:
        result = {
            "ok": False,
            "error": "root_tg_send_failed",
            "tried_roots": tried_roots,
            "hub_id": target_hub,
            "chat_id": target_chat,
            "bot_id": target_bot,
            "media": media_meta,
        }

    if target_chat:
        fallback_bot = target_bot or "adaos_bot"
        payload = {
            "target": {"bot_id": fallback_bot, "hub_id": target_hub or "unknown_hub", "chat_id": target_chat},
            "messages": [message],
            "options": None,
        }
        if meta:
            payload["_meta"] = meta
        _publish(f"tg.output.{fallback_bot}.chat.{target_chat}", payload, source="sdk.io.out")
        return {
            "ok": True,
            "transport": "local_tg_output_inline_fallback",
            "root_result": result,
            "hub_id": target_hub,
            "chat_id": target_chat,
            "bot_id": fallback_bot,
            "media": media_meta,
        }
    return result


@tool(
    "io.out.stream.publish",
    summary="Publish transport-independent browser stream data for a declarative webui receiver.",
    stability="experimental",
    examples=[
        "io.out.stream.publish('telemetry', {'value': 42}, _meta={'webspace_id':'default'})",
    ],
)
def stream_publish(
    receiver: str | None,
    data: Any = None,
    *,
    ts: float | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> Mapping[str, bool]:
    receiver_id = str(receiver or "").strip()
    if not receiver_id:
        return {"ok": False}

    payload: dict[str, Any] = {
        "receiver": receiver_id,
        "data": data,
        "ts": float(ts) if ts is not None else time.time(),
    }
    meta = _merged_meta(_meta)
    node_id = _local_node_id()
    if node_id:
        meta.setdefault("node_id", node_id)
        meta.setdefault("source_node_id", node_id)
    if meta:
        payload["_meta"] = meta

    _publish("io.out.stream.publish", payload, source="sdk.io.out")
    return {"ok": True}


def stream_variable_publish(
    receiver: str | None,
    value: Any = None,
    *,
    var_id: str | None = None,
    seq: int | None = None,
    updated_at: float | None = None,
    fingerprint: str | None = None,
    ttl_ms: int | None = None,
    ts: float | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> Mapping[str, bool]:
    """Publish one bounded replace-mode stream variable.

    The receiver still controls client-side rendering. This helper only gives
    stream producers a consistent envelope for freshness, stale-event rejection,
    and unchanged-payload dedupe.
    """

    receiver_id = str(receiver or "").strip()
    if not receiver_id:
        return {"ok": False}
    event_ts = float(ts) if ts is not None else time.time()
    variable_id = str(var_id or receiver_id).strip() or receiver_id
    payload: dict[str, Any] = {
        "id": variable_id,
        "value": value,
        "seq": int(seq) if seq is not None else time.time_ns(),
        "updated_at": float(updated_at) if updated_at is not None else event_ts,
        "fingerprint": str(fingerprint or _json_fingerprint(value)),
    }
    if ttl_ms is not None:
        try:
            ttl_value = int(ttl_ms)
            if ttl_value > 0:
                payload["ttl_ms"] = ttl_value
        except Exception:
            pass
    meta = dict(_meta or {})
    meta.setdefault("stream_semantics", "replace_variable")
    return stream_publish(receiver_id, payload, ts=event_ts, _meta=meta)
