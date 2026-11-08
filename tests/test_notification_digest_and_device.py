from copy import deepcopy
import json

import pytest

from adaos.services import conversation_delivery
from adaos.services.notifications import delivery, device_delivery
from adaos.services.notifications.endpoints import EndpointService
from test_notification_runtime import notification_runtime, emit
from test_notification_delivery import configure, Adapter


def digest_mode(service, binding):
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"]["mode"] = "digest"
    prefs["budgets"]["digest_seconds"] = 60
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=prefs["revision"])


def test_digest_is_durable_bounded_and_not_duplicated_by_policy_rechecks(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    digest_mode(service, binding)
    emit(service, binding)
    emit(service, binding, source_id="R2", event_id="E2", payload={"title": "Second"})
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM notification_digest_windows").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM conversation_output_intents").fetchone()[0] == 0
    clock[0] += 61
    assert delivery.flush_digests(service) == 1
    service.recheck_preferences()
    assert delivery.flush_digests(service) == 0
    adapter = Adapter()
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert len(adapter.sent) == 1
    assert "Document" in adapter.sent[0][0]["payload"]["text"]
    assert "Second" in adapter.sent[0][0]["payload"]["text"]
    assert not conversation_delivery.dispatch_once(adapter, now=clock[0])


def test_withdrawn_digest_member_is_omitted_before_render(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    digest_mode(service, binding)
    emit(service, binding, payload={"title": "PRIVATE"})
    emit(service, binding, source_id="R2", event_id="E2", payload={"title": "Allowed"})
    service.withdraw(binding, "R1")
    clock[0] += 61
    delivery.flush_digests(service)
    adapter = Adapter()
    conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert len(adapter.sent) == 1
    assert "PRIVATE" not in adapter.sent[0][0]["payload"]["text"]
    assert "Allowed" in adapter.sent[0][0]["payload"]["text"]


def test_mute_after_digest_render_prevents_disclosure_and_rebuilds_unsent_summary(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    digest_mode(service, binding)
    emit(service, binding, payload={"title": "PRIVATE"})
    emit(service, binding, source_id="R2", event_id="E2", payload={"title": "Allowed"})
    clock[0] += 61
    delivery.flush_digests(service)
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["rules"] = [{"scope": {"source_id": "R1"}, "choice": {"mode": "off", "endpoint_ids": []}}]
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=prefs["revision"])
    adapter = Adapter()
    conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert adapter.sent == []
    delivery.flush_digests(service)
    conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert len(adapter.sent) == 1
    assert "PRIVATE" not in adapter.sent[0][0]["payload"]["text"]


def test_unmute_only_applies_to_future_events(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    emit(service, binding)
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"]["mode"] = "off"
    prefs = service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=1)
    service.recheck_preferences()
    adapter = Adapter()
    conversation_delivery.dispatch_once(adapter, now=clock[0])
    prefs["defaults"]["mode"] = "immediate"
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=2)
    service.recheck_preferences()
    assert not conversation_delivery.dispatch_once(adapter, now=clock[0])
    emit(service, binding, event_id="E2", source_revision=2)
    conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert len(adapter.sent) == 1


def test_device_presented_receipt_is_not_read_and_cannot_cross_session(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=())
    # configure() with no provider route leaves its original subnet; use the actual test Hub.
    from dataclasses import replace
    from adaos.services.agent_context import get_ctx
    binding = replace(binding, subnet_ref="subnet:" + str(get_ctx().config.subnet_id))
    endpoints = EndpointService()
    device = endpoints.admit_device(binding.principal_ref, binding.subnet_ref, "session:browser1", permission="granted")
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"] = {"mode": "immediate", "endpoint_ids": [device["endpoint_id"]]}
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=prefs["revision"])
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    receipt = emit(service, binding)
    assert conversation_delivery.dispatch_once(now=clock[0])
    with pytest.raises(PermissionError):
        device_delivery.pull(binding.principal_ref, device["endpoint_id"], "session:other")
    pending = device_delivery.pull(binding.principal_ref, device["endpoint_id"], "session:browser1")["items"]
    assert len(pending) == 1
    result = device_delivery.acknowledge(binding.principal_ref, device["endpoint_id"], "session:browser1",
        intent_id=pending[0]["intent_id"], presentation_token=pending[0]["presentation_token"], evidence="presented")
    assert result == {"status": "presented", "read": False}
    assert service.get(binding.principal_ref, receipt["notification_id"])["read_revision"] == 0
    assert device_delivery.pull(binding.principal_ref, device["endpoint_id"], "session:browser1")["items"] == []


def test_forget_scrubs_private_snapshot_and_keeps_replay_tombstone(notification_runtime):
    service, binding, _, _ = notification_runtime
    binding, _, _ = configure(service, binding)
    original = emit(service, binding, payload={"title": "PRIVATE_DOCUMENT_TITLE"})
    assert service.forget_user(binding.principal_ref)["deleted_records"] == 1
    assert service.get(binding.principal_ref, original["notification_id"]) is None
    assert emit(service, binding, payload={"title": "PRIVATE_DOCUMENT_TITLE"})["notification_id"] is None
    with service.store.connect() as con:
        for row in con.execute("SELECT payload_json FROM conversation_response_outbox"):
            assert "PRIVATE_DOCUMENT_TITLE" not in row[0]
