from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import ProjectRef, StableSubscription
from adaos.services.artifact_pipeline.activation import (
    ActivationResult,
    WorkspaceActivationManager,
)
from adaos.services.artifact_pipeline.candidates import CandidateRecord, assert_promotable
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.sources import SourceProvider
from adaos.services.artifact_pipeline.storage import atomic_write_json


RELEASE_PLAN_SCHEMA = "adaos.artifact.release_plan.v1"
CHANNEL_INDEX_SCHEMA = "adaos.artifact.channel_index.v1"
SUBSCRIPTION_SET_SCHEMA = "adaos.artifact.subscription_set.v1"


class ChannelError(RuntimeError):
    pass


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

    def put_release(self, plan: ReleasePlan) -> Path:
        digest = plan.release.release_digest or plan.release.computed_digest()
        path = self.release_path(plan.release.project_id, digest)
        payload = {"schema": RELEASE_PLAN_SCHEMA, **plan.explain()}
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
        return payload

    def set_channel(self, project_id: str, channel: str, release_digest: str) -> ChannelPointer:
        plan = self.get_release(project_id, release_digest)
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
    return repository.set_channel(plan.release.project_id, channel, digest)


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
        result: dict[str, StableSubscription] = {}
        for item in payload.get("subscriptions") or ():
            if isinstance(item, Mapping):
                subscription = StableSubscription.from_mapping(item)
                result[subscription.project_id] = subscription
        return result

    def save(self, subscription: StableSubscription) -> None:
        subscriptions = self.load()
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
    "ChannelPointer",
    "ReleaseRepository",
    "SubscriptionManager",
    "SubscriptionStore",
    "UpdateNotice",
    "promote_candidate",
]
