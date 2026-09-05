from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import (
    ArtifactReleaseContractError,
    ArtifactSourceRef,
    ProjectRef,
    StableSubscription,
)
from adaos.services.artifact_pipeline.activation import (
    ActivationResult,
    WorkspaceActivationManager,
)
from adaos.services.artifact_pipeline.candidates import CandidateRecord, assert_promotable
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.sources import SourceProvider
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


RELEASE_PLAN_SCHEMA = "adaos.artifact.release_plan.v1"
CHANNEL_INDEX_SCHEMA = "adaos.artifact.channel_index.v1"
SUBSCRIPTION_SET_SCHEMA = "adaos.artifact.subscription_set.v1"
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ChannelError(RuntimeError):
    pass


class ChannelConflictError(ChannelError):
    def __init__(self, *, expected: str | None, observed: str | None) -> None:
        super().__init__(
            "channel compare-and-swap conflict: "
            f"expected {expected or '<absent>'}, observed {observed or '<absent>'}"
        )
        self.expected = expected
        self.observed = observed


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class ChannelPointer:
    project_id: str
    channel: str
    release: str
    release_digest: str
    source_revision: str
    updated_at: str

    def __post_init__(self) -> None:
        try:
            project_id = ProjectRef(self.project_id).project_id
        except ArtifactReleaseContractError as exc:
            raise ChannelError(str(exc)) from exc
        channel = str(self.channel or "").strip()
        if not channel or not all(
            char.isalnum() or char in {"-", "_", "."} for char in channel
        ):
            raise ChannelError("channel must be a safe canonical name")
        release = str(self.release or "").strip()
        if "@" not in release:
            raise ChannelError("channel release must be <project_id>@<semantic-version>")
        release_project, release_version = release.rsplit("@", 1)
        if release_project != project_id or not _SEMVER_RE.fullmatch(release_version):
            raise ChannelError("channel release does not match project identity and semantic version")
        release_digest = str(self.release_digest or "").strip().lower()
        if not _DIGEST_RE.fullmatch(release_digest):
            raise ChannelError("channel release_digest must be sha256:<64 lowercase hex characters>")
        source_revision = str(self.source_revision or "").strip()
        try:
            source_revision = ArtifactSourceRef(
                forge="channel",
                repository="channel-pointer",
                revision=source_revision,
            ).revision
        except ArtifactReleaseContractError as exc:
            raise ChannelError(f"invalid channel source revision: {exc}") from exc
        updated_at = str(self.updated_at or "").strip()
        try:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ChannelError("channel updated_at must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ChannelError("channel updated_at must include a timezone")
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "release", release)
        object.__setattr__(self, "release_digest", release_digest)
        object.__setattr__(self, "source_revision", source_revision)
        object.__setattr__(self, "updated_at", updated_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "channel": self.channel,
            "release": self.release,
            "release_digest": self.release_digest,
            "source_revision": self.source_revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ChannelPointer":
        allowed = {
            "project_id",
            "channel",
            "release",
            "release_digest",
            "source_revision",
            "updated_at",
        }
        unknown = set(value) - allowed
        missing = allowed - set(value)
        if unknown:
            raise ChannelError(
                "channel pointer contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        if missing:
            raise ChannelError(
                "channel pointer is missing required fields: "
                + ", ".join(sorted(missing))
            )
        return cls(
            project_id=str(value.get("project_id") or ""),
            channel=str(value.get("channel") or ""),
            release=str(value.get("release") or ""),
            release_digest=str(value.get("release_digest") or ""),
            source_revision=str(value.get("source_revision") or ""),
            updated_at=str(value.get("updated_at") or ""),
        )


class ReleaseRepository:
    """Immutable ProjectRelease plans plus mutable discovery channels."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def release_path(self, project_id: str, release_digest: str) -> Path:
        project_id = ProjectRef(project_id).project_id
        token = str(release_digest or "")
        if (
            not token.startswith("sha256:")
            or len(token) != 71
            or any(char not in "0123456789abcdef" for char in token.split(":", 1)[1])
        ):
            raise ChannelError("release digest must be sha256:<64 lowercase hex characters>")
        return self.root / "projects" / project_id / "releases" / f"{token.split(':', 1)[1]}.json"

    def channel_path(self, project_id: str) -> Path:
        project_id = ProjectRef(project_id).project_id
        return self.root / "projects" / project_id / "channels.json"

    def mutation_lock_path(self, project_id: str) -> Path:
        project_id = ProjectRef(project_id).project_id
        return self.root / "projects" / project_id / ".mutation.lock"

    def _release_digests_by_version(self, project_id: str) -> dict[str, str]:
        project_id = ProjectRef(project_id).project_id
        release_root = self.root / "projects" / project_id / "releases"
        versions: dict[str, str] = {}
        if not release_root.is_dir():
            return versions
        for path in sorted(release_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, Mapping) or payload.get("schema") != RELEASE_PLAN_SCHEMA:
                    raise ChannelError(f"unsupported release plan record: {path}")
                stored = ReleasePlan.from_mapping(payload)
            except ChannelError:
                raise
            except Exception as exc:
                raise ChannelError(f"cannot read immutable release record {path}: {exc}") from exc
            if stored.release.project_id != project_id:
                raise ChannelError(f"release record {path} belongs to a different project")
            digest = stored.release.release_digest or stored.release.computed_digest()
            previous = versions.get(stored.release.version)
            if previous is not None and previous != digest:
                raise ChannelError(
                    f"project version {project_id}@{stored.release.version} maps to multiple release digests"
                )
            versions[stored.release.version] = digest
        return versions

    def release_digests_by_version(self, project_id: str) -> dict[str, str]:
        """Return the immutable local version index for one Project."""

        return dict(self._release_digests_by_version(project_id))

    def put_release(self, plan: ReleasePlan) -> Path:
        digest = plan.release.release_digest or plan.release.computed_digest()
        path = self.release_path(plan.release.project_id, digest)
        payload = {"schema": RELEASE_PLAN_SCHEMA, **plan.explain()}
        with mutation_lock(self.mutation_lock_path(plan.release.project_id)):
            versions = self._release_digests_by_version(plan.release.project_id)
            existing_digest = versions.get(plan.release.version)
            if existing_digest is not None and existing_digest != digest:
                raise ChannelError(
                    f"project version {plan.release.project_id}@{plan.release.version} "
                    f"already maps to {existing_digest}, not {digest}"
                )
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != payload:
                    raise ChannelError(f"immutable release already exists with different content: {digest}")
                return path
            atomic_write_json(path, payload)
        return path

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        path = self.release_path(project_id, release_digest)
        if not path.is_file():
            raise FileNotFoundError(f"release not found: {project_id}@{release_digest}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema") != RELEASE_PLAN_SCHEMA:
            raise ChannelError("unsupported release plan record")
        plan = ReleasePlan.from_mapping(payload)
        actual = plan.release.release_digest or plan.release.computed_digest()
        if actual != release_digest:
            raise ChannelError("release record digest does not match requested identity")
        return plan

    def _read_channels(self, project_id: str) -> dict[str, Any]:
        project_id = ProjectRef(project_id).project_id
        path = self.channel_path(project_id)
        if not path.is_file():
            return {
                "schema": CHANNEL_INDEX_SCHEMA,
                "project_id": project_id,
                "channels": {},
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != CHANNEL_INDEX_SCHEMA:
            raise ChannelError("unsupported channel index")
        unknown = set(payload) - {"schema", "project_id", "channels", "updated_at"}
        if unknown:
            raise ChannelError(
                "channel index contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        if payload.get("project_id") != project_id:
            raise ChannelError("channel index belongs to a different project")
        channels = payload.get("channels")
        if not isinstance(channels, Mapping):
            raise ChannelError("channel index channels must be an object")
        for channel, raw_pointer in channels.items():
            if not isinstance(raw_pointer, Mapping):
                raise ChannelError("channel index entries must be objects")
            pointer = ChannelPointer.from_mapping(raw_pointer)
            if pointer.project_id != project_id or pointer.channel != channel:
                raise ChannelError("channel index key does not match pointer identity")
        return payload

    def set_channel(
        self,
        project_id: str,
        channel: str,
        release_digest: str,
        *,
        expected_release_digest: str | None,
    ) -> ChannelPointer:
        with mutation_lock(self.mutation_lock_path(project_id)):
            plan = self.get_release(project_id, release_digest)
            versions = self._release_digests_by_version(project_id)
            if versions.get(plan.release.version) != release_digest:
                raise ChannelError(
                    f"release {project_id}@{plan.release.version} is not the canonical digest for its version"
                )
            channel_id = str(channel or "").strip()
            if not channel_id or not all(char.isalnum() or char in {"-", "_", "."} for char in channel_id):
                raise ChannelError("channel must be a safe canonical name")
            pointer = ChannelPointer(
                project_id=project_id,
                channel=channel_id,
                release=f"{project_id}@{plan.release.version}",
                release_digest=release_digest,
                source_revision=plan.release.source_ref.revision,
                updated_at=_now_iso(),
            )
            payload = self._read_channels(project_id)
            channels = dict(payload.get("channels") or {})
            previous = channels.get(channel_id)
            if isinstance(previous, Mapping) and previous.get("release_digest") == release_digest:
                return ChannelPointer.from_mapping(previous)
            observed = (
                str(previous.get("release_digest") or "")
                if isinstance(previous, Mapping)
                else None
            )
            if observed != expected_release_digest:
                raise ChannelConflictError(
                    expected=expected_release_digest,
                    observed=observed,
                )
            channels[channel_id] = pointer.to_dict()
            payload["channels"] = {key: channels[key] for key in sorted(channels)}
            payload["updated_at"] = pointer.updated_at
            atomic_write_json(self.channel_path(project_id), payload)
            return pointer

    def get_channel(self, project_id: str, channel: str = "stable") -> ChannelPointer:
        payload = self._read_channels(project_id)
        raw = (payload.get("channels") or {}).get(channel)
        if not isinstance(raw, Mapping):
            raise FileNotFoundError(f"channel not found: {project_id}:{channel}")
        return ChannelPointer.from_mapping(raw)

    def clear_channel(
        self,
        project_id: str,
        channel: str,
        *,
        expected_release_digest: str,
    ) -> ChannelPointer:
        with mutation_lock(self.mutation_lock_path(project_id)):
            payload = self._read_channels(project_id)
            channels = dict(payload.get("channels") or {})
            raw = channels.get(channel)
            if not isinstance(raw, Mapping):
                raise ChannelConflictError(expected=expected_release_digest, observed=None)
            pointer = ChannelPointer.from_mapping(raw)
            if pointer.release_digest != expected_release_digest:
                raise ChannelConflictError(
                    expected=expected_release_digest,
                    observed=pointer.release_digest,
                )
            channels.pop(channel)
            payload["channels"] = {key: channels[key] for key in sorted(channels)}
            payload["updated_at"] = _now_iso()
            atomic_write_json(self.channel_path(project_id), payload)
            return pointer

    def get_channel_release(self, project_id: str, channel: str = "stable") -> ReleasePlan:
        pointer = self.get_channel(project_id, channel)
        return self.get_release(project_id, pointer.release_digest)


def promote_candidate(
    *,
    candidate: CandidateRecord,
    plan: ReleasePlan,
    current_stable: ReleasePlan | None,
    repository: ReleaseRepository,
    source_provider: SourceProvider,
    channel: str = "stable",
) -> ChannelPointer:
    assert_promotable(
        candidate,
        plan.release,
        current_stable.release if current_stable is not None else None,
    )
    if candidate.source_tree:
        actual_tree = source_provider.tree_revision(candidate.source_ref)
        if actual_tree != candidate.source_tree:
            raise ChannelError(
                f"candidate source tree differs from persisted source: {actual_tree} != {candidate.source_tree}"
            )
    repository.put_release(plan)
    digest = plan.release.release_digest or plan.release.computed_digest()
    expected = (
        current_stable.release.release_digest or current_stable.release.computed_digest()
        if current_stable is not None
        else None
    )
    return repository.set_channel(
        plan.release.project_id,
        channel,
        digest,
        expected_release_digest=expected,
    )


@dataclass(frozen=True, slots=True)
class UpdateNotice:
    subscription: StableSubscription
    pointer: ChannelPointer
    available: bool
    activation_allowed: bool
    reason: str


class SubscriptionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> dict[str, StableSubscription]:
        if not self.path.is_file():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema") != SUBSCRIPTION_SET_SCHEMA:
            raise ChannelError("unsupported subscription set")
        unknown = set(payload) - {"schema", "subscriptions"}
        if unknown:
            raise ChannelError(
                "subscription set contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        raw_subscriptions = payload.get("subscriptions")
        if not isinstance(raw_subscriptions, list) or any(
            not isinstance(item, Mapping) for item in raw_subscriptions
        ):
            raise ChannelError("subscription set must contain a list of objects")
        result: dict[str, StableSubscription] = {}
        for item in raw_subscriptions:
            try:
                subscription = StableSubscription.from_mapping(item)
            except ArtifactReleaseContractError as exc:
                raise ChannelError(f"invalid subscription: {exc}") from exc
            if subscription.project_id in result:
                raise ChannelError(
                    f"duplicate subscription for {subscription.project_id}"
                )
            result[subscription.project_id] = subscription
        return result

    def save(self, subscription: StableSubscription) -> None:
        self.reconcile(subscription)

    def reconcile(
        self,
        subscription: StableSubscription,
        *,
        remove_project_ids: tuple[str, ...] = (),
    ) -> tuple[StableSubscription, ...]:
        subscriptions = self.load()
        removed: list[StableSubscription] = []
        for project_id in sorted(set(remove_project_ids)):
            if project_id == subscription.project_id:
                continue
            existing = subscriptions.pop(project_id, None)
            if existing is not None:
                removed.append(existing)
        subscriptions[subscription.project_id] = subscription
        atomic_write_json(
            self.path,
            {
                "schema": SUBSCRIPTION_SET_SCHEMA,
                "subscriptions": [
                    item.to_dict() for _, item in sorted(subscriptions.items())
                ],
            },
        )
        return tuple(removed)


class SubscriptionManager:
    def __init__(self, repository: ReleaseRepository, store: SubscriptionStore) -> None:
        self.repository = repository
        self.store = store

    def subscribe_installed(
        self,
        *,
        project_id: str,
        release: str,
        release_digest: str,
        policy: str = "notify",
    ) -> StableSubscription:
        subscription = StableSubscription(
            project_id=project_id,
            policy=policy,
            installed_release=release,
            installed_digest=release_digest,
        )
        self.store.save(subscription)
        return subscription

    def check(self, subscription: StableSubscription) -> UpdateNotice:
        pointer = self.repository.get_channel(subscription.project_id, subscription.channel)
        available = pointer.release_digest != subscription.installed_digest
        allowed = available and subscription.policy == "notify"
        reason = "up_to_date"
        if available and subscription.policy == "pinned":
            reason = "pinned"
        elif available:
            reason = "channel_moved"
        return UpdateNotice(subscription, pointer, available, allowed, reason)

    def activate_update(
        self,
        subscription: StableSubscription,
        activation: WorkspaceActivationManager,
        *,
        idempotency_key: str,
        reload_runtime=None,
        health_check=None,
        reload_policy=None,
        health_policy=None,
        permission_decision=None,
        migration_executor=None,
        migration_rollback=None,
    ) -> tuple[StableSubscription, ActivationResult]:
        notice = self.check(subscription)
        if not notice.available:
            raise ChannelError("subscription is already up to date")
        if not notice.activation_allowed:
            raise ChannelError("pinned subscription requires an explicit policy change")
        plan = self.repository.get_release(
            subscription.project_id,
            notice.pointer.release_digest,
        )
        result = activation.activate(
            plan,
            idempotency_key=idempotency_key,
            reload_runtime=reload_runtime,
            health_check=health_check,
            reload_policy=reload_policy,
            health_policy=health_policy,
            permission_decision=permission_decision,
            migration_executor=migration_executor,
            migration_rollback=migration_rollback,
        )
        updated = replace(
            subscription,
            installed_release=notice.pointer.release,
            installed_digest=notice.pointer.release_digest,
        )
        self.store.save(updated)
        return updated, result


__all__ = [
    "CHANNEL_INDEX_SCHEMA",
    "RELEASE_PLAN_SCHEMA",
    "SUBSCRIPTION_SET_SCHEMA",
    "ChannelError",
    "ChannelConflictError",
    "ChannelPointer",
    "ReleaseRepository",
    "SubscriptionManager",
    "SubscriptionStore",
    "UpdateNotice",
    "promote_candidate",
]
