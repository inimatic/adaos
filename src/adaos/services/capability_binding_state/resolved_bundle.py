"""Sealed, registry-offline CBS distribution bundles.

The bundle carries only portable and immutable inputs selected by the online
thin resolver.  Admission verifies every byte before populating the existing
package, release, and portable-contract stores.  It deliberately does not
create credentials, BindingInstances, StateSpaces, plans, locks, or runtime
authority.
"""

from __future__ import annotations

import io
import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from adaos.domain.artifact_release import (
    canonical_json_bytes,
    canonical_payload_digest,
    sha256_digest,
)
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    ApplicationResolution,
)
from adaos.services.artifact_pipeline.channels import RELEASE_PLAN_SCHEMA, ReleaseRepository
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore,
    verify_artifact_package,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json

from .catalog import PortableContractCatalog
from .registry_distribution import ThinSemanticDistributionResolver
from .registry_projection import _application_release_validator, _canonical_record


RESOLVED_BUNDLE_SCHEMA = "adaos.semantic_registry.resolved_bundle.v1"
RESOLVED_BUNDLE_ADMISSION_SCHEMA = (
    "adaos.semantic_registry.resolved_bundle_admission.v1"
)
_MAX_MEMBERS = 512
_MAX_MEMBER_BYTES = 256 * 1024 * 1024
_MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
_FORBIDDEN_MEMBER_TOKENS = {
    "credential",
    "credentials",
    "secret",
    "secrets",
    "binding-instance",
    "binding_instance",
    "state-space",
    "state_space",
    "workspace-lock",
    "workspace_lock",
}


class ResolvedBundleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ResolvedBundleExport:
    archive_bytes: bytes
    bundle_digest: str
    manifest: Mapping[str, Any]
    thin_receipt: Mapping[str, Any]


def _schema_validator() -> Draft202012Validator:
    path = (
        Path(__file__).resolve().parents[2]
        / "abi"
        / "semantic_registry.resolved_bundle.v1.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(value))


def _zip_bytes(members: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, members[name])
    return output.getvalue()


def _member_entry(name: str, kind: str, data: bytes) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "digest": sha256_digest(data),
        "size": len(data),
    }


def _safe_member_name(name: str) -> str:
    token = str(name or "")
    path = PurePosixPath(token)
    if (
        not token
        or "\\" in token
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ResolvedBundleError("unsafe_member", f"unsafe bundle member: {token!r}")
    lowered = {part.lower() for part in path.parts}
    if lowered & _FORBIDDEN_MEMBER_TOKENS:
        raise ResolvedBundleError(
            "local_authority_forbidden",
            f"bundle member is reserved for local authority: {token}",
        )
    return token


def _read_bundle(data: bytes) -> dict[str, bytes]:
    if len(data) > _MAX_BUNDLE_BYTES:
        raise ResolvedBundleError("bundle_too_large", "resolved bundle exceeds size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_MEMBERS:
                raise ResolvedBundleError(
                    "invalid_member_count", "resolved bundle member count is invalid"
                )
            names: set[str] = set()
            total = 0
            result: dict[str, bytes] = {}
            for info in infos:
                name = _safe_member_name(info.filename)
                if name in names:
                    raise ResolvedBundleError(
                        "duplicate_member", f"duplicate bundle member: {name}"
                    )
                names.add(name)
                mode = (info.external_attr >> 16) & 0xFFFF
                if info.flag_bits & 0x1 or stat.S_ISLNK(mode) or info.is_dir():
                    raise ResolvedBundleError(
                        "unsafe_member", f"unsupported bundle member: {name}"
                    )
                if info.file_size > _MAX_MEMBER_BYTES:
                    raise ResolvedBundleError(
                        "member_too_large", f"bundle member exceeds size limit: {name}"
                    )
                total += info.file_size
                if total > _MAX_BUNDLE_BYTES:
                    raise ResolvedBundleError(
                        "bundle_too_large", "expanded resolved bundle exceeds size limit"
                    )
                result[name] = archive.read(info)
            return result
    except ResolvedBundleError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ResolvedBundleError("invalid_archive", "resolved bundle is not a valid ZIP") from exc


def _parse_json(members: Mapping[str, bytes], name: str) -> dict[str, Any]:
    try:
        value = json.loads(members[name].decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResolvedBundleError(
            "invalid_json_member", f"bundle member is not valid JSON: {name}"
        ) from exc
    if not isinstance(value, Mapping):
        raise ResolvedBundleError(
            "invalid_json_member", f"bundle member must contain an object: {name}"
        )
    return dict(value)


@dataclass(slots=True)
class ResolvedSemanticBundleExporter:
    resolver: ThinSemanticDistributionResolver

    def export(
        self,
        *,
        project_id: str,
        project_release_digest: str,
        query: Mapping[str, Any],
        registry_revision: str,
    ) -> ResolvedBundleExport:
        thin = self.resolver.resolve(
            project_id=project_id,
            project_release_digest=project_release_digest,
            query=query,
            registry_revision=registry_revision,
        )
        query_result = self.resolver.projection.query(
            query, registry_revision=registry_revision
        )
        if query_result["result_digest"] != thin["query_result_digest"]:
            raise ResolvedBundleError(
                "query_result_drift", "semantic query changed during bundle export"
            )
        application = self.resolver.projection.read_application_release(
            project_id, project_release_digest
        )
        plan = self.resolver.remote.get_release(project_id, project_release_digest)
        release_plan = {"schema": RELEASE_PLAN_SCHEMA, **plan.explain()}

        members: dict[str, bytes] = {
            "application.json": _json_bytes(application),
            "query.json": _json_bytes(dict(query)),
            "query-result.json": _json_bytes(query_result),
            "resolution-receipt.json": _json_bytes(thin),
            "release-plan.json": _json_bytes(release_plan),
        }
        entries: list[dict[str, Any]] = [
            _member_entry("application.json", "semantic_application", members["application.json"]),
            _member_entry("query.json", "semantic_query", members["query.json"]),
            _member_entry("query-result.json", "semantic_query_result", members["query-result.json"]),
            _member_entry(
                "resolution-receipt.json", "thin_resolution", members["resolution-receipt.json"]
            ),
            _member_entry("release-plan.json", "release_plan", members["release-plan.json"]),
        ]
        for digest in sorted(thin["portable_record_digests"]):
            payload = self.resolver.portable_catalog.load_mapping(digest)
            record = _canonical_record(payload)
            if record.digest != digest:
                raise ResolvedBundleError(
                    "portable_record_mismatch", f"portable record digest differs: {digest}"
                )
            name = f"records/{digest.removeprefix('sha256:')}.json"
            members[name] = _json_bytes(record.to_dict())
            entries.append(_member_entry(name, "portable_record", members[name]))
        for package in sorted(plan.packages, key=lambda item: item.key):
            data = self.resolver.package_store.read(package.digest)
            name = f"packages/{package.digest.removeprefix('sha256:')}.zip"
            members[name] = data
            entries.append(_member_entry(name, "package", data))

        manifest: dict[str, Any] = {
            "schema": RESOLVED_BUNDLE_SCHEMA,
            "project_id": str(project_id),
            "application_ref": str(application["application_ref"]),
            "project_release_digest": str(project_release_digest),
            "semantic_revision_digest": str(application["semantic_revision_digest"]),
            "application_projection_digest": str(application["projection_digest"]),
            "snapshot": dict(query_result["snapshot"]),
            "query_digest": str(query_result["query_digest"]),
            "query_result_digest": str(query_result["result_digest"]),
            "thin_resolution_receipt_digest": str(thin["receipt_digest"]),
            "provenance_receipt_digest": str(thin["provenance_receipt_digest"]),
            "portable_record_digests": sorted(thin["portable_record_digests"]),
            "package_digests": sorted(item.digest for item in plan.packages),
            "resolution_digests": sorted(
                ApplicationResolution.from_mapping(item).digest
                for item in thin["resolutions"]
            ),
            "members": sorted(entries, key=lambda item: item["name"]),
            "local_authority_excluded": [
                "BindingInstance",
                "StateSpace",
                "ResolutionPlan",
                "WorkspaceLock",
                "credentials",
                "operational evidence",
            ],
            "activation_performed": False,
            "local_authority_created": False,
        }
        manifest["manifest_digest"] = canonical_payload_digest(manifest)
        errors = sorted(
            _schema_validator().iter_errors(manifest),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            raise ResolvedBundleError("invalid_manifest", errors[0].message)
        members["manifest.json"] = _json_bytes(manifest)
        archive = _zip_bytes(members)
        return ResolvedBundleExport(
            archive_bytes=archive,
            bundle_digest=sha256_digest(archive),
            manifest=manifest,
            thin_receipt=thin,
        )


@dataclass(slots=True)
class ResolvedSemanticBundleAdmission:
    package_store: ContentAddressedPackageStore
    portable_catalog: PortableContractCatalog
    release_repository: ReleaseRepository
    receipt_root: Path | None = None

    def admit(
        self,
        archive_bytes: bytes,
        *,
        expected_bundle_digest: str,
    ) -> dict[str, Any]:
        actual_bundle_digest = sha256_digest(archive_bytes)
        if not expected_bundle_digest or actual_bundle_digest != expected_bundle_digest:
            raise ResolvedBundleError(
                "bundle_digest_mismatch", "resolved bundle digest does not match its sealed identity"
            )
        members = _read_bundle(archive_bytes)
        manifest = _parse_json(members, "manifest.json")
        errors = sorted(
            _schema_validator().iter_errors(manifest),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            raise ResolvedBundleError("invalid_manifest", errors[0].message)
        unsigned_manifest = dict(manifest)
        expected_manifest_digest = str(unsigned_manifest.pop("manifest_digest"))
        if canonical_payload_digest(unsigned_manifest) != expected_manifest_digest:
            raise ResolvedBundleError("manifest_digest_mismatch", "bundle manifest digest mismatch")

        declared = {str(item["name"]): dict(item) for item in manifest["members"]}
        if len(declared) != len(manifest["members"]):
            raise ResolvedBundleError("duplicate_member", "manifest contains duplicate members")
        if set(members) != {"manifest.json", *declared}:
            raise ResolvedBundleError(
                "member_set_mismatch", "bundle members differ from the sealed manifest"
            )
        for name, entry in declared.items():
            _safe_member_name(name)
            data = members[name]
            if entry["size"] != len(data) or entry["digest"] != sha256_digest(data):
                raise ResolvedBundleError(
                    "member_digest_mismatch", f"bundle member differs from manifest: {name}"
                )
        expected_core_kinds = {
            "application.json": "semantic_application",
            "query.json": "semantic_query",
            "query-result.json": "semantic_query_result",
            "resolution-receipt.json": "thin_resolution",
            "release-plan.json": "release_plan",
        }
        if any(
            declared.get(name, {}).get("kind") != kind
            for name, kind in expected_core_kinds.items()
        ):
            raise ResolvedBundleError(
                "member_kind_mismatch", "resolved bundle core member kinds differ"
            )

        query = _parse_json(members, "query.json")
        query_result = _parse_json(members, "query-result.json")
        application = _parse_json(members, "application.json")
        thin = _parse_json(members, "resolution-receipt.json")
        release_plan_value = _parse_json(members, "release-plan.json")
        if canonical_payload_digest(query) != manifest["query_digest"]:
            raise ResolvedBundleError("query_digest_mismatch", "bundle query digest mismatch")
        unsigned_result = dict(query_result)
        result_digest = str(unsigned_result.pop("result_digest", ""))
        if canonical_payload_digest(unsigned_result) != result_digest:
            raise ResolvedBundleError("query_result_digest_mismatch", "query result digest mismatch")
        if result_digest != manifest["query_result_digest"]:
            raise ResolvedBundleError("query_result_digest_mismatch", "query result identity differs")
        unsigned_application = dict(application)
        projection_digest = str(unsigned_application.pop("projection_digest", ""))
        if canonical_payload_digest(unsigned_application) != projection_digest:
            raise ResolvedBundleError("application_digest_mismatch", "Application projection digest mismatch")
        application_errors = sorted(
            _application_release_validator().iter_errors(application),
            key=lambda item: list(item.absolute_path),
        )
        if application_errors:
            raise ResolvedBundleError("invalid_application", application_errors[0].message)
        unsigned_thin = dict(thin)
        thin_digest = str(unsigned_thin.pop("receipt_digest", ""))
        if canonical_payload_digest(unsigned_thin) != thin_digest:
            raise ResolvedBundleError("thin_receipt_mismatch", "thin resolution receipt digest mismatch")

        plan = ReleasePlan.from_mapping(release_plan_value)
        release_digest = str(plan.release.release_digest or plan.release.computed_digest())
        if (
            release_digest != manifest["project_release_digest"]
            or application["project_release_digest"] != release_digest
            or thin["project_release_digest"] != release_digest
            or application["project_id"] != manifest["project_id"]
            or thin["project_id"] != manifest["project_id"]
            or application["application_ref"] != manifest["application_ref"]
            or thin["application_ref"] != manifest["application_ref"]
            or application["semantic_revision_digest"]
            != manifest["semantic_revision_digest"]
            or thin["semantic_revision_digest"]
            != manifest["semantic_revision_digest"]
            or projection_digest != manifest["application_projection_digest"]
            or thin["application_projection_digest"] != projection_digest
            or thin_digest != manifest["thin_resolution_receipt_digest"]
            or query_result["snapshot"] != manifest["snapshot"]
            or thin["snapshot"] != manifest["snapshot"]
            or query.get("snapshot") != manifest["snapshot"]
            or query_result.get("query_digest") != manifest["query_digest"]
            or thin["query_result_digest"] != result_digest
            or thin["provenance_receipt_digest"]
            != manifest["provenance_receipt_digest"]
        ):
            raise ResolvedBundleError(
                "closure_identity_mismatch", "resolved bundle identities do not form one exact closure"
            )
        application_requirements = {
            ApplicationRequirement.from_mapping(item).digest
            for item in application["requirements"]
        }
        query_requirements = {
            ApplicationRequirement.from_mapping(item).digest
            for item in query["requirements"]
        }
        if application_requirements != query_requirements:
            raise ResolvedBundleError(
                "application_requirements_mismatch",
                "bundle query differs from the immutable Application requirements",
            )

        record_entries = [item for item in declared.values() if item["kind"] == "portable_record"]
        package_entries = [item for item in declared.values() if item["kind"] == "package"]
        records = []
        records_by_name = {}
        for entry in record_entries:
            record = _canonical_record(_parse_json(members, str(entry["name"])))
            records.append(record)
            records_by_name[str(entry["name"])] = record
        record_digests = sorted(record.digest for record in records)
        application_portable_digests = {
            str(digest)
            for digests in application["portable_artifacts"].values()
            for digest in digests
        }
        if (
            record_digests != sorted(manifest["portable_record_digests"])
            or record_digests != sorted(thin["portable_record_digests"])
            or not set(record_digests).issubset(application_portable_digests)
        ):
            raise ResolvedBundleError("portable_closure_mismatch", "portable record closure differs")
        for entry in record_entries:
            token = records_by_name[str(entry["name"])].digest.removeprefix("sha256:")
            if entry["name"] != f"records/{token}.json":
                raise ResolvedBundleError(
                    "portable_closure_mismatch", "portable record path differs from its digest"
                )

        package_bytes: dict[str, bytes] = {}
        package_refs = {}
        for entry in package_entries:
            data = members[str(entry["name"])]
            verified = verify_artifact_package(data, expected_digest=str(entry["digest"]))
            package_bytes[verified.ref.digest] = data
            package_refs[verified.ref.digest] = verified.ref
        plan_packages = {item.digest: item for item in plan.packages}
        if (
            sorted(package_refs) != sorted(manifest["package_digests"])
            or set(package_refs) != set(plan_packages)
            or any(package_refs[digest] != plan_packages[digest] for digest in package_refs)
        ):
            raise ResolvedBundleError("package_closure_mismatch", "package closure differs")
        published_package_closure = sorted(
            (dict(item) for item in application["distribution"]["resolved"]["package_closure"]),
            key=lambda item: str(item["digest"]),
        )
        exact_package_closure = sorted(
            (item.to_dict() for item in plan.packages),
            key=lambda item: str(item["digest"]),
        )
        acquired_packages = {
            str(item["digest"]) for item in thin["package_acquisition"]
        }
        if (
            published_package_closure != exact_package_closure
            or set(package_refs) != acquired_packages
        ):
            raise ResolvedBundleError("package_closure_mismatch", "package closure differs")
        for entry in package_entries:
            token = str(entry["digest"]).removeprefix("sha256:")
            if entry["name"] != f"packages/{token}.zip":
                raise ResolvedBundleError(
                    "package_closure_mismatch", "package path differs from its digest"
                )

        resolutions = tuple(
            ApplicationResolution.from_mapping(item) for item in thin["resolutions"]
        )
        resolution_package_closure = sorted(
            (
                {
                    "kind": item.kind,
                    "id": item.artifact_id,
                    "version": item.version,
                    "digest": item.digest,
                }
                for item in plan.packages
            ),
            key=lambda item: str(item["digest"]),
        )
        if any(
            sorted(
                (dict(item) for item in resolution.to_dict()["package_closure"]),
                key=lambda item: str(item["digest"]),
            )
            != resolution_package_closure
            for resolution in resolutions
        ):
            raise ResolvedBundleError(
                "package_closure_mismatch",
                "ApplicationResolution package closure differs",
            )
        if any(item.to_dict()["binding_instances"] or item.to_dict()["state_attachments"] for item in resolutions):
            raise ResolvedBundleError(
                "local_authority_forbidden", "portable bundle contains local authority"
            )
        if sorted(item.digest for item in resolutions) != sorted(manifest["resolution_digests"]):
            raise ResolvedBundleError("resolution_closure_mismatch", "resolution closure differs")
        if thin.get("activation_performed") is not False or thin.get("local_authority_created") is not False:
            raise ResolvedBundleError(
                "local_authority_forbidden", "portable resolution receipt claims local authority"
            )

        # All parsing, canonical validation, closure checks, and package
        # verification complete before the first destination-store mutation.
        for digest in sorted(package_bytes):
            self.package_store.put(package_bytes[digest], expected_digest=digest)
        self.portable_catalog.put_many(records)
        self.release_repository.put_release(plan)

        receipt: dict[str, Any] = {
            "schema": RESOLVED_BUNDLE_ADMISSION_SCHEMA,
            "status": "admitted",
            "bundle_digest": actual_bundle_digest,
            "manifest_digest": manifest["manifest_digest"],
            "project_id": manifest["project_id"],
            "application_ref": manifest["application_ref"],
            "project_release_digest": release_digest,
            "semantic_revision_digest": manifest["semantic_revision_digest"],
            "snapshot": dict(manifest["snapshot"]),
            "portable_record_digests": record_digests,
            "package_digests": sorted(package_refs),
            "resolution_digests": sorted(item.digest for item in resolutions),
            "resolutions": [item.to_dict() for item in resolutions],
            "activation_performed": False,
            "local_authority_created": False,
        }
        receipt["receipt_digest"] = canonical_payload_digest(receipt)
        if self.receipt_root is not None:
            target = (
                Path(self.receipt_root).expanduser().resolve()
                / f"{actual_bundle_digest.removeprefix('sha256:')}.json"
            )
            if target.is_file():
                existing = json.loads(target.read_text(encoding="utf-8"))
                if existing != receipt:
                    raise ResolvedBundleError(
                        "admission_receipt_conflict", "bundle was admitted with a different receipt"
                    )
            else:
                atomic_write_json(target, receipt)
        return receipt


__all__ = [
    "RESOLVED_BUNDLE_ADMISSION_SCHEMA",
    "RESOLVED_BUNDLE_SCHEMA",
    "ResolvedBundleError",
    "ResolvedBundleExport",
    "ResolvedSemanticBundleAdmission",
    "ResolvedSemanticBundleExporter",
]
