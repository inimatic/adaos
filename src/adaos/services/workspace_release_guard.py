from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.domain.artifact_release import WorkspaceLock


class WorkspaceSourceMutationBlocked(RuntimeError):
    """Raised when a legacy source command targets release-owned Workspace data."""


def _load_active_lock(workspace_root: Path) -> WorkspaceLock | None:
    lock_path = Path(workspace_root).resolve() / ".adaos" / "workspace.lock.json"
    if not lock_path.is_file():
        return None
    try:
        payload: Any = json.loads(lock_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, Mapping):
            raise ValueError("WorkspaceLock must contain an object")
        return WorkspaceLock.from_mapping(payload)
    except Exception as exc:
        raise WorkspaceSourceMutationBlocked(
            "cannot trust the active WorkspaceLock; refusing direct installed-Workspace mutation"
        ) from exc


def load_active_workspace_lock(workspace_root: Path) -> WorkspaceLock | None:
    """Load the trusted WorkspaceLock used by release-aware source commands."""

    return _load_active_lock(Path(workspace_root))


def assert_workspace_component_mutable(
    workspace_root: Path,
    *,
    kind: str,
    artifact_id: str,
) -> None:
    """Fail closed when a legacy command would edit a lock-managed component."""

    normalized_kind = str(kind or "").strip().lower().rstrip("s")
    normalized_id = str(artifact_id or "").strip()
    if normalized_kind not in {"skill", "scenario"} or not normalized_id:
        raise ValueError("kind and artifact_id must identify a skill or scenario")
    lock = _load_active_lock(Path(workspace_root))
    if lock is None:
        return
    component_key = f"{normalized_kind}:{normalized_id}"
    component = next((item for item in lock.components if item.key == component_key), None)
    if component is None:
        return
    raise WorkspaceSourceMutationBlocked(
        f"direct mutation of installed Workspace component {component_key} is blocked by "
        f"active WorkspaceLock revision {lock.lock_revision} "
        f"({component.version}, {component.digest}); edit its source under .adaos/dev and "
        "publish an immutable Candidate through Trial and Publication"
    )


def assert_workspace_component_maintenance_owned(
    workspace_root: Path,
    *,
    kind: str,
    artifact_id: str,
    project_id: str,
) -> None:
    """Authorize an explicit maintenance push only for a declared Project owner."""

    root = Path(workspace_root).resolve()
    normalized_kind = str(kind or "").strip().lower().rstrip("s")
    normalized_id = str(artifact_id or "").strip()
    normalized_project = str(project_id or "").strip()
    if normalized_kind not in {"skill", "scenario"} or not normalized_id:
        raise ValueError("kind and artifact_id must identify a skill or scenario")
    if not normalized_project:
        raise WorkspaceSourceMutationBlocked("maintenance project id is required")

    lock = _load_active_lock(root)
    component_key = f"{normalized_kind}:{normalized_id}"
    if lock is None or not any(item.key == component_key for item in lock.components):
        raise WorkspaceSourceMutationBlocked(
            f"maintenance push requires {component_key} in the active WorkspaceLock"
        )

    manifest = root / "projects" / normalized_project / "project.yaml"
    try:
        project: Any = yaml.safe_load(manifest.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkspaceSourceMutationBlocked(
            f"cannot verify maintenance Project ownership from {manifest}"
        ) from exc
    owned = (
        project.get("components", {}).get("owned", [])
        if isinstance(project, Mapping)
        and isinstance(project.get("components"), Mapping)
        else []
    )
    refs = {
        str(item.get("ref") or "").strip()
        for item in owned
        if isinstance(item, Mapping)
    }
    if component_key not in refs:
        raise WorkspaceSourceMutationBlocked(
            f"Project {normalized_project!r} does not own {component_key}; maintenance push denied"
        )


__all__ = [
    "WorkspaceSourceMutationBlocked",
    "assert_workspace_component_maintenance_owned",
    "assert_workspace_component_mutable",
    "load_active_workspace_lock",
]
