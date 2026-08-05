from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from typing import Any

from adaos.services.agent_context import get_ctx


def _telegram_text_chunks(text: str, *, limit: int = 3500) -> list[str]:
    remaining = str(text or "").strip()
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _telegram_output_projection(
    payload: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    if str(meta.get("io_type") or "").strip().lower() != "telegram":
        return None
    if bool(meta.get("telegram_delivery_handled")):
        return None
    if str(payload.get("from") or "hub").strip().lower() in {"user", "human"}:
        return None
    chat_id = str(meta.get("chat_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not chat_id or not text:
        return None
    bot_id = str(meta.get("bot_id") or "main-bot").strip() or "main-bot"
    hub_id = str(meta.get("hub_id") or "").strip()
    if not hub_id:
        try:
            hub_id = str(get_ctx().config.subnet_id or "").strip()
        except Exception:
            hub_id = ""
    messages = [{"type": "text", "text": item} for item in _telegram_text_chunks(text)]
    actions = [dict(item) for item in payload.get("actions") or [] if isinstance(item, Mapping)]
    keyboard_rows: list[list[dict[str, str]]] = []
    for action in actions[:8]:
        label = str(action.get("label") or "").strip()
        token = str(action.get("token") or "").strip()
        action_config = action.get("action") if isinstance(action.get("action"), Mapping) else {}
        action_params = action_config.get("params") if isinstance(action_config.get("params"), Mapping) else {}
        url = str(
            action.get("url")
            or (
                action_params.get("url")
                if str(action_config.get("type") or "").strip() == "openUrl"
                else ""
            )
            or ""
        ).strip()
        if label and url and len(url) <= 2048 and re.match(r"^https?://", url, flags=re.IGNORECASE):
            keyboard_rows.append([{"text": label[:64], "url": url}])
        elif label and token and len(token.encode("utf-8")) <= 64:
            keyboard_rows.append([{"text": label[:64], "callback_data": token}])
    if messages and keyboard_rows:
        messages[0]["keyboard"] = {"inline_keyboard": keyboard_rows}
    options = {"reply_to": meta.get("reply_to")} if meta.get("reply_to") else None
    correlation = str(
        payload.get("id")
        or meta.get("message_id")
        or meta.get("turn_trace_id")
        or meta.get("request_id")
        or meta.get("update_id")
        or ""
    ).strip()
    if not correlation:
        correlation = hashlib.sha256(
            f"{chat_id}:{text}:{payload.get('ts') or ''}".encode("utf-8")
        ).hexdigest()[:24]
    operation_key = f"tg-dialog:{hub_id or 'hub'}:{bot_id}:{chat_id}:{correlation}"
    out = {
        "target": {"bot_id": bot_id, "hub_id": hub_id, "chat_id": chat_id},
        "messages": messages,
        "options": options,
        "_protocol": {
            "flow_id": "hub_root.integration.telegram",
            "message_type": "command",
            "delivery_class": "must_not_lose",
            "stream_id": f"hub-integration:telegram:{hub_id or 'hub'}:{bot_id}:{chat_id}",
            "message_id": f"tgmsg:{hashlib.sha256(operation_key.encode('utf-8')).hexdigest()[:24]}",
            "operation_key": operation_key,
            "authority_epoch": f"hub:{hub_id or 'unknown'}",
            "issued_at": time.time(),
            "ttl_ms": 600_000,
        },
    }
    return f"tg.output.{bot_id}.chat.{chat_id}", out


def _telegram_interaction_consumed_projection(
    interaction: Mapping[str, Any],
    response: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Project a terminal button presentation into the originating message.

    An Interaction accepts one response.  Once its typed action is durably
    stored, the old keyboard must not keep advertising choices that are no
    longer legal.  The original prompt remains visible and receives a compact
    record of what the user selected.
    """

    meta = response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {}
    if str(meta.get("io_type") or "").strip().lower() != "telegram":
        return None
    chat_id = str(meta.get("chat_id") or "").strip()
    # Only a callback query proves that the referenced message was authored by
    # this bot and is therefore editable.  ``reply_to`` on a text fallback
    # points at the user's message and must never be used as an edit target.
    raw_message_id = meta.get("telegram_source_message_id")
    try:
        message_id = int(raw_message_id)
    except (TypeError, ValueError):
        return None
    if not chat_id or message_id <= 0:
        return None

    consumed = response.get("consumed_command") if isinstance(response.get("consumed_command"), Mapping) else {}
    if str(response.get("status") or "answered").strip() != "answered" or not consumed:
        return None
    label = str(consumed.get("label") or "").strip()
    command = str(consumed.get("command") or "").strip()
    target_ref = consumed.get("target_ref") if isinstance(consumed.get("target_ref"), Mapping) else {}
    target_id = str(target_ref.get("id") or "").strip()
    target_title = str(target_ref.get("title") or "").strip()
    if command == "builder.project.select" and target_id:
        selected = f"{target_title} ({target_id})" if target_title and target_title != target_id else target_id
    else:
        selected = label or target_title or target_id or command or "действие"
    suffix = f"✓ Выбрано: {selected}"
    prompt = str(interaction.get("prompt") or "").strip()
    max_prompt = max(0, 4096 - len(suffix) - 2)
    if len(prompt) > max_prompt:
        prompt = (prompt[: max(0, max_prompt - 1)].rstrip() + "…") if max_prompt else ""
    text = f"{prompt}\n\n{suffix}" if prompt else suffix

    bot_id = str(meta.get("bot_id") or "main-bot").strip() or "main-bot"
    hub_id = str(meta.get("hub_id") or "").strip()
    if not hub_id:
        try:
            hub_id = str(get_ctx().config.subnet_id or "").strip()
        except Exception:
            hub_id = ""
    response_id = str(response.get("response_id") or "").strip()
    interaction_id = str(interaction.get("interaction_id") or "").strip()
    operation_key = (
        f"tg-interaction-consumed:{hub_id or 'hub'}:{bot_id}:{chat_id}:"
        f"{response_id or interaction_id or message_id}"
    )
    callback_query_id = str(meta.get("telegram_callback_query_id") or "").strip()
    out = {
        "target": {"bot_id": bot_id, "hub_id": hub_id, "chat_id": chat_id},
        "messages": [{"type": "text", "text": text}],
        "options": {
            "edit_message_id": message_id,
            **({"callback_query_id": callback_query_id} if callback_query_id else {}),
        },
        "_protocol": {
            "flow_id": "hub_root.integration.telegram",
            "message_type": "interaction_presentation_update",
            "delivery_class": "must_not_lose",
            "stream_id": f"hub-integration:telegram:{hub_id or 'hub'}:{bot_id}:{chat_id}",
            "message_id": f"tgmsg:{hashlib.sha256(operation_key.encode('utf-8')).hexdigest()[:24]}",
            "operation_key": operation_key,
            "authority_epoch": f"hub:{hub_id or 'unknown'}",
            "issued_at": time.time(),
            "ttl_ms": 600_000,
            "presentation_state": "consumed",
            "source_message_id": str(message_id),
        },
    }
    return f"tg.output.{bot_id}.chat.{chat_id}", out
