from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef
from adaos.services.artifact_pipeline.channels import (
    RELEASE_PLAN_SCHEMA,
    ChannelPointer,
)
from adaos.services.artifact_pipeline.attestations import ArtifactAttestation
from adaos.services.artifact_pipeline.attestation_sets import ReleaseAttestationSet
from adaos.services.artifact_pipeline.packages import verify_artifact_package
from adaos.services.artifact_pipeline.releases import ReleasePlan


class ArtifactRegistryClient(Protocol):
    def put_artifact_package(self, **kwargs: Any) -> dict: ...

    def get_artifact_package(self, **kwargs: Any) -> dict: ...

    def put_artifact_attestation(self, **kwargs: Any) -> dict: ...

    def list_artifact_attestations(self, **kwargs: Any) -> dict: ...

    def put_project_release(self, **kwargs: Any) -> dict: ...

    def get_project_release(self, **kwargs: Any) -> dict: ...

    def put_release_attestation_set(self, **kwargs: Any) -> dict: ...

    def get_release_attestation_set(self, **kwargs: Any) -> dict: ...

    def set_artifact_channel(self, **kwargs: Any) -> dict: ...

    def get_artifact_channel(self, **kwargs: Any) -> dict: ...

    def clear_artifact_channel(self, **kwargs: Any) -> dict: ...

    def get_draft_source_tree(self, **kwargs: Any) -> dict: ...


@dataclass(slots=True)
class RemoteReleaseRepository:
    client: ArtifactRegistryClient
    verify: Any = None
    cert: tuple[str, str] | None = None

    def _transport(self) -> dict[str, Any]:
        return {"verify": self.verify, "cert": self.cert}

    def put_package(self, package: ArtifactPackageRef, archive_bytes: bytes) -> None:
        verified = verify_artifact_package(archive_bytes, expected_digest=package.digest)
        if verified.ref != package:
            raise ValueError(f"package archive does not match PackageRef: {package.key}")
        put_binary = getattr(self.client, "put_artifact_package_bytes", None)
        if callable(put_binary):
            try:
                put_binary(
                    digest=package.digest,
                    archive=archive_bytes,
                    **self._transport(),
                )
                return
            except Exception as exc:
                if getattr(exc, "status_code", None) not in {404, 405}:
                    raise
        self.client.put_artifact_package(
            digest=package.digest,
            archive_b64=base64.b64encode(archive_bytes).decode("ascii"),
            **self._transport(),
        )

    def fetch_package(self, package: ArtifactPackageRef) -> bytes:
        get_binary = getattr(self.client, "get_artifact_package_bytes", None)
        if callable(get_binary):
            try:
                data = get_binary(digest=package.digest, **self._transport())
            except Exception as exc:
                if getattr(exc, "status_code", None) not in {404, 405}:
                    raise
            else:
                verified = verify_artifact_package(data, expected_digest=package.digest)
                if verified.ref != package:
                    raise ValueError(f"remote package does not match PackageRef: {package.key}")
                return data
        response = self.client.get_artifact_package(
            digest=package.digest,
            **self._transport(),
        )
        encoded = str(response.get("archive_b64") or "")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("artifact registry returned invalid package encoding") from exc
        verified = verify_artifact_package(data, expected_digest=package.digest)
        if verified.ref != package:
            raise ValueError(f"remote package does not match PackageRef: {package.key}")
        return data

    def put_release(
        self,
        plan: ReleasePlan,
        archives: Mapping[str, bytes],
    ) -> None:
        for package in plan.packages:
            archive = archives.get(package.digest)
            if archive is None:
                raise ValueError(f"archive is missing for {package.key}@{package.digest}")
            self.put_package(package, archive)
        self.put_release_record(plan)

    def put_release_record(self, plan: ReleasePlan) -> None:
        """Publish only the immutable release record after packages are durable."""

        digest = plan.release.release_digest or plan.release.computed_digest()
        self.client.put_project_release(
            project_id=plan.release.project_id,
            release_digest=digest,
            release_plan={"schema": RELEASE_PLAN_SCHEMA, **plan.explain()},
            **self._transport(),
        )

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        response = self.client.get_project_release(
            project_id=project_id,
            release_digest=release_digest,
            **self._transport(),
        )
        payload = response.get("release_plan")
        if not isinstance(payload, Mapping) or payload.get("schema") != RELEASE_PLAN_SCHEMA:
            raise ValueError("artifact registry returned an unsupported release plan")
        plan = ReleasePlan.from_mapping(payload)
        actual = plan.release.release_digest or plan.release.computed_digest()
        if actual != release_digest:
            raise ValueError("artifact registry release digest mismatch")
        return plan

    def put_release_attestation_set(
        self,
        attestation_set: ReleaseAttestationSet,
    ) -> ReleaseAttestationSet:
        sealed = attestation_set.seal()
        response = self.client.put_release_attestation_set(
            project_id=sealed.project_id,
            release_digest=sealed.release_digest,
            attestation_set=sealed.to_dict(),
            **self._transport(),
        )
        payload = response.get("attestation_set")
        if not isinstance(payload, Mapping):
            raise ValueError("artifact registry returned no release attestation set")
        observed = ReleaseAttestationSet.from_mapping(payload)
        if observed != sealed:
            raise ValueError("artifact registry bound a different release attestation set")
        return observed

    def get_release_attestation_set(
        self,
        project_id: str,
        release_digest: str,
    ) -> ReleaseAttestationSet:
        response = self.client.get_release_attestation_set(
            project_id=project_id,
            release_digest=release_digest,
            **self._transport(),
        )
        payload = response.get("attestation_set")
        if not isinstance(payload, Mapping):
            raise ValueError("artifact registry returned no release attestation set")
        observed = ReleaseAttestationSet.from_mapping(payload)
        if observed.project_id != project_id or observed.release_digest != release_digest:
            raise ValueError("artifact registry returned an attestation set for another release")
        return observed

    def set_channel(
        self,
        plan: ReleasePlan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ) -> ChannelPointer:
        digest = plan.release.release_digest or plan.release.computed_digest()
        response = self.client.set_artifact_channel(
            project_id=plan.release.project_id,
            channel=channel,
            release_digest=digest,
            expected_release_digest=expected_release_digest,
            **self._transport(),
        )
        pointer = response.get("pointer")
        if not isinstance(pointer, Mapping):
            raise ValueError("artifact registry returned no channel pointer")
        result = ChannelPointer.from_mapping(pointer)
        if result.release_digest != digest:
            raise ValueError("artifact registry channel points at a different release")
        return result

    def get_channel(self, project_id: str, channel: str = "stable") -> ChannelPointer:
        response = self.client.get_artifact_channel(
            project_id=project_id,
            channel=channel,
            **self._transport(),
        )
        pointer = response.get("pointer")
        if not isinstance(pointer, Mapping):
            raise ValueError("artifact registry returned no channel pointer")
        return ChannelPointer.from_mapping(pointer)

    def clear_channel(
        self,
        project_id: str,
        channel: str,
        *,
        expected_release_digest: str,
    ) -> ChannelPointer:
        response = self.client.clear_artifact_channel(
            project_id=project_id,
            channel=channel,
            expected_release_digest=expected_release_digest,
            **self._transport(),
        )
        pointer = response.get("pointer")
        if not isinstance(pointer, Mapping):
            raise ValueError("artifact registry returned no cleared channel pointer")
        result = ChannelPointer.from_mapping(pointer)
        if result.release_digest != expected_release_digest:
            raise ValueError("artifact registry cleared a different channel generation")
        return result

    def tree_revision(self, source_ref: ArtifactSourceRef) -> str:
        if source_ref.forge != "adaos-root":
            raise ValueError(f"unsupported remote source forge: {source_ref.forge}")
        if len(source_ref.path_scope) != 1:
            raise ValueError("remote source verification requires one exact artifact path")
        parts = [item for item in source_ref.path_scope[0].replace("\\", "/").split("/") if item]
        try:
            node_index = parts.index("nodes")
            node_id = parts[node_index + 1]
            kind = parts[node_index + 2]
            name = parts[node_index + 3]
        except (ValueError, IndexError) as exc:
            raise ValueError("remote SourceRef path does not identify a node artifact") from exc
        if kind not in {"skills", "scenarios"} or node_index + 4 != len(parts):
            raise ValueError("remote SourceRef path is not an exact skill or scenario path")
        response = self.client.get_draft_source_tree(
            kind=kind,
            name=name,
            revision=source_ref.revision,
            node_id=node_id,
            **self._transport(),
        )
        tree = str(response.get("tree_sha") or "").strip().lower()
        if len(tree) not in {40, 64} or any(char not in "0123456789abcdef" for char in tree):
            raise ValueError("Forge returned an invalid source tree identity")
        expected_path = source_ref.path_scope[0].rstrip("/").replace("\\", "/")
        actual_path = str(response.get("stored_path") or "").rstrip("/").replace("\\", "/")
        if actual_path and actual_path != expected_path:
            raise ValueError("Forge verified a different source path")
        return tree


@dataclass(slots=True)
class RemoteArtifactAttestationStore:
    client: ArtifactRegistryClient
    verify: Any = None
    cert: tuple[str, str] | None = None

    def _transport(self) -> dict[str, Any]:
        return {"verify": self.verify, "cert": self.cert}

    def put(self, attestation: ArtifactAttestation) -> str:
        sealed = attestation.seal()
        response = self.client.put_artifact_attestation(
            attestation=sealed.to_dict(),
            **self._transport(),
        )
        observed = str(response.get("attestation_digest") or "").strip().lower()
        if observed != sealed.attestation_digest:
            raise ValueError("artifact registry stored a different attestation digest")
        return observed

    def list_for_subject(
        self,
        subject_kind: str,
        subject_digest: str,
    ) -> tuple[ArtifactAttestation, ...]:
        response = self.client.list_artifact_attestations(
            subject_kind=subject_kind,
            subject_digest=subject_digest,
            **self._transport(),
        )
        raw = response.get("attestations")
        if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
            raise ValueError("artifact registry returned an invalid attestation list")
        result = tuple(ArtifactAttestation.from_mapping(item) for item in raw)
        if any(
            item.subject_kind != subject_kind or item.subject_digest != subject_digest
            for item in result
        ):
            raise ValueError("artifact registry returned attestations for another subject")
        return tuple(sorted(result, key=lambda item: str(item.attestation_digest)))


__all__ = [
    "ArtifactRegistryClient",
    "RemoteArtifactAttestationStore",
    "RemoteReleaseRepository",
]
