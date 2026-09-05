from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from adaos.domain.application import (
    Application,
    ApplicationInstallation,
    ApplicationOperation,
    ApplicationRelease,
    ApplicationSubscription,
    RuntimeSelection,
    TrialAccessGrant,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class ApplicationStoreError(RuntimeError):
    pass


class ApplicationRevisionConflict(ApplicationStoreError):
    def __init__(self, *, expected: int, observed: int) -> None:
        super().__init__(
            f"application state revision conflict: expected {expected}, observed {observed}"
        )
        self.expected = expected
        self.observed = observed


class ApplicationChannelConflict(ApplicationStoreError):
    def __init__(self, *, expected: str | None, observed: str | None) -> None:
        super().__init__(
            "application channel conflict: "
            f"expected {expected or '<absent>'}, observed {observed or '<absent>'}"
        )
        self.expected = expected
        self.observed = observed


_T = TypeVar("_T")


def _key(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ApplicationStoreError("record identity is required")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationStoreError(f"cannot read Application record {path.name}") from exc
    if not isinstance(payload, Mapping):
        raise ApplicationStoreError(f"Application record {path.name} is not an object")
    return dict(payload)


class ApplicationStore:
    """Durable local Application aggregate state.

    Immutable package bytes and legacy ProjectRelease plans remain in Artifact
    Pipeline stores. This store owns only product identity, compatibility
    envelopes, selections, subscriptions, grants, and operation receipts.
    """

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    @property
    def root(self) -> Path:
        path = self.state_dir / "applications"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _current_path(self, collection: str, identity: str) -> Path:
        return self.root / collection / _key(identity) / "current.json"

    def _list_current(self, collection: str, loader: Callable[[Mapping[str, Any]], _T]) -> tuple[_T, ...]:
        parent = self.root / collection
        if not parent.is_dir():
            return ()
        values = [loader(_read(path)) for path in parent.glob("*/current.json")]
        return tuple(values)

    def get_application(self, application_id: str) -> Application:
        path = self._current_path("definitions", application_id)
        if not path.is_file():
            raise FileNotFoundError(f"Application not found: {application_id}")
        value = Application.from_mapping(_read(path))
        if value.application_id != application_id:
            raise ApplicationStoreError("Application path identity mismatch")
        return value

    def list_applications(self) -> tuple[Application, ...]:
        return tuple(sorted(self._list_current("definitions", Application.from_mapping), key=lambda item: item.application_id))

    def save_application(self, value: Application, *, expected_revision: int) -> Application:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            try:
                current = self.get_application(value.application_id)
            except FileNotFoundError:
                current = None
            observed = current.revision if current is not None else 0
            if expected_revision != observed:
                raise ApplicationRevisionConflict(expected=expected_revision, observed=observed)
            if value.revision != observed + 1:
                raise ApplicationStoreError("Application revision must advance by exactly one")
            if current is not None and current.legacy_project_id != value.legacy_project_id:
                raise ApplicationStoreError("legacy Project identity is immutable")
            for item in self.list_applications():
                if item.application_id != value.application_id and item.legacy_project_id == value.legacy_project_id:
                    raise ApplicationStoreError("legacy Project is already mapped to another Application")
            atomic_write_json(self._current_path("definitions", value.application_id), value.to_dict())
            return value

    def _release_path(self, application_id: str, release_digest: str) -> Path:
        digest = str(release_digest or "").split(":", 1)[-1]
        return self.root / "releases" / _key(application_id) / f"{digest}.json"

    def put_release(self, value: ApplicationRelease) -> ApplicationRelease:
        application = self.get_application(value.application_id)
        if application.publisher_ref != value.publisher_ref:
            raise ApplicationStoreError("ApplicationRelease publisher does not own Application")
        if application.legacy_project_id != value.project_release.project_id:
            raise ApplicationStoreError("ApplicationRelease belongs to a different legacy Project")
        path = self._release_path(value.application_id, value.release_digest)
        payload = value.to_dict()
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                if _read(path) != payload:
                    raise ApplicationStoreError("immutable ApplicationRelease conflict")
                return ApplicationRelease.from_mapping(_read(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload)
        return value

    def get_release(self, application_id: str, release_digest: str) -> ApplicationRelease:
        path = self._release_path(application_id, release_digest)
        if not path.is_file():
            raise FileNotFoundError(f"ApplicationRelease not found: {application_id}@{release_digest}")
        value = ApplicationRelease.from_mapping(_read(path))
        if value.application_id != application_id or value.release_digest != release_digest:
            raise ApplicationStoreError("ApplicationRelease path identity mismatch")
        return value

    def list_releases(self, application_id: str) -> tuple[ApplicationRelease, ...]:
        parent = self.root / "releases" / _key(application_id)
        if not parent.is_dir():
            return ()
        values = [ApplicationRelease.from_mapping(_read(path)) for path in parent.glob("*.json")]
        if any(item.application_id != application_id for item in values):
            raise ApplicationStoreError("ApplicationRelease collection identity mismatch")
        return tuple(sorted(values, key=lambda item: (item.project_release.version, item.release_digest)))

    def _channel_path(self, application_id: str) -> Path:
        return self.root / "channels" / f"{_key(application_id)}.json"

    def get_channels(self, application_id: str) -> dict[str, Any]:
        self.get_application(application_id)
        path = self._channel_path(application_id)
        if not path.is_file():
            return {"schema": "adaos.application.channel_set.v1", "application_id": application_id, "revision": 0, "channels": {}}
        payload = _read(path)
        if payload.get("schema") != "adaos.application.channel_set.v1" or payload.get("application_id") != application_id:
            raise ApplicationStoreError("unsupported Application channel set")
        channels = payload.get("channels")
        if not isinstance(channels, Mapping) or set(channels) - {"stable", "prerelease"}:
            raise ApplicationStoreError("Application channel set is invalid")
        return payload

    def set_channel(
        self,
        application_id: str,
        channel: str,
        release_digest: str | None,
        *,
        expected_release_digest: str | None,
    ) -> dict[str, Any]:
        channel_id = str(channel or "").strip().lower()
        if channel_id not in {"stable", "prerelease"}:
            raise ApplicationStoreError("Application channel must be stable or prerelease")
        if release_digest is not None:
            self.get_release(application_id, release_digest)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            payload = self.get_channels(application_id)
            channels = dict(payload.get("channels") or {})
            observed = channels.get(channel_id)
            if observed == release_digest:
                return payload
            if observed != expected_release_digest:
                raise ApplicationChannelConflict(expected=expected_release_digest, observed=observed)
            if release_digest is None:
                channels.pop(channel_id, None)
            else:
                channels[channel_id] = release_digest
            updated = {
                "schema": "adaos.application.channel_set.v1",
                "application_id": application_id,
                "revision": int(payload.get("revision") or 0) + 1,
                "channels": {key: channels[key] for key in sorted(channels)},
            }
            atomic_write_json(self._channel_path(application_id), updated)
            return updated

    def _save_revisioned(
        self,
        collection: str,
        identity: str,
        value: Any,
        *,
        expected_revision: int,
        loader: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        path = self._current_path(collection, identity)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            current = loader(_read(path)) if path.is_file() else None
            observed = current.revision if current is not None else 0
            if expected_revision != observed:
                raise ApplicationRevisionConflict(expected=expected_revision, observed=observed)
            if value.revision != observed + 1:
                raise ApplicationStoreError("record revision must advance by exactly one")
            atomic_write_json(path, value.to_dict())
            return value

    def get_installation(self, application_id: str) -> ApplicationInstallation:
        path = self._current_path("installations", application_id)
        if not path.is_file():
            raise FileNotFoundError(f"ApplicationInstallation not found: {application_id}")
        value = ApplicationInstallation.from_mapping(_read(path))
        if value.application_id != application_id:
            raise ApplicationStoreError("ApplicationInstallation path identity mismatch")
        return value

    def list_installations(self) -> tuple[ApplicationInstallation, ...]:
        return tuple(sorted(self._list_current("installations", ApplicationInstallation.from_mapping), key=lambda item: item.application_id))

    def save_installation(self, value: ApplicationInstallation, *, expected_revision: int) -> ApplicationInstallation:
        self.get_application(value.application_id)
        return self._save_revisioned("installations", value.application_id, value, expected_revision=expected_revision, loader=ApplicationInstallation.from_mapping)

    def get_subscription(self, application_id: str) -> ApplicationSubscription:
        path = self._current_path("subscriptions", application_id)
        if not path.is_file():
            raise FileNotFoundError(f"ApplicationSubscription not found: {application_id}")
        return ApplicationSubscription.from_mapping(_read(path))

    def list_subscriptions(self) -> tuple[ApplicationSubscription, ...]:
        return tuple(sorted(self._list_current("subscriptions", ApplicationSubscription.from_mapping), key=lambda item: item.application_id))

    def save_subscription(self, value: ApplicationSubscription, *, expected_revision: int) -> ApplicationSubscription:
        self.get_application(value.application_id)
        return self._save_revisioned("subscriptions", value.application_id, value, expected_revision=expected_revision, loader=ApplicationSubscription.from_mapping)

    def _selection_identity(self, webspace_id: str, application_id: str) -> str:
        return f"{webspace_id}:{application_id}"

    def get_runtime_selection(self, webspace_id: str, application_id: str) -> RuntimeSelection:
        identity = self._selection_identity(webspace_id, application_id)
        path = self._current_path("runtime_selections", identity)
        if not path.is_file():
            raise FileNotFoundError(f"RuntimeSelection not found: {identity}")
        value = RuntimeSelection.from_mapping(_read(path))
        if value.webspace_id != webspace_id or value.application_id != application_id:
            raise ApplicationStoreError("RuntimeSelection path identity mismatch")
        return value

    def list_runtime_selections(self) -> tuple[RuntimeSelection, ...]:
        return tuple(sorted(self._list_current("runtime_selections", RuntimeSelection.from_mapping), key=lambda item: (item.webspace_id, item.application_id)))

    def save_runtime_selection(self, value: RuntimeSelection, *, expected_revision: int) -> RuntimeSelection:
        self.get_application(value.application_id)
        identity = self._selection_identity(value.webspace_id, value.application_id)
        return self._save_revisioned("runtime_selections", identity, value, expected_revision=expected_revision, loader=RuntimeSelection.from_mapping)

    def get_operation(self, operation_id: str) -> ApplicationOperation:
        path = self._current_path("operations", operation_id)
        if not path.is_file():
            raise FileNotFoundError(f"ApplicationOperation not found: {operation_id}")
        value = ApplicationOperation.from_mapping(_read(path))
        if value.operation_id != operation_id:
            raise ApplicationStoreError("ApplicationOperation path identity mismatch")
        return value

    def list_operations(self, application_id: str | None = None) -> tuple[ApplicationOperation, ...]:
        values = self._list_current("operations", ApplicationOperation.from_mapping)
        if application_id is not None:
            values = tuple(item for item in values if item.application_id == application_id)
        return tuple(sorted(values, key=lambda item: (item.created_at, item.operation_id), reverse=True))

    def put_operation(self, value: ApplicationOperation) -> ApplicationOperation:
        index_path = self.root / "idempotency" / f"{_key(value.idempotency_key)}.json"
        path = self._current_path("operations", value.operation_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if index_path.is_file():
                index = _read(index_path)
                existing = self.get_operation(str(index.get("operation_id") or ""))
                if existing.plan_digest != value.plan_digest or existing.application_id != value.application_id:
                    raise ApplicationStoreError("idempotency key already names another Application plan")
                return existing
            if path.is_file():
                existing = self.get_operation(value.operation_id)
                if existing != value:
                    raise ApplicationStoreError("ApplicationOperation identity conflict")
                return existing
            atomic_write_json(path, value.to_dict())
            atomic_write_json(index_path, {"schema": "adaos.application.idempotency.v1", "idempotency_key": value.idempotency_key, "operation_id": value.operation_id, "plan_digest": value.plan_digest})
            return value

    def save_operation(self, value: ApplicationOperation, *, expected_revision: int) -> ApplicationOperation:
        return self._save_revisioned("operations", value.operation_id, value, expected_revision=expected_revision, loader=ApplicationOperation.from_mapping)

    def get_grant(self, grant_id: str) -> TrialAccessGrant:
        path = self._current_path("trial_access_grants", grant_id)
        if not path.is_file():
            raise FileNotFoundError(f"TrialAccessGrant not found: {grant_id}")
        value = TrialAccessGrant.from_mapping(_read(path))
        if value.grant_id != grant_id:
            raise ApplicationStoreError("TrialAccessGrant path identity mismatch")
        return value

    def list_grants(self, application_id: str | None = None) -> tuple[TrialAccessGrant, ...]:
        values = self._list_current("trial_access_grants", TrialAccessGrant.from_mapping)
        if application_id is not None:
            values = tuple(item for item in values if item.application_id == application_id)
        return tuple(sorted(values, key=lambda item: (item.issued_at, item.grant_id), reverse=True))

    def save_grant(self, value: TrialAccessGrant, *, expected_revision: int) -> TrialAccessGrant:
        self.get_application(value.application_id)
        return self._save_revisioned("trial_access_grants", value.grant_id, value, expected_revision=expected_revision, loader=TrialAccessGrant.from_mapping)

    def put_snapshot_receipt(self, snapshot_ref: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        path = self.root / "snapshots" / f"{_key(snapshot_ref)}.json"
        record = {
            "schema": "adaos.application.snapshot_receipt.v1",
            **dict(payload),
            "receipt_ref": snapshot_ref,
        }
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file() and _read(path) != record:
                raise ApplicationStoreError("immutable snapshot receipt conflict")
            if not path.is_file():
                atomic_write_json(path, record)
        return record


__all__ = [
    "ApplicationChannelConflict",
    "ApplicationRevisionConflict",
    "ApplicationStore",
    "ApplicationStoreError",
]
