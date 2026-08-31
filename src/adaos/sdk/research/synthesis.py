"""Validation helpers for conceptual Research Fabric synthesis artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, ValidationError


class ResearchSynthesisError(ValueError):
    """Raised when a Research Fabric conceptual artifact violates its contract."""


_DIGESTIBLE_COMPONENTS = (
    "literature_scope",
    "concept_model",
    "claim_set",
    "argument_map",
    "related_work_map",
    "source_coverage",
    "novelty_ledger",
)
_SOURCE_BACKED_CLAIM_TYPES = {
    "externally_sourced_fact",
    "sourced_interpretation",
    "implementation_fact",
}
_VERIFIED_READING_STATUSES = {"abstract_read", "fragment_read", "full_text_read"}
_EXTERNAL_SUPPORT_KINDS = {"source_fragment", "source_record", "external_prior"}


def digest_payload(value: Any) -> str:
    """Return a stable sha256 digest for a JSON-compatible value."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def stamp_digest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with ``digest`` bound to the other fields."""

    payload = dict(value)
    payload.pop("digest", None)
    payload["digest"] = digest_payload(payload)
    return payload


def validate_synthesis_revision(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a candidate conceptual ResearchSynthesisRevision."""

    synthesis = _as_mapping(value, "synthesis")
    _validate_schema(synthesis, "research.synthesis.v1.schema.json")
    _require_matching_digest(synthesis, "synthesis")
    _require_source_snapshot_digest(_as_mapping(synthesis["source_snapshot"], "source_snapshot"))
    for key in _DIGESTIBLE_COMPONENTS:
        _require_matching_digest(_as_mapping(synthesis[key], key), key)
    if "threat_model" in synthesis:
        _require_matching_digest(
            _as_mapping(synthesis["threat_model"], "threat_model"),
            "threat_model",
        )
    _validate_versioned_requirements(synthesis)
    _validate_evidence_links(synthesis)
    return dict(synthesis)


def accept_synthesis_revision(
    synthesis: Mapping[str, Any],
    *,
    accepted_id: str,
    accepted_by: str,
    accepted_at: str,
    decision_id: str,
    rationale: str = "",
) -> dict[str, Any]:
    """Build and validate an AcceptedResearchSynthesis wrapper for Phase A1."""

    checked = validate_synthesis_revision(synthesis)
    if checked["status"] != "candidate":
        raise ResearchSynthesisError("only candidate synthesis revisions can be accepted")

    accepted = stamp_digest(
        {
            "schema": "adaos.research.accepted_synthesis.v1",
            "schema_version": "1.0.0",
            "accepted_id": accepted_id,
            "synthesis_id": checked["synthesis_id"],
            "synthesis_revision": checked["revision"],
            "synthesis_digest": checked["digest"],
            "accepted_components": {
                "source_snapshot_digest": checked["source_snapshot"]["snapshot_digest"],
                "literature_scope_digest": checked["literature_scope"]["digest"],
                "concept_model_digest": checked["concept_model"]["digest"],
                "claim_set_digest": checked["claim_set"]["digest"],
                "argument_map_digest": checked["argument_map"]["digest"],
                "related_work_map_digest": checked["related_work_map"]["digest"],
                "source_coverage_digest": checked["source_coverage"]["digest"],
                "novelty_ledger_digest": checked["novelty_ledger"]["digest"],
                **(
                    {"threat_model_digest": checked["threat_model"]["digest"]}
                    if "threat_model" in checked
                    else {}
                ),
                "research_agenda_digest": digest_payload(
                    {"research_agenda": checked["research_agenda"]}
                ),
            },
            "decision": {
                "decision_id": decision_id,
                "decision": "accepted_research_synthesis",
                "target_synthesis_digest": checked["digest"],
                "accepted_by": accepted_by,
                "accepted_at": accepted_at,
                "scope": "phase_a_independent_comparison",
                "rationale": rationale,
                "phase_b_authorized": False,
                "research_release_created": False,
            },
            "created_at": accepted_at,
        }
    )
    return validate_accepted_synthesis(accepted)


def validate_accepted_synthesis(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an AcceptedResearchSynthesis wrapper."""

    accepted = _as_mapping(value, "accepted_synthesis")
    _validate_schema(accepted, "research.accepted_synthesis.v1.schema.json")
    _require_matching_digest(accepted, "accepted_synthesis")
    decision = _as_mapping(accepted["decision"], "decision")
    if decision["target_synthesis_digest"] != accepted["synthesis_digest"]:
        raise ResearchSynthesisError("accepted synthesis decision target must match synthesis_digest")
    if decision["phase_b_authorized"] is not False:
        raise ResearchSynthesisError("Phase A1 accepted synthesis cannot authorize Phase B")
    if decision["research_release_created"] is not False:
        raise ResearchSynthesisError("Phase A1 accepted synthesis cannot create ResearchRelease")
    return dict(accepted)


def build_draft_candidate(
    accepted: Mapping[str, Any],
    *,
    draft_id: str,
    title: str,
    content_ref: Mapping[str, Any],
    section_trace: list[Mapping[str, Any]],
    created_at: str,
    created_by: str = "llm",
    language: str = "en",
    status: str = "candidate",
    version: int = 0,
) -> dict[str, Any]:
    """Build and validate a narrative DraftCandidate from accepted synthesis."""

    checked = validate_accepted_synthesis(accepted)
    draft = stamp_digest(
        {
            "schema": "adaos.research.draft_candidate.v1",
            "schema_version": "1.0.0",
            "draft_id": draft_id,
            "version": version,
            "title": title,
            "language": language,
            "genre": "conceptual_framework_with_design_science_agenda",
            "derived_from_accepted_synthesis_digest": checked["digest"],
            "derived_from_synthesis_digest": checked["synthesis_digest"],
            "content_ref": dict(content_ref),
            "section_trace": [dict(item) for item in section_trace],
            "status": status,
            "created_by": created_by,
            "created_at": created_at,
        }
    )
    return validate_draft_candidate(draft)


def validate_draft_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a narrative DraftCandidate projection."""

    draft = _as_mapping(value, "draft_candidate")
    _validate_schema(draft, "research.draft_candidate.v1.schema.json")
    _require_matching_digest(draft, "draft_candidate")
    return dict(draft)


def gate_a1_freeze(
    accepted: Mapping[str, Any],
    draft: Mapping[str, Any],
    *,
    gate_id: str,
    accepted_by: str,
    accepted_at: str,
    provenance_package_digest: str,
    visibility_receipt_digest: str,
    isolation_receipt_digest: str,
) -> dict[str, Any]:
    """Build and validate the Phase A1 freeze receipt."""

    accepted_checked = validate_accepted_synthesis(accepted)
    draft_checked = validate_draft_candidate(draft)
    accepted_components = _as_mapping(accepted_checked["accepted_components"], "accepted_components")
    if draft_checked["derived_from_accepted_synthesis_digest"] != accepted_checked["digest"]:
        raise ResearchSynthesisError("draft must derive from the accepted synthesis digest")
    if draft_checked["derived_from_synthesis_digest"] != accepted_checked["synthesis_digest"]:
        raise ResearchSynthesisError("draft must derive from the accepted synthesis source digest")
    freeze = stamp_digest(
        {
            "schema": "adaos.research.gate_a1_freeze.v1",
            "schema_version": "1.0.0",
            "gate_id": gate_id,
            "source_snapshot_digest": accepted_components["source_snapshot_digest"],
            "literature_scope_digest": accepted_components["literature_scope_digest"],
            "research_synthesis_digest": accepted_checked["synthesis_digest"],
            "accepted_synthesis_digest": accepted_checked["digest"],
            "claim_set_digest": accepted_components["claim_set_digest"],
            "draft_candidate_digest": draft_checked["digest"],
            "provenance_package_digest": provenance_package_digest,
            "visibility_receipt_digest": visibility_receipt_digest,
            "isolation_receipt_digest": isolation_receipt_digest,
            "decision": "accepted_for_comparison",
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
            "phase_b_authorized": False,
            "research_release_created": False,
        }
    )
    return validate_gate_a1_freeze(freeze, accepted=accepted_checked, draft=draft_checked)


def validate_gate_a1_freeze(
    value: Mapping[str, Any],
    *,
    accepted: Mapping[str, Any] | None = None,
    draft: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a Gate A1 freeze receipt and optional referenced artifacts."""

    freeze = _as_mapping(value, "gate_a1_freeze")
    _validate_schema(freeze, "research.gate_a1_freeze.v1.schema.json")
    _require_matching_digest(freeze, "gate_a1_freeze")
    if freeze["phase_b_authorized"] is not False:
        raise ResearchSynthesisError("Gate A1 cannot authorize Phase B")
    if freeze["research_release_created"] is not False:
        raise ResearchSynthesisError("Gate A1 cannot create ResearchRelease")
    if accepted is not None:
        checked = validate_accepted_synthesis(accepted)
        if freeze["accepted_synthesis_digest"] != checked["digest"]:
            raise ResearchSynthesisError("Gate A1 accepted_synthesis_digest mismatch")
        if freeze["research_synthesis_digest"] != checked["synthesis_digest"]:
            raise ResearchSynthesisError("Gate A1 research_synthesis_digest mismatch")
        accepted_components = _as_mapping(checked["accepted_components"], "accepted_components")
        if freeze["source_snapshot_digest"] != accepted_components["source_snapshot_digest"]:
            raise ResearchSynthesisError("Gate A1 source_snapshot_digest mismatch")
        if freeze["literature_scope_digest"] != accepted_components["literature_scope_digest"]:
            raise ResearchSynthesisError("Gate A1 literature_scope_digest mismatch")
        if freeze["claim_set_digest"] != accepted_components["claim_set_digest"]:
            raise ResearchSynthesisError("Gate A1 claim_set_digest mismatch")
    if draft is not None:
        checked_draft = validate_draft_candidate(draft)
        if freeze["draft_candidate_digest"] != checked_draft["digest"]:
            raise ResearchSynthesisError("Gate A1 draft_candidate_digest mismatch")
    return dict(freeze)


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchSynthesisError(f"{label} must be an object")
    return value


def _validate_versioned_requirements(synthesis: Mapping[str, Any]) -> None:
    schema_version = synthesis.get("schema_version")
    if schema_version not in {"1.1.0", "1.2.0"}:
        return
    phase_boundary = _as_mapping(synthesis.get("phase_boundary"), "phase_boundary")
    if phase_boundary.get("resource_feedback_mode") != "non_operational_research_hypothesis":
        raise ResearchSynthesisError(
            "ResearchSynthesisRevision 1.1 requires non-operational resource feedback"
        )
    if "threat_model" not in synthesis:
        raise ResearchSynthesisError("ResearchSynthesisRevision 1.1 requires threat_model")
    nearest_neighbors = _as_mapping(
        synthesis.get("related_work_map"), "related_work_map"
    ).get("nearest_neighbors") or []
    if not nearest_neighbors:
        raise ResearchSynthesisError(
            "ResearchSynthesisRevision 1.1 requires nearest-neighbor deltas"
        )
    required_delta = {
        "source_ref",
        "inherited_constructs",
        "differentiator",
        "excluded_overlap",
        "uncertainty",
    }
    admitted_literature_refs = {
        str(item.get("ref") or "")
        for item in _as_mapping(synthesis.get("source_snapshot"), "source_snapshot").get(
            "input_refs"
        )
        or []
        if item.get("kind") == "external_literature"
        and item.get("authority") == "admitted_literature"
    }
    for index, value in enumerate(nearest_neighbors):
        neighbor = _as_mapping(value, f"related_work_map.nearest_neighbors.{index}")
        missing = [key for key in required_delta if neighbor.get(key) in (None, "", [])]
        if missing:
            raise ResearchSynthesisError(
                "nearest-neighbor delta is missing " + ", ".join(sorted(missing))
            )
        if neighbor.get("source_ref") not in admitted_literature_refs:
            raise ResearchSynthesisError(
                f"nearest-neighbor delta references unadmitted source: {neighbor.get('source_ref')}"
            )
        if not isinstance(neighbor.get("inherited_constructs"), list):
            raise ResearchSynthesisError(
                "nearest-neighbor inherited_constructs must be an array"
            )
    claims = _as_mapping(synthesis.get("claim_set"), "claim_set").get("claims") or []
    for claim in claims:
        if claim.get("type") != "proposition_hypothesis":
            continue
        if not isinstance(claim.get("operationalization"), Mapping):
            raise ResearchSynthesisError(
                f"{claim.get('claim_id', '<unknown>')}: proposition requires operationalization"
            )

    if schema_version != "1.2.0":
        return
    literature_scope = _as_mapping(synthesis.get("literature_scope"), "literature_scope")
    ceiling = literature_scope.get("novelty_claim_ceiling")
    if ceiling not in {"known_combination_or_unresolved", "provisional_apparent_novelty"}:
        raise ResearchSynthesisError(
            "ResearchSynthesisRevision 1.2 requires literature_scope.novelty_claim_ceiling"
        )
    required_neighbors = literature_scope.get("required_nearest_neighbor_count")
    if not isinstance(required_neighbors, int):
        raise ResearchSynthesisError(
            "ResearchSynthesisRevision 1.2 requires required_nearest_neighbor_count"
        )
    if len(nearest_neighbors) < required_neighbors:
        raise ResearchSynthesisError(
            f"related_work_map requires at least {required_neighbors} nearest-neighbor deltas"
        )
    if ceiling == "known_combination_or_unresolved":
        apparent = {
            "apparently_new_boundary",
            "apparently_new_integration",
        }
        for entry in _as_mapping(
            synthesis.get("novelty_ledger"), "novelty_ledger"
        ).get("entries") or []:
            if entry.get("status") in apparent:
                raise ResearchSynthesisError(
                    "bounded novelty ceiling forbids apparently-new status; use unresolved"
                )
    for claim in claims:
        if claim.get("type") == "sourced_interpretation" and claim.get(
            "epistemic_status"
        ) not in {"source_supported", "contested"}:
            raise ResearchSynthesisError(
                f"{claim.get('claim_id', '<unknown>')}: sourced_interpretation needs a sourced status"
            )


def _schema_path(file_name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / file_name


def _load_schema(file_name: str) -> dict[str, Any]:
    with _schema_path(file_name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_schema(value: Mapping[str, Any], file_name: str) -> None:
    try:
        Draft202012Validator(_load_schema(file_name)).validate(value)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        prefix = f"{path}: " if path else ""
        raise ResearchSynthesisError(prefix + exc.message) from exc


def _digestable_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(value))
    payload.pop("digest", None)
    return payload


def _require_matching_digest(value: Mapping[str, Any], label: str) -> None:
    expected = value.get("digest")
    actual = digest_payload(_digestable_copy(value))
    if expected != actual:
        raise ResearchSynthesisError(f"{label} digest mismatch: expected {expected}, got {actual}")


def _require_source_snapshot_digest(source_snapshot: Mapping[str, Any]) -> None:
    expected = source_snapshot.get("snapshot_digest")
    payload = dict(source_snapshot)
    payload.pop("snapshot_digest", None)
    actual = digest_payload(payload)
    if expected != actual:
        raise ResearchSynthesisError(
            f"source_snapshot digest mismatch: expected {expected}, got {actual}"
        )


def _validate_evidence_links(synthesis: Mapping[str, Any]) -> None:
    snapshot = _as_mapping(synthesis["source_snapshot"], "source_snapshot")
    snapshot_refs: dict[str, Mapping[str, Any]] = {}
    fragment_owners: dict[str, Mapping[str, Any]] = {}
    for source in snapshot.get("input_refs") or []:
        checked = _as_mapping(source, "source_snapshot.input_refs[]")
        ref = str(checked.get("ref") or "")
        if ref in snapshot_refs:
            raise ResearchSynthesisError(f"duplicate source snapshot ref: {ref}")
        snapshot_refs[ref] = checked
        for fragment in checked.get("fragments") or []:
            if fragment in fragment_owners:
                raise ResearchSynthesisError(f"duplicate source fragment ref: {fragment}")
            fragment_owners[str(fragment)] = checked

    related_work = _as_mapping(synthesis["related_work_map"], "related_work_map")
    literature_by_ref: dict[str, Mapping[str, Any]] = {}
    literature_by_id: dict[str, Mapping[str, Any]] = {}
    for cluster in related_work.get("clusters") or []:
        checked_cluster = _as_mapping(cluster, "related_work_map.clusters[]")
        for source in checked_cluster.get("sources") or []:
            checked_source = _as_mapping(source, "related_work_map.clusters[].sources[]")
            source_id = str(checked_source.get("source_id") or "")
            source_ref = str(checked_source.get("source_ref") or "")
            previous = literature_by_id.get(source_id) or literature_by_ref.get(source_ref)
            if previous is not None and dict(previous) != dict(checked_source):
                raise ResearchSynthesisError(
                    f"literature source {source_id or source_ref} is inconsistent across clusters"
                )
            literature_by_id[source_id] = checked_source
            literature_by_ref[source_ref] = checked_source

            snapshot_source = snapshot_refs.get(source_ref)
            if snapshot_source is None:
                raise ResearchSynthesisError(
                    f"literature source {source_id} is not admitted by source_snapshot: {source_ref}"
                )
            if snapshot_source.get("kind") != "external_literature" or snapshot_source.get(
                "authority"
            ) != "admitted_literature":
                raise ResearchSynthesisError(
                    f"literature source {source_id} must be admitted external literature"
                )
            if checked_source.get("digest") != snapshot_source.get("digest"):
                raise ResearchSynthesisError(f"literature source {source_id} digest mismatch")

    literature_scope = _as_mapping(synthesis["literature_scope"], "literature_scope")
    source_count = len(literature_by_ref)
    verified_count = sum(
        1
        for source in literature_by_ref.values()
        if source.get("actual_reading_status") in _VERIFIED_READING_STATUSES
    )
    if literature_scope.get("source_count") != source_count:
        raise ResearchSynthesisError(
            f"literature_scope.source_count must equal admitted unique sources ({source_count})"
        )
    if literature_scope.get("verified_source_count") != verified_count:
        raise ResearchSynthesisError(
            "literature_scope.verified_source_count must equal sources read beyond metadata "
            f"({verified_count})"
        )

    claim_set = _as_mapping(synthesis["claim_set"], "claim_set")
    claims = claim_set.get("claims") or []
    claim_ids = {str(claim.get("claim_id") or "") for claim in claims}
    if len(claim_ids) != len(claims):
        raise ResearchSynthesisError("claim ids must be unique")
    _validate_claim_semantics(
        claim_set,
        claim_ids=claim_ids,
        snapshot_refs=snapshot_refs,
        fragment_owners=fragment_owners,
        literature_by_ref=literature_by_ref,
    )


def _validate_claim_semantics(
    claim_set: Mapping[str, Any],
    *,
    claim_ids: set[str],
    snapshot_refs: Mapping[str, Mapping[str, Any]],
    fragment_owners: Mapping[str, Mapping[str, Any]],
    literature_by_ref: Mapping[str, Mapping[str, Any]],
) -> None:
    claims = claim_set.get("claims") or []
    for claim in claims:
        claim_id = claim.get("claim_id", "<unknown>")
        support = list(claim.get("support") or [])
        claim_type = claim.get("type")
        status = claim.get("epistemic_status")
        if claim_type in _SOURCE_BACKED_CLAIM_TYPES and not support:
            raise ResearchSynthesisError(f"{claim_id}: source-backed claims require support")
        if status == "source_supported" and not support:
            raise ResearchSynthesisError(f"{claim_id}: source_supported claims require support")
        if claim_type == "proposition_hypothesis" and status == "source_supported":
            raise ResearchSynthesisError(
                f"{claim_id}: proposition_hypothesis cannot be source_supported"
            )
        verified_external_support = False
        for support_item in support:
            checked_support = _as_mapping(support_item, f"{claim_id}.support[]")
            support_ref = str(checked_support.get("ref") or "")
            support_kind = checked_support.get("kind")
            if support_kind == "claim":
                referenced_claim = support_ref.removeprefix("claim:")
                if referenced_claim not in claim_ids:
                    raise ResearchSynthesisError(
                        f"{claim_id}: support references unknown claim {support_ref}"
                    )
                if referenced_claim == claim_id:
                    raise ResearchSynthesisError(f"{claim_id}: claim cannot support itself")
                continue
            if support_kind == "source_fragment":
                owner = fragment_owners.get(support_ref)
                if owner is None:
                    raise ResearchSynthesisError(
                        f"{claim_id}: source fragment is not admitted by source_snapshot: {support_ref}"
                    )
                _require_support_digest(checked_support, owner, claim_id)
                literature = literature_by_ref.get(str(owner.get("ref") or ""))
                verified_external_support = verified_external_support or bool(
                    literature
                    and literature.get("actual_reading_status") in _VERIFIED_READING_STATUSES
                )
                continue
            if support_kind in {"source_record", "external_prior"}:
                source = literature_by_ref.get(support_ref)
                if source is None:
                    raise ResearchSynthesisError(
                        f"{claim_id}: external support is not in related_work_map: {support_ref}"
                    )
                _require_support_digest(checked_support, source, claim_id)
                verified_external_support = verified_external_support or (
                    source.get("actual_reading_status") in _VERIFIED_READING_STATUSES
                )
                continue
            if support_kind in {"artifact", "human_decision"}:
                source = snapshot_refs.get(support_ref)
                if source is None:
                    raise ResearchSynthesisError(
                        f"{claim_id}: support is not admitted by source_snapshot: {support_ref}"
                    )
                _require_support_digest(checked_support, source, claim_id)
        if status == "source_supported" and claim_type != "implementation_fact" and not verified_external_support:
            raise ResearchSynthesisError(
                f"{claim_id}: source_supported claim needs literature read beyond metadata"
            )
        if status == "requires_empirical_validation" and not claim.get("needed_evidence"):
            raise ResearchSynthesisError(
                f"{claim_id}: requires_empirical_validation claims need evidence requirements"
            )


def _require_support_digest(
    support: Mapping[str, Any], source: Mapping[str, Any], claim_id: str
) -> None:
    expected = source.get("digest")
    actual = support.get("digest")
    if not actual:
        raise ResearchSynthesisError(f"{claim_id}: source support must bind a digest")
    if actual != expected:
        raise ResearchSynthesisError(f"{claim_id}: source support digest mismatch for {support.get('ref')}")


__all__ = [
    "ResearchSynthesisError",
    "accept_synthesis_revision",
    "build_draft_candidate",
    "digest_payload",
    "gate_a1_freeze",
    "stamp_digest",
    "validate_accepted_synthesis",
    "validate_draft_candidate",
    "validate_gate_a1_freeze",
    "validate_synthesis_revision",
]
