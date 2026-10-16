"""Builder projections into the platform's existing digest-bound graph.

Missing links stay missing. A successful authoring run or source-map edge is
never promoted to evidence that a user-visible postcondition passed.
"""
from __future__ import annotations

import copy
import hashlib
import json

from adaos.services.traceability import build_graph


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def project_change(*, project_ref, generation, change, semantic_document=None, source_map=None):
    nodes, edges, identities, gaps = {}, {}, {}, []
    semantic = semantic_document or {}

    def node(kind, ref, value, metadata=None):
        version = digest(value)
        identity = kind + ":" + digest(ref)[7:23] + ":" + version[7:23]
        nodes[identity] = {"node_id": identity, "kind": kind, "ref": ref, "digest": version,
                           "metadata": copy.deepcopy(metadata or {})}
        identities[(kind, ref)] = identity
        return identity

    def edge(source, target, relation, metadata=None):
        identity = "edge:" + digest([source, target, relation])[7:39]
        edges[identity] = {"edge_id": identity, "source": source, "target": target, "relation": relation,
                           "metadata": copy.deepcopy(metadata or {})}

    for collection, kind in (("resources", "resource"), ("views", "view"), ("commands", "command"),
                              ("relationships", "relationship"), ("representative_states", "state")):
        for row in semantic.get(collection, []):
            ref = f"{kind}:{row['id']}"
            node("semantic", ref, row, {"owner": "current_semantic_source", "acceptance": "not_implied"})
            if kind == "resource":
                for field in row.get("fields", []):
                    field_id = field["id"]
                    node("semantic", "field:" + (field_id if "." in field_id else row["id"] + "." + field_id), field)
    for ref, paths in (source_map or {}).items():
        source = identities.get(("semantic", ref))
        if source:
            for path in paths:
                target = node("runtime_element", str(path), {"path": path}, {"proof": "declared_compilation_location"})
                edge(source, target, "compiled_to", {"authority": "retained_compiler_source_map"})
    for issue in change.get("issues", []):
        ref = str(issue["issue_id"])
        requirement = node("requirement", ref, {key: value for key, value in issue.items() if key not in {"status", "confidence", "priority"}},
            {"applicable_phases": issue.get("applicable_phases"), "verification_scope": issue.get("verification_scope"),
             "structural_status": issue.get("structural_status", "active")})
        for message in issue.get("source_message_ids", []):
            source = node("source_message", str(message), {"canonical_message_ref": message}, {"content_authority": "canonical_conversation"})
            edge(source, requirement, "derived_from", {"authority": "issue.source_message_ids"})
        resolved = 0
        for target_ref in issue.get("semantic_refs", []):
            target = identities.get(("semantic", target_ref))
            if target:
                edge(requirement, target, "implemented_by", {"authority": "issue.semantic_refs"})
                resolved += 1
            else:
                gaps.append({"issue_id": ref, "kind": "unresolved_semantic_ref", "ref": target_ref})
        if not resolved:
            gaps.append({"issue_id": ref, "kind": "semantic_mapping_missing"})
    for issue in change.get("issues", []):
        target = identities[("requirement", str(issue["issue_id"]))]
        for previous in issue.get("derived_from_issue_ids", []):
            source = identities.get(("requirement", previous))
            if source:
                edge(source, target, "superseded_by", {"authority": "issue_structure"})
    for constraint in change.get("acceptance_constraints", []):
        if constraint.get("status") == "superseded":
            continue
        ref = str(constraint["constraint_id"])
        target = node("verification_spec", ref, constraint, {"authority": "accepted_review", "proof": "expected_postcondition_only"})
        semantic_ref = constraint.get("target_ref")
        source = identities.get(("semantic", semantic_ref))
        if source:
            edge(source, target, "verified_by", {"postcondition": constraint.get("kind")})
        elif str(semantic_ref).startswith("widget:"):
            anchor = ".@" + semantic_ref.partition(":")[2]
            for ref, paths in (source_map or {}).items():
                source = identities.get(("semantic", ref))
                if source and any(path.endswith(anchor) or anchor + "." in path for path in paths):
                    edge(source, target, "verified_by", {"postcondition": constraint.get("kind"), "authority": "compiler_source_map"})
    for run in change.get("runs", []):
        node("execution", str(run["run_id"]), run, {"status": run.get("status"),
            "verification_claim": "authoring_status_is_not_behavioral_acceptance"})
    graph = build_graph("builder:" + digest(project_ref)[7:31], revision=max(1, generation), nodes=list(nodes.values()), edges=list(edges.values()))
    return {"schema": "adaos.builder.traceability_projection.v1", "project_ref": project_ref, "graph": graph,
            "gaps": gaps, "coverage_claim": "declared_links_only; no inferred behavioral acceptance"}


def evidence_impact(previous, current, receipts):
    """Keep immutable executions and explain compatibility against their pins."""
    from adaos.services.traceability import validate_graph
    validate_graph(previous)
    validate_graph(current)
    def unambiguous_versions(graph):
        candidates = {}
        for row in graph["nodes"]:
            candidates.setdefault(row["ref"], set()).add(row["digest"])
        # A receipt naming a bare ref cannot select between different kinds or
        # versions with the same ref. Do not silently accept the last node.
        return {ref: next(iter(values)) for ref, values in candidates.items() if len(values) == 1}
    previous_versions = unambiguous_versions(previous)
    versions = unambiguous_versions(current)
    outcomes = []
    for receipt in receipts:
        dependencies = receipt.get("dependencies") or {}
        causes = [ref for ref, expected in dependencies.items() if versions.get(ref) != expected]
        grounded = dependencies and all(previous_versions.get(ref) == expected for ref, expected in dependencies.items())
        outcome = "unqualified" if not grounded else "stale" if causes else "compatible"
        outcomes.append({"receipt_ref": receipt["receipt_ref"], "status": outcome, "dependency_causes": causes,
                         "original_execution_unchanged": True})
    return {"schema": "adaos.builder.evidence_impact.v1", "previous_graph": previous["digest"],
            "current_graph": current["digest"], "receipts": outcomes}
