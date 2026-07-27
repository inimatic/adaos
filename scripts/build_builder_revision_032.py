"""Build Builder UI revision 032 from the preserved autonomous revision 031."""

from __future__ import annotations

import copy
import hashlib
import json
import re
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
DATA_SOURCE_INVALIDATION_TAGS = {
    f"{CONTROL}.read_project_file": ["builder.project.files"],
    f"{CONTROL}.get_project": ["builder.project.metadata", "builder.project.lifecycle", "builder.project.publication"],
    f"{CONTROL}.get_lifecycle": ["builder.project.lifecycle"],
    f"{CONTROL}.get_preview": ["builder.project.preview"],
    f"{CONTROL}.get_llm_options": ["builder.project.llm"],
    f"{CONTROL}.get_prompt_context": ["builder.project.prompt"],
    f"{CONTROL}.list_project_objects": ["builder.project.files"],
    f"{CONTROL}.list_project_file_tree": ["builder.project.files"],
    f"{CONTROL}.list_projects": ["builder.project.catalog"],
    f"{CONTROL}.list_templates": ["builder.project.templates"],
    f"{CONTROL}.get_automation": ["builder.project.automation"],
    f"{CONTROL}.list_changes": ["builder.project.publication"],
}
ACTION_INVALIDATION_TAGS = {
    f"{CONTROL}.save_project_file": ["builder.project.files"],
    f"{CONTROL}.update_project_metadata": ["builder.project.metadata", "builder.project.lifecycle"],
    f"{CONTROL}.set_workflow_state": ["builder.project.metadata", "builder.project.lifecycle"],
    f"{CONTROL}.archive_project": ["builder.project.catalog", "builder.project.metadata", "builder.project.lifecycle"],
    f"{CONTROL}.select_preview": ["builder.project.preview"],
    f"{CONTROL}.set_llm_profile": ["builder.project.llm"],
    f"{CONTROL}.save_prompt_context": ["builder.project.prompt"],
    f"{CONTROL}.append_prompt_addendum": ["builder.project.prompt"],
    f"{CONTROL}.create_project": ["builder.project.catalog", "builder.project.metadata", "builder.project.lifecycle"],
    f"{CONTROL}.start_automation": ["builder.project.automation", "builder.project.lifecycle"],
    f"{CONTROL}.submit_automation": ["builder.project.automation", "builder.project.lifecycle"],
    f"{CONTROL}.push_project": ["builder.project.publication", "builder.project.lifecycle"],
    f"{CONTROL}.publish_project": ["builder.project.publication", "builder.project.metadata", "builder.project.lifecycle"],
    f"{CONTROL}.delete_project": ["builder.project.catalog", "builder.project.metadata", "builder.project.lifecycle"],
}
TEXT_FIELDS = {
    "title",
    "label",
    "placeholder",
    "help",
    "content",
    "submitLabel",
    "emptyText",
}
RU_EN = {
    "Builder — рабочее место разработки": "Builder — development workspace",
    "Dev‑пространство": "DEV workspace",
    "Forge и публикация": "Forge and publication",
    "ID проекта": "Project ID",
    "QR для открытия preview": "QR to open preview",
    "QR для просмотра": "Preview QR",
    "Автоматизация Builder": "Builder Automation",
    "Адрес preview пока недоступен": "Preview address is not available yet",
    "Артефакты": "Artifacts",
    "Архивация скрывает проект из активных и сохраняет его историю.": "Archiving hides the project from active projects and preserves its history.",
    "Архивация скрывает проект из активных. Будет создана резервная копия и доступна кнопка восстановления.": "Archiving hides the project from active projects. A backup will be created and the restore action will remain available.",
    "Архивирование": "Archive",
    "Архивирование и восстановление": "Archive and restore",
    "Архивировать": "Archive",
    "Базовое техническое задание": "Base technical specification",
    "Будет опубликована новая версия выбранного проекта.": "A new version of the selected project will be published.",
    "Версия": "Version",
    "Версия и среда": "Version and environment",
    "Внешнее изменение": "External change",
    "Восстановить": "Restore",
    "Восстановление": "Restore",
    "Выбор проекта": "Select project",
    "Выбор файла": "Select file",
    "Выбрать проект": "Choose project",
    "Выбрать файл": "Choose file",
    "Голосовой ввод": "Voice input",
    "Действия": "Actions",
    "Дерево файлов": "File tree",
    "Добавить": "Add",
    "Добавить уточнение": "Add clarification",
    "Дополнение к ТЗ": "Specification addendum",
    "Дополнения": "Addenda",
    "Дополнения к ТЗ": "Specification addenda",
    "Жизненный цикл": "Lifecycle",
    "Журнал stderr": "stderr log",
    "Журнал событий": "Event log",
    "Закрыть": "Close",
    "Запуск автономной разработки": "Start autonomous development",
    "Запустить": "Start",
    "История изменений": "Change history",
    "История уточнений": "Clarification history",
    "Исходное пространство": "Source workspace",
    "К автоматизации": "Move to Automation",
    "К публикации": "Move to publication",
    "Контекст разработки": "Development context",
    "Локальный": "Local",
    "Модель": "Model",
    "Можно повторить": "Retryable",
    "Навык": "Skill",
    "Название": "Title",
    "Настройки разработки": "Development settings",
    "Необратимое изменение Forge": "Irreversible Forge change",
    "Новое уточнение": "New clarification",
    "Новый DEV-проект": "New DEV project",
    "Обзор": "Overview",
    "Обзор проекта и стабильность": "Project overview and stability",
    "Обновить": "Update",
    "Обновлено": "Updated",
    "Описание": "Description",
    "Опишите, что нужно изменить или сгенерировать…": "Describe what should be changed or generated…",
    "Опубликовать": "Publish",
    "Открыть просмотр в новом окне": "Open preview in a new window",
    "Отмена": "Cancel",
    "Отправить новую итерацию": "Submit a new iteration",
    "Ошибка": "Error",
    "Подтвердите архивирование": "Confirm archive",
    "Подтверждение публикации": "Confirm publication",
    "Последнее сообщение": "Latest message",
    "Представления": "Views",
    "Применить": "Apply",
    "Провайдер": "Provider",
    "Проверить релиз": "Validate release",
    "Проект": "Project",
    "Проект: Builder": "Project: Builder",
    "Проекты": "Projects",
    "Просмотр dev‑пространства": "DEV workspace preview",
    "Просмотр защищенного файла": "Protected file preview",
    "Профиль LLM": "LLM profile",
    "Публикация": "Publication",
    "Рабочее место для сценариев Builder": "Workspace for Builder scenarios",
    "Разговор": "Conversation",
    "Разговор — Builder": "Conversation — Builder",
    "Редактор/просмотр файла": "File editor/preview",
    "Сверить": "Compare",
    "Сделать текущей": "Make current",
    "Синхронизация": "Synchronization",
    "Сканируйте на другом устройстве": "Scan on another device",
    "Создать": "Create",
    "Создать проект": "Create project",
    "Сообщение": "Message",
    "Состав проекта": "Project contents",
    "Состояние": "State",
    "Состояние проекции": "Projection state",
    "Сохранить": "Save",
    "Ссылка на итерацию": "Iteration reference",
    "Стабилизировать": "Stabilize",
    "Стадия ошибки": "Failure stage",
    "Стандартный": "Standard",
    "Статус": "Status",
    "Строгий": "Strict",
    "Сценарий": "Scenario",
    "ТЗ": "Specification",
    "Творческий": "Creative",
    "Текст": "Text",
    "Тип": "Type",
    "Тип задаётся при создании проекта и после этого не изменяется.": "The type is set when the project is created and cannot be changed later.",
    "Тип проекта": "Project type",
    "Удаление проекта": "Delete project",
    "Удалить": "Delete",
    "Удалить проект в Forge? Локальная DEV-копия будет сохранена.": "Delete the project in Forge? The local DEV copy will be preserved.",
    "Утверждённый implementation brief": "Approved implementation brief",
    "Уточнение или повтор после ошибки": "Clarification or retry after an error",
    "Файлы проекта": "Project files",
    "Что делать": "What to do",
    "Что произойдет": "What will happen",
    "Шаблон": "Template",
    "Этап": "Stage",
    "Этап выполнения": "Execution phase",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source(name: str, **params: Any) -> dict[str, Any]:
    return {"kind": "skill", "name": f"{CONTROL}.{name}", "params": params}


def _translation_key(text: str, english: str, used: dict[str, str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", english.lower()).strip(".")[:72] or "text"
    key = f"builder.text.{slug}"
    if key in used and used[key] != text:
        key = f"{key}.{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"
    used[key] = text
    return key


def _add_i18n(application: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    ru: dict[str, str] = {}
    en: dict[str, str] = {}
    used: dict[str, str] = {}
    text_keys: dict[str, str] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for field, raw in list(value.items()):
            if field == "initialState" or field.endswith("_i18n"):
                continue
            if field in TEXT_FIELDS and isinstance(raw, str) and raw and not raw.startswith("$"):
                english = RU_EN.get(raw, raw)
                if any(ord(char) > 127 for char in raw) and raw not in RU_EN:
                    raise ValueError(f"missing Builder en translation: {raw!r}")
                key = text_keys.get(raw)
                if not key:
                    key = _translation_key(raw, english, used)
                    text_keys[raw] = key
                    ru[key] = raw
                    en[key] = english
                value[f"{field}_i18n"] = {"key": key}
            visit(raw)

    visit(application)
    return ru, en


def _apply_runtime_read_policies(value: Any) -> None:
    if isinstance(value, dict):
        data_source = value.get("dataSource")
        if isinstance(data_source, dict) and data_source.get("kind") == "skill":
            tags = DATA_SOURCE_INVALIDATION_TAGS.get(str(data_source.get("name") or ""))
            if tags:
                data_source["cacheTtlMs"] = 0
                data_source["invalidationTags"] = list(tags)
                data_source["preserveLastValue"] = True
        if value.get("type") == "callSkill":
            tags = ACTION_INVALIDATION_TAGS.get(str(value.get("target") or ""))
            if tags:
                value["invalidates"] = list(tags)
        for nested in value.values():
            _apply_runtime_read_policies(nested)
    elif isinstance(value, list):
        for nested in value:
            _apply_runtime_read_policies(nested)


def build() -> None:
    base = _read(REVISION_031)
    before = copy.deepcopy(base["after_webui"])
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

    ru, en = _add_i18n(app)
    ru.update(
        {
            "scenario.builder.title": "Builder — рабочее место разработки",
            "builder.project_type.scenario": "Сценарий",
            "builder.project_type.skill": "Навык",
            "builder.project_stage.archive": "Архив",
            "builder.project_stage.prototype": "Прототип",
            "builder.project_sync.current": "Текущий",
            "builder.project_sync.available_dev": "Доступен в DEV",
            "builder.lifecycle.status.current": "текущая",
            "builder.lifecycle.status.previous": "предыдущая",
            "builder.lifecycle.status.active": "активна",
            "builder.lifecycle.status.not_started": "не начата",
            "builder.lifecycle.stage.prototype": "Прототип",
            "builder.lifecycle.stage.automation": "Автоматизация",
            "builder.lifecycle.stage.publication": "Публикация",
        }
    )
    en.update(
        {
            "scenario.builder.title": "Builder — development workspace",
            "builder.project_type.scenario": "Scenario",
            "builder.project_type.skill": "Skill",
            "builder.project_stage.archive": "Archive",
            "builder.project_stage.prototype": "Prototype",
            "builder.project_sync.current": "Current",
            "builder.project_sync.available_dev": "Available in DEV",
            "builder.lifecycle.status.current": "current",
            "builder.lifecycle.status.previous": "previous",
            "builder.lifecycle.status.active": "active",
            "builder.lifecycle.status.not_started": "not started",
            "builder.lifecycle.stage.prototype": "Prototype",
            "builder.lifecycle.stage.automation": "Automation",
            "builder.lifecycle.stage.publication": "Publication",
        }
    )
    resources = {
        "builder.i18n.en": {
            "kind": "data",
            "role": "i18n",
            "locale": "en",
            "path": "assets/i18n/en.json",
            "mime": "application/json",
            "delivery": "core",
        },
        "builder.i18n.ru": {
            "kind": "data",
            "role": "i18n",
            "locale": "ru",
            "path": "assets/i18n/ru.json",
            "mime": "application/json",
            "delivery": "core",
        },
    }
    webui["resources"] = copy.deepcopy(resources)
    app["resources"] = copy.deepcopy(resources)
    _apply_runtime_read_policies(webui)
    _write(SCENARIO / "assets" / "i18n" / "ru.json", ru)
    _write(SCENARIO / "assets" / "i18n" / "en.json", en)

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
