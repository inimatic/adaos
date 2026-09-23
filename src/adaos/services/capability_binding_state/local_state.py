"""Append-only local identity store and legacy CRUD compatibility projection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeVar

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    BindingDefinition,
    BindingDelivery,
    BindingInstance,
    CanonicalRecord,
    CapabilityBindingStateContractError,
    CapabilityContract,
    EnvironmentProfile,
    LocalRevisionObservation,
    StateAccessRelation,
    StateContract,
    StateLifecycleOperation,
    StateSpace,
    validate_state_contract_locks,
    version_satisfies,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.resources.local import LocalCrudResourceService
from adaos.services.resources.prototype import PrototypeResourceService


class LocalIdentityConflict(ValueError):
    pass


class StateAttachmentError(ValueError):
    pass


RevisionRecord = BindingInstance | StateSpace
RecordT = TypeVar("RecordT", BindingInstance, StateSpace)


def _ref_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._/-]+", "-", str(value).strip())
    return normalized.strip("-./") or "local"


@dataclass(slots=True)
class LocalIdentityStore:
    root: Path

    @property
    def writer_lock_path(self) -> Path:
        return Path(self.root) / ".identity-writer.lock"

    def append(self, record: RecordT) -> RecordT:
        if not isinstance(record, (BindingInstance, StateSpace)):
            raise LocalIdentityConflict("only revisioned local identity records may be appended")
        kind = "binding-instances" if isinstance(record, BindingInstance) else "state-spaces"
        directory = Path(self.root) / kind / _ref_key(record.stable_ref)
        path = directory / f"{record.revision:020d}.json"
        with mutation_lock(self.writer_lock_path):
            revisions = self._revision_paths(directory)
            if path.is_file():
                value = json.loads(path.read_text(encoding="utf-8"))
                existing = type(record).from_mapping(value)
                if existing != record:
                    raise LocalIdentityConflict(
                        f"revision {record.stable_ref}#{record.revision} already has different content"
                    )
                return existing
            if not revisions:
                if record.revision != 1 or record.predecessor_digest is not None:
                    raise LocalIdentityConflict("first local identity revision must be 1 without predecessor")
            else:
                latest_path = revisions[-1]
                value = json.loads(latest_path.read_text(encoding="utf-8"))
                latest = type(record).from_mapping(value)
                if record.revision != latest.revision + 1:
                    raise LocalIdentityConflict("local identity revisions must be monotonic and contiguous")
                if record.predecessor_digest != latest.digest:
                    raise LocalIdentityConflict("local identity predecessor digest is stale")
                if record.authority_epoch < latest.authority_epoch:
                    raise LocalIdentityConflict("authority epoch cannot decrease")
                if isinstance(record, StateSpace) and record.generation < latest.generation:
                    raise LocalIdentityConflict("state generation cannot decrease")
            atomic_write_json(path, record.to_dict())
        return record

    def latest(self, stable_ref: str, model: type[RecordT]) -> RecordT | None:
        kind = "binding-instances" if model is BindingInstance else "state-spaces"
        directory = Path(self.root) / kind / _ref_key(stable_ref)
        revisions = self._revision_paths(directory)
        if not revisions:
            return None
        value = json.loads(revisions[-1].read_text(encoding="utf-8"))
        record = model.from_mapping(value)
        if record.stable_ref != stable_ref:
            raise LocalIdentityConflict("local identity directory does not match stored stable ref")
        return record

    def by_digest(self, stable_ref: str, digest: str, model: type[RecordT]) -> RecordT:
        kind = "binding-instances" if model is BindingInstance else "state-spaces"
        directory = Path(self.root) / kind / _ref_key(stable_ref)
        for path in self._revision_paths(directory):
            value = json.loads(path.read_text(encoding="utf-8"))
            record = model.from_mapping(value)
            if record.digest == digest:
                if record.stable_ref != stable_ref:
                    raise LocalIdentityConflict(
                        "local identity directory does not match stored stable ref"
                    )
                return record
        raise KeyError(f"{stable_ref}@{digest}")

    def revisions(self, stable_ref: str, model: type[RecordT]) -> tuple[RecordT, ...]:
        """Return the exact immutable history for one local identity."""

        kind = "binding-instances" if model is BindingInstance else "state-spaces"
        directory = Path(self.root) / kind / _ref_key(stable_ref)
        records: list[RecordT] = []
        for path in self._revision_paths(directory):
            record = model.from_mapping(json.loads(path.read_text(encoding="utf-8")))
            if record.stable_ref != stable_ref:
                raise LocalIdentityConflict(
                    "local identity directory does not match stored stable ref"
                )
            records.append(record)
        return tuple(records)

    def observations(
        self,
        *,
        subject_ref: str | None = None,
        subject_revision_digest: str | None = None,
    ) -> tuple[LocalRevisionObservation, ...]:
        """Read immutable operational observations without changing identity state."""

        directory = Path(self.root) / "observations"
        result: list[LocalRevisionObservation] = []
        if not directory.is_dir():
            return ()
        for path in sorted(directory.glob("*.json")):
            record = LocalRevisionObservation.from_mapping(
                json.loads(path.read_text(encoding="utf-8"))
            )
            value = record.to_dict()
            if subject_ref is not None and value["subject_ref"] != subject_ref:
                continue
            if (
                subject_revision_digest is not None
                and value["subject_revision_digest"] != subject_revision_digest
            ):
                continue
            result.append(record)
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item.to_dict()["observed_at"],
                    item.digest,
                ),
            )
        )

    def put_fact(
        self,
        record: StateAccessRelation | StateLifecycleOperation | LocalRevisionObservation,
    ) -> Path:
        kind = {
            StateAccessRelation: "state-access",
            StateLifecycleOperation: "state-operations",
            LocalRevisionObservation: "observations",
        }.get(type(record))
        if kind is None:
            raise LocalIdentityConflict("unsupported local identity fact")
        path = Path(self.root) / kind / f"{record.digest.removeprefix('sha256:')}.json"
        with mutation_lock(self.writer_lock_path):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != record.to_dict():
                    raise LocalIdentityConflict(f"digest collision for local fact {record.digest}")
            else:
                atomic_write_json(path, record.to_dict())
        return path

    @staticmethod
    def _revision_paths(directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        return sorted(path for path in directory.glob("*.json") if path.is_file())


def redacted_graph_record(record: CanonicalRecord) -> dict[str, Any]:
    """Return only non-secret identity and compatibility facts for derived views."""

    value = record.to_dict()
    schema = str(value["schema"])
    allowed: dict[str, tuple[str, ...]] = {
        "adaos.binding.instance.v1": (
            "schema",
            "binding_instance_ref",
            "revision",
            "predecessor_digest",
            "binding_definition_ref",
            "binding_definition_digest",
            "delivery_digest",
            "environment_profile_ref",
            "environment_profile_digest",
            "mode",
            "authority_epoch",
            "revision_digest",
        ),
        "adaos.state.space.v1": (
            "schema",
            "state_space_ref",
            "revision",
            "predecessor_digest",
            "state_contract_ref",
            "state_contract_version",
            "state_contract_digest",
            "custodian_binding_instance_ref",
            "generation",
            "authority_epoch",
            "portability_class",
            "schema_locks",
            "revision_digest",
        ),
        "adaos.state.access_relation.v1": tuple(value),
        "adaos.local_revision.observation.v1": (
            "schema",
            "observation_ref",
            "subject_kind",
            "subject_ref",
            "subject_revision_digest",
            "observation_kind",
            "status",
            "observed_at",
            "observation_digest",
        ),
    }
    fields = allowed.get(schema)
    if fields is None:
        raise LocalIdentityConflict(f"record is not approved for graph projection: {schema}")
    return {field: value[field] for field in fields if field in value}


def calculate_effective_guarantees(
    *,
    state_contract: StateContract,
    binding_definition: BindingDefinition,
    binding_instance: BindingInstance,
    state_space: StateSpace,
    environment_profile: EnvironmentProfile,
    observations: Iterable[LocalRevisionObservation] = (),
) -> dict[str, tuple[str, ...]]:
    state = state_contract.to_dict()
    definition = binding_definition.to_dict()
    instance = binding_instance.to_dict()
    space = state_space.to_dict()
    profile = environment_profile.to_dict()
    if instance["binding_definition_digest"] != binding_definition.digest:
        raise StateAttachmentError("BindingInstance pins another BindingDefinition digest")
    if instance["environment_profile_digest"] != environment_profile.digest:
        raise StateAttachmentError("BindingInstance pins another EnvironmentProfile digest")
    if space["state_contract_digest"] != state_contract.digest:
        raise StateAttachmentError("StateSpace pins another StateContract digest")
    if space["custodian_binding_instance_ref"] != instance["binding_instance_ref"]:
        raise StateAttachmentError("StateSpace custodian does not match BindingInstance")
    if space["portability_class"] != state["portability_class"]:
        raise StateAttachmentError("StateSpace portability differs from StateContract")
    supports = [
        item
        for item in definition["state_support"]
        if item["state_contract_ref"] == state["state_contract_ref"]
        and version_satisfies(state["version"], item["contract_range"])
    ]
    if len(supports) != 1:
        raise StateAttachmentError("BindingDefinition has no exact compatible state support")
    binding_guarantees = supports[0]["guarantees"]
    result: dict[str, tuple[str, ...]] = {}
    for dimension in ("consistency", "durability", "isolation"):
        values = (
            set(state["guarantees"][dimension])
            & set(binding_guarantees[dimension])
            & set(profile["guarantees"][dimension])
        )
        result[dimension] = tuple(sorted(values))
    operational: set[str] = set()
    matching_observations = sorted(
        (
            item.to_dict()
            for item in observations
            if item.to_dict()["subject_kind"] == "state_space"
            and item.to_dict()["subject_ref"] == state_space.stable_ref
            and item.to_dict()["subject_revision_digest"] == state_space.digest
            and item.to_dict()["observation_kind"] in {"backup", "capacity"}
        ),
        key=lambda item: (item["observed_at"], item["observation_digest"]),
    )
    latest: dict[str, Mapping[str, Any]] = {}
    for item in matching_observations:
        latest[str(item["observation_kind"])] = item
    for kind, item in latest.items():
        operational.add(f"{kind}:{item['status']}")
        details = item.get("details") if isinstance(item.get("details"), Mapping) else {}
        if kind == "backup" and details.get("restore_tested") is True:
            operational.add("backup:restore_tested")
        if kind == "capacity" and (
            "available_bytes" in details or "used_bytes" in details
        ):
            operational.add("capacity:observed")
    if operational:
        result["operational"] = tuple(sorted(operational))
    return result


def validate_state_attachment(
    *,
    capability_contract: CapabilityContract,
    port_id: str,
    state_contract: StateContract,
    binding_definition: BindingDefinition,
    binding_instance: BindingInstance,
    state_space: StateSpace,
    environment_profile: EnvironmentProfile,
    relation: StateAccessRelation,
) -> dict[str, tuple[str, ...]]:
    capability = capability_contract.to_dict()
    ports = [item for item in capability["state_ports"] if item["port_id"] == port_id]
    if len(ports) != 1:
        raise StateAttachmentError(f"unknown or duplicate state port: {port_id}")
    port = ports[0]
    state = state_contract.to_dict()
    relation_value = relation.to_dict()
    if port["contract_ref"] != state["state_contract_ref"] or not version_satisfies(
        state["version"], port["contract_range"]
    ):
        raise StateAttachmentError("state port contract range is not satisfied")
    if relation_value["access"] != port["access"]:
        raise StateAttachmentError("state access relation differs from port access")
    if relation_value["binding_instance_revision_digest"] != binding_instance.digest:
        raise StateAttachmentError("state relation pins a stale BindingInstance revision")
    if relation_value["state_space_revision_digest"] != state_space.digest:
        raise StateAttachmentError("state relation pins a stale StateSpace revision")
    effective = calculate_effective_guarantees(
        state_contract=state_contract,
        binding_definition=binding_definition,
        binding_instance=binding_instance,
        state_space=state_space,
        environment_profile=environment_profile,
    )
    requirements = port["requirements"]
    mapping = {
        "consistency_at_least": "consistency",
        "durability": "durability",
        "isolation": "isolation",
    }
    missing = [
        f"{requirement}={requirements[requirement]}"
        for requirement, guarantee in mapping.items()
        if requirements.get(requirement) not in effective[guarantee]
    ]
    authority = requirements.get("mutation_authority")
    if authority:
        profile_authorities = set(environment_profile.to_dict()["authorities"])
        if authority not in profile_authorities and not str(
            state_space.to_dict()["mutation_authority_ref"]
        ).startswith(f"{authority}:"):
            missing.append(f"mutation_authority={authority}")
    if missing:
        raise StateAttachmentError(
            "state port requirements are not included in effective guarantees: "
            + ", ".join(missing)
        )
    return effective


@dataclass(frozen=True, slots=True)
class LegacyCrudProjection:
    binding_instance: BindingInstance
    state_space: StateSpace
    relations: tuple[StateAccessRelation, ...]
    source_record_digest: str


@dataclass(frozen=True, slots=True)
class StagedAuthorityTransition:
    binding_instance: BindingInstance
    state_space: StateSpace
    relations: tuple[StateAccessRelation, ...]


def stage_authority_transition(
    store: LocalIdentityStore,
    *,
    binding_instance: BindingInstance,
    state_space: StateSpace,
    relations: tuple[StateAccessRelation, ...],
) -> StagedAuthorityTransition:
    """Append inactive revisions for one future single-writer authority epoch."""

    if state_space.to_dict()["custodian_binding_instance_ref"] != binding_instance.stable_ref:
        raise LocalIdentityConflict("StateSpace is not in the BindingInstance custody")
    next_epoch = max(binding_instance.authority_epoch, state_space.authority_epoch) + 1
    binding_value = binding_instance.to_dict()
    next_binding = BindingInstance.create(
        binding_instance_ref=binding_instance.stable_ref,
        revision=binding_instance.revision + 1,
        predecessor_digest=binding_instance.digest,
        workspace_ref=binding_value["workspace_ref"],
        tenant_ref=binding_value.get("tenant_ref"),
        binding_definition_ref=binding_value["binding_definition_ref"],
        binding_definition_digest=binding_value["binding_definition_digest"],
        delivery_digest=binding_value["delivery_digest"],
        environment_profile_ref=binding_value["environment_profile_ref"],
        environment_profile_digest=binding_value["environment_profile_digest"],
        mode=binding_value["mode"],
        local_binding_ref=binding_value["local_binding_ref"],
        authority_epoch=next_epoch,
    )
    store.append(next_binding)

    state_value = state_space.to_dict()
    next_space = StateSpace.create(
        state_space_ref=state_space.stable_ref,
        revision=state_space.revision + 1,
        predecessor_digest=state_space.digest,
        state_contract_ref=state_value["state_contract_ref"],
        state_contract_version=state_value["state_contract_version"],
        state_contract_digest=state_value["state_contract_digest"],
        workspace_ref=state_value["workspace_ref"],
        tenant_ref=state_value.get("tenant_ref"),
        logical_owner_ref=state_value["logical_owner_ref"],
        lifecycle_authority_ref=state_value["lifecycle_authority_ref"],
        custodian_binding_instance_ref=next_binding.stable_ref,
        mutation_authority_ref=state_value["mutation_authority_ref"],
        locator_ref=state_value["locator_ref"],
        generation=state_space.generation,
        authority_epoch=next_epoch,
        portability_class=state_value["portability_class"],
        schema_locks=state_value["schema_locks"],
    )
    store.append(next_space)

    next_relations: list[StateAccessRelation] = []
    for relation in relations:
        relation_value = relation.to_dict()
        if (
            relation_value["binding_instance_ref"] != binding_instance.stable_ref
            or relation_value["state_space_ref"] != state_space.stable_ref
        ):
            raise LocalIdentityConflict("state access relation belongs to another attachment")
        next_relation = StateAccessRelation.create(
            relation_ref=relation.stable_ref,
            binding_instance_ref=next_binding.stable_ref,
            binding_instance_revision_digest=next_binding.digest,
            state_space_ref=next_space.stable_ref,
            state_space_revision_digest=next_space.digest,
            port_id=relation_value["port_id"],
            access=relation_value["access"],
        )
        store.put_fact(next_relation)
        next_relations.append(next_relation)
    return StagedAuthorityTransition(next_binding, next_space, tuple(next_relations))


@dataclass(slots=True)
class LegacyCrudProjector:
    service: LocalCrudResourceService
    store: LocalIdentityStore

    def project(
        self,
        resource_type: str,
        *,
        workspace_ref: str,
        tenant_ref: str | None,
        capability_contract: CapabilityContract,
        state_contract: StateContract,
        binding_definition: BindingDefinition,
        delivery: BindingDelivery,
        environment_profile: EnvironmentProfile,
        mode: str = "production",
    ) -> LegacyCrudProjection:
        snapshot = self.service.snapshot(resource_type)
        if snapshot is None:
            raise KeyError(resource_type)
        if delivery.binding_definition_digest != binding_definition.digest:
            raise CapabilityBindingStateContractError(
                "BindingDelivery does not deliver the selected BindingDefinition"
            )
        definition = snapshot.get("definition")
        if not isinstance(definition, Mapping):
            raise LocalIdentityConflict("legacy CRUD snapshot has no definition")
        owner_ref = str(snapshot.get("owner_ref") or "")
        authority = definition.get("authority")
        if not owner_ref or not isinstance(authority, Mapping):
            raise LocalIdentityConflict("legacy CRUD ownership or authority is missing")
        record_schema = definition.get("record_schema")
        record_schema_ref = str(definition.get("record_schema_ref") or "")
        if not isinstance(record_schema, Mapping) or not record_schema_ref:
            raise LocalIdentityConflict("legacy CRUD record schema lock is missing")
        validate_state_contract_locks(
            state_contract,
            schema_locks=(
                {
                    "lock_id": record_schema_ref,
                    "digest": canonical_payload_digest(dict(record_schema)),
                },
            ),
        )
        workspace_slug = _slug(workspace_ref)
        resource_slug = _slug(resource_type)
        binding_ref = f"binding-instance:{workspace_slug}/{resource_slug}"
        state_ref = f"state-space:{workspace_slug}/{resource_slug}"
        previous_binding = self.store.latest(binding_ref, BindingInstance)
        binding_values = {
            "workspace_ref": workspace_ref,
            "tenant_ref": tenant_ref,
            "binding_definition_ref": binding_definition.binding_definition_ref,
            "binding_definition_digest": binding_definition.digest,
            "delivery_digest": delivery.digest,
            "environment_profile_ref": environment_profile.profile_ref,
            "environment_profile_digest": environment_profile.digest,
            "mode": mode,
            "local_binding_ref": f"local-crud:{_slug(str(authority.get('binding') or 'local'))}",
            "authority_epoch": previous_binding.authority_epoch if previous_binding else 1,
        }
        binding = self._binding_revision(binding_ref, previous_binding, binding_values)
        self.store.append(binding)

        previous_space = self.store.latest(state_ref, StateSpace)
        state_value = state_contract.to_dict()
        space_values = {
            "state_contract_ref": state_contract.state_contract_ref,
            "state_contract_version": state_contract.version,
            "state_contract_digest": state_contract.digest,
            "workspace_ref": workspace_ref,
            "tenant_ref": tenant_ref,
            "logical_owner_ref": owner_ref,
            "lifecycle_authority_ref": owner_ref,
            "custodian_binding_instance_ref": binding.stable_ref,
            "mutation_authority_ref": workspace_ref,
            "locator_ref": f"local-resource-registry:{resource_slug}",
            "generation": int(snapshot.get("generation") or 1),
            "authority_epoch": previous_space.authority_epoch if previous_space else 1,
            "portability_class": state_value["portability_class"],
            "schema_locks": state_value["schema_locks"],
        }
        state_space = self._state_revision(state_ref, previous_space, space_values)
        self.store.append(state_space)

        relations: list[StateAccessRelation] = []
        for port in capability_contract.to_dict()["state_ports"]:
            if port["contract_ref"] != state_contract.state_contract_ref:
                continue
            relation = StateAccessRelation.create(
                relation_ref=f"state-access:{workspace_slug}/{resource_slug}/{_slug(port['port_id'])}",
                binding_instance_ref=binding.stable_ref,
                binding_instance_revision_digest=binding.digest,
                state_space_ref=state_space.stable_ref,
                state_space_revision_digest=state_space.digest,
                port_id=port["port_id"],
                access=port["access"],
            )
            self.store.put_fact(relation)
            relations.append(relation)
        return LegacyCrudProjection(
            binding_instance=binding,
            state_space=state_space,
            relations=tuple(relations),
            source_record_digest=canonical_payload_digest(snapshot),
        )

    @staticmethod
    def _binding_revision(
        stable_ref: str,
        previous: BindingInstance | None,
        values: Mapping[str, Any],
    ) -> BindingInstance:
        if previous is not None and LegacyCrudProjector._same_revision_values(previous, values):
            return previous
        return BindingInstance.create(
            binding_instance_ref=stable_ref,
            revision=previous.revision + 1 if previous else 1,
            predecessor_digest=previous.digest if previous else None,
            **dict(values),
        )

    @staticmethod
    def _state_revision(
        stable_ref: str,
        previous: StateSpace | None,
        values: Mapping[str, Any],
    ) -> StateSpace:
        if previous is not None and LegacyCrudProjector._same_revision_values(previous, values):
            return previous
        return StateSpace.create(
            state_space_ref=stable_ref,
            revision=previous.revision + 1 if previous else 1,
            predecessor_digest=previous.digest if previous else None,
            **dict(values),
        )

    @staticmethod
    def _same_revision_values(record: RevisionRecord, values: Mapping[str, Any]) -> bool:
        payload = record.to_dict()
        for key, value in values.items():
            if value is None and key not in payload:
                continue
            if payload.get(key) != value:
                return False
        return True


@dataclass(slots=True)
class PrototypeCrudProjector:
    """Project Builder Preview storage without sharing production state identity."""

    service: PrototypeResourceService
    store: LocalIdentityStore

    def project(
        self,
        resource_type: str,
        *,
        workspace_ref: str,
        tenant_ref: str | None,
        capability_contract: CapabilityContract,
        state_contract: StateContract,
        binding_definition: BindingDefinition,
        delivery: BindingDelivery,
        environment_profile: EnvironmentProfile,
        mode: str = "simulation",
    ) -> LegacyCrudProjection:
        if mode not in {"simulation", "sandbox"}:
            raise LocalIdentityConflict("prototype projection mode must be simulation or sandbox")
        snapshot = self.service.snapshot(resource_type)
        if snapshot is None:
            raise KeyError(resource_type)
        if delivery.binding_definition_digest != binding_definition.digest:
            raise CapabilityBindingStateContractError(
                "BindingDelivery does not deliver the selected BindingDefinition"
            )
        definition = snapshot.get("definition")
        if not isinstance(definition, Mapping):
            raise LocalIdentityConflict("prototype CRUD snapshot has no definition")
        authority = definition.get("authority")
        owner_ref = str(snapshot.get("project_ref") or "")
        if not owner_ref or not isinstance(authority, Mapping):
            raise LocalIdentityConflict("prototype CRUD ownership or authority is missing")
        if str(authority.get("provider") or "") != "prototype":
            raise LocalIdentityConflict("prototype CRUD authority must use the prototype provider")
        record_schema = definition.get("record_schema")
        record_schema_ref = str(definition.get("record_schema_ref") or "")
        if not isinstance(record_schema, Mapping) or not record_schema_ref:
            raise LocalIdentityConflict("prototype CRUD record schema lock is missing")
        validate_state_contract_locks(
            state_contract,
            schema_locks=(
                {
                    "lock_id": record_schema_ref,
                    "digest": canonical_payload_digest(dict(record_schema)),
                },
            ),
        )

        workspace_slug = _slug(workspace_ref)
        resource_slug = _slug(resource_type)
        identity_segment = "preview" if mode == "simulation" else "sandbox"
        binding_ref = f"binding-instance:{workspace_slug}/{identity_segment}/{resource_slug}"
        state_ref = f"state-space:{workspace_slug}/{identity_segment}/{resource_slug}"
        previous_binding = self.store.latest(binding_ref, BindingInstance)
        binding = LegacyCrudProjector._binding_revision(
            binding_ref,
            previous_binding,
            {
                "workspace_ref": workspace_ref,
                "tenant_ref": tenant_ref,
                "binding_definition_ref": binding_definition.binding_definition_ref,
                "binding_definition_digest": binding_definition.digest,
                "delivery_digest": delivery.digest,
                "environment_profile_ref": environment_profile.profile_ref,
                "environment_profile_digest": environment_profile.digest,
                "mode": mode,
                "local_binding_ref": (
                    f"prototype-resource:{_slug(str(authority.get('binding') or 'preview'))}"
                ),
                "authority_epoch": previous_binding.authority_epoch if previous_binding else 1,
            },
        )
        self.store.append(binding)

        previous_space = self.store.latest(state_ref, StateSpace)
        state_value = state_contract.to_dict()
        state_space = LegacyCrudProjector._state_revision(
            state_ref,
            previous_space,
            {
                "state_contract_ref": state_contract.state_contract_ref,
                "state_contract_version": state_contract.version,
                "state_contract_digest": state_contract.digest,
                "workspace_ref": workspace_ref,
                "tenant_ref": tenant_ref,
                "logical_owner_ref": owner_ref,
                "lifecycle_authority_ref": owner_ref,
                "custodian_binding_instance_ref": binding.stable_ref,
                "mutation_authority_ref": workspace_ref,
                "locator_ref": f"prototype-resource:{resource_slug}",
                "generation": int(snapshot.get("generation") or 1),
                "authority_epoch": previous_space.authority_epoch if previous_space else 1,
                "portability_class": state_value["portability_class"],
                "schema_locks": state_value["schema_locks"],
            },
        )
        self.store.append(state_space)

        relations: list[StateAccessRelation] = []
        for port in capability_contract.to_dict()["state_ports"]:
            if port["contract_ref"] != state_contract.state_contract_ref:
                continue
            relation = StateAccessRelation.create(
                relation_ref=(
                    f"state-access:{workspace_slug}/{identity_segment}/{resource_slug}/{_slug(port['port_id'])}"
                ),
                binding_instance_ref=binding.stable_ref,
                binding_instance_revision_digest=binding.digest,
                state_space_ref=state_space.stable_ref,
                state_space_revision_digest=state_space.digest,
                port_id=port["port_id"],
                access=port["access"],
            )
            self.store.put_fact(relation)
            relations.append(relation)
        return LegacyCrudProjection(
            binding_instance=binding,
            state_space=state_space,
            relations=tuple(relations),
            source_record_digest=canonical_payload_digest(snapshot),
        )


__all__ = [
    "LegacyCrudProjection",
    "LegacyCrudProjector",
    "PrototypeCrudProjector",
    "LocalIdentityConflict",
    "LocalIdentityStore",
    "StateAttachmentError",
    "StagedAuthorityTransition",
    "calculate_effective_guarantees",
    "redacted_graph_record",
    "stage_authority_transition",
    "validate_state_attachment",
]
