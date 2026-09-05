from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from .artifact_release import ProjectRelease, StableSubscription, canonical_payload_digest
from .project_deployment import ProjectDeployment


APPLICATION_SCHEMA = "adaos.application.v1"
APPLICATION_RELEASE_SCHEMA = "adaos.application.release.v1"
APPLICATION_INSTALLATION_SCHEMA = "adaos.application.installation.v1"
APPLICATION_SUBSCRIPTION_SCHEMA = "adaos.application.subscription.v1"
RUNTIME_SELECTION_SCHEMA = "adaos.application.runtime_selection.v1"
TRIAL_ACCESS_GRANT_SCHEMA = "adaos.application.trial_access_grant.v1"
APPLICATION_OPERATION_SCHEMA = "adaos.application.operation.v1"

ApplicationVisibility = Literal["private", "link", "public"]
ApplicationLifecycle = Literal["active", "retired", "archived"]
UpdateTrack = Literal["stable", "prerelease"]
UpdatePolicy = Literal["notify", "auto_compatible", "pinned"]
RuntimeSelectionSource = Literal[
    "stable_installation", "prerelease_trial", "local_trial"
]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REF_RE = re.compile(r"^(skill|scenario):[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ApplicationContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any, field_name: str, *, maximum: int = 500) -> str:
    token = str(value or "").strip()
    if not token:
        raise ApplicationContractError(f"{field_name} is required")
    if len(token) > maximum:
        raise ApplicationContractError(f"{field_name} exceeds {maximum} characters")
    return token


def _optional_text(value: Any, *, maximum: int = 500) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if len(token) > maximum:
        raise ApplicationContractError(f"text exceeds {maximum} characters")
    return token


def _identifier(value: Any, field_name: str) -> str:
    token = _text(value, field_name, maximum=128).lower()
    if not _ID_RE.fullmatch(token):
        raise ApplicationContractError(f"{field_name} must be a canonical identifier")
    return token


def _publisher_ref(value: Any) -> str:
    token = _text(value, "publisher_ref", maximum=167)
    if not token.startswith("subnet:"):
        raise ApplicationContractError("publisher_ref must use subnet:<id>")
    _identifier(token.split(":", 1)[1], "publisher subnet id")
    return token


def _digest(value: Any, field_name: str) -> str:
    token = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(token):
        raise ApplicationContractError(
            f"{field_name} must be sha256:<64 lowercase hex characters>"
        )
    return token


def _timestamp(value: Any, field_name: str) -> str:
    token = _text(value, field_name, maximum=80)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApplicationContractError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ApplicationContractError(f"{field_name} must include a timezone")
    return token


def _revision(value: Any, field_name: str = "revision", *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ApplicationContractError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApplicationContractError(f"{field_name} must be an integer") from exc
    if parsed < minimum:
        raise ApplicationContractError(f"{field_name} must be at least {minimum}")
    return parsed


def _schema_mapping(
    value: Mapping[str, Any],
    *,
    schema: str,
    allowed: set[str],
    required: set[str],
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApplicationContractError(f"{field_name} must be an object")
    payload = dict(value)
    if payload.get("schema") != schema:
        raise ApplicationContractError(f"unsupported {field_name} schema")
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown:
        raise ApplicationContractError(
            f"{field_name} contains unsupported fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise ApplicationContractError(
            f"{field_name} is missing required fields: {', '.join(sorted(missing))}"
        )
    return payload


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApplicationContractError(f"{field_name} must be an object")
    return dict(value)


def _mapping_tuple(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ApplicationContractError(f"{field_name} must be an array")
    if any(not isinstance(item, Mapping) for item in value):
        raise ApplicationContractError(f"{field_name} must contain only objects")
    return tuple(dict(item) for item in value)


@dataclass(frozen=True, slots=True)
class Application:
    application_id: str
    legacy_project_id: str
    publisher_ref: str
    slug: str
    display: Mapping[str, Any]
    visibility: ApplicationVisibility
    entrypoints: tuple[Mapping[str, Any], ...]
    publisher: Mapping[str, Any]
    lifecycle: ApplicationLifecycle = "active"
    derived_from: Mapping[str, Any] | None = None
    revision: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        object.__setattr__(self, "legacy_project_id", _identifier(self.legacy_project_id, "legacy_project_id"))
        object.__setattr__(self, "publisher_ref", _publisher_ref(self.publisher_ref))
        object.__setattr__(self, "slug", _identifier(self.slug, "slug"))
        display = _mapping(self.display, "display")
        display = {
            "title": _text(display.get("title"), "display.title", maximum=160),
            "summary": _optional_text(display.get("summary"), maximum=500),
        }
        object.__setattr__(self, "display", display)
        if self.visibility not in {"private", "link", "public"}:
            raise ApplicationContractError("visibility must be private, link, or public")
        if self.lifecycle not in {"active", "retired", "archived"}:
            raise ApplicationContractError("application lifecycle is invalid")
        entrypoints = _mapping_tuple(self.entrypoints, "entrypoints")
        if not entrypoints:
            raise ApplicationContractError("Application requires at least one entrypoint")
        normalized_entrypoints: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in entrypoints:
            entrypoint_id = _identifier(raw.get("entrypoint_id"), "entrypoint_id")
            presentation_ref = _text(raw.get("presentation_ref"), "presentation_ref")
            if not _REF_RE.fullmatch(presentation_ref):
                raise ApplicationContractError("presentation_ref must reference a skill or scenario")
            if entrypoint_id in seen:
                raise ApplicationContractError("entrypoint ids must be unique")
            seen.add(entrypoint_id)
            normalized_entrypoints.append(
                {"entrypoint_id": entrypoint_id, "presentation_ref": presentation_ref}
            )
        object.__setattr__(self, "entrypoints", tuple(normalized_entrypoints))
        publisher = _mapping(self.publisher, "publisher")
        publisher_ref = _publisher_ref(publisher.get("publisher_ref"))
        if publisher_ref != self.publisher_ref:
            raise ApplicationContractError("publisher presentation does not match publisher_ref")
        release_key_fingerprint = _digest(
            publisher.get("release_key_fingerprint"), "publisher.release_key_fingerprint"
        )
        object.__setattr__(
            self,
            "publisher",
            {
                "publisher_ref": publisher_ref,
                "display_name": _text(publisher.get("display_name"), "publisher.display_name", maximum=160),
                "subnet_short_ref": _text(publisher.get("subnet_short_ref"), "publisher.subnet_short_ref", maximum=32),
                "release_key_ref": _text(publisher.get("release_key_ref"), "publisher.release_key_ref", maximum=240),
                "release_key_fingerprint": release_key_fingerprint,
                "home_zone": _identifier(publisher.get("home_zone"), "publisher.home_zone"),
                "trust_relation": _text(publisher.get("trust_relation"), "publisher.trust_relation", maximum=40),
            },
        )
        if self.publisher["trust_relation"] not in {"local", "trusted", "unverified", "blocked"}:
            raise ApplicationContractError("publisher.trust_relation is invalid")
        if self.derived_from is not None:
            derived = _mapping(self.derived_from, "derived_from")
            object.__setattr__(
                self,
                "derived_from",
                {
                    "application_id": _identifier(derived.get("application_id"), "derived_from.application_id"),
                    "release_digest": _digest(derived.get("release_digest"), "derived_from.release_digest"),
                    "relationship": "independent_derivative",
                },
            )
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": APPLICATION_SCHEMA,
            "application_id": self.application_id,
            "legacy_project_id": self.legacy_project_id,
            "publisher_ref": self.publisher_ref,
            "slug": self.slug,
            "display": dict(self.display),
            "visibility": self.visibility,
            "entrypoints": [dict(item) for item in self.entrypoints],
            "publisher": dict(self.publisher),
            "lifecycle": self.lifecycle,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.derived_from is not None:
            payload["derived_from"] = dict(self.derived_from)
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Application":
        payload = _schema_mapping(
            value,
            schema=APPLICATION_SCHEMA,
            allowed={
                "schema", "application_id", "legacy_project_id", "publisher_ref", "slug",
                "display", "visibility", "entrypoints", "publisher", "lifecycle",
                "derived_from", "revision", "created_at", "updated_at",
            },
            required={
                "schema", "application_id", "legacy_project_id", "publisher_ref", "slug",
                "display", "visibility", "entrypoints", "publisher", "lifecycle",
                "revision", "created_at", "updated_at",
            },
            field_name="Application",
        )
        payload.pop("schema")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ApplicationRelease:
    application_id: str
    publisher_ref: str
    project_release: ProjectRelease
    accepted_candidate_id: str
    acceptance_evidence: tuple[Mapping[str, Any], ...]
    provenance_refs: tuple[str, ...]
    addresses_report_ids: tuple[str, ...] = ()
    lifecycle: str = "candidate"
    published_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        object.__setattr__(self, "publisher_ref", _publisher_ref(self.publisher_ref))
        if not isinstance(self.project_release, ProjectRelease):
            raise ApplicationContractError("project_release must be ProjectRelease")
        sealed = self.project_release.seal()
        object.__setattr__(self, "project_release", sealed)
        object.__setattr__(self, "accepted_candidate_id", _text(self.accepted_candidate_id, "accepted_candidate_id", maximum=180))
        evidence = _mapping_tuple(self.acceptance_evidence, "acceptance_evidence")
        if not evidence:
            raise ApplicationContractError("ApplicationRelease requires acceptance evidence")
        object.__setattr__(self, "acceptance_evidence", evidence)
        refs = tuple(sorted({_digest(item, "provenance_ref") for item in self.provenance_refs}))
        if not refs:
            raise ApplicationContractError("ApplicationRelease requires provenance refs")
        object.__setattr__(self, "provenance_refs", refs)
        report_ids = tuple(sorted({_identifier(item, "addresses_report_id") for item in self.addresses_report_ids}))
        object.__setattr__(self, "addresses_report_ids", report_ids)
        if self.lifecycle not in {"candidate", "trial", "prerelease", "stable", "superseded", "retired", "archived", "yanked"}:
            raise ApplicationContractError("application release lifecycle is invalid")
        if self.published_at is not None:
            object.__setattr__(self, "published_at", _timestamp(self.published_at, "published_at"))

    @property
    def release_digest(self) -> str:
        return str(self.project_release.release_digest or self.project_release.computed_digest())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": APPLICATION_RELEASE_SCHEMA,
            "application_id": self.application_id,
            "publisher_ref": self.publisher_ref,
            "legacy_project_id": self.project_release.project_id,
            "version": self.project_release.version,
            "release_digest": self.release_digest,
            "project_release": self.project_release.to_dict(),
            "accepted_candidate_id": self.accepted_candidate_id,
            "acceptance_evidence": [dict(item) for item in self.acceptance_evidence],
            "provenance_refs": list(self.provenance_refs),
            "addresses_report_ids": list(self.addresses_report_ids),
            "lifecycle": self.lifecycle,
        }
        if self.published_at is not None:
            payload["published_at"] = self.published_at
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationRelease":
        payload = _schema_mapping(
            value,
            schema=APPLICATION_RELEASE_SCHEMA,
            allowed={
                "schema", "application_id", "publisher_ref", "legacy_project_id", "version",
                "release_digest", "project_release", "accepted_candidate_id", "acceptance_evidence",
                "provenance_refs", "addresses_report_ids", "lifecycle", "published_at",
            },
            required={
                "schema", "application_id", "publisher_ref", "legacy_project_id", "version",
                "release_digest", "project_release", "accepted_candidate_id", "acceptance_evidence",
                "provenance_refs", "lifecycle",
            },
            field_name="ApplicationRelease",
        )
        project_release = ProjectRelease.from_mapping(_mapping(payload["project_release"], "project_release"))
        expected_digest = _digest(payload["release_digest"], "release_digest")
        if project_release.project_id != payload["legacy_project_id"] or project_release.version != payload["version"]:
            raise ApplicationContractError("ApplicationRelease compatibility identity does not match ProjectRelease")
        if (project_release.release_digest or project_release.computed_digest()) != expected_digest:
            raise ApplicationContractError("ApplicationRelease release_digest must preserve ProjectRelease identity")
        return cls(
            application_id=payload["application_id"],
            publisher_ref=payload["publisher_ref"],
            project_release=project_release,
            accepted_candidate_id=payload["accepted_candidate_id"],
            acceptance_evidence=_mapping_tuple(payload["acceptance_evidence"], "acceptance_evidence"),
            provenance_refs=tuple(payload["provenance_refs"]),
            addresses_report_ids=tuple(payload.get("addresses_report_ids") or ()),
            lifecycle=payload["lifecycle"],
            published_at=payload.get("published_at"),
        )


@dataclass(frozen=True, slots=True)
class ApplicationInstallation:
    installation_id: str
    application_id: str
    installed_release_digest: str
    component_refs: tuple[Mapping[str, Any], ...]
    data_policy: str
    status: str
    revision: int
    legacy_deployment_id: str | None = None
    snapshot_ref: str | None = None
    active_runtime_leases: tuple[str, ...] = ()
    rollback_holds: tuple[str, ...] = ()
    uncertain_operation_refs: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "installation_id", _text(self.installation_id, "installation_id", maximum=180))
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        object.__setattr__(self, "installed_release_digest", _digest(self.installed_release_digest, "installed_release_digest"))
        components = _mapping_tuple(self.component_refs, "component_refs")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in components:
            component_ref = _text(raw.get("component_ref"), "component_ref")
            if not _REF_RE.fullmatch(component_ref):
                raise ApplicationContractError("component_ref must reference a skill or scenario")
            if component_ref in seen:
                raise ApplicationContractError("component_refs must be unique")
            seen.add(component_ref)
            lifecycle = _text(raw.get("lifecycle"), "component lifecycle", maximum=16)
            if lifecycle not in {"bound", "shared"}:
                raise ApplicationContractError("component lifecycle must be bound or shared")
            normalized.append({
                "component_ref": component_ref,
                "package_digest": _digest(raw.get("package_digest"), "package_digest"),
                "lifecycle": lifecycle,
            })
        if not normalized:
            raise ApplicationContractError("ApplicationInstallation requires component refs")
        object.__setattr__(self, "component_refs", tuple(sorted(normalized, key=lambda item: item["component_ref"])))
        if self.data_policy not in {"retain", "delete", "snapshot_then_delete"}:
            raise ApplicationContractError("installation data_policy is invalid")
        if self.status not in {"planned", "installing", "active", "updating", "degraded", "removing", "removed", "failed", "unknown"}:
            raise ApplicationContractError("installation status is invalid")
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "legacy_deployment_id", _optional_text(self.legacy_deployment_id, maximum=180))
        object.__setattr__(self, "snapshot_ref", _optional_text(self.snapshot_ref, maximum=300))
        for field_name in ("active_runtime_leases", "rollback_holds", "uncertain_operation_refs"):
            values = tuple(sorted({_text(item, field_name, maximum=300) for item in getattr(self, field_name)}))
            object.__setattr__(self, field_name, values)
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": APPLICATION_INSTALLATION_SCHEMA,
            "installation_id": self.installation_id,
            "application_id": self.application_id,
            "installed_release_digest": self.installed_release_digest,
            "component_refs": [dict(item) for item in self.component_refs],
            "data_policy": self.data_policy,
            "status": self.status,
            "revision": self.revision,
            "legacy_deployment_id": self.legacy_deployment_id,
            "snapshot_ref": self.snapshot_ref,
            "active_runtime_leases": list(self.active_runtime_leases),
            "rollback_holds": list(self.rollback_holds),
            "uncertain_operation_refs": list(self.uncertain_operation_refs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationInstallation":
        required = {
            "schema", "installation_id", "application_id", "installed_release_digest",
            "component_refs", "data_policy", "status", "revision", "legacy_deployment_id",
            "snapshot_ref", "active_runtime_leases", "rollback_holds", "uncertain_operation_refs",
            "created_at", "updated_at",
        }
        payload = _schema_mapping(value, schema=APPLICATION_INSTALLATION_SCHEMA, allowed=required, required=required, field_name="ApplicationInstallation")
        payload.pop("schema")
        payload["component_refs"] = _mapping_tuple(payload["component_refs"], "component_refs")
        for key in ("active_runtime_leases", "rollback_holds", "uncertain_operation_refs"):
            payload[key] = tuple(payload[key])
        return cls(**payload)

    @classmethod
    def from_project_deployment(
        cls,
        application: Application,
        deployment: ProjectDeployment,
        *,
        component_digests: Mapping[str, str],
    ) -> "ApplicationInstallation":
        if deployment.project_ref != f"project:{application.legacy_project_id}":
            raise ApplicationContractError("ProjectDeployment belongs to a different Application")
        missing = sorted(
            placement.component_ref
            for placement in deployment.placements
            if placement.component_ref not in component_digests
        )
        if missing:
            raise ApplicationContractError(
                "component digests are missing for: " + ", ".join(missing)
            )
        refs = tuple(
            {
                "component_ref": placement.component_ref,
                "package_digest": component_digests[placement.component_ref],
                "lifecycle": "shared",
            }
            for placement in deployment.placements
        )
        status = {
            "draft": "planned",
            "planned": "planned",
            "applying": "installing",
        }.get(deployment.status, deployment.status)
        return cls(
            installation_id=f"installation:{deployment.deployment_id}",
            application_id=application.application_id,
            installed_release_digest=deployment.release_digest,
            component_refs=refs,
            data_policy=deployment.retention.runtime_data,
            status=status,
            revision=deployment.revision,
            legacy_deployment_id=deployment.deployment_id,
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
        )


@dataclass(frozen=True, slots=True)
class ApplicationSubscription:
    application_id: str
    update_track: UpdateTrack
    update_policy: UpdatePolicy
    revision: int
    observed_release_digest: str | None = None
    pinned_release_digest: str | None = None
    paused: bool = False
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        if self.update_track not in {"stable", "prerelease"}:
            raise ApplicationContractError("update_track must be stable or prerelease")
        if self.update_policy not in {"notify", "auto_compatible", "pinned"}:
            raise ApplicationContractError("update_policy is invalid")
        object.__setattr__(self, "revision", _revision(self.revision))
        if self.observed_release_digest is not None:
            object.__setattr__(self, "observed_release_digest", _digest(self.observed_release_digest, "observed_release_digest"))
        if self.pinned_release_digest is not None:
            object.__setattr__(self, "pinned_release_digest", _digest(self.pinned_release_digest, "pinned_release_digest"))
        object.__setattr__(self, "paused", bool(self.paused))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": APPLICATION_SUBSCRIPTION_SCHEMA,
            "application_id": self.application_id,
            "update_track": self.update_track,
            "update_policy": self.update_policy,
            "observed_release_digest": self.observed_release_digest,
            "pinned_release_digest": self.pinned_release_digest,
            "paused": self.paused,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationSubscription":
        required = {"schema", "application_id", "update_track", "update_policy", "observed_release_digest", "pinned_release_digest", "paused", "revision", "updated_at"}
        payload = _schema_mapping(value, schema=APPLICATION_SUBSCRIPTION_SCHEMA, allowed=required, required=required, field_name="ApplicationSubscription")
        payload.pop("schema")
        return cls(**payload)

    def to_legacy(self, legacy_project_id: str) -> StableSubscription:
        return StableSubscription(
            project_id=legacy_project_id,
            channel=self.update_track,
            policy="pinned" if self.update_policy == "pinned" else "notify",
            installed_digest=self.observed_release_digest,
        )

    @classmethod
    def from_legacy(
        cls,
        application_id: str,
        legacy: StableSubscription,
        *,
        revision: int = 1,
    ) -> "ApplicationSubscription":
        return cls(
            application_id=application_id,
            update_track="prerelease" if legacy.channel == "prerelease" else "stable",
            update_policy="pinned" if legacy.policy == "pinned" else "notify",
            observed_release_digest=legacy.installed_digest,
            pinned_release_digest=legacy.installed_digest if legacy.policy == "pinned" else None,
            revision=revision,
        )


@dataclass(frozen=True, slots=True)
class RuntimeSelection:
    webspace_id: str
    application_id: str
    source: RuntimeSelectionSource
    release_digest: str
    runtime_root_ref: str
    revision: int
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "webspace_id", _identifier(self.webspace_id, "webspace_id"))
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        if self.source not in {"stable_installation", "prerelease_trial", "local_trial"}:
            raise ApplicationContractError("runtime selection source is invalid")
        object.__setattr__(self, "release_digest", _digest(self.release_digest, "release_digest"))
        root_ref = _text(self.runtime_root_ref, "runtime_root_ref", maximum=200)
        if root_ref != "workspace" and not root_ref.startswith("trial:"):
            raise ApplicationContractError("runtime_root_ref must be workspace or trial:<candidate-id>")
        if self.source == "stable_installation" and root_ref != "workspace":
            raise ApplicationContractError("stable installation must select the Workspace runtime")
        if self.source != "stable_installation" and not root_ref.startswith("trial:"):
            raise ApplicationContractError("Trial source must select a Trial runtime root")
        object.__setattr__(self, "runtime_root_ref", root_ref)
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_SELECTION_SCHEMA,
            "webspace_id": self.webspace_id,
            "application_id": self.application_id,
            "source": self.source,
            "release_digest": self.release_digest,
            "runtime_root_ref": self.runtime_root_ref,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeSelection":
        required = {"schema", "webspace_id", "application_id", "source", "release_digest", "runtime_root_ref", "revision", "updated_at"}
        payload = _schema_mapping(value, schema=RUNTIME_SELECTION_SCHEMA, allowed=required, required=required, field_name="RuntimeSelection")
        payload.pop("schema")
        return cls(**payload)

    def advance(self, *, expected_revision: int, **changes: Any) -> "RuntimeSelection":
        if self.revision != expected_revision:
            raise ApplicationContractError(
                f"runtime selection revision conflict: expected {expected_revision}, observed {self.revision}"
            )
        return replace(self, revision=self.revision + 1, updated_at=utc_now(), **changes)


@dataclass(frozen=True, slots=True)
class TrialAccessGrant:
    grant_id: str
    application_id: str
    publisher_ref: str
    scope: str
    recipient_subnet_ref: str
    recipient_key_ref: str
    expires_at: str
    max_uses: int
    uses: int
    nonce: str
    allowed_zones: tuple[str, ...]
    status: str
    revision: int
    release_digest: str | None = None
    issued_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", _text(self.grant_id, "grant_id", maximum=180))
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        object.__setattr__(self, "publisher_ref", _publisher_ref(self.publisher_ref))
        if self.scope not in {"exact_release", "follow_prerelease"}:
            raise ApplicationContractError("TrialAccessGrant scope is invalid")
        object.__setattr__(self, "recipient_subnet_ref", _publisher_ref(self.recipient_subnet_ref))
        object.__setattr__(self, "recipient_key_ref", _text(self.recipient_key_ref, "recipient_key_ref", maximum=240))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        object.__setattr__(self, "issued_at", _timestamp(self.issued_at, "issued_at"))
        max_uses = _revision(self.max_uses, "max_uses")
        uses = _revision(self.uses, "uses", minimum=0)
        if uses > max_uses:
            raise ApplicationContractError("TrialAccessGrant uses exceeds max_uses")
        object.__setattr__(self, "max_uses", max_uses)
        object.__setattr__(self, "uses", uses)
        object.__setattr__(self, "nonce", _text(self.nonce, "nonce", maximum=240))
        object.__setattr__(self, "allowed_zones", tuple(sorted({_identifier(item, "allowed_zone") for item in self.allowed_zones})))
        if not self.allowed_zones:
            raise ApplicationContractError("TrialAccessGrant requires an allowed zone")
        if self.status not in {"active", "consumed", "expired", "revoked"}:
            raise ApplicationContractError("TrialAccessGrant status is invalid")
        object.__setattr__(self, "revision", _revision(self.revision))
        if self.release_digest is not None:
            object.__setattr__(self, "release_digest", _digest(self.release_digest, "release_digest"))
        if self.scope == "exact_release" and self.release_digest is None:
            raise ApplicationContractError("exact_release grant requires release_digest")
        if self.scope == "follow_prerelease" and self.release_digest is not None:
            raise ApplicationContractError("follow_prerelease grant cannot pin release_digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TRIAL_ACCESS_GRANT_SCHEMA,
            "grant_id": self.grant_id,
            "application_id": self.application_id,
            "publisher_ref": self.publisher_ref,
            "scope": self.scope,
            "release_digest": self.release_digest,
            "recipient_subnet_ref": self.recipient_subnet_ref,
            "recipient_key_ref": self.recipient_key_ref,
            "expires_at": self.expires_at,
            "max_uses": self.max_uses,
            "uses": self.uses,
            "nonce": self.nonce,
            "allowed_zones": list(self.allowed_zones),
            "status": self.status,
            "revision": self.revision,
            "issued_at": self.issued_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrialAccessGrant":
        required = {"schema", "grant_id", "application_id", "publisher_ref", "scope", "release_digest", "recipient_subnet_ref", "recipient_key_ref", "expires_at", "max_uses", "uses", "nonce", "allowed_zones", "status", "revision", "issued_at"}
        payload = _schema_mapping(value, schema=TRIAL_ACCESS_GRANT_SCHEMA, allowed=required, required=required, field_name="TrialAccessGrant")
        payload.pop("schema")
        payload["allowed_zones"] = tuple(payload["allowed_zones"])
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ApplicationOperation:
    operation_id: str
    application_id: str
    kind: str
    status: str
    actor_ref: str
    subnet_ref: str
    plan_digest: str
    idempotency_key: str
    expected_revision: int
    revision: int
    plan: Mapping[str, Any]
    result: Mapping[str, Any] = field(default_factory=dict)
    recovery_reason: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _text(self.operation_id, "operation_id", maximum=200))
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        if self.kind not in {"install", "update", "remove", "select_track", "install_trial", "publish_trial", "publish_prerelease", "promote_stable", "reconcile"}:
            raise ApplicationContractError("ApplicationOperation kind is invalid")
        if self.status not in {"planned", "applying", "succeeded", "failed", "unknown", "reconciling", "cancelled"}:
            raise ApplicationContractError("ApplicationOperation status is invalid")
        object.__setattr__(self, "actor_ref", _text(self.actor_ref, "actor_ref", maximum=200))
        object.__setattr__(self, "subnet_ref", _publisher_ref(self.subnet_ref))
        object.__setattr__(self, "plan_digest", _digest(self.plan_digest, "plan_digest"))
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, "idempotency_key", maximum=240))
        object.__setattr__(self, "expected_revision", _revision(self.expected_revision, "expected_revision", minimum=0))
        object.__setattr__(self, "revision", _revision(self.revision))
        plan = _mapping(self.plan, "plan")
        if canonical_payload_digest(plan) != self.plan_digest:
            raise ApplicationContractError("plan_digest does not match canonical plan content")
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "result", _mapping(self.result, "result"))
        object.__setattr__(self, "recovery_reason", _optional_text(self.recovery_reason, maximum=500))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": APPLICATION_OPERATION_SCHEMA,
            "operation_id": self.operation_id,
            "application_id": self.application_id,
            "kind": self.kind,
            "status": self.status,
            "actor_ref": self.actor_ref,
            "subnet_ref": self.subnet_ref,
            "plan_digest": self.plan_digest,
            "idempotency_key": self.idempotency_key,
            "expected_revision": self.expected_revision,
            "revision": self.revision,
            "plan": dict(self.plan),
            "result": dict(self.result),
            "recovery_reason": self.recovery_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationOperation":
        required = {"schema", "operation_id", "application_id", "kind", "status", "actor_ref", "subnet_ref", "plan_digest", "idempotency_key", "expected_revision", "revision", "plan", "result", "recovery_reason", "created_at", "updated_at"}
        payload = _schema_mapping(value, schema=APPLICATION_OPERATION_SCHEMA, allowed=required, required=required, field_name="ApplicationOperation")
        payload.pop("schema")
        return cls(**payload)


__all__ = [
    "APPLICATION_INSTALLATION_SCHEMA",
    "APPLICATION_OPERATION_SCHEMA",
    "APPLICATION_RELEASE_SCHEMA",
    "APPLICATION_SCHEMA",
    "APPLICATION_SUBSCRIPTION_SCHEMA",
    "RUNTIME_SELECTION_SCHEMA",
    "TRIAL_ACCESS_GRANT_SCHEMA",
    "Application",
    "ApplicationContractError",
    "ApplicationInstallation",
    "ApplicationOperation",
    "ApplicationRelease",
    "ApplicationSubscription",
    "RuntimeSelection",
    "TrialAccessGrant",
    "utc_now",
]
