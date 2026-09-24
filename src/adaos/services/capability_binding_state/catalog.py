"""Content-addressed local cache for portable CBS records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TypeVar

from adaos.domain.capability_binding_state import CanonicalRecord
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class PortableContractConflict(ValueError):
    pass


RecordT = TypeVar("RecordT", bound=CanonicalRecord)


def _identity(record: CanonicalRecord) -> tuple[str, str]:
    value = record.to_dict()
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
    if value.get("schema") == "adaos.binding.delivery.v1":
        package = value["package"]
        return (
            f"{value['binding_definition_ref']}@{value['binding_definition_digest']}",
            str(package["digest"]),
        )
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
