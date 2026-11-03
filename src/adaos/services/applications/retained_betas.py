"""Observe installed Beta receipts independently of the selected runtime."""
import json
from pathlib import Path

from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
from .local_beta_admission import read_local_beta_admission


def observe_retained_betas(owner, store, application_id):
    state = Path(owner.paths.state_dir())
    application = store.get_application(application_id)
    candidates = CandidateStore(state / "artifact_pipeline/candidates")
    activations = TrialActivationStore(state / "artifact_pipeline/trial-activations")
    try:
        installed = store.get_installation(application_id)
        from .lifecycle import workspace_release_kind
        stable_digest = (installed.installed_release_digest if installed.status == "active"
                         and workspace_release_kind(store, application_id, installed.installed_release_digest) == "stable" else None)
    except FileNotFoundError:
        stable_digest = None
    rows = []
    for path in (state / "applications/local_beta_admissions").glob("*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # An unattributable file grants no Application authority.
        if not isinstance(receipt, dict):
            continue
        if receipt.get("application_id") != application_id or receipt.get("status") != "active":
            continue
        identity = receipt.get("candidate_id")
        try:
            candidate = candidates.load(identity)
            if (candidate.project_id != application.legacy_project_id
                    or candidate.release_digest == stable_digest):
                continue
            release = store.get_release(application_id, candidate.release_digest)
            activation = activations.load(identity) or {}
            if activation.get("lifecycle_deletion") or activation.get("status") == "detached":
                continue
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            continue
        row = {"candidate_id": identity, "release_digest": candidate.release_digest,
            "version": release.project_release.version, "available_at": receipt.get("created_at"),
            "admission_kind": "installed_local_beta", "expires_at": None,
            "receipt_digest": receipt.get("receipt_digest"), "availability": "unavailable",
            "reason": "InstalledBetaAdmissionUnavailable"}
        try:
            verified = read_local_beta_admission(state, identity, candidate.release_digest, activation)
            if not verified or (activation.get("health_evidence") or {}).get("status") != "passed":
                raise ValueError("Installed Beta has no healthy admission")
            from .trial_runtime import NativeTrialRuntime
            NativeTrialRuntime.resolve(owner, identity, candidate.release_digest)
            row.update(availability="ready", reason="InstalledLocalBeta")
        except (OSError, ValueError, RuntimeError, KeyError):
            pass
        rows.append(row)
    return sorted(rows, key=lambda item: (str(item["available_at"] or ""), item["candidate_id"]), reverse=True)
