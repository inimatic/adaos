"""Build functional Builder UI revision 030 from the approved 029 prototype."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_029 = SCENARIO / "ui_revisions" / "029.json"
REVISION_030 = SCENARIO / "ui_revisions" / "030.json"
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


def _call(on: str, name: str, **params: Any) -> dict[str, Any]:
    return {"on": on, "type": "callSkill", "target": f"{CONTROL}.{name}", "params": params}


def _modal_schema(modal_id: str, widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": modal_id,
        "title": modal_id,
        "layout": {"type": "stack", "areas": [{"id": "main", "role": "main"}]},
        "widgets": widgets,
    }


def _button(button_id: str, label: str, icon: str, **extra: Any) -> dict[str, Any]:
    return {"id": button_id, "label": label, "icon": icon, **extra}


def build() -> None:
    base = _read(REVISION_029)
    old_webui = _read(WEBUI)
    webui = copy.deepcopy(base["after_webui"])
    webui["generated_by"] = CONTROL
    app = webui["ui"]["application"]
    page = app["desktop"]["pageSchema"]
    widgets = {item["id"]: item for item in page["widgets"]}

    page["meta"]["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "030",
        "prototype_base_revision": "029",
        "functional": True,
    }
    page["initialState"] = {
        "activeView": "files",
        "chatSurface": "builder",
        "selectedProjectKind": "scenario",
        "selectedProjectId": "builder",
        "selectedProjectRef": "scenario:builder",
        "selectedProjectTitle": "Builder",
        "selectedObjectKind": "scenario",
        "selectedObjectId": "builder",
        "selectedFilePath": "builder_memory.md",
        "selectedFileTitle": "builder_memory.md",
        "selectedFileProtected": False,
        "selectedNodeId": "stage-proto",
        "selectedTemplate": "default",
        "releaseBump": "patch",
        "previewUrl": "https://inimatic.com/?webspace=desktop-dev",
        "project": {
            "archived": False,
            "description": "Рабочее место для сценариев Builder",
            "title": "Builder",
            "type": "сценарий",
        },
        "dev": {
            "applyMode": "preview",
            "linkedSpace": "desktop-dev",
            "llmProfile": "standard",
            "model": "gpt-5",
            "provider": "openai",
            "voiceEnabled": False,
        },
    }

    widgets["node-views"]["inputs"]["buttons"] = [
        _button("overview", "Обзор", "information-circle-outline"),
        _button("chat", "Разговор", "chatbubbles-outline"),
        _button("files", "Артефакты", "file-tray-stacked-outline"),
        _button("context", "ТЗ", "document-text-outline"),
    ]

    identity = {
        "object_type": "$state.selectedObjectKind",
        "object_id": "$state.selectedObjectId",
    }
    project_identity = {
        "object_type": "$state.selectedProjectKind",
        "object_id": "$state.selectedProjectId",
    }

    widgets["artifact-workbench"]["dataSource"] = _source(
        "read_project_file", path="$state.selectedFilePath", **identity
    )
    widgets["artifact-workbench"]["actions"] = [
        _call(
            "save",
            "save_project_file",
            path="$state.selectedFilePath",
            text="$event.content",
            **identity,
        )
    ]
    widgets["artifact-readonly"]["dataSource"] = _source(
        "read_project_file", path="$state.selectedFilePath", **identity
    )

    overview = widgets["node-overview"]
    overview["dataSource"] = _source("get_project", **project_identity)
    overview["inputs"]["autoCommit"] = False
    overview["inputs"]["fields"] = [
        {
            "id": "title",
            "type": "shortText",
            "label": "Название",
            "required": True,
            "stateKey": "project.title",
            "default": "Builder",
        },
        {
            "id": "description",
            "type": "longText",
            "label": "Описание",
            "stateKey": "project.description",
            "default": "Рабочее место для сценариев Builder",
        },
        {"id": "project_type", "type": "shortText", "label": "Тип проекта", "default": "scenario"},
        {"id": "ov-version", "type": "staticContent", "title": "Версия", "content": "$data.version"},
        {"id": "ov-stage", "type": "staticContent", "title": "Этап", "content": "$data.workflow_state"},
        {
            "id": "ov-dev",
            "type": "staticContent",
            "title": "Dev‑пространство",
            "content": "$data.dev_webspace_id",
        },
        {"id": "ov-archive-section", "type": "section", "title": "Архивирование и восстановление"},
        {
            "id": "ov-archive-info",
            "type": "staticContent",
            "content": "Архивация скрывает проект из активных и сохраняет его историю.",
        },
    ]
    overview["actions"] = [
        _call(
            "submit",
            "update_project_metadata",
            title="$event.values.title",
            description="$event.values.description",
            project_type="$event.values.project_type",
            **project_identity,
        )
    ]

    tree = widgets["project-tree"]
    tree["dataSource"] = _source("get_lifecycle", **project_identity)
    tree["inputs"]["buttons"] = [
        _button("make-current", "Сделать текущей", "flash-outline", whenKey="canMakeCurrent"),
        _button("stabilize", "Стабилизировать", "shield-checkmark-outline", whenKey="canStabilize"),
        _button("go-automation", "К автоматизации", "construct-outline", whenKey="canOpenAutomation"),
        _button("go-publication", "К публикации", "rocket-outline", whenKey="canOpenPublication"),
    ]
    tree["actions"] = [
        {"on": "select", "type": "updateState", "params": {"selectedNodeId": "$event.id"}},
        {
            "on": "click:make-current",
            "type": "callSkill",
            "target": "builder_skill.set_ui_revision_current",
            "params": {"revision": "$event.item.revision"},
        },
        _call("click:stabilize", "push_project", message="Builder prototype stabilization", **project_identity),
        _call("click:stabilize", "set_workflow_state", state="prototype_stable", **project_identity),
        {"on": "click:go-automation", "type": "openModal", "params": {"modalId": "automation"}},
        _call("click:go-automation", "set_workflow_state", state="automation", **project_identity),
        {"on": "click:go-publication", "type": "openModal", "params": {"modalId": "publication"}},
        _call("click:go-publication", "set_workflow_state", state="publication", **project_identity),
    ]

    side = widgets["overview-side-status"]
    side["dataSource"] = _source("get_preview")
    side["inputs"].pop("selectedStateKey", None)
    side["inputs"]["fields"] = [
        {"key": "source_webspace_id", "label": "Исходное пространство"},
        {"key": "dev_webspace_id", "label": "Dev‑пространство"},
        {"key": "selected_kind", "label": "Тип проекта"},
        {"key": "selected_id", "label": "Проект"},
        {"key": "status", "label": "Состояние"},
    ]

    settings = widgets["chat-side-settings"]
    settings["dataSource"] = _source("get_llm_options", **project_identity)
    settings["inputs"]["submitLabel"] = "Применить"
    settings["inputs"]["fields"][1]["id"] = "llmModel"
    settings["actions"] = [
        _call("submit", "set_llm_profile", model="$event.values.llmModel", **project_identity)
    ]

    widgets["overview-restore"]["actions"] = [
        _call("click:restore", "archive_project", archived=False, **project_identity),
        {"on": "click:restore", "type": "updateState", "params": {"project.archived": False}},
    ]
    links = widgets["chat-side-links"]
    links["actions"] = [
        {"on": "click:open-dev-link", "type": "openUrl", "params": {"target": "_blank", "url": "$state.previewUrl"}},
        {"on": "click:show-qr", "type": "openModal", "params": {"modalId": "preview-qr"}},
        _call("click:compare", "select_preview", **project_identity),
    ]

    context_widgets = [
        {
            "area": "center",
            "id": "technical-spec-editor",
            "type": "item.textEditor",
            "title": "Базовое техническое задание",
            "visibleIf": "$state.activeView === 'context'",
            "dataSource": _source("get_prompt_context", **project_identity),
            "inputs": {
                "bindField": "base_tz",
                "mode": "markdown",
                "stateKey": "technicalSpecDraft",
                "titleTemplate": "ТЗ: {object_id}",
            },
            "actions": [
                _call("save", "save_prompt_context", text="$event.content", **project_identity)
            ],
        },
        {
            "area": "center",
            "id": "technical-spec-actions",
            "type": "ui.actions",
            "title": "Дополнения к ТЗ",
            "visibleIf": "$state.activeView === 'context'",
            "inputs": {
                "variant": "toolbar",
                "buttons": [_button("add-addendum", "Добавить уточнение", "add-circle-outline")],
            },
            "actions": [
                {"on": "click:add-addendum", "type": "openModal", "params": {"modalId": "technical-addendum"}}
            ],
        },
        {
            "area": "center",
            "id": "technical-spec-addenda",
            "type": "item.details",
            "title": "История уточнений",
            "visibleIf": "$state.activeView === 'context'",
            "dataSource": _source("get_prompt_context", **project_identity),
            "inputs": {"fields": [{"key": "tz_addenda", "label": "Дополнения"}]},
        },
        {
            "area": "right",
            "id": "context-side-state",
            "type": "item.details",
            "title": "Контекст разработки",
            "visibleIf": "$state.activeView === 'context'",
            "dataSource": _source("get_prompt_context", **project_identity),
            "inputs": {
                "fields": [
                    {"key": "workflow_state", "label": "Этап"},
                    {"key": "builder_llm_model", "label": "Модель"},
                    {"key": "llm_provider", "label": "Провайдер"},
                    {"key": "updated_at", "label": "Обновлено"},
                ]
            },
        },
    ]
    page["widgets"].extend(context_widgets)

    archive_actions = next(
        item for item in app["modals"]["confirm-archive"]["schema"]["widgets"] if item["id"] == "archive-actions"
    )
    archive_actions["actions"] = [
        _call("click:confirm", "archive_project", archived=True, **project_identity),
        {"on": "click:confirm", "type": "updateState", "params": {"project.archived": True}},
        {"on": "click:confirm", "type": "closeModal"},
        {"on": "click:cancel", "type": "closeModal"},
    ]

    file_modal = app["modals"]["file-picker"]["schema"]
    file_tree = next(item for item in file_modal["widgets"] if item["id"] == "file-tree")
    file_tree["dataSource"] = _source("list_project_file_tree", **identity, limit=1000)
    file_tree["actions"] = [
        {
            "on": "select",
            "type": "updateState",
            "params": {
                "selectedFilePath": "$event.path",
                "selectedFileProtected": "$event.protected",
                "selectedFileTitle": "$event.title",
            },
        },
        {"on": "select", "type": "closeModal"},
    ]
    file_modal["widgets"].insert(
        0,
        {
            "area": "main",
            "id": "project-object-list",
            "type": "ui.list",
            "title": "Состав проекта",
            "dataSource": _source("list_project_objects", **project_identity),
            "inputs": {
                "variant": "list",
                "titleKey": "title",
                "subtitleKey": "subtitle",
                "search": True,
            },
            "actions": [
                {
                    "on": "select",
                    "type": "updateState",
                    "params": {
                        "selectedObjectKind": "$event.object_type",
                        "selectedObjectId": "$event.object_id",
                        "selectedFilePath": None,
                    },
                }
            ],
        },
    )

    picker = app["modals"]["project-picker"]["schema"]
    project_list = next(item for item in picker["widgets"] if item["id"] == "project-picker-list")
    project_list["dataSource"] = _source(
        "list_projects",
        limit=200,
        selected_object_type="$state.selectedProjectKind",
        selected_object_id="$state.selectedProjectId",
    )
    project_list["actions"] = [
        {
            "on": "select",
            "type": "updateState",
            "params": {
                "selectedProjectKind": "$event.object_type",
                "selectedProjectId": "$event.object_id",
                "selectedProjectRef": "$event.id",
                "selectedProjectTitle": "$event.title",
                "selectedObjectKind": "$event.object_type",
                "selectedObjectId": "$event.object_id",
                "selectedFilePath": None,
                "project.archived": "$event.archived",
            },
        },
        _call("select", "select_preview", object_type="$event.object_type", object_id="$event.object_id"),
        {"on": "select", "type": "closeModal"},
    ]

    app["modals"]["new-project"] = {
        "title": "Новый DEV-проект",
        "presentation": {"kind": "modal", "restoreFocus": True},
        "schema": _modal_schema(
            "new-project",
            [
                {
                    "area": "main",
                    "id": "new-project-form",
                    "type": "ui.form",
                    "title": "Создать проект",
                    "inputs": {
                        "layout": "responsiveGrid",
                        "submitLabel": "Создать",
                        "fields": [
                            {
                                "id": "object_type",
                                "type": "select",
                                "label": "Тип",
                                "required": True,
                                "default": "scenario",
                                "options": [
                                    {"label": "Сценарий", "value": "scenario"},
                                    {"label": "Навык", "value": "skill"},
                                ],
                            },
                            {"id": "object_id", "type": "shortText", "label": "ID проекта", "required": True},
                        ],
                    },
                    "actions": [
                        _call(
                            "submit",
                            "create_project",
                            object_type="$event.values.object_type",
                            object_id="$event.values.object_id",
                            template="$state.selectedTemplate",
                        ),
                        {
                            "on": "submit",
                            "type": "updateState",
                            "params": {
                                "selectedProjectKind": "$event.values.object_type",
                                "selectedProjectId": "$event.values.object_id",
                                "selectedObjectKind": "$event.values.object_type",
                                "selectedObjectId": "$event.values.object_id",
                                "selectedFilePath": None,
                            },
                        },
                        {"on": "submit", "type": "closeModal"},
                    ],
                },
                {
                    "area": "main",
                    "id": "new-project-templates",
                    "type": "ui.list",
                    "title": "Шаблон",
                    "dataSource": _source("list_templates", object_type="scenario"),
                    "inputs": {"variant": "list", "titleKey": "label", "subtitleKey": "source"},
                    "actions": [
                        {"on": "select", "type": "updateState", "params": {"selectedTemplate": "$event.id"}}
                    ],
                },
                {
                    "area": "main",
                    "id": "new-project-actions",
                    "type": "ui.actions",
                    "title": "Отмена",
                    "inputs": {"variant": "toolbar", "buttons": [_button("cancel", "Отмена", "close-outline")]},
                    "actions": [{"on": "click:cancel", "type": "closeModal"}],
                },
            ],
        ),
    }

    app["modals"]["technical-addendum"] = {
        "title": "Дополнение к ТЗ",
        "presentation": {"kind": "modal"},
        "schema": _modal_schema(
            "technical-addendum",
            [
                {
                    "area": "main",
                    "id": "technical-addendum-form",
                    "type": "ui.form",
                    "title": "Новое уточнение",
                    "inputs": {
                        "submitLabel": "Добавить",
                        "fields": [
                            {"id": "text", "type": "longText", "label": "Текст", "required": True},
                            {"id": "iteration_ref", "type": "shortText", "label": "Ссылка на итерацию"},
                        ],
                    },
                    "actions": [
                        _call(
                            "submit",
                            "append_prompt_addendum",
                            text="$event.values.text",
                            iteration_ref="$event.values.iteration_ref",
                            **project_identity,
                        ),
                        {"on": "submit", "type": "closeModal"},
                    ],
                }
            ],
        ),
    }

    app["modals"]["automation"] = {
        "title": "Автоматизация Builder",
        "presentation": {"kind": "modal"},
        "schema": _modal_schema(
            "automation",
            [
                {
                    "area": "main",
                    "id": "automation-start",
                    "type": "ui.form",
                    "title": "Запуск автономной разработки",
                    "inputs": {
                        "submitLabel": "Запустить",
                        "fields": [
                            {
                                "id": "implementation_brief",
                                "type": "longText",
                                "label": "Утверждённый implementation brief",
                                "required": True,
                            }
                        ],
                    },
                    "actions": [
                        _call(
                            "submit",
                            "start_automation",
                            implementation_brief="$event.values.implementation_brief",
                            **project_identity,
                        )
                    ],
                },
                {
                    "area": "main",
                    "id": "automation-followup",
                    "type": "ui.form",
                    "title": "Уточнение для текущего запуска",
                    "inputs": {
                        "submitLabel": "Отправить",
                        "fields": [{"id": "text", "type": "longText", "label": "Сообщение", "required": True}],
                    },
                    "actions": [
                        _call("submit", "submit_automation", text="$event.values.text", **project_identity)
                    ],
                },
                {
                    "area": "main",
                    "id": "automation-state",
                    "type": "item.details",
                    "title": "Состояние",
                    "dataSource": _source("get_automation", **project_identity),
                    "inputs": {
                        "fields": [
                            {"key": "session_present", "label": "Сессия"},
                            {"key": "automation", "label": "Автоматизация"},
                        ]
                    },
                },
            ],
        ),
    }

    publication_actions = {
        "area": "main",
        "id": "publication-actions",
        "type": "ui.actions",
        "title": "Forge и публикация",
        "inputs": {
            "variant": "toolbar",
            "buttons": [
                _button("checkpoint", "Checkpoint", "git-commit-outline"),
                _button("update", "Обновить", "sync-outline"),
                _button("dry-run", "Проверить релиз", "checkmark-circle-outline"),
                _button("publish", "Опубликовать", "rocket-outline"),
                _button("delete", "Удалить", "trash-outline", kind="danger"),
            ],
        },
        "actions": [
            _call("click:checkpoint", "push_project", **project_identity),
            _call("click:update", "update_project", **project_identity),
            _call("click:dry-run", "publish_project", bump="$state.releaseBump", dry_run=True, **project_identity),
            {"on": "click:publish", "type": "openModal", "params": {"modalId": "confirm-publish"}},
            {"on": "click:delete", "type": "openModal", "params": {"modalId": "confirm-delete"}},
        ],
    }
    app["modals"]["publication"] = {
        "title": "Публикация",
        "presentation": {"kind": "modal"},
        "schema": _modal_schema(
            "publication",
            [
                {
                    "area": "main",
                    "id": "publication-status",
                    "type": "item.details",
                    "title": "Проект",
                    "dataSource": _source("get_project", **project_identity),
                    "inputs": {
                        "fields": [
                            {"key": "project_ref", "label": "Проект"},
                            {"key": "version", "label": "Версия"},
                            {"key": "workflow_state", "label": "Этап"},
                        ]
                    },
                },
                publication_actions,
                {
                    "area": "main",
                    "id": "publication-history",
                    "type": "ui.list",
                    "title": "История изменений",
                    "dataSource": _source("list_changes", limit=100, **project_identity),
                    "inputs": {
                        "variant": "list",
                        "titleKey": "title",
                        "subtitleKey": "subtitle",
                        "previewKey": "created_at",
                    },
                },
            ],
        ),
    }

    app["modals"]["confirm-publish"] = {
        "title": "Подтверждение публикации",
        "presentation": {"kind": "modal"},
        "schema": _modal_schema(
            "confirm-publish",
            [
                {
                    "area": "main",
                    "id": "publish-warning",
                    "type": "ui.form",
                    "title": "Внешнее изменение",
                    "inputs": {
                        "fields": [
                            {
                                "id": "warning",
                                "type": "staticContent",
                                "content": "Будет опубликована новая версия выбранного проекта.",
                            }
                        ]
                    },
                },
                {
                    "area": "main",
                    "id": "publish-confirm-actions",
                    "type": "ui.actions",
                    "inputs": {
                        "variant": "stack",
                        "buttons": [
                            _button("confirm", "Опубликовать", "rocket-outline", kind="danger"),
                            _button("cancel", "Отмена", "close-outline"),
                        ],
                    },
                    "actions": [
                        _call(
                            "click:confirm",
                            "publish_project",
                            bump="$state.releaseBump",
                            dry_run=False,
                            **project_identity,
                        ),
                        {"on": "click:confirm", "type": "closeModal"},
                        {"on": "click:cancel", "type": "closeModal"},
                    ],
                },
            ],
        ),
    }

    app["modals"]["confirm-delete"] = {
        "title": "Удаление проекта",
        "presentation": {"kind": "modal"},
        "schema": _modal_schema(
            "confirm-delete",
            [
                {
                    "area": "main",
                    "id": "delete-warning",
                    "type": "ui.form",
                    "title": "Необратимое изменение Forge",
                    "inputs": {
                        "fields": [
                            {
                                "id": "warning",
                                "type": "staticContent",
                                "content": "Удалить проект в Forge? Локальная DEV-копия будет сохранена.",
                            }
                        ]
                    },
                },
                {
                    "area": "main",
                    "id": "delete-confirm-actions",
                    "type": "ui.actions",
                    "inputs": {
                        "variant": "stack",
                        "buttons": [
                            _button("confirm", "Удалить", "trash-outline", kind="danger"),
                            _button("cancel", "Отмена", "close-outline"),
                        ],
                    },
                    "actions": [
                        _call("click:confirm", "delete_project", confirm=True, remove_local=False, **project_identity),
                        {"on": "click:confirm", "type": "closeModal"},
                        {"on": "click:cancel", "type": "closeModal"},
                    ],
                },
            ],
        ),
    }

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "030"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["mock_data"] = {}
    preview_state["datasources"] = []
    preview_state["user_summary"] = {
        "assumptions": ["Revision 029 is the approved visual and interaction baseline."],
        "preview": ["The 029 three-pane Builder workspace now uses SDK-backed project data and actions."],
        "risks": ["External Forge mutations still require their explicit confirmation modals."],
        "expected_behavior": [
            "Project, file, specification, automation, and publication operations stay in the selected project context."
        ],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "030",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {"source": "codex", "prototype_base_revision": "029", "sdk_only": True},
        "request": "Restore prototype 029 and bind the complete Prompt IDE capability surface through the SDK.",
        "patch": {"operation": "sdk_bindings_on_prototype_029", "base_revision": "029"},
        "llm": {"used": False},
        "before_webui": old_webui,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_030, revision)
    CURRENT.write_text("030\n", encoding="utf-8")


if __name__ == "__main__":
    build()
