from __future__ import annotations

import pytest

from adaos.services.governed_workflow import workflow_ref
from adaos.services.relationship_planes import (
    RelationshipPlaneError,
    relationship_edge,
    validate_relationship_planes,
)


def test_relationship_planes_validate_independently() -> None:
    issue_a = workflow_ref("aggregate", "issue:a")
    issue_b = workflow_ref("aggregate", "issue:b")
    change = workflow_ref("aggregate", "change:1")
    edges = [
        relationship_edge("demand:1", "demand", issue_a, issue_b, "depends_on"),
        relationship_edge("delivery:1", "delivery", change, issue_a, "contains"),
        relationship_edge("workflow:1", "workflow", change, change, "revise"),
    ]

    report = validate_relationship_planes(edges)

    assert report["valid"] is True
    assert report["plane_counts"]["demand"] == 1
    assert report["plane_counts"]["delivery"] == 1
    assert report["acyclic_edges_checked"]["workflow"] == 0


def test_acyclic_plane_rejects_cycle_but_workflow_plane_allows_loop() -> None:
    first = workflow_ref("component", "skill:first")
    second = workflow_ref("component", "skill:second")
    cyclic = [
        relationship_edge("component:1", "component_dependency", first, second, "depends_on"),
        relationship_edge("component:2", "component_dependency", second, first, "depends_on"),
    ]

    with pytest.raises(RelationshipPlaneError, match="cycle detected"):
        validate_relationship_planes(cyclic)

    loops = [relationship_edge("workflow:loop", "workflow", first, first, "revise")]
    assert validate_relationship_planes(loops)["valid"] is True


def test_relationship_edges_cannot_copy_mutable_state() -> None:
    invalid = {
        "schema": "adaos.workflow.relationship_edge.v1",
        "edge_id": "bad",
        "plane": "delivery",
        "source": workflow_ref("aggregate", "change:1"),
        "target": workflow_ref("aggregate", "issue:1"),
        "relation": "contains",
        "ownership": "reference",
        "state": {"status": "working"},
    }

    with pytest.raises(RelationshipPlaneError, match="Additional properties"):
        validate_relationship_planes([invalid])
