from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


APPLIED_RELEASE_SCHEMA = "adaos.builder.applied_release.v1"
ROLLBACK_PLAN_SCHEMA = "adaos.builder.rollback_plan.v1"
ROLLBACK_SURFACES = {
    "skill": {
        "label": "Skill runtime",
        "label_i18n": {"key": "builder.rollback.surface.skill"},
        "verification": "skill_health",
    },
    "scenario": {
        "label": "Scenario workspace",
        "label_i18n": {"key": "builder.rollback.surface.scenario"},
        "verification": "scenario_materialization",
    },
    "nlu_overlay": {
        "label": "NLU overlay",
        "label_i18n": {"key": "builder.rollback.surface.nlu_overlay"},
        "verification": "nlu_probe",
    },
    "entity_alias": {
        "label": "Entity aliases",
        "label_i18n": {"key": "builder.rollback.surface.entity_alias"},
        "verification": "entity_resolution_probe",
    },
}


class BuilderReleaseEvidenceError(ValueError):
    pass


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / "builder.applied_release.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def applied_release_record(
    *,
    project_id: str,
    candidate_id: str,
    version: str,
    release_digest: str,
    package_digest: str,
    apply_evidence: Mapping[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    evidence = dict(apply_evidence)
    record = {
        "schema": APPLIED_RELEASE_SCHEMA,
        "project_id": str(project_id),
        "candidate_id": str(candidate_id),
        "version": str(version),
        "release_digest": str(release_digest),
        "package_digest": str(package_digest),
        "draft_ref": dict(evidence.get("draft_ref") or {}),
        "validation_evidence": [dict(item) for item in evidence.get("validation_evidence") or []],
        "approval": dict(evidence.get("approval") or {}),
        "activation": dict(evidence.get("activation") or {}),
        "rollback": dict(evidence.get("rollback") or {}),
        "setup_plan_digest": evidence.get("setup_plan_digest"),
        "recorded_at": recorded_at,
    }
    errors = sorted(Draft202012Validator(_schema()).iter_errors(record), key=lambda item: list(item.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:20]
        )
        raise BuilderReleaseEvidenceError(
            "Builder apply requires complete draft, validation, approval identity, runtime slot, health, and rollback evidence: "
            + details
        )
    return record


def rollback_plan(
    applied_release: Mapping[str, Any],
    *,
    surface_kind: str,
) -> dict[str, Any]:
    """Project rollback evidence into one deterministic, channel-neutral UX.

    The plan deliberately separates inspection, the confirmed mutation, and
    verification.  A renderer may present these as a browser modal, Telegram
    buttons, or text choices without changing the operation semantics.
    """

    release = dict(applied_release)
    errors = sorted(Draft202012Validator(_schema()).iter_errors(release), key=lambda item: list(item.path))
    if errors:
        raise BuilderReleaseEvidenceError(f"invalid applied release for rollback: {errors[0].message}")
    surface_key = str(surface_kind or "").strip().lower()
    surface = ROLLBACK_SURFACES.get(surface_key)
    if surface is None:
        raise BuilderReleaseEvidenceError(
            "Builder rollback surface must be one of: " + ", ".join(sorted(ROLLBACK_SURFACES))
        )
    rollback = dict(release["rollback"])
    operation_ref = str(rollback["operation_ref"])
    project_id = str(release["project_id"])
    candidate_id = str(release["candidate_id"])
    return {
        "schema": ROLLBACK_PLAN_SCHEMA,
        "project_id": project_id,
        "candidate_id": candidate_id,
        "version": str(release["version"]),
        "surface_kind": surface_key,
        "surface": dict(surface),
        "target": {
            "mode": str(rollback["mode"]),
            "operation_ref": operation_ref,
            "runtime_slot": dict(release["activation"]).get("runtime_slot"),
        },
        "steps": [
            {
                "id": "inspect",
                "effect": "read_only",
                "command": "builder.rollback.inspect",
                "operation_ref": operation_ref,
            },
            {
                "id": "restore",
                "effect": "runtime_mutation",
                "command": "builder.rollback.execute",
                "operation_ref": operation_ref,
                "pending_action_required": True,
            },
            {
                "id": "verify",
                "effect": "read_only",
                "command": "builder.rollback.verify",
                "verification": surface["verification"],
            },
        ],
        "confirmation": {
            "required": True,
            "title": f"Restore {surface['label']}",
            "title_i18n": {"key": "builder.rollback.title", "params": {"surface": surface_key}},
            "summary": f"Restore {project_id} from release {candidate_id} rollback evidence.",
            "summary_i18n": {
                "key": "builder.rollback.summary",
                "params": {"project_id": project_id, "version": str(release["version"])},
            },
            "actions": [
                {
                    "id": "inspect",
                    "label": "Review target",
                    "label_i18n": {"key": "builder.rollback.action.inspect"},
                    "terminal": False,
                },
                {
                    "id": "restore",
                    "label": "Restore",
                    "label_i18n": {"key": "builder.rollback.action.restore"},
                    "terminal": True,
                },
                {
                    "id": "cancel",
                    "label": "Cancel",
                    "label_i18n": {"key": "builder.rollback.action.cancel"},
                    "terminal": True,
                },
            ],
        },
        "execution": {
            "side_effect_class": "runtime_mutation",
            "idempotency_key": f"builder.rollback:{operation_ref}",
            "conflict_key": f"builder.project:{project_id}:activation",
            "replay_policy": "return_recorded_outcome",
        },
        "outcomes": {
            "success": "rolled_back_and_verified",
            "failure": "rollback_failed_reconciliation_required",
            "unknown": "rollback_outcome_unknown_reconcile_before_retry",
            "cancelled": "rollback_cancelled",
        },
    }


__all__ = [
    "APPLIED_RELEASE_SCHEMA",
    "ROLLBACK_PLAN_SCHEMA",
    "ROLLBACK_SURFACES",
    "BuilderReleaseEvidenceError",
    "applied_release_record",
    "rollback_plan",
]
