from __future__ import annotations

import pytest

from adaos.services.traceability import TraceabilityError, build_graph, evaluate_paths, validate_graph


def _graph() -> dict:
    return build_graph(
        "example:research",
        revision=1,
        nodes=[
            {"node_id": "source:notebook", "kind": "source"},
            {"node_id": "claim:c1", "kind": "scientific_claim"},
            {"node_id": "protocol:p1", "kind": "protocol"},
            {"node_id": "task:t1", "kind": "engineering_task"},
            {"node_id": "observation:o1", "kind": "observation"},
            {"node_id": "decision:d1", "kind": "acceptance_decision"},
        ],
        edges=[
            {"edge_id": "e1", "source": "source:notebook", "target": "claim:c1", "relation": "informs"},
            {"edge_id": "e2", "source": "claim:c1", "target": "protocol:p1", "relation": "operationalized_by"},
            {"edge_id": "e3", "source": "protocol:p1", "target": "task:t1", "relation": "implemented_by"},
            {"edge_id": "e4", "source": "task:t1", "target": "observation:o1", "relation": "produces"},
            {"edge_id": "e5", "source": "observation:o1", "target": "decision:d1", "relation": "evaluated_by"},
        ],
    )


def test_traceability_graph_proves_domain_supplied_chain() -> None:
    graph = _graph()

    report = evaluate_paths(
        graph,
        [
            {
                "requirement_id": "source-to-decision",
                "source": "source:notebook",
                "target": "decision:d1",
                "via_kinds": ["scientific_claim", "protocol", "engineering_task", "observation"],
            }
        ],
    )

    assert report["valid"] is True
    assert report["coverage"] == 1.0
    assert report["findings"][0]["path"][-1] == "decision:d1"


def test_traceability_coverage_reports_missing_stage_without_rewriting_graph() -> None:
    report = evaluate_paths(
        _graph(),
        [
            {
                "requirement_id": "requires-replication",
                "source": "source:notebook",
                "target": "decision:d1",
                "via_kinds": ["replication_run"],
            }
        ],
    )

    assert report["valid"] is False
    assert report["findings"][0]["path"] == []


def test_traceability_rejects_digest_and_endpoint_drift() -> None:
    graph = _graph()
    graph["nodes"][0]["kind"] = "changed"
    with pytest.raises(TraceabilityError, match="digest"):
        validate_graph(graph)

    with pytest.raises(TraceabilityError, match="missing node"):
        build_graph(
            "invalid",
            revision=1,
            nodes=[{"node_id": "one", "kind": "source"}],
            edges=[{"edge_id": "e", "source": "one", "target": "two", "relation": "informs"}],
        )
