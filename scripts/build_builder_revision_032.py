"""Build Builder UI revision 032 from the preserved autonomous revision 031."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_031 = SCENARIO / "ui_revisions" / "031.json"
REVISION_032 = SCENARIO / "ui_revisions" / "032.json"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"
CONTROL = "builder_sdk_control_skill"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source(name: str, **params: Any) -> dict[str, Any]:
    return {"kind": "skill", "name": f"{CONTROL}.{name}", "params": params}


def build() -> None:
    base = _read(REVISION_031)
    before = _read(WEBUI)
    webui = copy.deepcopy(base["after_webui"])
    webui["generated_by"] = CONTROL
    app = webui["ui"]["application"]
    page = app["desktop"]["pageSchema"]
    widgets = {item["id"]: item for item in page["widgets"]}
    identity = {
        "object_type": "$state.selectedProjectKind",
        "object_id": "$state.selectedProjectId",
    }

    page.setdefault("meta", {})["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "032",
        "prototype_base_revision": "029",
        "previous_revision": "031",
        "functional": True,
    }
    state = page["initialState"]
    state.pop("previewUrl", None)
    state.update(
        {
            "builderConversationId": "conv.skill.builder_skill.default",
            "builderTopicId": "prompt-project:scenario:builder",
            "builderThreadId": "prompt-project:scenario:builder",
        }
    )

    chat = widgets["builder-chat"]
    stream_params = chat["dataSource"]["params"]
    stream_params["conversation_id"] = "$state.builderConversationId"
    stream_params["conversation_topic_id"] = "$state.builderTopicId"
    chat_meta = chat["inputs"]["meta"]
    chat_meta["conversation_id"] = "$state.builderConversationId"
    chat_meta["conversation_topic_id"] = "$state.builderTopicId"
    chat_meta["thread_id"] = "$state.builderThreadId"

    overview = widgets["node-overview"]
    overview.pop("dataSource", None)
    overview["inputs"]["fields"] = [
        {
            "id": "project_type",
            "type": "shortText",
            "label": "Тип проекта",
            "stateKey": "project.type",
            "default": "scenario",
            "disabled": True,
            "help": "Тип задаётся при создании проекта и после этого не изменяется.",
        },
        {
            "id": "description",
            "type": "longText",
            "label": "Описание",
            "stateKey": "project.description",
            "default": "Рабочее место для сценариев Builder",
        },
        {
            "id": "title",
            "type": "shortText",
            "label": "Название",
            "required": True,
            "stateKey": "project.title",
            "default": "Builder",
        },
        {"id": "ov-archive-section", "type": "section", "title": "Архивирование и восстановление"},
        {
            "id": "ov-archive-info",
            "type": "staticContent",
            "content": "Архивация скрывает проект из активных и сохраняет его историю.",
        },
    ]
    overview["actions"][0]["params"].pop("project_type", None)

    project_state = {
        "area": "center",
        "id": "overview-project-state",
        "type": "item.details",
        "title": "Версия и среда",
        "visibleIf": "$state.activeView === 'overview'",
        "dataSource": _source("get_project", **identity),
        "inputs": {
            "fields": [
                {"key": "version", "label": "Версия"},
                {"key": "workflow_state", "label": "Этап"},
                {"key": "dev_webspace_id", "label": "Dev‑пространство"},
            ]
        },
    }
    page["widgets"] = [item for item in page["widgets"] if item["id"] != project_state["id"]]
    overview_index = next(i for i, item in enumerate(page["widgets"]) if item["id"] == "node-overview")
    page["widgets"].insert(overview_index + 1, project_state)

    links = widgets["chat-side-links"]
    links["actions"] = [
        {
            "on": "click:open-dev-link",
            "type": "openWorkspace",
            "params": {"webspaceId": "$client.webspaceId", "newWindow": True},
        },
        {"on": "click:show-qr", "type": "openModal", "params": {"modalId": "preview-qr"}},
        {
            "on": "click:compare",
            "type": "callSkill",
            "target": f"{CONTROL}.select_preview",
            "params": identity,
        },
    ]

    app["modals"]["preview-qr"]["schema"]["widgets"][0] = {
        "area": "main",
        "id": "preview-qr-code",
        "type": "visual.qrCode",
        "title": "Сканируйте на другом устройстве",
        "dataSource": _source("get_preview"),
        "inputs": {
            "bindField": "qr_text",
            "captionField": "dev_webspace_id",
            "width": 240,
            "emptyText": "Адрес preview пока недоступен",
        },
    }

    automation_widgets = {
        item["id"]: item for item in app["modals"]["automation"]["schema"]["widgets"]
    }
    automation_widgets["automation-state"]["inputs"]["fields"] = [
        {"key": "status", "label": "Статус"},
        {"key": "phase", "label": "Этап выполнения"},
        {"key": "task_id", "label": "Task id"},
        {"key": "progress_message", "label": "Последнее сообщение"},
        {"key": "failure_message", "label": "Ошибка"},
        {"key": "failure_stage", "label": "Стадия ошибки"},
        {"key": "failure_id", "label": "Failure id"},
        {"key": "retryable", "label": "Можно повторить"},
        {"key": "diagnostic_hint", "label": "Что делать"},
        {"key": "stderr_path", "label": "Журнал stderr"},
        {"key": "events_path", "label": "Журнал событий"},
    ]
    automation_widgets["automation-followup"]["title"] = "Уточнение или повтор после ошибки"
    automation_widgets["automation-followup"]["inputs"]["submitLabel"] = "Отправить новую итерацию"

    picker = app["modals"]["project-picker"]["schema"]["widgets"][0]
    selection = next(action for action in picker["actions"] if action.get("on") == "select" and action.get("type") == "updateState")
    selection["params"].update(
        {
            "project.title": "$event.title",
            "project.description": "$event.description",
            "project.type": "$event.object_type",
            "builderTopicId": "prompt-project:$event.object_type:$event.object_id",
            "builderThreadId": "prompt-project:$event.object_type:$event.object_id",
        }
    )

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "032"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["mock_data"] = {}
    preview_state["datasources"] = []
    preview_state["user_summary"] = {
        "assumptions": ["Revision 031 is preserved as the autonomous input revision."],
        "preview": ["Preview actions, conversation history, metadata, and automation diagnostics use runtime-backed interfaces."],
        "risks": ["A UI revision apply still replaces the page projection once; chat history restores from the shared conversation ledger."],
        "expected_behavior": ["No preview operation creates a second '-dev' suffix."],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "032",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {
            "source": "codex",
            "prototype_base_revision": "029",
            "previous_revision": "031",
            "sdk_only": True,
        },
        "request": "Fix preview navigation, durable conversation bindings, project metadata rendering, and automation diagnostics.",
        "patch": {"operation": "runtime_binding_corrections", "base_revision": "031"},
        "llm": {"used": False},
        "before_webui": before,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_032, revision)
    CURRENT.write_text("032\n", encoding="utf-8")


if __name__ == "__main__":
    build()
