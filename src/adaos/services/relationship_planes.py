from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Mapping, Sequence

from adaos.services.governed_workflow import validate_workflow_record


RELATIONSHIP_EDGE_SCHEMA = "adaos.workflow.relationship_edge.v1"

PLANE_ALIASES = {
    "demand": "issue",
    "delivery": "change",
    "workflow": "workflow_statechart",
}

PLANE_POLICIES: dict[str, dict[str, Any]] = {
    "issue": {
        "relations": {"duplicate", "depends", "blocks", "related"},
        "acyclic_relations": {"depends", "blocks"},
    },
    "change": {
        "relations": {"contains_issue", "alternative", "supersedes", "depends", "split_from"},
        "acyclic_relations": {"depends", "supersedes", "split_from"},
    },
    "workflow_statechart": {
        "relations": {"transition", "revision_loop", "subworkflow"},
        "acyclic_relations": {"subworkflow"},
    },
    "artifact_lineage": {
        "relations": {"derived_from", "candidate_of", "published_as", "implements"},
        "acyclic_relations": {"derived_from", "candidate_of", "published_as", "implements"},
    },
    "component_dependency": {
        "relations": {"depends", "requires", "runtime_binding", "conflicts"},
        "acyclic_relations": {"depends", "requires", "runtime_binding"},
    },
    "execution": {
        "relations": {"attempt_of", "child_task", "retry_of", "recovery_of", "caused_by"},
        "acyclic_relations": {"attempt_of", "child_task", "retry_of", "recovery_of", "caused_by"},
    },
    "conversation_interaction": {
        "relations": {"message_in", "thread_of", "interaction_for", "response_to", "reply_route_for", "causes", "correlates", "supersedes"},
        "acyclic_relations": {"message_in", "thread_of", "interaction_for", "response_to", "reply_route_for", "causes", "supersedes"},
    },
    "release_deployment": {
        "relations": {"source_of", "packaged_as", "candidate_for", "promoted_to", "activated_as", "locked_by", "replaces"},
        "acyclic_relations": {"source_of", "packaged_as", "candidate_for", "promoted_to", "activated_as", "locked_by", "replaces"},
    },
    "authority_trust": {
        "relations": {"delegates_to", "approved_by", "authorized_by", "member_of"},
        "acyclic_relations": {"delegates_to", "approved_by", "authorized_by"},
    },
    "view_context": {
        "relations": {"projects", "focuses_on", "renders", "previews"},
        "acyclic_relations": {"projects", "focuses_on", "renders", "previews"},
    },
}

RELATION_ALIASES = {
    "depends_on": "depends",
    "alternative_to": "alternative",
    "contains": "contains_issue",
    "revise": "revision_loop",
    "based_on": "derived_from",
    "produced_from": "derived_from",
    "parent_of": "child_task",
    "spawned": "child_task",
    "promotes": "promoted_to",
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
    normalized_plane = PLANE_ALIASES.get(str(plane or "").strip(), str(plane or "").strip())
    normalized_relation = RELATION_ALIASES.get(
        str(relation or "").strip(), str(relation or "").strip()
    )
    edge = {
        "schema": RELATIONSHIP_EDGE_SCHEMA,
        "edge_id": str(edge_id or "").strip(),
        "plane": normalized_plane,
        "plane_version": 1,
        "source": copy.deepcopy(dict(source)),
        "target": copy.deepcopy(dict(target)),
        "relation": normalized_relation,
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
        candidate = copy.deepcopy(dict(raw))
        candidate["plane"] = PLANE_ALIASES.get(
            str(candidate.get("plane") or ""), str(candidate.get("plane") or "")
        )
        candidate["relation"] = RELATION_ALIASES.get(
            str(candidate.get("relation") or ""), str(candidate.get("relation") or "")
        )
        candidate.setdefault("plane_version", 1)
        try:
            edge = validate_workflow_record(RELATIONSHIP_EDGE_SCHEMA, candidate)
        except ValueError as exc:
            raise RelationshipPlaneError(str(exc)) from exc
        edge_id = str(edge["edge_id"])
        if edge_id in ids:
            raise RelationshipPlaneError(f"duplicate relationship edge_id: {edge_id}")
        ids.add(edge_id)
        if edge["plane"] not in PLANE_POLICIES:
            raise RelationshipPlaneError(f"unknown relationship plane: {edge['plane']}")
        policy = PLANE_POLICIES[edge["plane"]]
        if edge["relation"] not in policy["relations"]:
            raise RelationshipPlaneError(
                f"relation {edge['relation']} is not valid in {edge['plane']} plane"
            )
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
        "core_planes": [
            "issue", "change", "workflow_statechart", "artifact_lineage",
            "component_dependency", "execution", "conversation_interaction",
            "release_deployment",
        ],
        "plane_counts": {
            plane: sum(1 for edge in normalized if edge["plane"] == plane)
            for plane in PLANE_POLICIES
        },
        "acyclic_edges_checked": cycle_checks,
    }
