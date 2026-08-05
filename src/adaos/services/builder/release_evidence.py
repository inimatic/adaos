from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


APPLIED_RELEASE_SCHEMA = "adaos.builder.applied_release.v1"


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


__all__ = ["APPLIED_RELEASE_SCHEMA", "BuilderReleaseEvidenceError", "applied_release_record"]
