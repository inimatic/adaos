"""Deterministic safety rules for incremental semantic Prototype revisions."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .prototype_context import prototype_process_constraints
from .workflow import BuilderWorkflowError


class SemanticRevisionError(BuilderWorkflowError):
    """An incremental revision violated an independently verified invariant."""

    def __init__(self, findings: list[dict[str, Any]]) -> None:
        self.findings = copy.deepcopy(findings)
        detail = "; ".join(str(item.get("detail") or "") for item in findings)
        super().__init__(f"invalid semantic Prototype revision: {detail}")


def _source_preservation_refs(brief: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(brief, Mapping):
        return []
    return [
        str(item["id"])
        for item in prototype_process_constraints(brief)
        if item.get("verification_owner") == "source_preservation"
    ]


def _view_resource(
    command: Mapping[str, Any], views: Mapping[str, Mapping[str, Any]]
) -> str | None:
    view = views.get(str(command.get("view_ref") or ""))
    if view is None:
        return None
    return str(view.get("resource_ref") or "") or None


def reconcile_semantic_revision(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Preserve unrelated executable semantics in a source-preserving revision.

    The model may consolidate editors and change command exposure, but it may
    not silently remove an existing operation or move its identity to another
    resource. Missing operations and their editor views are restored from the
    digest-addressed input revision. Structural loss is rejected instead of
    guessed.
    """

    requirement_refs = _source_preservation_refs(brief)
    result = copy.deepcopy(dict(current))
    if not requirement_refs:
        return {
            "semantic_document": result,
            "normalizations": [],
            "requirement_refs": [],
        }
    if previous.get("schema") != "adaos.webui.semantic.v2" or result.get(
        "schema"
    ) != "adaos.webui.semantic.v2":
        raise SemanticRevisionError(
            [
                {
                    "code": "semantic.revision_source_invalid",
                    "path": "$",
                    "requirement_refs": requirement_refs,
                    "detail": "source preservation requires semantic v2 revisions",
                }
            ]
        )

    findings: list[dict[str, Any]] = []
    old_resources = {
        str(item.get("id") or ""): item for item in previous.get("resources") or []
    }
    new_resources = {
        str(item.get("id") or ""): item for item in result.get("resources") or []
    }
    old_views = {
        str(item.get("id") or ""): item for item in previous.get("views") or []
    }
    new_views = {
        str(item.get("id") or ""): item for item in result.get("views") or []
    }

    for resource_id, old_resource in old_resources.items():
        new_resource = new_resources.get(resource_id)
        if new_resource is None:
            findings.append(
                {
                    "code": "semantic.preserved_resource_missing",
                    "path": "$.resources",
                    "semantic_refs": [f"resource:{resource_id}"],
                    "requirement_refs": requirement_refs,
                    "detail": f"preserved resource {resource_id!r} was removed",
                }
            )
            continue
        old_fields = {
            str(item.get("id") or "") for item in old_resource.get("fields") or []
        }
        new_fields = {
            str(item.get("id") or "") for item in new_resource.get("fields") or []
        }
        missing_fields = sorted(old_fields - new_fields)
        if missing_fields:
            findings.append(
                {
                    "code": "semantic.preserved_fields_missing",
                    "path": "$.resources",
                    "semantic_refs": [
                        f"field:{resource_id}.{field_id}" for field_id in missing_fields
                    ],
                    "requirement_refs": requirement_refs,
                    "detail": (
                        f"preserved resource {resource_id!r} lost fields "
                        f"{missing_fields}"
                    ),
                }
            )
        had_collection = any(
            view.get("resource_ref") == resource_id
            and view.get("role") == "collection"
            for view in old_views.values()
        )
        has_collection = any(
            view.get("resource_ref") == resource_id
            and view.get("role") == "collection"
            for view in new_views.values()
        )
        if had_collection and not has_collection:
            findings.append(
                {
                    "code": "semantic.preserved_collection_missing",
                    "path": "$.views",
                    "semantic_refs": [f"resource:{resource_id}"],
                    "requirement_refs": requirement_refs,
                    "detail": (
                        f"preserved resource {resource_id!r} lost its collection view"
                    ),
                }
            )

    old_relationships = {
        str(item.get("id") or ""): item
        for item in previous.get("relationships") or []
    }
    new_relationships = {
        str(item.get("id") or ""): item
        for item in result.get("relationships") or []
    }
    endpoint_keys = (
        "from_resource_ref",
        "from_field_ref",
        "to_resource_ref",
        "to_field_ref",
    )
    for relationship_id, old_relationship in old_relationships.items():
        new_relationship = new_relationships.get(relationship_id)
        if new_relationship is None or any(
            new_relationship.get(key) != old_relationship.get(key)
            for key in endpoint_keys
        ):
            findings.append(
                {
                    "code": "semantic.preserved_relationship_changed",
                    "path": "$.relationships",
                    "semantic_refs": [f"relationship:{relationship_id}"],
                    "requirement_refs": requirement_refs,
                    "detail": (
                        f"preserved relationship {relationship_id!r} was removed or retargeted"
                    ),
                }
            )

    if findings:
        raise SemanticRevisionError(findings)

    commands = list(result.get("commands") or [])
    command_positions = {
        str(item.get("id") or ""): index for index, item in enumerate(commands)
    }
    normalizations: list[dict[str, Any]] = []

    def restore_view(view_id: str, command_id: str) -> None:
        if view_id in new_views:
            return
        source = old_views.get(view_id)
        if source is None:
            raise SemanticRevisionError(
                [
                    {
                        "code": "semantic.preserved_command_view_missing",
                        "path": "$.views",
                        "semantic_refs": [f"command:{command_id}"],
                        "requirement_refs": requirement_refs,
                        "detail": (
                            f"preserved command {command_id!r} has no recoverable editor view"
                        ),
                    }
                ]
            )
        restored = copy.deepcopy(dict(source))
        result.setdefault("views", []).append(restored)
        new_views[view_id] = restored
        normalizations.append(
            {
                "kind": "semantic_preserved_view_restored",
                "id": view_id,
                "for_command": command_id,
            }
        )

    for old_command in previous.get("commands") or []:
        command_id = str(old_command.get("id") or "")
        current_index = command_positions.get(command_id)
        restore = current_index is None
        reason = "missing"
        if current_index is not None:
            current_command = commands[current_index]
            old_resource = _view_resource(old_command, old_views)
            current_resource = _view_resource(current_command, new_views)
            if (
                current_command.get("kind") != old_command.get("kind")
                or current_resource != old_resource
            ):
                restore = True
                reason = "retargeted"
        if not restore:
            continue
        restored = copy.deepcopy(dict(old_command))
        restore_view(str(restored.get("view_ref") or ""), command_id)
        if current_index is None:
            command_positions[command_id] = len(commands)
            commands.append(restored)
        else:
            commands[current_index] = restored
        normalizations.append(
            {
                "kind": "semantic_preserved_command_restored",
                "id": command_id,
                "reason": reason,
            }
        )
    result["commands"] = commands
    return {
        "semantic_document": result,
        "normalizations": normalizations,
        "requirement_refs": requirement_refs,
    }


__all__ = ["SemanticRevisionError", "reconcile_semantic_revision"]
