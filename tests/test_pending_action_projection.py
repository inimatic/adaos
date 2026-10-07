from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services import conversation_interactions
from adaos.services.pending_action_projection import (
    PendingActionProjectionError,
    project_pending_action,
)


def _projection_fixture() -> tuple[dict, dict]:
    interaction = conversation_interactions.create_interaction(
        persist=False,
        interaction_id="interaction.pa.fixture",
        conversation_id="conversation.pa.fixture",
        owner="skill:builder",
        prompt="Create the reviewed repair tasks?",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [
                {"value": "preview", "label": "Preview", "description": None},
                {"value": "create", "label": "Create tasks", "description": None},
            ],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "preview",
                "label": "Preview",
                "command": "repair.preview",
                "value": "preview",
                "risk": "read",
                "confirmation_required": False,
                "principal_scope": ["user"],
                "target_ref": {"kind": "eval", "id": "eval.1"},
            },
            {
                "action_id": "create",
                "label": "Create tasks",
                "command": "repair.create",
                "value": "create",
                "risk": "local_write",
                "confirmation_required": True,
                "principal_scope": ["user:owner"],
                "target_ref": {"kind": "eval", "id": "eval.1"},
            },
        ],
        task_ref={"kind": "builder_eval", "id": "eval.1"},
        workflow_ref={"kind": "workflow", "id": "repair.eval.1", "generation": 3},
        metadata={
            "pending_action_kind": "builder.eval_repair.review",
            "evidence_refs": [{"kind": "report", "digest": "sha256:" + "a" * 64}],
        },
        now="2026-10-07T00:00:00+00:00",
    )
    profile = conversation_interactions.standard_capability_profile("web", persist=False)
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        profile,
        persist=False,
        now="2026-10-07T00:00:01+00:00",
    )
    return interaction, presentation


def test_projection_is_a_bounded_typed_view_of_interaction_and_plan() -> None:
    interaction, presentation = _projection_fixture()

    projection = project_pending_action(interaction, presentation)

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "abi"
        / "pending_action.projection.v1.schema.json"
    )
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(projection)
    assert projection["status"] == "awaiting_decision"
    assert projection["interaction_ref"] == {
        "id": "interaction.pa.fixture",
        "generation": 0,
        "presentation_id": presentation["presentation_id"],
    }
    assert projection["choices"][0]["assurance"] == {
        "voice_permitted": True,
        "trusted_interface_required": False,
        "step_up_required": False,
    }
    assert projection["choices"][1]["assurance"] == {
        "voice_permitted": False,
        "trusted_interface_required": True,
        "step_up_required": False,
    }
    assert projection["subject_digest"].startswith("sha256:")
    assert projection["plan_digest"].startswith("sha256:")


def test_projection_rejects_stale_or_unsupported_presentation() -> None:
    interaction, presentation = _projection_fixture()
    stale = copy.deepcopy(presentation)
    stale["interaction_generation"] = 1
    with pytest.raises(PendingActionProjectionError, match="generation is stale"):
        project_pending_action(interaction, stale)

    unsupported = copy.deepcopy(presentation)
    unsupported["supported"] = False
    with pytest.raises(PendingActionProjectionError, match="unsupported presentation"):
        project_pending_action(interaction, unsupported)


def test_projection_keeps_receipts_distinct_and_outcome_controls_status() -> None:
    interaction, presentation = _projection_fixture()

    projection = project_pending_action(
        interaction,
        presentation,
        delivery_receipt={"status": "delivered"},
        decision_receipt={"status": "accepted", "response_id": "response.1"},
        execution_receipt={"status": "running", "attempt_id": "attempt.1"},
        outcome_receipt={"outcome": "outcome_unknown", "attempt_id": "attempt.1"},
    )

    assert projection["status"] == "outcome_unknown"
    assert projection["receipts"] == {
        "delivery": {"status": "delivered"},
        "decision": {"status": "accepted", "response_id": "response.1"},
        "execution": {"status": "running", "attempt_id": "attempt.1"},
        "outcome": {"outcome": "outcome_unknown", "attempt_id": "attempt.1"},
    }
