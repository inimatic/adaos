from __future__ import annotations

from types import SimpleNamespace

from adaos.domain import make_client_subscription_record, make_projection_subscription
from adaos.services.eventbus import LocalEventBus
from adaos.services.platform_notifications import clear_platform_notifications, replace_platform_notifications
from adaos.services.projection_demand import clear_projection_demand_registry, write_client_subscription_record
from adaos.services.projection_diagnostics import projection_operator_diagnostics
from adaos.services.projection_dispatcher import clear_projection_dispatcher, projection_dispatcher_snapshot
from adaos.services.projection_event_bridge import register_projection_event_bridge
from adaos.services.projection_records import clear_projection_record_registry, get_projection_record
from adaos.services.status import StatusRegistry


def setup_function() -> None:
    clear_platform_notifications()
    clear_projection_demand_registry()
    clear_projection_dispatcher()
    clear_projection_record_registry()


def test_platform_emitters_keep_operator_truth_without_operational_skill(monkeypatch) -> None:
    """Local Wave 1 gate: core truth survives without infrastate_skill delivery."""

    bus = LocalEventBus()
    registry = StatusRegistry(bus=bus)
    materialized: list[dict[str, object]] = []
    register_projection_event_bridge(bus)
    monkeypatch.setattr("adaos.services.status_projection.get_ctx", lambda: SimpleNamespace(status_registry=registry))

    async def fake_materialize_projection_records_to_yjs(**kwargs):
        materialized.append(dict(kwargs))
        return {"ok": True, "accepted": True, **kwargs}

    monkeypatch.setattr(
        "adaos.services.projection_event_bridge.materialize_projection_records_to_yjs",
        fake_materialize_projection_records_to_yjs,
    )
    demanded_keys = ["status-card:runtime", "status-card:guard:yjs_pressure", "platform:notifications"]
    write_client_subscription_record(
        make_client_subscription_record(
            client_id="browser-local-acceptance",
            device_id="desktop",
            session_id="wave-1",
            webspace_id="desktop",
            role="operator",
            subscriptions=[
                make_projection_subscription(
                    projection_key=projection_key,
                    consumer_id=f"acceptance:{projection_key}",
                    consumer_kind="acceptance",
                    pinned=True,
                )
                for projection_key in demanded_keys
            ],
        )
    )

    registry.publish(
        {
            "id": "runtime",
            "owner": "core:runtime",
            "kind": "runtime",
            "scope": "platform",
            "webspace_id": "desktop",
            "status": "ready",
            "summary": "Runtime ready without operational skill delivery",
            "updated_at": 10.0,
        }
    )
    registry.publish(
        {
            "id": "guard:yjs_pressure",
            "owner": "core:guard",
            "kind": "guard",
            "scope": "platform",
            "webspace_id": "desktop",
            "status": "degraded",
            "summary": "Yjs pressure contained",
            "updated_at": 11.0,
        }
    )
    replace_platform_notifications(
        webspace_id="desktop",
        items=[
            {
                "id": "notification:guard-contained",
                "level": "warning",
                "message": "Operational skill quarantined; core status remains available",
                "ts": "2026-07-21T00:00:00Z",
                "source": "core:guard",
            }
        ],
        bus=bus,
        now=12.0,
    )

    records = {
        key: get_projection_record(webspace_id="desktop", projection_key=key)
        for key in demanded_keys
    }
    assert all(record is not None for record in records.values())
    assert records["status-card:runtime"].data["summary"].startswith("Runtime ready")
    assert records["status-card:guard:yjs_pressure"].data["status"] == "degraded"
    assert records["platform:notifications"].data["notification_total"] == 1

    diagnostics = projection_operator_diagnostics(webspace_id="desktop", now=13.0)
    by_key = {item["projection_key"]: item for item in diagnostics["active_projections"]}
    assert diagnostics["missing_handler_total"] == 0
    assert diagnostics["materialized_projection_total"] == 3
    assert by_key["status-card:runtime"]["status_card"]["published"] is True
    assert by_key["platform:notifications"]["handler"]["available"] is True

    dispatcher = projection_dispatcher_snapshot()
    assert dispatcher["stats"]["refreshed_total"] == 3
    assert {tuple(item["projection_keys"]) for item in materialized} == {
        ("status-card:runtime",),
        ("status-card:guard:yjs_pressure",),
        ("platform:notifications",),
    }
