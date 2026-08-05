from __future__ import annotations

import pytest

from adaos.sdk.builder import releases
from adaos.services.builder.release_evidence import BuilderReleaseEvidenceError


def _release() -> dict[str, object]:
    return {
        "schema": "adaos.builder.applied_release.v1",
        "project_id": "recipes",
        "candidate_id": "candidate.recipes.020",
        "version": "0.2.0",
        "release_digest": "sha256:" + "a" * 64,
        "package_digest": "sha256:" + "b" * 64,
        "draft_ref": {"draft_id": "draft.recipes", "revision": "UI-008"},
        "validation_evidence": [{"type": "test", "status": "passed"}],
        "approval": {
            "approval_id": "pa.builder.publish.1",
            "actor_id": "user:owner",
            "policy_evidence": [{"policy": "builder.publish", "decision": "allow"}],
        },
        "activation": {
            "operation_id": "activate.recipes.020",
            "runtime_slot": "B",
            "health_receipt": {"status": "passed"},
        },
        "rollback": {
            "mode": "workspace_lock_restore",
            "operation_ref": "activate.recipes.020:rollback",
        },
        "setup_plan_digest": None,
        "recorded_at": "2026-08-05T00:00:00+00:00",
    }


@pytest.mark.parametrize(
    ("surface_kind", "verification"),
    [
        ("skill", "skill_health"),
        ("scenario", "scenario_materialization"),
        ("nlu_overlay", "nlu_probe"),
        ("entity_alias", "entity_resolution_probe"),
    ],
)
def test_rollback_plan_has_one_confirmed_idempotent_mutation(
    surface_kind: str,
    verification: str,
) -> None:
    plan = releases.rollback_plan(_release(), surface_kind=surface_kind)

    assert plan["schema"] == "adaos.builder.rollback_plan.v1"
    assert plan["surface"]["verification"] == verification
    assert [step["effect"] for step in plan["steps"]] == ["read_only", "runtime_mutation", "read_only"]
    assert plan["steps"][1]["pending_action_required"] is True
    assert plan["execution"]["idempotency_key"] == "builder.rollback:activate.recipes.020:rollback"
    assert plan["execution"]["replay_policy"] == "return_recorded_outcome"
    assert {action["id"] for action in plan["confirmation"]["actions"]} == {
        "inspect",
        "restore",
        "cancel",
    }


def test_rollback_plan_rejects_unknown_surface() -> None:
    with pytest.raises(BuilderReleaseEvidenceError, match="rollback surface"):
        releases.rollback_plan(_release(), surface_kind="database")

