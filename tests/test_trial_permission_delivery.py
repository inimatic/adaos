from dataclasses import replace
import json
import time

import pytest

from adaos.services import conversation_delivery, max_delivery
from adaos.services.agent_context import get_ctx
from adaos.services.development_tickets import DevelopmentTicketService
from adaos.services.trial_permission_actions import list_personal_actions, owner_ref
from adaos.services.trial_permission_notifications import (
    publish_notice,
    respond_from_messenger,
)
from test_notification_delivery import configure
from test_notification_runtime import notification_runtime
from test_pending_actions import pending_action_docs


@pytest.fixture
def trial_request(tmp_path, monkeypatch, pending_action_docs):
    monkeypatch.setattr("adaos.services.development_tickets.current_authority_state_dir", lambda: tmp_path)
    service = DevelopmentTicketService()
    ctx = get_ctx()
    digest = "sha256:" + "b" * 64
    ticket = service.report_publication_gate_failure(
        component_type="scenario", component_id="infra_ops", gate="live_readiness",
        error="TrialPermissionReviewRequired", task_id="task.one", webspace_id="release-desk",
        run_policy="await_user_permission_decision",
        metadata={"requires_user_decision": True, "decision_kind": "trial_permission",
                  "package_digest": digest, "declared_permissions": ["workspace.read", "workspace.write"],
                  "application_title": "Infrastructure", "package_version": "0.1.8"},
    )["ticket"]
    result = service.publish_publication_permission_pending_action(
        ticket["ticket_id"], object_type="scenario", object_id="infra_ops", task_id="task.one",
        webspace_id="release-desk", package_digest=digest, permissions=["workspace.read", "workspace.write"],
        ctx=ctx,
    )
    return ctx, result["ticket"], result["pending_action"]


@pytest.mark.asyncio
async def test_trial_decision_is_one_personal_card_without_writing_other_desktops(trial_request, pending_action_docs):
    ctx, ticket, action = trial_request
    owner = owner_ref(ctx)
    cards = await list_personal_actions(ctx, principal={"kind": "user", "actor_id": owner}, locale="ru")
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == action["id"]
    assert card["domain_ref"]["ticket_id"] == ticket["ticket_id"]
    assert card["webspace_id"] == "release-desk"
    assert "Infrastructure 0.1.8" in card["title"]
    assert "читать данные рабочего пространства" in card["request_text"]
    assert "При отказе" in card["request_text"]
    assert await list_personal_actions(ctx, principal={"kind": "user", "actor_id": "user:guest"}) == []
    assert set(pending_action_docs) == {"release-desk"}


@pytest.mark.asyncio
@pytest.mark.parametrize("first_transport", ["telegram", "max"])
async def test_selected_telegram_and_max_buttons_share_the_exact_decision(
    trial_request, notification_runtime, monkeypatch, first_transport,
):
    ctx, ticket, action = trial_request
    service, binding, _, clock = notification_runtime
    clock[0] = time.time()
    binding, _, _ = configure(service, replace(binding, principal_ref=owner_ref(ctx)))
    sent = []

    class Root:
        def request(self, method, path, **kwargs):
            if path.endswith("/capabilities"):
                return {"schema": "adaos.notifications.relay_capabilities.v1", "guard_version": 1,
                        "online_source_check": True, "personal_destinations_only": True,
                        "authority_routing_version": 1, "trial_permission_buttons": True}
            sent.extend(kwargs["json"]["messages"])
            return {"accepted": True, "guard_version": 1}

    monkeypatch.setattr(max_delivery, "root_client", Root)
    first = publish_notice(ctx, ticket, action, locale="ru")
    assert publish_notice(ctx, ticket, action, locale="ru") == first
    assert conversation_delivery.dispatch_once(transport="telegram")
    assert conversation_delivery.dispatch_once(transport="max")
    assert not conversation_delivery.dispatch_once(transport="max")

    tg_buttons = sent[0]["keyboard"]["inline_keyboard"][0]
    assert [button["text"] for button in tg_buttons] == ["Разрешить для Trial", "Отказать"]
    assert all(len(button["callback_data"].encode()) <= 64 for button in tg_buttons)
    with service.store.connect() as con:
        max_message = json.loads(con.execute("SELECT request_json FROM max_outbound_attempts").fetchone()[0])["messages"][0]
    max_button = max_message["keyboard"]["inline_keyboard"][1][0]["callback_data"]
    assert max_button != tg_buttons[1]["callback_data"]

    tg = {"io_type": "telegram", "hub_id": "A", "bot_id": "test", "chat_id": "101", "user_id": "101",
          "idempotency_key": "telegram-click"}
    max_meta = {"io_type": "max", "hub_id": "A", "bot_id": "test", "chat_id": "202", "user_id": "alice",
                "binding_id": "binding:max", "idempotency_key": "max-click"}
    with pytest.raises(PermissionError, match="verified messenger"):
        await respond_from_messenger(ctx, tg_buttons[0]["callback_data"], tg, source="io.http")
    with pytest.raises(PermissionError, match="another messenger binding"):
        await respond_from_messenger(ctx, tg_buttons[0]["callback_data"], {**tg, "user_id": "999"}, source="io.nats")
    with pytest.raises(PermissionError):
        await respond_from_messenger(ctx, max_button, tg, source="io.nats")
    attempts = [
        (tg_buttons[0]["callback_data"], tg, "io.nats", "approve"),
        (max_button, max_meta, "max.root.mailbox", "refuse"),
    ]
    if first_transport == "max":
        attempts.reverse()
    first_token, first_meta, first_source, decision = attempts[0]
    result = await respond_from_messenger(ctx, first_token, first_meta, source=first_source)
    assert result["action"]["id"] == action["id"]
    assert result["response"]["responder"]["actor"] == owner_ref(ctx)
    assert result["response"]["response_action_id"] == decision
    second_token, second_meta, second_source, _ = attempts[1]
    with pytest.raises(ValueError, match="already terminal"):
        await respond_from_messenger(ctx, second_token, second_meta, source=second_source)


@pytest.mark.parametrize("send_to_max", [False, True])
def test_center_only_and_deselected_endpoints_do_not_send(trial_request, notification_runtime, send_to_max):
    ctx, ticket, action = trial_request
    service, binding, _, clock = notification_runtime
    clock[0] = time.time()
    binding, _, endpoints = configure(service, replace(binding, principal_ref=owner_ref(ctx)))
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["defaults"] = {"mode": "immediate", "endpoint_ids": [endpoints[1]["endpoint_id"]]} if send_to_max else {
        "mode": "center_only", "endpoint_ids": [],
    }
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=1)
    publish_notice(ctx, ticket, action, locale="en")
    with service.store.connect() as con:
        routes = list(con.execute("SELECT route_id FROM conversation_output_intents"))
    assert [row[0] for row in routes] == (["route:max"] if send_to_max else [])
