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


def pending_latest_automation_notifications(
    base_dir: Path,
    messages: list[Mapping[str, Any]],
    *,
    now: float | None = None,
    settle_seconds: float = 2.0,
    retry_started_after: float = 60.0,
) -> list[dict[str, Any]]:
    """Return Telegram projections missed by an Automation worker bus.

    Automation persists its exact brief and lifecycle stages before it reports
    success.  A worker-local event bus may not reach the Router process, so the
    Router reconciles the newest task from that durable transcript.  Existing
    transport receipts keep the normal live path and this recovery path
    idempotent.
    """

    current_time = time.time() if now is None else float(now)
    eligible: list[Mapping[str, Any]] = []
    for message in messages:
        meta = message.get("_meta") if isinstance(message.get("_meta"), Mapping) else {}
        if meta.get("schema") not in {
            "adaos.builder.automation_input.v1",
            "adaos.builder.automation_notification.v1",
        }:
            continue
        if not str(meta.get("task_id") or "").strip():
            continue
        try:
            message_time = float(message.get("ts") or 0.0)
        except (TypeError, ValueError):
            message_time = 0.0
        if message_time and current_time - message_time < max(0.0, settle_seconds):
            continue
        eligible.append(message)
    if not eligible:
        return []

    def _order(item: Mapping[str, Any]) -> tuple[float, float]:
        try:
            sequence = float(item.get("seq") or 0.0)
        except (TypeError, ValueError):
            sequence = 0.0
        try:
            timestamp = float(item.get("ts") or 0.0)
        except (TypeError, ValueError):
            timestamp = 0.0
        return sequence, timestamp

    latest = max(eligible, key=_order)
    latest_meta = latest.get("_meta") if isinstance(latest.get("_meta"), Mapping) else {}
    latest_task_id = str(latest_meta.get("task_id") or "").strip()
    task_messages = []
    for message in eligible:
        meta = message.get("_meta") if isinstance(message.get("_meta"), Mapping) else {}
        if str(meta.get("task_id") or "").strip() == latest_task_id:
            task_messages.append(message)

    def _receipt(identity: str) -> dict[str, Any]:
        if not identity:
            return {}
        path = (
            Path(base_dir)
            / "state"
            / "router"
            / "builder-deliveries"
            / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
        )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _settled(receipt: Mapping[str, Any]) -> bool:
        phase = str(receipt.get("phase") or "").strip().lower()
        if phase == "sent":
            return True
        if phase != "started":
            return False
        try:
            updated_at = float(receipt.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        return bool(updated_at and current_time - updated_at < retry_started_after)

    pending: list[dict[str, Any]] = []
    for message in sorted(task_messages, key=_order):
        projection = builder_chat_notifications(message)
        if projection is None:
            continue
        source_identity, items = projection
        if _settled(_receipt(source_identity)):
            continue
        for item in items:
            item_identity = str(item.get("id") or "").strip()
            if not _settled(_receipt(item_identity)):
                pending.append(item)
    return pending
