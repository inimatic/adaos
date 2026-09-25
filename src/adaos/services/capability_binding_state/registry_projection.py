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

from adaos.domain.application import Application, ApplicationRelease
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
from adaos.services.applications.cbs import (
    ApplicationCBSConflict,
    ApplicationCBSService,
)
from adaos.services.applications.cbs_admission import (
    NativeApplicationCBSAdmissionService,
)
from adaos.services.applications.service import (
    ApplicationService,
    ApplicationServiceError,
)
from adaos.services.applications.store import ApplicationStore, ApplicationStoreError
from adaos.services.artifact_pipeline.cbs_authoring import (
    BINDING_OUTPUT_PATH,
    CAPABILITY_OUTPUT_PATH,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .catalog import PortableContractCatalog, portable_record_identity


SEMANTIC_REGISTRY_SCHEMA = "adaos.semantic_registry.index.v1"
SEMANTIC_APPLICATION_RELEASE_SCHEMA = (
    "adaos.semantic_registry.application_release.v1"
)
PUBLIC_APPLICATION_CATALOG_SCHEMA = (
    "adaos.semantic_registry.public_application_catalog.v1"
)
PUBLIC_APPLICATION_RELEASE_SCHEMA = (
    "adaos.semantic_registry.public_application_release.v1"
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


def _empty_application_catalog() -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": PUBLIC_APPLICATION_CATALOG_SCHEMA,
        "applications": {},
        "releases": {},
    }
    value["index_digest"] = canonical_payload_digest(value)
    return value


def _validate_application_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if result.get("schema") != PUBLIC_APPLICATION_CATALOG_SCHEMA:
        raise SemanticRegistryProjectionError(
            "unsupported public Application catalog"
        )
    for field in ("applications", "releases"):
        if not isinstance(result.get(field), Mapping):
            raise SemanticRegistryProjectionError(
                f"public Application catalog {field} must be an object"
            )
        result[field] = dict(result[field])
    expected = str(result.get("index_digest") or "")
    unsigned = dict(result)
    unsigned.pop("index_digest", None)
    if expected != canonical_payload_digest(unsigned):
        raise SemanticRegistryProjectionError(
            "public Application catalog digest mismatch"
        )
    return result


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
    def application_catalog_root(self) -> Path:
        return self.root / "catalog"

    @property
    def application_catalog_path(self) -> Path:
        return self.application_catalog_root / "index.json"

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

    def _read_application_catalog(self) -> dict[str, Any]:
        if not self.application_catalog_path.is_file():
            return _empty_application_catalog()
        try:
            value = json.loads(
                self.application_catalog_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticRegistryProjectionError(
                "cannot read public Application catalog"
            ) from exc
        if not isinstance(value, Mapping):
            raise SemanticRegistryProjectionError(
                "public Application catalog must be an object"
            )
        return _validate_application_catalog(value)

    def _record_path(self, digest: str) -> Path:
        token = _digest_token(digest)
        return self.root / "records" / "sha256" / token[:2] / f"{token}.json"

    def _application_path(self, project_id: str, release_digest: str) -> Path:
        token = _digest_token(release_digest)
        return self.root / "applications" / str(project_id) / f"{token}.json"

    def _public_application_release_path(
        self, application_id: str, release_digest: str
    ) -> Path:
        token = _digest_token(release_digest)
        return (
            self.application_catalog_root
            / "releases"
            / str(application_id)
            / f"{token}.json"
        )

    def prepare_public_application(
        self,
        application: Application | Mapping[str, Any],
        release: ApplicationRelease | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Add one installable stable Application to the shared registry index.

        The record carries only portable product/release authority.  Installation,
        credentials, BindingInstances, StateSpaces, RuntimeSelections, and grants
        remain local to the consuming subnet.
        """

        app = (
            application
            if isinstance(application, Application)
            else Application.from_mapping(application)
        )
        app_release = (
            release
            if isinstance(release, ApplicationRelease)
            else ApplicationRelease.from_mapping(release)
        )
        if app.visibility != "public":
            raise SemanticRegistryProjectionError(
                "only public Applications may enter the shared catalog"
            )
        if (
            app.application_id != app_release.application_id
            or app.publisher_ref != app_release.publisher_ref
            or app.legacy_project_id != app_release.project_release.project_id
        ):
            raise SemanticRegistryProjectionError(
                "public Application and release identities differ"
            )

        semantic_index = self._read_index()
        semantic_key = (
            f"project:{app.legacy_project_id}@{app_release.release_digest}"
        )
        semantic_entry = semantic_index["application_releases"].get(semantic_key)
        if not isinstance(semantic_entry, Mapping):
            raise SemanticRegistryProjectionError(
                "public Application release has no exact semantic projection"
            )

        payload: dict[str, Any] = {
            "schema": PUBLIC_APPLICATION_RELEASE_SCHEMA,
            "application": app.to_dict(),
            "release": app_release.to_dict(),
            "channels": {"stable": app_release.release_digest},
            "semantic": {
                "projection_digest": semantic_entry.get("projection_digest"),
                "path": semantic_entry.get("path"),
            },
        }
        payload["projection_digest"] = canonical_payload_digest(payload)
        path = self._public_application_release_path(
            app.application_id, app_release.release_digest
        )

        with mutation_lock(self.lock_path, timeout_s=30.0):
            catalog = self._read_application_catalog()
            if path.is_file():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise SemanticRegistryProjectionError(
                        "public Application release is unreadable"
                    ) from exc
                if existing != payload:
                    raise SemanticRegistryProjectionError(
                        "public Application release is immutable"
                    )

            relative = path.relative_to(
                Path(self.registry_root).resolve()
            ).as_posix()
            release_key = f"{app.application_id}@{app_release.release_digest}"
            catalog["releases"][release_key] = {
                "application_id": app.application_id,
                "publisher_ref": app.publisher_ref,
                "version": app_release.project_release.version,
                "release_digest": app_release.release_digest,
                "path": relative,
                "projection_digest": payload["projection_digest"],
            }
            catalog["applications"][app.application_id] = {
                "application_id": app.application_id,
                "publisher_ref": app.publisher_ref,
                "application_revision": app.revision,
                "stable_release_digest": app_release.release_digest,
                "release_key": release_key,
                "projection_digest": payload["projection_digest"],
            }
            catalog.pop("index_digest", None)
            catalog["index_digest"] = canonical_payload_digest(catalog)
            if not path.is_file():
                atomic_write_json(path, payload)
            atomic_write_json(self.application_catalog_path, catalog)

        return {
            "schema": "adaos.semantic_registry.public_application_publication.v1",
            "status": "prepared",
            "application_id": app.application_id,
            "application_revision": app.revision,
            "release_digest": app_release.release_digest,
            "projection_digest": payload["projection_digest"],
            "index_digest": catalog["index_digest"],
            "path": relative,
            "paths": [f"{SEMANTIC_REGISTRY_DIRECTORY}/catalog"],
        }

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
        application_store: ApplicationStore | None = None,
        local_publisher_ref: str | None = None,
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
        application_catalog = self.import_public_applications(
            application_store=application_store,
            local_publisher_ref=local_publisher_ref,
        )
        return {
            "schema": "adaos.semantic_registry.import.v1",
            "status": "imported",
            "index_digest": index["index_digest"],
            "record_count": len(imported),
            "record_digests": imported,
            "application_catalog": application_catalog,
        }

    def import_public_applications(
        self,
        *,
        application_store: ApplicationStore | None = None,
        local_publisher_ref: str | None = None,
    ) -> dict[str, Any]:
        """Hydrate installable public Application facts from the same registry.

        The Git registry is a transport/index authority.  Package bytes still
        come from the existing content-addressed package store and are verified
        by the ordinary install pipeline.
        """

        if not self.application_catalog_path.is_file():
            return {
                "schema": "adaos.semantic_registry.public_application_import.v1",
                "status": "not_available",
                "application_count": 0,
                "release_count": 0,
                "applications": [],
            }
        catalog = self._read_application_catalog()
        store = application_store or ApplicationStore(Path(self.state_dir))
        imported: list[dict[str, Any]] = []
        registry_root = Path(self.registry_root).resolve()
        for application_id, raw_application_entry in sorted(
            catalog["applications"].items()
        ):
            if not isinstance(raw_application_entry, Mapping):
                raise SemanticRegistryProjectionError(
                    f"public Application catalog entry is invalid: {application_id}"
                )
            release_key = str(raw_application_entry.get("release_key") or "")
            raw_release_entry = catalog["releases"].get(release_key)
            if not isinstance(raw_release_entry, Mapping):
                raise SemanticRegistryProjectionError(
                    f"public Application release is not indexed: {release_key}"
                )
            relative = Path(str(raw_release_entry.get("path") or ""))
            path = (registry_root / relative).resolve()
            if registry_root != path and registry_root not in path.parents:
                raise SemanticRegistryProjectionError(
                    "public Application release path escapes registry root"
                )
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SemanticRegistryProjectionError(
                    f"cannot read public Application release: {release_key}"
                ) from exc
            if (
                not isinstance(payload, Mapping)
                or payload.get("schema") != PUBLIC_APPLICATION_RELEASE_SCHEMA
            ):
                raise SemanticRegistryProjectionError(
                    f"unsupported public Application release: {release_key}"
                )
            expected_projection = str(payload.get("projection_digest") or "")
            unsigned = dict(payload)
            unsigned.pop("projection_digest", None)
            if (
                expected_projection != canonical_payload_digest(unsigned)
                or raw_release_entry.get("projection_digest")
                != expected_projection
                or raw_application_entry.get("projection_digest")
                != expected_projection
            ):
                raise SemanticRegistryProjectionError(
                    f"public Application projection digest mismatch: {release_key}"
                )
            application = Application.from_mapping(
                payload.get("application") or {}
            )
            release = ApplicationRelease.from_mapping(payload.get("release") or {})
            channels = payload.get("channels")
            stable_digest = (
                str(channels.get("stable") or "")
                if isinstance(channels, Mapping)
                else ""
            )
            if (
                application.application_id != application_id
                or raw_application_entry.get("application_id") != application_id
                or raw_application_entry.get("publisher_ref")
                != application.publisher_ref
                or int(raw_application_entry.get("application_revision") or 0)
                != application.revision
                or raw_application_entry.get("stable_release_digest")
                != stable_digest
                or raw_release_entry.get("application_id") != application_id
                or raw_release_entry.get("publisher_ref")
                != application.publisher_ref
                or raw_release_entry.get("release_digest")
                != release.release_digest
            ):
                raise SemanticRegistryProjectionError(
                    f"public Application catalog metadata mismatch: {application_id}"
                )
            semantic = payload.get("semantic")
            if not isinstance(semantic, Mapping):
                raise SemanticRegistryProjectionError(
                    f"public Application has no semantic projection: {application_id}"
                )
            semantic_path = (
                registry_root / Path(str(semantic.get("path") or ""))
            ).resolve()
            if (
                (registry_root != semantic_path and registry_root not in semantic_path.parents)
                or not semantic_path.is_file()
            ):
                raise SemanticRegistryProjectionError(
                    f"public Application semantic projection is unavailable: {application_id}"
                )
            try:
                semantic_payload = json.loads(
                    semantic_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise SemanticRegistryProjectionError(
                    f"public Application semantic projection is unreadable: {application_id}"
                ) from exc
            if (
                not isinstance(semantic_payload, Mapping)
                or semantic_payload.get("projection_digest")
                != semantic.get("projection_digest")
                or semantic_payload.get("project_release_digest")
                != release.release_digest
                or semantic_payload.get("project_id")
                != release.project_release.project_id
            ):
                raise SemanticRegistryProjectionError(
                    f"public Application semantic projection differs: {application_id}"
                )
            try:
                result = store.import_public_registry_release(
                    application,
                    release,
                    stable_release_digest=stable_digest,
                    local_publisher_ref=local_publisher_ref,
                )
            except ApplicationStoreError as exc:
                raise SemanticRegistryProjectionError(
                    f"public Application import failed for {application_id}: {exc}"
                ) from exc
            try:
                requirement_source = ApplicationCBSService(
                    Path(self.state_dir)
                ).import_semantic_requirement_set(
                    application_id=application_id,
                    semantic_release=semantic_payload,
                )
            except ApplicationCBSConflict as exc:
                raise SemanticRegistryProjectionError(
                    "public Application semantic requirements failed validation: "
                    f"{application_id}: {exc}"
                ) from exc
            local_admission = self._reconcile_installed_admission(
                application=application,
                release=release,
                application_store=store,
                requirement_source=requirement_source,
                local_publisher_ref=local_publisher_ref,
            )
            imported.append(
                {
                    **result,
                    "semantic_requirement_set": {
                        "application_ref": requirement_source["application_ref"],
                        "project_release_digest": requirement_source[
                            "project_release_digest"
                        ],
                        "compilation_digest": requirement_source[
                            "compilation_digest"
                        ],
                        "requirement_set_digest": requirement_source[
                            "requirement_set_digest"
                        ],
                    },
                    "local_admission": local_admission,
                }
            )
        return {
            "schema": "adaos.semantic_registry.public_application_import.v1",
            "status": "imported",
            "index_digest": catalog["index_digest"],
            "application_count": len(imported),
            "release_count": len(catalog["releases"]),
            "applications": imported,
        }

    def _reconcile_installed_admission(
        self,
        *,
        application: Application,
        release: ApplicationRelease,
        application_store: ApplicationStore,
        requirement_source: Mapping[str, Any],
        local_publisher_ref: str | None,
    ) -> dict[str, Any]:
        """Re-admit an already active imported release when its bytes are local."""

        try:
            installation = application_store.get_installation(
                application.application_id
            )
        except FileNotFoundError:
            return {"status": "not_installed"}
        if (
            installation.status != "active"
            or installation.installed_release_digest != release.release_digest
        ):
            return {
                "status": "not_current",
                "installed_release_digest": installation.installed_release_digest,
            }

        try:
            install_access = ApplicationService(
                application_store
            ).ensure_install_access(
                application.application_id,
                release_digest=str(release.release_digest),
                subnet_ref=str(local_publisher_ref or "subnet:local"),
                issuer_ref="system:semantic-registry-reconciliation",
            )
        except (ApplicationServiceError, OSError, ValueError) as exc:
            install_access = {
                "status": "failed",
                "reason": "install_access_reconciliation_failed",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }

        artifact_root = Path(self.state_dir) / "artifact_pipeline"
        try:
            plan = ReleaseRepository(artifact_root / "release-cache").get_release(
                release.project_release.project_id,
                str(release.release_digest),
            )
            packages = ContentAddressedPackageStore(artifact_root / "packages")
            missing = [
                package.digest
                for package in plan.packages
                if not packages.has(package.digest)
            ]
            if missing:
                return {
                    "status": "awaiting_packages",
                    "missing_package_digests": missing,
                    "install_access": install_access,
                }
            subnet = str(local_publisher_ref or "subnet:local").removeprefix(
                "subnet:"
            )
            admission = NativeApplicationCBSAdmissionService(
                Path(self.state_dir)
            ).admit(
                application_ref=str(requirement_source["application_ref"]),
                compilation=requirement_source,
                release_plan=plan,
                package_store=packages,
                workspace_ref=f"workspace:{subnet or 'local'}",
                evidence_context={
                    "application_id": application.application_id,
                    "source": "semantic_registry_reconciliation",
                },
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            return {
                "status": "failed",
                "reason": "installed_cbs_reconciliation_failed",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "install_access": install_access,
            }
        return {
            "status": str(admission.get("status") or "unknown"),
            "admission_digest": admission.get("admission_digest"),
            "requirements_total": admission.get("requirements_total"),
            "requirements_resolved": admission.get("requirements_resolved"),
            "install_access": install_access,
        }


__all__ = [
    "SEMANTIC_APPLICATION_RELEASE_SCHEMA",
    "PUBLIC_APPLICATION_CATALOG_SCHEMA",
    "PUBLIC_APPLICATION_RELEASE_SCHEMA",
    "SEMANTIC_REGISTRY_DIRECTORY",
    "SEMANTIC_REGISTRY_SCHEMA",
    "SemanticRegistryProjection",
    "SemanticRegistryProjectionError",
]
