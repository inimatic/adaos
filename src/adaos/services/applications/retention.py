from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .service import ApplicationService
from .store import _read


class ApplicationRetentionError(RuntimeError):
    pass


def _timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApplicationRetentionError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ApplicationRetentionError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _digests(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for item in value.values():
            result.update(_digests(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.update(_digests(item))
    elif isinstance(value, str):
        token = value.strip().lower()
        if token.startswith("sha256:") and len(token) == 71 and all(char in "0123456789abcdef" for char in token[7:]):
            result.add(token)
    return result


class ApplicationRetentionService:
    """Projects product references into conservative Artifact CAS holds."""

    def __init__(self, applications: ApplicationService) -> None:
        self.applications = applications
        self.store = applications.store

    @property
    def root(self) -> Path:
        path = self.store.root / "retention"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _hold_path(self, hold_id: str) -> Path:
        token = hashlib.sha256(str(hold_id).encode("utf-8")).hexdigest()
        return self.root / "holds" / f"{token}.json"

    def _tombstone_path(self, application_id: str, release_digest: str) -> Path:
        token = hashlib.sha256(f"{application_id}:{release_digest}".encode("utf-8")).hexdigest()
        return self.root / "tombstones" / f"{token}.json"

    def add_hold(
        self,
        *,
        hold_id: str,
        application_id: str,
        release_digest: str,
        kind: str,
        owner_ref: str,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"rollback", "report", "runtime", "manual"}:
            raise ApplicationRetentionError("unsupported Application retention hold kind")
        release = self.store.get_release(application_id, release_digest)
        if expires_at is not None:
            _timestamp(expires_at, field="expires_at")
        record = {
            "schema": "adaos.application.retention_hold.v1",
            "hold_id": str(hold_id),
            "application_id": application_id,
            "release_digest": release.release_digest,
            "kind": kind,
            "owner_ref": str(owner_ref),
            "expires_at": expires_at,
            "status": "active",
        }
        path = self._hold_path(hold_id)
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            if path.is_file() and _read(path) != record:
                raise ApplicationRetentionError("retention hold identity conflict")
            if not path.is_file():
                atomic_write_json(path, record)
        return record

    def release_hold(self, hold_id: str) -> dict[str, Any]:
        path = self._hold_path(hold_id)
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            record = _read(path)
            if record.get("status") == "released":
                return record
            record["status"] = "released"
            record["released_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            atomic_write_json(path, record)
            return record

    def retire_release(
        self,
        application_id: str,
        release_digest: str,
        *,
        reason: str,
        grace_until: str,
        disposition: str = "retired",
    ) -> dict[str, Any]:
        if disposition not in {"superseded", "retired", "yanked", "revoked"}:
            raise ApplicationRetentionError("release disposition is invalid")
        self.store.get_release(application_id, release_digest)
        channels = self.store.get_channels(application_id).get("channels") or {}
        if release_digest in channels.values():
            raise ApplicationRetentionError("an active channel release cannot be retired")
        _timestamp(grace_until, field="grace_until")
        record = {
            "schema": "adaos.application.release_tombstone.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "disposition": disposition,
            "reason": str(reason),
            "grace_until": grace_until,
            "record_digest": "",
        }
        record["record_digest"] = canonical_payload_digest({key: value for key, value in record.items() if key != "record_digest"})
        path = self._tombstone_path(application_id, release_digest)
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            if path.is_file() and _read(path) != record:
                raise ApplicationRetentionError("immutable release tombstone conflict")
            if not path.is_file():
                atomic_write_json(path, record)
        return record

    def protected_digests(self, *, now: datetime | None = None) -> set[str]:
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        protected_releases: set[str] = set()
        for application in self.store.list_applications():
            channels = self.store.get_channels(application.application_id).get("channels") or {}
            protected_releases.update(str(value) for value in channels.values())
        for installation in self.store.list_installations():
            if installation.status != "removed":
                protected_releases.add(installation.installed_release_digest)
        protected_releases.update(item.release_digest for item in self.store.list_runtime_selections())
        for operation in self.store.list_operations():
            if operation.status in {"planned", "applying", "unknown", "reconciling"}:
                protected_releases.update(_digests(operation.to_dict()))
        grants_by_id = {grant.grant_id: grant for grant in self.store.list_grants()}
        for grant in grants_by_id.values():
            if grant.status == "active" and _timestamp(grant.expires_at, field="grant.expires_at") > observed:
                if grant.release_digest:
                    protected_releases.add(grant.release_digest)
                else:
                    channels = self.store.get_channels(grant.application_id).get("channels") or {}
                    if channels.get("prerelease"):
                        protected_releases.add(str(channels["prerelease"]))
        redemption_root = self.store.root / "trial_access_redemptions"
        if redemption_root.is_dir():
            for path in redemption_root.glob("*.json"):
                receipt = _read(path)
                grant = grants_by_id.get(str(receipt.get("grant_id") or ""))
                if grant is not None and _timestamp(grant.expires_at, field="grant.expires_at") > observed:
                    protected_releases.add(str(receipt.get("release_digest") or ""))
        hold_root = self.root / "holds"
        if hold_root.is_dir():
            for path in hold_root.glob("*.json"):
                hold = _read(path)
                if hold.get("schema") != "adaos.application.retention_hold.v1":
                    raise ApplicationRetentionError("retention hold schema is invalid")
                expires = hold.get("expires_at")
                if hold.get("status") == "active" and (
                    expires is None or _timestamp(str(expires), field="hold.expires_at") > observed
                ):
                    protected_releases.add(str(hold.get("release_digest") or ""))
        tombstone_root = self.root / "tombstones"
        if tombstone_root.is_dir():
            for path in tombstone_root.glob("*.json"):
                tombstone = _read(path)
                if tombstone.get("schema") != "adaos.application.release_tombstone.v1":
                    raise ApplicationRetentionError("release tombstone schema is invalid")
                expected = canonical_payload_digest(
                    {key: value for key, value in tombstone.items() if key != "record_digest"}
                )
                if tombstone.get("record_digest") != expected:
                    raise ApplicationRetentionError("release tombstone digest mismatch")
                if _timestamp(str(tombstone.get("grace_until") or ""), field="grace_until") > observed:
                    protected_releases.add(str(tombstone.get("release_digest") or ""))
        protected = set(protected_releases)
        known_releases: dict[str, Any] = {}
        for application in self.store.list_applications():
            for release in self.store.list_releases(application.application_id):
                known_releases[release.release_digest] = release
        unknown = sorted(digest for digest in protected_releases if digest and digest not in known_releases)
        if unknown:
            raise ApplicationRetentionError(
                "protected Application release metadata is missing: " + ", ".join(unknown)
            )
        for digest in protected_releases:
            release = known_releases.get(digest)
            if release is not None:
                protected.update(_digests(release.to_dict()))
        return protected

    def diagnostics(self, *, now: datetime | None = None) -> dict[str, Any]:
        protected = self.protected_digests(now=now)
        holds = list((self.root / "holds").glob("*.json")) if (self.root / "holds").is_dir() else []
        tombstones = list((self.root / "tombstones").glob("*.json")) if (self.root / "tombstones").is_dir() else []
        return {
            "schema": "adaos.application.retention_diagnostics.v1",
            "protected_digests": sorted(protected),
            "hold_records": len(holds),
            "tombstone_records": len(tombstones),
        }


__all__ = ["ApplicationRetentionError", "ApplicationRetentionService"]
