from __future__ import annotations

import time
from threading import RLock
from typing import Any, Iterable, Mapping

from adaos.domain import ProjectionRecord, ProjectionStatus, make_projection_record, projection_fingerprint
from adaos.services.projection_dispatcher import (
    ProjectionRefreshContext,
    ProjectionRefreshResult,
    register_projection_refresh_handler,
    unregister_projection_refresh_handler,
)


PLATFORM_NOTIFICATIONS_PROJECTION_KEY = "platform:notifications"
PLATFORM_NOTIFICATIONS_CHANGED_EVENT = "adaos.platform.notifications.changed"
PLATFORM_NOTIFICATIONS_PROJECTION_KIND = "platform-notifications"
PLATFORM_NOTIFICATIONS_SCHEMA = "adaos.platform-notifications.v1"

_LOCK = RLock()
_ITEMS: dict[str, list[dict[str, Any]]] = {}
_UPDATED_AT: dict[str, float] = {}


def _normalize_item(value: Mapping[str, Any]) -> dict[str, Any] | None:
    message = str(value.get("message") or "").strip()
    if not message:
        return None
    operation_id = str(value.get("operation_id") or value.get("code") or "").strip()
    level = str(value.get("level") or "info").strip().lower() or "info"
    notification_id = str(value.get("id") or "").strip()
    if not notification_id:
        fingerprint = projection_fingerprint(
            {
                "level": level,
                "message": message,
                "operation_id": operation_id,
                "ts": value.get("ts"),
            }
        )
        notification_id = f"notification:{fingerprint[:20]}"
    return {
        "id": notification_id,
        "level": level,
        "message": message,
        "ts": str(value.get("ts") or ""),
        "source": str(value.get("source") or "operations").strip() or "operations",
        "code": operation_id or str(value.get("code") or "").strip() or None,
        "operation_id": operation_id or None,
        "target_kind": str(value.get("target_kind") or "").strip() or None,
        "target_id": str(value.get("target_id") or "").strip() or None,
        "details": dict(value.get("details")) if isinstance(value.get("details"), Mapping) else None,
    }


def replace_platform_notifications(
    *,
    webspace_id: str,
    items: Iterable[Mapping[str, Any]],
    max_items: int = 40,
    bus: Any | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    token = str(webspace_id or "").strip()
    if not token:
        raise ValueError("webspace_id is required")
    normalized = [item for value in items if (item := _normalize_item(value)) is not None]
    normalized = normalized[-max(1, int(max_items)) :]
    ts = float(now if now is not None else time.time())
    with _LOCK:
        previous = list(_ITEMS.get(token, []))
        changed = previous != normalized
        _ITEMS[token] = normalized
        _UPDATED_AT[token] = ts
    if changed and bus is not None:
        try:
            from adaos.services.eventbus import emit

            emit(
                bus,
                PLATFORM_NOTIFICATIONS_CHANGED_EVENT,
                {
                    "webspace_id": token,
                    "projection_key": PLATFORM_NOTIFICATIONS_PROJECTION_KEY,
                    "notification_total": len(normalized),
                },
                "platform.notifications",
                source_authority="platform",
                scope={"webspace_id": token},
                schema=PLATFORM_NOTIFICATIONS_CHANGED_EVENT,
                version=1,
                priority="normal",
                generate_event_id=True,
                ts=ts,
            )
        except Exception:
            pass
    return {
        "ok": True,
        "accepted": changed,
        "webspace_id": token,
        "projection_key": PLATFORM_NOTIFICATIONS_PROJECTION_KEY,
        "notification_total": len(normalized),
        "updated_at": ts,
    }


def platform_notifications_snapshot(*, webspace_id: str) -> dict[str, Any]:
    token = str(webspace_id or "").strip()
    with _LOCK:
        items = [dict(item) for item in _ITEMS.get(token, [])]
        updated_at = _UPDATED_AT.get(token)
    return {
        "schema": PLATFORM_NOTIFICATIONS_SCHEMA,
        "webspace_id": token,
        "items": items,
        "notification_total": len(items),
        "updated_at": updated_at,
    }


def platform_notifications_projection_record(*, webspace_id: str) -> ProjectionRecord:
    snapshot = platform_notifications_snapshot(webspace_id=webspace_id)
    return make_projection_record(
        projection_key=PLATFORM_NOTIFICATIONS_PROJECTION_KEY,
        kind=PLATFORM_NOTIFICATIONS_PROJECTION_KIND,
        data=snapshot,
        webspace_id=webspace_id,
        status=ProjectionStatus.READY,
        source="platform.notifications",
        source_authority="platform",
        access={"audience": "shared", "visibility": "operator", "read_only": True},
        lifecycle_reason="materialized",
        updated_at=snapshot.get("updated_at") or time.time(),
    )


def refresh_platform_notifications_projection(context: ProjectionRefreshContext) -> ProjectionRefreshResult:
    if context.projection_key != PLATFORM_NOTIFICATIONS_PROJECTION_KEY:
        return ProjectionRefreshResult(
            projection_key=context.projection_key,
            webspace_id=context.webspace_id,
            status=ProjectionStatus.UNAVAILABLE.value,
            reason="platform_notifications_projection_key_invalid",
        )
    record = platform_notifications_projection_record(webspace_id=context.webspace_id)
    return ProjectionRefreshResult(
        projection_key=context.projection_key,
        webspace_id=context.webspace_id,
        status=record.status,
        record=record.to_dict(),
        reason=record.meta.lifecycle_reason,
    )


def ensure_platform_notifications_projection_handler() -> None:
    register_projection_refresh_handler(
        PLATFORM_NOTIFICATIONS_PROJECTION_KEY,
        refresh_platform_notifications_projection,
    )


def remove_platform_notifications_projection_handler() -> bool:
    return unregister_projection_refresh_handler(PLATFORM_NOTIFICATIONS_PROJECTION_KEY)


def clear_platform_notifications() -> None:
    with _LOCK:
        _ITEMS.clear()
        _UPDATED_AT.clear()


__all__ = [
    "PLATFORM_NOTIFICATIONS_CHANGED_EVENT",
    "PLATFORM_NOTIFICATIONS_PROJECTION_KEY",
    "clear_platform_notifications",
    "ensure_platform_notifications_projection_handler",
    "platform_notifications_projection_record",
    "platform_notifications_snapshot",
    "refresh_platform_notifications_projection",
    "remove_platform_notifications_projection_handler",
    "replace_platform_notifications",
]
