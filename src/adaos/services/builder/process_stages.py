"""Read-only stage list; inspection is not workflow command admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def process_stages(workflow: Mapping[str, Any], *, revision: str | None = None) -> list[dict[str, Any]]:
    prototype = workflow.get("prototype") or {}
    automation = workflow.get("automation") or {}
    delivery = workflow.get("delivery") or {}
    publication = workflow.get("publication") or {}
    change = workflow.get("change") or workflow.get("change_set") or {}
    head = str(prototype.get("head_revision") or "")
    selected = str(revision or head)
    is_head = selected == head
    acceptance = prototype.get("acceptance") or {}
    accepted = bool(selected and is_head and prototype.get("stable") and
                    (not prototype.get("acceptance_required") or
                     str(acceptance.get("revision") or "") == selected))
    own_automation = bool(selected and str(automation.get("source_prototype_revision") or "") == selected)
    if not own_automation:
        automation, delivery, publication = {}, {}, {}
    auto_status = str(automation.get("status") or "not_started")
    trial_status = str(delivery.get("status") or "idle")
    stable_status = str(publication.get("status") or "not_started")
    state = str((workflow.get("workflow_description") or workflow.get("governed") or {}).get("state") or "ready")
    if state.startswith("publication") or state == "published":
        active = "stable"
    elif state.startswith("trial"):
        active = "trial"
    elif state == "verification":
        active = "verification"
    elif state.startswith("automation") or workflow.get("active_phase") == "automation":
        active = "automation"
    elif state in {"ready", "clarification_needed", "change_planning"}:
        active = "change"
    else:
        active = "prototype"
    identifier = str(workflow.get("object_id") or "")
    task = str(automation.get("snapshot_task_id") or automation.get("head_task_id") or "current")
    candidate = str(delivery.get("candidate_id") or "")
    definitions = [
        ("change", f"change:{change.get('change_id') or change.get('change_set_id') or identifier}",
         "Change", str(change.get("status") or "ready"), True, ""),
        ("prototype", f"prototype:{identifier}:{selected or 'current'}", "Prototype",
         "accepted" if accepted else str(prototype.get("status") or "working") if is_head else "historical",
         True, ""),
        ("automation", f"automation:{identifier}:{task}", "Automation", auto_status,
         accepted or auto_status != "not_started", "Accept this Prototype revision first"),
        ("verification", f"verification:{identifier}:{task}", "Verification",
         "reviewing" if state == "verification" and is_head else "recorded" if auto_status == "completed" else "not_started",
         auto_status == "completed", "Complete Automation first"),
        ("trial", f"trial:{candidate or identifier}", "Beta", trial_status,
         bool(candidate and trial_status not in {"idle", "stale"}), "Prepare this revision's Trial first"),
        ("stable", f"publication:{identifier}:{publication.get('current_version') or 'current'}", "Stable", stable_status,
         trial_status in {"accepted", "published"} or stable_status not in {"not_started", "idle"}, "Accept this revision's Trial first"),
    ]
    rows = []
    for stage, ref, title, status, available, reason in definitions:
        rows.append({"id": ref, "ref": ref, "stage": stage, "title": title, "status": status,
                     "subtitle": status if available else reason, "disabled": not available,
                     "current": is_head and stage == active,
                     "icon": "person-outline" if stage in {"change", "verification", "trial", "stable"} else "construct-outline",
                     "revision": selected if stage == "prototype" else task,
                     "previewStage": "prototype" if stage == "prototype" else "automation",
                     "canPreview": bool(selected) if stage == "prototype" else stage == "automation" and auto_status == "completed",
                     "canOpenPlacement": stage == "trial" and available and trial_status != "published" or stage == "stable" and stable_status == "published",
                     "placementKind": "trial" if stage == "trial" else "stable"})
    return rows
