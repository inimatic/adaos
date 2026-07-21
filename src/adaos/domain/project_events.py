from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


BUILDER_CONTEXT_SELECTED = "builder.context.selected"
BUILDER_PREVIEW_DESIRED = "builder.preview.desired"
BUILDER_PREVIEW_OBSERVED = "builder.preview.observed"
PROJECT_CONTENT_CHANGED = "project.content.changed"

ProjectKind = Literal["scenario", "skill"]

_LEGACY_SELECTION_REASONS = {
    "builder_project_created",
    "builder_project_switched",
    "project_loaded",
    "project_selected",
}


def normalize_project_kind(value: Any) -> ProjectKind | None:
    token = str(value or "").strip().lower().rstrip("s")
    if token in {"scenario", "skill"}:
        return token  # type: ignore[return-value]
    return None


def legacy_project_event_topic(reason: Any) -> str:
    """Map the overloaded legacy event to one canonical semantic topic."""

    token = str(reason or "").strip().lower()
    return BUILDER_CONTEXT_SELECTED if token in _LEGACY_SELECTION_REASONS else PROJECT_CONTENT_CHANGED


@dataclass(frozen=True, slots=True)
class ProjectEventIdentity:
    kind: ProjectKind
    project_id: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "ProjectEventIdentity | None":
        source = payload if isinstance(payload, Mapping) else {}
        kind = normalize_project_kind(source.get("project_kind") or source.get("object_type"))
        project_id = str(source.get("project_id") or source.get("object_id") or "").strip()
        if kind is None or not project_id:
            return None
        return cls(kind=kind, project_id=project_id)

    def payload(self, **extra: Any) -> dict[str, Any]:
        return {
            **extra,
            "project_kind": self.kind,
            "project_id": self.project_id,
            "object_type": self.kind,
            "object_id": self.project_id,
        }


__all__ = [
    "BUILDER_CONTEXT_SELECTED",
    "BUILDER_PREVIEW_DESIRED",
    "BUILDER_PREVIEW_OBSERVED",
    "PROJECT_CONTENT_CHANGED",
    "ProjectEventIdentity",
    "legacy_project_event_topic",
    "normalize_project_kind",
]
