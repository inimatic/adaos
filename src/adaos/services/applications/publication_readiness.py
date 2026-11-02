"""Read-only publication blockers for an immutable, locally reviewed Candidate."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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
