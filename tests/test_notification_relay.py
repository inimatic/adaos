import json

import pytest

from adaos.services import conversation_delivery
from adaos.services.notifications import delivery, relay
from test_notification_runtime import notification_runtime, emit
from test_notification_delivery import configure, Adapter


def guard_for(service):
    with service.store.connect() as con:
        row = con.execute("SELECT payload_json FROM conversation_response_outbox ORDER BY envelope_id LIMIT 1").fetchone()
    return json.loads(row[0])["payload"]["data"]["relay_guard"]


def test_fresh_relay_check_rejects_mute_after_local_handoff(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    emit(service, binding)
    guard = guard_for(service)
    assert relay.authorize(guard["intent_id"], guard["token"], now=clock[0])["allowed"]
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"]["mode"] = "off"
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=1)
    proof = relay.authorize(guard["intent_id"], guard["token"], now=clock[0])
    assert not proof["allowed"] and proof["reason"] == "muted"
    with pytest.raises(PermissionError):
        relay.authorize(guard["intent_id"], "a" * 48, now=clock[0])


def test_relay_rechecks_revision_endpoint_and_source_lifetime(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    emit(service, binding)
    guard = guard_for(service)
    emit(service, binding, event_id="E2", source_revision=2, payload={"title": "New"})
    assert relay.authorize(guard["intent_id"], guard["token"], now=clock[0])["reason"] == "notification_revision_superseded"


def test_independent_provider_lanes_do_not_claim_peers(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding)
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    emit(service, binding)
    adapter = Adapter()
    assert conversation_delivery.dispatch_once(adapter, transport="max", now=clock[0])
    assert adapter.sent[0][1]["transport"] == "max"
    assert not conversation_delivery.dispatch_once(adapter, transport="max", now=clock[0])
    assert conversation_delivery.dispatch_once(adapter, transport="telegram", now=clock[0])


def test_old_root_cannot_ignore_notification_guard():
    from adaos.domain.notifications import NotificationUnavailable
    class OldRoot:
        def request(self, *args, **kwargs): return {"ok": True}
    with pytest.raises(NotificationUnavailable):
        relay.require_root_guard(OldRoot())


def test_max_secondary_queue_stops_after_mute_without_root_post(notification_runtime, monkeypatch):
    from adaos.services import max_delivery
    service, binding, _, clock = notification_runtime
    # The production adapter fences against wall time as well as the lease;
    # keep both sides on the same virtual clock throughout this queue test.
    monkeypatch.setattr(max_delivery.time, "time", lambda: clock[0])
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    emit(service, binding)
    assert conversation_delivery.dispatch_once(now=clock[0])
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"]["mode"] = "off"
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=1)
    class NeverRoot:
        def request(self, *args, **kwargs): pytest.fail("muted secondary queue reached Root")
    assert max_delivery.dispatch_once(NeverRoot())
    with service.store.connect() as con:
        assert con.execute("SELECT status FROM max_outbound_attempts").fetchone()[0] == "failed"
