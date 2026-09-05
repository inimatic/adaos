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

from adaos.domain.project_events import PROJECT_CONTENT_CHANGED, ProjectEventIdentity
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
_IGNORED_FILE_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
_IGNORED_FILE_SUFFIXES = {".pyc", ".pyo"}


class DeveloperProjectError(SdkError):
    """Raised when a DEV project operation violates the public contract."""


class ProjectNotFoundError(DeveloperProjectError):
    """Raised when a requested DEV project does not exist."""


def _publish_content_changed(
    kind: str,
    project_id: str,
    *,
    reason: str,
    changed_paths: list[str] | None = None,
) -> None:
    try:
        from adaos.sdk.data.events import publish

        identity = ProjectEventIdentity(kind=_kind(kind), project_id=_project_id(project_id))
        publish(
            PROJECT_CONTENT_CHANGED,
            identity.payload(
                reason=str(reason or "content_changed").strip() or "content_changed",
                changed_paths=list(changed_paths or []),
            ),
            source="sdk.developer.projects",
        )
    except Exception:
        # Local SDK operations remain usable in tests and offline tooling that
        # intentionally do not initialize the runtime event bus.
        return


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


def resolve_root(kind: str, project_id: str, *, required: bool = True) -> Path:
    """Resolve an exact component source root through the active CTX snapshot."""

    return _root(kind, project_id, required=required)


def _manifest_path(kind: ProjectKind, root: Path) -> Path | None:
    names = ("skill.yaml",) if kind == "skill" else ("scenario.yaml",)
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
        for root in sorted(
            (item for item in parent.iterdir() if item.is_dir() and not item.name.startswith((".", "_"))),
            key=lambda item: item.name.lower(),
        ):
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
        "project_type": str(manifest.get("type") or normalized_kind),
        "version": str(manifest.get("version") or ""),
        "depends": list(manifest.get("depends") or []),
        "manifest": manifest_path.name if manifest_path else None,
    }


def _write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    if path.suffix.lower() == ".json":
        text = json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
    else:
        text = yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def update_metadata(
    kind: str,
    project_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    project_type: str | None = None,
) -> dict[str, Any]:
    """Update bounded manifest metadata while preserving scenario UI payloads."""

    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    root = _root(normalized_kind, normalized_id)
    if title is not None and not str(title).strip():
        raise DeveloperProjectError("title must not be empty")
    manifests = [
        root / name
        for name in (("scenario.yaml",) if normalized_kind == "scenario" else ("skill.yaml",))
        if (root / name).is_file()
    ]
    if not manifests:
        raise ProjectNotFoundError(f"manifest for {normalized_kind} '{normalized_id}' was not found")
    if project_type is not None:
        requested_type = str(project_type).strip()
        current_types = {
            str(_read_manifest(path).get("type") or normalized_kind).strip()
            for path in manifests
        }
        if not requested_type or current_types != {requested_type}:
            current = ", ".join(sorted(current_types)) or normalized_kind
            raise DeveloperProjectError(
                f"project_type is immutable after creation (current: {current})"
            )
    values = {
        "title": str(title).strip() if title is not None else None,
        "description": str(description).strip() if description is not None else None,
    }
    updated: list[str] = []
    for path in manifests:
        payload = _read_manifest(path)
        for key, value in values.items():
            if value is not None:
                payload[key] = value
        _write_manifest(path, payload)
        updated.append(path.name)
    result = {**describe(normalized_kind, normalized_id), "updated_manifests": updated}
    _publish_content_changed(
        normalized_kind,
        normalized_id,
        reason="project_metadata_updated",
        changed_paths=updated,
    )
    return result


def find_scenario_root(project_id: str) -> Path | None:
    """Find a scenario in the active DEV snapshot or another local snapshot."""

    normalized_id = _project_id(project_id)
    direct = _root("scenario", normalized_id, required=False)
    if direct.is_dir():
        return direct
    _skills, scenarios = _roots()
    dev_root = scenarios.parent.parent
    for candidate in sorted(dev_root.glob(f"*/scenarios/{normalized_id}")):
        resolved = candidate.resolve()
        if resolved.is_dir():
            return resolved
    return None


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
        relative_parts = set(Path(relative).parts)
        if relative_parts & _IGNORED_FILE_PARTS or full.suffix.lower() in _IGNORED_FILE_SUFFIXES:
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
    result = {
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
    return result


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
    result = {
        "ok": True,
        "kind": _kind(kind),
        "project_id": _project_id(project_id),
        "path": relative,
        "size_bytes": len(raw),
    }
    _publish_content_changed(
        _kind(kind),
        _project_id(project_id),
        reason="project_file_written",
        changed_paths=[relative],
    )
    return result


def list_templates(kind: str) -> list[dict[str, Any]]:
    normalized_kind = _kind(kind)
    plural = f"{normalized_kind}s"
    service = _service()
    workspace = service._workspace_templates_dir(plural)
    builtin = service._builtin_templates_dir(plural)
    default = service._default_template_name(plural)
    user_names = [name for name in service._collect_templates(workspace) if not name.startswith((".", "_"))]
    builtin_names = [name for name in service._collect_templates(builtin) if not name.startswith((".", "_"))]
    def item(name: str, source: str) -> dict[str, Any]:
        parent = builtin if source == "builtin" else workspace
        manifest_name = "skill.yaml" if normalized_kind == "skill" else "scenario.yaml"
        manifest = _read_manifest(parent / name / manifest_name)
        description = str(manifest.get("description") or "").strip()
        version = str(manifest.get("version") or "").strip()
        suffix = "builtin" if source == "builtin" else "workspace"
        return {
            "id": name,
            "label": "Default" if name == default and source == "builtin" else f"{name} ({suffix})",
            "source": source,
            "kind": normalized_kind,
            "version": version,
            "description": description,
            "search_text": " ".join(part for part in (name, version, description, suffix) if part),
        }

    ordered_builtin = [default, *(name for name in builtin_names if name != default)]
    items = [item(name, "builtin") for name in ordered_builtin]
    items.extend(item(name, "workspace") for name in user_names if name not in set(ordered_builtin))
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
    payload = _jsonable(result)
    _publish_content_changed(normalized_kind, normalized_id, reason="project_created")
    return payload


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
    result = _jsonable(method(normalized_id, message=message, metadata=metadata))
    _publish_content_changed(normalized_kind, normalized_id, reason="project_pushed")
    return result


def update(kind: str, project_id: str) -> dict[str, Any]:
    _kind(kind)
    _project_id(project_id)
    raise DeveloperProjectError(
        "DEV draft update is retired because it can overwrite local changes; "
        "use an exact-base rebase/migration workflow instead"
    )


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
    result = _jsonable(method(normalized_id, bump=bump, force=force, dry_run=dry_run))
    if not dry_run:
        _publish_content_changed(normalized_kind, normalized_id, reason="project_published")
    return result


def prepare_candidate(
    kind: str,
    project_id: str,
    *,
    change_ids: list[str] | tuple[str, ...],
    validation_evidence: Mapping[str, Any] | None = None,
    target_webspace_id: str = "desktop",
    target_space_kind: str = "development",
    target_zone: str | None = None,
    target_subnet_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    bounded_changes = tuple(str(item).strip() for item in change_ids if str(item).strip())
    if not bounded_changes:
        raise DeveloperProjectError("candidate requires at least one Builder Change id")
    result = _jsonable(
        _service().prepare_artifact_candidate(
            normalized_kind,
            normalized_id,
            change_ids=bounded_changes,
            validation_evidence=validation_evidence,
            target_webspace_id=target_webspace_id,
            target_space_kind=target_space_kind,
            target_zone=target_zone,
            target_subnet_id=target_subnet_id,
            idempotency_key=idempotency_key,
        )
    )
    _publish_content_changed(normalized_kind, normalized_id, reason="candidate_prepared")
    return result


def decide_candidate(
    candidate_id: str,
    *,
    accepted: bool,
    observations: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    token = str(candidate_id or "").strip()
    if not token:
        raise DeveloperProjectError("candidate_id is required")
    return _jsonable(
        _service().decide_artifact_candidate(
            token,
            accepted=accepted,
            observations=tuple(dict(item) for item in observations),
        )
    )


def get_candidate(candidate_id: str) -> dict[str, Any]:
    """Inspect one prepared candidate without repeating trial activation."""

    token = str(candidate_id or "").strip()
    if not token:
        raise DeveloperProjectError("candidate_id is required")
    return _jsonable(_service().get_artifact_candidate(token))


def reconcile_candidate_trial(candidate_id: str) -> dict[str, Any]:
    """Rebuild an active derived Trial Workspace from its immutable release."""

    token = str(candidate_id or "").strip()
    if not token:
        raise DeveloperProjectError("candidate_id is required")
    return _jsonable(_service().reconcile_artifact_trial_activation(token))


def prepare_rebased_candidate(
    stale_candidate_id: str,
    kind: str,
    project_id: str,
    *,
    validation_evidence: Mapping[str, Any] | None = None,
    target_webspace_id: str = "desktop",
    target_space_kind: str = "development",
    target_zone: str | None = None,
    target_subnet_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    candidate_token = str(stale_candidate_id or "").strip()
    if not candidate_token:
        raise DeveloperProjectError("stale_candidate_id is required")
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    result = _jsonable(
        _service().prepare_rebased_artifact_candidate(
            candidate_token,
            normalized_kind,
            normalized_id,
            validation_evidence=validation_evidence,
            target_webspace_id=target_webspace_id,
            target_space_kind=target_space_kind,
            target_zone=target_zone,
            target_subnet_id=target_subnet_id,
            idempotency_key=idempotency_key,
        )
    )
    _publish_content_changed(normalized_kind, normalized_id, reason="candidate_rebased")
    return result


def promote_candidate(
    candidate_id: str,
    *,
    permission_decision: bool | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    token = str(candidate_id or "").strip()
    if not token:
        raise DeveloperProjectError("candidate_id is required")
    kwargs = {"permission_decision": permission_decision} if permission_decision is not None else {}
    result = _jsonable(_service().promote_artifact_candidate(token, **kwargs))
    kind = str(result.get("kind") or "").strip()
    project_id = str(result.get("name") or "").strip()
    if kind and project_id:
        _publish_content_changed(kind, project_id, reason="candidate_promoted")
    return result


def check_subscription(project_id: str) -> dict[str, Any]:
    normalized_id = _project_id(project_id)
    return _jsonable(_service().check_artifact_subscription(normalized_id))


def plan_registry_reconciliation(
    kind: str,
    project_id: str,
    *,
    channel: str = "stable",
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    return _jsonable(
        _service().plan_artifact_registry_reconciliation(
            normalized_kind,
            normalized_id,
            channel=str(channel or "stable").strip() or "stable",
        )
    )


def apply_registry_reconciliation(
    kind: str,
    project_id: str,
    *,
    reviewed_plan_digest: str,
    channel: str = "stable",
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    reviewed = str(reviewed_plan_digest or "").strip().lower()
    if not reviewed:
        raise DeveloperProjectError("reviewed_plan_digest is required")
    return _jsonable(
        _service().apply_artifact_registry_reconciliation(
            normalized_kind,
            normalized_id,
            channel=str(channel or "stable").strip() or "stable",
            reviewed_plan_digest=reviewed,
        )
    )


def plan_remote_registry_recovery(
    kind: str,
    project_id: str,
    *,
    channel: str = "stable",
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    return _jsonable(
        _service().plan_artifact_remote_registry_recovery(
            normalized_kind,
            normalized_id,
            channel=str(channel or "stable").strip() or "stable",
        )
    )


def revalidate_remote_registry_recovery(
    kind: str,
    project_id: str,
    *,
    channel: str = "stable",
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    return _jsonable(
        _service().revalidate_artifact_remote_registry_recovery(
            normalized_kind,
            normalized_id,
            channel=str(channel or "stable").strip() or "stable",
        )
    )


def apply_remote_registry_recovery(
    kind: str,
    project_id: str,
    *,
    reviewed_plan_digest: str,
    channel: str = "stable",
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    reviewed = str(reviewed_plan_digest or "").strip().lower()
    if not reviewed:
        raise DeveloperProjectError("reviewed_plan_digest is required")
    return _jsonable(
        _service().apply_artifact_remote_registry_recovery(
            normalized_kind,
            normalized_id,
            channel=str(channel or "stable").strip() or "stable",
            reviewed_plan_digest=reviewed,
        )
    )


def plan_subscription_update(project_id: str) -> dict[str, Any]:
    normalized_id = _project_id(project_id)
    return _jsonable(_service().plan_artifact_subscription_update(normalized_id))


def inspect_subscription_update(project_id: str) -> dict[str, Any]:
    normalized_id = _project_id(project_id)
    return _jsonable(_service().inspect_artifact_subscription_update(normalized_id))


async def apply_subscription_update(
    kind: str,
    project_id: str,
    *,
    expected_plan_digest: str,
    idempotency_key: str | None = None,
    permission_decision: bool | Mapping[str, Any] | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    expected = str(expected_plan_digest or "").strip()
    if not expected:
        raise DeveloperProjectError("expected_plan_digest is required")
    from adaos.services.artifact_subscription_update import (
        ArtifactSubscriptionUpdateCoordinator,
    )

    ctx = require_ctx("sdk.developer.projects")
    coordinator = ArtifactSubscriptionUpdateCoordinator(ctx)
    result = await coordinator.update(
        normalized_kind,
        normalized_id,
        expected_plan_digest=expected,
        idempotency_key=(str(idempotency_key or "").strip() or None),
        permission_decision=permission_decision,
        webspace_id=webspace_id,
    )
    _publish_content_changed(normalized_kind, normalized_id, reason="subscription_activated")
    return _jsonable(result)


def activate_subscription(
    kind: str,
    project_id: str,
    *,
    idempotency_key: str | None = None,
    expected_plan_digest: str | None = None,
    permission_decision: bool | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    normalized_id = _project_id(project_id)
    kwargs: dict[str, Any] = {
        "idempotency_key": idempotency_key,
        "expected_plan_digest": expected_plan_digest,
    }
    if permission_decision is not None:
        kwargs["permission_decision"] = permission_decision
    result = _jsonable(_service().activate_artifact_subscription(normalized_id, **kwargs))
    _publish_content_changed(normalized_kind, normalized_id, reason="subscription_activated")
    return result


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
    payload = {**dict(result or {}), "local_removed": removed}
    _publish_content_changed(normalized_kind, normalized_id, reason="project_deleted")
    return payload


__all__ = [
    "DeveloperProjectError",
    "ProjectNotFoundError",
    "create",
    "check_subscription",
    "plan_registry_reconciliation",
    "apply_registry_reconciliation",
    "plan_remote_registry_recovery",
    "revalidate_remote_registry_recovery",
    "apply_remote_registry_recovery",
    "inspect_subscription_update",
    "plan_subscription_update",
    "activate_subscription",
    "apply_subscription_update",
    "delete",
    "describe",
    "find_scenario_root",
    "list_files",
    "list_projects",
    "list_templates",
    "publish",
    "prepare_candidate",
    "prepare_rebased_candidate",
    "decide_candidate",
    "promote_candidate",
    "push",
    "read_file",
    "update",
    "update_metadata",
    "write_file",
]
