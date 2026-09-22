"""Disposable Semantic, Impact, Viability, and Evolver projections.

Canonical CBS records remain the only architecture truth.  Every value emitted
here can be deleted and rebuilt from those records.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


_REF_FIELDS = (
    "capability_ref",
    "state_contract_ref",
    "binding_definition_ref",
    "claim_ref",
    "assessment_ref",
    "profile_ref",
    "requirement_ref",
    "binding_instance_ref",
    "state_space_ref",
    "relation_ref",
    "resolution_ref",
    "plan_ref",
    "observation_ref",
)
_PRIMARY_REF_BY_SCHEMA = {
    "adaos.capability.contract.v1": "capability_ref",
    "adaos.state.contract.v1": "state_contract_ref",
    "adaos.binding.definition.v1": "binding_definition_ref",
    "adaos.evidence.claim.v1": "claim_ref",
    "adaos.evidence.assessment.v1": "assessment_ref",
    "adaos.external_dependency.observation.v1": "observation_ref",
    "adaos.environment.profile.v1": "profile_ref",
    "adaos.application.requirement.v1": "requirement_ref",
    "adaos.binding.instance.v1": "binding_instance_ref",
    "adaos.state.space.v1": "state_space_ref",
    "adaos.state.access_relation.v1": "relation_ref",
    "adaos.state.lifecycle_operation.v1": "operation_ref",
    "adaos.local_revision.observation.v1": "observation_ref",
    "adaos.application.resolution.v1": "resolution_ref",
    "adaos.resolution.plan.v1": "plan_ref",
}
_DIGEST_FIELDS = (
    "contract_digest",
    "definition_digest",
    "delivery_digest",
    "claim_digest",
    "assessment_digest",
    "profile_digest",
    "requirement_digest",
    "revision_digest",
    "relation_digest",
    "resolution_digest",
    "plan_digest",
    "observation_digest",
    "lock_digest",
    "digest",
)
_DERIVED_SCHEMAS = {
    "adaos.semantic_graph.v1",
    "adaos.impact_projection.v1",
    "adaos.viability_projection.v1",
    "adaos.evolver.observations.v1",
    "adaos.evolver.proposal.v1",
}


class DerivedGraphError(ValueError):
    pass


def _mapping(record: Any) -> dict[str, Any]:
    value = record.to_dict() if hasattr(record, "to_dict") else record
    if not isinstance(value, Mapping):
        raise DerivedGraphError("graph sources must be canonical object records")
    result = dict(value)
    if not str(result.get("schema") or "").strip():
        raise DerivedGraphError("graph source record is missing schema")
    if result["schema"] in _DERIVED_SCHEMAS:
        raise DerivedGraphError("derived projections cannot become graph authority")
    return result


def _identity(value: Mapping[str, Any]) -> str:
    primary = _PRIMARY_REF_BY_SCHEMA.get(str(value["schema"]))
    if primary and value.get(primary):
        return str(value[primary])
    for field in _REF_FIELDS:
        if value.get(field):
            return str(value[field])
    schema = str(value["schema"])
    if schema in {"adaos.workspace.lock.v1", "adaos.workspace.lock.v2"}:
        return f"workspace-lock:{value.get('lock_revision')}"
    for field in _DIGEST_FIELDS:
        if value.get(field):
            return str(value[field])
    return canonical_payload_digest(dict(value))


def _digest(value: Mapping[str, Any]) -> str:
    for field in _DIGEST_FIELDS:
        token = str(value.get(field) or "")
        if token.startswith("sha256:"):
            return token
    return canonical_payload_digest(dict(value))


def _node(value: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity(value)
    digest = _digest(value)
    attributes: dict[str, Any] = {}
    for field in (
        "version",
        "capability_version",
        "portability_class",
        "mode",
        "revision",
        "generation",
        "authority_epoch",
        "status",
        "result",
        "claim_kind",
        "profile_class",
        "lock_revision",
    ):
        if field in value:
            attributes[field] = value[field]
    return {
        "node_id": f"{identity}@{digest}",
        "schema": value["schema"],
        "stable_ref": identity,
        "digest": digest,
        "attributes": attributes,
    }


def _refs(items: Any, *fields: str) -> list[str]:
    if not isinstance(items, (list, tuple)):
        return []
    result: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for field in fields:
            token = str(item.get(field) or "").strip()
            if token:
                result.append(token)
                break
    return result


def _record_edges(value: Mapping[str, Any]) -> list[tuple[str, str]]:
    schema = str(value["schema"])
    edges: list[tuple[str, str]] = []

    def one(relation: str, target: Any) -> None:
        token = str(target or "").strip()
        if token:
            edges.append((relation, token))

    def many(relation: str, values: Iterable[str]) -> None:
        edges.extend((relation, token) for token in values if token)

    if schema == "adaos.capability.contract.v1":
        many(
            "depends_on_capability", _refs(value.get("dependencies"), "capability_ref")
        )
        many(
            "uses_state_contract",
            _refs(value.get("state_ports"), "state_contract_ref", "contract_ref"),
        )
    elif schema == "adaos.binding.definition.v1":
        one("implements", value.get("capability_ref"))
        many(
            "supports_state_contract",
            _refs(value.get("state_support"), "state_contract_ref", "contract_ref"),
        )
    elif schema == "adaos.binding.delivery.v1":
        one("delivers", value.get("binding_definition_ref"))
        package = value.get("package")
        if isinstance(package, Mapping):
            one("packaged_by", package.get("digest"))
    elif schema == "adaos.application.requirement.v1":
        one("requires", value.get("capability_ref"))
    elif schema == "adaos.binding.instance.v1":
        one("materializes", value.get("binding_definition_ref"))
        one("uses_delivery", value.get("delivery_digest"))
        one("runs_in", value.get("environment_profile_ref"))
    elif schema == "adaos.state.space.v1":
        one("conforms_to", value.get("state_contract_ref"))
        one("custodied_by", value.get("custodian_binding_instance_ref"))
    elif schema == "adaos.state.access_relation.v1":
        one("grants_binding", value.get("binding_instance_ref"))
        one("attaches_state", value.get("state_space_ref"))
    elif schema == "adaos.evidence.claim.v1":
        many("supports_subject", _refs(value.get("subjects"), "ref", "digest"))
        environment = value.get("environment")
        if isinstance(environment, Mapping):
            one(
                "scoped_to",
                environment.get("profile_ref") or environment.get("profile_digest"),
            )
        many("observes_dependency", _refs(value.get("dependencies"), "ref"))
    elif schema == "adaos.evidence.assessment.v1":
        one("assesses", value.get("claim_ref") or value.get("claim_digest"))
    elif schema == "adaos.application.resolution.v1":
        one("resolves", value.get("requirement_ref"))
        one(
            "selects_binding",
            (value.get("binding_definition") or {}).get("ref")
            if isinstance(value.get("binding_definition"), Mapping)
            else None,
        )
        many(
            "selects_contract", _refs(value.get("selected_contracts"), "ref", "digest")
        )
        many("selects_package", _refs(value.get("package_closure"), "digest", "ref"))
        many(
            "materializes_instance",
            _refs(value.get("binding_instances"), "ref", "binding_instance_ref"),
        )
        many(
            "attaches_state",
            _refs(value.get("state_attachments"), "state_space_ref", "ref"),
        )
        many(
            "relies_on_evidence",
            _refs(value.get("evidence"), "claim_ref", "ref", "claim_digest"),
        )
    elif schema == "adaos.resolution.plan.v1":
        one("activates", value.get("application_resolution_ref"))
        many(
            "desires_binding",
            _refs(value.get("desired_bindings"), "binding_instance_ref", "ref"),
        )
        many(
            "desires_state",
            _refs(value.get("desired_state_attachments"), "state_space_ref", "ref"),
        )
        many(
            "requires_evidence",
            _refs(value.get("evidence"), "claim_ref", "ref", "claim_digest"),
        )
    elif schema == "adaos.workspace.lock.v2":
        cbs = value.get("cbs")
        if isinstance(cbs, Mapping):
            one("pins_resolution", cbs.get("application_resolution_ref"))
            one("commits_plan", cbs.get("resolution_plan_ref"))
            many(
                "pins_binding",
                _refs(cbs.get("binding_instances"), "binding_instance_ref", "ref"),
            )
            many("pins_state", _refs(cbs.get("state_spaces"), "state_space_ref", "ref"))
    return edges


def build_semantic_graph(records: Iterable[Any]) -> dict[str, Any]:
    """Build a deterministic, redacted materialized view."""

    sources = [_mapping(record) for record in records]
    sources.sort(
        key=lambda value: (str(value["schema"]), _identity(value), _digest(value))
    )
    nodes = [_node(value) for value in sources]
    known: dict[str, str] = {}
    for node in nodes:
        known[node["stable_ref"]] = node["node_id"]
        known[node["digest"]] = node["node_id"]
    edges: list[dict[str, Any]] = []
    external_nodes: dict[str, dict[str, Any]] = {}
    for value, source in zip(sources, nodes, strict=True):
        for relation, target_ref in _record_edges(value):
            target_id = known.get(target_ref)
            if target_id is None:
                target_id = f"external:{target_ref}"
                external_nodes.setdefault(
                    target_id,
                    {
                        "node_id": target_id,
                        "schema": "adaos.semantic_graph.external_ref.v1",
                        "stable_ref": target_ref,
                        "digest": None,
                        "attributes": {},
                    },
                )
            edges.append(
                {
                    "source": source["node_id"],
                    "target": target_id,
                    "relation": relation,
                    "provenance": {
                        "source_schema": value["schema"],
                        "source_digest": source["digest"],
                    },
                }
            )
    nodes.extend(external_nodes.values())
    nodes.sort(key=lambda item: item["node_id"])
    edges.sort(key=lambda item: (item["source"], item["relation"], item["target"]))
    source_manifest = [
        {
            "schema": value["schema"],
            "identity": _identity(value),
            "digest": _digest(value),
        }
        for value in sources
    ]
    body = {
        "schema": "adaos.semantic_graph.v1",
        "source_manifest_digest": canonical_payload_digest(source_manifest),
        "nodes": nodes,
        "edges": edges,
    }
    return {**body, "projection_digest": canonical_payload_digest(body)}


def build_impact_projection(
    graph: Mapping[str, Any], changed_subjects: Iterable[str]
) -> dict[str, Any]:
    """Walk reverse dependency edges from changed refs or digests."""

    changed = sorted({str(item) for item in changed_subjects if str(item).strip()})
    node_by_id = {str(node["node_id"]): node for node in graph.get("nodes") or []}
    frontier = {
        node_id
        for node_id, node in node_by_id.items()
        if node.get("stable_ref") in changed or node.get("digest") in changed
    }
    reverse: dict[str, set[str]] = {}
    relation_by_pair: dict[tuple[str, str], set[str]] = {}
    for edge in graph.get("edges") or []:
        source, target = str(edge["source"]), str(edge["target"])
        reverse.setdefault(target, set()).add(source)
        relation_by_pair.setdefault((target, source), set()).add(str(edge["relation"]))
    distance = {node_id: 0 for node_id in frontier}
    queue = sorted(frontier)
    while queue:
        target = queue.pop(0)
        for source in sorted(reverse.get(target, ())):
            if source in distance:
                continue
            distance[source] = distance[target] + 1
            queue.append(source)
    affected = [
        {
            "node_id": node_id,
            "stable_ref": node_by_id[node_id].get("stable_ref"),
            "schema": node_by_id[node_id].get("schema"),
            "distance": distance[node_id],
            "via_relations": sorted(
                {
                    relation
                    for (target, source), relations in relation_by_pair.items()
                    if source == node_id
                    and target in distance
                    and distance[target] < distance[source]
                    for relation in relations
                }
            ),
        }
        for node_id in sorted(distance, key=lambda item: (distance[item], item))
    ]
    body = {
        "schema": "adaos.impact_projection.v1",
        "semantic_graph_digest": graph.get("projection_digest"),
        "changed_subjects": changed,
        "affected": affected,
    }
    return {**body, "projection_digest": canonical_payload_digest(body)}


def build_viability_projection(records: Iterable[Any]) -> dict[str, Any]:
    """Summarize whether resolutions are currently admissible without acting."""

    sources = [_mapping(record) for record in records]
    assessments: dict[str, str] = {}
    for value in sources:
        if value["schema"] == "adaos.evidence.assessment.v1":
            status = str(value.get("status") or "unknown")
            for key in (value.get("claim_ref"), value.get("claim_digest")):
                if key:
                    assessments[str(key)] = status
    resolutions: list[dict[str, Any]] = []
    for value in sources:
        if value["schema"] != "adaos.application.resolution.v1":
            continue
        evidence_refs = _refs(value.get("evidence"), "claim_ref", "ref", "claim_digest")
        statuses = {ref: assessments.get(ref, "missing") for ref in evidence_refs}
        rejections = value.get("rejection_explanations") or []
        obligations = value.get("provisioning_obligations") or []
        blocking_evidence = sorted(
            ref for ref, status in statuses.items() if status != "admissible"
        )
        blockers: list[str] = []
        if rejections:
            blockers.append("resolution_rejections")
        if blocking_evidence:
            blockers.append("evidence_not_admissible")
        if any(
            isinstance(item, Mapping) and item.get("status") in {"failed", "blocked"}
            for item in obligations
        ):
            blockers.append("provisioning_blocked")
        resolutions.append(
            {
                "resolution_ref": value.get("resolution_ref"),
                "resolution_digest": _digest(value),
                "status": "viable" if not blockers else "not_viable",
                "blockers": blockers,
                "evidence_status": dict(sorted(statuses.items())),
            }
        )
    resolutions.sort(key=lambda item: str(item["resolution_ref"]))
    body = {"schema": "adaos.viability_projection.v1", "resolutions": resolutions}
    return {**body, "projection_digest": canonical_payload_digest(body)}


def build_evolver_observations(records: Iterable[Any]) -> dict[str, Any]:
    """Derive read-only opportunities and risks; never issue authority changes."""

    sources = [_mapping(record) for record in records]
    observations: list[dict[str, Any]] = []
    operations: dict[str, set[str]] = {}
    for value in sources:
        schema = str(value["schema"])
        if schema == "adaos.capability.contract.v1":
            for operation in value.get("operations") or []:
                if isinstance(operation, Mapping) and operation.get("operation_id"):
                    operations.setdefault(str(operation["operation_id"]), set()).add(
                        str(value.get("capability_ref"))
                    )
        elif (
            schema == "adaos.evidence.assessment.v1"
            and value.get("status") != "admissible"
        ):
            observations.append(
                {
                    "kind": "evidence_risk",
                    "subject_ref": value.get("claim_ref"),
                    "status": value.get("status"),
                    "source_digest": _digest(value),
                }
            )
        elif schema == "adaos.resolution.plan.v1":
            failed = [
                str(item.get("id") or item.get("operation") or "unknown")
                for item in (value.get("migrations") or [])
                if isinstance(item, Mapping)
                and item.get("status") in {"failed", "blocked"}
            ]
            if failed:
                observations.append(
                    {
                        "kind": "migration_failure",
                        "subject_ref": value.get("plan_ref"),
                        "failed": sorted(failed),
                        "source_digest": _digest(value),
                    }
                )
    for operation, contracts in operations.items():
        if len(contracts) > 1:
            observations.append(
                {
                    "kind": "operation_overlap",
                    "operation_id": operation,
                    "capability_refs": sorted(contracts),
                }
            )
    observations.sort(
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
    )
    body = {
        "schema": "adaos.evolver.observations.v1",
        "observations": observations,
        "authority": "none",
    }
    return {**body, "projection_digest": canonical_payload_digest(body)}


def create_evolver_proposal(
    *, proposal_ref: str, observation_refs: Iterable[str], summary: str
) -> dict[str, Any]:
    body = {
        "schema": "adaos.evolver.proposal.v1",
        "proposal_ref": str(proposal_ref),
        "maturity": "application_local",
        "observation_refs": sorted({str(item) for item in observation_refs}),
        "summary": str(summary),
        "authority": "advisory_only",
        "promotion_evidence": {},
    }
    return {**body, "proposal_digest": canonical_payload_digest(body)}


def promote_evolver_proposal(
    proposal: Mapping[str, Any], *, target: str, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    order = ("application_local", "candidate", "reusable", "platform")
    current = str(proposal.get("maturity") or "")
    if (
        current not in order
        or target not in order
        or order.index(target) != order.index(current) + 1
    ):
        raise DerivedGraphError(
            "proposal promotion must advance exactly one maturity stage"
        )
    required = {
        "candidate": ("review_ref",),
        "reusable": (
            "package_digest",
            "conformance_evidence_digest",
            "independent_consumer_refs",
        ),
        "platform": ("governance_decision_ref", "platform_evidence_digest"),
    }[target]
    missing = [field for field in required if not evidence.get(field)]
    if missing:
        raise DerivedGraphError("promotion evidence is missing: " + ", ".join(missing))
    if target == "reusable" and len(set(evidence["independent_consumer_refs"])) < 2:
        raise DerivedGraphError("reusable proposals require two independent consumers")
    body = {key: value for key, value in proposal.items() if key != "proposal_digest"}
    body["maturity"] = target
    body["promotion_evidence"] = dict(evidence)
    body["authority"] = "advisory_only"
    return {**body, "proposal_digest": canonical_payload_digest(body)}


class DerivedGraphStore:
    """Replaceable storage for the current derived projection."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def projection_path(self) -> Path:
        return self.root / "semantic-graph.json"

    def rebuild(self, records: Iterable[Any]) -> dict[str, Any]:
        projection = build_semantic_graph(records)
        with mutation_lock(self.root / ".graph.lock"):
            atomic_write_json(self.projection_path, projection)
        return projection

    def load(self) -> dict[str, Any]:
        return json.loads(self.projection_path.read_text(encoding="utf-8"))

    def delete(self) -> None:
        with mutation_lock(self.root / ".graph.lock"):
            self.projection_path.unlink(missing_ok=True)


__all__ = [
    "DerivedGraphError",
    "DerivedGraphStore",
    "build_evolver_observations",
    "build_impact_projection",
    "build_semantic_graph",
    "build_viability_projection",
    "create_evolver_proposal",
    "promote_evolver_proposal",
]
