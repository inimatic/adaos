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


def test_capability_negotiation_preserves_trusted_choices_and_fails_closed_on_text() -> None:
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
    assert text_view["mode"] == "unsupported"
    assert text_view["supported"] is False
    assert text_view["action_tokens"] == {}
    assert all(item["enabled"] is False for item in text_view["actions"])
    assert "assurance" in text_view["reason_code"]
    assert set(web_view["action_tokens"].values()) == {"prototype", "automation"}
    assert all(item["assurance"]["trusted_interface_required"] for item in web_view["actions"])
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
    assert answered["response"]["consumed_command"]["action_id"] == "prototype"
    assert answered["response"]["consumed_command"]["label"] == "Prototype first"
    assert answered["response"]["consumed_command"]["value"] == "prototype"
    assert answered["response"]["assurance_receipt"]["mode"] == "trusted_interface_required"
    assert answered["response"]["assurance_receipt"]["presentation_id"] == result["presentation"]["presentation_id"]
    assert duplicate["duplicate"] is True
    assert answered["dispatch"]["status"] == "pending"
    assert answered["dispatch"]["command"] == answered["response"]["consumed_command"]
    assert duplicate["dispatch"]["dispatch_id"] == answered["dispatch"]["dispatch_id"]
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


def test_response_and_generation_are_committed_atomically() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.atomic",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.atomic",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )
    accepted = conversation_interactions.submit_action_token(
        presentation["actions"][0]["token"],
        actor_id="user:local",
        idempotency_key="atomic:first",
    )
    losing_response = dict(accepted["response"])
    losing_response.update(
        {
            "response_id": "response.atomic.loser",
            "idempotency_key": "atomic:loser",
            "interaction_generation": 0,
            "metadata": {"request_digest": "sha256:losing-request"},
        }
    )
    losing_interaction = dict(accepted["interaction"])

    with pytest.raises(ValueError, match="stale interaction generation"):
        conversation_store.commit_interaction_response(
            losing_response,
            losing_interaction,
            expected_generation=0,
        )

    assert conversation_store.get_interaction_response("response.atomic.loser") is None
    assert conversation_store.get_interaction_dispatch(response_id="response.atomic.loser") is None
    assert conversation_store.get_interaction_dispatch(
        response_id=str(accepted["response"]["response_id"])
    )["status"] == "pending"
    assert conversation_store.get_interaction("interaction.atomic")["generation"] == 1
    assert len(conversation_store.list_interaction_responses("interaction.atomic")) == 1


def test_dispatch_obligation_lease_requires_reconciliation_contract_before_reclaim() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.dispatch-lease",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.dispatch-lease",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )
    accepted = conversation_interactions.submit_action_token(
        presentation["actions"][0]["token"],
        actor_id="user:local",
        idempotency_key="dispatch-lease:first",
    )
    response_id = str(accepted["response"]["response_id"])
    first = conversation_store.claim_interaction_dispatch(
        response_id,
        lease_owner="worker:one",
        lease_seconds=10,
        now_epoch=100,
        now_iso="2026-10-07T00:00:00+00:00",
    )
    assert first["status"] == "dispatching"
    assert first["attempt_count"] == 1
    with pytest.raises(ValueError, match="active executor lease"):
        conversation_store.claim_interaction_dispatch(
            response_id,
            lease_owner="worker:two",
            now_epoch=105,
        )
    with pytest.raises(ValueError, match="requires an admitted reconciliation contract"):
        conversation_store.claim_interaction_dispatch(
            response_id,
            lease_owner="worker:two",
            now_epoch=111,
            now_iso="2026-10-07T00:00:11+00:00",
        )
    with pytest.raises(ValueError, match="requires an admitted reconciliation contract"):
        conversation_store.claim_interaction_dispatch(
            response_id,
            lease_owner="worker:two",
            reconciliation_contract="adaos.governed_workflow.dispatch_replay.v1",
            now_epoch=111,
            now_iso="2026-10-07T00:00:11+00:00",
        )


def test_governed_workflow_dispatch_lease_can_recover_before_effect_and_close_once() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.workflow-dispatch-lease",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        workflow_ref={"kind": "workflow", "id": "workflow:lease", "generation": 0},
        interaction_id="interaction.workflow-dispatch-lease",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )
    accepted = conversation_interactions.submit_action_token(
        presentation["actions"][0]["token"],
        actor_id="user:local",
        idempotency_key="workflow-dispatch-lease:first",
    )
    response_id = str(accepted["response"]["response_id"])
    conversation_store.claim_interaction_dispatch(
        response_id,
        lease_owner="worker:one",
        lease_seconds=10,
        now_epoch=100,
        now_iso="2026-10-07T00:00:00+00:00",
    )
    recovered = conversation_store.claim_interaction_dispatch(
        response_id,
        lease_owner="worker:two",
        reconciliation_contract="adaos.governed_workflow.dispatch_replay.v1",
        now_epoch=111,
        now_iso="2026-10-07T00:00:11+00:00",
    )
    assert recovered["attempt_count"] == 2
    completed = conversation_store.complete_interaction_dispatch(
        recovered["dispatch_id"],
        lease_owner="worker:two",
        status="succeeded",
        outcome={"result": "ok"},
        now_epoch=112,
        now_iso="2026-10-07T00:00:12+00:00",
    )
    assert completed["status"] == "succeeded"
    replay = conversation_store.complete_interaction_dispatch(
        recovered["dispatch_id"],
        lease_owner="worker:two",
        status="succeeded",
        outcome={"result": "ignored-idempotent-replay"},
        now_epoch=113,
    )
    assert replay == completed
    with pytest.raises(ValueError, match="another outcome"):
        conversation_store.complete_interaction_dispatch(
            recovered["dispatch_id"],
            lease_owner="worker:two",
            status="failed",
            outcome={"reason": "late"},
            now_epoch=114,
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


def test_standard_action_presets_are_semantic_contracts_not_labels() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.action-semantics",
        owner="skill:test",
        prompt="Inspect the evidence",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [{"value": "details", "label": "Details", "description": None}],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "inspect",
                "label": "Details",
                "command": "evidence.details",
                "value": "details",
                "risk": "read",
                "confirmation_required": False,
                "preset": "details",
            }
        ],
        interaction_id="interaction.action-semantics",
    )

    semantics = interaction["actions"][0]["semantics"]
    assert semantics == {
        "schema": "adaos.conversation.action_semantics.v1",
        "preset": "details",
        "effect_class": "observation",
        "operation": "inspect_details",
        "executor": "client",
        "mutates_domain": False,
        "records_consent": False,
        "terminal": False,
        "effect_ref": None,
        "schedule": None,
        "assertion_required": True,
    }

    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="preview action must be read-only",
    ):
        conversation_interactions.create_interaction(
            conversation_id="conv.false-preview",
            owner="skill:test",
            prompt="Repair",
            actions=[
                {
                    "action_id": "repair",
                    "label": "Preview",
                    "command": "repair.apply",
                    "value": "repair",
                    "risk": "destructive",
                    "confirmation_required": True,
                    "preset": "preview",
                }
            ],
            interaction_id="interaction.false-preview",
        )

    legacy = dict(interaction)
    legacy["actions"] = [dict(interaction["actions"][0])]
    legacy["actions"][0].pop("semantics")
    assert conversation_interactions.interaction_handle(legacy).interaction_id == interaction["interaction_id"]


def test_declared_test_requires_exact_effect_and_completion_assertion() -> None:
    digest = "sha256:" + "a" * 64
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.test-effect",
        owner="skill:test",
        prompt="Run the declared test",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [{"value": "run", "label": "Run test", "description": None}],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "run",
                "label": "Run test",
                "command": "test.run",
                "value": "run",
                "risk": "read",
                "confirmation_required": False,
                "preset": "test",
                "semantics": {
                    "effect_ref": {"kind": "test", "id": "suite:smoke", "digest": digest}
                },
            }
        ],
        interaction_id="interaction.test-effect",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )
    accepted = conversation_interactions.submit_action_token(
        presentation["actions"][0]["token"],
        actor_id="user:local",
        idempotency_key="test-effect:run",
    )
    dispatch = conversation_store.claim_interaction_dispatch(
        accepted["response"]["response_id"],
        lease_owner="worker:test",
    )
    with pytest.raises(ValueError, match="requires an effect_assertion"):
        conversation_store.complete_interaction_dispatch(
            dispatch["dispatch_id"],
            lease_owner="worker:test",
            status="succeeded",
            outcome={"result": "callback returned"},
        )

    completed = conversation_store.complete_interaction_dispatch(
        dispatch["dispatch_id"],
        lease_owner="worker:test",
        status="succeeded",
        outcome={
            "effect_assertion": {
                "schema": "adaos.conversation.action_effect_assertion.v1",
                "preset": "test",
                "operation": "run_declared_test",
                "effect_ref": {"kind": "test", "id": "suite:smoke", "digest": digest},
                "observed": True,
                "domain_mutated": False,
                "test_executed": True,
            }
        },
    )
    assert completed["status"] == "succeeded"
    assert completed["command"]["semantics"]["preset"] == "test"


def test_snooze_is_bounded_by_the_existing_consent_deadline() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.snooze",
        owner="skill:test",
        prompt="Remind me later",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [{"value": "later", "label": "In one hour", "description": None}],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "later",
                "label": "In one hour",
                "command": "interaction.snooze",
                "value": "later",
                "risk": "read",
                "confirmation_required": False,
                "preset": "snooze",
                "semantics": {"schedule": {"resume_at": "2026-10-07T11:00:00+00:00"}},
            }
        ],
        interaction_id="interaction.snooze",
        expires_at="2026-10-07T12:00:00+00:00",
        now="2026-10-07T10:00:00+00:00",
    )

    assert interaction["actions"][0]["semantics"]["schedule"] == {
        "resume_at": "2026-10-07T11:00:00+00:00",
        "consent_deadline": "2026-10-07T12:00:00+00:00",
    }

    for resume_at in ("2026-10-07T12:00:00+00:00", "2026-10-07T13:00:00+00:00"):
        with pytest.raises(
            conversation_interactions.ConversationInteractionError,
            match="cannot reach or extend the consent deadline",
        ):
            conversation_interactions.create_interaction(
                conversation_id=f"conv.snooze.invalid.{resume_at}",
                owner="skill:test",
                prompt="Remind me later",
                actions=[
                    {
                        "action_id": "later",
                        "label": "Later",
                        "command": "interaction.snooze",
                        "value": "later",
                        "risk": "read",
                        "confirmation_required": False,
                        "preset": "snooze",
                        "semantics": {"schedule": {"resume_at": resume_at}},
                    }
                ],
                interaction_id=f"interaction.snooze.invalid.{resume_at}",
                expires_at="2026-10-07T12:00:00+00:00",
                now="2026-10-07T10:00:00+00:00",
            )

    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="requires resume_at and an expiring interaction",
    ):
        conversation_interactions.create_interaction(
            conversation_id="conv.snooze.no-deadline",
            owner="skill:test",
            prompt="Remind me later",
            actions=[
                {
                    "action_id": "later",
                    "label": "Later",
                    "command": "interaction.snooze",
                    "value": "later",
                    "risk": "read",
                    "confirmation_required": False,
                    "preset": "snooze",
                    "semantics": {"schedule": {"resume_at": "2026-10-07T11:00:00+00:00"}},
                }
            ],
            interaction_id="interaction.snooze.no-deadline",
            now="2026-10-07T10:00:00+00:00",
        )


def test_snooze_completion_asserts_the_exact_resume_time() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.snooze.assertion",
        owner="skill:test",
        prompt="Remind me later",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [{"value": "later", "label": "Later", "description": None}],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "later",
                "label": "Later",
                "command": "interaction.snooze",
                "value": "later",
                "risk": "read",
                "confirmation_required": False,
                "preset": "snooze",
                "semantics": {"schedule": {"resume_at": "2026-10-07T11:00:00+00:00"}},
            }
        ],
        interaction_id="interaction.snooze.assertion",
        expires_at="2026-10-07T12:00:00+00:00",
        now="2026-10-07T10:00:00+00:00",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
        now="2026-10-07T10:01:00+00:00",
    )
    accepted = conversation_interactions.submit_action_token(
        presentation["actions"][0]["token"],
        actor_id="user:local",
        idempotency_key="snooze:assertion",
        now="2026-10-07T10:02:00+00:00",
    )
    dispatch = conversation_store.claim_interaction_dispatch(
        accepted["response"]["response_id"],
        lease_owner="worker:snooze",
    )
    with pytest.raises(ValueError, match="resume_at does not match"):
        conversation_store.complete_interaction_dispatch(
            dispatch["dispatch_id"],
            lease_owner="worker:snooze",
            status="succeeded",
            outcome={
                "effect_assertion": {
                    "schema": "adaos.conversation.action_effect_assertion.v1",
                    "preset": "snooze",
                    "operation": "snooze_interaction",
                    "effect_ref": None,
                    "observed": True,
                    "domain_mutated": False,
                    "resume_at": "2026-10-07T11:30:00+00:00",
                }
            },
        )


def test_semantic_change_atomically_supersedes_pending_dispatch() -> None:
    original = conversation_interactions.create_interaction(
        conversation_id="conv.supersession",
        owner="skill:test",
        prompt="Apply revision A?",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.supersession.a",
    )
    presentation = conversation_interactions.negotiate_presentation(
        original,
        conversation_interactions.standard_capability_profile("web"),
    )
    answered = conversation_interactions.submit_action_token(
        presentation["actions"][0]["token"],
        actor_id="user:local",
        idempotency_key="supersession:answer-a",
    )
    replacement = conversation_interactions.create_interaction(
        conversation_id="conv.supersession",
        owner="skill:test",
        prompt="Apply revision B?",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.supersession.b",
        persist=False,
    )

    result = conversation_interactions.supersede_interaction(
        original["interaction_id"],
        replacement,
        expected_generation=1,
        reason="subject_revision_changed",
        now="2026-10-07T01:00:00+00:00",
    )

    assert result["superseded"] is True
    assert result["interaction"]["status"] == "superseded"
    assert result["replacement"]["status"] == "created"
    assert result["replacement"]["metadata"]["supersedes_interaction_id"] == original["interaction_id"]
    dispatch = conversation_store.get_interaction_dispatch(
        response_id=answered["response"]["response_id"]
    )
    assert dispatch["status"] == "rejected"
    assert dispatch["outcome"]["reason_code"] == "interaction_superseded"
    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="terminal|stale",
    ):
        conversation_interactions.submit_action_token(
            presentation["actions"][1]["token"],
            actor_id="user:local",
            idempotency_key="supersession:stale-button",
        )


def test_identical_semantics_do_not_rotate_interaction() -> None:
    current = conversation_interactions.create_interaction(
        conversation_id="conv.same-semantics",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.same-semantics.a",
    )
    replacement = conversation_interactions.create_interaction(
        conversation_id="conv.same-semantics",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.same-semantics.b",
        persist=False,
    )

    result = conversation_interactions.supersede_interaction(
        current["interaction_id"],
        replacement,
        expected_generation=0,
        reason="producer_refresh",
    )

    assert result["superseded"] is False
    assert conversation_store.get_interaction(replacement["interaction_id"]) is None


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
                    "executor": {
                        "available": True,
                        "adapter_id": "builder.prototype.derive",
                        "executor_id": "builder.prototype.worker",
                        "contract_digest": "sha256:" + "0" * 64,
                        "reason_code": None,
                    },
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


def test_workflow_description_rejects_mutating_action_without_executor_readiness() -> None:
    with pytest.raises(conversation_interactions.ConversationInteractionError, match="executor_unavailable"):
        conversation_interactions.interaction_from_workflow_description(
            {
                "schema": "adaos.workflow.description.v1",
                "workflow_type": "builder.change",
                "definition_version": "1.0.0",
                "instance_id": "change:missing-executor",
                "state": "automation_ready",
                "generation": 2,
                "target": {"kind": "aggregate", "id": "change:missing-executor"},
                "allowed_commands": [
                    {
                        "command": "start_automation",
                        "transition_id": "start_automation",
                        "target_ref": {"kind": "aggregate", "id": "change:missing-executor"},
                        "risk": {"class": "isolated_write", "confirmation": "none"},
                        "authority": {"actors": ["user"], "permissions": ["builder.change"]},
                        "capability_requirements": {
                            "required": [],
                            "optional": ["buttons"],
                            "fallback": "numbered_text",
                        },
                        "explanation": "Start automation",
                    }
                ],
            },
            conversation_id="conv.workflow.executor",
            owner="skill:builder",
            interaction_id="interaction.workflow.executor",
            workflow_ref={"kind": "workflow", "id": "change:missing-executor", "generation": 2},
        )


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
        capabilities={
            "text": True,
            "buttons": True,
            "bound_dialog_response": True,
            "trusted_interface": True,
            "step_up": False,
        },
        limits={"actions": 10},
    )
    compact = conversation_interactions.channel_capability_profile(
        "client:reconnect",
        version=2,
        transport="web",
        client="browser",
        surface="chat",
        capabilities={
            "text": True,
            "buttons": False,
            "bound_dialog_response": True,
            "trusted_interface": True,
            "step_up": False,
        },
        limits={"actions": 0},
    )

    rich_view = conversation_interactions.negotiate_presentation(interaction, rich)
    compact_view = conversation_interactions.negotiate_presentation(interaction, compact)

    assert rich_view["mode"] == "buttons"
    assert compact_view["mode"] == "numbered_text"
    assert rich_view["interaction_generation"] == compact_view["interaction_generation"] == 0
    assert rich_view["action_tokens"] != compact_view["action_tokens"]
    assert compact_view["plan"]["fallback_used"] == "numbered_text"
    assert compact_view["plan"]["reason_code"] == "numbered_fallback"


def test_step_up_choice_requires_step_up_capability() -> None:
    action = {
        "action_id": "delete",
        "label": "Delete permanently",
        "command": "record.delete",
        "value": "delete",
        "risk": "destructive",
        "confirmation_required": True,
    }
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.step-up",
        owner="skill:test",
        prompt="Delete this record?",
        input_spec={
            "kind": "confirmation",
            "required_fields": [],
            "choices": [],
            "sensitive": False,
        },
        actions=[action],
        interaction_id="interaction.step-up",
    )

    ordinary = conversation_interactions.standard_capability_profile("web")
    ordinary_view = conversation_interactions.negotiate_presentation(interaction, ordinary)
    assert ordinary_view["supported"] is False
    assert ordinary_view["actions"][0]["assurance"]["mode"] == "step_up_required"
    assert ordinary_view["actions"][0]["token"] is None

    elevated = conversation_interactions.channel_capability_profile(
        "web:elevated:chat",
        transport="web",
        client="browser",
        surface="chat",
        capabilities={
            "text": True,
            "buttons": True,
            "bound_dialog_response": True,
            "trusted_interface": True,
            "step_up": True,
        },
        limits={"actions": 10},
    )
    elevated_view = conversation_interactions.negotiate_presentation(interaction, elevated)
    assert elevated_view["supported"] is True
    assert elevated_view["actions"][0]["enabled"] is True
    assert elevated_view["actions"][0]["token"]


def test_action_token_is_bound_to_exact_presentation() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.presentation-binding",
        owner="skill:test",
        prompt="Choose",
        input_spec=_choice_interaction()["input_spec"],
        actions=_choice_interaction()["actions"],
        interaction_id="interaction.presentation-binding",
    )
    first = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web", client="one"),
    )
    second = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web", client="two"),
    )
    first_token = first["actions"][0]["token"]
    second_token = second["actions"][0]["token"]
    assert first_token != second_token

    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="presentation binding",
    ):
        conversation_interactions.submit_response(
            interaction["interaction_id"],
            actor_id="user:local",
            expected_generation=0,
            idempotency_key="mismatched-presentation",
            action_token=first_token,
            presentation_id=second["presentation_id"],
        )

    accepted = conversation_interactions.submit_action_token(
        first_token,
        actor_id="user:local",
        idempotency_key="exact-presentation",
    )
    assert accepted["response"]["presentation_id"] == first["presentation_id"]


def test_semantic_messages_render_typed_en_ru_variants_with_provenance() -> None:
    catalog_ref = {
        "package_id": "skill:test",
        "package_version": "1.2.3",
        "catalog_digest": "sha256:" + "a" * 64,
    }
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.localized",
        owner="skill:test",
        prompt="Continue for {device}?",
        prompt_message={
            "key": "test.continue.prompt",
            "version": 2,
            "params": {"device": {"type": "identifier", "value": "Node0"}},
            "fallback": {
                "visual": "Continue for {device}?",
                "spoken": "Continue for {device}?",
            },
            "translations": {
                "ru": {
                    "visual": "Продолжить для {device}?",
                    "spoken": "Продолжить работу для {device}?",
                }
            },
            "catalog_ref": catalog_ref,
            "source_locale": "en",
            "critical": True,
            "fallback_policy": "allow",
        },
        input_spec=_choice_interaction()["input_spec"],
        actions=[
            {
                "action_id": "inspect",
                "label": "Inspect",
                "label_message": {
                    "key": "test.inspect.label",
                    "version": 1,
                    "params": {},
                    "fallback": {"visual": "Inspect", "spoken": "Inspect"},
                    "translations": {
                        "ru": {"visual": "Проверить", "spoken": "Проверить"}
                    },
                    "catalog_ref": catalog_ref,
                    "source_locale": "en",
                    "critical": True,
                    "fallback_policy": "allow",
                },
                "command": "record.inspect",
                "value": "prototype",
                "risk": "read",
                "confirmation_required": False,
            }
        ],
        interaction_id="interaction.localized",
    )
    profile = conversation_interactions.standard_capability_profile("web", locale="ru-RU")

    presentation = conversation_interactions.negotiate_presentation(interaction, profile)

    assert presentation["prompt"] == "Продолжить для Node0?"
    assert presentation["spoken_prompt"] == "Продолжить работу для Node0?"
    assert presentation["actions"][0]["label"] == "Проверить"
    receipt = presentation["metadata"]["message_receipts"]["prompt"]
    assert receipt["resolved_locale"] == "ru"
    assert receipt["catalog_ref"] == catalog_ref
    assert receipt["used_fallback"] is False


def test_critical_semantic_message_can_require_exact_locale() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.locale-required",
        owner="skill:test",
        prompt="Approve?",
        prompt_message={
            "key": "test.approve.prompt",
            "version": 1,
            "params": {},
            "fallback": {"visual": "Approve?", "spoken": "Approve?"},
            "translations": {"en": {"visual": "Approve?", "spoken": "Approve?"}},
            "catalog_ref": {
                "package_id": "skill:test",
                "package_version": "1.0.0",
                "catalog_digest": "sha256:" + "b" * 64,
            },
            "source_locale": "en",
            "critical": True,
            "fallback_policy": "require_locale",
        },
        input_spec=_choice_interaction()["input_spec"],
        actions=[
            {
                "action_id": "inspect",
                "label": "Inspect",
                "command": "record.inspect",
                "value": "prototype",
                "risk": "read",
                "confirmation_required": False,
            }
        ],
        interaction_id="interaction.locale-required",
    )
    profile = conversation_interactions.standard_capability_profile("web", locale="de-DE")

    presentation = conversation_interactions.negotiate_presentation(interaction, profile)

    assert presentation["supported"] is False
    assert presentation["mode"] == "unsupported"
    assert presentation["reason_code"] == "unsupported:localized_material_unavailable"
    assert presentation["action_tokens"] == {}


def test_semantic_message_rejects_parameter_type_mismatch() -> None:
    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="does not match declared type integer",
    ):
        conversation_interactions.create_interaction(
            conversation_id="conv.bad-param",
            owner="skill:test",
            prompt="Count: {count}",
            prompt_message={
                "key": "test.count",
                "params": {"count": {"type": "integer", "value": "many"}},
                "fallback": {"visual": "Count: {count}", "spoken": "Count: {count}"},
                "translations": {},
                "catalog_ref": None,
                "source_locale": "en",
                "critical": True,
                "fallback_policy": "allow",
            },
            interaction_id="interaction.bad-param",
        )


def test_semantic_message_parameter_is_plain_data_not_a_template() -> None:
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.safe-param",
        owner="skill:test",
        prompt="Subject: {subject}",
        prompt_message={
            "key": "test.subject",
            "params": {
                "subject": {"type": "text", "value": "<b>{not_a_parameter}</b>"}
            },
            "fallback": {
                "visual": "Subject: {subject}",
                "spoken": "Subject: {subject}",
            },
            "translations": {},
            "catalog_ref": None,
            "source_locale": "en",
            "critical": True,
            "fallback_policy": "allow",
        },
        interaction_id="interaction.safe-param",
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("web"),
    )

    assert presentation["prompt"] == "Subject: <b>{not_a_parameter}</b>"
    assert presentation["spoken_prompt"] == "Subject: <b>{not_a_parameter}</b>"


def test_interaction_query_is_acl_filtered_bounded_and_cursor_bound() -> None:
    for index in range(3):
        conversation_interactions.create_interaction(
            conversation_id="conv.query",
            owner="skill:publisher",
            prompt=f"Question {index}",
            actions=[
                {
                    "action_id": "inspect",
                    "label": "Inspect",
                    "command": "record.inspect",
                    "value": index,
                    "risk": "read",
                    "confirmation_required": False,
                    "principal_scope": ["user"],
                }
            ],
            interaction_id=f"interaction.query.{index}",
            now=f"2026-10-07T00:00:0{index}+00:00",
        )
    conversation_interactions.create_interaction(
        conversation_id="conv.query",
        owner="skill:private",
        prompt="Transport only",
        actions=[
            {
                "action_id": "inspect",
                "label": "Inspect",
                "command": "record.inspect",
                "value": "private",
                "risk": "read",
                "confirmation_required": False,
                "principal_scope": ["transport"],
            }
        ],
        interaction_id="interaction.query.private",
        now="2026-10-07T00:00:04+00:00",
    )
    principal = {"kind": "user", "id": "local", "actor_id": "user:local"}

    first = conversation_interactions.query_interactions(
        principal=principal,
        conversation_id="conv.query",
        limit=1,
        field_mask="summary",
    )
    assert len(first["items"]) == 1
    assert first["has_more"] is True
    assert "actions" not in first["items"][0]
    assert first["items"][0]["interaction_id"] != "interaction.query.private"

    second = conversation_interactions.query_interactions(
        principal=principal,
        conversation_id="conv.query",
        limit=1,
        field_mask="summary",
        cursor=first["next_cursor"],
    )
    assert second["items"][0]["interaction_id"] != first["items"][0]["interaction_id"]

    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="does not match",
    ):
        conversation_interactions.query_interactions(
            principal=principal,
            conversation_id="conv.query",
            limit=1,
            field_mask="audit",
            cursor=first["next_cursor"],
        )


def test_interaction_query_cursor_rejects_changed_snapshot() -> None:
    principal = {"kind": "user", "id": "local", "actor_id": "user:local"}
    for suffix in ("one", "two"):
        conversation_interactions.create_interaction(
            conversation_id="conv.query-change",
            owner="skill:publisher",
            prompt=f"Question {suffix}",
            actions=[
                {
                    "action_id": "inspect",
                    "label": "Inspect",
                    "command": "record.inspect",
                    "value": suffix,
                    "risk": "read",
                    "confirmation_required": False,
                    "principal_scope": ["user"],
                }
            ],
            interaction_id=f"interaction.query-change.{suffix}",
        )
    first = conversation_interactions.query_interactions(
        principal=principal,
        conversation_id="conv.query-change",
        limit=1,
    )
    conversation_interactions.create_interaction(
        conversation_id="conv.query-change",
        owner="skill:publisher",
        prompt="Question three",
        actions=[
            {
                "action_id": "inspect",
                "label": "Inspect",
                "command": "record.inspect",
                "value": "three",
                "risk": "read",
                "confirmation_required": False,
                "principal_scope": ["user"],
            }
        ],
        interaction_id="interaction.query-change.three",
    )

    with pytest.raises(
        conversation_interactions.ConversationInteractionError,
        match="does not match",
    ):
        conversation_interactions.query_interactions(
            principal=principal,
            conversation_id="conv.query-change",
            limit=1,
            cursor=first["next_cursor"],
        )
