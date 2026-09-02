"""Governed Builder source-recovery operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.workspace import BuilderWorkspaceService

    return BuilderWorkspaceService.from_context()


def plan(
    *,
    kind: str,
    artifact_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Compare immutable release, Workspace, and DEV source without mutation."""

    return dict(
        _service().development_source_recovery_plan(
            kind=kind,
            artifact_id=artifact_id,
            project_id=project_id,
        )
        or {}
    )


def apply(
    *,
    kind: str,
    artifact_id: str,
    expected_plan_digest: str,
    decisions: Mapping[str, str] | None = None,
    project_id: str | None = None,
    actor: str = "builder.sdk",
) -> dict[str, Any]:
    """Apply explicit reviewed decisions to DEV and create a planned Change."""

    return dict(
        _service().apply_development_source_recovery(
            kind=kind,
            artifact_id=artifact_id,
            expected_plan_digest=expected_plan_digest,
            decisions=decisions,
            project_id=project_id,
            actor=actor,
        )
        or {}
    )


__all__ = ["apply", "plan"]
