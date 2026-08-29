from __future__ import annotations

import json
import logging
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from adaos.domain.artifact_release import (
    ProjectRelease,
    canonical_payload_digest,
    sha256_digest,
)
from adaos.domain.workspace_manifest import (
    parse_scenario_skill_bindings,
    parse_skill_activation_policy,
)


REGISTRY_FILE_NAME = "registry.json"
REGISTRY_FORMAT_VERSION = 2
RegistryKind = Literal["skills", "scenarios", "projects"]
_LOG = logging.getLogger("adaos.workspace_registry")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_INSTALL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_COMPATIBILITY_SCHEMA = "adaos.workspace.artifact_compatibility.v1"
_REQUIRED_MANIFEST_BY_KIND: dict[RegistryKind, str] = {
    "skills": "skill.yaml",
    "scenarios": "scenario.yaml",
    "projects": "project.yaml",
}
_NON_CANONICAL_MANIFESTS_BY_KIND: dict[RegistryKind, tuple[str, ...]] = {
    "skills": ("skill.yml", "manifest.yaml", "manifest.yml", "skill.json", "manifest.json", "adaos.skill.yaml"),
    "scenarios": ("scenario.yml", "scenario.json"),
    "projects": ("project.yml", "project.json"),
}
_DERIVED_RUNTIME_MANIFESTS_BY_KIND: dict[RegistryKind, tuple[str, ...]] = {
    "skills": (),
    "scenarios": ("scenario.json",),
    "projects": (),
}


class WorkspaceRegistryError(RuntimeError):
    """Raised when the authoritative Workspace registry cannot be trusted."""


def registry_pattern_set(patterns: Iterable[str]) -> list[str]:
    merged: list[str] = []
    if REGISTRY_FILE_NAME not in merged:
        merged.append(REGISTRY_FILE_NAME)
    for raw in patterns:
        try:
            value = str(raw).strip()
        except Exception:
            continue
        if value and value not in merged:
            merged.append(value)
    return merged


def workspace_registry_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / REGISTRY_FILE_NAME


def workspace_registry_is_git_tracked(workspace_root: Path) -> bool:
    root = Path(workspace_root)
    if not (root / ".git").exists():
        return False
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", REGISTRY_FILE_NAME],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if completed.returncode == 1:
        return False
    return True


def load_workspace_registry(workspace_root: Path, *, fallback_to_scan: bool = True) -> dict[str, Any]:
    path = workspace_registry_path(workspace_root)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WorkspaceRegistryError(f"cannot parse workspace registry: {path}") from exc
        if not isinstance(data, dict):
            raise WorkspaceRegistryError(f"workspace registry must contain an object: {path}")
        raw_version = data.get("version", 1)
        if raw_version is None:
            raw_version = 1
        try:
            version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise WorkspaceRegistryError(
                f"unsupported workspace registry version {raw_version!r}: {path}"
            ) from exc
        if version not in {1, REGISTRY_FORMAT_VERSION}:
            raise WorkspaceRegistryError(
                f"unsupported workspace registry version {raw_version!r}: {path}"
            )
        return _normalize_registry_payload(
            data,
            workspace_root=Path(workspace_root) if version == 1 else None,
        )
    if fallback_to_scan:
        return rebuild_workspace_registry(workspace_root)
    return _normalize_registry_payload({})


def write_workspace_registry(workspace_root: Path, payload: dict[str, Any]) -> Path:
    path = workspace_registry_path(workspace_root)
    with _registry_mutation_lock(_workspace_registry_lock_path(Path(workspace_root))):
        _write_workspace_registry_unlocked(path, payload)
    return path


def _write_workspace_registry_unlocked(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_registry_json(path, _normalize_registry_payload(payload))


def _workspace_registry_lock_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".adaos" / ".registry-writer.lock"


def _registry_mutation_lock(path: Path):
    # Imported lazily because artifact_pipeline.publication depends on this
    # module during package initialization.
    from adaos.services.artifact_pipeline.storage import mutation_lock

    return mutation_lock(path)


def _atomic_write_registry_json(path: Path, payload: dict[str, Any]) -> None:
    from adaos.services.artifact_pipeline.storage import atomic_write_json

    atomic_write_json(path, payload)


def rebuild_workspace_registry(workspace_root: Path) -> dict[str, Any]:
    root = Path(workspace_root)
    payload: dict[str, Any] = {
        "version": REGISTRY_FORMAT_VERSION,
        "updated_at": _now_iso(),
        "skills": [],
        "scenarios": [],
        "projects": [],
    }
    for kind in ("skills", "scenarios", "projects"):
        entries: list[dict[str, Any]] = []
        kind_root = root / kind
        if kind_root.exists():
            for child in sorted(kind_root.iterdir(), key=lambda item: item.name.lower()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if _is_sparse_placeholder_dir(child):
                    continue
                entry = build_registry_entry(kind, child)
                if entry is not None:
                    entries.append(entry)
        payload[kind] = entries
    return _normalize_registry_payload(payload)


def upsert_workspace_registry_entry(
    workspace_root: Path,
    kind: RegistryKind,
    artifact_dir: Path,
    *,
    version: str | None = None,
    updated_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace_root = Path(workspace_root)
    with _registry_mutation_lock(_workspace_registry_lock_path(workspace_root)):
        payload = load_workspace_registry(workspace_root, fallback_to_scan=True)
        entry = build_registry_entry(kind, artifact_dir)
        if entry is None:
            raise FileNotFoundError(f"cannot build registry entry for {kind[:-1]} at {artifact_dir}")
        if version:
            entry["version"] = str(version)
        if updated_at:
            entry["updated_at"] = str(updated_at)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if value is None:
                    continue
                entry[str(key)] = value
        items = list(payload.get(kind) or [])
        items = [item for item in items if isinstance(item, dict) and str(item.get("name") or "") != entry["name"]]
        items.append(entry)
        payload[kind] = _normalize_entries(kind, items)
        payload["updated_at"] = entry.get("updated_at") or _now_iso()
        _write_workspace_registry_unlocked(workspace_registry_path(workspace_root), payload)
        return entry


def set_workspace_registry_channel(
    workspace_root: Path,
    kind: RegistryKind,
    name_or_id: str,
    *,
    channel: str,
    release: ProjectRelease,
    expected_entry_digest: str | None = None,
) -> dict[str, Any]:
    """Point one registry artifact channel at an immutable project release.

    Registry v1 entries remain readable. The first channel update rewrites the
    local catalog as v2 while preserving legacy path/install fields.
    """

    workspace_root = Path(workspace_root)
    with _registry_mutation_lock(_workspace_registry_lock_path(workspace_root)):
        return _set_workspace_registry_channel_unlocked(
            workspace_root,
            kind,
            name_or_id,
            channel=channel,
            release=release,
            expected_entry_digest=expected_entry_digest,
        )


def _set_workspace_registry_channel_unlocked(
    workspace_root: Path,
    kind: RegistryKind,
    name_or_id: str,
    *,
    channel: str,
    release: ProjectRelease,
    expected_entry_digest: str | None,
) -> dict[str, Any]:
    channel_id = _clean_text(channel)
    if not channel_id:
        raise ValueError("channel must not be empty")
    payload = load_workspace_registry(workspace_root, fallback_to_scan=False)
    items = list(payload.get(kind) or [])
    needle = (_clean_text(name_or_id) or "").lower()
    matched_index: int | None = None
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        names = {
            value.lower()
            for value in (_clean_text(raw.get("name")), _clean_text(raw.get("id")))
            if value
        }
        if needle and needle in names:
            matched_index = index
            break
    if matched_index is None:
        raise FileNotFoundError(f"{kind[:-1]} '{name_or_id}' is not listed in workspace registry.json")

    entry = dict(items[matched_index])
    observed_entry_digest = canonical_payload_digest(entry)
    if (
        expected_entry_digest is not None
        and observed_entry_digest != expected_entry_digest
    ):
        raise WorkspaceRegistryError(
            "workspace registry entry changed after review: "
            f"expected {expected_entry_digest}, observed {observed_entry_digest}"
        )
    artifact_id = _clean_text(entry.get("id")) or _clean_text(entry.get("name"))
    component = next(
        (
            item
            for item in release.components
            if item.kind == kind[:-1] and item.artifact_id == artifact_id
        ),
        None,
    )
    if component is None:
        raise ValueError(
            f"release {release.project_id}@{release.version} does not contain {kind[:-1]} '{artifact_id}'"
        )

    channels = dict(entry.get("channels") or {}) if isinstance(entry.get("channels"), dict) else {}
    channels[channel_id] = {
        "release": f"{release.project_id}@{release.version}",
        "release_digest": release.release_digest or release.computed_digest(),
        "source_revision": release.source_ref.revision,
        "package_digest": component.digest,
        "version": component.version,
    }
    entry["channels"] = {key: channels[key] for key in sorted(channels)}
    source = dict(entry.get("source") or {}) if isinstance(entry.get("source"), dict) else {}
    source.update(
        {
            "forge": release.source_ref.forge,
            "repository": release.source_ref.repository,
            "revision": release.source_ref.revision,
            "path_scope": list(release.source_ref.path_scope),
        }
    )
    entry["source"] = source
    items[matched_index] = entry
    payload[kind] = _normalize_entries(kind, items)
    payload["updated_at"] = _now_iso()
    _write_workspace_registry_unlocked(workspace_registry_path(workspace_root), payload)
    return dict(entry)


def list_workspace_registry_entries(
    workspace_root: Path,
    *,
    kind: RegistryKind | None = None,
    name: str | None = None,
    fallback_to_scan: bool = True,
) -> list[dict[str, Any]]:
    payload = load_workspace_registry(workspace_root, fallback_to_scan=fallback_to_scan)
    kinds = (kind,) if kind else ("skills", "scenarios", "projects")
    results: list[dict[str, Any]] = []
    wanted_name = (name or "").strip().lower()
    for current_kind in kinds:
        for item in payload.get(current_kind) or []:
            if not isinstance(item, dict):
                continue
            artifact_name = str(item.get("name") or "")
            if wanted_name and artifact_name.lower() != wanted_name:
                continue
            results.append(dict(item))
    return results


def find_workspace_registry_entry(
    workspace_root: Path,
    *,
    kind: RegistryKind,
    name_or_id: str,
    fallback_to_scan: bool = True,
) -> dict[str, Any] | None:
    needle = str(name_or_id or "").strip().lower()
    if not needle:
        return None
    for item in list_workspace_registry_entries(
        workspace_root,
        kind=kind,
        fallback_to_scan=fallback_to_scan,
    ):
        name = str(item.get("name") or "").strip().lower()
        artifact_id = str(item.get("id") or "").strip().lower()
        if needle in {name, artifact_id}:
            return dict(item)
    return None


def find_registry_payload_entry(
    payload: dict[str, Any],
    *,
    kind: RegistryKind,
    name_or_id: str,
) -> dict[str, Any] | None:
    needle = str(name_or_id or "").strip().lower()
    if not needle:
        return None
    normalized = _normalize_registry_payload(payload)
    for item in normalized.get(kind) or []:
        if not isinstance(item, dict):
            continue
        names = {
            str(item.get("name") or "").strip().lower(),
            str(item.get("id") or "").strip().lower(),
        }
        if needle in names:
            return dict(item)
    return None


def load_workspace_registry_git_ref(
    git: Any,
    workspace_root: Path,
    *,
    remote: str = "origin",
    branch: str = "main",
) -> dict[str, Any]:
    root = Path(workspace_root)
    remote_name = str(remote or "origin").strip() or "origin"
    branch_name = str(branch or "main").strip() or "main"
    try:
        git.fetch(str(root), remote=remote_name, branch=branch_name)
    except Exception:
        # A cached remote ref is still preferable to a generated registry
        # modified in the local workspace.
        pass
    raw = git.show(str(root), f"{remote_name}/{branch_name}:registry.json")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise WorkspaceRegistryError("remote workspace registry must be an object")
    return _normalize_registry_payload(payload)


def workspace_registry_install_name(entry: dict[str, Any], *, kind: RegistryKind) -> str:
    install = entry.get("install")
    install_name = _clean_text(install.get("name")) if isinstance(install, dict) else ""
    name = install_name or _clean_text(entry.get("name"))
    if not name:
        path = _clean_text(entry.get("path"))
        prefix = f"{kind}/"
        if path.startswith(prefix):
            name = path[len(prefix) :].strip("/")
    return name or _clean_text(entry.get("id"))


def resolve_workspace_registry_install_name(
    workspace_root: Path,
    *,
    kind: RegistryKind,
    name_or_id: str,
    fallback_to_scan: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    entry = find_workspace_registry_entry(
        workspace_root,
        kind=kind,
        name_or_id=name_or_id,
        fallback_to_scan=fallback_to_scan,
    )
    if entry is None:
        return str(name_or_id or "").strip(), None
    install_name = workspace_registry_install_name(entry, kind=kind)
    return install_name or str(name_or_id or "").strip(), entry


def resolve_registry_payload_install_name(
    payload: dict[str, Any],
    *,
    kind: RegistryKind,
    name_or_id: str,
) -> tuple[str, dict[str, Any] | None]:
    entry = find_registry_payload_entry(payload, kind=kind, name_or_id=name_or_id)
    if entry is None:
        return str(name_or_id or "").strip(), None
    install_name = workspace_registry_install_name(entry, kind=kind)
    return install_name or str(name_or_id or "").strip(), entry


def _registry_kind_noun(kind: RegistryKind) -> str:
    if kind == "skills":
        return "skill"
    if kind == "scenarios":
        return "scenario"
    return "project"


def format_registry_payload_not_found(
    payload: dict[str, Any],
    *,
    kind: RegistryKind,
    name_or_id: str,
    limit: int = 8,
) -> str:
    noun = _registry_kind_noun(kind)
    requested = str(name_or_id or "").strip()
    normalized = _normalize_registry_payload(payload)
    entries = [item for item in normalized.get(kind) or [] if isinstance(item, dict)]
    candidates = [workspace_registry_install_name(item, kind=kind) for item in entries[:limit]]
    candidates = [item for item in dict.fromkeys(candidates) if item]
    suffix = f" Available {kind} include: {', '.join(candidates)}." if candidates else ""
    return (
        f"{noun} '{requested}' is not listed in remote registry.json. "
        f"Install by the registry 'name' field, not by an arbitrary label.{suffix}"
    )


def format_workspace_registry_not_found(
    workspace_root: Path,
    *,
    kind: RegistryKind,
    name_or_id: str,
    fallback_to_scan: bool = False,
    limit: int = 8,
) -> str:
    noun = _registry_kind_noun(kind)
    requested = str(name_or_id or "").strip()
    entries = list_workspace_registry_entries(
        workspace_root,
        kind=kind,
        fallback_to_scan=fallback_to_scan,
    )
    needle = requested.lower()
    candidates: list[str] = []
    for item in entries:
        name = _clean_text(item.get("name"))
        artifact_id = _clean_text(item.get("id"))
        title = _clean_text(item.get("title"))
        haystack = " ".join(part for part in (name, artifact_id, title) if part).lower()
        if needle and needle in haystack:
            candidates.append(name or artifact_id)
    if not candidates:
        candidates = [
            workspace_registry_install_name(item, kind=kind)
            for item in entries[:limit]
        ]
    candidates = [item for item in dict.fromkeys(candidates) if item]
    suffix = ""
    if candidates:
        suffix = f" Available {kind} include: {', '.join(candidates[:limit])}."
    return (
        f"{noun} '{requested}' is not listed in workspace registry.json. "
        f"Install by the registry 'name' field, not by an arbitrary label.{suffix}"
    )


def build_registry_entry(kind: RegistryKind, artifact_dir: Path) -> dict[str, Any] | None:
    directory = Path(artifact_dir)
    manifest_path, manifest = _load_manifest(directory, kind)
    if manifest_path is None:
        return None

    artifact_name = directory.name
    manifest_id = _canonical_manifest_id(kind, manifest) or artifact_name
    title = _clean_text(manifest.get("title")) or _clean_text(manifest.get("name"))
    description = _clean_text(manifest.get("description"))
    tags = _clean_tags(manifest.get("tags"))
    version, compatibility = _canonical_manifest_version(manifest_path, manifest)
    entry: dict[str, Any] = {
        "kind": kind[:-1],
        "id": manifest_id,
        "name": artifact_name,
        "version": version,
        "updated_at": _clean_text(manifest.get("updated_at")) or _now_iso(),
        "path": f"{kind}/{artifact_name}",
        "manifest": f"{kind}/{artifact_name}/{manifest_path.name}",
        "source": {
            "path": f"{kind}/{artifact_name}",
            "manifest": f"{kind}/{artifact_name}/{manifest_path.name}",
        },
        "install": {
            "kind": kind[:-1],
            "name": artifact_name,
            "id": manifest_id,
        },
    }
    if title and title != artifact_name:
        entry["title"] = title
    title_i18n = manifest.get("title_i18n")
    if isinstance(title_i18n, dict):
        entry["title_i18n"] = {str(key): value for key, value in title_i18n.items() if value is not None}
    if description:
        entry["description"] = description
    description_i18n = manifest.get("description_i18n")
    if isinstance(description_i18n, dict):
        entry["description_i18n"] = {str(key): value for key, value in description_i18n.items() if value is not None}
    if tags:
        entry["tags"] = tags
    if compatibility is not None:
        entry["compatibility"] = compatibility

    publisher = manifest.get("publisher")
    if isinstance(publisher, dict):
        publisher_entry = {str(key): value for key, value in publisher.items() if value is not None}
        if publisher_entry:
            entry["publisher"] = publisher_entry

    if kind == "skills":
        if manifest_id and manifest_id != artifact_name:
            entry["manifest_id"] = manifest_id
        manifest_entry = _clean_text(manifest.get("entry"))
        if manifest_entry:
            entry["entry"] = manifest_entry
        runtime = manifest.get("runtime")
        if isinstance(runtime, dict) and runtime:
            runtime_python = _clean_text(runtime.get("python"))
            if runtime_python:
                entry["runtime_python"] = runtime_python
        activation = parse_skill_activation_policy(manifest)
        if activation is not None:
            entry["activation"] = activation.to_dict()
        tools = manifest.get("tools")
        if isinstance(tools, list):
            entry["tools_count"] = len(tools)
    elif kind == "scenarios":
        scenario_id = _clean_text(manifest.get("id"))
        if scenario_id and scenario_id != artifact_name:
            entry["manifest_id"] = scenario_id
        trigger = _clean_text(manifest.get("trigger"))
        if trigger:
            entry["trigger"] = trigger
        skills = parse_scenario_skill_bindings(manifest)
        skills_payload = skills.to_dict()
        if skills_payload:
            entry["skills"] = skills_payload
        runtime = manifest.get("runtime")
        activation = runtime.get("activation") if isinstance(runtime, dict) else None
        if isinstance(activation, dict):
            entry["activation"] = {
                str(key): value
                for key, value in activation.items()
                if key in {"mode", "startup_allowed", "background_refresh"}
            }
        io_meta = manifest.get("io")
        if isinstance(io_meta, dict):
            io_entry: dict[str, Any] = {}
            for key in ("input", "output"):
                value = io_meta.get(key)
                if isinstance(value, list):
                    io_entry[key] = [str(item) for item in value]
            if io_entry:
                entry["io"] = io_entry
    else:
        catalog = manifest.get("catalog")
        if isinstance(catalog, dict):
            catalog_title = _clean_text(catalog.get("title"))
            catalog_description = _clean_text(catalog.get("description"))
            catalog_title_i18n = catalog.get("title_i18n")
            catalog_description_i18n = catalog.get("description_i18n")
            categories = _clean_tags(catalog.get("categories"))
            catalog_tags = _clean_tags(catalog.get("tags"))
            if catalog_title:
                entry["title"] = catalog_title
            if isinstance(catalog_title_i18n, dict):
                entry["title_i18n"] = {
                    str(key): value for key, value in catalog_title_i18n.items() if value is not None
                }
            if catalog_description:
                entry["description"] = catalog_description
            if isinstance(catalog_description_i18n, dict):
                entry["description_i18n"] = {
                    str(key): value for key, value in catalog_description_i18n.items() if value is not None
                }
            if categories:
                entry["categories"] = categories
            if catalog_tags:
                entry["tags"] = catalog_tags
        publication = manifest.get("publication")
        if isinstance(publication, dict):
            publication_entry = {
                str(key): value
                for key, value in publication.items()
                if value is not None
            }
            if publication_entry:
                entry["publication"] = publication_entry
            for field in ("stage", "visibility", "channel"):
                token = _clean_text(publication.get(field))
                if token:
                    entry[field] = token
        install = manifest.get("install")
        if isinstance(install, dict):
            entry["install_default"] = bool(install.get("default") is True)
            features = install.get("features")
            if isinstance(features, list):
                entry["features"] = [
                    dict(item)
                    for item in features
                    if isinstance(item, dict)
                ]
        components = manifest.get("components")
        owned = components.get("owned") if isinstance(components, dict) else None
        if isinstance(owned, list):
            refs = [
                str(item.get("ref") or "").strip()
                for item in owned
                if isinstance(item, dict) and str(item.get("ref") or "").strip()
            ]
            entry["components"] = refs
            entry["components_count"] = len(refs)
        profiles = manifest.get("profiles")
        if isinstance(profiles, list):
            entry["profiles"] = [str(item) for item in profiles if str(item).strip()]

    return entry


def _normalize_registry_payload(
    raw: Any,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    payload: dict[str, Any] = {
        "version": REGISTRY_FORMAT_VERSION,
        "updated_at": _clean_text(data.get("updated_at")) or _now_iso(),
        "skills": _normalize_entries(
            "skills",
            data.get("skills"),
            workspace_root=workspace_root,
        ),
        "scenarios": _normalize_entries(
            "scenarios",
            data.get("scenarios"),
            workspace_root=workspace_root,
        ),
        "projects": _normalize_entries(
            "projects",
            data.get("projects"),
            workspace_root=workspace_root,
        ),
    }
    return payload


def _normalize_entries(
    kind: RegistryKind,
    raw_entries: Any,
    *,
    workspace_root: Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list):
        return []
    merged: dict[str, dict[str, Any]] = {}
    alias_owners: dict[str, str] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        name = _registry_entry_name(kind, raw)
        if not name:
            continue
        if not _registry_entry_manifest_supported(kind, raw, name=name):
            continue
        item = dict(raw)
        item["kind"] = kind[:-1]
        item["name"] = name
        if workspace_root is not None:
            item = _enrich_registry_entry_from_canonical_manifest(
                workspace_root,
                kind=kind,
                item=item,
            )
        if name in merged:
            raise WorkspaceRegistryError(
                f"duplicate workspace registry install name for {kind}: {name!r}"
            )
        owner = f"{kind[:-1]}:{name}"
        for alias in (item.get("name"), item.get("id")):
            token = _clean_text(alias)
            if not token:
                continue
            key = unicodedata.normalize("NFC", token).casefold()
            previous_owner = alias_owners.get(key)
            if previous_owner is not None and previous_owner != owner:
                raise WorkspaceRegistryError(
                    "ambiguous workspace registry alias "
                    f"{token!r}: {previous_owner} conflicts with {owner}"
                )
            alias_owners[key] = owner
        merged[name] = item
    return [merged[key] for key in sorted(merged, key=str.lower)]


def _registry_entry_name(kind: RegistryKind, raw: dict[str, Any]) -> str | None:
    name = _clean_text(raw.get("name"))
    install = raw.get("install")
    if not name and isinstance(install, dict):
        name = _clean_text(install.get("name"))
    if not name:
        path = _clean_text(raw.get("path"))
        parts = (path or "").replace("\\", "/").strip("/").split("/")
        if len(parts) == 2 and parts[0] == kind:
            name = _clean_text(parts[1])
    name = name or _clean_text(raw.get("id"))
    if name and not _INSTALL_NAME_RE.fullmatch(name):
        raise WorkspaceRegistryError(
            f"unsafe workspace registry install name for {kind}: {name!r}"
        )
    return name


def _enrich_registry_entry_from_canonical_manifest(
    workspace_root: Path,
    *,
    kind: RegistryKind,
    item: dict[str, Any],
) -> dict[str, Any]:
    name = str(item["name"])
    kind_root = (Path(workspace_root) / kind).resolve()
    artifact_dir = kind_root / name
    if not artifact_dir.is_dir():
        return item
    resolved_artifact_dir = artifact_dir.resolve()
    if resolved_artifact_dir.parent != kind_root:
        raise WorkspaceRegistryError(
            f"workspace registry artifact path escapes {kind}: {artifact_dir}"
        )
    if _is_sparse_placeholder_dir(resolved_artifact_dir):
        return item
    canonical = build_registry_entry(kind, resolved_artifact_dir)
    if canonical is None:
        return item

    enriched = dict(item)
    for field in ("kind", "id", "name", "version", "path", "manifest"):
        enriched[field] = canonical[field]
    for field in (
        "title",
        "title_i18n",
        "description",
        "description_i18n",
        "tags",
        "entry",
        "runtime_python",
        "activation",
        "tools_count",
        "skills",
        "trigger",
        "io",
        "categories",
        "publication",
        "stage",
        "visibility",
        "channel",
        "install_default",
        "features",
        "components",
        "components_count",
        "profiles",
    ):
        if field in canonical:
            enriched[field] = canonical[field]

    source = dict(enriched.get("source") or {}) if isinstance(enriched.get("source"), dict) else {}
    source.update(canonical["source"])
    enriched["source"] = source
    install = dict(enriched.get("install") or {}) if isinstance(enriched.get("install"), dict) else {}
    install.update(canonical["install"])
    enriched["install"] = install

    canonical_compatibility = canonical.get("compatibility")
    if isinstance(canonical_compatibility, dict):
        enriched["compatibility"] = dict(canonical_compatibility)
    elif isinstance(enriched.get("compatibility"), dict):
        previous = enriched["compatibility"]
        if previous.get("schema") == _COMPATIBILITY_SCHEMA:
            enriched.pop("compatibility", None)
    return enriched


def _canonical_manifest_id(kind: RegistryKind, manifest: dict[str, Any]) -> str | None:
    if kind == "skills":
        return _clean_text(manifest.get("id")) or _clean_text(manifest.get("name"))
    return _clean_text(manifest.get("id"))


def _canonical_manifest_version(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    declared = _clean_text(manifest.get("version"))
    if declared and _SEMVER_RE.fullmatch(declared):
        return declared, None

    manifest_digest = sha256_digest(manifest_path.read_bytes())
    synthetic = f"0.0.0-legacy.{manifest_digest.split(':', 1)[1][:12]}"
    compatibility: dict[str, Any] = {
        "schema": _COMPATIBILITY_SCHEMA,
        "status": "migration_required",
        "reason": (
            "canonical_manifest_version_invalid"
            if declared
            else "canonical_manifest_version_missing"
        ),
        "version_source": "canonical_manifest_digest",
        "manifest_digest": manifest_digest,
        "publishable": False,
    }
    if declared:
        compatibility["declared_version"] = declared
    return synthetic, compatibility


def _registry_entry_manifest_supported(kind: RegistryKind, raw: dict[str, Any], *, name: str) -> bool:
    manifest = _clean_text(raw.get("manifest"))
    source = raw.get("source")
    if not manifest and isinstance(source, dict):
        manifest = _clean_text(source.get("manifest"))
    if not manifest:
        return True
    required = _REQUIRED_MANIFEST_BY_KIND[kind]
    if Path(manifest).name == required:
        return True
    _LOG.error(
        "workspace registry entry rejected: unsupported declaration kind=%s name=%s required=%s manifest=%s",
        kind[:-1],
        name,
        required,
        manifest,
    )
    return False


def _find_existing_registry_entry(
    payload: dict[str, Any],
    kind: RegistryKind,
    artifact_name: str,
) -> dict[str, Any] | None:
    token = _clean_text(artifact_name)
    if not token:
        return None
    for raw in list(payload.get(kind) or []):
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("name"))
        artifact_id = _clean_text(raw.get("id"))
        if token not in {name, artifact_id}:
            continue
        item = dict(raw)
        item["kind"] = kind[:-1]
        item["name"] = name or token
        item.setdefault("id", artifact_id or token)
        item.setdefault("path", f"{kind}/{token}")
        install = dict(item.get("install") or {}) if isinstance(item.get("install"), dict) else {}
        install.setdefault("kind", kind[:-1])
        install.setdefault("name", item["name"])
        install.setdefault("id", item.get("id") or token)
        item["install"] = install
        return item
    return None


def _load_manifest(directory: Path, kind: RegistryKind) -> tuple[Path | None, dict[str, Any]]:
    required = _REQUIRED_MANIFEST_BY_KIND[kind]
    if not directory.is_dir():
        return None, {}
    path = directory / required
    if not path.exists():
        unsupported = [
            name
            for name in _NON_CANONICAL_MANIFESTS_BY_KIND[kind]
            if (directory / name).exists()
        ]
        _LOG.error(
            "workspace artifact rejected: required declaration is missing kind=%s path=%s required=%s unsupported_present=%s",
            kind[:-1],
            str(directory),
            required,
            ",".join(unsupported) or "-",
        )
        return None, {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        _LOG.error(
            "workspace artifact rejected: failed to read required declaration kind=%s manifest=%s",
            kind[:-1],
            str(path),
            exc_info=True,
        )
        return None, {}
    if not isinstance(data, dict):
        _LOG.error(
            "workspace artifact rejected: required declaration must contain an object kind=%s manifest=%s",
            kind[:-1],
            str(path),
        )
        return None, {}
    unsupported = [
        name
        for name in _NON_CANONICAL_MANIFESTS_BY_KIND[kind]
        if name not in _DERIVED_RUNTIME_MANIFESTS_BY_KIND[kind]
        if (directory / name).exists()
    ]
    if unsupported:
        _LOG.warning(
            "workspace artifact contains unsupported declaration files; ignoring them kind=%s path=%s required=%s unsupported_present=%s",
            kind[:-1],
            str(directory),
            required,
            ",".join(unsupported),
        )
    return path, data


def _is_sparse_placeholder_dir(directory: Path) -> bool:
    try:
        entries = list(directory.iterdir())
    except Exception:
        return False
    if not entries:
        return True
    ignored_dirs = {"__pycache__", ".pytest_cache"}
    ignored_files = {".gitignore", ".gitkeep"}
    try:
        for entry in directory.rglob("*"):
            relative = entry.relative_to(directory)
            if any(part in ignored_dirs for part in relative.parts):
                continue
            if entry.is_dir():
                continue
            if entry.name in ignored_files or entry.suffix.lower() in {".pyc", ".pyo"}:
                continue
            return False
    except Exception:
        return False
    return True


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    return text or None


def _clean_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item or "").strip() for item in value]
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not item:
            continue
        folded = item.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(item)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    "REGISTRY_FILE_NAME",
    "REGISTRY_FORMAT_VERSION",
    "WorkspaceRegistryError",
    "build_registry_entry",
    "find_registry_payload_entry",
    "find_workspace_registry_entry",
    "format_registry_payload_not_found",
    "list_workspace_registry_entries",
    "load_workspace_registry_git_ref",
    "load_workspace_registry",
    "rebuild_workspace_registry",
    "registry_pattern_set",
    "resolve_registry_payload_install_name",
    "resolve_workspace_registry_install_name",
    "set_workspace_registry_channel",
    "upsert_workspace_registry_entry",
    "workspace_registry_is_git_tracked",
    "workspace_registry_path",
    "write_workspace_registry",
]
