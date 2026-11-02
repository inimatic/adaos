"""Read-only publication blockers for an immutable, locally reviewed Candidate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from adaos.services.artifact_pipeline.candidates import CandidateStore


def candidate_publication_blocker(candidate: Any, state_dir: Path) -> dict[str, Any] | None:
    # Project releases intentionally have a content-addressed aggregate SourceRef.
    # Only the component verification ref has to name a published checkpoint.
    source = getattr(candidate, "verification_source_ref", None) or getattr(candidate, "source_ref", None)
    if source is None or source.forge != "content-addressed-dev":
        return None

    # Recovery continues from the admitted, exact promotion journal. It must
    # not require a new Candidate after data adoption/publication has started.
    pipeline = Path(state_dir) / "artifact_pipeline"
    filename = CandidateStore(pipeline / "candidates").path(candidate.candidate_id).name
    path = pipeline / "promotions" / filename
    try:
        promotion = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        promotion = {}
    promotion = promotion if isinstance(promotion, Mapping) else {}
    receipts = promotion.get("receipts")
    admitted = receipts.get("admitted") if isinstance(receipts, Mapping) else None
    admitted = admitted if isinstance(admitted, Mapping) else {}
    if (promotion.get("schema") == "adaos.artifact.promotion_operation.v1"
            and promotion.get("candidate_id") == candidate.candidate_id
            and promotion.get("release_digest") == candidate.release_digest
            and admitted.get("verification_source_ref") == source.to_dict()
            and admitted.get("source_tree") == candidate.source_tree):
        return None

    return {
        "ok": False,
        "status": "blocked",
        "error": "candidate_source_checkpoint_required",
        "message": (
            "This Beta has only a local source checkpoint. Prepare a new Candidate "
            "from a published component checkpoint, review it, then Finalize. "
            "The current Beta and its data are retained."
        ),
        "candidate_id": candidate.candidate_id,
        "release_digest": candidate.release_digest,
        "version": candidate.version,
        "recovery": {
            "action": "create_candidate_from_published_checkpoint",
            "requires_user_review": True,
            "preserves_current_beta": True,
        },
    }
