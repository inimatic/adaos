from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from adaos.domain.artifact_release import (
    ArtifactReleaseContractError,
    ArtifactSourceRef,
    ProjectRef,
    StableSubscription,
    WorkspaceLock,
)
from adaos.services.workspace_registry import (
    find_workspace_registry_entry,
    workspace_registry_install_name,
)


ARTIFACT_IDENTITY_EXPLANATION_SCHEMA = "adaos.artifact.identity_explanation.v1"
ArtifactIdentityKind = Literal["skill", "scenario"]
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


class ArtifactIdentityDiagnosticError(RuntimeError):
    pass


def explain_workspace_artifact_identity(
    workspace_root: Path,
    *,
    kind: ArtifactIdentityKind,
    name_or_id: str,
    channel: str = "stable",
) -> dict[str, Any]:
    """Explain registry, release, package, and activation identity without mutation."""

    if kind not in {"skill", "scenario"}:
        raise ValueError("kind must be skill or scenario")
    requested = str(name_or_id or "").strip()
    if not requested:
        raise ValueError("name_or_id must not be empty")
    selected_channel = str(channel or "").strip()
    if not selected_channel:
        raise ValueError("channel must not be empty")

    root = Path(workspace_root).expanduser().resolve()
    registry_kind = "skills" if kind == "skill" else "scenarios"
    entry = find_workspace_registry_entry(
        root,
        kind=registry_kind,
        name_or_id=requested,
        fallback_to_scan=False,
    )
    if entry is None:
        raise FileNotFoundError(
            f"{kind} {requested!r} is not listed in {root / 'registry.json'}"
        )

    canonical_id = str(entry.get("id") or entry.get("name") or "").strip()
    install_name = workspace_registry_install_name(entry, kind=registry_kind)
    manifest_name = "skill.yaml" if kind == "skill" else "scenario.yaml"
    compatibility = (
        dict(entry["compatibility"])
        if isinstance(entry.get("compatibility"), Mapping)
        else None
    )
    warnings: list[str] = []
    if compatibility and compatibility.get("publishable") is False:
        warnings.append(str(compatibility.get("reason") or "manifest_migration_required"))

    channels = entry.get("channels")
    channel_map = dict(channels) if isinstance(channels, Mapping) else {}
    raw_pointer = channel_map.get(selected_channel)
    pointer = dict(raw_pointer) if isinstance(raw_pointer, Mapping) else None
    if pointer is None:
        warnings.append(f"channel_{selected_channel}_not_resolved")

    source = _explain_source(entry, pointer=pointer, warnings=warnings)
    release = _explain_release(canonical_id, pointer=pointer, warnings=warnings)
    package = _explain_package(pointer=pointer, warnings=warnings)
    activation = _explain_activation(
        root,
        component_key=f"{kind}:{canonical_id}",
        selected_package_digest=package.get("digest"),
        selected_release=release.get("reference"),
        selected_project_id=release.get("project_id"),
        warnings=warnings,
    )
    subscription = _explain_subscription(
        root,
        project_id=str(release.get("project_id") or canonical_id),
        activation=activation,
        warnings=warnings,
    )
    _correlate_active_identity(
        source=source,
        release=release,
        package=package,
        activation=activation,
        warnings=warnings,
    )

    try:
        project_ref = ProjectRef(
            str(release.get("project_id") or canonical_id)
        ).to_dict()
    except ArtifactReleaseContractError as exc:
        raise ArtifactIdentityDiagnosticError(
            f"registry does not resolve a canonical project identity: {exc}"
        ) from exc

    return {
        "schema": ARTIFACT_IDENTITY_EXPLANATION_SCHEMA,
        "read_only": True,
        "requested": {
            "kind": kind,
            "name_or_id": requested,
            "channel": selected_channel,
        },
        "project_ref": project_ref,
        "registry": {
            "canonical_id": canonical_id,
            "install_name": install_name,
            "install_path": str(entry.get("path") or f"{registry_kind}/{install_name}"),
            "manifest": str(
                entry.get("manifest")
                or f"{registry_kind}/{install_name}/{manifest_name}"
            ),
            "version": {
                "value": str(entry.get("version") or ""),
                "authority": manifest_name,
                "compatibility": compatibility,
            },
        },
        "source": source,
        "channel": {
            "name": selected_channel,
            "status": "resolved" if pointer is not None else "not_resolved",
            "available": sorted(str(item) for item in channel_map),
            "pointer": pointer,
        },
        "subscription": subscription,
        "release": release,
        "package": package,
        "activation": activation,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _explain_source(
    entry: Mapping[str, Any],
    *,
    pointer: Mapping[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any]:
    raw_source = entry.get("source")
    source = dict(raw_source) if isinstance(raw_source, Mapping) else {}
    pointer_revision = str((pointer or {}).get("source_revision") or "").strip()
    registry_revision = str(source.get("revision") or "").strip()
    if pointer_revision and registry_revision and pointer_revision != registry_revision:
        warnings.append("source_revision_conflict")
        return {
            "status": "conflict",
            "path": source.get("path"),
            "manifest": source.get("manifest"),
            "ref": None,
            "registry_revision": registry_revision,
            "channel_revision": pointer_revision,
        }

    revision = pointer_revision or registry_revision
    forge = str(source.get("forge") or "").strip()
    repository = str(source.get("repository") or "").strip()
    raw_scope = source.get("path_scope")
    if isinstance(raw_scope, (list, tuple)):
        path_scope = tuple(str(item) for item in raw_scope)
    else:
        source_path = str(source.get("path") or "").strip().replace("\\", "/")
        path_scope = (source_path + "/",) if source_path else ()
    if forge and repository and revision:
        try:
            source_ref = ArtifactSourceRef(
                forge=forge,
                repository=repository,
                revision=revision,
                path_scope=path_scope,
            )
        except ArtifactReleaseContractError as exc:
            warnings.append("source_ref_invalid")
            return {
                "status": "invalid",
                "path": source.get("path"),
                "manifest": source.get("manifest"),
                "ref": None,
                "error": str(exc),
            }
        return {
            "status": "immutable",
            "path": source.get("path"),
            "manifest": source.get("manifest"),
            "ref": source_ref.to_dict(),
        }

    warnings.append("source_ref_not_resolved")
    return {
        "status": "legacy_path_only",
        "path": source.get("path") or entry.get("path"),
        "manifest": source.get("manifest") or entry.get("manifest"),
        "ref": None,
    }


def _explain_release(
    canonical_id: str,
    *,
    pointer: Mapping[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any]:
    reference = str((pointer or {}).get("release") or "").strip()
    project_id = canonical_id
    version = str((pointer or {}).get("version") or "").strip()
    if "@" in reference:
        parsed_project, parsed_version = reference.rsplit("@", 1)
        project_id = parsed_project or project_id
        version = parsed_version or version
    digest = str((pointer or {}).get("release_digest") or "").strip()
    status = "not_resolved"
    if reference:
        status = "resolved"
        if "@" not in reference or not version or not _SEMVER_RE.fullmatch(version):
            status = "invalid_pointer"
            warnings.append("release_reference_invalid")
        if not _DIGEST_RE.fullmatch(digest):
            status = "invalid_pointer"
            warnings.append("release_digest_invalid")
    return {
        "status": status,
        "project_id": project_id,
        "reference": reference or None,
        "version": version or None,
        "digest": digest or None,
    }


def _explain_package(
    *,
    pointer: Mapping[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any]:
    digest = str((pointer or {}).get("package_digest") or "").strip()
    version = str((pointer or {}).get("version") or "").strip()
    status = "not_resolved"
    if digest:
        status = "resolved_pointer"
        if not _DIGEST_RE.fullmatch(digest) or not _SEMVER_RE.fullmatch(version):
            status = "invalid_pointer"
            warnings.append("package_pointer_invalid")
    return {
        "status": status,
        "digest": digest or None,
        "version": version or None,
    }


def _explain_activation(
    workspace_root: Path,
    *,
    component_key: str,
    selected_package_digest: Any,
    selected_release: Any,
    selected_project_id: Any,
    warnings: list[str],
) -> dict[str, Any]:
    lock_path = workspace_root / ".adaos" / "workspace.lock.json"
    if not lock_path.is_file():
        warnings.append("workspace_lock_not_present")
        return {
            "status": "legacy_unlocked",
            "lock_path": str(lock_path),
            "component": None,
            "slots": [],
            "bindings": [],
        }
    try:
        raw_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if not isinstance(raw_lock, Mapping):
            raise ArtifactIdentityDiagnosticError("WorkspaceLock must contain an object")
        lock = WorkspaceLock.from_mapping(raw_lock)
    except (OSError, json.JSONDecodeError, ArtifactReleaseContractError) as exc:
        raise ArtifactIdentityDiagnosticError(f"cannot trust WorkspaceLock: {exc}") from exc

    component = next((item for item in lock.components if item.key == component_key), None)
    selected_digest = str(selected_package_digest or "").strip()
    if component is None:
        status = "not_active"
    elif selected_digest and component.digest == selected_digest:
        status = "active_selected_package"
    elif selected_digest:
        status = "active_different_package"
        warnings.append("active_package_differs_from_selected_channel")
    else:
        status = "active_without_selected_channel"

    release_reference = str(selected_release or "").strip()
    project_id = str(selected_project_id or "").strip()
    slots = [
        {"slot_id": item.slot_id, **item.to_dict()}
        for item in lock.slots
        if (release_reference and item.release == release_reference)
        or (project_id and item.project_id == project_id)
    ]
    bindings = [
        item.to_dict()
        for item in lock.bindings
        if item.consumer == component_key or item.dependency == component_key
    ]
    lock_payload = lock.to_dict()
    return {
        "status": status,
        "lock_path": str(lock_path),
        "lock_revision": lock.lock_revision,
        "lock_digest": lock_payload["lock_digest"],
        "component": component.to_dict() if component is not None else None,
        "slots": slots,
        "bindings": bindings,
    }


def _explain_subscription(
    workspace_root: Path,
    *,
    project_id: str,
    activation: Mapping[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    path = workspace_root / ".adaos" / "subscriptions.json"
    if not path.is_file():
        warnings.append("subscription_not_present")
        return {"status": "not_subscribed", "path": str(path), "record": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ArtifactIdentityDiagnosticError("subscription set must contain an object")
        if payload.get("schema") != "adaos.artifact.subscription_set.v1":
            raise ArtifactIdentityDiagnosticError("unsupported subscription set schema")
        unknown = set(payload) - {"schema", "subscriptions"}
        if unknown:
            raise ArtifactIdentityDiagnosticError(
                "subscription set contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        raw_subscriptions = payload.get("subscriptions")
        if not isinstance(raw_subscriptions, list) or any(
            not isinstance(item, Mapping) for item in raw_subscriptions
        ):
            raise ArtifactIdentityDiagnosticError(
                "subscription set must contain a list of objects"
            )
        subscriptions: dict[str, StableSubscription] = {}
        for item in raw_subscriptions:
            subscription = StableSubscription.from_mapping(item)
            if subscription.project_id in subscriptions:
                raise ArtifactIdentityDiagnosticError(
                    f"duplicate subscription for {subscription.project_id}"
                )
            subscriptions[subscription.project_id] = subscription
    except ArtifactIdentityDiagnosticError:
        raise
    except (OSError, json.JSONDecodeError, ArtifactReleaseContractError) as exc:
        raise ArtifactIdentityDiagnosticError(
            f"cannot trust subscription set: {exc}"
        ) from exc

    subscription = subscriptions.get(project_id)
    if subscription is None:
        warnings.append("subscription_not_present")
        return {"status": "not_subscribed", "path": str(path), "record": None}

    active_slots = activation.get("slots")
    slots = [item for item in active_slots if isinstance(item, Mapping)] if isinstance(active_slots, list) else []
    matches = any(
        item.get("release") == subscription.installed_release
        and item.get("release_digest") == subscription.installed_digest
        for item in slots
    )
    if matches:
        status = "active_installed"
    elif slots:
        status = "active_differs_from_installed"
        warnings.append("active_release_differs_from_subscription")
    else:
        status = "installed_not_active"
        warnings.append("subscribed_release_not_active")
    return {
        "status": status,
        "path": str(path),
        "record": subscription.to_dict(),
    }


def _correlate_active_identity(
    *,
    source: dict[str, Any],
    release: dict[str, Any],
    package: dict[str, Any],
    activation: Mapping[str, Any],
    warnings: list[str],
) -> None:
    active_component = activation.get("component")
    if isinstance(active_component, Mapping):
        active_package = dict(active_component)
        package["active"] = active_package
        if package.get("digest") is None:
            package["status"] = "active_without_channel"
        active_source = active_package.get("source_ref")
        if isinstance(active_source, Mapping):
            source["activation_ref"] = dict(active_source)
            registry_ref = source.get("ref")
            if registry_ref is None:
                source["status"] = "activation_resolved_registry_unresolved"
            elif registry_ref != active_source:
                source["status"] = "registry_activation_conflict"
                warnings.append("active_source_differs_from_selected_channel")

    active_slots = activation.get("slots")
    if isinstance(active_slots, list):
        release["active_slots"] = [
            dict(item) for item in active_slots if isinstance(item, Mapping)
        ]
        if release.get("reference") is None and release["active_slots"]:
            release["status"] = "active_without_channel"


__all__ = [
    "ARTIFACT_IDENTITY_EXPLANATION_SCHEMA",
    "ArtifactIdentityDiagnosticError",
    "ArtifactIdentityKind",
    "explain_workspace_artifact_identity",
]
