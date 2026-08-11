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
    if len(primaries) != 1:
        raise ProjectCompositionError("project must declare exactly one primary owned component")
    defaults = [item for item in payload.get("entrypoints") or [] if item.get("default") is True]
    if len(defaults) > 1:
        raise ProjectCompositionError("project may declare at most one default entrypoint")
    return payload


def _read(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectCompositionError(f"failed to read Project manifest: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ProjectCompositionError("Project manifest must be an object")
    return validate(value)


def _write(path: Path, value: Mapping[str, Any]) -> None:
    payload = validate(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def get(project_id: str) -> dict[str, Any]:
    root = resolve_root(project_id)
    payload = _read(root / "project.yaml")
    manifest_digest = "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **payload,
        "ref": f"project:{payload['id']}",
        "manifest_digest": manifest_digest,
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
        primary = next(item for item in project["components"]["owned"] if item["role"] == "primary")
        result.append(
            {
                "id": project["id"],
                "ref": f"project:{project['id']}",
                "version": project["version"],
                "title": project["catalog"]["title"],
                "description": project["catalog"]["description"],
                "profiles": list(project["profiles"]),
                "categories": list(project["catalog"]["categories"]),
                "tags": list(project["catalog"]["tags"]),
                "primary_ref": primary["ref"],
                "source_path": str(manifest_path.parent.resolve()),
                "manifest_digest": get(str(project["id"]))["manifest_digest"],
            }
        )
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
    """Atomically create a one-skill research Project through Builder SDKs."""

    token = _project_id(project_id)
    target_skill = _project_id(skill_id or token)
    project_root = resolve_root(token, required=False)
    skill_root = component_projects.resolve_root("skill", target_skill, required=False)
    if project_root.exists():
        raise ProjectCompositionError(f"project:{token} already exists")
    if skill_root.exists():
        raise ProjectCompositionError(f"skill:{target_skill} already exists")
    created_skill = False
    created_project = False
    try:
        component_projects.create("skill", target_skill, template="research_direction")
        created_skill = True
        component_projects.update_metadata(
            "skill",
            target_skill,
            title=str(title or token).strip(),
            description=str(description or "").strip(),
        )
        payload = {
            "schema": _SCHEMA,
            "kind": "project",
            "id": token,
            "version": "0.1.0",
            "profiles": ["adaos.research.direction.v1"],
            "components": {
                "owned": [{"ref": f"skill:{target_skill}", "role": "primary"}],
                "dependencies": [{"ref": "project:adaos_research_platform", "version": "^0.1"}],
            },
            "entrypoints": [
                {
                    "id": "research",
                    "presentation": "scenario:research_workbench",
                    "default": True,
                    "bindings": {"direction_ref": f"skill:{target_skill}"},
                }
            ],
            "catalog": {
                "title": str(title or token).strip(),
                "description": str(description or "").strip(),
                "categories": [str(item).strip() for item in categories if str(item).strip()],
                "tags": [str(item).strip() for item in tags if str(item).strip()],
            },
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
        result = create(payload)
        created_project = True
        return {"ok": True, "project": result, "primary_skill": component_projects.describe("skill", target_skill)}
    except Exception:
        # Roll back only roots proven to have been created by this operation.
        created_project = created_project or project_root.is_dir()
        if created_project and project_root.parent == _root_parent() and project_root.is_dir():
            shutil.rmtree(project_root)
        if created_skill and skill_root.parent == component_projects.resolve_root("skill", target_skill, required=False).parent and skill_root.is_dir():
            shutil.rmtree(skill_root)
        raise


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
    "create_research_direction",
    "get",
    "list_projects",
    "project_for_component",
    "resolve_presentation",
    "resolve_root",
    "validate",
]
