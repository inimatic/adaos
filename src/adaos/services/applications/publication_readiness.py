"""Read-only publication blockers for an immutable, locally reviewed Candidate."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PublicationPreconditionFailed(ValueError):
    """A bounded, read-only rejection; never an observation of external effects."""

    MESSAGES = {
        "TrialIdentityMismatch": "The Trial does not match the reviewed Candidate and release.",
        "TrialHealthUnverified": "The exact Trial has no successful health verification.",
        "TrialRuntimeNotSelected": "The reviewed release is not selected in this Webspace.",
        "TrialWorkspaceLockUnavailable": "The Trial has no immutable WorkspaceLock evidence.",
        "CandidateSourceUnavailable": "The reviewed source snapshot is unavailable.",
        "CandidateTestsUnverified": "The exact Candidate requires its complete test plan in the current user environment.",
        "RecoveryRelocationInProgress": "Application recovery relocation is in progress. The confirmed acceptance will resume after storage admission reopens.",
    }

    def __init__(self, code: str):
        self.code = code
        super().__init__(self.MESSAGES[code])


def verify_trial_publication(store, activation: dict, *, application_id: str,
                             webspace_id: str, candidate_id: str, package_digest: str,
                             release_digest: str, allow_completed: bool = False) -> str:
    """Verify identity and placement separately from the Trial's origin Webspace.

    A Trial may be placed in several Webspaces without being rebuilt. Its
    activation target is provenance, while RuntimeSelection is current authority.
    NativeTrialRuntime still verifies the immutable tree and data transition.
    """
    identity = activation.get("candidate_ref") or {}
    binding = activation.get("runtime_binding") or {}
    if (activation.get("status") not in ({"active", "completed"} if allow_completed else {"active"})
            or identity.get("candidate_id") != candidate_id
            or identity.get("release_digest") != release_digest
            or identity.get("package_digest") != package_digest
            or binding.get("authority") != "immutable_candidate"):
        raise PublicationPreconditionFailed("TrialIdentityMismatch")
    if (activation.get("health_evidence") or {}).get("status") != "passed":
        raise PublicationPreconditionFailed("TrialHealthUnverified")
    try:
        selected = store.get_runtime_selection(webspace_id, application_id)
    except FileNotFoundError:
        raise PublicationPreconditionFailed("TrialRuntimeNotSelected") from None
    allowed = {("local_trial", f"trial:{candidate_id}")}
    if allow_completed:
        allowed.add(("stable_installation", "workspace"))
    if (selected.application_id != application_id or selected.webspace_id != webspace_id
            or selected.release_digest != release_digest
            or (selected.source, selected.runtime_root_ref) not in allowed):
        raise PublicationPreconditionFailed("TrialRuntimeNotSelected")
    lock_digest = str(binding.get("workspace_lock_digest") or "")
    if (len(lock_digest) != 71 or not lock_digest.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in lock_digest[7:])):
        raise PublicationPreconditionFailed("TrialWorkspaceLockUnavailable")
    return lock_digest


def verify_candidate_publication_tests(owner, candidate) -> dict[str, Any]:
    """Qualify before publication effects; health evidence is a separate gate."""
    from .test_acceptance import require_candidate_test_acceptance
    from adaos.services.artifact_pipeline.source_validation import (
        SourceValidationError, SourceValidationTimeout, SourceValidationUnavailable,
    )
    try:
        return require_candidate_test_acceptance(owner, candidate)
    except (ValueError, SourceValidationError, SourceValidationTimeout, SourceValidationUnavailable) as exc:
        raise PublicationPreconditionFailed("CandidateTestsUnverified") from exc


def candidate_publication_blocker(candidate: Any, state_dir: Path) -> dict[str, Any] | None:
    # Project releases intentionally have a content-addressed aggregate SourceRef.
    # Finalize can establish the published component binding separately.
    source = getattr(candidate, "verification_source_ref", None) or getattr(candidate, "source_ref", None)
    if source is None or source.forge != "content-addressed-dev":
        return None

    # Finalize publishes this exact retained tree itself. A local checkpoint
    # is preparation work, not a reason to replace an already reviewed Beta.
    tree = str(getattr(candidate, "source_tree", None) or "")
    if len(tree) in {40, 64} and all(char in "0123456789abcdef" for char in tree):
        return None

    return {
        "ok": False,
        "status": "blocked",
        "error": "candidate_source_unavailable",
        "message": (
            "The reviewed Beta source snapshot is unavailable. Restore that exact "
            "snapshot before Finalize. The current Beta and its data are retained."
        ),
        "candidate_id": candidate.candidate_id,
        "release_digest": candidate.release_digest,
        "version": candidate.version,
        "recovery": {
            "action": "restore_reviewed_source_snapshot",
            "requires_user_review": False,
            "preserves_current_beta": True,
        },
    }
