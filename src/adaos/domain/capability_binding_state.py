"""Versioned contracts for capability, binding, and state separation.

The records in this module are deliberately small immutable wrappers around
fail-closed JSON ABI documents.  Portable records never contain installation
locators or credentials.  Their exact canonical payload is therefore safe to
package, attest, and compare independently from a workspace.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping, TypeVar

from jsonschema import Draft202012Validator
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from adaos.domain.artifact_release import canonical_payload_digest


CAPABILITY_CONTRACT_SCHEMA = "adaos.capability.contract.v1"
STATE_CONTRACT_SCHEMA = "adaos.state.contract.v1"
BINDING_DEFINITION_SCHEMA = "adaos.binding.definition.v1"
BINDING_DELIVERY_SCHEMA = "adaos.binding.delivery.v1"
EVIDENCE_CLAIM_SCHEMA = "adaos.evidence.claim.v1"
EVIDENCE_ASSESSMENT_SCHEMA = "adaos.evidence.assessment.v1"
ENVIRONMENT_PROFILE_SCHEMA = "adaos.environment.profile.v1"
APPLICATION_REQUIREMENT_SCHEMA = "adaos.application.requirement.v1"
BINDING_INSTANCE_SCHEMA = "adaos.binding.instance.v1"
STATE_SPACE_SCHEMA = "adaos.state.space.v1"
STATE_ACCESS_RELATION_SCHEMA = "adaos.state.access_relation.v1"
STATE_LIFECYCLE_OPERATION_SCHEMA = "adaos.state.lifecycle_operation.v1"
LOCAL_REVISION_OBSERVATION_SCHEMA = "adaos.local_revision.observation.v1"
APPLICATION_RESOLUTION_SCHEMA = "adaos.application.resolution.v1"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REF_RE = re.compile(r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
_LOGICAL_ENTRYPOINT_RE = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?::[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)?$"
)
_PORTABLE_SECRET_KEYS = {"credential", "credentials", "dsn", "password", "secret", "token"}


class CapabilityBindingStateContractError(ValueError):
    """A canonical CBS record is malformed or inconsistent."""


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        # ``allow_nan=False`` makes this a cheap finite-number assertion and
        # preserves the canonical JSON convention used by package records.
        json.dumps(value, allow_nan=False)
        return value
    raise CapabilityBindingStateContractError(
        f"canonical records do not support {type(value).__name__} values"
    )


@lru_cache(maxsize=None)
def _schema(schema_name: str) -> Mapping[str, Any]:
    filename = schema_name.removeprefix("adaos.") + ".schema.json"
    path = Path(__file__).resolve().parents[1] / "abi" / filename
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityBindingStateContractError(
            f"cannot load ABI schema {schema_name}: {exc}"
        ) from exc
    Draft202012Validator.check_schema(value)
    return MappingProxyType(value)


def _validate_schema(schema_name: str, payload: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(dict(_schema(schema_name))).iter_errors(dict(payload)),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(item) for item in first.absolute_path)
    suffix = f" at {location}" if location else ""
    raise CapabilityBindingStateContractError(
        f"invalid {schema_name}{suffix}: {first.message}"
    )


def _validate_ref(value: Any, *, prefix: str, field: str) -> str:
    token = str(value or "").strip()
    if not _REF_RE.fullmatch(token) or not token.startswith(prefix):
        raise CapabilityBindingStateContractError(
            f"{field} must be a stable {prefix} reference"
        )
    return token


def _validate_version(value: Any, *, field: str = "version") -> str:
    token = str(value or "").strip()
    try:
        parsed = Version(token)
    except InvalidVersion as exc:
        raise CapabilityBindingStateContractError(
            f"{field} must be a semantic version"
        ) from exc
    if not token or parsed.local is not None:
        raise CapabilityBindingStateContractError(
            f"{field} must be a portable semantic version without local metadata"
        )
    return token


def _validate_range(value: Any, *, field: str) -> str:
    token = str(value or "").strip()
    if token.startswith("^"):
        base = _validate_version(token[1:], field=field)
        parsed = Version(base)
        major, minor, patch = tuple(parsed.release) + (0,) * (3 - len(parsed.release))
        if major:
            token = f">={base},<{major + 1}.0.0"
        elif minor:
            token = f">={base},<0.{minor + 1}.0"
        else:
            token = f">={base},<0.0.{patch + 1}"
    try:
        SpecifierSet(token)
    except InvalidSpecifier as exc:
        raise CapabilityBindingStateContractError(
            f"{field} must be a semantic version range"
        ) from exc
    return str(value).strip()


def version_satisfies(version: str, version_range: str) -> bool:
    """Return whether ``version`` is admitted by a canonical CBS range."""

    normalized = _validate_range(version_range, field="version_range")
    if normalized.startswith("^"):
        base = Version(normalized[1:])
        major, minor, patch = tuple(base.release) + (0,) * (3 - len(base.release))
        ceiling = (
            Version(f"{major + 1}.0.0")
            if major
            else Version(f"0.{minor + 1}.0")
            if minor
            else Version(f"0.0.{patch + 1}")
        )
        return base <= Version(version) < ceiling
    return Version(_validate_version(version)) in SpecifierSet(normalized)


def _assert_portable(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _PORTABLE_SECRET_KEYS):
                raise CapabilityBindingStateContractError(
                    f"portable record contains prohibited credential field: {'.'.join(path + (str(key),))}"
                )
            _assert_portable(item, path=path + (str(key),))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_portable(item, path=path + (str(index),))
        return
    if not isinstance(value, str):
        return
    token = value.strip()
    lowered = token.lower()
    if re.match(r"^[a-zA-Z]:[\\/]", token) or token.startswith(("/", "\\\\")):
        raise CapabilityBindingStateContractError(
            f"portable record contains an absolute path at {'.'.join(path)}"
        )
    if lowered.startswith(
        ("postgres://", "postgresql://", "mysql://", "sqlite://", "mongodb://", "redis://")
    ):
        raise CapabilityBindingStateContractError(
            f"portable record contains a storage locator at {'.'.join(path)}"
        )


RecordT = TypeVar("RecordT", bound="CanonicalRecord")


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    """Immutable, schema-validated, digest-addressed JSON record."""

    _payload: Mapping[str, Any]

    SCHEMA: ClassVar[str]
    DIGEST_FIELD: ClassVar[str]
    PORTABLE: ClassVar[bool] = True

    def __post_init__(self) -> None:
        raw = _thaw(self._payload)
        if not isinstance(raw, dict):
            raise CapabilityBindingStateContractError("canonical record must be an object")
        _validate_schema(self.SCHEMA, raw)
        if self.PORTABLE:
            portable = dict(raw)
            portable.pop(self.DIGEST_FIELD, None)
            _assert_portable(portable)
        expected = str(raw.get(self.DIGEST_FIELD) or "").lower()
        unsigned = dict(raw)
        unsigned.pop(self.DIGEST_FIELD, None)
        actual = canonical_payload_digest(unsigned)
        if expected != actual:
            raise CapabilityBindingStateContractError(
                f"{self.DIGEST_FIELD} does not match canonical {self.SCHEMA} content"
            )
        object.__setattr__(self, "_payload", _freeze(raw))

    @classmethod
    def from_mapping(cls: type[RecordT], value: Mapping[str, Any]) -> RecordT:
        return cls(_freeze(dict(value)))

    @classmethod
    def _create(cls: type[RecordT], value: Mapping[str, Any]) -> RecordT:
        payload = dict(_thaw(value))
        payload["schema"] = cls.SCHEMA
        payload.pop(cls.DIGEST_FIELD, None)
        payload[cls.DIGEST_FIELD] = canonical_payload_digest(payload)
        return cls.from_mapping(payload)

    @property
    def digest(self) -> str:
        return str(self._payload[self.DIGEST_FIELD])

    def to_dict(self) -> dict[str, Any]:
        return dict(_thaw(self._payload))


@dataclass(frozen=True, slots=True)
class CapabilityContract(CanonicalRecord):
    SCHEMA: ClassVar[str] = CAPABILITY_CONTRACT_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "contract_digest"

    @classmethod
    def create(
        cls,
        *,
        capability_ref: str,
        version: str,
        title: str,
        operations: Iterable[Mapping[str, Any]],
        invariants: Iterable[str] = (),
        effects: Iterable[str] = (),
        authority_requirements: Iterable[str] = (),
        dependencies: Iterable[Mapping[str, Any]] = (),
        state_ports: Iterable[Mapping[str, Any]] = (),
        conformance_refs: Iterable[str] = (),
        compatibility: Mapping[str, Any] | None = None,
    ) -> "CapabilityContract":
        _validate_ref(capability_ref, prefix="capability:", field="capability_ref")
        _validate_version(version)
        for item in dependencies:
            _validate_range(item.get("contract_range"), field="dependencies.contract_range")
        for item in state_ports:
            _validate_range(item.get("contract_range"), field="state_ports.contract_range")
        return cls._create(
            {
                "capability_ref": capability_ref,
                "version": version,
                "title": title,
                "operations": [dict(item) for item in operations],
                "invariants": list(invariants),
                "effects": list(effects),
                "authority_requirements": list(authority_requirements),
                "dependencies": [dict(item) for item in dependencies],
                "state_ports": [dict(item) for item in state_ports],
                "conformance_refs": list(conformance_refs),
                "compatibility": dict(compatibility or {}),
            }
        )

    @property
    def capability_ref(self) -> str:
        return str(self._payload["capability_ref"])

    @property
    def version(self) -> str:
        return str(self._payload["version"])


@dataclass(frozen=True, slots=True)
class StateContract(CanonicalRecord):
    SCHEMA: ClassVar[str] = STATE_CONTRACT_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "contract_digest"

    @classmethod
    def create(
        cls,
        *,
        state_contract_ref: str,
        version: str,
        schema_locks: Iterable[Mapping[str, Any]],
        invariant_refs: Iterable[str],
        guarantees: Mapping[str, Any],
        lifecycle: Mapping[str, Any],
        ownership_constraints: Mapping[str, Any],
        portability_class: str,
        migration_compatibility: Mapping[str, Any],
    ) -> "StateContract":
        _validate_ref(state_contract_ref, prefix="state-contract:", field="state_contract_ref")
        _validate_version(version)
        return cls._create(
            {
                "state_contract_ref": state_contract_ref,
                "version": version,
                "schema_locks": [dict(item) for item in schema_locks],
                "invariant_refs": list(invariant_refs),
                "guarantees": dict(guarantees),
                "lifecycle": dict(lifecycle),
                "ownership_constraints": dict(ownership_constraints),
                "portability_class": portability_class,
                "migration_compatibility": dict(migration_compatibility),
            }
        )

    @property
    def state_contract_ref(self) -> str:
        return str(self._payload["state_contract_ref"])

    @property
    def version(self) -> str:
        return str(self._payload["version"])


@dataclass(frozen=True, slots=True)
class BindingDefinition(CanonicalRecord):
    SCHEMA: ClassVar[str] = BINDING_DEFINITION_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "definition_digest"

    @classmethod
    def create(
        cls,
        *,
        binding_definition_ref: str,
        version: str,
        capability_ref: str,
        capability_version: str,
        entry_protocol: str,
        implementation_entrypoint: str,
        state_support: Iterable[Mapping[str, Any]],
        modes: Iterable[str],
        environment_constraints: Mapping[str, Any],
        authority_requirements: Iterable[str],
        conformance_obligations: Iterable[str],
    ) -> "BindingDefinition":
        _validate_ref(
            binding_definition_ref,
            prefix="binding-definition:",
            field="binding_definition_ref",
        )
        _validate_ref(capability_ref, prefix="capability:", field="capability_ref")
        _validate_version(version)
        _validate_version(capability_version, field="capability_version")
        if not _LOGICAL_ENTRYPOINT_RE.fullmatch(str(implementation_entrypoint or "")):
            raise CapabilityBindingStateContractError(
                "implementation_entrypoint must be a logical id, not a package path"
            )
        for item in state_support:
            _validate_range(item.get("contract_range"), field="state_support.contract_range")
        return cls._create(
            {
                "binding_definition_ref": binding_definition_ref,
                "version": version,
                "capability_ref": capability_ref,
                "capability_version": capability_version,
                "entry_protocol": entry_protocol,
                "implementation_entrypoint": implementation_entrypoint,
                "state_support": [dict(item) for item in state_support],
                "modes": list(modes),
                "environment_constraints": dict(environment_constraints),
                "authority_requirements": list(authority_requirements),
                "conformance_obligations": list(conformance_obligations),
            }
        )

    @property
    def binding_definition_ref(self) -> str:
        return str(self._payload["binding_definition_ref"])

    @property
    def capability_ref(self) -> str:
        return str(self._payload["capability_ref"])


@dataclass(frozen=True, slots=True)
class BindingDelivery(CanonicalRecord):
    SCHEMA: ClassVar[str] = BINDING_DELIVERY_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "delivery_digest"

    @classmethod
    def create(
        cls,
        *,
        binding_definition_ref: str,
        binding_definition_digest: str,
        logical_entrypoint: str,
        package: Mapping[str, Any],
        physical_member: str,
    ) -> "BindingDelivery":
        _validate_ref(
            binding_definition_ref,
            prefix="binding-definition:",
            field="binding_definition_ref",
        )
        if not _DIGEST_RE.fullmatch(str(binding_definition_digest or "")):
            raise CapabilityBindingStateContractError(
                "binding_definition_digest must be a canonical digest"
            )
        if not _LOGICAL_ENTRYPOINT_RE.fullmatch(str(logical_entrypoint or "")):
            raise CapabilityBindingStateContractError("logical_entrypoint is invalid")
        member = PurePosixPath(str(physical_member or "").replace("\\", "/"))
        if member.is_absolute() or not member.parts or ".." in member.parts:
            raise CapabilityBindingStateContractError(
                "physical_member must be a safe package-relative path"
            )
        return cls._create(
            {
                "binding_definition_ref": binding_definition_ref,
                "binding_definition_digest": binding_definition_digest,
                "logical_entrypoint": logical_entrypoint,
                "package": dict(package),
                "physical_member": member.as_posix(),
            }
        )

    @property
    def binding_definition_digest(self) -> str:
        return str(self._payload["binding_definition_digest"])

    @property
    def package_digest(self) -> str:
        package = self._payload["package"]
        assert isinstance(package, Mapping)
        return str(package["digest"])


@dataclass(frozen=True, slots=True)
class EnvironmentProfile(CanonicalRecord):
    SCHEMA: ClassVar[str] = ENVIRONMENT_PROFILE_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "profile_digest"

    @classmethod
    def create(
        cls,
        *,
        profile_ref: str,
        profile_class: str,
        modes: Iterable[str],
        provider_features: Iterable[str],
        guarantees: Mapping[str, Any],
        authorities: Iterable[str],
    ) -> "EnvironmentProfile":
        _validate_ref(profile_ref, prefix="profile:", field="profile_ref")
        return cls._create(
            {
                "profile_ref": profile_ref,
                "profile_class": profile_class,
                "modes": list(modes),
                "provider_features": list(provider_features),
                "guarantees": dict(guarantees),
                "authorities": list(authorities),
            }
        )

    @property
    def profile_ref(self) -> str:
        return str(self._payload["profile_ref"])


@dataclass(frozen=True, slots=True)
class EvidenceClaim(CanonicalRecord):
    SCHEMA: ClassVar[str] = EVIDENCE_CLAIM_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "claim_digest"

    @classmethod
    def create(
        cls,
        *,
        claim_ref: str,
        claim_kind: str,
        subjects: Iterable[Mapping[str, Any]],
        environment: Mapping[str, Any],
        dependencies: Iterable[Mapping[str, Any]],
        suite_digest: str,
        evidence_digest: str,
        provenance: Mapping[str, Any],
        issued_at: str,
        freshness: Mapping[str, Any],
        result: str,
        redaction: Mapping[str, Any],
        portability_scope: str,
    ) -> "EvidenceClaim":
        _validate_ref(claim_ref, prefix="evidence-claim:", field="claim_ref")
        return cls._create(
            {
                "claim_ref": claim_ref,
                "claim_kind": claim_kind,
                "subjects": [dict(item) for item in subjects],
                "environment": dict(environment),
                "dependencies": [dict(item) for item in dependencies],
                "suite_digest": suite_digest,
                "evidence_digest": evidence_digest,
                "provenance": dict(provenance),
                "issued_at": issued_at,
                "freshness": dict(freshness),
                "result": result,
                "redaction": dict(redaction),
                "portability_scope": portability_scope,
            }
        )

    @property
    def claim_ref(self) -> str:
        return str(self._payload["claim_ref"])


@dataclass(frozen=True, slots=True)
class EvidenceAssessment(CanonicalRecord):
    SCHEMA: ClassVar[str] = EVIDENCE_ASSESSMENT_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "assessment_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        *,
        assessment_ref: str,
        claim_ref: str,
        claim_digest: str,
        evaluated_at: str,
        policy_digest: str,
        status: str,
        reasons: Iterable[str] = (),
    ) -> "EvidenceAssessment":
        _validate_ref(assessment_ref, prefix="evidence-assessment:", field="assessment_ref")
        _validate_ref(claim_ref, prefix="evidence-claim:", field="claim_ref")
        return cls._create(
            {
                "assessment_ref": assessment_ref,
                "claim_ref": claim_ref,
                "claim_digest": claim_digest,
                "evaluated_at": evaluated_at,
                "policy_digest": policy_digest,
                "status": status,
                "reasons": list(reasons),
            }
        )


@dataclass(frozen=True, slots=True)
class ApplicationRequirement(CanonicalRecord):
    SCHEMA: ClassVar[str] = APPLICATION_REQUIREMENT_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "requirement_digest"

    @classmethod
    def create(
        cls,
        *,
        requirement_ref: str,
        capability_ref: str,
        contract_range: str,
        environment_target: Mapping[str, Any],
        policy_constraints: Mapping[str, Any],
        evidence_threshold: Mapping[str, Any],
    ) -> "ApplicationRequirement":
        _validate_ref(requirement_ref, prefix="requirement:", field="requirement_ref")
        _validate_ref(capability_ref, prefix="capability:", field="capability_ref")
        _validate_range(contract_range, field="contract_range")
        return cls._create(
            {
                "requirement_ref": requirement_ref,
                "capability_ref": capability_ref,
                "contract_range": contract_range,
                "environment_target": dict(environment_target),
                "policy_constraints": dict(policy_constraints),
                "evidence_threshold": dict(evidence_threshold),
            }
        )

    @property
    def requirement_ref(self) -> str:
        return str(self._payload["requirement_ref"])

    @property
    def capability_ref(self) -> str:
        return str(self._payload["capability_ref"])


@dataclass(frozen=True, slots=True)
class BindingInstance(CanonicalRecord):
    SCHEMA: ClassVar[str] = BINDING_INSTANCE_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "revision_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        *,
        binding_instance_ref: str,
        revision: int,
        predecessor_digest: str | None,
        workspace_ref: str,
        tenant_ref: str | None,
        binding_definition_ref: str,
        binding_definition_digest: str,
        delivery_digest: str,
        environment_profile_ref: str,
        environment_profile_digest: str,
        mode: str,
        local_binding_ref: str,
        authority_epoch: int,
    ) -> "BindingInstance":
        _validate_ref(
            binding_instance_ref,
            prefix="binding-instance:",
            field="binding_instance_ref",
        )
        value: dict[str, Any] = {
            "binding_instance_ref": binding_instance_ref,
            "revision": revision,
            "workspace_ref": workspace_ref,
            "binding_definition_ref": binding_definition_ref,
            "binding_definition_digest": binding_definition_digest,
            "delivery_digest": delivery_digest,
            "environment_profile_ref": environment_profile_ref,
            "environment_profile_digest": environment_profile_digest,
            "mode": mode,
            "local_binding_ref": local_binding_ref,
            "authority_epoch": authority_epoch,
        }
        if predecessor_digest is not None:
            value["predecessor_digest"] = predecessor_digest
        if tenant_ref is not None:
            value["tenant_ref"] = tenant_ref
        return cls._create(value)

    @property
    def stable_ref(self) -> str:
        return str(self._payload["binding_instance_ref"])

    @property
    def revision(self) -> int:
        return int(self._payload["revision"])

    @property
    def predecessor_digest(self) -> str | None:
        value = self._payload.get("predecessor_digest")
        return str(value) if value is not None else None

    @property
    def authority_epoch(self) -> int:
        return int(self._payload["authority_epoch"])


@dataclass(frozen=True, slots=True)
class StateSpace(CanonicalRecord):
    SCHEMA: ClassVar[str] = STATE_SPACE_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "revision_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        *,
        state_space_ref: str,
        revision: int,
        predecessor_digest: str | None,
        state_contract_ref: str,
        state_contract_version: str,
        state_contract_digest: str,
        workspace_ref: str,
        tenant_ref: str | None,
        logical_owner_ref: str,
        lifecycle_authority_ref: str,
        custodian_binding_instance_ref: str,
        mutation_authority_ref: str,
        locator_ref: str,
        generation: int,
        authority_epoch: int,
        portability_class: str,
        schema_locks: Iterable[Mapping[str, Any]],
    ) -> "StateSpace":
        _validate_ref(state_space_ref, prefix="state-space:", field="state_space_ref")
        _validate_ref(
            state_contract_ref,
            prefix="state-contract:",
            field="state_contract_ref",
        )
        _validate_version(state_contract_version, field="state_contract_version")
        value: dict[str, Any] = {
            "state_space_ref": state_space_ref,
            "revision": revision,
            "state_contract_ref": state_contract_ref,
            "state_contract_version": state_contract_version,
            "state_contract_digest": state_contract_digest,
            "workspace_ref": workspace_ref,
            "logical_owner_ref": logical_owner_ref,
            "lifecycle_authority_ref": lifecycle_authority_ref,
            "custodian_binding_instance_ref": custodian_binding_instance_ref,
            "mutation_authority_ref": mutation_authority_ref,
            "locator_ref": locator_ref,
            "generation": generation,
            "authority_epoch": authority_epoch,
            "portability_class": portability_class,
            "schema_locks": [dict(item) for item in schema_locks],
        }
        if predecessor_digest is not None:
            value["predecessor_digest"] = predecessor_digest
        if tenant_ref is not None:
            value["tenant_ref"] = tenant_ref
        return cls._create(value)

    @property
    def stable_ref(self) -> str:
        return str(self._payload["state_space_ref"])

    @property
    def revision(self) -> int:
        return int(self._payload["revision"])

    @property
    def predecessor_digest(self) -> str | None:
        value = self._payload.get("predecessor_digest")
        return str(value) if value is not None else None

    @property
    def generation(self) -> int:
        return int(self._payload["generation"])

    @property
    def authority_epoch(self) -> int:
        return int(self._payload["authority_epoch"])


@dataclass(frozen=True, slots=True)
class StateAccessRelation(CanonicalRecord):
    SCHEMA: ClassVar[str] = STATE_ACCESS_RELATION_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "relation_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        *,
        relation_ref: str,
        binding_instance_ref: str,
        binding_instance_revision_digest: str,
        state_space_ref: str,
        state_space_revision_digest: str,
        port_id: str,
        access: str,
    ) -> "StateAccessRelation":
        _validate_ref(relation_ref, prefix="state-access:", field="relation_ref")
        return cls._create(
            {
                "relation_ref": relation_ref,
                "binding_instance_ref": binding_instance_ref,
                "binding_instance_revision_digest": binding_instance_revision_digest,
                "state_space_ref": state_space_ref,
                "state_space_revision_digest": state_space_revision_digest,
                "port_id": port_id,
                "access": access,
            }
        )

    @property
    def stable_ref(self) -> str:
        return str(self._payload["relation_ref"])


@dataclass(frozen=True, slots=True)
class StateLifecycleOperation(CanonicalRecord):
    SCHEMA: ClassVar[str] = STATE_LIFECYCLE_OPERATION_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "operation_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        *,
        operation_ref: str,
        operation: str,
        state_space_ref: str,
        state_space_revision_digest: str,
        authority_ref: str,
        requested_at: str,
        reason: str,
        target_locator_ref: str | None = None,
    ) -> "StateLifecycleOperation":
        _validate_ref(operation_ref, prefix="state-operation:", field="operation_ref")
        value: dict[str, Any] = {
            "operation_ref": operation_ref,
            "operation": operation,
            "state_space_ref": state_space_ref,
            "state_space_revision_digest": state_space_revision_digest,
            "authority_ref": authority_ref,
            "requested_at": requested_at,
            "reason": reason,
        }
        if target_locator_ref is not None:
            value["target_locator_ref"] = target_locator_ref
        return cls._create(value)


@dataclass(frozen=True, slots=True)
class LocalRevisionObservation(CanonicalRecord):
    SCHEMA: ClassVar[str] = LOCAL_REVISION_OBSERVATION_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "observation_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        *,
        observation_ref: str,
        subject_kind: str,
        subject_ref: str,
        subject_revision_digest: str,
        observation_kind: str,
        status: str,
        observed_at: str,
        details: Mapping[str, Any] | None = None,
    ) -> "LocalRevisionObservation":
        _validate_ref(observation_ref, prefix="observation:", field="observation_ref")
        return cls._create(
            {
                "observation_ref": observation_ref,
                "subject_kind": subject_kind,
                "subject_ref": subject_ref,
                "subject_revision_digest": subject_revision_digest,
                "observation_kind": observation_kind,
                "status": status,
                "observed_at": observed_at,
                "details": dict(details or {}),
            }
        )

    @property
    def stable_ref(self) -> str:
        return str(self._payload["observation_ref"])


@dataclass(frozen=True, slots=True)
class ApplicationResolution(CanonicalRecord):
    SCHEMA: ClassVar[str] = APPLICATION_RESOLUTION_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "resolution_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        *,
        resolution_ref: str,
        semantic_revision_digest: str,
        requirement_ref: str,
        requirement_digest: str,
        environment_profile_ref: str,
        environment_profile_digest: str,
        policy_digest: str,
        selected_contracts: Iterable[Mapping[str, Any]],
        binding_definition: Mapping[str, Any],
        project_release_digest: str,
        package_closure: Iterable[Mapping[str, Any]],
        delivery: Mapping[str, Any],
        binding_instances: Iterable[Mapping[str, Any]],
        provisioning_obligations: Iterable[Mapping[str, Any]],
        state_attachments: Iterable[Mapping[str, Any]],
        evidence: Iterable[Mapping[str, Any]],
        rejection_explanations: Iterable[Mapping[str, Any]],
        created_at: str,
    ) -> "ApplicationResolution":
        _validate_ref(
            resolution_ref,
            prefix="application-resolution:",
            field="resolution_ref",
        )
        return cls._create(
            {
                "resolution_ref": resolution_ref,
                "semantic_revision_digest": semantic_revision_digest,
                "requirement_ref": requirement_ref,
                "requirement_digest": requirement_digest,
                "environment_profile_ref": environment_profile_ref,
                "environment_profile_digest": environment_profile_digest,
                "policy_digest": policy_digest,
                "selected_contracts": [dict(item) for item in selected_contracts],
                "binding_definition": dict(binding_definition),
                "project_release_digest": project_release_digest,
                "package_closure": [dict(item) for item in package_closure],
                "delivery": dict(delivery),
                "binding_instances": [dict(item) for item in binding_instances],
                "provisioning_obligations": [dict(item) for item in provisioning_obligations],
                "state_attachments": [dict(item) for item in state_attachments],
                "evidence": [dict(item) for item in evidence],
                "rejection_explanations": [dict(item) for item in rejection_explanations],
                "created_at": created_at,
            }
        )

    @property
    def resolution_ref(self) -> str:
        return str(self._payload["resolution_ref"])


def validate_state_contract_locks(
    contract: StateContract,
    *,
    schema_locks: Iterable[Mapping[str, Any]],
    migration_locks: Iterable[Mapping[str, Any]] = (),
) -> None:
    """Bind a StateContract to existing lock facts without copying their content."""

    payload = contract.to_dict()
    declared_schema = {
        str(item["lock_id"]): str(item["digest"])
        for item in payload["schema_locks"]
    }
    expected_schema = {
        str(item.get("lock_id") or item.get("id") or ""): str(item.get("digest") or "")
        for item in schema_locks
    }
    if declared_schema != expected_schema:
        raise CapabilityBindingStateContractError(
            "StateContract schema locks do not match existing authoritative locks"
        )
    migration = payload["migration_compatibility"]
    declared_migrations = set(migration.get("migration_lock_refs") or ())
    expected_migrations = {
        str(item.get("lock_id") or item.get("id") or "")
        for item in migration_locks
    }
    if declared_migrations != expected_migrations:
        raise CapabilityBindingStateContractError(
            "StateContract migration lock refs do not match existing authoritative locks"
        )


__all__ = [
    "APPLICATION_REQUIREMENT_SCHEMA",
    "APPLICATION_RESOLUTION_SCHEMA",
    "BINDING_INSTANCE_SCHEMA",
    "BINDING_DEFINITION_SCHEMA",
    "BINDING_DELIVERY_SCHEMA",
    "CAPABILITY_CONTRACT_SCHEMA",
    "ENVIRONMENT_PROFILE_SCHEMA",
    "EVIDENCE_ASSESSMENT_SCHEMA",
    "EVIDENCE_CLAIM_SCHEMA",
    "LOCAL_REVISION_OBSERVATION_SCHEMA",
    "STATE_CONTRACT_SCHEMA",
    "STATE_ACCESS_RELATION_SCHEMA",
    "STATE_LIFECYCLE_OPERATION_SCHEMA",
    "STATE_SPACE_SCHEMA",
    "ApplicationRequirement",
    "ApplicationResolution",
    "BindingDefinition",
    "BindingDelivery",
    "BindingInstance",
    "CanonicalRecord",
    "CapabilityBindingStateContractError",
    "CapabilityContract",
    "EnvironmentProfile",
    "EvidenceAssessment",
    "EvidenceClaim",
    "LocalRevisionObservation",
    "StateContract",
    "StateAccessRelation",
    "StateLifecycleOperation",
    "StateSpace",
    "validate_state_contract_locks",
    "version_satisfies",
]
