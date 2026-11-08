from copy import deepcopy
import json

import pytest

from adaos.services import conversation_delivery, durable_delivery
from adaos.services.agent_context import get_ctx
from adaos.services.notifications import delivery
from adaos.services.notifications.endpoints import EndpointService
from adaos.services.notifications.runtime import NotificationService
from test_notification_runtime import notification_runtime, emit


class Adapter:
    def __init__(self):
        self.sent = []

    def send(self, envelope, route, attempt):
        self.sent.append((envelope, route, attempt))
        return {"delivered": True, "receipt": {"provider_message_id": str(len(self.sent)), "evidence": "provider_created"}}


def configure(service, binding, *, transports=("telegram", "max")):
    ctx = get_ctx()
    from types import SimpleNamespace
    ctx.config = SimpleNamespace(subnet_id="A")
    endpoints = EndpointService(clock=service.clock)
    items = []
    for transport in transports:
        route = durable_delivery.create_reply_route("notice-scope", route_id="route:" + transport,
            transport=transport, principal_scope=[binding.principal_ref],
            destination_ref={"hub_id": str(ctx.config.subnet_id), "chat_id": "101" if transport == "telegram" else "202", "bot_id": "test",
                             "user_id": "alice", "binding_id": "binding:" + transport},
            channel_context={"chat_type": "private" if transport == "telegram" else "dialog"})
        # Service-only route admission itself enforces the actual configured Hub.
        from dataclasses import replace
        binding = replace(binding, subnet_ref="subnet:" + str(ctx.config.subnet_id))
        endpoint = endpoints.admit_route(binding.principal_ref, binding.subnet_ref, route["route_id"], label=transport)
        items.append(endpoint)
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"] = {"mode": "immediate", "endpoint_ids": [x["endpoint_id"] for x in items]}
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=0)
    return binding, endpoints, items


def test_shared_outbox_multicast_and_no_second_transcript(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, endpoints, items = configure(service, binding)
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    receipt = emit(service, binding)
    adapter = Adapter()
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert not conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert {x[1]["transport"] for x in adapter.sent} == {"telegram", "max"}
    assert all(x[0]["payload"]["text"].endswith("Reviewer: Completed: Document") for x in adapter.sent)
    assert emit(service, binding) == receipt
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM conversation_output_intents WHERE status='delivered'").fetchone()[0] == 2


def test_latest_mute_and_source_withdrawal_stop_queued_sends(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding)
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    emit(service, binding)
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"]["mode"] = "off"
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=1)
    adapter = Adapter()
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert adapter.sent == []
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM conversation_output_intents WHERE status='suppressed'").fetchone()[0] == 2


def test_quiet_notice_is_rescheduled_after_restart_using_current_policy(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding)
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["quiet_hours"].update(enabled=True, timezone="Europe/Moscow")
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=1)
    receipt = emit(service, binding)
    assert service.get(binding.principal_ref, receipt["notification_id"])["decision"]["reason"] == "quiet_hours"
    adapter = Adapter()
    assert not conversation_delivery.dispatch_once(adapter, now=clock[0])
    clock[0] = 1794200400  # 05:00 UTC, 08:00 Moscow
    restarted = NotificationService(clock=service.clock)
    assert restarted.schedule_due() == 1
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert len(adapter.sent) == 2


def test_revoked_endpoint_and_source_grant_are_final_dispatch_gates(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, endpoints, items = configure(service, binding)
    emit(service, binding)
    endpoints.set_status(binding.principal_ref, items[0]["endpoint_id"], status="paused", expected_revision=1)
    # No fake authority override: there is no Application grant for this fixture.
    adapter = Adapter()
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert adapter.sent == []
    with service.store.connect() as con:
        errors = [r[0] for r in con.execute("SELECT last_error FROM conversation_output_intents")]
    assert "endpoint_paused_or_revoked" in errors
    assert "permission_denied" in errors


def test_old_content_is_suppressed_and_new_content_keeps_stable_author(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding)
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    emit(service, binding)
    emit(service, binding, event_id="E2", source_revision=2, payload={"title": "Final"})
    adapter = Adapter()
    for _ in range(4):
        assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert len(adapter.sent) == 2
    assert all(x[0]["payload"]["text"].endswith("Reviewer: Completed: Final") for x in adapter.sent)


def test_conversation_dispatch_rechecks_quiet_policy_not_the_frozen_flag():
    from adaos.services import conversation_store, conversation_publication
    from adaos.services.conversation_attention import default_attention_policy
    attention = default_attention_policy()
    attention["quiet_hours"].update(enabled=True, timezone="Europe/Moscow")
    conversation_store.upsert_conversation(conversation_id="quiet:legacy", owner="skill:test", webspace_id="test",
        policy={"output_route_ids": ["route:quiet"], "attention_policy": attention})
    durable_delivery.create_reply_route("quiet:legacy", route_id="route:quiet", transport="telegram",
        principal_scope=["user:alice"], destination_ref={"hub_id": "A"})
    result = conversation_publication.publish(conversation_id="quiet:legacy", owner="skill:test", webspace_id="test",
        publication_key="one", text="Completed", route_ids=["route:quiet"])
    # The envelope was created with default attention; dispatch must use current policy.
    envelope = durable_delivery.get_envelope(result["intent_ids"][0])
    assert envelope["attention_plan"]["quiet_hours_applied"] is False
    adapter = Adapter()
    assert conversation_delivery.dispatch_once(adapter, now=1794168000)
    assert adapter.sent == []
    assert not conversation_delivery.dispatch_once(adapter, now=1794168001)
    assert conversation_delivery.dispatch_once(adapter, now=1794200400)
    assert len(adapter.sent) == 1
