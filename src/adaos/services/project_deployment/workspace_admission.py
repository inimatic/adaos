"""Local placement cannot silently replace the Workspace's immutable selection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from adaos.domain.artifact_release import WorkspaceLock
from adaos.domain.project_deployment import DeploymentPlanChange


def workspace_package_pins(workspace_root: Path) -> dict[str, str]:
    path = workspace_root / ".adaos" / "workspace.lock.json"
    try:
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
    except FileNotFoundError:
        return {}
    if len(raw.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("workspace_lock_read_budget_exceeded")
    lock = WorkspaceLock.from_mapping(json.loads(raw))
    return {package.key: package.digest for package in lock.components}


def workspace_admission_warnings(
    workspace_root: Path, changes: Iterable[DeploymentPlanChange], local_node_id: str
) -> tuple[str, ...]:
    local = [change for change in changes if change.node_id == local_node_id
             and change.action in {"install", "update", "noop"}]
    if not local:
        return ()
    try:
        pins = workspace_package_pins(workspace_root)
    except (OSError, ValueError, TypeError):
        return ("blocked:workspace_lock:unavailable",)
    return tuple(sorted({
        f"blocked:{change.component_ref}:workspace_lock_package_conflict"
        for change in local
        if change.component_ref in pins
        and pins[change.component_ref] != change.target_package_digest
    }))
