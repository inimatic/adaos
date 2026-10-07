from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from adaos.services import conversation_interactions, conversation_store, intent_mediation


def _choice(
    conversation_id: str,
    interaction_id: str,
    *,
    risk: str = "read",
    confirmation_required: bool = False,
) -> dict[str, object]:
    return conversation_interactions.create_interaction(
        conversation_id=conversation_id,
        owner="skill:test",
        prompt="Choose the development route",
        interaction_id=interaction_id,
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [
                {"value": "prototype", "label": "Prototype first", "description": None},
                {"value": "automation", "label": "Implement directly", "description": None},
            ],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "prototype",
                "label": "Prototype first",
                "command": "builder.route.prototype",
                "value": "prototype",
                "risk": risk,
                "confirmation_required": confirmation_required,
                "expected_generation": 7,
                "target_ref": {"kind": "workflow", "id": "change.1", "generation": 7},
            },
            {
                "action_id": "automation",
                "label": "Implement directly",
                "command": "builder.route.automation",
                "value": "automation",
                "risk": "read",
                "confirmation_required": False,
                "expected_generation": 7,
                "target_ref": {"kind": "workflow", "id": "change.1", "generation": 7},
            },
        ],
    )


def _confirmation(conversation_id: str, interaction_id: str) -> dict[str, object]:
    return conversation_interactions.create_interaction(
        conversation_id=conversation_id,
        owner="skill:test",
        prompt="Continue?",
        interaction_id=interaction_id,
        input_spec={
            "kind": "confirmation",
            "required_fields": [],
            "choices": [],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "continue",
                "label": "Continue",
                "command": "workflow.continue",
                "value": True,
                "risk": "read",
                "confirmation_required": False,
                "expected_generation": 3,
                "target_ref": {"kind": "workflow", "id": "work.1", "generation": 3},
            },
            {
                "action_id": "cancel",
                "label": "Cancel",
                "command": "workflow.cancel",
                "value": False,
                "risk": "read",
                "confirmation_required": False,
                "expected_generation": 3,
                "target_ref": {"kind": "workflow", "id": "work.1", "generation": 3},
            },
        ],
    )


@pytest.mark.parametrize(
    ("locale", "text", "expected"),
    [
        ("en", "first", "prototype"),
        ("en", "2", "automation"),
        ("ru", "первый", "prototype"),
        ("ru", "второй", "automation"),
    ],
)
def test_deterministic_short_choice_is_proposed_and_committed(
    locale: str,
    text: str,
    expected: str,
) -> None:
    interaction = _choice(f"conv.{locale}.{expected}", f"interaction.{locale}.{expected}")

    proposal = intent_mediation.propose_intent(
        interaction["conversation_id"],
        f"message.{locale}.{expected}",
        text,
        locale=locale,
    )
    result = intent_mediation.commit_proposal(
        proposal["proposal_id"],
        actor_id="user:local",
        idempotency_key=f"intent:{locale}:{expected}",
    )

    assert proposal["model"]["name"] == "deterministic-intent-mediator"
    assert result["response"]["source"] == "intent"
    assert result["response"]["values"]["choice"] == expected
    assert result["response"]["consumed_command"]["command"] == f"builder.route.{expected}"
    assert result["proposal"]["disposition"] == "committed"


@pytest.mark.parametrize(
    ("locale", "text", "expected"),
    [
        ("en", "yes please", True),
        ("en", "I confirm", True),
        ("en", "no thanks", False),
        ("en", "do not confirm", False),
        ("ru", "да, подтверждаю", True),
        ("ru", "согласна", True),
        ("ru", "нет, спасибо", False),
        ("ru", "не подтверждаю", False),
    ],
)
def test_localized_confirmation_uses_bounded_explicit_phrases(
    locale: str,
    text: str,
    expected: bool,
) -> None:
    case_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    interaction = _confirmation(
        f"conv.confirm.{locale}.{expected}.{case_id}",
        f"interaction.confirm.{locale}.{expected}.{case_id}",
    )

    proposal = intent_mediation.propose_intent(
        interaction["conversation_id"],
        f"message.confirm.{locale}.{expected}.{case_id}",
        text,
        locale=locale,
    )
    result = intent_mediation.commit_proposal(
        proposal["proposal_id"],
        actor_id="user:local",
        idempotency_key=f"confirm:{locale}:{expected}:{text}",
    )

    assert proposal["disposition"] == "proposed"
    assert result["response"]["values"]["confirmed"] is expected
    assert result["response"]["consumed_command"]["command"] == (
        "workflow.continue" if expected else "workflow.cancel"
    )


@pytest.mark.parametrize(
    "text",
    [
        "yes no",
        "no, actually yes",
        "yes, maybe",
        "да нет",
        "нет, хотя да",
        "да, наверное",
    ],
)
def test_ambiguous_or_same_utterance_correction_never_mutates_confirmation(text: str) -> None:
    case_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    interaction = _confirmation(
        f"conv.ambiguous.{case_id}",
        f"interaction.ambiguous.{case_id}",
    )

    proposal = intent_mediation.propose_intent(
        interaction["conversation_id"],
        f"message.ambiguous.{case_id}",
        text,
        locale="ru" if any("а" <= char <= "я" for char in text.casefold()) else "en",
    )

    assert proposal["disposition"] == "clarification_required"
    assert proposal["clarification"]["reason_code"] == "localized_confirmation_ambiguous"
    assert conversation_store.get_interaction(interaction["interaction_id"])["generation"] == 0
    with pytest.raises(intent_mediation.IntentMediationError, match="not committable"):
        intent_mediation.commit_proposal(
            proposal["proposal_id"],
            actor_id="user:local",
            idempotency_key=f"ambiguous:{text}",
        )


def test_confirmation_correction_is_a_new_versioned_proposal() -> None:
    interaction = _confirmation("conv.confirm.correction", "interaction.confirm.correction")
    ambiguous = intent_mediation.propose_intent(
        interaction["conversation_id"],
        "message.confirm.correction",
        "no, actually yes",
        locale="en",
    )

    corrected = intent_mediation.correct_proposal(
        ambiguous["proposal_id"],
        "yes",
        source_message_id="message.confirm.correction.2",
    )
    result = intent_mediation.commit_proposal(
        corrected["proposal_id"],
        actor_id="user:local",
        idempotency_key="confirm:correction:yes",
    )

    assert conversation_store.get_intent_proposal(ambiguous["proposal_id"])["disposition"] == "corrected"
    assert corrected["supersedes_proposal_id"] == ambiguous["proposal_id"]
    assert result["response"]["values"]["confirmed"] is True


def test_multiple_pending_targets_require_clarification_without_mutation() -> None:
    _choice("conv.ambiguous.intent", "interaction.intent.one")
    _choice("conv.ambiguous.intent", "interaction.intent.two")

    proposal = intent_mediation.propose_intent(
        "conv.ambiguous.intent",
        "message.ambiguous",
        "первый",
        locale="ru",
    )

    assert proposal["disposition"] == "clarification_required"
    assert proposal["clarification"]["reason_code"] == "multiple_pending_targets"
    assert len(proposal["alternatives"]) == 2
    assert conversation_store.get_interaction("interaction.intent.one")["generation"] == 0
    assert conversation_store.get_interaction("interaction.intent.two")["generation"] == 0
    with pytest.raises(intent_mediation.IntentMediationError, match="not committable"):
        intent_mediation.commit_proposal(
            proposal["proposal_id"],
            actor_id="user:local",
            idempotency_key="intent:ambiguous",
        )


def test_multi_act_preserves_issue_question_and_governed_answer() -> None:
    _choice("conv.multi", "interaction.multi")

    proposal = intent_mediation.propose_intent(
        "conv.multi",
        "message.multi",
        "первый; Добавь поиск по рецептам; Почему публикация заблокирована?",
        locale="ru",
    )

    assert [item["kind"] for item in proposal["semantic_acts"]] == [
        "interaction_answer",
        "new_issue",
        "question",
    ]
    result = intent_mediation.commit_proposal(
        proposal["proposal_id"],
        actor_id="user:local",
        idempotency_key="intent:multi",
    )
    assert result["response"]["values"]["choice"] == "prototype"


def test_absent_or_stale_command_cannot_be_committed() -> None:
    interaction = _choice("conv.stale.intent", "interaction.stale.intent")
    proposal = intent_mediation.propose_intent(
        "conv.stale.intent",
        "message.stale.intent",
        "Implement directly",
    )
    tampered = copy.deepcopy(proposal)
    tampered["semantic_acts"][0]["command"] = "builder.publish"
    conversation_store.save_intent_proposal(tampered)

    with pytest.raises(intent_mediation.IntentMediationError, match="no longer allowed"):
        intent_mediation.commit_proposal(
            proposal["proposal_id"],
            actor_id="user:local",
            idempotency_key="intent:tampered",
        )

    current = conversation_store.get_interaction(interaction["interaction_id"])
    advanced = copy.deepcopy(current)
    advanced["generation"] = 1
    conversation_store.save_interaction(advanced, expected_generation=0)
    fresh = intent_mediation.propose_intent(
        "conv.stale.intent",
        "message.stale.intent.2",
        "Prototype first",
    )
    later = copy.deepcopy(conversation_store.get_interaction(interaction["interaction_id"]))
    later["generation"] = 2
    conversation_store.save_interaction(later, expected_generation=1)
    with pytest.raises(intent_mediation.IntentMediationError, match="stale"):
        intent_mediation.commit_proposal(
            fresh["proposal_id"],
            actor_id="user:local",
            idempotency_key="intent:stale",
        )


def test_protected_action_requires_explicit_control() -> None:
    _choice(
        "conv.protected.intent",
        "interaction.protected.intent",
        risk="publication",
        confirmation_required=True,
    )

    proposal = intent_mediation.propose_intent(
        "conv.protected.intent",
        "message.protected.intent",
        "Prototype first",
    )

    assert proposal["disposition"] == "clarification_required"
    assert proposal["clarification"]["reason_code"] == "protected_action_requires_explicit_control"


def test_voice_answer_requires_exact_live_dialog_binding() -> None:
    interaction = _choice("conv.voice.bound", "interaction.voice.bound")
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("voice"),
        now="2026-10-07T10:00:00+00:00",
    )
    missing = intent_mediation.propose_intent(
        interaction["conversation_id"],
        "message.voice.missing",
        "first",
        modality="voice",
        explicit_interaction_id=interaction["interaction_id"],
        now="2026-10-07T10:00:01+00:00",
    )
    binding = {
        "schema": "adaos.conversation.dialog_binding.v1",
        "interaction_id": interaction["interaction_id"],
        "interaction_generation": 0,
        "presentation_id": presentation["presentation_id"],
        "session_ref": {"kind": "voice_session", "id": "voice-session-1"},
        "participant_ref": {"kind": "user", "id": "user:local"},
        "source_message_id": "message.voice.bound",
        "opened_at": "2026-10-07T10:00:00+00:00",
        "expires_at": "2026-10-07T10:00:30+00:00",
    }
    proposal = intent_mediation.propose_intent(
        interaction["conversation_id"],
        "message.voice.bound",
        "first",
        modality="voice",
        explicit_interaction_id=interaction["interaction_id"],
        dialog_binding=binding,
        now="2026-10-07T10:00:01+00:00",
    )
    result = intent_mediation.commit_proposal(
        proposal["proposal_id"],
        actor_id="user:local",
        idempotency_key="voice:bound:first",
        now="2026-10-07T10:00:02+00:00",
    )

    assert missing["disposition"] == "clarification_required"
    assert missing["clarification"]["reason_code"] == "voice_dialog_binding_required"
    assert proposal["disposition"] == "proposed"
    assert result["response"]["assurance_receipt"]["mode"] == "voice_permitted"
    assert result["response"]["assurance_receipt"]["presentation_id"] == presentation[
        "presentation_id"
    ]


def test_voice_dialog_binding_rejects_wrong_participant_and_expiry() -> None:
    interaction = _choice("conv.voice.guard", "interaction.voice.guard")
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("voice"),
        now="2026-10-07T11:00:00+00:00",
    )
    binding = {
        "schema": "adaos.conversation.dialog_binding.v1",
        "interaction_id": interaction["interaction_id"],
        "interaction_generation": 0,
        "presentation_id": presentation["presentation_id"],
        "session_ref": {"kind": "voice_session", "id": "voice-session-2"},
        "participant_ref": {"kind": "user", "id": "user:expected"},
        "source_message_id": "message.voice.guard",
        "opened_at": "2026-10-07T11:00:00+00:00",
        "expires_at": "2026-10-07T11:00:10+00:00",
    }
    proposal = intent_mediation.propose_intent(
        interaction["conversation_id"],
        "message.voice.guard",
        "first",
        modality="voice",
        explicit_interaction_id=interaction["interaction_id"],
        dialog_binding=binding,
        now="2026-10-07T11:00:01+00:00",
    )

    with pytest.raises(
        intent_mediation.IntentMediationError,
        match="voice_dialog_participant_mismatch",
    ):
        intent_mediation.commit_proposal(
            proposal["proposal_id"],
            actor_id="user:other",
            idempotency_key="voice:wrong-participant",
            now="2026-10-07T11:00:02+00:00",
        )
    with pytest.raises(
        intent_mediation.IntentMediationError,
        match="voice_dialog_expired",
    ):
        intent_mediation.commit_proposal(
            proposal["proposal_id"],
            actor_id="user:expected",
            idempotency_key="voice:expired",
            now="2026-10-07T11:00:11+00:00",
        )


def test_utf8_persistence_correction_and_rates() -> None:
    _choice("conv.utf8", "interaction.utf8")
    proposal = intent_mediation.propose_intent(
        "conv.utf8",
        "message.utf8",
        "Добавь раздел избранного — без потери рецептов",
        locale="ru",
        retention_class="audit",
        redaction="required",
    )
    correction = intent_mediation.correct_proposal(
        proposal["proposal_id"],
        "Почему выбран прототип?",
        locale="ru",
    )

    stored = conversation_store.get_intent_proposal(proposal["proposal_id"])
    assert stored["source_text"] == "Добавь раздел избранного — без потери рецептов"
    assert stored["disposition"] == "corrected"
    assert correction["supersedes_proposal_id"] == proposal["proposal_id"]
    assert correction["semantic_acts"][0]["kind"] == "question"
    metrics = intent_mediation.interpretation_metrics("conv.utf8")
    assert metrics["corrections"] == 1
    assert metrics["total"] == 2


def test_offline_ru_en_evaluation_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "intent_mediation_ru_en.v1.json").read_text(
            encoding="utf-8"
        )
    )
    for index, case in enumerate(fixture["cases"]):
        conversation_id = f"conv.eval.{index}"
        _choice(conversation_id, f"interaction.eval.{index}")
        proposal = intent_mediation.propose_intent(
            conversation_id,
            f"message.eval.{index}",
            case["text"],
            locale=case["locale"],
        )
        act = proposal["semantic_acts"][0]
        assert act["kind"] == case["expected_kind"], case
        assert act["arguments"].get("action_id") == case["expected_action_id"], case
