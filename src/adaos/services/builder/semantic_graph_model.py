"""Canonical graph projection for the current semantic v2 authoring source.

The persisted ``semantic.webui.json`` document remains the single authoring
source.  This module gives Core one lossless graph model for validation,
dependency closure, reduction and impact evidence.  It deliberately does not
add blueprint or inference-time semantics; those remain later catalog/profile
extensions after parity with the current implementation is established.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from .workflow import BuilderWorkflowError


GRAPH_SCHEMA = "adaos.builder.semantic_graph.v1"
SOURCE_SCHEMA = "adaos.webui.semantic.v2"

_COLLECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("resources", "resource", "resource", "document.resource"),
    ("relationships", "relationship", "relationship", "document.relationship"),
    ("views", "view", "view", "document.view"),
    ("capability_surfaces", "surface", "capability_surface", "document.surface"),
    ("commands", "command", "command", "document.command"),
    ("representative_states", "state", "representative_state", "document.state"),
    ("requirement_bindings", "requirement", "requirement_binding", "document.requirement"),
    ("automation_requirements", "automation", "automation_requirement", "document.automation"),
    ("capability_gaps", "gap", "capability_gap", "document.gap"),
)
_COLLECTION_KEYS = {item[0] for item in _COLLECTIONS}


class SemanticGraphError(BuilderWorkflowError):
    """The graph cannot losslessly represent a supported semantic document."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _graph_digest(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop("graph_digest", None)
    return _digest(payload)


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi/builder.semantic_graph.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(prefix: str, value: Any) -> str:
    return f"{prefix}:{_digest(value)[7:]}"


def _field_ref(resource_id: str, field_id: str) -> str:
    identity = str(field_id)
    return "field:" + (
        identity if identity.startswith(resource_id + ".") else f"{resource_id}.{identity}"
    )


def _node_contract(kind: str) -> str:
    names = {
        "document": "document",
        "layout": "layout",
        "layout_region": "layoutRegion",
        "resource": "resource",
        "field": "field",
        "identity_field": "implicitIdentityField",
        "relationship": "relationship",
        "view": "view",
        "query": "queryControl",
        "capability_surface": "capabilitySurface",
        "capability": "capability",
        "command": "command",
        "representative_state": "representativeState",
        "requirement_binding": "requirementBinding",
        "automation_requirement": "automationRequirement",
        "capability_gap": "capabilityGap",
    }
    return f"{SOURCE_SCHEMA}#{names[kind]}"


def _edge_key(edge: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(edge["source"]),
        str(edge["relation"]),
        int(edge.get("ordinal", -1)),
        str(edge["target"]),
        str(edge.get("path") or ""),
        bool(edge["dependency"]),
    )


def _field_reference_values(value: Any, path: str = "$"):
    """Yield all current semantic-v2 field refs with their exact source path."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.endswith("field_ref") and isinstance(child, str):
                yield key, child, child_path, None
            elif key.endswith("field_refs") and isinstance(child, list):
                for index, item in enumerate(child):
                    if isinstance(item, str):
                        yield key, item, f"{child_path}[{index}]", index
            else:
                yield from _field_reference_values(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _field_reference_values(child, f"{path}[{index}]")


def semantic_document_to_graph(document: Mapping[str, Any]) -> dict[str, Any]:
    """Project one validated semantic-v2 document into a deterministic graph."""
    from .semantic_prototype import validate_semantic_prototype

    source = copy.deepcopy(dict(document))
    if source.get("schema") != SOURCE_SCHEMA:
        raise SemanticGraphError("semantic graph requires adaos.webui.semantic.v2")
    validate_semantic_prototype(source)

    document_id = str(source["document_id"])
    root_ref = f"document:{document_id}"
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def add_node(ref: str, kind: str, payload: Mapping[str, Any], source_path: str | None) -> None:
        if ref in nodes:
            raise SemanticGraphError(f"duplicate semantic graph node: {ref}")
        nodes[ref] = {
            "ref": ref,
            "kind": kind,
            "contract": _node_contract(kind),
            "source_path": source_path,
            "payload": copy.deepcopy(dict(payload)),
        }

    def add_edge(
        source_ref: str,
        relation: str,
        target_ref: str,
        *,
        dependency: bool,
        ordinal: int | None = None,
        path: str | None = None,
    ) -> None:
        edge: dict[str, Any] = {
            "source": source_ref,
            "relation": relation,
            "target": target_ref,
            "dependency": dependency,
        }
        if ordinal is not None:
            edge["ordinal"] = ordinal
        if path is not None:
            edge["path"] = path
        edges.append(edge)

    root_payload = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key not in {"schema", "layout", *_COLLECTION_KEYS}
    }
    add_node(root_ref, "document", root_payload, "$")

    layout = copy.deepcopy(dict(source["layout"]))
    regions = list(layout.pop("regions", []))
    layout_ref = f"layout:{document_id}"
    add_node(layout_ref, "layout", layout, "$.layout")
    add_edge(root_ref, "document.layout", layout_ref, dependency=False, ordinal=0, path="$.layout")
    for ordinal, region in enumerate(regions):
        region_ref = f"region:{region['id']}"
        add_node(region_ref, "layout_region", region, f"$.layout.regions[{ordinal}]")
        add_edge(
            layout_ref,
            "layout.region",
            region_ref,
            dependency=False,
            ordinal=ordinal,
            path=f"$.regions[{ordinal}]",
        )

    # Source nodes are created before reference edges so all dependencies can
    # be checked as closed graph references.
    for collection, prefix, kind, relation in _COLLECTIONS:
        for ordinal, item in enumerate(source.get(collection, [])):
            payload = copy.deepcopy(dict(item))
            if collection == "resources":
                fields = list(payload.pop("fields"))
                identities = list(payload.pop("identity_field_refs"))
                ref = f"resource:{item['id']}"
                add_node(ref, kind, payload, f"$.{collection}[{ordinal}]")
                add_edge(root_ref, relation, ref, dependency=False, ordinal=ordinal, path=f"$.{collection}[{ordinal}]")
                declared: set[str] = set()
                for field_ordinal, field in enumerate(fields):
                    field_ref = _field_ref(str(item["id"]), str(field["id"]))
                    declared.add(field_ref)
                    add_node(
                        field_ref,
                        "field",
                        field,
                        f"$.{collection}[{ordinal}].fields[{field_ordinal}]",
                    )
                    add_edge(
                        ref,
                        "resource.field",
                        field_ref,
                        dependency=False,
                        ordinal=field_ordinal,
                        path=f"$.fields[{field_ordinal}]",
                    )
                for identity_ordinal, identity in enumerate(identities):
                    field_ref = _field_ref(str(item["id"]), str(identity))
                    if field_ref not in declared and field_ref not in nodes:
                        add_node(
                            field_ref,
                            "identity_field",
                            {"id": identity},
                            None,
                        )
                    add_edge(
                        ref,
                        "resource.identity_field",
                        field_ref,
                        dependency=False,
                        ordinal=identity_ordinal,
                        path=f"$.identity_field_refs[{identity_ordinal}]",
                    )
                    add_edge(
                        ref,
                        "resource.identity",
                        field_ref,
                        dependency=True,
                        ordinal=identity_ordinal,
                        path=f"$.identity_field_refs[{identity_ordinal}]",
                    )
                continue

            if collection == "automation_requirements":
                # One user requirement may legitimately produce distinct
                # business-rule and external-integration obligations.
                ref = _identity(prefix, item)
            elif collection in {"requirement_bindings", "capability_gaps"}:
                ref = _identity(prefix, item["requirement_ref"])
            else:
                ref = f"{prefix}:{item['id']}"
            add_node(ref, kind, payload, f"$.{collection}[{ordinal}]")
            add_edge(root_ref, relation, ref, dependency=False, ordinal=ordinal, path=f"$.{collection}[{ordinal}]")
            if collection == "views":
                for query_ordinal, query in enumerate(item.get("query_controls", [])):
                    query_ref = f"query:{query['id']}"
                    add_node(
                        query_ref,
                        "query",
                        query,
                        f"$.{collection}[{ordinal}].query_controls[{query_ordinal}]",
                    )
                    add_edge(
                        ref,
                        "view.query",
                        query_ref,
                        dependency=True,
                        ordinal=query_ordinal,
                        path=f"$.query_controls[{query_ordinal}]",
                    )

    region_nodes = {
        ref.partition(":")[2]: ref
        for ref, node in nodes.items()
        if node["kind"] == "layout_region"
    }
    state_nodes = {
        ref.partition(":")[2]: ref
        for ref, node in nodes.items()
        if node["kind"] == "representative_state"
    }

    def add_known_dependency(
        source_ref: str,
        relation: str,
        target_ref: str,
        path: str,
        ordinal: int | None = None,
    ) -> None:
        if target_ref in nodes:
            add_edge(
                source_ref,
                relation,
                target_ref,
                dependency=True,
                ordinal=ordinal,
                path=path,
            )

    # Preserve the dependency semantics already used by bounded semantic
    # admission.  Containment answers where a field is serialized; these
    # directed edges answer which semantic facts must be read when that field
    # is edited.  Reverse relationship edges are intentional impact context,
    # not ownership.
    for resource in source["resources"]:
        resource_ref = f"resource:{resource['id']}"
        declared_field_refs: set[str] = set()
        for field in resource["fields"]:
            declared_ref = _field_ref(str(resource["id"]), str(field["id"]))
            declared_field_refs.add(declared_ref)
            add_known_dependency(
                declared_ref,
                "field.resource",
                resource_ref,
                "$.owner_resource",
            )
        for identity in resource["identity_field_refs"]:
            identity_ref = _field_ref(str(resource["id"]), str(identity))
            if identity_ref in declared_field_refs:
                continue
            add_known_dependency(
                identity_ref,
                "field.resource",
                resource_ref,
                "$.owner_resource",
            )

    for resource in source["resources"]:
        resource_id = str(resource["id"])
        for field in resource["fields"]:
            source_ref = _field_ref(resource_id, str(field["id"]))
            for key, identity, path, ordinal in _field_reference_values(field):
                add_known_dependency(
                    source_ref,
                    "field.field",
                    _field_ref(resource_id, identity),
                    path,
                    ordinal,
                )

    for relationship in source["relationships"]:
        source_ref = f"relationship:{relationship['id']}"
        from_resource = str(relationship["from_resource_ref"])
        to_resource = str(relationship["to_resource_ref"])
        add_known_dependency(source_ref, "relationship.from_resource", f"resource:{from_resource}", "$.from_resource_ref")
        add_known_dependency(source_ref, "relationship.to_resource", f"resource:{to_resource}", "$.to_resource_ref")
        add_known_dependency(source_ref, "relationship.from_field", _field_ref(from_resource, str(relationship["from_field_ref"])), "$.from_field_ref")
        add_known_dependency(source_ref, "relationship.to_field", _field_ref(to_resource, str(relationship["to_field_ref"])), "$.to_field_ref")
        add_known_dependency(
            _field_ref(from_resource, str(relationship["from_field_ref"])),
            "field.relationship",
            source_ref,
            "$.relationship_impact",
        )
        for ordinal, field_id in enumerate(relationship.get("label_field_refs", [])):
            add_known_dependency(
                source_ref,
                "relationship.label_field",
                _field_ref(to_resource, str(field_id)),
                f"$.label_field_refs[{ordinal}]",
                ordinal,
            )
        if not relationship.get("label_field_refs"):
            target = next(
                (
                    view
                    for view in source["views"]
                    if view["resource_ref"] == relationship["to_resource_ref"]
                    and view["role"] == "collection"
                ),
                None,
            )
            if target is not None:
                add_known_dependency(
                    source_ref,
                    "relationship.implicit_label_view",
                    f"view:{target['id']}",
                    "$.implicit_label_view",
                )

    view_resource: dict[str, str] = {}
    for view in source["views"]:
        source_ref = f"view:{view['id']}"
        resource_id = str(view["resource_ref"])
        view_resource[str(view["id"])] = resource_id
        add_known_dependency(source_ref, "view.resource", f"resource:{resource_id}", "$.resource_ref")
        source_view_id = str((view.get("selection_filter") or {}).get("source_view_ref") or "")
        source_resource_id = view_resource.get(source_view_id)
        if source_resource_id is None:
            source_view = next((item for item in source["views"] if str(item["id"]) == source_view_id), None)
            source_resource_id = str(source_view["resource_ref"]) if source_view else None
        for key, identity, path, ordinal in _field_reference_values(view):
            field_resource = source_resource_id if key == "source_field_ref" and source_resource_id else resource_id
            add_known_dependency(source_ref, "view.field", _field_ref(field_resource, identity), path, ordinal)
        for key in ("activation_source_view_ref",):
            identity = view.get(key)
            if isinstance(identity, str):
                add_known_dependency(source_ref, "view.source_view", f"view:{identity}", f"$.{key}")
        selection_filter = view.get("selection_filter") or {}
        if isinstance(selection_filter.get("source_view_ref"), str):
            add_known_dependency(
                source_ref,
                "view.source_view",
                f"view:{selection_filter['source_view_ref']}",
                "$.selection_filter.source_view_ref",
            )
        region_role = str(view.get("region_role") or "")
        if region_role in region_nodes:
            add_known_dependency(source_ref, "view.region", region_nodes[region_role], "$.region_role")
        filter_state = (view.get("filter") or {}).get("state_ref")
        if isinstance(filter_state, str) and filter_state in state_nodes:
            add_known_dependency(source_ref, "view.state", state_nodes[filter_state], "$.filter.state_ref")
        for query in view.get("query_controls", []):
            query_ref = f"query:{query['id']}"
            add_known_dependency(query_ref, "query.view", source_ref, "$.owner_view")
            for _key, identity, path, ordinal in _field_reference_values(query):
                add_known_dependency(query_ref, "query.field", _field_ref(resource_id, identity), path, ordinal)

    for command in source["commands"]:
        source_ref = f"command:{command['id']}"
        view_id = str(command["view_ref"])
        add_known_dependency(source_ref, "command.view", f"view:{view_id}", "$.view_ref")
        resource_id = view_resource.get(view_id)
        if resource_id:
            for _key, identity, path, ordinal in _field_reference_values(command):
                add_known_dependency(source_ref, "command.field", _field_ref(resource_id, identity), path, ordinal)
            for ordinal, field_id in enumerate((command.get("fixed_values") or {}).keys()):
                add_known_dependency(
                    source_ref,
                    "command.field",
                    _field_ref(resource_id, str(field_id)),
                    f"$.fixed_values.{field_id}",
                    ordinal,
                )

    for state in source["representative_states"]:
        source_ref = f"state:{state['id']}"
        view_id = str(state["view_ref"])
        add_known_dependency(source_ref, "state.view", f"view:{view_id}", "$.view_ref")
        resource_id = view_resource.get(view_id)
        if resource_id:
            for _key, identity, path, ordinal in _field_reference_values(state):
                add_known_dependency(source_ref, "state.field", _field_ref(resource_id, identity), path, ordinal)

    for binding in source["requirement_bindings"]:
        source_ref = _identity("requirement", binding["requirement_ref"])
        for ordinal, semantic_ref in enumerate(binding["semantic_refs"]):
            target_ref = str(semantic_ref)
            if target_ref.startswith("field:") and target_ref not in nodes:
                field_id = target_ref.partition(":")[2]
                candidates = [
                    ref
                    for ref, node in nodes.items()
                    if node["kind"] in {"field", "identity_field"}
                    and (
                        str(node["payload"]["id"]) == field_id
                        or str(node["payload"]["id"]).rpartition(".")[2] == field_id
                    )
                ]
                if len(candidates) == 1:
                    target_ref = candidates[0]
            add_known_dependency(
                source_ref,
                "requirement.binds",
                target_ref,
                f"$.semantic_refs[{ordinal}]",
                ordinal,
            )

    for automation in source.get("automation_requirements", []):
        add_known_dependency(
            _identity("automation", automation),
            "automation.requirement",
            _identity("requirement", automation["requirement_ref"]),
            "$.requirement_ref",
        )

    for gap in source.get("capability_gaps", []):
        add_known_dependency(
            _identity("gap", gap["requirement_ref"]),
            "gap.requirement",
            _identity("requirement", gap["requirement_ref"]),
            "$.requirement_ref",
        )

    for surface in source.get("capability_surfaces", []):
        source_ref = f"surface:{surface['id']}"
        capability_ref = str(surface["capability_ref"])
        target_ref = f"capability:{capability_ref}"
        if target_ref not in nodes:
            add_node(target_ref, "capability", {"capability_ref": capability_ref}, None)
        add_edge(
            source_ref,
            "surface.capability",
            target_ref,
            dependency=True,
            path="$.capability_ref",
        )
        region_role = str(surface.get("region_role") or "")
        if region_role in region_nodes:
            add_known_dependency(source_ref, "surface.region", region_nodes[region_role], "$.region_role")

    graph: dict[str, Any] = {
        "schema": GRAPH_SCHEMA,
        "graph_id": f"graph:{document_id}",
        "root_ref": root_ref,
        "source_schema": SOURCE_SCHEMA,
        "source_digest": _digest(source),
        "graph_digest": "sha256:" + "0" * 64,
        "source_collections": [key for key, *_rest in _COLLECTIONS if key in source],
        "nodes": sorted(nodes.values(), key=lambda item: item["ref"]),
        "edges": sorted(edges, key=_edge_key),
    }
    graph["graph_digest"] = _graph_digest(graph)
    _validate_graph_structure(graph, verify_digests=True)
    return graph


def _validate_graph_structure(graph: Mapping[str, Any], *, verify_digests: bool) -> None:
    try:
        Draft202012Validator(_schema()).validate(dict(graph))
    except ValidationError as exc:
        raise SemanticGraphError(f"invalid semantic graph: {exc.message}") from exc
    if verify_digests and graph["graph_digest"] != _graph_digest(graph):
        raise SemanticGraphError("semantic graph digest mismatch")
    nodes = list(graph["nodes"])
    index = {str(item["ref"]): item for item in nodes}
    if len(index) != len(nodes):
        raise SemanticGraphError("semantic graph contains duplicate node refs")
    root = index.get(str(graph["root_ref"]))
    if root is None or root["kind"] != "document":
        raise SemanticGraphError("semantic graph root must resolve to one document node")
    seen_edges: set[tuple[Any, ...]] = set()
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["source"] not in index or edge["target"] not in index:
            raise SemanticGraphError("semantic graph edge endpoint is missing")
        key = _edge_key(edge)
        if key in seen_edges:
            raise SemanticGraphError("semantic graph contains duplicate edges")
        seen_edges.add(key)
        if not edge["dependency"] and "ordinal" in edge:
            groups[(str(edge["source"]), str(edge["relation"]))].append(int(edge["ordinal"]))
    for identity, ordinals in groups.items():
        if sorted(ordinals) != list(range(len(ordinals))):
            raise SemanticGraphError(f"semantic graph containment ordinals are not contiguous: {identity}")


def _children(graph: Mapping[str, Any], source: str, relation: str) -> list[str]:
    matches = [
        edge
        for edge in graph["edges"]
        if edge["source"] == source and edge["relation"] == relation and not edge["dependency"]
    ]
    return [str(edge["target"]) for edge in sorted(matches, key=lambda item: int(item["ordinal"]))]


def _graph_to_document_unchecked(graph: Mapping[str, Any]) -> dict[str, Any]:
    index = {str(item["ref"]): item for item in graph["nodes"]}
    root_ref = str(graph["root_ref"])
    document = {"schema": SOURCE_SCHEMA, **copy.deepcopy(index[root_ref]["payload"])}
    layout_refs = _children(graph, root_ref, "document.layout")
    if len(layout_refs) != 1:
        raise SemanticGraphError("semantic graph requires exactly one layout")
    layout_ref = layout_refs[0]
    layout = copy.deepcopy(index[layout_ref]["payload"])
    region_refs = _children(graph, layout_ref, "layout.region")
    if region_refs:
        layout["regions"] = [copy.deepcopy(index[ref]["payload"]) for ref in region_refs]
    document["layout"] = layout

    for collection, _prefix, _kind, relation in _COLLECTIONS:
        if collection not in graph["source_collections"]:
            continue
        values: list[dict[str, Any]] = []
        for ref in _children(graph, root_ref, relation):
            value = copy.deepcopy(index[ref]["payload"])
            if collection == "resources":
                value["identity_field_refs"] = [
                    copy.deepcopy(index[field_ref]["payload"]["id"])
                    for field_ref in _children(graph, ref, "resource.identity_field")
                ]
                value["fields"] = [
                    copy.deepcopy(index[field_ref]["payload"])
                    for field_ref in _children(graph, ref, "resource.field")
                ]
            values.append(value)
        document[collection] = values
    return document


def validate_semantic_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Validate graph structure, source parity and canonical reconstruction."""
    _validate_graph_structure(graph, verify_digests=True)
    document = _graph_to_document_unchecked(graph)
    from .semantic_prototype import validate_semantic_prototype

    validate_semantic_prototype(document)
    if _digest(document) != graph["source_digest"]:
        raise SemanticGraphError("semantic graph source digest mismatch")
    if graph["graph_id"] != f"graph:{document['document_id']}" or graph["root_ref"] != f"document:{document['document_id']}":
        raise SemanticGraphError("semantic graph identity differs from its source document")
    rebuilt = semantic_document_to_graph(document)
    if rebuilt != graph:
        raise SemanticGraphError("semantic graph is not the canonical projection of its source")
    return semantic_graph_receipt(graph)


def semantic_graph_to_document(graph: Mapping[str, Any]) -> dict[str, Any]:
    validate_semantic_graph(graph)
    return _graph_to_document_unchecked(graph)


def canonicalize_semantic_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild digests and derived edges after an admitted node-payload edit."""
    _validate_graph_structure(graph, verify_digests=False)
    document = _graph_to_document_unchecked(graph)
    from .semantic_prototype import validate_semantic_prototype

    validate_semantic_prototype(document)
    return semantic_document_to_graph(document)


def semantic_graph_receipt(graph: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "adaos.builder.semantic_graph_receipt.v1",
        "graph_schema": str(graph["schema"]),
        "graph_id": str(graph["graph_id"]),
        "root_ref": str(graph["root_ref"]),
        "source_schema": str(graph["source_schema"]),
        "source_digest": str(graph["source_digest"]),
        "graph_digest": str(graph["graph_digest"]),
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "roundtrip": True,
    }


def semantic_graph_dependency_closure(
    graph: Mapping[str, Any], roots: Sequence[str]
) -> list[str]:
    validate_semantic_graph(graph)
    index = {str(item["ref"]) for item in graph["nodes"]}
    pending = [str(ref) for ref in roots]
    if any(ref not in index for ref in pending):
        raise SemanticGraphError("dependency roots are absent from the semantic graph")
    dependencies: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["dependency"]:
            dependencies[str(edge["source"])].append(str(edge["target"]))
    visited: set[str] = set()
    while pending:
        ref = pending.pop()
        if ref in visited:
            continue
        visited.add(ref)
        pending.extend(dependencies.get(ref, []))
    return sorted(visited)


def _diff_values(
    ref: str,
    before: Any,
    after: Any,
    path: str,
    changes: list[dict[str, Any]],
) -> None:
    if before == after:
        return
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}"
            if key not in before:
                changes.append({"kind": "property.add", "ref": ref, "path": child_path, "after": copy.deepcopy(after[key])})
            elif key not in after:
                changes.append({"kind": "property.remove", "ref": ref, "path": child_path, "before": copy.deepcopy(before[key])})
            else:
                _diff_values(ref, before[key], after[key], child_path, changes)
        return
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            _diff_values(ref, left, right, f"{path}[{index}]", changes)
        return
    changes.append(
        {
            "kind": "property.set",
            "ref": ref,
            "path": path,
            "before": copy.deepcopy(before),
            "after": copy.deepcopy(after),
        }
    )


def semantic_graph_diff(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    validate_semantic_graph(before)
    validate_semantic_graph(after)
    left = {str(item["ref"]): item for item in before["nodes"]}
    right = {str(item["ref"]): item for item in after["nodes"]}
    changes: list[dict[str, Any]] = []
    for ref in sorted(set(left) | set(right)):
        if ref not in left:
            changes.append({"kind": "node.add", "ref": ref, "after": copy.deepcopy(right[ref])})
        elif ref not in right:
            changes.append({"kind": "node.remove", "ref": ref, "before": copy.deepcopy(left[ref])})
        else:
            _diff_values(ref, left[ref]["payload"], right[ref]["payload"], "$.payload", changes)
    left_edges = {_canonical_bytes(item): item for item in before["edges"]}
    right_edges = {_canonical_bytes(item): item for item in after["edges"]}
    for key in sorted(set(left_edges) - set(right_edges)):
        edge = left_edges[key]
        changes.append({"kind": "edge.remove", "ref": str(edge["source"]), "before": copy.deepcopy(edge)})
    for key in sorted(set(right_edges) - set(left_edges)):
        edge = right_edges[key]
        changes.append({"kind": "edge.add", "ref": str(edge["source"]), "after": copy.deepcopy(edge)})
    if len(changes) > 4096:
        raise SemanticGraphError("semantic graph diff exceeds bounded capacity")
    return {
        "schema": "adaos.builder.semantic_graph_diff.v1",
        "before_graph_digest": str(before["graph_digest"]),
        "after_graph_digest": str(after["graph_digest"]),
        "before_source_digest": str(before["source_digest"]),
        "after_source_digest": str(after["source_digest"]),
        "write_refs": sorted({str(item["ref"]) for item in changes}),
        "changes": changes,
    }


__all__ = [
    "GRAPH_SCHEMA",
    "SOURCE_SCHEMA",
    "SemanticGraphError",
    "canonicalize_semantic_graph",
    "semantic_document_to_graph",
    "semantic_graph_dependency_closure",
    "semantic_graph_diff",
    "semantic_graph_receipt",
    "semantic_graph_to_document",
    "validate_semantic_graph",
]
