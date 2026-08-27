from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from adaos.adapters.git.workspace import wait_for_materialized
from adaos.sdk.developer.compositions import normalized_definition
from adaos.services.artifact_pipeline.storage import atomic_write_json


class ProjectInstallError(RuntimeError):
    """Raised when a workspace Project cannot be installed."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProjectInstallError(f"cannot read Project manifest {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ProjectInstallError(f"Project manifest must contain an object: {path}")
    return dict(payload)


def load_workspace_project(workspace_root: Path, project_id: str) -> dict[str, Any]:
    workspace = Path(workspace_root).expanduser().resolve()
    project_root = (workspace / "projects" / str(project_id)).resolve()
    if workspace not in project_root.parents:
        raise ProjectInstallError("Project path escapes workspace")
    manifest = project_root / "project.yaml"
    if not manifest.is_file():
        raise ProjectInstallError(f"project:{project_id} was not found in workspace")
    return normalized_definition(_read_yaml(manifest))


def ensure_workspace_project_materialized(ctx: Any, project_id: str) -> None:
    workspace_root = Path(ctx.paths.workspace_dir()).expanduser().resolve()
    project_root = (workspace_root / "projects" / str(project_id)).resolve()
    if workspace_root not in project_root.parents:
        raise ProjectInstallError("Project path escapes workspace")
    if (project_root / "project.yaml").is_file():
        return
    sparse_add = getattr(getattr(ctx, "git", None), "sparse_add", None)
    if callable(sparse_add):
        sparse_add(str(workspace_root), f"projects/{project_id}")
        try:
            wait_for_materialized(
                project_root,
                files=("project.yaml",),
                attempts=5,
                delay=0.1,
            )
        except FileNotFoundError:
            pass


def list_workspace_projects(
    workspace_root: Path,
    *,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    workspace = Path(workspace_root).expanduser().resolve()
    projects_root = workspace / "projects"
    if not projects_root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for manifest in sorted(projects_root.glob("*/project.yaml"), key=lambda item: item.parent.name.lower()):
        try:
            definition = normalized_definition(_read_yaml(manifest))
        except Exception:
            continue
        visibility = str((definition.get("publication") or {}).get("visibility") or "unlisted")
        if visibility == "hidden" and not include_hidden:
            continue
        items.append({**definition, "source_path": str(manifest.parent.resolve())})
    return items


def default_install_project_ids(workspace_root: Path) -> tuple[str, ...]:
    return tuple(
        str(item["id"])
        for item in list_workspace_projects(workspace_root)
        if bool((item.get("install") or {}).get("default") is True)
    )


def selected_project_component_refs(
    definition: Mapping[str, Any],
    *,
    feature_ids: Sequence[str] = (),
    include_optional: bool = False,
) -> tuple[str, ...]:
    owned = [str(item.get("ref") or "") for item in (definition.get("components") or {}).get("owned") or []]
    owned_set = {ref for ref in owned if ref}
    primary_refs = {
        str(item.get("ref") or "")
        for item in (definition.get("components") or {}).get("owned") or []
        if item.get("role") == "primary"
    }
    selected = {ref for ref in primary_refs if ref}
    requested_features = {str(item).strip() for item in feature_ids if str(item).strip()}
    features = list((definition.get("install") or {}).get("features") or [])
    if not features:
        selected.update(owned_set)
    else:
        for feature in features:
            feature_id = str(feature.get("id") or "").strip()
            requested = bool(feature_id and feature_id in requested_features)
            enabled = (
                requested
                or bool(include_optional)
                or bool(feature.get("default") is True)
                or bool(feature.get("optional") is False)
            )
            if enabled:
                selected.update(
                    str(ref)
                    for ref in feature.get("components") or []
                    if str(ref).strip()
                )
    return tuple(ref for ref in owned if ref in selected)


def _installed_projects_path(ctx: Any) -> Path:
    state_dir = Path(ctx.paths.state_dir() if callable(ctx.paths.state_dir) else ctx.paths.state_dir)
    return state_dir / "projects" / "installed.json"


def load_installed_projects(ctx: Any) -> list[dict[str, Any]]:
    path = _installed_projects_path(ctx)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, Mapping):
        return []
    return [
        dict(item)
        for item in payload.get("projects") or []
        if isinstance(item, Mapping)
    ]


def record_project_install(
    ctx: Any,
    definition: Mapping[str, Any],
    *,
    component_refs: Sequence[str],
    webspace_id: str,
) -> dict[str, Any]:
    path = _installed_projects_path(ctx)
    items = load_installed_projects(ctx)
    project_id = str(definition.get("id") or "").strip()
    if not project_id:
        raise ProjectInstallError("Project id is empty")
    catalog = definition.get("catalog") if isinstance(definition.get("catalog"), Mapping) else {}
    record = {
        "id": project_id,
        "version": str(definition.get("version") or ""),
        "title": str(catalog.get("title") or project_id),
        "description": str(catalog.get("description") or ""),
        "categories": list(catalog.get("categories") or []),
        "tags": list(catalog.get("tags") or []),
        "publication": dict(definition.get("publication") or {}),
        "install": dict(definition.get("install") or {}),
        "component_refs": [str(ref) for ref in component_refs],
        "webspace_id": str(webspace_id),
        "installed_at": _now_iso(),
        "status": "installed",
        "source": "workspace",
    }
    for field in ("title_i18n", "description_i18n"):
        value = catalog.get(field)
        if isinstance(value, Mapping):
            record[field] = {str(key): item for key, item in value.items() if item is not None}
    remaining = [
        item
        for item in items
        if str(item.get("id") or "").strip() != project_id
    ]
    payload = {
        "schema": "adaos.project.installs.v1",
        "updated_at": record["installed_at"],
        "projects": sorted([*remaining, record], key=lambda item: str(item.get("id") or "")),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    return record


def install_workspace_project(
    project_id: str,
    *,
    ctx: Any,
    scenario_mgr: Any,
    skill_mgr: Any,
    webspace_id: str,
    setup_skills: bool = False,
    feature_ids: Sequence[str] = (),
    include_optional: bool = False,
) -> dict[str, Any]:
    workspace_root = Path(ctx.paths.workspace_dir())
    ensure_workspace_project_materialized(ctx, project_id)
    definition = load_workspace_project(workspace_root, project_id)
    component_refs = selected_project_component_refs(
        definition,
        feature_ids=feature_ids,
        include_optional=include_optional,
    )
    result: dict[str, Any] = {
        "id": str(definition["id"]),
        "version": str(definition["version"]),
        "title": str((definition.get("catalog") or {}).get("title") or definition["id"]),
        "components": list(component_refs),
        "scenarios": [],
        "skills": [],
        "warnings": [],
    }
    catalog = definition.get("catalog") if isinstance(definition.get("catalog"), Mapping) else {}
    for field in ("title_i18n", "description_i18n"):
        value = catalog.get(field)
        if isinstance(value, Mapping):
            result[field] = {str(key): item for key, item in value.items() if item is not None}

    for ref in component_refs:
        kind, _, artifact_id = ref.partition(":")
        if kind != "skill":
            continue
        try:
            skill_mgr.install(artifact_id, validate=False)
            runtime = None
            try:
                runtime = skill_mgr.prepare_runtime(artifact_id, run_tests=False)
            except Exception:
                runtime = None
            version = getattr(runtime, "version", None) if runtime else None
            slot = getattr(runtime, "slot", None) if runtime else None
            skill_mgr.activate_for_space(
                artifact_id,
                version=version,
                slot=slot,
                space="default",
                webspace_id=webspace_id,
            )
            if setup_skills:
                try:
                    skill_mgr.setup_skill(artifact_id)
                except Exception as exc:
                    result["warnings"].append(f"skill setup {artifact_id}: {exc}")
            result["skills"].append({"id": artifact_id, "version": version, "slot": slot})
        except Exception as exc:
            result["warnings"].append(f"skill {artifact_id}: {exc}")

    for ref in component_refs:
        kind, _, artifact_id = ref.partition(":")
        if kind != "scenario":
            continue
        try:
            meta = scenario_mgr.install_with_deps(artifact_id, webspace_id=webspace_id)
            result["scenarios"].append(
                {"id": meta.id.value, "version": getattr(meta, "version", None)}
            )
        except Exception as exc:
            result["warnings"].append(f"scenario {artifact_id}: {exc}")

    try:
        result["record"] = record_project_install(
            ctx,
            definition,
            component_refs=component_refs,
            webspace_id=webspace_id,
        )
    except Exception as exc:
        result["warnings"].append(f"project install record {definition['id']}: {exc}")
    return result


__all__ = [
    "ProjectInstallError",
    "default_install_project_ids",
    "ensure_workspace_project_materialized",
    "install_workspace_project",
    "list_workspace_projects",
    "load_installed_projects",
    "load_workspace_project",
    "record_project_install",
    "selected_project_component_refs",
]
