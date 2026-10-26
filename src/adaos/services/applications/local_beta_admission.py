"""Explicit installed-local-Beta admission, separate from temporary Trial TTL.

Only Core lifecycle operations write this authority. A receipt binds exact
Candidate, package and runtime-lock identities; it is not a second selection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.candidates import CandidateStore


def admission_path(state: Path, candidate_id: str) -> Path:
    return Path(state) / "applications/local_beta_admissions" / (
        hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json")


def read_local_beta_admission(state: Path, candidate_id: str, release_digest: str,
                              activation: Mapping[str, Any]) -> dict[str, Any] | None:
    path = admission_path(state, candidate_id)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("status") != "active":
        return None
    expected = {"schema": "adaos.application.local_beta_admission.v1",
                "candidate_id": candidate_id, "release_digest": release_digest,
                "runtime_binding_digest": canonical_payload_digest(activation.get("runtime_binding") or {})}
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError("Installed local Beta admission identity changed")
    sealed = {key: item for key, item in value.items() if key != "receipt_digest"}
    if canonical_payload_digest(sealed) != value.get("receipt_digest"):
        raise ValueError("Installed local Beta admission receipt changed")
    candidate = CandidateStore(Path(state) / "artifact_pipeline/candidates").load(candidate_id)
    from .store import ApplicationStore

    application = ApplicationStore(state).get_application(str(value.get("application_id") or ""))
    if (candidate.status != "accepted" or candidate.release_digest != release_digest
            or candidate.digest != value.get("candidate_digest")
            or candidate.package_digest != value.get("package_digest")
            or application.legacy_project_id != candidate.project_id
            or application.publisher_ref != value.get("publisher_ref")):
        raise ValueError("Installed local Beta no longer has exact accepted Candidate authority")
    return value
