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
        relationship_edge("issue:1", "issue", issue_a, issue_b, "depends"),
        relationship_edge("change:1", "change", change, issue_a, "contains_issue"),
        relationship_edge("workflow:1", "workflow_statechart", change, change, "revision_loop"),
    ]

    report = validate_relationship_planes(edges)

    assert report["valid"] is True
    assert report["plane_counts"]["issue"] == 1
    assert report["plane_counts"]["change"] == 1
    assert report["acyclic_edges_checked"]["workflow_statechart"] == 0
    assert len(report["core_planes"]) == 8


def test_acyclic_plane_rejects_cycle_but_workflow_plane_allows_loop() -> None:
    first = workflow_ref("component", "skill:first")
    second = workflow_ref("component", "skill:second")
    cyclic = [
        relationship_edge("component:1", "component_dependency", first, second, "depends"),
        relationship_edge("component:2", "component_dependency", second, first, "depends"),
    ]

    with pytest.raises(RelationshipPlaneError, match="cycle detected"):
        validate_relationship_planes(cyclic)

    loops = [relationship_edge("workflow:loop", "workflow_statechart", first, first, "revision_loop")]
    assert validate_relationship_planes(loops)["valid"] is True


def test_relationship_edges_cannot_copy_mutable_state() -> None:
    invalid = {
        "schema": "adaos.workflow.relationship_edge.v1",
        "edge_id": "bad",
        "plane": "change",
        "plane_version": 1,
        "source": workflow_ref("aggregate", "change:1"),
        "target": workflow_ref("aggregate", "issue:1"),
        "relation": "contains",
        "ownership": "reference",
        "state": {"status": "working"},
    }

    with pytest.raises(RelationshipPlaneError, match="Additional properties"):
        validate_relationship_planes([invalid])


def test_relation_cannot_leak_between_normative_graph_planes() -> None:
    change = workflow_ref("aggregate", "change:1")
    issue = workflow_ref("aggregate", "issue:1")
    edge = relationship_edge(
        "change:wrong-relation",
        "change",
        change,
        issue,
        "contains_issue",
    )
    edge["relation"] = "published_as"

    with pytest.raises(RelationshipPlaneError, match="not valid in change plane"):
        validate_relationship_planes([edge])
