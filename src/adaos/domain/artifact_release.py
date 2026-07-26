from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Literal, Mapping


ArtifactKind = Literal["skill", "scenario"]
SubscriptionPolicy = Literal["notify", "pinned"]

PROJECT_REF_SCHEMA = "adaos.artifact.project_ref.v1"
SOURCE_REF_SCHEMA = "adaos.artifact.source_ref.v1"
PACKAGE_REF_SCHEMA = "adaos.artifact.package_ref.v1"
PROJECT_RELEASE_SCHEMA = "adaos.artifact.project_release.v1"
WORKSPACE_LOCK_SCHEMA = "adaos.workspace.lock.v1"
SUBSCRIPTION_SCHEMA = "adaos.artifact.subscription.v1"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{6,255}$")


class ArtifactReleaseContractError(ValueError):
    pass


def canonical_json_bytes(value: Mapping[str, Any] | list[Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_payload_digest(value: Mapping[str, Any] | list[Any]) -> str:
    return sha256_digest(canonical_json_bytes(value))


def _text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ArtifactReleaseContractError(f"{field} must not be empty")
    return text


def _artifact_id(value: Any, *, field: str) -> str:
    text = _text(value, field=field)
    if not _ID_RE.fullmatch(text):
        raise ArtifactReleaseContractError(f"{field} is not a canonical artifact id: {text!r}")
    return text


def _version(value: Any, *, field: str = "version") -> str:
    text = _text(value, field=field)
    if not _VERSION_RE.fullmatch(text):
        raise ArtifactReleaseContractError(f"{field} is not a semantic version: {text!r}")
    return text


def _digest(value: Any, *, field: str) -> str:
    text = _text(value, field=field).lower()
    if not _DIGEST_RE.fullmatch(text):
        raise ArtifactReleaseContractError(f"{field} must be sha256:<64 lowercase hex characters>")
    return text


def _revision(value: Any) -> str:
    text = _text(value, field="revision")
    if not _REVISION_RE.fullmatch(text):
        raise ArtifactReleaseContractError("revision must be an immutable forge revision identifier")
    return text


def _relative_scope(value: Any) -> str:
    raw = _text(value, field="path_scope").replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or raw.startswith("/") or ":" in parts[0] or any(part == ".." for part in parts):
        raise ArtifactReleaseContractError(f"path_scope must be a safe relative path: {raw!r}")
    return "/".join(parts) + ("/" if raw.endswith("/") else "")


def _unique_texts(values: Iterable[Any], *, field: str) -> tuple[str, ...]:
    merged: list[str] = []
    for raw in values:
        value = _text(raw, field=field)
        if value not in merged:
            merged.append(value)
    return tuple(merged)


def _require_mapping_contract(
    value: Mapping[str, Any],
    *,
    schema: str | None,
    allowed: Iterable[str],
    required: Iterable[str],
    field: str,
) -> None:
    allowed_keys = set(allowed)
    required_keys = set(required)
    if schema is not None:
        actual_schema = value.get("schema")
        if actual_schema != schema:
            raise ArtifactReleaseContractError(
                f"unsupported {field} schema: {actual_schema!r}; expected {schema!r}"
            )
    unknown = sorted(str(key) for key in set(value) - allowed_keys)
    if unknown:
        raise ArtifactReleaseContractError(
            f"{field} contains unsupported fields: {', '.join(unknown)}"
        )
    missing = sorted(required_keys - set(value))
    if missing:
        raise ArtifactReleaseContractError(
            f"{field} is missing required fields: {', '.join(missing)}"
        )


@dataclass(frozen=True, slots=True)
class ProjectRef:
    project_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _artifact_id(self.project_id, field="project_id"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PROJECT_REF_SCHEMA, "project_id": self.project_id}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProjectRef":
        _require_mapping_contract(
            value,
            schema=PROJECT_REF_SCHEMA,
            allowed={"schema", "project_id"},
            required={"schema", "project_id"},
            field="ProjectRef",
        )
        return cls(project_id=value.get("project_id"))


@dataclass(frozen=True, slots=True)
class ArtifactSourceRef:
    forge: str
    repository: str
    revision: str
    path_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "forge", _text(self.forge, field="forge").lower())
        object.__setattr__(self, "repository", _text(self.repository, field="repository"))
        object.__setattr__(self, "revision", _revision(self.revision))
        normalized: list[str] = []
        for item in self.path_scope:
            path = _relative_scope(item)
            if path not in normalized:
                normalized.append(path)
        object.__setattr__(self, "path_scope", tuple(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SOURCE_REF_SCHEMA,
            "forge": self.forge,
            "repository": self.repository,
            "revision": self.revision,
            "path_scope": list(self.path_scope),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ArtifactSourceRef":
        _require_mapping_contract(
            value,
            schema=SOURCE_REF_SCHEMA,
            allowed={"schema", "forge", "repository", "revision", "path_scope"},
            required={"schema", "forge", "repository", "revision", "path_scope"},
            field="SourceRef",
        )
        return cls(
            forge=value.get("forge"),
            repository=value.get("repository"),
            revision=value.get("revision"),
            path_scope=tuple(value.get("path_scope") or ()),
        )


@dataclass(frozen=True, slots=True)
class ArtifactPackageRef:
    kind: ArtifactKind
    artifact_id: str
    version: str
    digest: str
    manifest_digest: str
    source_ref: ArtifactSourceRef

    def __post_init__(self) -> None:
        if self.kind not in {"skill", "scenario"}:
            raise ArtifactReleaseContractError("kind must be skill or scenario")
        object.__setattr__(self, "artifact_id", _artifact_id(self.artifact_id, field="artifact_id"))
        object.__setattr__(self, "version", _version(self.version))
        object.__setattr__(self, "digest", _digest(self.digest, field="digest"))
        object.__setattr__(self, "manifest_digest", _digest(self.manifest_digest, field="manifest_digest"))
        if not isinstance(self.source_ref, ArtifactSourceRef):
            raise ArtifactReleaseContractError("source_ref must be ArtifactSourceRef")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.artifact_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PACKAGE_REF_SCHEMA,
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "digest": self.digest,
            "manifest_digest": self.manifest_digest,
            "source_ref": self.source_ref.to_dict(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ArtifactPackageRef":
        _require_mapping_contract(
            value,
            schema=PACKAGE_REF_SCHEMA,
            allowed={
                "schema",
                "kind",
                "artifact_id",
                "version",
                "digest",
                "manifest_digest",
                "source_ref",
            },
            required={
                "schema",
                "kind",
                "artifact_id",
                "version",
                "digest",
                "manifest_digest",
                "source_ref",
            },
            field="PackageRef",
        )
        source = value.get("source_ref")
        if not isinstance(source, Mapping):
            raise ArtifactReleaseContractError("source_ref must be an object")
        return cls(
            kind=value.get("kind"),
            artifact_id=value.get("artifact_id"),
            version=value.get("version"),
            digest=value.get("digest"),
            manifest_digest=value.get("manifest_digest"),
            source_ref=ArtifactSourceRef.from_mapping(source),
        )


@dataclass(frozen=True, slots=True)
class ResolvedDependency:
    kind: ArtifactKind
    artifact_id: str
    version: str
    package_digest: str
    version_spec: str = ""
    optional: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"skill", "scenario"}:
            raise ArtifactReleaseContractError("dependency kind must be skill or scenario")
        object.__setattr__(self, "artifact_id", _artifact_id(self.artifact_id, field="dependency.artifact_id"))
        object.__setattr__(self, "version", _version(self.version, field="dependency.version"))
        object.__setattr__(self, "package_digest", _digest(self.package_digest, field="dependency.package_digest"))
        object.__setattr__(self, "version_spec", str(self.version_spec or "").strip())

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.artifact_id}"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "package_digest": self.package_digest,
            "optional": bool(self.optional),
        }
        if self.version_spec:
            payload["version_spec"] = self.version_spec
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ResolvedDependency":
        _require_mapping_contract(
            value,
            schema=None,
            allowed={
                "kind",
                "artifact_id",
                "version",
                "package_digest",
                "version_spec",
                "optional",
            },
            required={"kind", "artifact_id", "version", "package_digest", "optional"},
            field="ResolvedDependency",
        )
        return cls(
            kind=value.get("kind"),
            artifact_id=value.get("artifact_id"),
            version=value.get("version"),
            package_digest=value.get("package_digest"),
            version_spec=value.get("version_spec") or "",
            optional=value.get("optional") is True,
        )


@dataclass(frozen=True, slots=True)
class ProjectRelease:
    project_id: str
    version: str
    source_ref: ArtifactSourceRef
    components: tuple[ArtifactPackageRef, ...]
    resolved_dependencies: tuple[ResolvedDependency, ...] = ()
    permissions: tuple[str, ...] = ()
    migrations: tuple[Mapping[str, Any], ...] = ()
    validation_evidence: tuple[Mapping[str, Any], ...] = ()
    release_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _artifact_id(self.project_id, field="project_id"))
        object.__setattr__(self, "version", _version(self.version))
        if not isinstance(self.source_ref, ArtifactSourceRef):
            raise ArtifactReleaseContractError("source_ref must be ArtifactSourceRef")
        if not self.components:
            raise ArtifactReleaseContractError("ProjectRelease requires at least one component")
        component_keys = [item.key for item in self.components]
        if len(component_keys) != len(set(component_keys)):
            raise ArtifactReleaseContractError("ProjectRelease component identities must be unique")
        dependency_keys = [item.key for item in self.resolved_dependencies]
        if len(dependency_keys) != len(set(dependency_keys)):
            raise ArtifactReleaseContractError("resolved dependency identities must be unique")
        object.__setattr__(self, "permissions", _unique_texts(self.permissions, field="permissions"))
        if self.release_digest is not None:
            object.__setattr__(self, "release_digest", _digest(self.release_digest, field="release_digest"))

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": PROJECT_RELEASE_SCHEMA,
            "project_id": self.project_id,
            "version": self.version,
            "source_ref": self.source_ref.to_dict(),
            "components": [item.to_dict() for item in sorted(self.components, key=lambda item: item.key)],
            "resolved_dependencies": [
                item.to_dict() for item in sorted(self.resolved_dependencies, key=lambda item: item.key)
            ],
            "permissions": sorted(self.permissions),
            "migrations": [dict(item) for item in self.migrations],
            "validation_evidence": [dict(item) for item in self.validation_evidence],
        }

    def computed_digest(self) -> str:
        return canonical_payload_digest(self.unsigned_dict())

    def seal(self) -> "ProjectRelease":
        digest = self.computed_digest()
        if self.release_digest and self.release_digest != digest:
            raise ArtifactReleaseContractError("release_digest does not match ProjectRelease content")
        return replace(self, release_digest=digest)

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["release_digest"] = self.release_digest or self.computed_digest()
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProjectRelease":
        _require_mapping_contract(
            value,
            schema=PROJECT_RELEASE_SCHEMA,
            allowed={
                "schema",
                "project_id",
                "version",
                "source_ref",
                "components",
                "resolved_dependencies",
                "permissions",
                "migrations",
                "validation_evidence",
                "release_digest",
            },
            required={
                "schema",
                "project_id",
                "version",
                "source_ref",
                "components",
                "resolved_dependencies",
                "permissions",
                "migrations",
                "validation_evidence",
                "release_digest",
            },
            field="ProjectRelease",
        )
        source = value.get("source_ref")
        if not isinstance(source, Mapping):
            raise ArtifactReleaseContractError("source_ref must be an object")
        components = value.get("components")
        if not isinstance(components, list):
            raise ArtifactReleaseContractError("components must be a list")
        dependencies = value.get("resolved_dependencies") or []
        permissions = value.get("permissions")
        migrations = value.get("migrations")
        validation_evidence = value.get("validation_evidence")
        if not isinstance(dependencies, list):
            raise ArtifactReleaseContractError("resolved_dependencies must be a list")
        if not isinstance(permissions, list):
            raise ArtifactReleaseContractError("permissions must be a list")
        if not isinstance(migrations, list):
            raise ArtifactReleaseContractError("migrations must be a list")
        if not isinstance(validation_evidence, list):
            raise ArtifactReleaseContractError("validation_evidence must be a list")
        if any(not isinstance(item, Mapping) for item in components):
            raise ArtifactReleaseContractError("components must contain only objects")
        if any(not isinstance(item, Mapping) for item in dependencies):
            raise ArtifactReleaseContractError("resolved_dependencies must contain only objects")
        if any(not isinstance(item, Mapping) for item in migrations):
            raise ArtifactReleaseContractError("migrations must contain only objects")
        if any(not isinstance(item, Mapping) for item in validation_evidence):
            raise ArtifactReleaseContractError("validation_evidence must contain only objects")
        release = cls(
            project_id=value.get("project_id"),
            version=value.get("version"),
            source_ref=ArtifactSourceRef.from_mapping(source),
            components=tuple(
                ArtifactPackageRef.from_mapping(item)
                for item in components
                if isinstance(item, Mapping)
            ),
            resolved_dependencies=tuple(
                ResolvedDependency.from_mapping(item)
                for item in dependencies
                if isinstance(item, Mapping)
            ),
            permissions=tuple(permissions),
            migrations=tuple(migrations),
            validation_evidence=tuple(validation_evidence),
            release_digest=value.get("release_digest"),
        )
        return release.seal()


@dataclass(frozen=True, slots=True)
class WorkspaceSlot:
    slot_id: str
    project_id: str
    release: str
    release_digest: str
    audience: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot_id", _artifact_id(self.slot_id, field="slot_id"))
        object.__setattr__(self, "project_id", _artifact_id(self.project_id, field="project_id"))
        object.__setattr__(self, "release", _text(self.release, field="release"))
        object.__setattr__(self, "release_digest", _digest(self.release_digest, field="release_digest"))
        if self.audience is not None:
            object.__setattr__(self, "audience", _text(self.audience, field="audience"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project_id": self.project_id,
            "release": self.release,
            "release_digest": self.release_digest,
        }
        if self.audience:
            payload["audience"] = self.audience
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, slot_id: str) -> "WorkspaceSlot":
        _require_mapping_contract(
            value,
            schema=None,
            allowed={"project_id", "release", "release_digest", "audience"},
            required={"project_id", "release", "release_digest"},
            field="WorkspaceSlot",
        )
        return cls(
            slot_id=slot_id,
            project_id=value.get("project_id"),
            release=value.get("release"),
            release_digest=value.get("release_digest"),
            audience=value.get("audience"),
        )


@dataclass(frozen=True, slots=True)
class DependencyBinding:
    consumer: str
    dependency: str
    package_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "consumer", _text(self.consumer, field="consumer"))
        object.__setattr__(self, "dependency", _text(self.dependency, field="dependency"))
        object.__setattr__(self, "package_digest", _digest(self.package_digest, field="package_digest"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer,
            "dependency": self.dependency,
            "package_digest": self.package_digest,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DependencyBinding":
        _require_mapping_contract(
            value,
            schema=None,
            allowed={"consumer", "dependency", "package_digest"},
            required={"consumer", "dependency", "package_digest"},
            field="DependencyBinding",
        )
        return cls(
            consumer=value.get("consumer"),
            dependency=value.get("dependency"),
            package_digest=value.get("package_digest"),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceLock:
    lock_revision: int
    updated_at: str
    slots: tuple[WorkspaceSlot, ...] = ()
    components: tuple[ArtifactPackageRef, ...] = ()
    bindings: tuple[DependencyBinding, ...] = ()
    previous_lock_revision: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.lock_revision, int) or self.lock_revision < 1:
            raise ArtifactReleaseContractError("lock_revision must be a positive integer")
        object.__setattr__(self, "updated_at", _text(self.updated_at, field="updated_at"))
        if self.previous_lock_revision is not None:
            if self.previous_lock_revision < 1 or self.previous_lock_revision >= self.lock_revision:
                raise ArtifactReleaseContractError("previous_lock_revision must be positive and lower than lock_revision")
        slot_ids = [item.slot_id for item in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ArtifactReleaseContractError("WorkspaceLock slot ids must be unique")
        component_keys = [item.key for item in self.components]
        if len(component_keys) != len(set(component_keys)):
            raise ArtifactReleaseContractError("WorkspaceLock supports one active package per artifact identity")
        packages_by_key = {item.key: item for item in self.components}
        for binding in self.bindings:
            dependency = packages_by_key.get(binding.dependency)
            if dependency is None or dependency.digest != binding.package_digest:
                raise ArtifactReleaseContractError(
                    f"binding for {binding.consumer} references inactive package "
                    f"{binding.dependency}@{binding.package_digest}"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": WORKSPACE_LOCK_SCHEMA,
            "lock_revision": self.lock_revision,
            "updated_at": self.updated_at,
            "slots": {
                item.slot_id: item.to_dict()
                for item in sorted(self.slots, key=lambda value: value.slot_id)
            },
            "components": [item.to_dict() for item in sorted(self.components, key=lambda value: value.key)],
            "bindings": [
                item.to_dict()
                for item in sorted(self.bindings, key=lambda value: (value.consumer, value.dependency))
            ],
        }
        if self.previous_lock_revision is not None:
            payload["previous_lock_revision"] = self.previous_lock_revision
        payload["lock_digest"] = canonical_payload_digest(payload)
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkspaceLock":
        _require_mapping_contract(
            value,
            schema=WORKSPACE_LOCK_SCHEMA,
            allowed={
                "schema",
                "lock_revision",
                "previous_lock_revision",
                "updated_at",
                "slots",
                "components",
                "bindings",
                "lock_digest",
            },
            required={
                "schema",
                "lock_revision",
                "updated_at",
                "slots",
                "components",
                "bindings",
                "lock_digest",
            },
            field="WorkspaceLock",
        )
        raw_slots = value.get("slots") or {}
        if not isinstance(raw_slots, Mapping):
            raise ArtifactReleaseContractError("WorkspaceLock slots must be an object")
        raw_components = value.get("components") or []
        raw_bindings = value.get("bindings") or []
        if not isinstance(raw_components, list) or not isinstance(raw_bindings, list):
            raise ArtifactReleaseContractError("WorkspaceLock components and bindings must be lists")
        if any(not isinstance(item, Mapping) for item in raw_slots.values()):
            raise ArtifactReleaseContractError("WorkspaceLock slots must contain only objects")
        if any(not isinstance(item, Mapping) for item in raw_components):
            raise ArtifactReleaseContractError("WorkspaceLock components must contain only objects")
        if any(not isinstance(item, Mapping) for item in raw_bindings):
            raise ArtifactReleaseContractError("WorkspaceLock bindings must contain only objects")
        lock = cls(
            lock_revision=value.get("lock_revision"),
            previous_lock_revision=value.get("previous_lock_revision"),
            updated_at=value.get("updated_at"),
            slots=tuple(
                WorkspaceSlot.from_mapping(item, slot_id=str(slot_id))
                for slot_id, item in raw_slots.items()
                if isinstance(item, Mapping)
            ),
            components=tuple(
                ArtifactPackageRef.from_mapping(item)
                for item in raw_components
                if isinstance(item, Mapping)
            ),
            bindings=tuple(
                DependencyBinding.from_mapping(item)
                for item in raw_bindings
                if isinstance(item, Mapping)
            ),
        )
        expected = value.get("lock_digest")
        if expected is not None and _digest(expected, field="lock_digest") != lock.to_dict()["lock_digest"]:
            raise ArtifactReleaseContractError("lock_digest does not match WorkspaceLock content")
        return lock


@dataclass(frozen=True, slots=True)
class StableSubscription:
    project_id: str
    channel: str = "stable"
    policy: SubscriptionPolicy = "notify"
    installed_release: str | None = None
    installed_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _artifact_id(self.project_id, field="project_id"))
        object.__setattr__(self, "channel", _artifact_id(self.channel, field="channel"))
        if self.policy not in {"notify", "pinned"}:
            raise ArtifactReleaseContractError("subscription policy must be notify or pinned")
        if self.installed_release is not None:
            object.__setattr__(self, "installed_release", _text(self.installed_release, field="installed_release"))
        if self.installed_digest is not None:
            object.__setattr__(self, "installed_digest", _digest(self.installed_digest, field="installed_digest"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": SUBSCRIPTION_SCHEMA,
            "project_id": self.project_id,
            "channel": self.channel,
            "policy": self.policy,
        }
        if self.installed_release:
            payload["installed_release"] = self.installed_release
        if self.installed_digest:
            payload["installed_digest"] = self.installed_digest
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StableSubscription":
        _require_mapping_contract(
            value,
            schema=SUBSCRIPTION_SCHEMA,
            allowed={
                "schema",
                "project_id",
                "channel",
                "policy",
                "installed_release",
                "installed_digest",
            },
            required={"schema", "project_id", "channel", "policy"},
            field="StableSubscription",
        )
        return cls(
            project_id=value.get("project_id"),
            channel=value.get("channel") or "stable",
            policy=value.get("policy") or "notify",
            installed_release=value.get("installed_release"),
            installed_digest=value.get("installed_digest"),
        )


__all__ = [
    "ArtifactKind",
    "ArtifactPackageRef",
    "ArtifactReleaseContractError",
    "ArtifactSourceRef",
    "DependencyBinding",
    "PACKAGE_REF_SCHEMA",
    "PROJECT_REF_SCHEMA",
    "PROJECT_RELEASE_SCHEMA",
    "ProjectRef",
    "ProjectRelease",
    "ResolvedDependency",
    "SOURCE_REF_SCHEMA",
    "SUBSCRIPTION_SCHEMA",
    "StableSubscription",
    "SubscriptionPolicy",
    "WORKSPACE_LOCK_SCHEMA",
    "WorkspaceLock",
    "WorkspaceSlot",
    "canonical_json_bytes",
    "canonical_payload_digest",
    "sha256_digest",
]
