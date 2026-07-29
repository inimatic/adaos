"""Build Builder UI 055 as a bounded chat-first forward port of UI 054.

The transform is deterministic and UTF-8-only.  It retains every recovered
control-plane widget/modal, hides the permanent Lifecycle tree, and adds the
on-demand Process projection without invoking an LLM.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
OWNER = "sn_6acf0c01"
SCENARIO = ROOT / ".adaos" / "dev" / OWNER / "scenarios" / "builder"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
SCENARIO_YAML = SCENARIO / "scenario.yaml"
REVISIONS = SCENARIO / "ui_revisions"
CURRENT = REVISIONS / "current.txt"
PARITY = ROOT / "docs" / "architecture" / "builder-functional-parity.json"
BASE_REVISION = "054"
REVISION = "055"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _widget(schema: dict[str, Any], widget_id: str) -> dict[str, Any]:
    matches = [item for item in schema["widgets"] if item.get("id") == widget_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one widget {widget_id!r}; found {len(matches)}")
    return matches[0]


def _base_webui() -> dict[str, Any]:
    record = _read(REVISIONS / f"{BASE_REVISION}.json")
    value = record.get("after_webui")
    if not isinstance(value, dict):
        raise ValueError(f"UI revision {BASE_REVISION} has no after_webui")
    return copy.deepcopy(value)


def _interaction_status() -> dict[str, Any]:
    return {
        "area": "center",
        "id": "interaction-status",
        "type": "item.details",
        "title": "Current change",
        "title_i18n": {"key": "builder.text.current.change"},
        "visibleIf": "$state.activeView === 'conversation'",
        "dataSource": {
            "kind": "skill",
            "name": "builder_sdk_control_skill.get_interaction_frame",
            "params": {
                "object_type": "$state.selectedProjectKind",
                "object_id": "$state.selectedProjectId",
            },
            "cacheTtlMs": 0,
            "invalidationTags": [
                "builder.project.change",
                "builder.project.lifecycle",
                "builder.project.preview",
            ],
            "preserveLastValue": True,
        },
        "inputs": {
            "compact": True,
            "fields": [
                {"key": "message", "label": "Next"},
                {"key": "status.phase", "label": "Activity"},
                {"key": "status.gate", "label": "Gate"},
            ],
            "stateBindings": {
                "workflowGeneration": "generation",
                "conversationFocus": "context.conversation_focus",
                "inspectedProcessRef": "context.inspected_ref",
                "previewTargetRef": "context.preview_target",
            },
        },
    }


def _context_actions() -> dict[str, Any]:
    return {
        "area": "center",
        "id": "context-actions",
        "type": "ui.actions",
        "title": "Available actions",
        "title_i18n": {"key": "builder.text.available.actions"},
        "visibleIf": "$state.activeView === 'conversation'",
        "inputs": {
            "variant": "toolbar",
            "size": "small",
            "buttons": [
                {
                    "id": "show-process",
                    "label": "Process",
                    "icon": "git-branch-outline",
                    "label_i18n": {"key": "builder.text.process"},
                },
                {
                    "id": "approve-prototype",
                    "label": "Approve prototype",
                    "icon": "shield-checkmark-outline",
                    "enabledIf": "$state.canEditPrototype === true",
                    "label_i18n": {"key": "builder.text.approve.prototype"},
                },
                {
                    "id": "start-implementation",
                    "label": "Start implementation",
                    "icon": "construct-outline",
                    "enabledIf": "$state.canStartImplementation === true",
                    "label_i18n": {"key": "builder.text.start.implementation"},
                },
                {
                    "id": "prepare-trial",
                    "label": "Prepare trial",
                    "icon": "flask-outline",
                    "enabledIf": "$state.canPrepareCandidate === true",
                    "label_i18n": {"key": "builder.text.prepare.trial"},
                },
                {
                    "id": "publication",
                    "label": "Publication",
                    "icon": "rocket-outline",
                    "enabledIf": "$state.canPublish === true",
                    "label_i18n": {"key": "builder.text.publication"},
                },
            ],
        },
        "actions": [
            {"on": "click:show-process", "type": "openModal", "params": {"modalId": "process"}},
            {
                "on": "click:approve-prototype",
                "type": "callSkill",
                "target": "builder_sdk_control_skill.push_project",
                "params": {
                    "checkpoint_id": "chat-first-prototype-approval",
                    "message": "Builder prototype approval",
                    "object_type": "$state.selectedProjectKind",
                    "object_id": "$state.selectedProjectId",
                },
                "invalidates": ["builder.project.change", "builder.project.lifecycle"],
            },
            {
                "on": "click:approve-prototype",
                "type": "callSkill",
                "target": "builder_sdk_control_skill.transition_workflow",
                "params": {
                    "action": "stabilize_prototype",
                    "object_type": "$state.selectedProjectKind",
                    "object_id": "$state.selectedProjectId",
                    "expected_generation": "$state.workflowGeneration",
                },
                "invalidates": [
                    "builder.project.change",
                    "builder.project.metadata",
                    "builder.project.lifecycle",
                ],
            },
            {
                "on": "click:start-implementation",
                "type": "openModal",
                "params": {"modalId": "automation"},
            },
            {
                "on": "click:prepare-trial",
                "type": "openModal",
                "params": {"modalId": "publication"},
            },
            {
                "on": "click:publication",
                "type": "openModal",
                "params": {"modalId": "publication"},
            },
        ],
    }


def _change_summary() -> dict[str, Any]:
    return {
        "area": "left",
        "id": "change-summary",
        "type": "item.details",
        "title": "Change",
        "title_i18n": {"key": "builder.text.change"},
        "dataSource": {
            "kind": "skill",
            "name": "builder_sdk_control_skill.get_change_set",
            "params": {
                "object_type": "$state.selectedProjectKind",
                "object_id": "$state.selectedProjectId",
            },
            "cacheTtlMs": 0,
            "invalidationTags": ["builder.project.change", "builder.project.lifecycle"],
            "preserveLastValue": True,
        },
        "inputs": {
            "compact": True,
            "fields": [
                {"key": "request", "label": "Intent"},
                {"key": "status", "label": "Status"},
                {"key": "gate", "label": "Next gate"},
            ],
            "stateBindings": {
                "selectedChangeId": "change_id",
                "selectedChangeStatus": "status",
                "selectedChangeGate": "gate",
            },
        },
    }


def _process_modal() -> dict[str, Any]:
    return {
        "title": "Development process",
        "title_i18n": {"key": "builder.text.development.process"},
        "presentation": {"kind": "drawer", "restoreFocus": True},
        "schema": {
            "id": "process",
            "title": "Development process",
            "layout": {"type": "stack", "areas": [{"id": "main", "role": "main"}]},
            "widgets": [
                {
                    "area": "main",
                    "id": "process-tree",
                    "type": "collection.tree",
                    "title": "Issue → Change → Prototype → Implementation → Trial → Publication",
                    "dataSource": {
                        "kind": "skill",
                        "name": "builder_sdk_control_skill.get_process_tree",
                        "params": {
                            "object_type": "$state.selectedProjectKind",
                            "object_id": "$state.selectedProjectId",
                        },
                        "cacheTtlMs": 0,
                        "invalidationTags": ["builder.project.change", "builder.project.lifecycle"],
                        "preserveLastValue": True,
                    },
                    "inputs": {
                        "selectedStateKey": "inspectedProcessRef",
                        "compactIndent": True,
                        "wrapTitles": True,
                        "buttons": [
                            {
                                "id": "show-preview",
                                "label": "Show in Preview",
                                "icon": "eye-outline",
                                "whenKey": "canPreview",
                            }
                        ],
                    },
                    "actions": [
                        {
                            "on": "select",
                            "type": "updateState",
                            "params": {"inspectedProcessRef": "$event.id"},
                        },
                        {
                            "on": "select",
                            "type": "callSkill",
                            "target": "builder_sdk_control_skill.inspect_process_ref",
                            "params": {
                                "inspected_ref": "$event.id",
                                "expected_generation": "$state.workflowGeneration",
                                "object_type": "$state.selectedProjectKind",
                                "object_id": "$state.selectedProjectId",
                            },
                            "invalidates": ["builder.project.change"],
                        },
                        {
                            "on": "click:show-preview",
                            "type": "callSkill",
                            "target": "builder_sdk_control_skill.select_preview_target",
                            "params": {
                                "stage": "$event.item.previewStage",
                                "revision": "$event.item.revision",
                                "object_type": "$state.selectedProjectKind",
                                "object_id": "$state.selectedProjectId",
                            },
                            "invalidates": [
                                "builder.project.preview",
                                "builder.project.metadata",
                            ],
                        },
                    ],
                }
            ],
        },
    }


def transform(base: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    page = result["ui"]["application"]["desktop"]["pageSchema"]
    state = page.setdefault("initialState", {})
    state.update(
        {
            "activeView": "conversation",
            "workflowGeneration": 0,
            "conversationFocus": "scenario:builder",
            "inspectedProcessRef": None,
            "previewTargetRef": None,
            "selectedChangeId": None,
            "selectedChangeStatus": "not_planned",
            "selectedChangeGate": None,
            "canStartImplementation": False,
            "processPinned": False,
        }
    )

    header = _widget(page, "project-header")
    header["title"] = "Project · Change · Preview"
    header["inputs"]["buttons"] = [
        {"id": "project-label", "label": "$state.selectedProjectTitle", "icon": "folder-outline"},
        {"id": "change-label", "label": "$state.changeLabel", "icon": "git-branch-outline"},
        {"id": "preview-label", "label": "$state.previewViewingLabel", "icon": "eye-outline"},
    ]

    status = _widget(page, "workflow-status")
    status["title"] = "Current context"
    status["inputs"]["fields"] = [
        {"key": "working_label", "label": "Editable activity"},
        {"key": "change_label", "label": "Change"},
        {"key": "viewing_label", "label": "Preview"},
    ]
    status["inputs"]["stateBindings"].update(
        {
            "workflowGeneration": "workflow_generation",
            "changeLabel": "change_label",
            "canStartImplementation": "can_start_implementation",
        }
    )

    views = _widget(page, "node-views")
    for button in views["inputs"]["buttons"]:
        if button.get("id") == "conversation":
            button["label"] = "Conversation"
            button["label_i18n"] = {"key": "builder.text.conversation"}

    chat = _widget(page, "builder-chat")
    chat["title"] = "Development conversation"
    chat["title_i18n"] = {"key": "builder.text.development.conversation"}
    chat["visibleIf"] = "$state.activeView === 'conversation'"

    left_actions = _widget(page, "left-actions")
    left_actions["inputs"]["buttons"].append(
        {
            "id": "show-process",
            "label": "Process",
            "icon": "git-branch-outline",
            "label_i18n": {"key": "builder.text.process"},
        }
    )
    left_actions["actions"] = [
        {
            "on": "click:choose-project",
            "type": "updateState",
            "params": {"projectPickerArchived": False},
        },
        {
            "on": "click:choose-project",
            "type": "openModal",
            "params": {"modalId": "project-picker"},
        },
        {
            "on": "click:show-process",
            "type": "openModal",
            "params": {"modalId": "process"},
        },
    ]

    lifecycle = _widget(page, "project-tree")
    lifecycle["visibleIf"] = "$state.processPinned === true"
    lifecycle["title"] = "Lifecycle (compatibility)"

    widgets = page["widgets"]
    header_index = widgets.index(header)
    widgets.insert(header_index + 1, _interaction_status())
    widgets.insert(header_index + 2, _context_actions())
    lifecycle_index = widgets.index(lifecycle)
    widgets.insert(lifecycle_index, _change_summary())
    result["ui"]["application"].setdefault("modals", {})["process"] = _process_modal()
    return result


def _update_i18n() -> None:
    translations = {
        "en": {
            "builder.text.current.change": "Current change",
            "builder.text.available.actions": "Available actions",
            "builder.text.process": "Process",
            "builder.text.approve.prototype": "Approve prototype",
            "builder.text.start.implementation": "Start implementation",
            "builder.text.prepare.trial": "Prepare trial",
            "builder.text.development.process": "Development process",
            "builder.text.development.conversation": "Development conversation",
            "builder.text.change": "Change",
        },
        "ru": {
            "builder.text.current.change": "Текущее изменение",
            "builder.text.available.actions": "Доступные действия",
            "builder.text.process": "Процесс",
            "builder.text.approve.prototype": "Согласовать прототип",
            "builder.text.start.implementation": "Начать реализацию",
            "builder.text.prepare.trial": "Подготовить апробацию",
            "builder.text.development.process": "Процесс разработки",
            "builder.text.development.conversation": "Диалог разработки",
            "builder.text.change": "Изменение",
        },
    }
    for locale, additions in translations.items():
        path = SCENARIO / "assets" / "i18n" / f"{locale}.json"
        value = _read(path)
        value.update(additions)
        _write(path, value)


def build() -> None:
    current = CURRENT.read_text(encoding="utf-8-sig").strip()
    if current not in {BASE_REVISION, REVISION}:
        raise ValueError(
            f"Builder UI moved from the reviewed {BASE_REVISION} base: current={current}"
        )
    before = _base_webui()
    after = transform(before)
    manifest = yaml.safe_load(SCENARIO_YAML.read_text(encoding="utf-8-sig")) or {}
    scenario = _read(SCENARIO_JSON)
    scenario["version"] = str(manifest.get("version") or scenario.get("version") or "")
    scenario["updated_at"] = str(manifest.get("updated_at") or scenario.get("updated_at") or "")
    scenario["ui"] = copy.deepcopy(after["ui"])
    scenario["ui"]["manifest"] = "webui.json"
    _write(WEBUI, after)
    _write(SCENARIO_JSON, scenario)
    _write(SCENARIO / "assets" / "builder_functional_parity.json", _read(PARITY))
    _update_i18n()
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": REVISION,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scenario_id": "builder",
        "request": {
            "text": "Make the recovered Builder conversation-first and expose dependent Process provenance on demand."
        },
        "patch": {
            "id": "builder-conversational-workbench-055",
            "target": "ui",
            "operation": "chat_first_forward_port",
            "base_revision": BASE_REVISION,
            "status": "applied",
        },
        "before_webui": before,
        "after_webui": after,
        "preview_state": {},
    }
    _write(REVISIONS / f"{REVISION}.json", revision)
    CURRENT.write_text(REVISION + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
