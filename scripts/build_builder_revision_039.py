from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
WEBUI = SCENARIO / "webui.json"
REVISION = "039"


def widget(page: dict, widget_id: str) -> dict:
    return next(item for item in page["widgets"] if item.get("id") == widget_id)


def main() -> None:
    before = json.loads(WEBUI.read_text(encoding="utf-8-sig"))
    after = copy.deepcopy(before)
    page = after["ui"]["application"]["desktop"]["pageSchema"]
    state = page["initialState"]
    state.update(
        {
            "workflowActivePhase": "prototype",
            "workflowWorkingLabel": "WORKING: Prototype",
            "previewViewingLabel": "Preview: not selected",
            "previewViewingReadOnly": False,
            "canEditPrototype": True,
            "canEditAutomation": False,
            "canReturnToPrototype": False,
            "canPublish": False,
        }
    )

    header = widget(page, "project-header")
    header["inputs"]["buttons"] = [
        {"id": "project-label", "label": "$state.selectedProjectTitle"},
        {"id": "workflow-label", "label": "$state.workflowWorkingLabel", "icon": "git-branch-outline"},
        {"id": "preview-label", "label": "$state.previewViewingLabel", "icon": "eye-outline"},
    ]

    workflow_status = {
        "area": "center",
        "id": "workflow-status",
        "type": "item.details",
        "title": "Workflow and Preview",
        "dataSource": {
            "kind": "skill",
            "name": "builder_sdk_control_skill.get_project",
            "params": {
                "object_type": "$state.selectedProjectKind",
                "object_id": "$state.selectedProjectId",
            },
            "cacheTtlMs": 0,
            "invalidationTags": [
                "builder.project.metadata",
                "builder.project.lifecycle",
                "builder.project.preview",
                "builder.project.automation",
                "builder.project.publication",
            ],
            "preserveLastValue": True,
        },
        "inputs": {
            "fields": [
                {"key": "working_label", "label": "Editable process"},
                {"key": "viewing_label", "label": "Preview"},
            ],
            "stateBindings": {
                "workflowActivePhase": "workflow_active_phase",
                "workflowWorkingLabel": "working_label",
                "previewViewingLabel": "viewing_label",
                "previewViewingReadOnly": "viewing_read_only",
                "canEditPrototype": "can_edit_prototype",
                "canEditAutomation": "can_edit_automation",
                "canReturnToPrototype": "can_return_to_prototype",
                "canPublish": "can_publish",
            },
        },
    }
    page["widgets"] = [item for item in page["widgets"] if item.get("id") != "workflow-status"]
    header_index = next(index for index, item in enumerate(page["widgets"]) if item.get("id") == "project-header")
    page["widgets"].insert(header_index + 1, workflow_status)

    prototype_chat = widget(page, "builder-chat")
    prototype_chat["visibleIf"] = (
        "$state.activeView === 'conversation' && $state.selectedLifecycleStage === 'prototype' "
        "&& $state.workflowActivePhase === 'prototype'"
    )
    readonly_notice = {
        "area": "center",
        "id": "prototype-frozen-notice",
        "type": "ui.actions",
        "title": "Prototype is frozen",
        "visibleIf": (
            "$state.activeView === 'conversation' && $state.selectedLifecycleStage === 'prototype' "
            "&& $state.workflowActivePhase !== 'prototype'"
        ),
        "inputs": {
            "variant": "stack",
            "buttons": [
                {
                    "id": "prototype-frozen",
                    "label": "Automation is the current editable process. This Prototype snapshot is read-only.",
                    "icon": "lock-closed-outline",
                    "disabled": True,
                }
            ],
        },
    }
    page["widgets"] = [item for item in page["widgets"] if item.get("id") != "prototype-frozen-notice"]
    prototype_index = next(index for index, item in enumerate(page["widgets"]) if item.get("id") == "builder-chat")
    page["widgets"].insert(prototype_index + 1, readonly_notice)

    automation_start = widget(page, "automation-conversation-start")
    automation_start["title"] = "Hand off the selected Prototype to Automation"
    automation_start["visibleIf"] = (
        "$state.activeView === 'conversation' && $state.selectedLifecycleStage === 'automation' "
        "&& $state.workflowActivePhase === 'prototype'"
    )
    automation_followup = widget(page, "automation-conversation-followup")
    automation_followup["visibleIf"] = (
        "$state.activeView === 'conversation' && $state.selectedLifecycleStage === 'automation' "
        "&& $state.workflowActivePhase === 'automation'"
    )

    return_action = {
        "area": "center",
        "id": "automation-return-to-prototype",
        "type": "ui.actions",
        "title": "Return the Automation result to Prototype",
        "visibleIf": (
            "$state.activeView === 'conversation' && $state.selectedLifecycleStage === 'automation' "
            "&& $state.canReturnToPrototype === true"
        ),
        "inputs": {
            "variant": "toolbar",
            "buttons": [
                {
                    "id": "return-to-prototype",
                    "label": "Derive safe Prototype",
                    "icon": "return-down-back-outline",
                }
            ],
        },
        "actions": [
            {
                "on": "click:return-to-prototype",
                "type": "callSkill",
                "target": "builder_sdk_control_skill.return_to_prototype",
                "params": {
                    "object_type": "$state.selectedProjectKind",
                    "object_id": "$state.selectedProjectId",
                },
                "invalidates": ["builder.project.automation", "builder.project.lifecycle"],
            }
        ],
    }
    page["widgets"] = [item for item in page["widgets"] if item.get("id") != return_action["id"]]
    followup_index = next(
        index for index, item in enumerate(page["widgets"]) if item.get("id") == "automation-conversation-followup"
    )
    page["widgets"].insert(followup_index + 1, return_action)

    settings = widget(page, "chat-side-settings")
    settings["visibleIf"] = (
        "$state.activeView === 'conversation' && $state.selectedLifecycleStage === 'prototype' "
        "&& $state.workflowActivePhase === 'prototype'"
    )

    project_state = widget(page, "overview-project-state")
    project_state["inputs"].setdefault("stateBindings", {}).update(
        {
            "workflowActivePhase": "workflow_active_phase",
            "workflowWorkingLabel": "working_label",
            "previewViewingLabel": "viewing_label",
            "previewViewingReadOnly": "viewing_read_only",
            "canEditPrototype": "can_edit_prototype",
            "canEditAutomation": "can_edit_automation",
            "canReturnToPrototype": "can_return_to_prototype",
            "canPublish": "can_publish",
        }
    )

    tree = widget(page, "project-tree")
    buttons = [item for item in tree["inputs"].get("buttons", []) if item.get("id") != "show-preview"]
    buttons.insert(
        0,
        {
            "id": "show-preview",
            "label": "Show in Preview",
            "icon": "eye-outline",
            "whenKey": "canPreview",
        },
    )
    tree["inputs"]["buttons"] = buttons
    actions = []
    for action in tree.get("actions", []):
        target = str(action.get("target") or "")
        event = str(action.get("on") or "")
        if target == "builder_sdk_control_skill.set_workflow_state" and event in {
            "click:go-automation",
            "click:go-publication",
        }:
            continue
        if target == "builder_sdk_control_skill.set_workflow_state" and event == "click:stabilize":
            action = copy.deepcopy(action)
            action["target"] = "builder_sdk_control_skill.transition_workflow"
            action["params"] = {
                "action": "stabilize_prototype",
                "object_type": "$state.selectedProjectKind",
                "object_id": "$state.selectedProjectId",
            }
        actions.append(action)
    actions.append(
        {
            "on": "click:show-preview",
            "type": "callSkill",
            "target": "builder_sdk_control_skill.select_preview_target",
            "params": {
                "stage": "$event.item.lifecycleStage",
                "revision": "$event.item.revision",
                "object_type": "$state.selectedProjectKind",
                "object_id": "$state.selectedProjectId",
            },
            "invalidates": ["builder.project.preview", "builder.project.lifecycle", "builder.project.metadata"],
        }
    )
    tree["actions"] = actions

    publication = widget(page, "publication-workspace-actions")
    for action in publication.get("actions", []):
        if action.get("on") in {"click:publish", "click:dry-run"}:
            action["enabledIf"] = "$state.canPublish === true"

    overview_preview = widget(page, "overview-side-status")
    existing_fields = overview_preview["inputs"].get("fields", [])
    overview_preview["inputs"]["fields"] = [
        {"key": "viewing", "label": "VIEWING"},
        {"key": "preview_follows_active", "label": "Follows active process"},
        *existing_fields,
    ]

    page.setdefault("meta", {}).setdefault("builder", {}).update(
        {
            "ui_revision": REVISION,
            "previous_revision": "038",
            "workflow_contract": "adaos.builder.workflow.v1",
        }
    )
    page["initialStateSource"]["map"].update(
        {
            "workflowActivePhase": "workflow_active_phase",
            "workflowWorkingLabel": "working_label",
            "previewViewingLabel": "viewing_label",
        }
    )
    after["ui"]["version"] = REVISION
    after["ui"]["user_summary"] = {
        "assumptions": [
            "Lifecycle selection is navigation only; it never changes the editable process or Preview target."
        ],
        "expected_behavior": [
            "Exactly one of Prototype or Automation is WORKING; the other is FROZEN.",
            "Publication is an immutable PUBLISHED snapshot and never the mutable process.",
            "Show in Preview explicitly chooses a supported Lifecycle snapshot.",
        ],
        "preview": ["Preview headings use proto:, active:, or public: prefixes."],
        "risks": ["Returning to Prototype runs a guarded LLM adaptation and is asynchronous."],
    }

    WEBUI.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    revision_dir = SCENARIO / "ui_revisions"
    revision_dir.mkdir(parents=True, exist_ok=True)
    revision_payload = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": REVISION,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scenario_id": "builder",
        "request": {"text": "Implement the complete Prototype/Automation workflow and explicit Preview target."},
        "patch": {
            "id": "builder-workflow-039",
            "target": "ui",
            "operation": "replace_workflow_contract",
            "status": "applied",
        },
        "before_webui": before,
        "after_webui": after,
        "preview_state": {},
    }
    (revision_dir / f"{REVISION}.json").write_text(
        json.dumps(revision_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (revision_dir / "current.txt").write_text(REVISION + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
