from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Mapping, Sequence

from adaos.services.governed_workflow import validate_workflow_record


RELATIONSHIP_EDGE_SCHEMA = "adaos.workflow.relationship_edge.v1"

PLANE_POLICIES: dict[str, dict[str, Any]] = {
    "demand": {"acyclic_relations": {"depends_on", "blocks"}},
    "delivery": {"acyclic_relations": {"depends_on", "supersedes", "split_from"}},
    "workflow": {"acyclic_relations": set()},
    "artifact_lineage": {"acyclic_relations": {"derived_from", "based_on", "produced_from"}},
    "component_dependency": {"acyclic_relations": {"depends_on", "requires"}},
    "execution": {"acyclic_relations": {"parent_of", "spawned"}},
    "conversation_interaction": {"acyclic_relations": {"response_to", "supersedes"}},
    "release_deployment": {"acyclic_relations": {"promotes", "replaces", "derived_from"}},
    "authority_trust": {"acyclic_relations": {"delegates_to", "approved_by"}},
    "view_context": {"acyclic_relations": {"projects", "focuses_on", "renders"}},
}


class RelationshipPlaneError(ValueError):
    """Raised when relationship planes leak state or violate graph policy."""


def relationship_edge(
    edge_id: str,
    plane: str,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    relation: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    edge = {
        "schema": RELATIONSHIP_EDGE_SCHEMA,
        "edge_id": str(edge_id or "").strip(),
        "plane": str(plane or "").strip(),
        "source": copy.deepcopy(dict(source)),
        "target": copy.deepcopy(dict(target)),
        "relation": str(relation or "").strip(),
        "ownership": "reference",
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    try:
        return validate_workflow_record(RELATIONSHIP_EDGE_SCHEMA, edge)
    except ValueError as exc:
        raise RelationshipPlaneError(str(exc)) from exc


def validate_relationship_planes(
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in edges:
        try:
            edge = validate_workflow_record(RELATIONSHIP_EDGE_SCHEMA, raw)
        except ValueError as exc:
            raise RelationshipPlaneError(str(exc)) from exc
        edge_id = str(edge["edge_id"])
        if edge_id in ids:
            raise RelationshipPlaneError(f"duplicate relationship edge_id: {edge_id}")
        ids.add(edge_id)
        if edge["plane"] not in PLANE_POLICIES:
            raise RelationshipPlaneError(f"unknown relationship plane: {edge['plane']}")
        normalized.append(edge)

    cycle_checks: dict[str, int] = {}
    for plane, policy in PLANE_POLICIES.items():
        acyclic = set(policy["acyclic_relations"])
        graph: dict[str, set[str]] = defaultdict(set)
        edge_count = 0
        for edge in normalized:
            if edge["plane"] != plane or edge["relation"] not in acyclic:
                continue
            source = f"{edge['source']['kind']}:{edge['source']['id']}"
            target = f"{edge['target']['kind']}:{edge['target']['id']}"
            if source == target:
                raise RelationshipPlaneError(
                    f"{plane} {edge['relation']} cannot reference itself: {source}"
                )
            graph[source].add(target)
            graph.setdefault(target, set())
            edge_count += 1
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise RelationshipPlaneError(f"cycle detected in {plane} acyclic relations at {node}")
            if node in visited:
                return
            visiting.add(node)
            for target in graph[node]:
                visit(target)
            visiting.remove(node)
            visited.add(node)

        for node in sorted(graph):
            visit(node)
        cycle_checks[plane] = edge_count

    return {
        "schema": "adaos.workflow.relationship_plane_report.v1",
        "valid": True,
        "edge_count": len(normalized),
        "plane_counts": {
            plane: sum(1 for edge in normalized if edge["plane"] == plane)
            for plane in PLANE_POLICIES
        },
        "acyclic_edges_checked": cycle_checks,
    }
