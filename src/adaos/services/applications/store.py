from __future__ import annotations

import base64
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
from adaos.domain.application_access import ApplicationAccessGrant
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


def _encode_event_cursor(sequence: int) -> str:
    raw = json.dumps({"after": int(sequence)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_event_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        token = str(cursor).strip()
        payload = json.loads(
            base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
        )
    except Exception as exc:
        raise ApplicationStoreError("invalid Application operation event cursor") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"after"}:
        raise ApplicationStoreError("invalid Application operation event cursor")
    sequence = payload.get("after")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ApplicationStoreError("invalid Application operation event cursor")
    return sequence


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

    def delete_unpublished_application(
        self,
        application_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Delete one exact local-only Application definition.

        Published, installed, subscribed, selected, or access-bearing Applications
        are lifecycle records rather than disposable development state.  Refuse
        deletion whenever any of those durable references exist.
        """

        with mutation_lock(self.lock_path, timeout_s=30.0):
            application = self.get_application(application_id)
            if application.revision != expected_revision:
                raise ApplicationRevisionConflict(
                    expected=expected_revision,
                    observed=application.revision,
                )
            if self.list_releases(application_id):
                raise ApplicationStoreError(
                    "published Application releases cannot be deleted as local development"
                )
            channels_path = self._channel_path(application_id)
            if channels_path.is_file() and (self.get_channels(application_id).get("channels") or {}):
                raise ApplicationStoreError(
                    "published Application channels cannot be deleted as local development"
                )
            referenced = (
                any(item.application_id == application_id for item in self.list_installations())
                or any(item.application_id == application_id for item in self.list_subscriptions())
                or any(item.application_id == application_id for item in self.list_runtime_selections())
                or bool(self.list_grants(application_id))
                or bool(self.list_application_access_grants(application_id))
            )
            if referenced:
                raise ApplicationStoreError(
                    "installed, selected, subscribed, or access-bearing Application cannot be deleted"
                )
            path = self._current_path("definitions", application_id)
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass
            if channels_path.is_file():
                channels_path.unlink()
            return {
                "ok": True,
                "application_id": application_id,
                "revision": application.revision,
                "definition_removed": True,
            }

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
                existing = ApplicationRelease.from_mapping(_read(path))
                if existing.to_dict() != payload:
                    raise ApplicationStoreError("immutable ApplicationRelease conflict")
                return existing
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload)
        return value

    def import_public_registry_release(
        self,
        application: Application,
        release: ApplicationRelease,
        *,
        stable_release_digest: str,
        local_publisher_ref: str | None = None,
    ) -> dict[str, Any]:
        """Import one verified public registry projection into local authority.

        This is a catalog hydration boundary, not an installation.  It never
        creates an ApplicationInstallation, RuntimeSelection, grant, credential,
        or local provider binding.  Locally published aggregates cannot be
        overwritten by a registry observation.
        """

        if application.visibility != "public":
            raise ApplicationStoreError(
                "registry Application visibility must be public"
            )
        if (
            application.application_id != release.application_id
            or application.publisher_ref != release.publisher_ref
            or application.legacy_project_id != release.project_release.project_id
            or stable_release_digest != release.release_digest
        ):
            raise ApplicationStoreError(
                "registry Application, release, and stable channel identities differ"
            )
        local_publisher = str(local_publisher_ref or "").strip().lower()
        with mutation_lock(self.lock_path, timeout_s=30.0):
            try:
                current = self.get_application(application.application_id)
            except FileNotFoundError:
                current = None

            definition_status = "imported"
            if current is not None:
                if (
                    current.publisher_ref != application.publisher_ref
                    or current.legacy_project_id != application.legacy_project_id
                ):
                    raise ApplicationStoreError(
                        "registry Application conflicts with local identity"
                    )
                if current.publisher_ref.lower() == local_publisher:
                    if current.to_dict() != application.to_dict():
                        raise ApplicationStoreError(
                            "registry cannot overwrite a locally published Application"
                        )
                    definition_status = "local_exact"
                elif current.revision > application.revision:
                    definition_status = "newer_local_observation"
                elif current.revision == application.revision:
                    if current.to_dict() != application.to_dict():
                        raise ApplicationStoreError(
                            "registry Application revision has conflicting content"
                        )
                    definition_status = "unchanged"
                else:
                    atomic_write_json(
                        self._current_path(
                            "definitions", application.application_id
                        ),
                        application.to_dict(),
                    )
                    definition_status = "updated"
            else:
                for item in self.list_applications():
                    if item.legacy_project_id == application.legacy_project_id:
                        raise ApplicationStoreError(
                            "registry Project is already mapped to another Application"
                        )
                atomic_write_json(
                    self._current_path("definitions", application.application_id),
                    application.to_dict(),
                )

            release_path = self._release_path(
                application.application_id, release.release_digest
            )
            release_payload = release.to_dict()
            if release_path.is_file():
                existing_release = ApplicationRelease.from_mapping(
                    _read(release_path)
                )
                if existing_release.to_dict() != release_payload:
                    raise ApplicationStoreError(
                        "immutable registry ApplicationRelease conflict"
                    )
                release_status = "unchanged"
            else:
                release_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(release_path, release_payload)
                release_status = "imported"

            channels_path = self._channel_path(application.application_id)
            channels = (
                self.get_channels(application.application_id)
                if channels_path.is_file()
                else {
                    "schema": "adaos.application.channel_set.v1",
                    "application_id": application.application_id,
                    "revision": 0,
                    "channels": {},
                }
            )
            observed_stable = (channels.get("channels") or {}).get("stable")
            if (
                current is not None
                and current.publisher_ref.lower() == local_publisher
                and observed_stable not in {None, stable_release_digest}
            ):
                raise ApplicationStoreError(
                    "registry cannot move a locally published stable channel"
                )
            channel_status = "unchanged"
            if observed_stable != stable_release_digest:
                updated_channels = dict(channels.get("channels") or {})
                updated_channels["stable"] = stable_release_digest
                updated_channels.pop("prerelease", None)
                atomic_write_json(
                    channels_path,
                    {
                        "schema": "adaos.application.channel_set.v1",
                        "application_id": application.application_id,
                        "revision": int(channels.get("revision") or 0) + 1,
                        "channels": {
                            key: updated_channels[key]
                            for key in sorted(updated_channels)
                        },
                    },
                )
                channel_status = "updated"

        return {
            "application_id": application.application_id,
            "release_digest": release.release_digest,
            "definition_status": definition_status,
            "release_status": release_status,
            "channel_status": channel_status,
        }

    def get_release(self, application_id: str, release_digest: str) -> ApplicationRelease:
        path = self._release_path(application_id, release_digest)
        if not path.is_file():
            raise FileNotFoundError(f"ApplicationRelease not found: {application_id}@{release_digest}")
        value = ApplicationRelease.from_mapping(_read(path))
        if value.application_id != application_id or value.release_digest != release_digest:
            raise ApplicationStoreError("ApplicationRelease path identity mismatch")
        return value

    def get_release_summary(self, application_id: str, release_digest: str) -> dict[str, Any]:
        """Read only immutable release fields required by catalog projections."""

        path = self._release_path(application_id, release_digest)
        if not path.is_file():
            raise FileNotFoundError(f"ApplicationRelease not found: {application_id}@{release_digest}")
        raw = _read(path)
        observed_application_id = str(raw.get("application_id") or "")
        observed_digest = str(raw.get("release_digest") or "")
        if observed_application_id != application_id or observed_digest != release_digest:
            raise ApplicationStoreError("ApplicationRelease path identity mismatch")
        project = raw.get("project_release")
        project = dict(project) if isinstance(project, Mapping) else {}
        catalog = project.get("catalog")
        catalog = dict(catalog) if isinstance(catalog, Mapping) else {}
        summary: dict[str, Any] = {
            "schema": "adaos.application.release_summary.v1",
            "application_id": observed_application_id,
            "publisher_ref": raw.get("publisher_ref"),
            "legacy_project_id": raw.get("legacy_project_id"),
            "version": raw.get("version") or project.get("version"),
            "release_digest": observed_digest,
            "lifecycle": raw.get("lifecycle"),
            "project_release": {
                "project_id": project.get("project_id"),
                "version": project.get("version"),
                "release_digest": project.get("release_digest"),
                "catalog": catalog,
            },
        }
        if raw.get("published_at") is not None:
            summary["published_at"] = raw["published_at"]
        return summary

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
        from .runtime_channel import ApplicationRuntimeChannel

        channel = ApplicationRuntimeChannel(self.state_dir, application_id).read()
        if channel is not None:
            selected = next((item for item in channel if item.webspace_id == webspace_id), None)
            if selected is None:
                raise FileNotFoundError(f"RuntimeSelection not found: {webspace_id}:{application_id}")
            return selected
        identity = self._selection_identity(webspace_id, application_id)
        path = self._current_path("runtime_selections", identity)
        if not path.is_file():
            raise FileNotFoundError(f"RuntimeSelection not found: {identity}")
        value = RuntimeSelection.from_mapping(_read(path))
        if value.webspace_id != webspace_id or value.application_id != application_id:
            raise ApplicationStoreError("RuntimeSelection path identity mismatch")
        return value

    def list_runtime_selections(self) -> tuple[RuntimeSelection, ...]:
        from .runtime_channel import ApplicationRuntimeChannel

        channels = ApplicationRuntimeChannel.list_selections(self.state_dir)
        application_ids = {item.application_id for item in channels}
        values = [item for item in self._list_current("runtime_selections", RuntimeSelection.from_mapping)
                  if item.application_id not in application_ids] + list(channels)
        return tuple(sorted(values, key=lambda item: (item.webspace_id, item.application_id)))

    def save_runtime_selection(self, value: RuntimeSelection, *, expected_revision: int) -> RuntimeSelection:
        from .runtime_channel import ApplicationRuntimeChannel

        self.get_application(value.application_id)
        with mutation_lock(self.lock_path):
            legacy = tuple(item for item in self._list_current("runtime_selections", RuntimeSelection.from_mapping)
                           if item.application_id == value.application_id)
            return ApplicationRuntimeChannel(self.state_dir, value.application_id).select(
                value, expected_revision=expected_revision, legacy=legacy)

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

    def _append_operation_event_unlocked(self, value: ApplicationOperation) -> dict[str, Any]:
        identity = _key(f"{value.operation_id}:{value.revision}")
        index_path = self.root / "operation_event_index" / f"{identity}.json"
        if index_path.is_file():
            sequence = int(_read(index_path)["sequence"])
            return _read(self.root / "operation_events" / f"{sequence:020d}.json")
        sequence_path = self.root / "operation_events" / "sequence.json"
        sequence = int(_read(sequence_path).get("sequence") or 0) + 1 if sequence_path.is_file() else 1
        while (self.root / "operation_events" / f"{sequence:020d}.json").exists():
            sequence += 1
        event = {
            "schema": "adaos.application.operation_event.v1",
            "sequence": sequence,
            "event_id": f"appopevent.{identity}",
            "application_id": value.application_id,
            "operation_id": value.operation_id,
            "operation_revision": value.revision,
            "status": value.status,
            "occurred_at": value.updated_at,
            "operation": value.to_dict(),
        }
        event_path = self.root / "operation_events" / f"{sequence:020d}.json"
        if event_path.exists():
            raise ApplicationStoreError("Application operation event sequence conflict")
        atomic_write_json(event_path, event)
        atomic_write_json(index_path, {"schema": "adaos.application.operation_event_index.v1", "sequence": sequence})
        atomic_write_json(sequence_path, {"schema": "adaos.application.operation_event_sequence.v1", "sequence": sequence})
        return event

    def list_operation_events(
        self,
        *,
        application_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[dict[str, Any], ...], str | None]:
        after = _decode_event_cursor(cursor)
        size = max(1, min(int(limit), 200))
        parent = self.root / "operation_events"
        selected: list[dict[str, Any]] = []
        if parent.is_dir():
            for path in sorted(parent.glob("[0-9]*.json")):
                event = _read(path)
                sequence = int(event.get("sequence") or 0)
                if sequence <= after:
                    continue
                if application_id is not None and event.get("application_id") != application_id:
                    continue
                selected.append(event)
                if len(selected) > size:
                    break
        page = selected[:size]
        next_cursor = _encode_event_cursor(int(page[-1]["sequence"])) if page else cursor
        return tuple(page), next_cursor

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
            self._append_operation_event_unlocked(value)
            atomic_write_json(path, value.to_dict())
            atomic_write_json(index_path, {"schema": "adaos.application.idempotency.v1", "idempotency_key": value.idempotency_key, "operation_id": value.operation_id, "plan_digest": value.plan_digest})
            return value

    def save_operation(self, value: ApplicationOperation, *, expected_revision: int) -> ApplicationOperation:
        path = self._current_path("operations", value.operation_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            current = ApplicationOperation.from_mapping(_read(path)) if path.is_file() else None
            observed = current.revision if current is not None else 0
            if expected_revision != observed:
                raise ApplicationRevisionConflict(expected=expected_revision, observed=observed)
            if value.revision != observed + 1:
                raise ApplicationStoreError("record revision must advance by exactly one")
            self._append_operation_event_unlocked(value)
            atomic_write_json(path, value.to_dict())
            return value

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

    def get_application_access_grant(self, grant_id: str) -> ApplicationAccessGrant:
        path = self._current_path("application_access_grants", grant_id)
        if not path.is_file():
            raise FileNotFoundError(f"ApplicationAccessGrant not found: {grant_id}")
        value = ApplicationAccessGrant.from_mapping(_read(path))
        if value.grant_id != grant_id:
            raise ApplicationStoreError("ApplicationAccessGrant path identity mismatch")
        return value

    def list_application_access_grants(
        self,
        application_id: str | None = None,
        *,
        subject_ref: str | None = None,
    ) -> tuple[ApplicationAccessGrant, ...]:
        values = self._list_current("application_access_grants", ApplicationAccessGrant.from_mapping)
        if application_id is not None:
            values = tuple(item for item in values if item.application_id == application_id)
        if subject_ref is not None:
            values = tuple(item for item in values if item.subject_ref == subject_ref)
        return tuple(sorted(values, key=lambda item: (item.updated_at, item.grant_id), reverse=True))

    def save_application_access_grant(
        self,
        value: ApplicationAccessGrant,
        *,
        expected_revision: int,
    ) -> ApplicationAccessGrant:
        self.get_application(value.application_id)
        return self._save_revisioned(
            "application_access_grants",
            value.grant_id,
            value,
            expected_revision=expected_revision,
            loader=ApplicationAccessGrant.from_mapping,
        )

    def append_application_access_audit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        application_id = str(payload.get("application_id") or "").strip()
        subject_ref = str(payload.get("subject_ref") or "").strip()
        event_action = str(payload.get("action") or payload.get("decision") or "access").strip()
        if not application_id or not subject_ref:
            raise ApplicationStoreError("Application access audit requires application_id and subject_ref")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            parent = self.root / "application_access_audit"
            sequence_path = parent / "sequence.json"
            sequence = int(_read(sequence_path).get("sequence") or 0) + 1 if sequence_path.is_file() else 1
            while (parent / f"{sequence:020d}.json").exists():
                sequence += 1
            event = {
                "schema": "adaos.application.access_audit.v1",
                "sequence": sequence,
                "event_id": f"appaccess.{_key(f'{sequence}:{application_id}:{subject_ref}:{event_action}')}",
                **dict(payload),
            }
            atomic_write_json(parent / f"{sequence:020d}.json", event)
            atomic_write_json(sequence_path, {"schema": "adaos.application.access_audit_sequence.v1", "sequence": sequence})
            return event

    def list_application_access_audit(
        self,
        application_id: str | None = None,
        *,
        subject_ref: str | None = None,
        limit: int = 200,
    ) -> tuple[dict[str, Any], ...]:
        parent = self.root / "application_access_audit"
        size = max(1, min(int(limit), 1000))
        events: list[dict[str, Any]] = []
        if parent.is_dir():
            for path in sorted(parent.glob("[0-9]*.json"), reverse=True):
                event = _read(path)
                if application_id is not None and event.get("application_id") != application_id:
                    continue
                if subject_ref is not None and event.get("subject_ref") != subject_ref:
                    continue
                events.append(event)
                if len(events) >= size:
                    break
        return tuple(events)

    def get_trial_redemption(self, redemption_id: str) -> dict[str, Any]:
        path = self.root / "trial_access_redemptions" / f"{_key(redemption_id)}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Trial access redemption not found: {redemption_id}")
        payload = _read(path)
        if (
            payload.get("schema") != "adaos.application.trial_access_redemption.v1"
            or payload.get("redemption_id") != redemption_id
        ):
            raise ApplicationStoreError("Trial access redemption path identity mismatch")
        return payload

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
