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


BEHAVIORAL_AXES = ('implementation', 'runtime', 'state', 'environment')


def project_change(*, project_ref, generation, change, semantic_document=None, source_map=None,
                   behavioral_context=None):
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
        for identity, text in zip(issue.get('source_criterion_ids', []), issue.get('source_acceptance_criteria', [])):
            source = node('source_criterion', identity, {'text': text, 'source_issue_id': issue['issue_id']},
                          {'proof': 'canonical_source_only; behavioral_acceptance_not_implied'})
            edge(source, target, 'derived_from', {'authority': 'lossless_issue_partition'})
        for previous in issue.get("derived_from_issue_ids", []):
            source = identities.get(("requirement", previous))
            if source:
                edge(source, target, "superseded_by", {"authority": "issue_structure"})
    for issue in change.get('issues', []):
        target = identities[('requirement', str(issue['issue_id']))]
        for criterion in issue.get('criterion_ids', []):
            source = identities.get(('source_criterion', criterion))
            if source:
                edge(source, target, 'derived_from', {'authority': 'lossless_issue_partition',
                     'proof': 'declared_obligation; no inferred behavioral acceptance'})
            else:
                gaps.append({'issue_id': issue['issue_id'], 'kind': 'unresolved_source_criterion', 'ref': criterion})
    for constraint in change.get("acceptance_constraints", []):
        if constraint.get("status") == "superseded":
            continue
        ref = str(constraint["constraint_id"])
        target = node("verification_spec", ref, constraint, {"authority": constraint.get("authority", "accepted_review"), "proof": "expected_postcondition_only"})
        for requirement_ref in constraint.get('requirement_refs', []):
            requirement = identities.get(('requirement', requirement_ref))
            if requirement:
                edge(requirement, target, 'verified_by', {'authority': 'explicit_constraint_requirement_refs'})
            else:
                gaps.append({'constraint_id': ref, 'kind': 'unresolved_requirement_ref', 'ref': requirement_ref})
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
    for axis in BEHAVIORAL_AXES:
        value = (behavioral_context or {}).get(axis)
        if value is not None:
            node('behavioral_dependency', 'behavior:' + axis, value,
                 {'axis': axis, 'authority': 'execution_owner_snapshot'})
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
            candidates.setdefault(row["ref"], set()).add((row['kind'], row["digest"]))
        # A receipt naming a bare ref cannot select between different kinds or
        # versions with the same ref. Do not silently accept the last node.
        return {ref: next(iter(values))[1] for ref, values in candidates.items() if len(values) == 1}
    previous_versions = unambiguous_versions(previous)
    versions = unambiguous_versions(current)
    outcomes = []
    for receipt in receipts:
        dependencies = receipt.get("dependencies") or {}
        causes = [ref for ref, expected in dependencies.items() if versions.get(ref) != expected]
        grounded = dependencies and all(previous_versions.get(ref) == expected for ref, expected in dependencies.items())
        behavioral = receipt.get('kind') == 'behavioral'
        missing = []
        if behavioral:
            required = {'behavior:' + axis for axis in BEHAVIORAL_AXES}
            spec_refs = set(receipt.get('verification_refs') or [])
            valid_specs = {row['ref'] for row in previous['nodes'] if row['kind'] == 'verification_spec'}
            missing = sorted((required | spec_refs) - dependencies.keys())
            body = {key: value for key, value in receipt.items() if key not in {'receipt_ref','receipt_digest'}}
            observations = receipt.get('observations') or {}
            valid_observations = all(isinstance(observations.get(ref), dict)
                and isinstance(observations[ref].get('passed'), bool)
                and observations[ref].get('evidence_ref') for ref in spec_refs)
            observed_outcome = 'passed' if valid_observations and all(observations[ref]['passed'] for ref in spec_refs) else 'failed'
            grounded = (grounded and not missing and bool(spec_refs) and spec_refs <= valid_specs
                        and valid_observations and receipt.get('outcome') == observed_outcome
                        and receipt.get('receipt_digest') == digest(body)
                        and receipt.get('receipt_ref') == 'behavioral:' + digest(body)[7:])
        outcome = "unqualified" if not grounded else "stale" if causes else "compatible"
        outcomes.append({"receipt_ref": receipt["receipt_ref"], "status": outcome, "dependency_causes": causes,
                         "original_execution_unchanged": True, 'missing_dependency_refs': missing,
                         'reuse_admissible': bool(behavioral and outcome == 'compatible' and receipt.get('outcome') == 'passed')})
    return {"schema": "adaos.builder.evidence_impact.v1", "previous_graph": previous["digest"],
            "current_graph": current["digest"], "receipts": outcomes}


def make_behavioral_receipt(graph, *, verification_refs, execution_ref, observations):
    """Bind owner-supplied observations to every required execution dependency.

    This records a bounded behavioral proof, not an authoring status. The
    execution owner must supply the actual runtime/state/environment snapshots.
    """
    from adaos.services.traceability import validate_graph
    validate_graph(graph)
    refs = list(dict.fromkeys(verification_refs))
    if not refs or not execution_ref or any(ref not in observations for ref in refs):
        raise ValueError('Behavioral execution requires observed preregistered verification specs')
    nodes = {row['ref']: row for row in graph['nodes']}
    required = ['behavior:' + axis for axis in BEHAVIORAL_AXES]
    if any(ref not in nodes for ref in required) or any(nodes.get(ref, {}).get('kind') != 'verification_spec' for ref in refs):
        raise ValueError('Behavioral dependency closure is incomplete')
    for ref in refs:
        if not isinstance(observations[ref].get('passed'), bool) or not observations[ref].get('evidence_ref'):
            raise ValueError('Each behavioral observation requires a verdict and retained evidence')
    body = {'schema': 'adaos.builder.behavioral_receipt.v1', 'kind': 'behavioral',
        'execution_ref': execution_ref, 'verification_refs': refs,
        'dependencies': {ref: nodes[ref]['digest'] for ref in [*required, *refs]},
        'observations': copy.deepcopy(observations),
        'outcome': 'passed' if all(observations[ref]['passed'] for ref in refs) else 'failed'}
    return {**body, 'receipt_ref': 'behavioral:' + digest(body)[7:], 'receipt_digest': digest(body)}


def attach_behavioral_execution(graph, receipt):
    """Extend the graph with immutable spec → execution → observed acceptance."""
    impact = evidence_impact(graph, graph, [receipt])['receipts'][0]
    if receipt.get('kind') != 'behavioral' or impact['status'] != 'compatible':
        raise ValueError('Behavioral receipt is not grounded in this execution context')
    nodes, edges = copy.deepcopy(graph['nodes']), copy.deepcopy(graph['edges'])
    execution_id = 'execution:' + receipt['receipt_digest'][7:]
    if any(row['node_id'] == execution_id for row in nodes):
        return copy.deepcopy(graph)
    nodes.append({'node_id': execution_id, 'kind': 'behavioral_execution',
        'ref': receipt['execution_ref'], 'digest': receipt['receipt_digest'],
        'metadata': {'receipt_ref': receipt['receipt_ref'], 'outcome': receipt['outcome']}})
    for row in graph['nodes']:
        if row['ref'] in receipt['verification_refs']:
            edges.append({'edge_id': 'edge:' + digest([row['node_id'],execution_id])[7:],
                'source': row['node_id'], 'target': execution_id, 'relation': 'executed_by'})
    if impact['reuse_admissible']:
        acceptance_id = 'acceptance:' + receipt['receipt_digest'][7:]
        nodes.append({'node_id': acceptance_id, 'kind': 'behavioral_acceptance',
            'ref': receipt['receipt_ref'], 'digest': receipt['receipt_digest'],
            'metadata': {'scope': 'exact_observed_postconditions', 'publication_acceptance': 'not_implied'}})
        edges.append({'edge_id': 'edge:' + digest([execution_id, acceptance_id])[7:],
            'source': execution_id, 'target': acceptance_id, 'relation': 'accepted_by'})
    return build_graph(graph['graph_id'], revision=graph['revision'] + 1, nodes=nodes, edges=edges)
