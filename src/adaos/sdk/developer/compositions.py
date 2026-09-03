"""Local distributable Project declarations for the active DEV snapshot.

The older :mod:`adaos.sdk.developer.projects` facade addresses individual
skill/scenario component checkouts.  This module addresses the additive
``adaos.project.v1`` composition that owns one or more such components.
"""

from __future__ import annotations

import json
import hashlib
import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core.errors import SdkError
from adaos.sdk.developer import projects as component_projects


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_SCHEMA = "adaos.project.v1"
_MEMBER_DEFAULTS = {
    "exposure": "application",
    "lifecycle": "bound",
    "relations": ("uses",),
}
_PUBLICATION_DEFAULTS = {
    "stage": "alpha",
    "visibility": "unlisted",
    "channel": "stable",
}
_INSTALL_DEFAULTS = {
    "default": False,
    "features": (),
}


class ProjectCompositionError(SdkError):
    """Raised when a distributable Project declaration is invalid."""


class ProjectCompositionNotFound(ProjectCompositionError):
    """Raised when a Project does not exist in the active DEV snapshot."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_id(value: str) -> str:
    token = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(token):
        raise ProjectCompositionError("project_id must match ^[a-z0-9][a-z0-9_.-]{0,127}$")
    return token


def _root_parent() -> Path:
    ctx = require_ctx("sdk.developer.compositions")
    method = getattr(ctx.paths, "dev_projects_dir", None)
    root = Path(method() if callable(method) else Path(ctx.paths.dev_dir()) / "projects").resolve()
    return root


def resolve_root(project_id: str, *, required: bool = True) -> Path:
    parent = _root_parent()
    root = (parent / _project_id(project_id)).resolve()
    if root.parent != parent:
        raise ProjectCompositionError("project path escapes DEV projects root")
    if required and not (root / "project.yaml").is_file():
        raise ProjectCompositionNotFound(f"project:{project_id} was not found in DEV space")
    return root


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / "project.v1.schema.json"


def validate(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ProjectCompositionError(f"project manifest invalid at {location}: {error.message}")
    owned_refs = [str(item.get("ref") or "") for item in payload["components"]["owned"]]
    if len(owned_refs) != len(set(owned_refs)):
        raise ProjectCompositionError("project owned component refs must be unique")
    primaries = [item for item in payload["components"]["owned"] if item.get("role") == "primary"]
    if owned_refs and len(primaries) != 1:
        raise ProjectCompositionError("project with owned components must declare exactly one primary")
    defaults = [item for item in payload.get("entrypoints") or [] if item.get("default") is True]
    if len(defaults) > 1:
        raise ProjectCompositionError("project may declare at most one default entrypoint")
    dependency_refs = [str(item.get("ref") or "") for item in payload["components"]["dependencies"]]
    if len(dependency_refs) != len(set(dependency_refs)):
        raise ProjectCompositionError("project dependency refs must be unique")
    overlap = sorted(set(owned_refs).intersection(dependency_refs))
    if overlap:
        raise ProjectCompositionError(f"owned components cannot also be dependencies: {overlap}")
    required_entrypoints = set((payload.get("compatibility") or {}).get("required_entrypoints") or [])
    declared_entrypoints = {str(item["id"]) for item in payload.get("entrypoints") or []}
    missing_entrypoints = sorted(required_entrypoints - declared_entrypoints)
    if missing_entrypoints:
        raise ProjectCompositionError(
            f"required Project entrypoints are not declared: {missing_entrypoints}"
        )
    install = payload.get("install") or {}
    feature_refs = {
        str(ref)
        for feature in install.get("features") or []
        for ref in feature.get("components") or []
    }
    unknown_feature_refs = sorted(feature_refs.difference(owned_refs))
    if unknown_feature_refs:
        raise ProjectCompositionError(
            f"Project install feature components must be owned members: {unknown_feature_refs}"
        )
    return payload


def normalized_definition(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the compatibility-significant Project definition.

    Defaults are expanded only for release locking. Source manifests are never
    rewritten, so legacy Project digests and Builder receipts remain stable.
    """

    payload = validate(
        {
            key: item
            for key, item in value.items()
            if key not in {"ref", "manifest_digest", "source_path"}
        }
    )
    members = []
    for raw in payload["components"]["owned"]:
        item = dict(raw)
        item.setdefault("exposure", _MEMBER_DEFAULTS["exposure"])
        item.setdefault("lifecycle", _MEMBER_DEFAULTS["lifecycle"])
        item.setdefault("relations", list(_MEMBER_DEFAULTS["relations"]))
        item["relations"] = sorted(item["relations"])
        members.append(item)
    dependencies = []
    for raw in payload["components"]["dependencies"]:
        item = dict(raw)
        item.setdefault("version", None)
        item.setdefault("lifecycle", "shared")
        item.setdefault("relations", ["uses"])
        item["relations"] = sorted(item["relations"])
        dependencies.append(item)
    publication = {**_PUBLICATION_DEFAULTS, **dict(payload.get("publication") or {})}
    install = {**_INSTALL_DEFAULTS, **dict(payload.get("install") or {})}
    install["features"] = sorted(
        (
            {
                **dict(item),
                "default": bool(item.get("default", False)),
                "optional": bool(item.get("optional", False)),
                "components": sorted(str(ref) for ref in item.get("components") or []),
            }
            for item in install.get("features") or []
        ),
        key=lambda item: item["id"],
    )
    catalog = dict(payload["catalog"])
    catalog_payload = {
        "title": str(catalog.get("title") or payload["id"]),
        "description": str(catalog.get("description") or ""),
        "categories": sorted(str(item) for item in catalog.get("categories") or []),
        "tags": sorted(str(item) for item in catalog.get("tags") or []),
    }
    for field in ("title_i18n", "description_i18n"):
        value = catalog.get(field)
        if isinstance(value, Mapping):
            catalog_payload[field] = dict(value)
    return {
        "schema": payload["schema"],
        "kind": payload["kind"],
        "id": payload["id"],
        "version": payload["version"],
        "profiles": sorted(payload["profiles"]),
        "components": {
            "owned": sorted(members, key=lambda item: item["ref"]),
            "dependencies": sorted(dependencies, key=lambda item: item["ref"]),
        },
        "entrypoints": sorted(
            (dict(item) for item in payload.get("entrypoints") or []),
            key=lambda item: item["id"],
        ),
        "catalog": catalog_payload,
        "publication": publication,
        "install": install,
        "compatibility": dict(payload.get("compatibility") or {}),
        "lifecycle": dict(payload["lifecycle"]),
    }


def _read(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectCompositionError(f"failed to read Project manifest: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ProjectCompositionError("Project manifest must be an object")
    return validate(value)


def _manifest_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write(path: Path, value: Mapping[str, Any]) -> None:
    payload = validate(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def get(project_id: str) -> dict[str, Any]:
    root = resolve_root(project_id)
    payload = _read(root / "project.yaml")
    return {
        **payload,
        "ref": f"project:{payload['id']}",
        "manifest_digest": _manifest_digest(payload),
        "source_path": str(root),
    }


def list_projects(*, profile: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    parent = _root_parent()
    if not parent.is_dir():
        return []
    result: list[dict[str, Any]] = []
    maximum = max(1, min(int(limit), 5000))
    for manifest_path in sorted(parent.glob("*/project.yaml"), key=lambda item: item.parent.name.lower()):
        project = _read(manifest_path)
        if profile and str(profile) not in set(project.get("profiles") or []):
            continue
        primary = next(
            (item for item in project["components"]["owned"] if item["role"] == "primary"),
            {},
        )
        item = {
            **project,
            "id": project["id"],
            "ref": f"project:{project['id']}",
            "version": project["version"],
            "title": project["catalog"]["title"],
            "description": project["catalog"]["description"],
            "profiles": list(project["profiles"]),
            "categories": list(project["catalog"]["categories"]),
            "tags": list(project["catalog"]["tags"]),
            "publication": dict(project.get("publication") or {}),
            "install": dict(project.get("install") or {}),
            "stage": str((project.get("publication") or {}).get("stage") or "alpha"),
            "visibility": str((project.get("publication") or {}).get("visibility") or "unlisted"),
            "default_install": bool((project.get("install") or {}).get("default") is True),
            "primary_ref": primary.get("ref"),
            "source_path": str(manifest_path.parent.resolve()),
            "manifest_digest": _manifest_digest(project),
        }
        for field in ("title_i18n", "description_i18n"):
            value = project["catalog"].get(field)
            if isinstance(value, Mapping):
                item[field] = dict(value)
        result.append(item)
        if len(result) >= maximum:
            break
    return result


def create(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = validate(value)
    root = resolve_root(str(payload["id"]), required=False)
    manifest_path = root / "project.yaml"
    if root.exists():
        raise ProjectCompositionError(f"project:{payload['id']} already exists")
    _write(manifest_path, payload)
    return get(str(payload["id"]))


def replace(
    project_id: str,
    value: Mapping[str, Any],
    *,
    expected_manifest_digest: str,
) -> dict[str, Any]:
    """Replace one mutable DEV Project definition with optimistic concurrency.

    Published ProjectRelease objects remain immutable. This operation exists
    for migrations and Builder edits of the source declaration and refuses an
    identity change or a stale caller snapshot.
    """

    token = _project_id(project_id)
    current = get(token)
    expected = str(expected_manifest_digest or "").strip().lower()
    if expected != str(current["manifest_digest"]):
        raise ProjectCompositionError(
            "project manifest changed since it was read; refresh and retry"
        )
    payload = {
        key: item
        for key, item in dict(value).items()
        if key not in {"ref", "manifest_digest", "source_path"}
    }
    if str(payload.get("id") or "") != token:
        raise ProjectCompositionError("replacement cannot change project id")
    _write(resolve_root(token) / "project.yaml", payload)
    return get(token)


def _new_project_definition(
    project_id: str,
    *,
    primary_ref: str,
    title: str,
    description: str = "",
    profiles: Sequence[str] = (),
    dependencies: Sequence[Mapping[str, Any]] = (),
    entrypoints: Sequence[Mapping[str, Any]] = (),
    categories: Sequence[str] = (),
    tags: Sequence[str] = (),
    member: Mapping[str, Any] | None = None,
    compatibility: Mapping[str, Any] | None = None,
    actor: str = "user:local",
) -> dict[str, Any]:
    token = _project_id(project_id)
    member_value = {
        "ref": primary_ref,
        "role": "primary",
        "exposure": "application",
        "lifecycle": "bound",
        "relations": ["uses"],
        **dict(member or {}),
    }
    return {
        "schema": _SCHEMA,
        "kind": "project",
        "id": token,
        "version": "0.1.0",
        "profiles": [str(item).strip() for item in profiles if str(item).strip()],
        "components": {
            "owned": [member_value],
            "dependencies": [dict(item) for item in dependencies],
        },
        "entrypoints": [dict(item) for item in entrypoints],
        "catalog": {
            "title": str(title or token).strip(),
            "description": str(description or "").strip(),
            "categories": [str(item).strip() for item in categories if str(item).strip()],
            "tags": [str(item).strip() for item in tags if str(item).strip()],
        },
        "publication": dict(_PUBLICATION_DEFAULTS),
        "install": {"default": False, "features": []},
        **({"compatibility": dict(compatibility)} if compatibility is not None else {}),
        "lifecycle": {
            "uninstall": {
                "components": "remove_if_unreferenced",
                "runtime_data": "retain",
                "source_artifacts": "retain",
            }
        },
        "created_at": _now(),
        "created_by": str(actor or "user:local"),
    }


def create_with_primary_component(
    project_id: str,
    *,
    kind: str,
    component_id: str | None = None,
    template: str | None = None,
    title: str,
    description: str = "",
    profiles: Sequence[str] = (),
    dependencies: Sequence[Mapping[str, Any]] = (),
    entrypoints: Sequence[Mapping[str, Any]] = (),
    categories: Sequence[str] = (),
    tags: Sequence[str] = (),
    member: Mapping[str, Any] | None = None,
    compatibility: Mapping[str, Any] | None = None,
    actor: str = "user:local",
) -> dict[str, Any]:
    """Atomically scaffold one component and its distributable Project.

    The operation is deliberately domain-neutral. Domain orchestrators choose
    templates, profiles, bindings, and contracts; Builder owns source creation
    and rollback of roots created by this call.
    """

    token = _project_id(project_id)
    component_kind = str(kind or "").strip().lower()
    if component_kind not in {"skill", "scenario"}:
        raise ProjectCompositionError("component kind must be skill or scenario")
    target_component = _project_id(component_id or token)
    project_root = resolve_root(token, required=False)
    component_root = component_projects.resolve_root(
        component_kind, target_component, required=False
    )
    if project_root.exists():
        raise ProjectCompositionError(f"project:{token} already exists")
    if component_root.exists():
        raise ProjectCompositionError(
            f"{component_kind}:{target_component} already exists"
        )
    created_component = False
    created_project = False
    try:
        component_projects.create(
            component_kind,
            target_component,
            **({"template": template} if template else {}),
        )
        created_component = True
        component_projects.update_metadata(
            component_kind,
            target_component,
            title=str(title or token).strip(),
            description=str(description or "").strip(),
        )
        payload = _new_project_definition(
            token,
            primary_ref=f"{component_kind}:{target_component}",
            title=title,
            description=description,
            profiles=profiles,
            dependencies=dependencies,
            entrypoints=entrypoints,
            categories=categories,
            tags=tags,
            member=member,
            compatibility=compatibility,
            actor=actor,
        )
        result = create(payload)
        created_project = True
        return {
            "ok": True,
            "project": result,
            "primary_component": component_projects.describe(
                component_kind, target_component
            ),
        }
    except Exception:
        if (created_project or project_root.is_dir()) and project_root.parent == _root_parent():
            shutil.rmtree(project_root)
        expected_parent = component_projects.resolve_root(
            component_kind, target_component, required=False
        ).parent
        if created_component and component_root.parent == expected_parent and component_root.is_dir():
            shutil.rmtree(component_root)
        raise


def create_for_existing_component(
    project_id: str,
    *,
    kind: str,
    component_id: str,
    title: str | None = None,
    description: str | None = None,
    profiles: Sequence[str] = (),
    dependencies: Sequence[Mapping[str, Any]] = (),
    entrypoints: Sequence[Mapping[str, Any]] | None = None,
    categories: Sequence[str] = (),
    tags: Sequence[str] = (),
    member: Mapping[str, Any] | None = None,
    compatibility: Mapping[str, Any] | None = None,
    actor: str = "user:local",
) -> dict[str, Any]:
    """Create a Project authority around one existing unowned DEV component."""

    token = _project_id(project_id)
    component_kind = str(kind or "").strip().lower().rstrip("s")
    if component_kind not in {"skill", "scenario"}:
        raise ProjectCompositionError("component kind must be skill or scenario")
    target_component = _project_id(component_id)
    component_ref = f"{component_kind}:{target_component}"
    if resolve_root(token, required=False).exists():
        raise ProjectCompositionError(f"project:{token} already exists")
    try:
        described = component_projects.describe(component_kind, target_component)
    except Exception as exc:
        raise ProjectCompositionError(
            f"{component_ref} was not found in DEV space"
        ) from exc
    owner = project_for_component(component_ref)
    if owner is not None:
        raise ProjectCompositionError(
            f"{component_ref} is already owned by {owner['ref']}"
        )
    resolved_title = str(title or described.get("title") or described.get("name") or token).strip()
    resolved_description = str(
        description
        if description is not None
        else described.get("description") or ""
    ).strip()
    resolved_entrypoints = (
        [
            {
                "id": "main",
                "presentation": component_ref,
                "default": True,
                "bindings": {},
            }
        ]
        if entrypoints is None and component_kind == "scenario"
        else [dict(item) for item in (entrypoints or ())]
    )
    result = create(
        _new_project_definition(
            token,
            primary_ref=component_ref,
            title=resolved_title,
            description=resolved_description,
            profiles=profiles,
            dependencies=dependencies,
            entrypoints=resolved_entrypoints,
            categories=categories,
            tags=tags,
            member=member,
            compatibility=compatibility,
            actor=actor,
        )
    )
    return {
        "ok": True,
        "project": result,
        "primary_component": described,
        "created_component": False,
    }


def ensure_owned_component(
    project_id: str,
    component_ref: str,
    *,
    role: str = "implementation",
    exposure: str = "project_only",
    lifecycle: str = "bound",
    relations: Sequence[str] = ("realizes", "uses"),
) -> dict[str, Any]:
    """Idempotently attach an existing component to one mutable DEV Project."""

    project = get(project_id)
    ref = str(component_ref or "").strip()
    kind, separator, component_id = ref.partition(":")
    if separator != ":" or kind not in {"skill", "scenario"}:
        raise ProjectCompositionError("component_ref must identify a skill or scenario")
    component_projects.describe(kind, _project_id(component_id))
    owned = [dict(item) for item in project["components"]["owned"]]
    if any(str(item.get("ref") or "") == ref for item in owned):
        return {"ok": True, "idempotent": True, "project": project}
    owner = project_for_component(ref)
    if owner is not None and str(owner.get("id") or "") != str(project["id"]):
        raise ProjectCompositionError(f"{ref} is already owned by {owner['ref']}")
    replacement = {
        key: item
        for key, item in project.items()
        if key not in {"ref", "manifest_digest", "source_path"}
    }
    replacement["components"] = {
        **dict(replacement["components"]),
        "owned": [
            *owned,
            {
                "ref": ref,
                "role": str(role or "implementation"),
                "exposure": str(exposure or "project_only"),
                "lifecycle": str(lifecycle or "bound"),
                "relations": [str(item) for item in relations],
            },
        ],
    }
    updated = replace(
        str(project["id"]),
        replacement,
        expected_manifest_digest=str(project["manifest_digest"]),
    )
    return {"ok": True, "idempotent": False, "project": updated}


def create_research_direction(
    project_id: str,
    *,
    title: str,
    description: str = "",
    skill_id: str | None = None,
    categories: Sequence[str] = ("research",),
    tags: Sequence[str] = (),
    actor: str = "user:local",
) -> dict[str, Any]:
    """Compatibility wrapper for legacy direction-as-Project callers."""

    result = create_with_primary_component(
        project_id,
        kind="skill",
        component_id=skill_id,
        template="research_direction",
        title=title,
        description=description,
        profiles=("adaos.research.direction.v1",),
        dependencies=(
            {
                "ref": "project:adaos_research_platform",
                "version": "^0.1",
                "lifecycle": "shared",
                "relations": ["presents", "uses"],
            },
        ),
        entrypoints=(
            {
                "id": "research",
                "presentation": "scenario:research_workbench",
                "default": True,
                "bindings": {"direction_ref": f"skill:{_project_id(skill_id or project_id)}"},
            },
        ),
        categories=categories,
        tags=tags,
        member={
            "exposure": "project_only",
            "lifecycle": "bound",
            "relations": ["realizes"],
        },
        compatibility={"required_entrypoints": ["research"]},
        actor=actor,
    )
    return {
        **result,
        "primary_skill": result["primary_component"],
    }


def project_for_component(component_ref: str) -> dict[str, Any] | None:
    matches = []
    for item in list_projects(limit=5000):
        project = get(str(item["id"]))
        if component_ref in {str(owned["ref"]) for owned in project["components"]["owned"]}:
            matches.append(project)
    if not matches:
        return None
    if len(matches) > 1:
        raise ProjectCompositionError(f"component {component_ref} is owned by multiple local Projects")
    return matches[0]


def prepare_candidate(
    project_id: str,
    *,
    source_kind: str,
    source_name: str,
    source_revision: str,
    change_ids: Sequence[str],
    validation_evidence: Mapping[str, Any] | None = None,
    target_webspace_id: str = "desktop",
    target_space_kind: str = "development",
    target_zone: str | None = None,
    target_subnet_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Prepare an immutable Trial for the Project owning a changed component."""

    token = _project_id(project_id)
    get(token)
    component_kind = str(source_kind or "").strip().lower().rstrip("s")
    if component_kind not in {"skill", "scenario"}:
        raise ProjectCompositionError("source_kind must be skill or scenario")
    component_id = _project_id(source_name)
    revision = str(source_revision or "").strip()
    if not revision:
        raise ProjectCompositionError("source_revision is required")
    bounded_changes = tuple(
        dict.fromkeys(str(item).strip() for item in change_ids if str(item).strip())
    )
    if not bounded_changes:
        raise ProjectCompositionError("candidate requires at least one Builder Change id")
    from adaos.services.root.service import RootDeveloperService

    return RootDeveloperService().prepare_project_candidate(
        token,
        source_kind=component_kind,  # type: ignore[arg-type]
        source_name=component_id,
        source_revision=revision,
        change_ids=bounded_changes,
        validation_evidence=validation_evidence,
        target_webspace_id=target_webspace_id,
        target_space_kind=target_space_kind,
        target_zone=target_zone,
        target_subnet_id=target_subnet_id,
        idempotency_key=idempotency_key,
    )


def resolve_presentation(component_ref: str, *, project_id: str | None = None) -> dict[str, Any]:
    """Resolve Project entrypoint, skill default, then generic preview fallback."""

    project = get(project_id) if project_id else project_for_component(component_ref)
    if project:
        entrypoints = list(project.get("entrypoints") or [])
        selected = next((item for item in entrypoints if item.get("default") is True), entrypoints[0] if entrypoints else None)
        if selected:
            return {"source": "project", "project_ref": project["ref"], **dict(selected)}
    kind, _, component_id = str(component_ref).partition(":")
    if kind == "skill" and component_id:
        root = component_projects.resolve_root("skill", component_id)
        manifest = yaml.safe_load((root / "skill.yaml").read_text(encoding="utf-8-sig")) or {}
        presentations = list(manifest.get("presentations") or []) if isinstance(manifest, Mapping) else []
        selected = next((item for item in presentations if item.get("default") is True), presentations[0] if presentations else None)
        if isinstance(selected, Mapping) and selected.get("scenario"):
            return {
                "source": "skill",
                "id": str(selected.get("id") or "default"),
                "presentation": f"scenario:{selected['scenario']}",
                "bindings": dict(selected.get("bindings") or {}),
            }
    return {
        "source": "fallback",
        "id": "skill-preview",
        "presentation": "scenario:skill_preview",
        "bindings": {"component_ref": component_ref},
    }


__all__ = [
    "ProjectCompositionError",
    "ProjectCompositionNotFound",
    "create",
    "replace",
    "create_with_primary_component",
    "create_for_existing_component",
    "ensure_owned_component",
    "create_research_direction",
    "get",
    "list_projects",
    "normalized_definition",
    "prepare_candidate",
    "project_for_component",
    "resolve_presentation",
    "resolve_root",
    "validate",
]
