from __future__ import annotations

from adaos.services import conversation_interactions, conversation_store, durable_delivery


def _route(conversation_id: str = "conversation:delivery") -> dict[str, object]:
    return durable_delivery.create_reply_route(
        conversation_id,
        route_id=f"route:{conversation_id}",
        transport="telegram",
        destination_ref={"chat_id": "100", "bot_id": "main"},
        principal_scope=["user:local"],
    )


def test_progress_is_coalesced_and_terminal_delivery_recovers_without_business_replay() -> None:
    route = _route()
    business_invocations = 1
    accepted = durable_delivery.enqueue_response(
        "conversation:delivery",
        "accepted",
        text="Accepted",
        command_id="builder.start_automation",
        reply_route_ids=[route["route_id"]],
        envelope_id="response:accepted",
    )
    first_progress = durable_delivery.enqueue_response(
        "conversation:delivery",
        "progress",
        text="10%",
        task_ref={"kind": "task", "id": "RUN-1"},
        reply_route_ids=[route["route_id"]],
        coalesce_key="RUN-1:progress",
        envelope_id="response:progress:1",
    )
    second_progress = durable_delivery.enqueue_response(
        "conversation:delivery",
        "progress",
        text="70%",
        task_ref={"kind": "task", "id": "RUN-1"},
        reply_route_ids=[route["route_id"]],
        coalesce_key="RUN-1:progress",
        envelope_id="response:progress:2",
    )
    terminal = durable_delivery.enqueue_response(
        "conversation:delivery",
        "terminal",
        text="Completed",
        task_ref={"kind": "task", "id": "RUN-1"},
        reply_route_ids=[route["route_id"]],
        envelope_id="response:terminal",
    )

    assert [accepted["sequence"], first_progress["sequence"], second_progress["sequence"], terminal["sequence"]] == [1, 2, 3, 4]
    assert durable_delivery.get_envelope(first_progress["envelope_id"])["status"] == "superseded"
    first_attempt = durable_delivery.claim_delivery(
        terminal["envelope_id"], route["route_id"], presentation_id="telegram:message"
    )
    durable_delivery.complete_delivery(
        first_attempt["attempt_id"], delivered=False, error="transport offline"
    )

    recovered = durable_delivery.recover_delivery(conversation_id="conversation:delivery")
    assert terminal["envelope_id"] in {item["envelope_id"] for item in recovered["resumable"]}
    second_attempt = durable_delivery.claim_delivery(
        terminal["envelope_id"], route["route_id"], presentation_id="telegram:message"
    )
    assert second_attempt["attempt_number"] == 2
    assert second_attempt["idempotency_key"] == first_attempt["idempotency_key"]
    durable_delivery.complete_delivery(
        second_attempt["attempt_id"],
        delivered=True,
        receipt={"telegram_message_id": "200"},
    )

    assert business_invocations == 1
    result = durable_delivery.terminal_result("conversation:delivery")
    assert result["status"] == "delivered"
    assert result["payload"]["text"] == "Completed"


def test_idempotency_route_authority_and_queryable_undeliverable_result() -> None:
    route = _route("conversation:idempotency")
    first = durable_delivery.enqueue_response(
        "conversation:idempotency",
        "notification",
        text="Принято без повторного запуска",
        reply_route_ids=[route["route_id"]],
        envelope_id="response:idempotent",
    )
    duplicate = durable_delivery.enqueue_response(
        "conversation:idempotency",
        "notification",
        text="Принято без повторного запуска",
        reply_route_ids=[route["route_id"]],
        envelope_id="response:idempotent",
    )
    assert duplicate == first

    terminal = durable_delivery.enqueue_response(
        "conversation:offline",
        "terminal",
        text="Root is offline; result retained locally",
        sensitivity="sensitive",
        envelope_id="response:offline:terminal",
    )
    assert terminal["status"] == "undeliverable"
    assert durable_delivery.terminal_result("conversation:offline")["payload"]["text"] == "[redacted]"
    assert (
        durable_delivery.terminal_result("conversation:offline", include_sensitive=True)["payload"]["text"]
        == "Root is offline; result retained locally"
    )


def test_delayed_human_input_survives_service_reentry() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conversation:delayed",
        owner="skill:builder",
        prompt="Describe the missing acceptance condition",
        input_spec={
            "kind": "text",
            "required_fields": [],
            "choices": [],
            "sensitive": False,
        },
        interaction_id="interaction:delayed",
    )

    recovered = conversation_store.get_interaction(interaction["interaction_id"])
    result = conversation_interactions.submit_response(
        recovered["interaction_id"],
        actor_id="user:local",
        expected_generation=recovered["generation"],
        idempotency_key="delayed:message:1",
        original_text="Сохранять список после перезапуска.",
    )

    assert result["interaction"]["status"] == "answered"
    assert result["response"]["values"]["text"] == "Сохранять список после перезапуска."
