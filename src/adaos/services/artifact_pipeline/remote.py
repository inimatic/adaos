from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from adaos.domain.artifact_release import ArtifactPackageRef
from adaos.services.artifact_pipeline.channels import (
    RELEASE_PLAN_SCHEMA,
    ChannelPointer,
)
from adaos.services.artifact_pipeline.packages import verify_artifact_package
from adaos.services.artifact_pipeline.releases import ReleasePlan


class ArtifactRegistryClient(Protocol):
    def put_artifact_package(self, **kwargs: Any) -> dict: ...

    def get_artifact_package(self, **kwargs: Any) -> dict: ...

    def put_project_release(self, **kwargs: Any) -> dict: ...

    def get_project_release(self, **kwargs: Any) -> dict: ...

    def set_artifact_channel(self, **kwargs: Any) -> dict: ...

    def get_artifact_channel(self, **kwargs: Any) -> dict: ...


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
        self.client.put_artifact_package(
            digest=package.digest,
            archive_b64=base64.b64encode(archive_bytes).decode("ascii"),
            **self._transport(),
        )

    def fetch_package(self, package: ArtifactPackageRef) -> bytes:
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

    def set_channel(self, plan: ReleasePlan, channel: str = "stable") -> ChannelPointer:
        digest = plan.release.release_digest or plan.release.computed_digest()
        response = self.client.set_artifact_channel(
            project_id=plan.release.project_id,
            channel=channel,
            release_digest=digest,
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


__all__ = ["ArtifactRegistryClient", "RemoteReleaseRepository"]
