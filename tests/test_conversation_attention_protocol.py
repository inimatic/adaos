from __future__ import annotations

import pytest

from adaos.services import conversation_attention, durable_delivery


def test_async_reply_protocol_separates_work_materialization_delivery_and_acknowledgement() -> None:
    route = durable_delivery.create_reply_route(
        "conversation:protocol",
        route_id="route:protocol",
        thread_id="thread:origin",
        transport="telegram",
        destination_ref={"chat_id": "100", "bot_id": "main"},
        principal_scope=["user:local"],
        channel_context={"webspace_id": "dev1", "channel_id": "builder"},
    )
    task_ref = {"kind": "task", "id": "RUN-protocol"}
    categories = ["accepted", "started", "progress", "input_required", "resumed"]
    envelopes = [
        durable_delivery.enqueue_response(
            "conversation:protocol",
            category,
            text=category,
            task_ref=task_ref,
            reply_route_ids=[route["route_id"]],
            envelope_id=f"response:protocol:{category}",
        )
        for category in categories
    ]
    terminal = durable_delivery.enqueue_response(
        "conversation:protocol",
        "terminal",
        text="Completed",
        data={"outcome": "success"},
        task_ref=task_ref,
        reply_route_ids=[route["route_id"]],
        envelope_id="response:protocol:terminal",
    )

    assert [item["sequence"] for item in [*envelopes, terminal]] == [1, 2, 3, 4, 5, 6]
    assert route["thread_id"] == "thread:origin"
    assert route["delivery_policy"]["retry_without_execution"] is True
    assert terminal["materialization_status"] == "pending"
    materialized = durable_delivery.mark_response_materialized(
        terminal["envelope_id"],
        message_ref={"conversation_id": "conversation:protocol", "message_id": "message:6"},
    )
    assert materialized["materialization_status"] == "materialized"
    assert materialized["status"] == "pending"

    attempt = durable_delivery.claim_delivery(
        terminal["envelope_id"], route["route_id"], presentation_id="telegram:message"
    )
    durable_delivery.complete_delivery(
        attempt["attempt_id"], delivered=True, receipt={"telegram_message_id": "206"}
    )
    acknowledged = durable_delivery.acknowledge_response(
        terminal["envelope_id"], receipt={"callback_query_id": "ack-1"}
    )
    assert acknowledged["status"] == "acknowledged"
    assert acknowledged["acknowledged_at"] is not None

    duplicate = durable_delivery.enqueue_response(
        "conversation:protocol",
        "terminal",
        text="Completed",
        data={"outcome": "success"},
        task_ref=task_ref,
        reply_route_ids=[route["route_id"]],
        envelope_id="response:protocol:terminal:duplicate",
    )
    assert duplicate["envelope_id"] == terminal["envelope_id"]
    with pytest.raises(durable_delivery.DurableDeliveryError, match="terminal response already exists"):
        durable_delivery.enqueue_response(
            "conversation:protocol",
            "terminal",
            text="A conflicting second result",
            task_ref=task_ref,
            reply_route_ids=[route["route_id"]],
            envelope_id="response:protocol:terminal:conflict",
        )


def test_attention_policy_coalesces_progress_and_respects_quiet_hours_with_escalation() -> None:
    policy = conversation_attention.default_attention_policy(policy_id="attention:test")
    policy["quiet_hours"].update(
        {"enabled": True, "start": "22:00", "end": "08:00", "timezone": "UTC"}
    )
    progress = conversation_attention.plan_attention(
        "progress",
        coalesce_key="RUN-1:progress",
        policy=policy,
        now="2026-08-01T23:00:00+00:00",
    )
    required = conversation_attention.plan_attention(
        "input_required",
        policy=policy,
        now="2026-08-01T23:00:00+00:00",
    )

    assert progress["disposition"] == "update_status"
    assert progress["coalesce"] is True
    assert progress["retain_evidence"] is True
    assert required["attention"] == "important"
    assert required["notify"] is False
    assert required["quiet_hours_applied"] is True
    assert required["escalation_reason"] == "input_required"
