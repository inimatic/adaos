"""SDK operations for durable Builder artifact checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


def _service():
    from adaos.services.builder.workspace import BuilderWorkspaceService

    return BuilderWorkspaceService.from_context()


def checkpoint(
    *,
    kind: str,
    artifact_id: str,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    service = _service()
    return dict(
        service.checkpoint_artifact(
            kind=kind,
            artifact_id=artifact_id,
            message=message,
            metadata=metadata,
        )
        or {}
    )


def create_draft(
    *,
    kind: str,
    artifact_id: str,
    source_idea: str,
    template_id: str | None = None,
    webspace_id: str | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return dict(
        _service().create_draft(
            kind=kind,
            artifact_id=artifact_id,
            source_idea=source_idea,
            template_id=template_id,
            webspace_id=webspace_id,
            source=source,
        )
        or {}
    )


def get_draft(draft_id: str) -> dict[str, Any]:
    """Read one bounded Builder draft descriptor from runtime state."""

    token = str(draft_id or "").strip()
    if not token or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in token):
        raise ValueError("draft_id contains unsupported characters")
    from adaos.services.runtime_paths import current_state_dir

    path = current_state_dir() / "builder" / "drafts" / token / "builder.draft.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = ["checkpoint", "create_draft", "get_draft"]
