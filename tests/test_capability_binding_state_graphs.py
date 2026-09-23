from pathlib import Path

import pytest

from adaos.services.capability_binding_state import (
    DerivedGraphError,
    DerivedGraphStore,
    build_evolver_observations,
    build_impact_projection,
    build_semantic_graph,
    build_viability_projection,
    create_evolver_proposal,
    promote_evolver_proposal,
)
from adaos.services.capability_binding_state.reference_crud import flowboard_contracts


def _resolution(claim_ref: str) -> dict:
    return {
        "schema": "adaos.application.resolution.v1",
        "resolution_ref": "application-resolution:flowboard/one",
        "resolution_digest": "sha256:" + "9" * 64,
        "requirement_ref": "requirement:flowboard/manage-work-items",
        "binding_definition": {"ref": "binding-definition:resource.records.local-json"},
        "selected_contracts": [{"ref": "capability:resource.records.manage"}],
        "package_closure": [{"digest": "sha256:" + "8" * 64}],
        "binding_instances": [{"ref": "binding-instance:flowboard/local"}],
        "state_attachments": [{"state_space_ref": "state-space:flowboard/work-items"}],
        "evidence": [{"claim_ref": claim_ref}],
        "provisioning_obligations": [],
        "rejection_explanations": [],
    }


def _assessment(claim_ref: str, status: str = "admissible") -> dict:
    return {
        "schema": "adaos.evidence.assessment.v1",
        "assessment_ref": "evidence-assessment:flowboard/current",
        "assessment_digest": "sha256:" + "7" * 64,
        "claim_ref": claim_ref,
        "claim_digest": "sha256:" + "6" * 64,
        "status": status,
    }


def test_graph_rebuild_is_deterministic_and_disposable(tmp_path: Path) -> None:
    contracts = flowboard_contracts()
    records = [
        contracts.requirement,
        contracts.capability,
        contracts.state,
        contracts.production_binding,
    ]
    store = DerivedGraphStore(tmp_path / "derived")

    first = store.rebuild(records)
    canonical_before = [record.to_dict() for record in records]
    store.delete()
    assert not store.projection_path.exists()
    second = store.rebuild(reversed(records))

    assert first == second
    assert canonical_before == [record.to_dict() for record in records]
    assert not any("locator" in str(node) for node in second["nodes"])


def test_impact_walks_from_contract_to_requirement_and_resolution() -> None:
    claim_ref = "evidence-claim:flowboard/conformance"
    contracts = flowboard_contracts()
    graph = build_semantic_graph(
        [
            contracts.capability,
            contracts.state,
            contracts.production_binding,
            contracts.simulation_binding,
            contracts.profile,
            contracts.requirement,
            _resolution(claim_ref),
            _assessment(claim_ref),
        ]
    )

    impact = build_impact_projection(graph, ["capability:resource.records.manage"])
    affected = {item["stable_ref"] for item in impact["affected"]}

    assert "requirement:flowboard/manage-work-items" in affected
    assert "binding-definition:resource.records.local-json" in affected
    assert "application-resolution:flowboard/one" in affected


def test_viability_and_evolver_are_read_only_current_views() -> None:
    claim_ref = "evidence-claim:flowboard/conformance"
    stale = _assessment(claim_ref, "stale")
    viability = build_viability_projection([_resolution(claim_ref), stale])
    observations = build_evolver_observations([stale])

    assert viability["resolutions"][0]["status"] == "not_viable"
    assert viability["resolutions"][0]["blockers"] == ["evidence_not_admissible"]
    assert observations["authority"] == "none"
    assert observations["observations"][0]["kind"] == "evidence_risk"


def test_evolver_maturity_requires_evidence_and_remains_advisory() -> None:
    local = create_evolver_proposal(
        proposal_ref="evolver-proposal:flowboard/resource-api",
        observation_refs=["observation:overlap"],
        summary="Extract a reusable resource operation",
    )
    candidate = promote_evolver_proposal(
        local, target="candidate", evidence={"review_ref": "review:architecture/1"}
    )
    with pytest.raises(DerivedGraphError, match="two independent consumers"):
        promote_evolver_proposal(
            candidate,
            target="reusable",
            evidence={
                "package_digest": "sha256:" + "1" * 64,
                "conformance_evidence_digest": "sha256:" + "2" * 64,
                "independent_consumer_refs": ["application:flowboard"],
            },
        )
    reusable = promote_evolver_proposal(
        candidate,
        target="reusable",
        evidence={
            "package_digest": "sha256:" + "1" * 64,
            "conformance_evidence_digest": "sha256:" + "2" * 64,
            "independent_consumer_refs": ["application:flowboard", "application:drive"],
        },
    )
    assert reusable["maturity"] == "reusable"
    assert reusable["authority"] == "advisory_only"


def test_evolver_observes_gaps_repeated_schemas_and_package_cooccurrence() -> None:
    contracts = flowboard_contracts()
    state_copy = contracts.state.to_dict()
    state_copy["state_contract_ref"] = "state-contract:inventory.items"
    state_copy["contract_digest"] = "sha256:" + "5" * 64
    package_a = "sha256:" + "1" * 64
    package_b = "sha256:" + "2" * 64
    resolutions = []
    for suffix in ("one", "two"):
        value = _resolution(f"evidence-claim:flowboard/{suffix}")
        value["resolution_ref"] = f"application-resolution:flowboard/{suffix}"
        value["resolution_digest"] = "sha256:" + ("3" if suffix == "one" else "4") * 64
        value["package_closure"] = [{"digest": package_a}, {"digest": package_b}]
        value["rejection_explanations"] = [
            {"code": "missing_capability_dependency"}
        ]
        resolutions.append(value)

    result = build_evolver_observations(
        [contracts.state, state_copy, *resolutions]
    )
    kinds = [item["kind"] for item in result["observations"]]

    assert kinds.count("capability_gap") == 2
    assert "repeated_schema" in kinds
    assert "package_cooccurrence" in kinds
