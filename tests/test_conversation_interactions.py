from __future__ import annotations

import pytest

from adaos.domain import Event
from adaos.sdk import chat
from adaos.services import conversation_interactions, conversation_store
from adaos.services.eventbus import LocalEventBus
from adaos.services.router.service import _compact_voice_chat_stream_message, _telegram_output_projection


def _choice_interaction(conversation_id: str = "conv.builder") -> dict[str, object]:
    return {
        "interaction_id": "interaction.builder.route",
        "prompt": "How should this change proceed?",
        "input_spec": {
            "kind": "choice",
            "required_fields": [],
            "choices": [
                {"value": "prototype", "label": "Prototype first", "description": None},
                {"value": "automation", "label": "Implementation directly", "description": None},
            ],
            "sensitive": False,
        },
        "actions": [
            {
                "action_id": "prototype",
                "label": "Prototype first",
                "command": "builder.change.route",
                "value": "prototype",
                "risk": "local_reversible",
                "confirmation_required": False,
            },
            {
                "action_id": "automation",
                "label": "Implementation directly",
                "command": "builder.change.route",
                "value": "automation",
                "risk": "local_reversible",
                "confirmation_required": False,
            },
        ],
        "required_capabilities": [],
        "optional_capabilities": ["buttons"],
        "fallbacks": ["numbered_text", "plain_text", "unsupported"],
        "task_ref": {"kind": "task", "id": "task.builder.1"},
        "workflow_ref": {"kind": "workflow", "id": "change.builder.1"},
    }


def test_capability_negotiation_preserves_semantics_across_web_telegram_and_text() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.capabilities",
        owner="skill:builder",
        prompt="Choose a route",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        optional_capabilities=("buttons",),
        interaction_id="interaction.capabilities",
    )
    web = conversation_interactions.standard_capability_profile("web")
    telegram = conversation_interactions.standard_capability_profile("telegram")
    text = conversation_interactions.standard_capability_profile("text")

    web_view = conversation_interactions.negotiate_presentation(interaction, web)
    telegram_view = conversation_interactions.negotiate_presentation(interaction, telegram)
    text_view = conversation_interactions.negotiate_presentation(interaction, text)

    assert web_view["mode"] == "buttons"
    assert telegram_view["mode"] == "buttons"
    assert text_view["mode"] == "numbered_text"
    assert [item["command"] for item in web_view["actions"]] == [
        item["command"] for item in text_view["actions"]
    ]
    assert set(web_view["action_tokens"].values()) == {"prototype", "automation"}
    assert telegram["handoff"]["cross_channel"] is True
    assert telegram["acknowledgement"] == "action"
    assert telegram["permission_boundary"] == "separate"
    assert telegram["business_availability_boundary"] == "separate"
    assert telegram_view["plan"]["requirements_id"] == interaction["requirements"]["requirements_id"]
    assert telegram_view["plan"]["semantic_equivalent"] is True
    assert telegram_view["plan"]["renegotiate_on_profile_change"] is True


def test_sensitive_interaction_never_degrades_to_plain_text() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.secret",
        owner="skill:test",
        prompt="Enter the secret",
        input_spec={
            "kind": "text",
            "required_fields": [],
            "choices": [],
            "sensitive": True,
        },
        fallbacks=("plain_text", "unsupported"),
        interaction_id="interaction.secret",
    )
    text = conversation_interactions.standard_capability_profile("text")

    presentation = conversation_interactions.negotiate_presentation(interaction, text)

    assert presentation["supported"] is False
    assert presentation["mode"] == "unsupported"
    assert "secure_input" in presentation["reason_code"]


def test_chat_request_is_durable_materializes_actions_and_resumes_by_token() -> None:
    bus = LocalEventBus()
    visible: list[Event] = []
    bus.subscribe("io.out.chat.append", lambda event: visible.append(event))

    result = chat.request(
        _choice_interaction(),
        conversation_id="conv.builder",
        owner="skill:builder",
        webspace_id="desktop-dev",
        channel_id="builder",
        route_id="dialog",
        actor_id="agent:builder_skill:builder",
        bus=bus,
    )

    assert result["handle"]["durable"] is True
    assert result["handle"]["status"] == "awaiting_input"
    assert visible[0].payload["interaction"]["interaction_id"] == "interaction.builder.route"
    assert len(visible[0].payload["actions"]) == 2
    token = result["presentation"]["actions"][0]["token"]

    answered = chat.respond(
        "interaction.builder.route",
        actor_id="user:local",
        expected_generation=0,
        idempotency_key="web:click:1",
        action_token=token,
    )
    duplicate = chat.respond(
        "interaction.builder.route",
        actor_id="user:local",
        expected_generation=0,
        idempotency_key="web:click:1",
        action_token=token,
    )

    assert answered["interaction"]["status"] == "answered"
    assert answered["interaction"]["generation"] == 1
    assert answered["response"]["values"]["choice"] == "prototype"
    assert answered["response"]["presentation_id"] == result["presentation"]["presentation_id"]
    assert answered["response"]["consumed_command"]["command"] == "builder.change.route"
    assert duplicate["duplicate"] is True
    assert conversation_store.get_interaction("interaction.builder.route")["status"] == "answered"


def test_unbound_text_requires_disambiguation_for_multiple_pending_interactions() -> None:
    for suffix in ("one", "two"):
        conversation_interactions.create_interaction(
            conversation_id="conv.ambiguous",
            owner="skill:test",
            prompt=f"Question {suffix}",
            input_spec={
                "kind": "text",
                "required_fields": [],
                "choices": [],
                "sensitive": False,
            },
            interaction_id=f"interaction.{suffix}",
        )

    result = conversation_interactions.resolve_unbound_text(
        "conv.ambiguous",
        "yes",
        actor_id="user:local",
        idempotency_key="message:yes",
    )

    assert result["status"] == "ambiguous"
    assert result["reason_code"] == "multiple_pending_interactions"
    assert len(result["candidates"]) == 2
    assert all(item["generation"] == 0 for item in result["candidates"])


def test_telegram_projection_converts_negotiated_actions_to_inline_keyboard() -> None:
    projection = _telegram_output_projection(
        {
            "id": "message.interaction",
            "from": "hub",
            "text": "Choose",
            "actions": [
                {"label": "Prototype first", "token": "ia:0:abc"},
                {"label": "Implementation", "token": "ia:0:def"},
            ],
        },
        {
            "io_type": "telegram",
            "chat_id": "100",
            "bot_id": "main-bot",
            "hub_id": "hub-1",
        },
    )

    assert projection is not None
    _subject, payload = projection
    keyboard = payload["messages"][0]["keyboard"]["inline_keyboard"]
    assert keyboard[0][0] == {"text": "Prototype first", "callback_data": "ia:0:abc"}


def test_web_voice_projection_preserves_interaction_action_token() -> None:
    compact = _compact_voice_chat_stream_message(
        {
            "id": "message.interaction",
            "from": "hub",
            "text": "Choose",
            "actions": [
                {
                    "action_id": "inspect",
                    "label": "Show process",
                    "command": "builder.process.inspect",
                    "token": "ia:0:abc",
                }
            ],
        }
    )

    assert compact["actions"][0]["token"] == "ia:0:abc"


def test_response_rejects_payload_reuse_and_stale_action() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.conflict",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.conflict",
    )
    profile = conversation_interactions.standard_capability_profile("web")
    presentation = conversation_interactions.negotiate_presentation(interaction, profile)
    first_token, second_token = [item["token"] for item in presentation["actions"]]
    conversation_interactions.submit_response(
        "interaction.conflict",
        actor_id="user:local",
        expected_generation=0,
        idempotency_key="click:same",
        action_token=first_token,
    )

    with pytest.raises(conversation_interactions.ConversationInteractionError, match="idempotency conflict"):
        conversation_interactions.submit_response(
            "interaction.conflict",
            actor_id="user:local",
            expected_generation=0,
            idempotency_key="click:same",
            action_token=second_token,
        )
    with pytest.raises(conversation_interactions.ConversationInteractionError, match="stale interaction generation"):
        conversation_interactions.submit_response(
            "interaction.conflict",
            actor_id="user:local",
            expected_generation=0,
            idempotency_key="click:new",
            action_token=second_token,
        )


def test_action_token_rejects_actor_outside_principal_scope() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.principal",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.principal",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )

    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="principal is not authorized",
    ):
        conversation_interactions.submit_action_token(
            presentation["actions"][0]["token"],
            actor_id="agent:untrusted",
            idempotency_key="click:wrong-principal",
        )


def test_interaction_lifecycle_accepts_completes_cancels_and_expires() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.lifecycle",
        owner="skill:test",
        prompt="Say something",
        input_spec={
            "kind": "text",
            "required_fields": [],
            "choices": [],
            "sensitive": False,
        },
        interaction_id="interaction.lifecycle.complete",
    )
    answered = conversation_interactions.submit_response(
        interaction["interaction_id"],
        actor_id="user:local",
        expected_generation=0,
        idempotency_key="message:lifecycle",
        original_text="done",
    )
    accepted = conversation_interactions.accept_response(
        interaction["interaction_id"],
        answered["response"]["response_id"],
        expected_generation=1,
    )
    completed = conversation_interactions.transition_interaction(
        interaction["interaction_id"],
        "complete",
        expected_generation=2,
        reason="workflow_consumed_response",
    )
    assert accepted["status"] == "accepted"
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None

    cancellable = conversation_interactions.create_interaction(
        conversation_id="conv.lifecycle",
        owner="skill:test",
        prompt="Wait",
        interaction_id="interaction.lifecycle.cancel",
    )
    cancelled = conversation_interactions.transition_interaction(
        cancellable["interaction_id"],
        "cancel",
        expected_generation=0,
        reason="user_cancelled",
    )
    assert cancelled["status"] == "cancelled"

    conversation_interactions.create_interaction(
        conversation_id="conv.lifecycle",
        owner="skill:test",
        prompt="Expired",
        interaction_id="interaction.lifecycle.expire",
        expires_at="2026-07-30T10:00:00+00:00",
        now="2026-07-30T09:00:00+00:00",
    )
    expired = conversation_interactions.expire_due_interactions(
        now="2026-07-30T10:01:00+00:00"
    )
    assert [item["interaction_id"] for item in expired] == ["interaction.lifecycle.expire"]
    assert expired[0]["status"] == "expired"


def test_workflow_description_projects_bound_semantic_actions() -> None:
    interaction = conversation_interactions.interaction_from_workflow_description(
        {
            "schema": "adaos.workflow.description.v1",
            "workflow_type": "builder.change",
            "definition_version": "1.0.0",
            "instance_id": "change:1",
            "state": "prototype_review",
            "generation": 4,
            "target": {"kind": "aggregate", "id": "change:1"},
            "allowed_commands": [
                {
                    "command": "approve_prototype",
                    "transition_id": "approve_prototype",
                    "target_ref": {"kind": "aggregate", "id": "change:1"},
                    "risk": {"class": "isolated_write", "confirmation": "required"},
                    "authority": {"actors": ["user"], "permissions": ["builder.change"]},
                    "capability_requirements": {
                        "required": [],
                        "optional": ["buttons"],
                        "fallback": "numbered_text",
                    },
                    "explanation": "Approve the prototype",
                }
            ],
        },
        conversation_id="conv.workflow.projection",
        owner="skill:builder",
        interaction_id="interaction.workflow.projection",
        workflow_ref={"kind": "workflow", "id": "change:1", "generation": 4},
    )

    action = interaction["actions"][0]
    assert action["command"] == "approve_prototype"
    assert action["expected_generation"] == 4
    assert action["principal_scope"] == ["user"]
    assert action["target_ref"]["id"] == "change:1"
    assert action["confirmation_required"] is True


def test_capability_action_limit_falls_back_without_dropping_commands() -> None:
    actions = [
        {
            "action_id": f"action-{index}",
            "label": f"Action {index}",
            "command": f"command.{index}",
            "value": str(index),
            "risk": "read",
            "confirmation_required": False,
        }
        for index in range(10)
    ]
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.limit",
        owner="skill:test",
        prompt="Choose",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [
                {"value": str(index), "label": f"Action {index}", "description": None}
                for index in range(10)
            ],
            "sensitive": False,
        },
        actions=actions,
        interaction_id="interaction.limit",
    )
    telegram = conversation_interactions.standard_capability_profile("telegram")

    presentation = conversation_interactions.negotiate_presentation(interaction, telegram)

    assert presentation["mode"] == "numbered_text"
    assert presentation["reason_code"] == "action_limit_numbered_fallback"
    assert len(presentation["actions"]) == 10


def test_profile_change_renegotiates_presentation_without_changing_semantics() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.reconnect",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.reconnect",
    )
    rich = conversation_interactions.channel_capability_profile(
        "client:reconnect",
        version=1,
        transport="web",
        client="browser",
        surface="chat",
        capabilities={"text": True, "buttons": True},
        limits={"actions": 10},
    )
    compact = conversation_interactions.channel_capability_profile(
        "client:reconnect",
        version=2,
        transport="web",
        client="browser",
        surface="chat",
        capabilities={"text": True, "buttons": False},
        limits={"actions": 0},
    )

    rich_view = conversation_interactions.negotiate_presentation(interaction, rich)
    compact_view = conversation_interactions.negotiate_presentation(interaction, compact)

    assert rich_view["mode"] == "buttons"
    assert compact_view["mode"] == "numbered_text"
    assert rich_view["interaction_generation"] == compact_view["interaction_generation"] == 0
    assert rich_view["action_tokens"] == compact_view["action_tokens"]
    assert compact_view["plan"]["fallback_used"] == "numbered_text"
    assert compact_view["plan"]["reason_code"] == "numbered_fallback"
