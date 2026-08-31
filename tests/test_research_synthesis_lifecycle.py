from __future__ import annotations

import copy

import pytest

from adaos.sdk.research import (
    ResearchSynthesisError,
    accept_synthesis_revision,
    build_draft_candidate,
    digest_payload,
    gate_a1_freeze,
    stamp_digest,
    validate_synthesis_revision,
)


ZERO_DIGEST = "sha256:" + "0" * 64
ONE_DIGEST = "sha256:" + "1" * 64
NOW = "2026-08-29T00:00:00+03:00"


def _source_snapshot() -> dict:
    value = {
        "input_refs": [
            {
                "ref": "file://genesis.md",
                "kind": "genesis_brief",
                "authority": "authoritative_input",
                "digest": ZERO_DIGEST,
                "accessed_at": NOW,
                "fragments": ["fragment:annotation"],
            },
            {
                "ref": "source:S1",
                "kind": "external_literature",
                "authority": "admitted_literature",
                "digest": ONE_DIGEST,
                "accessed_at": NOW,
                "fragments": [],
            }
        ],
        "allowed_paths": ["docs/architecture"],
        "allowed_external_sources": ["bounded scoping review records"],
        "denied_material_classes": ["hidden comparator drafts", "empirical result claims"],
    }
    value["snapshot_digest"] = digest_payload(value)
    return value


def _support_ref(ref: str = "source:S1") -> dict:
    return {
        "ref": ref,
        "kind": "source_record",
        "digest": ONE_DIGEST,
        "note": "bounded scoping source",
    }


def _minimal_synthesis() -> dict:
    concept_model = stamp_digest(
        {
            "entities": ["Human", "Agent", "Artifact"],
            "relations": ["variation", "selection", "retention"],
        }
    )
    claim_set = stamp_digest(
        {
            "claims": [
                {
                    "claim_id": "C1",
                    "statement": "AI-native software can be framed as human-agent-artifact coevolution.",
                    "type": "sourced_interpretation",
                    "epistemic_status": "source_supported",
                    "support": [_support_ref()],
                    "counterarguments": [],
                    "confidence": "medium",
                    "acceptance_state": "accepted_in_synthesis",
                    "needed_evidence": [],
                    "provenance_refs": ["source:S1"],
                },
                {
                    "claim_id": "C2",
                    "statement": "Evolnomics requires future empirical validation before mechanism deployment.",
                    "type": "proposition_hypothesis",
                    "epistemic_status": "requires_empirical_validation",
                    "support": [{"ref": "claim:C1", "kind": "claim"}],
                    "counterarguments": [],
                    "confidence": "low",
                    "acceptance_state": "accepted_in_synthesis",
                    "needed_evidence": ["Compare attribution choices against downstream utility."],
                    "provenance_refs": ["claim:C1"],
                },
            ]
        }
    )
    related_work_map = stamp_digest(
        {
            "review_type": "bounded_reproducible_scoping_review",
            "clusters": [
                {
                    "cluster_id": "RW1",
                    "name": "Human-AI co-creation",
                    "sources": [
                        {
                            "source_id": "S1",
                            "metadata": {"title": "Representative prior work"},
                            "actual_reading_status": "abstract_read",
                            "source_ref": "source:S1",
                            "digest": ONE_DIGEST,
                        }
                    ],
                    "relevance": "Provides neighboring framing.",
                    "limits": "Does not close an economic feedback loop.",
                }
            ],
            "nearest_neighbors": [{"source_id": "S1", "relation": "conceptual neighbor"}],
            "contradictions": [{"source_id": "S1", "status": "none found in fixture"}],
        }
    )
    value = {
        "schema": "adaos.research.synthesis.v1",
        "schema_version": "1.0.0",
        "synthesis_id": "evolnomics.phase_a",
        "revision": 1,
        "direction_ref": "research-direction:evolnomics",
        "task_ref": "research-task:phase-a",
        "profile": "conceptual_framework",
        "genre": "conceptual_framework_with_design_science_agenda",
        "status": "candidate",
        "phase_boundary": {
            "phase": "conceptual_phase_a",
            "implementation_authorized": False,
            "mechanism_selected": False,
            "research_release_created": False,
            "prohibited_commitments": [
                "token_or_currency",
                "allocation_formula",
                "signal_weights",
                "emission_or_burn_rules",
                "transfer_or_settlement",
                "payout_or_royalty",
                "ownership_rights",
                "governance_thresholds",
                "automated_sanctions_or_rewards",
            ],
        },
        "source_snapshot": _source_snapshot(),
        "literature_scope": stamp_digest(
            {
                "review_type": "bounded_reproducible_scoping_review",
                "search_directions": ["human-AI coevolution", "provenance-aware incentives"],
                "inclusion_rules": ["admit sources with direct conceptual relevance"],
                "exclusion_rules": ["exclude unchecked claims from narrative drafts"],
                "stop_rule": "stop after nearest-neighbor coverage stabilizes for Draft 0",
                "source_count": 1,
                "verified_source_count": 1,
                "out_of_scope": ["production token design"],
            }
        ),
        "concept_model": concept_model,
        "claim_set": claim_set,
        "argument_map": stamp_digest({"nodes": ["C1", "C2"], "edges": [["C1", "supports", "C2"]]}),
        "related_work_map": related_work_map,
        "source_coverage": stamp_digest({"covered": ["genesis brief", "S1"], "gaps": []}),
        "novelty_ledger": stamp_digest(
            {
                "entries": [
                    {
                        "entry_id": "N1",
                        "candidate_contribution": "Links accepted contribution provenance to later evolution resources.",
                        "status": "apparently_new_integration",
                        "support": [_support_ref()],
                        "uncertainty": "Fixture uses one source; real run must expand coverage.",
                    }
                ]
            }
        ),
        "boundary_conditions": [{"id": "B1", "statement": "No Phase B without human Gate A2."}],
        "counterarguments": [{"id": "CA1", "statement": "Attribution may be gamed.", "status": "open"}],
        "limitations": [{"id": "L1", "statement": "No empirical validation is claimed in Phase A."}],
        "research_agenda": [
            {
                "item_id": "RA1",
                "question": "Which attribution substrate is sufficient for later mechanism evaluation?",
                "purpose": "design_science_artifact",
                "status": "proposed",
                "needed_evidence": ["Builder handoff after Gate A2."],
            }
        ],
        "attribution": {"llm_role": "conceptual synthesis authoring", "human_role": "acceptance"},
        "provenance": {
            "created_by": "llm",
            "model_or_actor": "test-llm",
            "tool_refs": ["pytest"],
            "traceability_graph_digest": ZERO_DIGEST,
        },
        "created_at": NOW,
    }
    return stamp_digest(value)


def _restamp_after_claim_change(value: dict) -> dict:
    value["claim_set"] = stamp_digest(value["claim_set"])
    return stamp_digest(value)


def test_conceptual_synthesis_lifecycle_reaches_gate_a1_without_research_release() -> None:
    synthesis = _minimal_synthesis()
    assert validate_synthesis_revision(synthesis)["digest"] == synthesis["digest"]

    accepted = accept_synthesis_revision(
        synthesis,
        accepted_id="accepted.evolnomics.phase_a.1",
        accepted_by="user:zver",
        accepted_at=NOW,
        decision_id="decision.evolnomics.phase_a.1",
    )
    draft = build_draft_candidate(
        accepted,
        draft_id="draft.evolnomics.0",
        title="Human-Agent-Artifact Coevolution and Evolnomics",
        content_ref={
            "ref": "artifact://drafts/evolnomics-draft-0.md",
            "digest": digest_payload({"body": "Draft 0 fixture"}),
            "media_type": "text/markdown",
        },
        section_trace=[
            {
                "section_id": "problem",
                "source": "accepted_synthesis",
                "claim_ids": ["C1", "C2"],
                "source_refs": ["source:S1"],
            }
        ],
        created_at=NOW,
    )
    freeze = gate_a1_freeze(
        accepted,
        draft,
        gate_id="gate.evolnomics.a1",
        accepted_by="user:zver",
        accepted_at=NOW,
        provenance_package_digest=ZERO_DIGEST,
        visibility_receipt_digest=ONE_DIGEST,
        isolation_receipt_digest=ZERO_DIGEST,
    )

    assert accepted["decision"]["phase_b_authorized"] is False
    assert accepted["decision"]["research_release_created"] is False
    assert draft["created_by"] == "llm"
    assert freeze["decision"] == "accepted_for_comparison"
    assert freeze["phase_b_authorized"] is False
    assert freeze["research_release_created"] is False
    assert freeze["research_synthesis_digest"] == synthesis["digest"]
    assert freeze["claim_set_digest"] == synthesis["claim_set"]["digest"]


def test_digest_binding_rejects_mutated_synthesis() -> None:
    synthesis = _minimal_synthesis()
    mutated = copy.deepcopy(synthesis)
    mutated["claim_set"]["claims"][0]["statement"] = "Changed after digest freeze."
    with pytest.raises(ResearchSynthesisError, match="digest mismatch"):
        validate_synthesis_revision(mutated)


def test_source_supported_claims_need_support() -> None:
    synthesis = _minimal_synthesis()
    synthesis["claim_set"]["claims"][0]["support"] = []
    synthesis = _restamp_after_claim_change(synthesis)
    with pytest.raises(ResearchSynthesisError, match="source-backed claims require support"):
        validate_synthesis_revision(synthesis)


def test_proposition_hypothesis_cannot_be_source_supported() -> None:
    synthesis = _minimal_synthesis()
    synthesis["claim_set"]["claims"][1]["epistemic_status"] = "source_supported"
    synthesis = _restamp_after_claim_change(synthesis)
    with pytest.raises(ResearchSynthesisError, match="proposition_hypothesis cannot"):
        validate_synthesis_revision(synthesis)
