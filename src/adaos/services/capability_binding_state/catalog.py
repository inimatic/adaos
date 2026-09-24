"""Content-addressed local cache for portable CBS records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TypeVar

from packaging.version import Version

from adaos.domain.capability_binding_state import (
    BINDING_DEFINITION_SCHEMA,
    BINDING_DELIVERY_SCHEMA,
    CAPABILITY_CONTRACT_SCHEMA,
    BindingDefinition,
    BindingDelivery,
    CanonicalRecord,
    CapabilityContract,
    version_satisfies,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class PortableContractConflict(ValueError):
    pass


RecordT = TypeVar("RecordT", bound=CanonicalRecord)


def _identity(record: CanonicalRecord) -> tuple[str, str]:
    value = record.to_dict()
    # Delivery is package-specific metadata for an otherwise stable binding
    # definition.  It contains ``binding_definition_ref`` too, so it must be
    # classified before the generic portable-record fields below.  Otherwise
    # every delivery is incorrectly indexed as ``<binding>@1`` and relocating
    # the same implementation to another package looks like a semantic
    # BindingDefinition mutation.
    if value.get("schema") == "adaos.binding.delivery.v1":
        package = value["package"]
        return (
            f"{value['binding_definition_ref']}@{value['binding_definition_digest']}",
            str(package["digest"]),
        )
    # Prefer the record's own stable identity over references to records it
    # depends on.  BindingDefinition, for example, contains capability_ref but
    # must never collide with the CapabilityContract it realizes.
    for field in (
        "binding_definition_ref",
        "claim_ref",
        "profile_ref",
        "requirement_ref",
        "state_contract_ref",
        "capability_ref",
    ):
        if field in value:
            suffix = str(value.get("version") or value.get("claim_kind") or "1")
            return str(value[field]), suffix
    raise PortableContractConflict(f"unsupported portable record: {value.get('schema')}")


def _key(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class PortableContractCatalog:
    root: Path

    @property
    def records_root(self) -> Path:
        return Path(self.root) / "records"

    @property
    def index_path(self) -> Path:
        return Path(self.root) / "index.json"

    @property
    def writer_lock_path(self) -> Path:
        return Path(self.root) / ".catalog.lock"

    def put(self, record: CanonicalRecord) -> Path:
        payload = record.to_dict()
        identity, revision = _identity(record)
        digest = record.digest
        path = self.records_root / digest.removeprefix("sha256:")[:2] / f"{digest.removeprefix('sha256:')}.json"
        with mutation_lock(self.writer_lock_path):
            index = self._read_index()
            identity_key = f"{identity}@{revision}"
            previous = index["identities"].get(identity_key)
            if previous is not None and previous != digest:
                raise PortableContractConflict(
                    f"portable identity already has different content: {identity_key}"
                )
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != payload:
                    raise PortableContractConflict(f"digest collision for {digest}")
            else:
                atomic_write_json(path, payload)
            index["identities"][identity_key] = digest
            index["records"][digest] = {
                "schema": payload["schema"],
                "identity": identity,
                "revision": revision,
                "path": path.relative_to(Path(self.root)).as_posix(),
            }
            atomic_write_json(self.index_path, index)
        return path

    def load(self, digest: str, model: type[RecordT]) -> RecordT:
        index = self._read_index()
        entry = index["records"].get(str(digest))
        if not isinstance(entry, Mapping):
            raise KeyError(digest)
        relative = Path(str(entry.get("path") or ""))
        path = (Path(self.root) / relative).resolve()
        root = Path(self.root).resolve()
        if root != path and root not in path.parents:
            raise PortableContractConflict("portable catalog index escapes its root")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise PortableContractConflict("portable catalog record must be an object")
        result = model.from_mapping(value)
        if result.digest != digest:
            raise PortableContractConflict("portable catalog record digest mismatch")
        return result

    def matching_capabilities(
        self, capability_ref: str, contract_range: str
    ) -> tuple[CapabilityContract, ...]:
        """Return verified installed contracts satisfying one semantic requirement.

        The portable catalog is the authority for an identity already installed on
        this node.  Authoring callers need the canonical content, not merely the
        identity, otherwise a generated provider can accidentally redefine the
        same version with different schemas and fail only during admission.
        """

        matches: list[CapabilityContract] = []
        index = self._read_index()
        for digest, raw_entry in index["records"].items():
            if not isinstance(raw_entry, Mapping):
                continue
            if raw_entry.get("schema") != CAPABILITY_CONTRACT_SCHEMA:
                continue
            if str(raw_entry.get("identity") or "") != str(capability_ref):
                continue
            version = str(raw_entry.get("revision") or "")
            if not version_satisfies(version, contract_range):
                continue
            matches.append(self.load(str(digest), CapabilityContract))
        return tuple(
            sorted(matches, key=lambda item: Version(item.version), reverse=True)
        )

    def matching_bindings(
        self, capability_ref: str, capability_version: str
    ) -> tuple[BindingDefinition, ...]:
        """Return portable binding semantics for one exact contract identity."""

        matches: list[BindingDefinition] = []
        index = self._read_index()
        for digest, raw_entry in index["records"].items():
            if (
                not isinstance(raw_entry, Mapping)
                or raw_entry.get("schema") != BINDING_DEFINITION_SCHEMA
            ):
                continue
            binding = self.load(str(digest), BindingDefinition)
            value = binding.to_dict()
            if (
                binding.capability_ref == str(capability_ref)
                and value.get("capability_version") == str(capability_version)
            ):
                matches.append(binding)
        return tuple(
            sorted(
                matches,
                key=lambda item: (
                    Version(str(item.to_dict()["version"])),
                    item.binding_definition_ref,
                ),
                reverse=True,
            )
        )

    def deliveries_for_binding(
        self, binding_definition_digest: str
    ) -> tuple[BindingDelivery, ...]:
        """Return package-specific deliveries for an installed binding digest."""

        matches: list[BindingDelivery] = []
        index = self._read_index()
        for digest, raw_entry in index["records"].items():
            if (
                not isinstance(raw_entry, Mapping)
                or raw_entry.get("schema") != BINDING_DELIVERY_SCHEMA
            ):
                continue
            delivery = self.load(str(digest), BindingDelivery)
            if delivery.binding_definition_digest == str(binding_definition_digest):
                matches.append(delivery)
        return tuple(
            sorted(
                matches,
                key=lambda item: (
                    str(item.to_dict()["package"].get("version") or ""),
                    item.package_digest,
                ),
                reverse=True,
            )
        )

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {
                "schema": "adaos.portable_contract_catalog.v1",
                "identities": {},
                "records": {},
            }
        value = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise PortableContractConflict("portable catalog index must be an object")
        if value.get("schema") != "adaos.portable_contract_catalog.v1":
            raise PortableContractConflict("unsupported portable catalog schema")
        identities = value.get("identities")
        records = value.get("records")
        if not isinstance(identities, Mapping) or not isinstance(records, Mapping):
            raise PortableContractConflict("portable catalog index is malformed")
        return {
            "schema": "adaos.portable_contract_catalog.v1",
            "identities": dict(identities),
            "records": dict(records),
        }
