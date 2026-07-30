from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


BUILDER_PROJECT_SCHEMA = "adaos.builder.project.v1"
BUILDER_PROJECT_VERSION = "1.0.0"
_TERMINAL_CHANGE_STATES = {"published", "rejected", "superseded", "cancelled"}
_PORTFOLIO_FIELDS = (
    "active_phase",
    "prototype",
    "automation",
    "delivery",
    "publication",
    "change",
    "change_set",
    "context_packet",
    "governed",
    "pending_transition",
)


class BuilderProjectError(ValueError):
    """Raised when Project coordination would make Change state ambiguous."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _refs(values: Sequence[Any] | None, *, limit: int = 500) -> list[str]:
    return list(
        dict.fromkeys(str(item).strip() for item in values or [] if str(item).strip())
    )[:limit]


def capture_compatibility_record(workflow: Mapping[str, Any]) -> dict[str, Any] | None:
    change = workflow.get("change") or workflow.get("change_set")
    if not isinstance(change, Mapping):
        return None
    change_id = str(change.get("change_id") or change.get("change_set_id") or "").strip()
    if not change_id:
        return None
    return {
        "change_id": change_id,
        **{field: copy.deepcopy(workflow.get(field)) for field in _PORTFOLIO_FIELDS},
    }


def restore_compatibility_record(workflow: dict[str, Any], record: Mapping[str, Any]) -> None:
    for field in _PORTFOLIO_FIELDS:
        workflow[field] = copy.deepcopy(record.get(field))


def normalize_portfolio(value: Any, workflow: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(item, Mapping):
                continue
            change_id = str(item.get("change_id") or key or "").strip()
            if change_id:
                result[change_id] = copy.deepcopy(dict(item))
    current = capture_compatibility_record(workflow)
    if current:
        result[current["change_id"]] = current
    return dict(list(result.items())[-100:])


def affected_refs(change: Mapping[str, Any], project_ref: str) -> list[str]:
    explicit = _refs(change.get("affected_refs") or [])
    if explicit:
        return explicit
    inferred: list[str] = []
    for issue in change.get("issues") or []:
        if isinstance(issue, Mapping):
            inferred.extend(_refs(issue.get("semantic_refs") or []))
    # Missing scope must fail safe: it conflicts at the whole-project boundary.
    return _refs(inferred) or [project_ref]


def _change_summary(
    change: Mapping[str, Any],
    governed: Mapping[str, Any],
    *,
    project_ref: str,
    previous: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    change_id = str(change.get("change_id") or change.get("change_set_id") or "").strip()
    if not change_id:
        raise BuilderProjectError("project Change requires change_id")
    return {
        "change_id": change_id,
        "change_ref": f"change:{change_id}",
        "status": str(change.get("status") or "planned"),
        "gate": str(change.get("gate") or "prototype"),
        "base_generation": max(0, int((previous or {}).get("base_generation") or change.get("base_generation") or 0)),
        "affected_refs": affected_refs(change, project_ref),
        "workflow_instance_ref": str(governed.get("instance_id") or f"change:{project_ref}:{change_id}"),
        "workflow_state": str(governed.get("state") or "") or None,
        "mutation_status": str((previous or {}).get("mutation_status") or "idle"),
        "updated_at": str(change.get("updated_at") or now or _now()),
    }


def _conflicts(changes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    open_changes = [
        dict(item)
        for item in changes
        if str(item.get("status") or "") not in _TERMINAL_CHANGE_STATES
    ]
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(open_changes):
        for right in open_changes[index + 1 :]:
            overlap = sorted(set(left.get("affected_refs") or []) & set(right.get("affected_refs") or []))
            if overlap:
                conflicts.append(
                    {
                        "left_change_id": str(left["change_id"]),
                        "right_change_id": str(right["change_id"]),
                        "affected_refs": overlap,
                        "kind": "direct",
                    }
                )
    return conflicts


def normalize_project(
    value: Any,
    *,
    object_type: str,
    object_id: str,
    archived: bool,
    workflow: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    project_ref = f"{object_type}:{object_id}"
    changes = [dict(item) for item in raw.get("changes") or [] if isinstance(item, Mapping)]
    by_id = {str(item.get("change_id") or ""): item for item in changes if str(item.get("change_id") or "")}
    current = workflow.get("change") or workflow.get("change_set")
    governed = workflow.get("governed")
    delivery = dict(workflow.get("delivery") or {})
    publication = dict(workflow.get("publication") or {})
    if isinstance(current, Mapping) and isinstance(governed, Mapping):
        summary = _change_summary(
            current,
            governed,
            project_ref=project_ref,
            previous=by_id.get(str(current.get("change_id") or current.get("change_set_id") or "")),
            now=now,
        )
        by_id[summary["change_id"]] = summary
    focus = {
        str(key).strip(): str(item).strip()
        for key, item in dict(raw.get("focus_by_context") or {}).items()
        if str(key).strip() and str(item).strip() in by_id
    }
    current_id = str((current or {}).get("change_id") or (current or {}).get("change_set_id") or "") if isinstance(current, Mapping) else ""
    if current_id:
        focus.setdefault("default", current_id)
    normalized_changes = list(by_id.values())[-100:]
    timestamp = str(raw.get("updated_at") or now or _now())
    return {
        "schema": BUILDER_PROJECT_SCHEMA,
        "project_id": object_id,
        "project_ref": project_ref,
        "object_type": object_type,
        "source_ref": (
            copy.deepcopy(raw.get("source_ref"))
            if isinstance(raw.get("source_ref"), Mapping)
            else copy.deepcopy(current.get("base_ref"))
            if isinstance(current, Mapping) and isinstance(current.get("base_ref"), Mapping)
            else None
        ),
        "stable_release_ref": (
            copy.deepcopy(raw.get("stable_release_ref"))
            if isinstance(raw.get("stable_release_ref"), Mapping)
            else {
                "kind": "release",
                "id": project_ref,
                "version": str(publication.get("current_version")),
            }
            if str(publication.get("status") or "") == "published"
            and str(publication.get("current_version") or "").strip()
            else None
        ),
        "installed_release_ref": copy.deepcopy(raw.get("installed_release_ref")) if isinstance(raw.get("installed_release_ref"), Mapping) else None,
        "dev_ref": (
            copy.deepcopy(raw.get("dev_ref"))
            if isinstance(raw.get("dev_ref"), Mapping)
            else {
                "kind": "dev_artifact",
                "id": project_ref,
                "generation": int(workflow.get("generation") or 0),
            }
        ),
        "candidate_ref": (
            copy.deepcopy(raw.get("candidate_ref"))
            if isinstance(raw.get("candidate_ref"), Mapping)
            else {
                "kind": "candidate",
                "id": str(delivery.get("candidate_id")),
                "digest": delivery.get("package_digest") or delivery.get("release_digest"),
            }
            if str(delivery.get("candidate_id") or "").strip()
            else None
        ),
        "policy": {
            "parallel_changes": True,
            "unknown_conflict_scope": "project",
            "prototype_data_modes": ["mock", "fixture"],
            **(dict(raw.get("policy")) if isinstance(raw.get("policy"), Mapping) else {}),
        },
        "component_refs": _refs(raw.get("component_refs") or [project_ref]),
        "changes": normalized_changes,
        "conflicts": _conflicts(normalized_changes),
        "dependencies": [
            copy.deepcopy(dict(item)) for item in raw.get("dependencies") or [] if isinstance(item, Mapping)
        ][:1000],
        "focus_by_context": focus,
        "workflow_versions": {"project": BUILDER_PROJECT_VERSION, "change": "1.0.0"},
        "archived": bool(archived),
        "generation": max(0, int(raw.get("generation") or 0)),
        "artifact_generation": max(0, int(raw.get("artifact_generation") or 0)),
        "view_generation": max(0, int(raw.get("view_generation") or 0)),
        "updated_at": timestamp,
    }


def set_focus(project: Mapping[str, Any], context_ref: str, change_id: str, *, now: str | None = None) -> dict[str, Any]:
    result = copy.deepcopy(dict(project))
    known = {str(item.get("change_id") or "") for item in result.get("changes") or []}
    if change_id not in known:
        raise BuilderProjectError(f"unknown project Change: {change_id}")
    context = str(context_ref or "default").strip() or "default"
    result.setdefault("focus_by_context", {})[context] = change_id
    result["view_generation"] = int(result.get("view_generation") or 0) + 1
    result["updated_at"] = now or _now()
    return result


def begin_mutation(
    project: Mapping[str, Any],
    change_id: str,
    *,
    expected_project_generation: int,
    expected_base_generation: int,
    now: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(project))
    if int(result.get("generation") or 0) != int(expected_project_generation):
        raise BuilderProjectError("stale project generation")
    changes = [dict(item) for item in result.get("changes") or []]
    target = next((item for item in changes if item.get("change_id") == change_id), None)
    if target is None:
        raise BuilderProjectError(f"unknown project Change: {change_id}")
    if int(target.get("base_generation") or 0) != int(expected_base_generation):
        raise BuilderProjectError("stale Change base generation; rebase, split, or supersede is required")
    if int(target.get("base_generation") or 0) != int(result.get("artifact_generation") or 0):
        raise BuilderProjectError("Change base is behind the project artifact generation; explicit rebase is required")
    target_refs = set(target.get("affected_refs") or [])
    for other in changes:
        if other.get("change_id") == change_id or other.get("mutation_status") != "active":
            continue
        overlap = sorted(target_refs & set(other.get("affected_refs") or []))
        if overlap:
            raise BuilderProjectError(
                f"Change conflicts with active mutation {other.get('change_id')}: {overlap[0]}"
            )
    target["mutation_status"] = "active"
    target["updated_at"] = now or _now()
    result["changes"] = changes
    result["generation"] = int(result.get("generation") or 0) + 1
    result["updated_at"] = now or _now()
    result["conflicts"] = _conflicts(changes)
    return result


def finish_mutation(
    project: Mapping[str, Any],
    change_id: str,
    *,
    outcome_unknown: bool = False,
    advance_base: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(project))
    changes = [dict(item) for item in result.get("changes") or []]
    target = next((item for item in changes if item.get("change_id") == change_id), None)
    if target is None:
        raise BuilderProjectError(f"unknown project Change: {change_id}")
    target["mutation_status"] = "outcome_unknown" if outcome_unknown else "idle"
    if advance_base and not outcome_unknown:
        result["artifact_generation"] = int(result.get("artifact_generation") or 0) + 1
        target["base_generation"] = result["artifact_generation"]
    target["updated_at"] = now or _now()
    result["changes"] = changes
    result["generation"] = int(result.get("generation") or 0) + 1
    result["updated_at"] = now or _now()
    result["conflicts"] = _conflicts(changes)
    return result


def rebase_change(
    project: Mapping[str, Any],
    change_id: str,
    *,
    expected_project_generation: int,
    verified_unchanged_refs: Sequence[str],
    now: str | None = None,
) -> dict[str, Any]:
    """Move a planned Change to the latest base after deterministic scope verification."""

    result = copy.deepcopy(dict(project))
    if int(result.get("generation") or 0) != int(expected_project_generation):
        raise BuilderProjectError("stale project generation")
    changes = [dict(item) for item in result.get("changes") or []]
    target = next((item for item in changes if item.get("change_id") == change_id), None)
    if target is None:
        raise BuilderProjectError(f"unknown project Change: {change_id}")
    required = set(target.get("affected_refs") or [])
    verified = set(_refs(verified_unchanged_refs))
    if not required or not required <= verified:
        raise BuilderProjectError("rebase requires deterministic unchanged evidence for every affected ref")
    if target.get("mutation_status") != "idle":
        raise BuilderProjectError("an active or unknown mutation cannot be rebased")
    target["base_generation"] = int(result.get("artifact_generation") or 0)
    target["updated_at"] = now or _now()
    result["changes"] = changes
    result["generation"] = int(result.get("generation") or 0) + 1
    result["updated_at"] = now or _now()
    return result
