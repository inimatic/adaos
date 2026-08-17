"""Digest-bound traceability graphs and domain-supplied path coverage."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class TraceabilityError(ValueError):
    """Raised when a traceability graph or coverage request is invalid."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / "traceability.graph.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_graph(value: Mapping[str, Any]) -> dict[str, Any]:
    graph = dict(value)
    errors = sorted(Draft202012Validator(_schema()).iter_errors(graph), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise TraceabilityError(f"traceability graph invalid at {location}: {error.message}")
    nodes = [str(item["node_id"]) for item in graph["nodes"]]
    edges = [str(item["edge_id"]) for item in graph["edges"]]
    if len(nodes) != len(set(nodes)):
        raise TraceabilityError("traceability node ids must be unique")
    if len(edges) != len(set(edges)):
        raise TraceabilityError("traceability edge ids must be unique")
    node_ids = set(nodes)
    for edge in graph["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            raise TraceabilityError(f"traceability edge {edge['edge_id']} references a missing node")
        if edge["source"] == edge["target"]:
            raise TraceabilityError(f"traceability edge {edge['edge_id']} is a self-reference")
    expected = _digest({key: item for key, item in graph.items() if key != "digest"})
    if graph["digest"] != expected:
        raise TraceabilityError("traceability graph digest does not match its content")
    return graph


def build_graph(
    graph_id: str,
    *,
    revision: int,
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "schema": "adaos.traceability.graph.v1",
        "graph_id": str(graph_id),
        "revision": int(revision),
        "nodes": sorted((dict(item) for item in nodes), key=lambda item: str(item.get("node_id") or "")),
        "edges": sorted((dict(item) for item in edges), key=lambda item: str(item.get("edge_id") or "")),
    }
    identity["digest"] = _digest(identity)
    return validate_graph(identity)


def _path_with_kinds(
    graph: Mapping[str, Any], source: str, target: str, via_kinds: Sequence[str]
) -> list[str] | None:
    kinds = {str(item["node_id"]): str(item["kind"]) for item in graph["nodes"]}
    if source not in kinds or target not in kinds:
        return None
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        adjacency[str(edge["source"])].append(str(edge["target"]))
    expected = [str(item) for item in via_kinds]
    initial_index = 1 if expected and kinds[source] == expected[0] else 0
    queue: deque[tuple[str, int, list[str]]] = deque([(source, initial_index, [source])])
    visited = {(source, initial_index)}
    while queue:
        node, index, path = queue.popleft()
        if node == target and index == len(expected):
            return path
        for candidate in sorted(adjacency[node]):
            next_index = index
            if next_index < len(expected) and kinds[candidate] == expected[next_index]:
                next_index += 1
            state = (candidate, next_index)
            if state not in visited:
                visited.add(state)
                queue.append((candidate, next_index, [*path, candidate]))
    return None


def evaluate_paths(
    value: Mapping[str, Any], requirements: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Evaluate domain-owned path requirements without embedding domain kinds in core."""

    graph = validate_graph(value)
    findings: list[dict[str, Any]] = []
    requirement_ids: set[str] = set()
    for raw in requirements:
        requirement = dict(raw)
        requirement_id = str(requirement.get("requirement_id") or "").strip()
        source = str(requirement.get("source") or "").strip()
        target = str(requirement.get("target") or "").strip()
        via_kinds = [str(item) for item in requirement.get("via_kinds") or []]
        if not requirement_id or requirement_id in requirement_ids or not source or not target:
            raise TraceabilityError("path requirements need unique ids, source, and target")
        requirement_ids.add(requirement_id)
        path = _path_with_kinds(graph, source, target, via_kinds)
        findings.append(
            {
                "requirement_id": requirement_id,
                "covered": path is not None,
                "source": source,
                "target": target,
                "via_kinds": via_kinds,
                "path": path or [],
            }
        )
    covered = sum(1 for item in findings if item["covered"])
    return {
        "schema": "adaos.traceability.coverage.v1",
        "graph_digest": graph["digest"],
        "valid": covered == len(findings),
        "covered": covered,
        "total": len(findings),
        "coverage": 1.0 if not findings else covered / len(findings),
        "findings": findings,
    }


__all__ = ["TraceabilityError", "build_graph", "evaluate_paths", "validate_graph"]
