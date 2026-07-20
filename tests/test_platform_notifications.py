from __future__ import annotations

from adaos.domain import make_client_subscription_record, make_projection_subscription
from adaos.services.eventbus import LocalEventBus
from adaos.services.platform_notifications import (
    PLATFORM_NOTIFICATIONS_CHANGED_EVENT,
    PLATFORM_NOTIFICATIONS_PROJECTION_KEY,
    clear_platform_notifications,
    platform_notifications_projection_record,
    platform_notifications_snapshot,
    replace_platform_notifications,
)
from adaos.services.projection_demand import clear_projection_demand_registry, write_client_subscription_record
from adaos.services.projection_dispatcher import clear_projection_dispatcher
from adaos.services.projection_event_bridge import register_projection_event_bridge
from adaos.services.projection_records import clear_projection_record_registry, get_projection_record


def setup_function() -> None:
    clear_platform_notifications()
    clear_projection_demand_registry()
    clear_projection_dispatcher()
    clear_projection_record_registry()


def test_platform_notification_registry_is_bounded_and_builds_projection_record() -> None:
    replace_platform_notifications(
        webspace_id="desktop",
        max_items=2,
        now=20.0,
        items=[
            {"id": "one", "level": "info", "message": "One", "ts": "1"},
            {"id": "two", "level": "success", "message": "Two", "ts": "2"},
            {"id": "three", "level": "error", "message": "Three", "ts": "3"},
        ],
    )

    snapshot = platform_notifications_snapshot(webspace_id="desktop")
    record = platform_notifications_projection_record(webspace_id="desktop")

    assert [item["id"] for item in snapshot["items"]] == ["two", "three"]
    assert record.meta.projection_key == PLATFORM_NOTIFICATIONS_PROJECTION_KEY
    assert record.meta.source_authority == "platform"
    assert record.status == "ready"
    assert record.data["notification_total"] == 2


def test_notification_event_refreshes_demanded_projection(monkeypatch) -> None:
    bus = LocalEventBus()
    materialized = []
    register_projection_event_bridge(bus)
    write_client_subscription_record(
        make_client_subscription_record(
            client_id="browser-1",
            device_id="desktop",
            session_id="session-1",
            webspace_id="desktop",
            role="operator",
            subscriptions=[
                make_projection_subscription(
                    projection_key=PLATFORM_NOTIFICATIONS_PROJECTION_KEY,
                    consumer_id="shell:notifications",
                    consumer_kind="shell",
                )
            ],
        )
    )

    async def fake_materialize_projection_records_to_yjs(**kwargs):
        materialized.append(kwargs)
        return {"ok": True, "accepted": True, **kwargs}

    monkeypatch.setattr(
        "adaos.services.projection_event_bridge.materialize_projection_records_to_yjs",
        fake_materialize_projection_records_to_yjs,
    )

    replace_platform_notifications(
        webspace_id="desktop",
        items=[{"id": "done", "level": "success", "message": "Install completed", "ts": "10"}],
        bus=bus,
        now=30.0,
    )

    stored = get_projection_record(
        webspace_id="desktop",
        projection_key=PLATFORM_NOTIFICATIONS_PROJECTION_KEY,
    )
    assert stored is not None
    assert stored.data["items"][0]["message"] == "Install completed"
    assert materialized == [
        {
            "webspace_id": "desktop",
            "projection_keys": [PLATFORM_NOTIFICATIONS_PROJECTION_KEY],
            "demanded_only": True,
        }
    ]


def test_notification_change_event_name_is_stable() -> None:
    assert PLATFORM_NOTIFICATIONS_CHANGED_EVENT == "adaos.platform.notifications.changed"
