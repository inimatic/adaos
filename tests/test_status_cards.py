from __future__ import annotations

from types import SimpleNamespace

from adaos.domain import Event
from adaos.sdk import status as sdk_status
from adaos.services.eventbus import LocalEventBus
from adaos.services.status import StatusRegistry, make_status_card, register_status_registry


def test_status_card_normalizes_canonical_status_and_staleness() -> None:
    card = make_status_card(
        id="runtime",
        owner="skill:infrastate_skill",
        kind="runtime",
        scope="infrastate",
        status="ready",
        summary="ready",
        updated_at=10.0,
        ttl_ms=1000,
        details_ref={"kind": "stream", "receiver": "infrastate.runtime"},
        route={"kind": "stream", "receiver": "infrastate.runtime"},
    )

    payload = card.to_dict(now_ts=12.0)

    assert payload["status"] == "online"
    assert payload["severity"] == "info"
    assert payload["stale"] is True
    assert payload["expires_at"] == 11.0
    assert payload["details_ref"]["receiver"] == "infrastate.runtime"


def test_status_card_fingerprint_ignores_volatile_times() -> None:
    first = make_status_card(
        id="runtime",
        owner="skill:infrastate_skill",
        kind="runtime",
        scope="infrastate",
        status="warning",
        summary="degraded",
        updated_at=10.0,
    )
    second = make_status_card(
        id="runtime",
        owner="skill:infrastate_skill",
        kind="runtime",
        scope="infrastate",
        status="warning",
        summary="degraded",
        updated_at=20.0,
        version=9,
    )

    assert first.fingerprint == second.fingerprint
    assert make_status_card(
        id="runtime",
        owner="skill:infrastate_skill",
        kind="runtime",
        scope="infrastate",
        status="degraded",
        summary="changed",
        fingerprint=first.fingerprint,
    ).fingerprint != first.fingerprint


def test_status_registry_dedupes_versions_and_marks_stale() -> None:
    registry = StatusRegistry()

    first = registry.publish(
        {
            "id": "runtime",
            "owner": "skill:infrastate_skill",
            "kind": "runtime",
            "scope": "infrastate",
            "status": "ready",
            "summary": "ready",
            "updated_at": 10.0,
            "ttl_ms": 1000,
        }
    )
    duplicate = registry.publish(
        {
            "id": "runtime",
            "owner": "skill:infrastate_skill",
            "kind": "runtime",
            "scope": "infrastate",
            "status": "ready",
            "summary": "ready",
            "updated_at": 10.5,
            "ttl_ms": 1000,
        }
    )
    changed = registry.publish(
        {
            "id": "runtime",
            "owner": "skill:infrastate_skill",
            "kind": "runtime",
            "scope": "infrastate",
            "status": "degraded",
            "summary": "route unstable",
            "updated_at": 12.0,
            "ttl_ms": 1000,
        }
    )
    snapshot = registry.snapshot(now_ts=14.0)

    assert first["changed"] is True
    assert duplicate["changed"] is False
    assert duplicate["card"]["version"] == 1
    assert changed["card"]["version"] == 2
    assert snapshot["cards"][0]["stale"] is True
    assert snapshot["diagnostics"]["changed_total"] == 2
    assert snapshot["diagnostics"]["unchanged_total"] == 1


def test_register_status_registry_consumes_sdk_status_events(monkeypatch) -> None:
    bus = LocalEventBus()
    registry = register_status_registry(bus)
    changed: list[Event] = []
    bus.subscribe("adaos.status.card.changed", lambda event: changed.append(event))
    monkeypatch.setattr(sdk_status, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr(
        sdk_status,
        "get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )

    result = sdk_status.publish_status(
        id="runtime",
        kind="runtime",
        scope="infrastate",
        status="ready",
        summary="ready",
        webspace_id="desktop",
    )
    snapshot = registry.snapshot(webspace_id="desktop")

    assert result["ok"] is True
    assert snapshot["total"] == 1
    assert snapshot["cards"][0]["owner"] == "skill:infrastate_skill"
    assert changed[0].payload["card"]["id"] == "runtime"


def test_agent_context_status_registry_registers_bus_subscription(tmp_path) -> None:
    from adaos.services.eventbus import emit
    from adaos.services.testing.bootstrap import bootstrap_test_ctx

    slot_dir = tmp_path / ".adaos" / "skills" / "infrastate_skill" / "slots" / "dev"
    slot_dir.mkdir(parents=True)
    handle = bootstrap_test_ctx(skill_name="infrastate_skill", skill_slot_dir=slot_dir, secrets={})
    try:
        registry = handle.ctx.status_registry
        emit(
            handle.ctx.bus,
            "adaos.status.card.single",
            {
                "card": {
                    "id": "runtime",
                    "owner": "skill:infrastate_skill",
                    "kind": "runtime",
                    "scope": "infrastate",
                    "status": "ready",
                }
            },
            "test",
        )

        snapshot = registry.snapshot()

        assert snapshot["total"] == 1
        assert snapshot["cards"][0]["status"] == "online"
    finally:
        handle.teardown()


def test_publish_status_stream_publishes_card_and_stream_variable(monkeypatch) -> None:
    bus = LocalEventBus()
    seen_status: list[Event] = []
    seen_stream: list[Event] = []
    bus.subscribe("adaos.status.card.batch", lambda event: seen_status.append(event))
    bus.subscribe("io.out.stream.publish", lambda event: seen_stream.append(event))
    monkeypatch.setattr(sdk_status, "get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr("adaos.sdk.io.out.get_ctx", lambda: SimpleNamespace(bus=bus))
    monkeypatch.setattr("adaos.sdk.io.out.load_config", lambda: SimpleNamespace(node_id="node-1"))
    monkeypatch.setattr(
        sdk_status,
        "get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )
    monkeypatch.setattr(
        "adaos.sdk.io.out.get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )

    result = sdk_status.publish_status_stream(
        "infrastate.runtime",
        id="runtime",
        kind="runtime",
        scope="infrastate",
        status="warning",
        summary="route reconnecting",
        webspace_id="desktop",
        ttl_ms=30000,
        seq=7,
        _meta={"webspace_id": "desktop"},
    )

    assert result["ok"] is True
    assert seen_status[0].payload["cards"][0]["details_ref"]["receiver"] == "infrastate.runtime"
    stream_payload = seen_stream[0].payload
    assert stream_payload["receiver"] == "infrastate.runtime"
    assert stream_payload["data"]["id"] == "runtime"
    assert stream_payload["data"]["seq"] == 7
    assert stream_payload["data"]["value"]["route"]["receiver"] == "infrastate.runtime"
