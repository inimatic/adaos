"""Shared semantic index over immutable artifacts in the source registry.

The registry remains the existing Git/package distribution system.  This
projection gives portable CBS records independent identities without creating
another package manager or copying installation-local authority into shared
storage.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    EvidenceClaim,
    StateContract,
)
from adaos.services.applications.cbs import ApplicationCBSService
from adaos.services.applications.cbs_admission import (
    NativeApplicationCBSAdmissionService,
)
from adaos.services.artifact_pipeline.cbs_authoring import (
    BINDING_OUTPUT_PATH,
    CAPABILITY_OUTPUT_PATH,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .catalog import PortableContractCatalog, portable_record_identity


SEMANTIC_REGISTRY_SCHEMA = "adaos.semantic_registry.index.v1"
SEMANTIC_APPLICATION_RELEASE_SCHEMA = (
    "adaos.semantic_registry.application_release.v1"
)
SEMANTIC_REGISTRY_DIRECTORY = "semantic"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODEL_BY_SCHEMA: dict[str, type[CanonicalRecord]] = {
    CAPABILITY_CONTRACT_SCHEMA: CapabilityContract,
    STATE_CONTRACT_SCHEMA: StateContract,
    BINDING_DEFINITION_SCHEMA: BindingDefinition,
    BINDING_DELIVERY_SCHEMA: BindingDelivery,
    EVIDENCE_CLAIM_SCHEMA: EvidenceClaim,
}


class SemanticRegistryProjectionError(ValueError):
    """A shared semantic projection would be ambiguous or non-portable."""


def _digest_token(value: str) -> str:
    digest = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(digest):
        raise SemanticRegistryProjectionError("canonical sha256 digest is required")
    return digest.removeprefix("sha256:")


def _canonical_record(value: Mapping[str, Any]) -> CanonicalRecord:
    schema = str(value.get("schema") or "")
    model = _MODEL_BY_SCHEMA.get(schema)
    if model is None:
        raise SemanticRegistryProjectionError(
            f"semantic registry does not support portable schema: {schema or '<missing>'}"
        )
    record = model.from_mapping(value)
    if isinstance(record, EvidenceClaim):
        payload = record.to_dict()
        redaction = payload.get("redaction")
        if (
            payload.get("portability_scope") != "portable"
            or not isinstance(redaction, Mapping)
            or redaction.get("portable") is not True
        ):
            raise SemanticRegistryProjectionError(
                "only explicitly portable EvidenceClaims may enter the shared registry"
            )
    return record


def _json_archive_member(archive_bytes: bytes, name: str) -> Mapping[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
            value = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise SemanticRegistryProjectionError(
            f"verified provider package has no valid {name}"
        ) from exc
    if not isinstance(value, Mapping):
        raise SemanticRegistryProjectionError(f"{name} must contain an object")
    return value


def _empty_index() -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": SEMANTIC_REGISTRY_SCHEMA,
        "identities": {},
        "records": {},
        "application_releases": {},
    }
    value["index_digest"] = canonical_payload_digest(value)
    return value


def _validate_index(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if result.get("schema") != SEMANTIC_REGISTRY_SCHEMA:
        raise SemanticRegistryProjectionError("unsupported semantic registry index")
    for field in ("identities", "records", "application_releases"):
        if not isinstance(result.get(field), Mapping):
            raise SemanticRegistryProjectionError(
                f"semantic registry index {field} must be an object"
            )
        result[field] = dict(result[field])
    expected = str(result.get("index_digest") or "")
    unsigned = dict(result)
    unsigned.pop("index_digest", None)
    if expected != canonical_payload_digest(unsigned):
        raise SemanticRegistryProjectionError("semantic registry index digest mismatch")
    return result


@dataclass(slots=True)
class SemanticRegistryProjection:
    """Publish and consume the Git-backed semantic registry projection."""

    registry_root: Path
    state_dir: Path

    @property
    def root(self) -> Path:
        return Path(self.registry_root).resolve() / SEMANTIC_REGISTRY_DIRECTORY

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    @property
    def lock_path(self) -> Path:
        # Mutation locks are node-local runtime state and must never be committed.
        return (
            Path(self.state_dir).resolve()
            / "capability-binding-state"
            / "semantic-registry-projection.lock"
        )

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return _empty_index()
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticRegistryProjectionError(
                "cannot read semantic registry index"
            ) from exc
        if not isinstance(value, Mapping):
            raise SemanticRegistryProjectionError(
                "semantic registry index must be an object"
            )
        return _validate_index(value)

    def _record_path(self, digest: str) -> Path:
        token = _digest_token(digest)
        return self.root / "records" / "sha256" / token[:2] / f"{token}.json"

    def _application_path(self, project_id: str, release_digest: str) -> Path:
        token = _digest_token(release_digest)
        return self.root / "applications" / str(project_id) / f"{token}.json"

    @staticmethod
    def _portable_records_from_packages(
        plan: ReleasePlan,
        package_store: ContentAddressedPackageStore,
    ) -> dict[str, CanonicalRecord]:
        records: dict[str, CanonicalRecord] = {}
        for package in plan.packages:
            archive_bytes, verified = package_store.read_verified(package.digest)
            if verified.ref != package:
                raise SemanticRegistryProjectionError(
                    f"verified package identity changed: {package.key}"
                )
            if not verified.binding_deliveries:
                continue
            capability = CapabilityContract.from_mapping(
                _json_archive_member(archive_bytes, CAPABILITY_OUTPUT_PATH)
            )
            binding = BindingDefinition.from_mapping(
                _json_archive_member(archive_bytes, BINDING_OUTPUT_PATH)
            )
            records[capability.digest] = capability
            records[binding.digest] = binding
            for delivery in verified.binding_deliveries:
                if (
                    delivery.binding_definition_digest != binding.digest
                    or delivery.package_digest != package.digest
                ):
                    raise SemanticRegistryProjectionError(
                        f"provider delivery is not bound to its exact package: {package.key}"
                    )
                records[delivery.digest] = delivery
        return records

    def prepare_release(
        self,
        plan: ReleasePlan,
        *,
        package_store: ContentAddressedPackageStore,
    ) -> dict[str, Any]:
        """Write an exact CBS release projection ready for the same Git commit.

        No push or authority switch happens here.  The caller commits ``semantic``
        together with the already verified source closure.
        """

        release = plan.release.seal()
        release_digest = str(release.release_digest)
        admission_service = NativeApplicationCBSAdmissionService(Path(self.state_dir))
        admission = admission_service.find_by_project_release(release_digest)
        if admission is None:
            raise SemanticRegistryProjectionError(
                "exact ProjectRelease has no CBS admission; semantic publication is fail-closed"
            )
        if (
            admission.get("status") != "admitted"
            or int(admission.get("requirements_total") or 0)
            != int(admission.get("requirements_resolved") or -1)
        ):
            raise SemanticRegistryProjectionError(
                "exact ProjectRelease CBS admission is unresolved"
            )
        application_ref = str(admission.get("application_ref") or "")
        compilation_digest = str(admission.get("compilation_digest") or "")
        compilation = ApplicationCBSService(Path(self.state_dir)).inspect_digest(
            application_ref, compilation_digest
        )
        if compilation is None:
            raise SemanticRegistryProjectionError(
                "exact CBS compilation referenced by admission is unavailable"
            )
        if (
            compilation.get("semantic_revision_digest")
            != admission.get("semantic_revision_digest")
        ):
            raise SemanticRegistryProjectionError(
                "CBS admission and compilation semantic revisions differ"
            )

        requirements = [
            ApplicationRequirement.from_mapping(item).to_dict()
            for item in compilation.get("requirements") or ()
        ]
        records = self._portable_records_from_packages(plan, package_store)
        selected_digests: set[str] = set()
        for raw_resolution in admission.get("resolutions") or ():
            if not isinstance(raw_resolution, Mapping):
                raise SemanticRegistryProjectionError(
                    "CBS admission contains a malformed resolution"
                )
            for item in raw_resolution.get("selected_contracts") or ():
                if isinstance(item, Mapping):
                    selected_digests.add(str(item.get("digest") or ""))
            binding = raw_resolution.get("binding_definition")
            if isinstance(binding, Mapping):
                selected_digests.add(str(binding.get("digest") or ""))
            delivery = raw_resolution.get("delivery")
            if isinstance(delivery, Mapping):
                selected_digests.add(str(delivery.get("delivery_digest") or ""))
        selected_digests.discard("")

        local_catalog = admission_service.portable_catalog
        for digest in sorted(selected_digests - set(records)):
            try:
                value = local_catalog.load_mapping(digest)
            except (KeyError, OSError, ValueError) as exc:
                raise SemanticRegistryProjectionError(
                    f"selected portable CBS record is unavailable: {digest}"
                ) from exc
            record = _canonical_record(value)
            if record.digest != digest:
                raise SemanticRegistryProjectionError(
                    f"selected portable CBS record digest changed: {digest}"
                )
            records[digest] = record

        missing = sorted(selected_digests - set(records))
        if missing:
            raise SemanticRegistryProjectionError(
                "selected CBS records are absent from publication: " + ", ".join(missing)
            )

        portable_evidence_digests: list[str] = []
        omitted_local_evidence = 0
        for raw_claim in admission.get("evidence") or ():
            if not isinstance(raw_claim, Mapping):
                raise SemanticRegistryProjectionError(
                    "CBS admission contains a malformed EvidenceClaim"
                )
            redaction = raw_claim.get("redaction")
            if (
                raw_claim.get("portability_scope") != "portable"
                or not isinstance(redaction, Mapping)
                or redaction.get("portable") is not True
            ):
                omitted_local_evidence += 1
                continue
            claim = _canonical_record(raw_claim)
            records[claim.digest] = claim
            portable_evidence_digests.append(claim.digest)

        artifacts_by_schema: dict[str, list[str]] = {}
        for record in records.values():
            artifacts_by_schema.setdefault(record.SCHEMA, []).append(record.digest)
        artifacts_by_schema = {
            schema: sorted(set(digests))
            for schema, digests in sorted(artifacts_by_schema.items())
        }
        packages = [item.to_dict() for item in sorted(plan.packages, key=lambda p: p.key)]
        application_release: dict[str, Any] = {
            "schema": SEMANTIC_APPLICATION_RELEASE_SCHEMA,
            "project_id": release.project_id,
            "version": release.version,
            "application_ref": application_ref,
            "project_release_digest": release_digest,
            "source_ref": release.source_ref.to_dict(),
            "compilation_digest": compilation_digest,
            "semantic_revision_digest": str(compilation["semantic_revision_digest"]),
            "requirements": requirements,
            "portable_artifacts": artifacts_by_schema,
            "distribution": {
                "thin": {
                    "requirements_only": True,
                    "registry_required": True,
                },
                "resolved": {
                    "project_release_digest": release_digest,
                    "package_closure": packages,
                    "portable_artifact_digests": sorted(records),
                },
            },
        }
        application_release["projection_digest"] = canonical_payload_digest(
            application_release
        )

        with mutation_lock(self.lock_path, timeout_s=30.0):
            index = self._read_index()
            identities = index["identities"]
            record_index = index["records"]
            app_index = index["application_releases"]

            pending_records: list[tuple[Path, dict[str, Any]]] = []
            for digest, record in sorted(records.items()):
                payload = record.to_dict()
                identity, revision = portable_record_identity(record)
                identity_key = f"{record.SCHEMA}|{identity}|{revision}"
                previous = identities.get(identity_key)
                if previous is not None and previous != digest:
                    raise SemanticRegistryProjectionError(
                        "portable semantic identity already has different content: "
                        + identity_key
                    )
                path = self._record_path(digest)
                if path.is_file():
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if existing != payload:
                        raise SemanticRegistryProjectionError(
                            f"semantic registry digest collision: {digest}"
                        )
                else:
                    pending_records.append((path, payload))
                relative = path.relative_to(Path(self.registry_root).resolve()).as_posix()
                raw_entry = record_index.get(digest)
                entry = dict(raw_entry) if isinstance(raw_entry, Mapping) else {}
                publishers = set(entry.get("published_by_release_digests") or ())
                publishers.add(release_digest)
                identities[identity_key] = digest
                record_index[digest] = {
                    "schema": record.SCHEMA,
                    "identity": identity,
                    "revision": revision,
                    "path": relative,
                    "published_by_release_digests": sorted(publishers),
                }

            app_path = self._application_path(release.project_id, release_digest)
            if app_path.is_file():
                existing = json.loads(app_path.read_text(encoding="utf-8"))
                if existing != application_release:
                    raise SemanticRegistryProjectionError(
                        "application semantic release is immutable"
                    )
            app_key = f"project:{release.project_id}@{release_digest}"
            app_index[app_key] = {
                "project_id": release.project_id,
                "version": release.version,
                "application_ref": application_ref,
                "project_release_digest": release_digest,
                "semantic_revision_digest": str(compilation["semantic_revision_digest"]),
                "path": app_path.relative_to(
                    Path(self.registry_root).resolve()
                ).as_posix(),
                "projection_digest": application_release["projection_digest"],
            }
            index.pop("index_digest", None)
            index["index_digest"] = canonical_payload_digest(index)

            for path, payload in pending_records:
                atomic_write_json(path, payload)
            if not app_path.is_file():
                atomic_write_json(app_path, application_release)
            atomic_write_json(self.index_path, index)

        return {
            "schema": "adaos.semantic_registry.publication.v1",
            "status": "prepared",
            "project_id": release.project_id,
            "application_ref": application_ref,
            "project_release_digest": release_digest,
            "semantic_revision_digest": compilation["semantic_revision_digest"],
            "application_projection_digest": application_release["projection_digest"],
            "index_digest": index["index_digest"],
            "record_count": len(records),
            "portable_evidence_count": len(portable_evidence_digests),
            "omitted_local_evidence_count": omitted_local_evidence,
            "paths": [SEMANTIC_REGISTRY_DIRECTORY],
        }

    def import_to_local_catalog(
        self,
        *,
        digests: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Verify shared records and hydrate the node-local portable cache."""

        index = self._read_index()
        selected = (
            sorted({_digest_token(item) for item in digests})
            if digests is not None
            else sorted(_digest_token(item) for item in index["records"])
        )
        catalog = PortableContractCatalog(
            Path(self.state_dir).resolve() / "capability-binding-state" / "portable"
        )
        imported: list[str] = []
        for token in selected:
            digest = f"sha256:{token}"
            raw_entry = index["records"].get(digest)
            if not isinstance(raw_entry, Mapping):
                raise SemanticRegistryProjectionError(
                    f"semantic registry record is not indexed: {digest}"
                )
            relative = Path(str(raw_entry.get("path") or ""))
            path = (Path(self.registry_root).resolve() / relative).resolve()
            registry_root = Path(self.registry_root).resolve()
            if registry_root != path and registry_root not in path.parents:
                raise SemanticRegistryProjectionError(
                    "semantic registry record path escapes registry root"
                )
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SemanticRegistryProjectionError(
                    f"cannot read semantic registry record: {digest}"
                ) from exc
            if not isinstance(value, Mapping):
                raise SemanticRegistryProjectionError(
                    f"semantic registry record is not an object: {digest}"
                )
            record = _canonical_record(value)
            if record.digest != digest:
                raise SemanticRegistryProjectionError(
                    f"semantic registry record digest mismatch: {digest}"
                )
            identity, revision = portable_record_identity(record)
            if (
                raw_entry.get("schema") != record.SCHEMA
                or raw_entry.get("identity") != identity
                or raw_entry.get("revision") != revision
            ):
                raise SemanticRegistryProjectionError(
                    f"semantic registry record metadata mismatch: {digest}"
                )
            catalog.put(record)
            imported.append(digest)
        return {
            "schema": "adaos.semantic_registry.import.v1",
            "status": "imported",
            "index_digest": index["index_digest"],
            "record_count": len(imported),
            "record_digests": imported,
        }


__all__ = [
    "SEMANTIC_APPLICATION_RELEASE_SCHEMA",
    "SEMANTIC_REGISTRY_DIRECTORY",
    "SEMANTIC_REGISTRY_SCHEMA",
    "SemanticRegistryProjection",
    "SemanticRegistryProjectionError",
]
