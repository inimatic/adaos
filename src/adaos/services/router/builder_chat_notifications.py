"""Project Builder transcript messages into the configured notification route."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import hashlib
import json
from pathlib import Path
import time

from .telegram_projection import _telegram_text_chunks


def builder_chat_notifications(payload: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    channel = str(payload.get("dialog_channel_id") or meta.get("dialog_channel_id") or "")
    owner = str(meta.get("conversation_owner") or "")
    if channel != "builder" and owner != "skill:builder_skill":
        return None
    # Telegram-origin turns already use their reply route. Lifecycle transcript
    # events must also be projected: worker ui.notify may not cross its bridge.
    if str(meta.get("io_type") or "").lower() in {"telegram", "tg"}:
        return None
    text = str(payload.get("text") or "").strip()
    if not text:
        return None
    is_user = str(payload.get("from") or "") == "user"
    identity = str(
        (meta.get("source_message_id") if is_user else meta.get("response_idempotency_key"))
        or payload.get("id") or ""
    ).strip()
    if not identity:
        return None
    topic = str(payload.get("thread_id") or meta.get("thread_id") or meta.get("topic_id") or "")
    title = "Builder · Постановка" if is_user else "Builder · Ответ"
    if topic:
        title += f" · {topic.rsplit(':', 1)[-1]}"
    chunks = _telegram_text_chunks(text, limit=3000)
    notifications = []
    for index, chunk in enumerate(chunks):
        part = f" ({index + 1}/{len(chunks)})" if len(chunks) > 1 else ""
        notifications.append({
            "id": f"builder-chat:{identity}:{index}",
            "text": f"{title}{part}\n{chunk}",
            "_meta": {
                "schema": "adaos.builder.chat_notification.v1",
                "notification_scope": "subnet",
                "skip_voice_chat": True,
                "source_message_id": identity,
                "thread_id": topic,
            },
        })
    return identity, notifications


def claim_builder_notification(seen: dict[str, None], identity: str) -> bool:
    """Share deduplication across a transcript and its direct lifecycle notice."""
    if identity in seen:
        return False
    seen[identity] = None
    if len(seen) > 1024:
        seen.pop(next(iter(seen)))
    return True


def record_builder_delivery(base_dir: Path, payload: Mapping[str, Any], **updates: Any) -> None:
    """Keep a private transport receipt without retaining chat text or credentials."""
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    if meta.get("schema") not in {"adaos.builder.chat_notification.v1", "adaos.builder.automation_notification.v1"}:
        return
    identity = str(payload.get("id") or meta.get("response_idempotency_key") or meta.get("source_message_id") or "")
    if not identity:
        return
    root = Path(base_dir) / "state" / "router" / "builder-deliveries"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    try:
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        previous = {}
    path.write_text(json.dumps({**previous, "message_id": identity, "updated_at": time.time(), **updates}, ensure_ascii=False), encoding="utf-8")
