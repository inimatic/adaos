from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.domain.application import utc_now
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .service import ApplicationService, ApplicationServiceError


class StableSourceProjectionError(ApplicationServiceError):
    pass


class StableSourceProjectionService:
    """Project an exact public stable source revision through a bounded port."""

    def __init__(
        self,
        applications: ApplicationService,
        *,
        publisher: Callable[..., Mapping[str, Any]],
    ) -> None:
        self.applications = applications
        self.publisher = publisher

    @property
    def root(self) -> Path:
        path = self.applications.store.root / "stable_source_projections"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _path(self, application_id: str, release_digest: str) -> Path:
        identity = hashlib.sha256(f"{application_id}:{release_digest}".encode("utf-8")).hexdigest()
        return self.root / f"{identity}.json"

    def publish(
        self,
        application_id: str,
        release_digest: str,
        *,
        publisher_ref: str,
        release_notes: str,
    ) -> dict[str, Any]:
        application = self.applications.store.get_application(application_id)
        if application.publisher_ref != publisher_ref:
            raise StableSourceProjectionError("only the Application publisher may project stable source")
        if application.visibility != "public":
            raise StableSourceProjectionError("stable source projection is limited to public Applications")
        channels = self.applications.store.get_channels(application_id).get("channels") or {}
        if channels.get("stable") != release_digest:
            raise StableSourceProjectionError("source projection requires the exact current stable release")
        release = self.applications.store.get_release(application_id, release_digest)
        path = self._path(application_id, release_digest)
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("release_digest") != release_digest:
                raise StableSourceProjectionError("stable source projection identity mismatch")
            return payload
        result = dict(
            self.publisher(
                application=application.to_dict(),
                release=release.to_dict(),
                release_notes=str(release_notes or "").strip()[:20_000],
            )
        )
        repository = str(result.get("repository") or "").strip()
        commit = str(result.get("commit") or "").strip()
        projected_revision = str(result.get("source_revision") or "").strip()
        expected_revision = release.project_release.source_ref.revision
        if not repository or not commit or projected_revision != expected_revision:
            raise StableSourceProjectionError("source publisher returned incomplete or mismatched evidence")
        receipt = {
            "schema": "adaos.application.stable_source_projection.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "source_revision": expected_revision,
            "repository": repository,
            "commit": commit,
            "publisher_ref": publisher_ref,
            "published_at": utc_now(),
        }
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != receipt:
                    raise StableSourceProjectionError("stable source was projected concurrently")
                return existing
            atomic_write_json(path, receipt)
        return receipt


__all__ = ["StableSourceProjectionError", "StableSourceProjectionService"]
