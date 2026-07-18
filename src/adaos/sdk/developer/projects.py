"""Bounded SDK access to DEV skill and scenario projects."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core.errors import SdkError

ProjectKind = Literal["skill", "scenario"]

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".intent",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_READONLY_NAMES = {"prompt_state.json", "prep_result.json"}
_READONLY_PREFIXES = ("ui_revisions/", ".git/")


class DeveloperProjectError(SdkError):
    """Raised when a DEV project operation violates the public contract."""


class ProjectNotFoundError(DeveloperProjectError):
    """Raised when a requested DEV project does not exist."""


def _kind(value: str) -> ProjectKind:
    normalized = str(value or "").strip().lower().rstrip("s")
    if normalized not in {"skill", "scenario"}:
        raise DeveloperProjectError("kind must be skill or scenario")
    return normalized  # type: ignore[return-value]


def _project_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not _PROJECT_ID.fullmatch(normalized):
        raise DeveloperProjectError("project_id contains unsupported characters")
    return normalized


def _roots() -> tuple[Path, Path]:
    ctx = require_ctx("sdk.developer.projects")
    return Path(ctx.paths.dev_skills_dir()).resolve(), Path(ctx.paths.dev_scenarios_dir()).resolve()


def _root(kind: str, project_id: str, *, required: bool = True) -> Path:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    skills, scenarios = _roots()
    parent = skills if normalized_kind == "skill" else scenarios
    candidate = (parent / normalized_id).resolve()
    if candidate.parent != parent:
        raise DeveloperProjectError("project path escapes DEV root")
    if required and not candidate.is_dir():
        raise ProjectNotFoundError(f"{normalized_kind} '{normalized_id}' was not found in DEV space")
    return candidate


def _manifest_path(kind: ProjectKind, root: Path) -> Path | None:
    names = (
        ("skill.yaml", "manifest.yaml", "skill.json", "manifest.json")
        if kind == "skill"
        else ("scenario.yaml", "scenario.yml", "scenario.json")
    )
    return next((root / name for name in names if (root / name).is_file()), None)


def _read_manifest(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DeveloperProjectError(f"failed to read project manifest: {exc}") from exc
    return dict(value) if isinstance(value, Mapping) else {}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    data = getattr(value, "__dict__", None)
    if isinstance(data, Mapping):
        return _jsonable(data)
    return str(value)


def _service():
    from adaos.services.root.service import RootDeveloperService

    return RootDeveloperService()


def list_projects(*, kind: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """List local DEV projects without contacting Root/Forge."""

    skills, scenarios = _roots()
    requested = [_kind(kind)] if kind else ["scenario", "skill"]
    items: list[dict[str, Any]] = []
    for current in requested:
        parent = skills if current == "skill" else scenarios
        if not parent.is_dir():
            continue
        for root in sorted((item for item in parent.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
            manifest_path = _manifest_path(current, root)
            manifest = _read_manifest(manifest_path)
            project_id = str(manifest.get("id") or manifest.get("name") or root.name)
            items.append(
                {
                    "kind": current,
                    "id": project_id,
                    "name": str(manifest.get("name") or project_id),
                    "title": str(manifest.get("title") or manifest.get("name") or project_id),
                    "description": str(manifest.get("description") or ""),
                    "version": str(manifest.get("version") or ""),
                    "depends": list(manifest.get("depends") or []),
                    "manifest": manifest_path.name if manifest_path else None,
                }
            )
            if len(items) >= max(1, min(int(limit), 5000)):
                return items
    return items


def describe(kind: str, project_id: str) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    root = _root(normalized_kind, project_id)
    manifest_path = _manifest_path(normalized_kind, root)
    manifest = _read_manifest(manifest_path)
    return {
        "ok": True,
        "kind": normalized_kind,
        "id": str(manifest.get("id") or manifest.get("name") or root.name),
        "name": str(manifest.get("name") or manifest.get("id") or root.name),
        "title": str(manifest.get("title") or manifest.get("name") or manifest.get("id") or root.name),
        "description": str(manifest.get("description") or ""),
        "version": str(manifest.get("version") or ""),
        "depends": list(manifest.get("depends") or []),
        "manifest": manifest_path.name if manifest_path else None,
    }


def _file(root: Path, relative_path: str) -> tuple[str, Path]:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw:
        raise DeveloperProjectError("path is required")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise DeveloperProjectError("path is outside project root")
    full = (root / relative).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise DeveloperProjectError("path is outside project root") from exc
    return relative.as_posix(), full


def _editable(relative_path: str, full: Path | None = None) -> tuple[bool, str]:
    path = Path(relative_path)
    if path.name in _READONLY_NAMES:
        return False, "managed_state_file"
    if relative_path.startswith(_READONLY_PREFIXES):
        return False, "managed_or_append_only_file"
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return False, "unsupported_file_type"
    if full is not None and full.is_symlink():
        return False, "symlink_not_editable"
    return True, ""


def list_files(kind: str, project_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    normalized_kind = _kind(kind)
    root = _root(normalized_kind, project_id)
    maximum = max(1, min(int(limit), 5000))
    items: list[dict[str, Any]] = []
    for full in sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.as_posix().lower()):
        relative = full.relative_to(root).as_posix()
        if relative.startswith(".git/"):
            continue
        stat = full.stat()
        editable, reason = _editable(relative, full)
        items.append(
            {
                "kind": normalized_kind,
                "project_id": project_id,
                "path": relative,
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "editable": editable,
                "readonly_reason": reason,
            }
        )
        if len(items) >= maximum:
            break
    return items


def read_file(kind: str, project_id: str, path: str, *, max_bytes: int = 131_072) -> dict[str, Any]:
    root = _root(kind, project_id)
    relative, full = _file(root, path)
    if not full.is_file():
        raise ProjectNotFoundError(f"project file '{relative}' was not found")
    maximum = max(1, min(int(max_bytes), 1_048_576))
    raw = full.read_bytes()
    truncated = len(raw) > maximum
    content = raw[:maximum].decode("utf-8", errors="replace")
    editable, reason = _editable(relative, full)
    return {
        "ok": True,
        "kind": _kind(kind),
        "project_id": _project_id(project_id),
        "path": relative,
        "content": content,
        "size_bytes": len(raw),
        "truncated": truncated,
        "editable": editable and not truncated,
        "readonly_reason": reason or ("file_too_large" if truncated else ""),
    }


def write_file(
    kind: str,
    project_id: str,
    path: str,
    text: str,
    *,
    max_bytes: int = 131_072,
) -> dict[str, Any]:
    root = _root(kind, project_id)
    relative, full = _file(root, path)
    editable, reason = _editable(relative, full)
    if not editable:
        raise DeveloperProjectError(f"project file is not editable: {reason}")
    raw = str(text).encode("utf-8")
    maximum = max(1, min(int(max_bytes), 1_048_576))
    if len(raw) > maximum:
        raise DeveloperProjectError(f"project file exceeds {maximum} bytes")
    full.parent.mkdir(parents=True, exist_ok=True)
    temporary = full.with_name(f".{full.name}.tmp")
    temporary.write_bytes(raw)
    temporary.replace(full)
    return {
        "ok": True,
        "kind": _kind(kind),
        "project_id": _project_id(project_id),
        "path": relative,
        "size_bytes": len(raw),
    }


def list_templates(kind: str) -> list[dict[str, Any]]:
    normalized_kind = _kind(kind)
    plural = f"{normalized_kind}s"
    service = _service()
    workspace = service._workspace_templates_dir(plural)
    builtin = service._builtin_templates_dir(plural)
    default = service._default_template_name(plural)
    user_names = [name for name in service._collect_templates(workspace) if not name.startswith((".", "_"))]
    builtin_names = [name for name in service._collect_templates(builtin) if not name.startswith((".", "_"))]
    items = [{"id": default, "label": "Default", "source": "builtin", "kind": normalized_kind}]
    items.extend({"id": name, "label": f"{name} (workspace)", "source": "workspace", "kind": normalized_kind} for name in user_names)
    items.extend(
        {"id": name, "label": f"{name} (builtin)", "source": "builtin", "kind": normalized_kind}
        for name in builtin_names
        if name != default
    )
    return items


def create(kind: str, project_id: str, *, template: str | None = None) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    service = _service()
    result = (
        service.create_skill(normalized_id, template=template)
        if normalized_kind == "skill"
        else service.create_scenario(normalized_id, template=template)
    )
    return _jsonable(result)


def push(
    kind: str,
    project_id: str,
    *,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    service = _service()
    method = service.push_skill if normalized_kind == "skill" else service.push_scenario
    return _jsonable(method(normalized_id, message=message, metadata=metadata))


def update(kind: str, project_id: str) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    service = _service()
    method = service.update_skill if normalized_kind == "skill" else service.update_scenario
    return _jsonable(method(normalized_id))


def publish(
    kind: str,
    project_id: str,
    *,
    bump: Literal["major", "minor", "patch"] = "patch",
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    service = _service()
    method = service.publish_skill if normalized_kind == "skill" else service.publish_scenario
    return _jsonable(method(normalized_id, bump=bump, force=force, dry_run=dry_run))


def delete(kind: str, project_id: str, *, remove_local: bool = True) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    local_root = _root(normalized_kind, normalized_id, required=False)
    service = _service()
    method = service.delete_skill if normalized_kind == "skill" else service.delete_scenario
    result = _jsonable(method(normalized_id))
    removed = False
    if remove_local and local_root.is_dir():
        parent = local_root.parent.resolve()
        resolved = local_root.resolve()
        if resolved.parent != parent:
            raise DeveloperProjectError("refusing to remove project outside DEV root")
        shutil.rmtree(resolved)
        removed = True
    return {**dict(result or {}), "local_removed": removed}


__all__ = [
    "DeveloperProjectError",
    "ProjectNotFoundError",
    "create",
    "delete",
    "describe",
    "list_files",
    "list_projects",
    "list_templates",
    "publish",
    "push",
    "read_file",
    "update",
    "write_file",
]
