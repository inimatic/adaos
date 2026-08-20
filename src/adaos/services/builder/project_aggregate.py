from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from adaos.services.builder.placement import (
    active_project_placement,
    normalize_project_placements,
)


BUILDER_PROJECT_SCHEMA = "adaos.builder.project.v1"
BUILDER_PROJECT_VERSION = "1.1.0"
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
    "reviews",
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


def _issue_refs(change: Mapping[str, Any]) -> list[str]:
    return _refs(
        [
            f"issue:{str(item.get('issue_id') or '').strip()}"
            for item in change.get("issues") or []
            if isinstance(item, Mapping) and str(item.get("issue_id") or "").strip()
        ]
    )


def _change_dependencies(change: Mapping[str, Any]) -> list[str]:
    values = change.get("depends_on_change_ids") or change.get("dependencies") or []
    if isinstance(values, str):
        values = [values]
    return _refs(values)


def _change_summary(
    change: Mapping[str, Any],
    governed: Mapping[str, Any],
    *,
    project_ref: str,
    previous: Mapping[str, Any] | None = None,
    initial_base_generation: int = 0,
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
        # A newly planned Change is based on the Project artifact generation
        # visible at planning time.  Persisted summaries retain their original
        # base so genuinely concurrent Changes still become stale when another
        # mutation advances the artifact generation.
        "base_generation": max(
            0,
            int(
                (previous or {}).get("base_generation")
                if previous is not None
                else initial_base_generation
            ),
        ),
        "affected_refs": affected_refs(change, project_ref),
        "issue_refs": _issue_refs(change),
        "depends_on_change_ids": _change_dependencies(change),
        "workflow_instance_ref": str(governed.get("instance_id") or f"change:{project_ref}:{change_id}"),
        "workflow_state": str(governed.get("state") or "") or None,
        "mutation_status": str((previous or {}).get("mutation_status") or "idle"),
        "updated_at": str(change.get("updated_at") or now or _now()),
    }


def _dependency_footprint(
    refs: Sequence[Any],
    dependencies: Sequence[Mapping[str, Any]],
) -> set[str]:
    footprint = set(_refs(refs))
    adjacency: dict[str, set[str]] = {}
    for edge in dependencies:
        if str(edge.get("kind") or "") not in {"requires", "derives"}:
            continue
        source = str(edge.get("from_ref") or "").strip()
        target = str(edge.get("to_ref") or "").strip()
        if source and target:
            adjacency.setdefault(source, set()).add(target)
    pending = list(footprint)
    while pending:
        current = pending.pop()
        for target in adjacency.get(current, ()):
            if target not in footprint:
                footprint.add(target)
                pending.append(target)
    return footprint


def _conflicts(
    changes: Sequence[Mapping[str, Any]],
    dependencies: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    open_changes = [
        dict(item)
        for item in changes
        if str(item.get("status") or "") not in _TERMINAL_CHANGE_STATES
    ]
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(open_changes):
        for right in open_changes[index + 1 :]:
            left_direct = set(left.get("affected_refs") or [])
            right_direct = set(right.get("affected_refs") or [])
            overlap = sorted(left_direct & right_direct)
            if overlap:
                conflicts.append(
                    {
                        "left_change_id": str(left["change_id"]),
                        "right_change_id": str(right["change_id"]),
                        "affected_refs": overlap,
                        "kind": "direct",
                    }
                )
                continue
            indirect = sorted(
                _dependency_footprint(list(left_direct), dependencies)
                & _dependency_footprint(list(right_direct), dependencies)
            )
            if indirect:
                conflicts.append(
                    {
                        "left_change_id": str(left["change_id"]),
                        "right_change_id": str(right["change_id"]),
                        "affected_refs": indirect,
                        "kind": "component_dependency",
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
    title: str | None = None,
    description: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    project_ref = f"{object_type}:{object_id}"
    changes = [dict(item) for item in raw.get("changes") or [] if isinstance(item, Mapping)]
    for item in changes:
        item.setdefault("issue_refs", [])
        item.setdefault("depends_on_change_ids", [])
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
            initial_base_generation=max(0, int(raw.get("artifact_generation") or 0)),
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
    dependencies = [
        copy.deepcopy(dict(item)) for item in raw.get("dependencies") or [] if isinstance(item, Mapping)
    ][:1000]
    timestamp = str(raw.get("updated_at") or now or _now())
    prototype = dict(workflow.get("prototype") or {})
    automation = dict(workflow.get("automation") or {})
    governed = dict(workflow.get("governed") or {})
    prototype_revision = str(prototype.get("head_revision") or "").strip()
    accepted_prototype_ref = (
        {
            "kind": "prototype",
            "id": project_ref,
            "revision": prototype_revision or "current",
        }
        if bool(prototype.get("stable"))
        else None
    )
    implementation_id = str(
        automation.get("snapshot_task_id")
        or automation.get("head_task_id")
        or automation.get("result_version")
        or ""
    ).strip()
    accepted_implementation_ref = (
        {
            "kind": "implementation",
            "id": implementation_id or project_ref,
            "version": str(automation.get("result_version") or "").strip() or None,
            "source_prototype_revision": str(
                automation.get("source_prototype_revision") or ""
            ).strip()
            or None,
        }
        if str(automation.get("status") or "") == "completed"
        else None
    )
    candidate_ref = (
        copy.deepcopy(raw.get("candidate_ref"))
        if isinstance(raw.get("candidate_ref"), Mapping)
        else {
            "kind": "candidate",
            "id": str(delivery.get("candidate_id")),
            "digest": delivery.get("package_digest") or delivery.get("release_digest"),
        }
        if str(delivery.get("candidate_id") or "").strip()
        else None
    )
    issue_refs = _refs(
        issue_ref
        for item in normalized_changes
        for issue_ref in item.get("issue_refs") or []
    )
    change_edges = [
        copy.deepcopy(dict(item))
        for item in raw.get("change_edges") or []
        if isinstance(item, Mapping)
        and str(item.get("relation") or "")
        in {"contains_issue", "alternative", "supersedes", "depends", "blocks", "related"}
    ]
    for item in normalized_changes:
        change_ref = str(item.get("change_ref") or f"change:{item.get('change_id')}")
        for issue_ref in item.get("issue_refs") or []:
            change_edges.append(
                {"from_ref": change_ref, "to_ref": str(issue_ref), "relation": "contains_issue"}
            )
        for dependency in item.get("depends_on_change_ids") or []:
            change_edges.append(
                {
                    "from_ref": change_ref,
                    "to_ref": f"change:{str(dependency).removeprefix('change:')}",
                    "relation": "depends",
                }
            )
    if isinstance(current, Mapping):
        current_ref = f"change:{str(current.get('change_id') or current.get('change_set_id') or '')}"
        supersedes = str(
            current.get("supersedes_change_id")
            or current.get("supersedes_change_set_id")
            or ""
        ).strip()
        if supersedes:
            change_edges.append(
                {
                    "from_ref": current_ref,
                    "to_ref": f"change:{supersedes.removeprefix('change:')}",
                    "relation": "supersedes",
                }
            )
    change_edges = list(
        {
            (item["from_ref"], item["to_ref"], item["relation"]): item
            for item in change_edges
            if str(item.get("from_ref") or "").strip()
            and str(item.get("to_ref") or "").strip()
        }.values()
    )[-2000:]
    trials = [
        copy.deepcopy(dict(item))
        for item in raw.get("trials") or []
        if isinstance(item, Mapping)
    ]
    delivery_status = str(delivery.get("status") or "idle")
    if candidate_ref and delivery_status in {
        "checkpoint",
        "trial",
        "accepted",
        "rejected",
        "stale",
        "published",
    }:
        normalized_trial_status = "ready" if delivery_status == "checkpoint" else delivery_status
        current_trial = {
            "trial_ref": f"trial:{candidate_ref['id']}",
            "candidate_ref": copy.deepcopy(candidate_ref),
            "status": normalized_trial_status,
            "workspace_ref": str(delivery.get("trial_workspace") or "").strip() or None,
            "started_at": delivery.get("prepared_at"),
            "decided_at": delivery.get("decided_at"),
        }
        trials = [
            item for item in trials if item.get("trial_ref") != current_trial["trial_ref"]
        ]
        trials.append(current_trial)
    placements = normalize_project_placements(raw.get("placements"), project_ref=project_ref)
    stable_placement = active_project_placement(placements, kind="stable")
    trial_placement = active_project_placement(placements, kind="trial")
    conflicts = _conflicts(normalized_changes, dependencies)
    raw_lifecycle = dict(raw.get("lifecycle") or {})
    was_archived = str(raw_lifecycle.get("status") or "active") == "archived"
    lifecycle = {
        "status": "archived" if archived else "active",
        "archived_at": (
            raw_lifecycle.get("archived_at")
            or (timestamp if archived and not was_archived else None)
        ),
        "restored_at": (
            timestamp if not archived and was_archived else raw_lifecycle.get("restored_at")
        ),
        "reason": raw_lifecycle.get("reason"),
    }
    if archived:
        explanation_status = "archived"
        blockers = ["project_archived"]
        next_commands = ["builder.project.restore"]
    elif conflicts:
        explanation_status = "conflicted"
        blockers = ["change_conflicts_require_resolution"]
        next_commands = ["builder.change.focus", "builder.change.rebase"]
    elif delivery_status in {"checkpoint", "trial", "accepted"}:
        explanation_status = "trial"
        blockers = []
        next_commands = ["builder.trial.inspect", "builder.trial.decide"]
    elif str(publication.get("status") or "") == "published":
        explanation_status = "published"
        blockers = []
        next_commands = ["builder.change.plan"]
    elif any(str(item.get("status") or "") not in _TERMINAL_CHANGE_STATES for item in normalized_changes):
        explanation_status = "active"
        blockers = []
        next_commands = ["builder.process.inspect", "builder.change.focus"]
    else:
        explanation_status = "ready"
        blockers = []
        next_commands = ["builder.change.plan"]
    return {
        "schema": BUILDER_PROJECT_SCHEMA,
        "project_id": object_id,
        "project_ref": project_ref,
        "object_type": object_type,
        "identity": {
            "stable_id": object_id,
            "kind": object_type,
            "project_ref": project_ref,
            "title": str(title or raw.get("title") or object_id),
            "description": str(description or raw.get("description") or "").strip() or None,
        },
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
        "installed_release_ref": (
            copy.deepcopy(raw.get("installed_release_ref"))
            if isinstance(raw.get("installed_release_ref"), Mapping)
            else {
                "kind": "release",
                "id": project_ref,
                "version": str(publication.get("current_version")),
                "workspace_lock_digest": str(
                    dict(publication.get("release_record") or {}).get("workspace_lock_digest")
                    or ""
                )
                or None,
            }
            if str(publication.get("status") or "") == "published"
            and isinstance(publication.get("release_record"), Mapping)
            and str(publication.get("current_version") or "").strip()
            else None
        ),
        "dev_ref": (
            copy.deepcopy(raw.get("dev_ref"))
            if isinstance(raw.get("dev_ref"), Mapping)
            else {
                "kind": "dev_artifact",
                "id": project_ref,
                "generation": int(workflow.get("generation") or 0),
            }
        ),
        "candidate_ref": candidate_ref,
        "accepted_prototype_ref": accepted_prototype_ref,
        "accepted_implementation_ref": accepted_implementation_ref,
        "issue_refs": issue_refs,
        "policy": {
            "parallel_changes": True,
            "unknown_conflict_scope": "project",
            "prototype_data_modes": ["mock", "fixture"],
            "risk_policy": {"default_class": "isolated_write", "fail_closed": True},
            "approval_policy_refs": [],
            "allowed_executors": ["builder.llm", "builder.codex", "builder.deterministic"],
            **(dict(raw.get("policy")) if isinstance(raw.get("policy"), Mapping) else {}),
        },
        "component_refs": _refs(raw.get("component_refs") or [project_ref]),
        "changes": normalized_changes,
        "conflicts": conflicts,
        "dependencies": dependencies,
        "change_edges": change_edges,
        "active_candidate_refs": (
            [copy.deepcopy(candidate_ref)]
            if candidate_ref and delivery_status not in {"rejected", "stale", "published"}
            else []
        ),
        "trials": trials[-100:],
        "placements": placements,
        "stable_placement_ref": stable_placement["placement_id"] if stable_placement else None,
        "trial_placement_ref": trial_placement["placement_id"] if trial_placement else None,
        "focus_by_context": focus,
        "workflow_versions": {"project": BUILDER_PROJECT_VERSION, "change": "1.0.0"},
        "workflow_definition_version": str(governed.get("definition_version") or "1.0.0"),
        "archived": bool(archived),
        "lifecycle": lifecycle,
        "explanation": {
            "status": explanation_status,
            "summary": (
                f"Project {project_ref} is {explanation_status}; "
                f"{len(normalized_changes)} change(s), {len(conflicts)} conflict(s), "
                f"{len(trials)} trial(s)."
            ),
            "blockers": blockers,
            "next_commands": next_commands,
        },
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


def set_dependencies(
    project: Mapping[str, Any],
    dependencies: Sequence[Mapping[str, Any]],
    *,
    expected_project_generation: int,
    now: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(project))
    if int(result.get("generation") or 0) != int(expected_project_generation):
        raise BuilderProjectError("stale project generation")
    normalized: list[dict[str, str]] = []
    for edge in dependencies:
        source = str(edge.get("from_ref") or "").strip()
        target = str(edge.get("to_ref") or "").strip()
        kind = str(edge.get("kind") or "").strip()
        if not source or not target or kind not in {"requires", "conflicts", "derives", "promotes"}:
            raise BuilderProjectError("project dependency requires valid from_ref, to_ref, and kind")
        normalized.append({"from_ref": source, "to_ref": target, "kind": kind})
    result["dependencies"] = normalized[:1000]
    result["conflicts"] = _conflicts(result.get("changes") or [], result["dependencies"])
    result["generation"] = int(result.get("generation") or 0) + 1
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
    result["conflicts"] = _conflicts(changes, result.get("dependencies") or [])
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
    result["conflicts"] = _conflicts(changes, result.get("dependencies") or [])
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
