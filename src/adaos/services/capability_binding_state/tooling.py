"""Bounded developer and admission tooling for the CBS architecture spine.

The helpers in this module are deliberately read-only.  They explain or
validate canonical records; none of them may provision state, resolve package
closures, or switch workspace authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from adaos.domain.artifact_release import WorkspaceLock, canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    ApplicationResolution,
    BindingDefinition,
    BindingDelivery,
    BindingInstance,
    CanonicalRecord,
    CapabilityContract,
    EvidenceClaim,
    LocalRevisionObservation,
    ResolutionPlan,
    StateContract,
    StateSpace,
)
from adaos.services.artifact_pipeline.attestations import (
    ArtifactAttestation,
    ArtifactTrustStore,
    verify_artifact_attestation,
)


PORTABLE_BUNDLE_PREDICATE = "https://adaos.dev/attestations/cbs-portable-bundle/v1"
_PORTABLE_TYPES = (CapabilityContract, StateContract, BindingDefinition, EvidenceClaim)


@dataclass(frozen=True, slots=True)
class TerminologyIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def lint_persistent_terminology(value: Any, *, root: str = "$") -> tuple[TerminologyIssue, ...]:
    """Flag the two legacy terms that are unsafe in new persistent records.

    Callers intentionally choose what is persistent.  WebUI's ephemeral
    ``state_ref`` remains valid and therefore WebUI documents are not passed to
    this check.
    """

    issues: list[TerminologyIssue] = []

    def visit(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key)
                child_path = f"{path}.{key}"
                if key == "state_ref":
                    issues.append(
                        TerminologyIssue(
                            "persistent_state_ref",
                            child_path,
                            "persistent data identity must use state_space_ref, not WebUI state_ref",
                        )
                    )
                if key == "capabilities":
                    issues.append(
                        TerminologyIssue(
                            "ambiguous_capabilities",
                            child_path,
                            "declare provided_capabilities or requested_permissions explicitly",
                        )
                    )
                visit(child, child_path)
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")

    visit(value, root)
    return tuple(issues)


def explicit_compatibility_edge(
    newer: CapabilityContract,
    older: CapabilityContract,
) -> dict[str, Any]:
    """Explain an explicit semantic edge without inferring it from SemVer."""

    compatibility = newer.to_dict()["compatibility"]
    backward = set(compatibility.get("backward_compatible_with") or ())
    supersedes = set(compatibility.get("supersedes") or ())
    same_identity_edge = (
        newer.capability_ref == older.capability_ref and older.version in backward
    )
    superseding_edge = older.capability_ref in supersedes
    return {
        "schema": "adaos.cbs.compatibility_edge.v1",
        "from": {
            "ref": older.capability_ref,
            "version": older.version,
            "digest": older.digest,
        },
        "to": {
            "ref": newer.capability_ref,
            "version": newer.version,
            "digest": newer.digest,
        },
        "compatible": same_identity_edge or superseding_edge,
        "basis": (
            "backward_compatible_with"
            if same_identity_edge
            else "supersedes"
            if superseding_edge
            else "no_explicit_edge"
        ),
    }


def portable_bundle_digest(records: Iterable[CanonicalRecord]) -> str:
    """Digest an exact, secret-free portable contract/evidence bundle."""

    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, _PORTABLE_TYPES) or not record.PORTABLE:
            raise ValueError(f"record is not admitted to a portable CBS bundle: {type(record).__name__}")
        value = record.to_dict()
        if isinstance(record, EvidenceClaim):
            redaction = value.get("redaction") or {}
            if redaction.get("portable") is not True or value.get("portability_scope") != "portable":
                raise ValueError("portable EvidenceClaim must declare portable redaction and scope")
        identity = (str(value["schema"]), record.digest)
        if identity in identities:
            raise ValueError(f"duplicate portable record: {identity[0]}@{identity[1]}")
        identities.add(identity)
        normalized.append(value)
    if not normalized:
        raise ValueError("portable CBS bundle must contain at least one record")
    normalized.sort(key=lambda item: (str(item["schema"]), _record_ref(item), _record_version(item)))
    return canonical_payload_digest(
        {"schema": "adaos.cbs.portable_bundle.v1", "records": normalized}
    )


def admit_portable_bundle(
    records: Iterable[CanonicalRecord],
    *,
    delivering_package_digest: str,
    project_id: str,
    trust_domain: str,
    attestation: ArtifactAttestation,
    trust_store: ArtifactTrustStore,
) -> dict[str, Any]:
    """Reuse package attestation policy to admit portable CBS records."""

    bundle_digest = portable_bundle_digest(records)
    verified = verify_artifact_attestation(
        attestation,
        trust_store=trust_store,
        expected_subject_kind="package",
        expected_subject_digest=delivering_package_digest,
        expected_project_id=project_id,
        expected_predicate_type=PORTABLE_BUNDLE_PREDICATE,
        expected_predicate_digest=bundle_digest,
        allowed_issuers=(trust_domain,),
    )
    return {
        "schema": "adaos.cbs.portable_bundle_admission.v1",
        "admitted": True,
        "trust_domain": trust_domain,
        "bundle_digest": bundle_digest,
        "package_digest": delivering_package_digest,
        "attestation": verified,
    }


def contract_reference(record: CapabilityContract | StateContract | BindingDefinition) -> dict[str, Any]:
    """Generate a stable human-readable reference projection from canonical data."""

    value = record.to_dict()
    if isinstance(record, CapabilityContract):
        summary = {
            "kind": "capability",
            "ref": record.capability_ref,
            "version": record.version,
            "digest": record.digest,
            "operations": [item["operation_id"] for item in value["operations"]],
            "state_ports": [
                {
                    "port_id": item["port_id"],
                    "contract_ref": item["contract_ref"],
                    "range": item["contract_range"],
                    "access": item["access"],
                }
                for item in value["state_ports"]
            ],
            "compatibility": value["compatibility"],
        }
    elif isinstance(record, StateContract):
        summary = {
            "kind": "state",
            "ref": record.state_contract_ref,
            "version": record.version,
            "digest": record.digest,
            "portability_class": value["portability_class"],
            "guarantees": value["guarantees"],
            "migration_compatibility": value["migration_compatibility"],
        }
    else:
        summary = {
            "kind": "binding",
            "ref": record.binding_definition_ref,
            "version": value["version"],
            "digest": record.digest,
            "capability_ref": record.capability_ref,
            "implementation_entrypoint": value["implementation_entrypoint"],
            "modes": value["modes"],
            "conformance_obligations": value["conformance_obligations"],
        }
    return {"schema": "adaos.cbs.contract_reference.v1", **summary}


def contract_diff(
    before: CapabilityContract | StateContract | BindingDefinition,
    after: CapabilityContract | StateContract | BindingDefinition,
) -> dict[str, Any]:
    """Return a deterministic field-level canonical contract diff."""

    left = before.to_dict()
    right = after.to_dict()
    ignored = {before.DIGEST_FIELD, after.DIGEST_FIELD}
    keys = sorted((set(left) | set(right)) - ignored)
    changes = [
        {"field": key, "before": left.get(key), "after": right.get(key)}
        for key in keys
        if left.get(key) != right.get(key)
    ]
    return {
        "schema": "adaos.cbs.contract_diff.v1",
        "before_digest": before.digest,
        "after_digest": after.digest,
        "changed": bool(changes),
        "changes": changes,
    }


def inspect_state_identity(
    state_space: StateSpace,
    *,
    binding_instance: BindingInstance | None = None,
    state_contract: StateContract | None = None,
    observations: Iterable[LocalRevisionObservation] = (),
) -> dict[str, Any]:
    """Build an operator projection while keeping authority records immutable."""

    state = state_space.to_dict()
    relevant = [
        item.to_dict()
        for item in observations
        if item.to_dict()["subject_kind"] == "state_space"
        and item.to_dict()["subject_ref"] == state_space.stable_ref
        and item.to_dict()["subject_revision_digest"] == state_space.digest
    ]
    relevant.sort(key=lambda item: (item["observed_at"], item["observation_digest"]))
    latest: dict[str, dict[str, Any]] = {}
    for item in relevant:
        latest[item["observation_kind"]] = item
    result: dict[str, Any] = {
        "schema": "adaos.cbs.state_identity_inspection.v1",
        "state_space_ref": state_space.stable_ref,
        "revision": state_space.revision,
        "revision_digest": state_space.digest,
        "state_contract_ref": state["state_contract_ref"],
        "state_contract_version": state["state_contract_version"],
        "logical_owner_ref": state["logical_owner_ref"],
        "lifecycle_authority_ref": state["lifecycle_authority_ref"],
        "custodian_binding_instance_ref": state["custodian_binding_instance_ref"],
        "mutation_authority_ref": state["mutation_authority_ref"],
        "generation": state["generation"],
        "authority_epoch": state["authority_epoch"],
        "portability_class": state["portability_class"],
        "locator_ref": state["locator_ref"],
        "observations": latest,
    }
    if binding_instance is not None:
        binding = binding_instance.to_dict()
        if binding_instance.stable_ref != state["custodian_binding_instance_ref"]:
            raise ValueError("BindingInstance is not the StateSpace custodian")
        result["binding"] = {
            "binding_instance_ref": binding_instance.stable_ref,
            "revision": binding_instance.revision,
            "revision_digest": binding_instance.digest,
            "binding_definition_ref": binding["binding_definition_ref"],
            "mode": binding["mode"],
            "authority_epoch": binding["authority_epoch"],
        }
    if state_contract is not None:
        if state_contract.digest != state["state_contract_digest"]:
            raise ValueError("StateContract does not match StateSpace revision")
        result["contract"] = contract_reference(state_contract)
    return result


def build_identity_map(
    *,
    requirement: ApplicationRequirement,
    capability_contract: CapabilityContract,
    state_contracts: Iterable[StateContract],
    binding_definition: BindingDefinition | None = None,
    delivery: BindingDelivery | None = None,
    binding_instances: Iterable[BindingInstance] = (),
    state_spaces: Iterable[StateSpace] = (),
    resolution: ApplicationResolution | None = None,
    plan: ResolutionPlan | None = None,
    workspace_lock: WorkspaceLock | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain the orthogonal identities currently joined for one install."""

    bindings = tuple(binding_instances)
    spaces = tuple(state_spaces)
    lock_value = (
        workspace_lock.to_dict()
        if isinstance(workspace_lock, WorkspaceLock)
        else dict(workspace_lock)
        if isinstance(workspace_lock, Mapping)
        else None
    )
    value: dict[str, Any] = {
        "schema": "adaos.cbs.identity_map.v1",
        "requirement": {"ref": requirement.requirement_ref, "digest": requirement.digest},
        "capability_contract": {
            "ref": capability_contract.capability_ref,
            "version": capability_contract.version,
            "digest": capability_contract.digest,
        },
        "state_contracts": [
            {"ref": item.state_contract_ref, "version": item.version, "digest": item.digest}
            for item in sorted(state_contracts, key=lambda item: (item.state_contract_ref, item.version))
        ],
        "binding_instances": [
            {"ref": item.stable_ref, "revision": item.revision, "digest": item.digest}
            for item in sorted(bindings, key=lambda item: (item.stable_ref, item.revision))
        ],
        "state_spaces": [
            {
                "ref": item.stable_ref,
                "revision": item.revision,
                "digest": item.digest,
                "generation": item.generation,
            }
            for item in sorted(spaces, key=lambda item: (item.stable_ref, item.revision))
        ],
    }
    if binding_definition is not None:
        value["binding_definition"] = {
            "ref": binding_definition.binding_definition_ref,
            "digest": binding_definition.digest,
        }
    if delivery is not None:
        delivery_value = delivery.to_dict()
        value["delivery"] = {
            "digest": delivery.digest,
            "package": delivery_value["package"],
            "physical_member": delivery_value["physical_member"],
        }
    if resolution is not None:
        value["application_resolution"] = {
            "ref": resolution.resolution_ref,
            "digest": resolution.digest,
        }
    if plan is not None:
        value["resolution_plan"] = {"ref": plan.plan_ref, "digest": plan.digest}
    if lock_value is not None:
        value["workspace_lock"] = {
            "revision": lock_value.get("lock_revision"),
            "digest": lock_value.get("lock_digest"),
        }
    value["map_digest"] = canonical_payload_digest(value)
    return value


def _record_ref(value: Mapping[str, Any]) -> str:
    for key in (
        "capability_ref",
        "state_contract_ref",
        "binding_definition_ref",
        "claim_ref",
    ):
        if key in value:
            return str(value[key])
    return ""


def _record_version(value: Mapping[str, Any]) -> str:
    return str(value.get("version") or "")


__all__ = [
    "PORTABLE_BUNDLE_PREDICATE",
    "TerminologyIssue",
    "admit_portable_bundle",
    "build_identity_map",
    "contract_diff",
    "contract_reference",
    "explicit_compatibility_edge",
    "inspect_state_identity",
    "lint_persistent_terminology",
    "portable_bundle_digest",
]
