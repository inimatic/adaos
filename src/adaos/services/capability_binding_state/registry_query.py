"""Read-only, snapshot-pinned discovery over the shared semantic registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from packaging.version import Version
from referencing import Registry, Resource

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    BINDING_DEFINITION_SCHEMA,
    BINDING_DELIVERY_SCHEMA,
    CAPABILITY_CONTRACT_SCHEMA,
    EVIDENCE_CLAIM_SCHEMA,
    STATE_CONTRACT_SCHEMA,
    ApplicationRequirement,
    BindingDefinition,
    BindingDelivery,
    CanonicalRecord,
    CapabilityContract,
    EnvironmentProfile,
    EvidenceClaim,
    StateContract,
    version_satisfies,
)

from .catalog import portable_record_identity


SEMANTIC_REGISTRY_QUERY_SCHEMA = "adaos.semantic_registry.query.v1"
SEMANTIC_REGISTRY_QUERY_RESULT_SCHEMA = "adaos.semantic_registry.query_result.v1"

_MODEL_BY_SCHEMA: dict[str, type[CanonicalRecord]] = {
    CAPABILITY_CONTRACT_SCHEMA: CapabilityContract,
    STATE_CONTRACT_SCHEMA: StateContract,
    BINDING_DEFINITION_SCHEMA: BindingDefinition,
    BINDING_DELIVERY_SCHEMA: BindingDelivery,
    EVIDENCE_CLAIM_SCHEMA: EvidenceClaim,
}


class SemanticRegistryQueryError(ValueError):
    """A registry query is malformed, stale, or references corrupt content."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _abi_path(filename: str) -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / filename


def _read_schema(filename: str) -> dict[str, Any]:
    value = json.loads(_abi_path(filename).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise SemanticRegistryQueryError("schema_invalid", f"{filename} is not an object")
    return dict(value)


@lru_cache(maxsize=2)
def _validator(kind: str) -> Draft202012Validator:
    if kind == "query":
        query = _read_schema("semantic_registry.query.v1.schema.json")
        requirement = _read_schema("application.requirement.v1.schema.json")
        profile = _read_schema("environment.profile.v1.schema.json")
        registry = Registry().with_resources(
            (
                (str(requirement["$id"]), Resource.from_contents(requirement)),
                (str(profile["$id"]), Resource.from_contents(profile)),
            )
        )
        return Draft202012Validator(
            query,
            registry=registry,
            format_checker=FormatChecker(),
        )
    if kind == "result":
        return Draft202012Validator(
            _read_schema("semantic_registry.query_result.v1.schema.json"),
            format_checker=FormatChecker(),
        )
    raise AssertionError(kind)


def _validate(kind: str, value: Mapping[str, Any]) -> None:
    errors = sorted(
        _validator(kind).iter_errors(dict(value)),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(item) for item in first.absolute_path)
    suffix = f" at {location}" if location else ""
    raise SemanticRegistryQueryError(
        f"{kind}_invalid",
        f"invalid semantic registry {kind}{suffix}: {first.message}",
    )


def _instant(value: Any, *, field: str) -> datetime:
    token = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SemanticRegistryQueryError(
            "query_invalid", f"{field} must be an RFC3339 instant"
        ) from exc
    if parsed.tzinfo is None:
        raise SemanticRegistryQueryError(
            "query_invalid", f"{field} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _record_ref(record: CapabilityContract | StateContract) -> dict[str, str]:
    value = record.to_dict()
    stable_ref = str(value.get("capability_ref") or value.get("state_contract_ref") or "")
    return {
        "ref": stable_ref,
        "version": str(value["version"]),
        "digest": record.digest,
    }


def _binding_ref(record: BindingDefinition) -> dict[str, str]:
    value = record.to_dict()
    return {
        "ref": record.binding_definition_ref,
        "version": str(value["version"]),
        "digest": record.digest,
    }


def _delivery_ref(record: BindingDelivery) -> dict[str, Any]:
    value = record.to_dict()
    return {
        "digest": record.digest,
        "binding_definition_digest": record.binding_definition_digest,
        "logical_entrypoint": str(value["logical_entrypoint"]),
        "package": dict(value["package"]),
    }


def _rejection(
    code: str,
    message: str,
    *,
    subject_ref: str | None = None,
    subject_digest: str | None = None,
) -> dict[str, str]:
    value = {"code": str(code), "message": str(message)}
    if subject_ref:
        value["subject_ref"] = str(subject_ref)
    if subject_digest:
        value["subject_digest"] = str(subject_digest)
    return value


def _dedupe_rejections(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        row = dict(value)
        key = canonical_payload_digest(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return sorted(
        result,
        key=lambda item: (
            str(item.get("code") or ""),
            str(item.get("subject_ref") or ""),
            str(item.get("subject_digest") or ""),
            str(item.get("message") or ""),
        ),
    )


@dataclass(frozen=True, slots=True)
class _RegistryRecords:
    capabilities: tuple[CapabilityContract, ...]
    states: tuple[StateContract, ...]
    bindings: tuple[BindingDefinition, ...]
    deliveries: tuple[BindingDelivery, ...]
    evidence: tuple[EvidenceClaim, ...]


def _load_records(
    registry_root: Path,
    index: Mapping[str, Any],
) -> tuple[_RegistryRecords, dict[str, dict[str, Any]]]:
    root = Path(registry_root).resolve()
    by_schema: dict[str, list[CanonicalRecord]] = {
        schema: [] for schema in _MODEL_BY_SCHEMA
    }
    entries: dict[str, dict[str, Any]] = {}
    for digest, raw_entry in sorted(dict(index.get("records") or {}).items()):
        if not isinstance(raw_entry, Mapping):
            raise SemanticRegistryQueryError(
                "registry_invalid", f"record index entry is invalid: {digest}"
            )
        schema = str(raw_entry.get("schema") or "")
        model = _MODEL_BY_SCHEMA.get(schema)
        if model is None:
            continue
        relative = Path(str(raw_entry.get("path") or ""))
        path = (root / relative).resolve()
        if root != path and root not in path.parents:
            raise SemanticRegistryQueryError(
                "registry_invalid", f"record path escapes registry root: {digest}"
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticRegistryQueryError(
                "registry_invalid", f"cannot read portable record: {digest}"
            ) from exc
        if not isinstance(value, Mapping):
            raise SemanticRegistryQueryError(
                "registry_invalid", f"portable record is not an object: {digest}"
            )
        try:
            record = model.from_mapping(value)
        except (TypeError, ValueError) as exc:
            raise SemanticRegistryQueryError(
                "registry_invalid", f"portable record is invalid: {digest}"
            ) from exc
        identity, revision = portable_record_identity(record)
        if (
            record.digest != digest
            or raw_entry.get("schema") != record.SCHEMA
            or raw_entry.get("identity") != identity
            or raw_entry.get("revision") != revision
        ):
            raise SemanticRegistryQueryError(
                "registry_invalid", f"portable record metadata differs: {digest}"
            )
        by_schema[schema].append(record)
        entries[str(digest)] = dict(raw_entry)
    return (
        _RegistryRecords(
            capabilities=tuple(by_schema[CAPABILITY_CONTRACT_SCHEMA]),  # type: ignore[arg-type]
            states=tuple(by_schema[STATE_CONTRACT_SCHEMA]),  # type: ignore[arg-type]
            bindings=tuple(by_schema[BINDING_DEFINITION_SCHEMA]),  # type: ignore[arg-type]
            deliveries=tuple(by_schema[BINDING_DELIVERY_SCHEMA]),  # type: ignore[arg-type]
            evidence=tuple(by_schema[EVIDENCE_CLAIM_SCHEMA]),  # type: ignore[arg-type]
        ),
        entries,
    )


def _profile_admits_binding(
    definition: BindingDefinition,
    profile: EnvironmentProfile,
    *,
    target_mode: str,
    required_authorities: set[str],
) -> tuple[bool, dict[str, str] | None]:
    value = definition.to_dict()
    environment = profile.to_dict()
    constraints = value["environment_constraints"]
    if (
        target_mode not in value["modes"]
        or environment["profile_class"] not in constraints["profile_classes"]
        or not set(constraints["provider_features"]).issubset(
            set(environment["provider_features"])
        )
    ):
        return False, _rejection(
            "environment_constraint",
            "BindingDefinition does not support the requested environment",
            subject_ref=definition.binding_definition_ref,
            subject_digest=definition.digest,
        )
    binding_authorities = set(value["authority_requirements"])
    if not (binding_authorities | required_authorities).issubset(
        set(environment["authorities"])
    ):
        return False, _rejection(
            "missing_authority",
            "EnvironmentProfile lacks a BindingDefinition authority",
            subject_ref=definition.binding_definition_ref,
            subject_digest=definition.digest,
        )
    ingress_by_profile = {
        str(item.get("profile_ref") or ""): dict(item.get("guarantees") or {})
        for item in environment.get("ingress_guarantees") or ()
        if isinstance(item, Mapping)
    }
    for port in value.get("ingress_ports") or ():
        offered = ingress_by_profile.get(str(port.get("profile_ref") or ""), {})
        required = dict(port.get("required_guarantees") or {})
        if any(offered.get(key) != expected for key, expected in required.items()):
            return False, _rejection(
                "missing_ingress_guarantee",
                f"EnvironmentProfile cannot materialize ingress port {port.get('name')}",
                subject_ref=definition.binding_definition_ref,
                subject_digest=definition.digest,
            )
    return True, None


def _portable_state_selection(
    contract: CapabilityContract,
    definition: BindingDefinition,
    profile: EnvironmentProfile,
    states: Iterable[StateContract],
) -> tuple[tuple[StateContract, ...], dict[str, str] | None]:
    contract_value = contract.to_dict()
    definition_value = definition.to_dict()
    profile_value = profile.to_dict()
    selected: list[StateContract] = []
    for port in contract_value["state_ports"]:
        supports = [
            item
            for item in definition_value["state_support"]
            if item["state_contract_ref"] == port["contract_ref"]
            and port["access"] in item["access_modes"]
        ]
        matches: list[StateContract] = []
        for state in states:
            state_value = state.to_dict()
            if (
                state.state_contract_ref != port["contract_ref"]
                or not version_satisfies(state.version, port["contract_range"])
            ):
                continue
            for support in supports:
                if not version_satisfies(state.version, support["contract_range"]):
                    continue
                effective = {
                    dimension: set(state_value["guarantees"][dimension])
                    & set(support["guarantees"][dimension])
                    & set(profile_value["guarantees"][dimension])
                    for dimension in ("consistency", "durability", "isolation")
                }
                requirements = port["requirements"]
                required_values = {
                    "consistency": requirements.get("consistency_at_least"),
                    "durability": requirements.get("durability"),
                    "isolation": requirements.get("isolation"),
                }
                if any(
                    required is not None and required not in effective[dimension]
                    for dimension, required in required_values.items()
                ):
                    continue
                authority = requirements.get("mutation_authority")
                if authority and authority not in profile_value["authorities"]:
                    continue
                matches.append(state)
                break
        if not matches:
            return (), _rejection(
                "incompatible_state_support",
                f"no portable StateContract and BindingDefinition guarantees satisfy port {port['port_id']}",
                subject_ref=definition.binding_definition_ref,
                subject_digest=definition.digest,
            )
        selected.append(
            sorted(matches, key=lambda item: (Version(item.version), item.digest), reverse=True)[0]
        )
    return tuple(selected), None


def _publisher_rows(
    delivery: BindingDelivery,
    *,
    record_entries: Mapping[str, Mapping[str, Any]],
    application_releases: Mapping[str, Any],
    allowed: set[str],
    denied: set[str],
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    raw_entry = record_entries.get(delivery.digest) or {}
    published_by = set(raw_entry.get("published_by_release_digests") or ())
    rows: list[dict[str, Any]] = []
    for raw in application_releases.values():
        if not isinstance(raw, Mapping):
            continue
        release_digest = str(raw.get("project_release_digest") or "")
        application_ref = str(raw.get("application_ref") or "")
        if release_digest not in published_by:
            continue
        if application_ref in denied or (allowed and application_ref not in allowed):
            continue
        rows.append(
            {
                "application_ref": application_ref,
                "project_id": str(raw.get("project_id") or ""),
                "version": str(raw.get("version") or ""),
                "project_release_digest": release_digest,
                "semantic_revision_digest": str(raw.get("semantic_revision_digest") or ""),
                "projection_digest": str(raw.get("projection_digest") or ""),
            }
        )
    rows.sort(key=lambda item: (item["application_ref"], item["project_release_digest"]))
    if (allowed or denied) and not rows:
        return [], _rejection(
            "publisher_denied",
            "no publisher of the exact BindingDelivery is admitted by policy",
            subject_ref=delivery.to_dict()["binding_definition_ref"],
            subject_digest=delivery.digest,
        )
    return rows, None


def _claim_status(
    claim: EvidenceClaim,
    *,
    as_of: datetime,
    observations: Mapping[str, Mapping[str, Any]],
    allow_stale: bool,
) -> tuple[str | None, dict[str, str] | None]:
    value = claim.to_dict()
    issued_at = _instant(value["issued_at"], field="EvidenceClaim.issued_at")
    max_age = int(value["freshness"]["max_age_seconds"])
    if (as_of - issued_at).total_seconds() > max_age:
        if allow_stale:
            return "stale_allowed", None
        return None, _rejection(
            "stale_evidence",
            "portable EvidenceClaim is older than its freshness policy",
            subject_ref=claim.claim_ref,
            subject_digest=claim.digest,
        )
    for dependency in value["dependencies"]:
        dependency_ref = str(dependency["ref"])
        observed = observations.get(dependency_ref)
        if observed is None:
            return None, _rejection(
                "evidence_dependency_unobserved",
                f"external dependency has no query observation: {dependency_ref}",
                subject_ref=claim.claim_ref,
                subject_digest=claim.digest,
            )
        for field in ("observed_version", "fingerprint"):
            expected = dependency.get(field)
            if expected is not None and observed.get(field) != expected:
                return None, _rejection(
                    "evidence_dependency_mismatch",
                    f"external dependency {dependency_ref} changed {field}",
                    subject_ref=claim.claim_ref,
                    subject_digest=claim.digest,
                )
    return "admissible", None


def _select_evidence(
    *,
    requirement: ApplicationRequirement,
    contract: CapabilityContract,
    supporting: tuple[CapabilityContract, ...],
    states: tuple[StateContract, ...],
    definition: BindingDefinition,
    profile: EnvironmentProfile,
    target_mode: str,
    claims: Iterable[EvidenceClaim],
    evidence_inputs: Mapping[str, Any],
) -> tuple[tuple[dict[str, str], ...], dict[str, str] | None]:
    requirement_value = requirement.to_dict()
    required_kinds = set(requirement_value["evidence_threshold"]["required_claim_kinds"])
    required_kinds.update(str(item) for item in evidence_inputs["required_claim_kinds"])
    if not required_kinds:
        return (), None
    accepted_results = set(str(item) for item in evidence_inputs["accepted_results"])
    observations: dict[str, Mapping[str, Any]] = {}
    for item in evidence_inputs["dependency_observations"]:
        ref = str(item["ref"])
        if ref in observations and dict(observations[ref]) != dict(item):
            raise SemanticRegistryQueryError(
                "query_invalid", f"dependency observation conflicts: {ref}"
            )
        observations[ref] = dict(item)
    as_of = _instant(evidence_inputs["as_of"], field="evidence_inputs.as_of")
    allow_stale = (
        bool(requirement_value["evidence_threshold"]["allow_stale"])
        and target_mode != "production"
    )
    selected: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    state_digests = {item.digest for item in states}
    conformance_subjects = {
        contract.digest,
        definition.digest,
        *(item.digest for item in supporting),
    }
    for kind in sorted(required_kinds):
        kind_matches: list[dict[str, str]] = []
        for claim in claims:
            value = claim.to_dict()
            if value["claim_kind"] != kind:
                continue
            if value["result"] not in accepted_results:
                failures.append(
                    _rejection(
                        "evidence_incompatible",
                        f"EvidenceClaim result is not accepted: {value['result']}",
                        subject_ref=claim.claim_ref,
                        subject_digest=claim.digest,
                    )
                )
                continue
            if value["environment"]["profile_digest"] != profile.digest:
                failures.append(
                    _rejection(
                        "evidence_environment_mismatch",
                        "EvidenceClaim targets another EnvironmentProfile",
                        subject_ref=claim.claim_ref,
                        subject_digest=claim.digest,
                    )
                )
                continue
            subjects = {str(item["digest"]) for item in value["subjects"]}
            required_subjects = (
                conformance_subjects
                if kind == "capability_conformance"
                else {definition.digest, *state_digests}
            )
            if not required_subjects.issubset(subjects):
                continue
            status, rejection = _claim_status(
                claim,
                as_of=as_of,
                observations=observations,
                allow_stale=allow_stale,
            )
            if rejection is not None:
                failures.append(rejection)
                continue
            assert status is not None
            kind_matches.append(
                {
                    "claim_ref": claim.claim_ref,
                    "claim_kind": str(value["claim_kind"]),
                    "claim_digest": claim.digest,
                    "status": status,
                }
            )
        if not kind_matches:
            if failures:
                return (), _dedupe_rejections(failures)[0]
            return (), _rejection(
                "missing_evidence",
                f"no portable EvidenceClaim satisfies required kind {kind}",
                subject_ref=definition.binding_definition_ref,
                subject_digest=definition.digest,
            )
        selected.append(
            sorted(kind_matches, key=lambda item: item["claim_digest"], reverse=True)[0]
        )
    return tuple(selected), None


def execute_semantic_registry_query(
    query: Mapping[str, Any],
    *,
    registry_root: Path,
    index: Mapping[str, Any],
    registry_revision: str,
) -> dict[str, Any]:
    """Resolve portable candidates without importing records or local authority."""

    request = dict(query)
    _validate("query", request)
    snapshot = dict(request["snapshot"])
    observed_revision = str(registry_revision or "").strip()
    if not observed_revision:
        raise SemanticRegistryQueryError(
            "snapshot_unavailable", "registry revision is required"
        )
    if snapshot["registry_revision"] != observed_revision:
        raise SemanticRegistryQueryError(
            "snapshot_drift", "registry revision differs from the pinned query snapshot"
        )
    if snapshot["index_digest"] != index.get("index_digest"):
        raise SemanticRegistryQueryError(
            "snapshot_drift", "semantic index digest differs from the pinned query snapshot"
        )

    requirements = tuple(
        ApplicationRequirement.from_mapping(item) for item in request["requirements"]
    )
    profile = EnvironmentProfile.from_mapping(request["environment_profile"])
    target_mode = str(request["target_mode"])
    policy = dict(request["policy_inputs"])
    evidence_inputs = dict(request["evidence_inputs"])
    records, record_entries = _load_records(Path(registry_root), index)
    application_release_index = dict(index.get("application_releases") or {})
    allowed_publishers = set(str(item) for item in policy["allowed_publisher_refs"])
    denied_publishers = set(str(item) for item in policy["denied_publisher_refs"])
    if allowed_publishers & denied_publishers:
        raise SemanticRegistryQueryError(
            "query_invalid", "publisher policy allows and denies the same identity"
        )

    requirement_rows: list[dict[str, Any]] = []
    selected_digests: set[str] = set()
    selected_releases: dict[str, dict[str, Any]] = {}
    for requirement in requirements:
        requirement_value = requirement.to_dict()
        rejections: list[dict[str, str]] = []
        candidates: list[dict[str, Any]] = []
        environment_target = requirement_value["environment_target"]
        if (
            environment_target["profile_ref"] != profile.profile_ref
            or target_mode not in environment_target["allowed_modes"]
            or target_mode not in profile.to_dict()["modes"]
        ):
            rejections.append(
                _rejection(
                    "environment_mismatch",
                    "requirement and query target different environment materializations",
                    subject_ref=profile.profile_ref,
                    subject_digest=profile.digest,
                )
            )
        required_authorities = set(
            str(item)
            for item in requirement_value["policy_constraints"].get(
                "required_authorities", ()
            )
        ) | set(str(item) for item in policy["required_authorities"])
        if not required_authorities.issubset(set(profile.to_dict()["authorities"])):
            rejections.append(
                _rejection(
                    "missing_authority",
                    "EnvironmentProfile lacks a required query authority",
                    subject_ref=profile.profile_ref,
                    subject_digest=profile.digest,
                )
            )
        matching_contracts = sorted(
            (
                item
                for item in records.capabilities
                if item.capability_ref == requirement.capability_ref
                and version_satisfies(
                    item.version, str(requirement_value["contract_range"])
                )
            ),
            key=lambda item: (Version(item.version), item.digest),
            reverse=True,
        )
        if not matching_contracts:
            rejections.append(
                _rejection(
                    "unmet_requirement",
                    "no CapabilityContract satisfies the requested range",
                    subject_ref=requirement.capability_ref,
                )
            )
        if rejections:
            requirement_rows.append(
                {
                    "requirement_ref": requirement.requirement_ref,
                    "requirement_digest": requirement.digest,
                    "status": "unmatched",
                    "candidates": [],
                    "rejections": _dedupe_rejections(rejections),
                }
            )
            continue

        for contract in matching_contracts:
            contract_value = contract.to_dict()
            supporting: list[CapabilityContract] = []
            dependency_failed = False
            for dependency in contract_value["dependencies"]:
                matches = sorted(
                    (
                        item
                        for item in records.capabilities
                        if item.capability_ref == dependency["capability_ref"]
                        and version_satisfies(
                            item.version, dependency["contract_range"]
                        )
                    ),
                    key=lambda item: (Version(item.version), item.digest),
                    reverse=True,
                )
                if not matches:
                    if dependency.get("optional") is True:
                        continue
                    rejections.append(
                        _rejection(
                            "missing_capability_dependency",
                            "no CapabilityContract satisfies dependency "
                            f"{dependency['capability_ref']} {dependency['contract_range']}",
                            subject_ref=contract.capability_ref,
                            subject_digest=contract.digest,
                        )
                    )
                    dependency_failed = True
                    break
                supporting.append(matches[0])
            if dependency_failed:
                continue
            definitions = [
                item
                for item in records.bindings
                if item.capability_ref == contract.capability_ref
                and item.to_dict()["capability_version"] == contract.version
            ]
            if not definitions:
                rejections.append(
                    _rejection(
                        "no_binding_definition",
                        "no BindingDefinition implements the exact CapabilityContract",
                        subject_ref=contract.capability_ref,
                        subject_digest=contract.digest,
                    )
                )
                continue
            for definition in definitions:
                admitted, rejection = _profile_admits_binding(
                    definition,
                    profile,
                    target_mode=target_mode,
                    required_authorities=required_authorities,
                )
                if not admitted:
                    assert rejection is not None
                    rejections.append(rejection)
                    continue
                states, rejection = _portable_state_selection(
                    contract, definition, profile, records.states
                )
                if rejection is not None:
                    rejections.append(rejection)
                    continue
                deliveries = sorted(
                    (
                        item
                        for item in records.deliveries
                        if item.binding_definition_digest == definition.digest
                    ),
                    key=lambda item: (item.package_digest, item.digest),
                    reverse=True,
                )
                if not deliveries:
                    rejections.append(
                        _rejection(
                            "no_binding_delivery",
                            "no exact package delivery exists for BindingDefinition",
                            subject_ref=definition.binding_definition_ref,
                            subject_digest=definition.digest,
                        )
                    )
                    continue
                for delivery in deliveries:
                    publishers, rejection = _publisher_rows(
                        delivery,
                        record_entries=record_entries,
                        application_releases=application_release_index,
                        allowed=allowed_publishers,
                        denied=denied_publishers,
                    )
                    if rejection is not None:
                        rejections.append(rejection)
                        continue
                    evidence, rejection = _select_evidence(
                        requirement=requirement,
                        contract=contract,
                        supporting=tuple(supporting),
                        states=states,
                        definition=definition,
                        profile=profile,
                        target_mode=target_mode,
                        claims=records.evidence,
                        evidence_inputs=evidence_inputs,
                    )
                    if rejection is not None:
                        rejections.append(rejection)
                        continue
                    identity = {
                        "capability_contract_digest": contract.digest,
                        "supporting_contract_digests": sorted(
                            item.digest for item in supporting
                        ),
                        "state_contract_digests": sorted(item.digest for item in states),
                        "binding_definition_digest": definition.digest,
                        "delivery_digest": delivery.digest,
                        "evidence_claim_digests": sorted(
                            item["claim_digest"] for item in evidence
                        ),
                        "publisher_release_digests": sorted(
                            item["project_release_digest"] for item in publishers
                        ),
                    }
                    candidate = {
                        "candidate_digest": canonical_payload_digest(identity),
                        "capability_contract": _record_ref(contract),
                        "supporting_contracts": [
                            _record_ref(item) for item in supporting
                        ],
                        "state_contracts": [_record_ref(item) for item in states],
                        "binding_definition": _binding_ref(definition),
                        "delivery": _delivery_ref(delivery),
                        "evidence": list(evidence),
                        "publisher_release_digests": sorted(
                            item["project_release_digest"] for item in publishers
                        ),
                        "explanation": (
                            "portable semantic, environment, state, publisher and evidence inputs are eligible; "
                            "exact package fetch and local authority admission remain pending"
                        ),
                    }
                    candidates.append(candidate)
                    selected_digests.update(
                        {
                            contract.digest,
                            definition.digest,
                            delivery.digest,
                            *(item.digest for item in supporting),
                            *(item.digest for item in states),
                            *(item["claim_digest"] for item in evidence),
                        }
                    )
                    for publisher in publishers:
                        selected_releases[
                            str(publisher["project_release_digest"])
                        ] = publisher

        candidates = sorted(
            {
                str(item["candidate_digest"]): item for item in candidates
            }.values(),
            key=lambda item: (
                Version(str(item["capability_contract"]["version"])),
                str(item["binding_definition"]["ref"]),
                str(item["binding_definition"]["digest"]),
                str(item["delivery"]["digest"]),
            ),
            reverse=True,
        )
        requirement_rows.append(
            {
                "requirement_ref": requirement.requirement_ref,
                "requirement_digest": requirement.digest,
                "status": "matched" if candidates else "unmatched",
                "candidates": candidates,
                "rejections": _dedupe_rejections(rejections),
            }
        )

    matched = sum(item["status"] == "matched" for item in requirement_rows)
    status = (
        "matched"
        if matched == len(requirement_rows)
        else "unmatched"
        if matched == 0
        else "partial"
    )
    result: dict[str, Any] = {
        "schema": SEMANTIC_REGISTRY_QUERY_RESULT_SCHEMA,
        "query_ref": str(request["query_ref"]),
        "query_digest": canonical_payload_digest(request),
        "snapshot": snapshot,
        "status": status,
        "requirements": requirement_rows,
        "record_digests": sorted(selected_digests),
        "application_releases": [
            selected_releases[key] for key in sorted(selected_releases)
        ],
        "activation_performed": False,
        "local_authority_created": False,
    }
    result["result_digest"] = canonical_payload_digest(result)
    _validate("result", result)
    return result


__all__ = [
    "SEMANTIC_REGISTRY_QUERY_SCHEMA",
    "SEMANTIC_REGISTRY_QUERY_RESULT_SCHEMA",
    "SemanticRegistryQueryError",
    "execute_semantic_registry_query",
]
