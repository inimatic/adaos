"""Builder-owned migration from accepted design 071 to live SDK bindings.

This is application implementation, never a scenario template or LLM context.
The accepted archive and operational Workspace edition are read-only inputs.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import yaml

from scripts import build_builder_workbench_prototype as design

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos/dev/sn_6acf0c01/scenarios/builder"
IDENTITY = {"object_type": "$state.selectedProjectKind", "object_id": "$state.selectedProjectId",
            "_meta": {"current_scenario": "builder"}}
TAGS = ["builder.project.metadata", "builder.project.lifecycle", "builder.project.preview",
        "builder.project.automation", "builder.project.publication", "builder.project.change"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def normalize_slots(value):
    for node in walk(value):
        for key in ("widgets", "areas", "actions", "fields", "buttons", "columns"):
            item = node.get(key)
            if isinstance(item, dict) and ("id" in item or "on" in item or "key" in item):
                node[key] = [item]
    return value


def source(name, **params):
    return {"kind": "skill", "name": "builder_sdk_control_skill." + name,
            "params": {**IDENTITY, **params}, "cacheTtlMs": 0,
            "invalidationTags": TAGS, "preserveLastValue": True, "maxRequestHz": 0.2}


def call(on, name, **params):
    return {"on": on, "type": "callSkill", "target": "builder_sdk_control_skill." + name,
            "params": {**IDENTITY, **params}, "invalidates": [*TAGS, "builder.project.files", "builder.project.llm"]}


def table(identifier, title, title_en, value, columns, *, view, detailed=False):
    visible = f"$state.workbenchView === '{view}'"
    if detailed:
        visible += " && $state.viewProfile === 'detailed'"
    return {"id": identifier, "type": "ui.table", "area": "main",
            **design.text_field("title", title, title_en), "visibleIf": visible,
            "dataSource": design.source(value), "inputs": {"columns": columns, "pagination": True,
            "pageSize": 10, "pageSizeOptions": [10, 25, 50], "search": True, "rowKey": "id"}}


def build():
    accepted = read(SCENARIO / "ui_revisions/071.json")["after_webui"]
    old = normalize_slots(read(ROOT / ".adaos/workspace/scenarios/builder/webui.json"))
    live = copy.deepcopy(accepted)
    live["resources"] = {**old.get("resources", {}), **live.get("resources", {})}
    for locale in ("en", "ru"):
        live["resources"][f"builder.live.i18n.{locale}"] = {"kind": "data", "role": "i18n", "locale": locale,
            "path": f"assets/i18n/workbench-live-{locale}.json", "mime": "application/json", "delivery": "core"}
    live["generated_by"] = "builder-owned-live-implementation"
    live["ui"].pop("user_summary", None)
    live["ui"]["version"] = str(yaml.safe_load((SCENARIO / "scenario.yaml").read_text(encoding="utf-8"))["version"])
    application = live["ui"]["application"]
    application["resources"] = copy.deepcopy(live["resources"])
    page = application["desktop"]["pageSchema"]
    old_app = old["ui"]["application"]
    old_page = old_app["desktop"]["pageSchema"]
    old_widgets = {node["id"]: node for node in old_page["widgets"]}
    specimens = {node["id"]: node for node in page["widgets"]}
    modals = copy.deepcopy(old_app["modals"])
    state = copy.deepcopy(old_page["initialState"])
    for key in list(state):
        if key.startswith("research"):
            del state[key]
    state.update({"selectedProjectKind": "project", "selectedProjectId": "builder",
                  "selectedProjectRef": "project:builder", "selectedProjectTitle": "Builder",
                  "applicationTitle": "Builder", "workbenchView": "result", "viewProfile": "basic",
                  "conversationMode": "task", "current": {}, "workbench": {}, "commands": {},
                  "selectedFilePath": None, "previewRevision": "", "inspectedRevision": "",
                  "projectPickerSample": "all", "projectPickerQuery": "", "selectedTrace": {},
                  "designSettings": {"diagnostics": False}, "informalNote": ""})
    page["initialState"] = state
    page["initialStateSource"] = copy.deepcopy(old_page["initialStateSource"])
    page["meta"]["builder"] = {"scenario_id": "builder", "functional": True,
                                "binding_mode": "skill", "workflow_contract": "adaos.builder.workflow.v1",
                                "accepted_design_revision": "071", "live_commands": True,
                                "qualification": "pending"}
    header = copy.deepcopy(specimens["design-workbench-header"])
    header["inputs"]["statusDataSource"] = design.source({
        "title": "$state.applicationTitle", "state": "$state.current.summary",
        "state_i18n": "$state.current.summary_i18n",
        "target_label": "$state.workbench.revision_label"})
    for button in header["inputs"]["buttons"]:
        if button["id"] == "specimens":
            button.pop("options", None)
            button.pop("selectedStateKey", None)
            button.pop("optionMetaPaths", None)
            button.update(design.text_field("label", "Процесс", "Process"))
        if button["id"] == "applications":
            button.pop("label_i18n", None)
    header["actions"] = [action for action in header["actions"] if not action["on"].startswith("select:specimens")]
    for action in header["actions"]:
        if action["type"] == "openModal":
            action["params"]["modalId"] = {"design-applications": "project-picker", "design-history": "process"}.get(
                action["params"]["modalId"], action["params"]["modalId"])
        if action["type"] == "openDevTickets":
            action["params"] = {"target_scope": {"type": "$state.selectedProjectKind", "id": "$state.selectedProjectId", "source": "dev"}}
    header["actions"].append(design.modal_action("click:specimens", "process"))

    current = copy.deepcopy(specimens["design-current-work"])
    current["dataSource"] = source("get_workbench")
    current["inputs"]["fields"] = [
        design.field("current.phase", "Этап", "Stage", valueI18nPrefix="builder.workbench.phase."),
        design.field("current.summary", "Сейчас", "Current state", valueI18nPrefix="builder.workbench.state.")]
    current["inputs"]["stateBindings"] = {"workbench": "view", "current": "current", "commands": "commands",
        "applicationTitle": "title", "selectedProjectTitle": "title", "workflowGeneration": "workflow_generation",
        "project.title": "title", "project.description": "description", "project.type": "object_type",
        "projectArchived": "archived",
        "workflowActivePhase": "phase", "canEditPrototype": "can_edit_prototype", "canEditAutomation": "can_edit_automation",
        "canReturnToPrototype": "can_return_to_prototype", "canPrepareCandidate": "can_prepare_candidate",
        "canAcceptCandidate": "can_decide_candidate", "canPublish": "can_publish",
        "canStartImplementation": "can_start_implementation", "projectAvailabilityState": "availability_state",
        "builderConversationId": "conversation_id", "builderTopicId": "topic_id", "builderThreadId": "thread_id"}
    primary_buttons = [
        design.button("correct", "Задание", "Task", "create-outline", enabledIf="$state.projectAvailabilityState === 'available'"),
        design.button("accept", "Принять прототип", "Accept prototype", "checkmark-outline", visibleIf="$state.commands.accept_prototype"),
        design.button("implement", "Автоматизация", "Automation", "construct-outline", visibleIf="$state.commands.start_automation || $state.commands.retry_automation"),
        design.button("checkpoint", "Принять проверку", "Accept verification", "shield-checkmark-outline", visibleIf="$state.commands.accept_verification"),
        design.button("publication", "Поставка", "Delivery", "cube-outline", visibleIf="$state.canPrepareCandidate || $state.canPublish"),
        design.button("return", "Вернуться к прототипу", "Return to prototype", "return-up-back-outline", visibleIf="$state.canReturnToPrototype"),
    ]
    primary = design.toolbar("design-primary-actions", primary_buttons, [
        design.update("click:correct", workbenchView="conversation", conversationMode="task"),
        design.modal_action("click:accept", "prototype-review"), design.modal_action("click:implement", "automation"),
        design.modal_action("click:checkpoint", "design-checkpoint"), design.modal_action("click:publication", "publication"),
        design.modal_action("click:return", "design-return")])
    tabs = copy.deepcopy(specimens["design-workbench-views"])
    widgets = [header, current, primary, tabs]
    preview = design.details("design-revision-identity", "Результат", "Result", {}, [
        design.field("viewing_label", "Preview", "Preview"), design.field("working_label", "Рабочая редакция", "Working edition"),
        design.field("viewing_read_only", "Историческая редакция", "Historical edition")], visible="$state.workbenchView === 'result'")
    preview["dataSource"] = source("get_project")
    preview["inputs"]["fields"] = preview["inputs"]["fields"][:2]
    widgets += [preview, design.toolbar("design-preview-commands", [
        design.button("open", "Открыть Preview", "Open preview", "open-outline"),
        design.button("qr", "QR", "QR", "qr-code-outline"),
        design.button("history", "Редакции", "Revisions", "git-branch-outline")],
        [call("click:open", "open_preview"), design.modal_action("click:qr", "preview-qr"),
         design.modal_action("click:history", "design-revisions")], visible="$state.workbenchView === 'result'")]
    widgets[-1]["actions"][0].update(openResultUrl=True, resultUrlPath="preview_url", resultPreferCurrentOrigin=True)
    automation_status = copy.deepcopy(next(w for w in modals["automation"]["schema"]["widgets"] if w["id"] == "automation-state"))
    automation_status.update(id="design-result-automation", visibleIf="$state.workbenchView === 'result' && $state.workflowActivePhase === 'automation'")
    automation_status["inputs"]["presentation"] = "section"
    widgets.append(automation_status)
    brief = design.details("design-task-brief", "Состав изменения", "Change scope", "$state.workbench", [
        design.field("request", "Запрос", "Request"), design.field("change_id", "Изменение", "Change"),
        design.field("source_message_ids", "Исходные сообщения", "Source messages")], visible="$state.workbenchView === 'brief'")
    widgets.append(brief)
    issues = table("design-scope-requirements", "Задачи изменения", "Change issues", "$state.workbench.issues", [
        design.field("title", "Требование", "Requirement"), design.field("stage", "Этап", "Stage"),
        design.field("status", "Статус", "Status"), design.field("sources", "Источники", "Sources")], view="brief")
    issues["actions"] = [design.update("select", selectedTrace="$event"), design.modal_action("select", "design-trace")]
    widgets.append(issues)
    widgets.append(design.toolbar("design-scope-sources", [
        design.button("spec", "Спецификация", "Specification", "document-text-outline"),
        design.button("delta", "Изменение требований", "Requirement delta", "git-compare-outline"),
        design.button("context", "Контекст", "Context", "code-slash-outline")],
        [design.modal_action("click:spec", "design-specification"), design.modal_action("click:delta", "design-delta"),
         design.modal_action("click:context", "design-context")],
        visible="$state.workbenchView === 'brief' && $state.viewProfile === 'detailed'"))
    checks = table("design-checks", "Проверки", "Checks", "$state.workbench.checks", [
        design.field("stage", "Этап", "Stage"),
        design.field("name", "Проверка", "Check"), design.field("result", "Результат", "Result"),
        design.field("scope", "Редакция", "Revision")], view="checks")
    checks["dataSource"] = source("get_review")
    checks["actions"] = [design.update("select", selectedTrace="$event"), design.modal_action("select", "design-trace")]
    widgets.append(checks)
    runs = table("design-trace-tasks", "Запуски", "Runs", "$state.workbench.runs", [
        design.field("title", "Запуск", "Run"), design.field("stage", "Этап", "Stage"), design.field("status", "Статус", "Status")], view="process")
    runs["actions"] = copy.deepcopy(issues["actions"])
    widgets.append(runs)
    tree = copy.deepcopy(modals["process"]["schema"]["widgets"][0])
    tree.update(id="design-process", visibleIf="$state.workbenchView === 'process'")
    widgets.append(tree)
    files = copy.deepcopy(modals["file-picker"]["schema"]["widgets"])
    for item in files:
        item["visibleIf"] = "$state.workbenchView === 'files'"
    files[0]["id"] = "design-component-picker"
    files[1]["id"] = "design-file-tree"
    files[1]["actions"] = [action for action in files[1]["actions"] if action["type"] != "closeModal"]
    files[1]["actions"].append(design.modal_action("select", "design-file-viewer"))
    widgets.extend(files)
    for key in ("technical-spec-editor", "technical-spec-actions", "technical-spec-addenda"):
        item = copy.deepcopy(old_widgets[key])
        item.update(area="main", visibleIf="$state.workbenchView === 'inputs'")
        widgets.append(item)
    for key in ("publication-workspace-status", "publication-workspace-actions", "publication-workspace-history",
                "overview-subscription-update", "overview-subscription-update-actions"):
        if key in old_widgets:
            item = copy.deepcopy(old_widgets[key])
            item.update(area="main", visibleIf="$state.workbenchView === 'deliveries'")
            widgets.append(item)
    for key, item in old_widgets.items():
        if key.startswith("development-feedback-"):
            item = copy.deepcopy(item)
            item.update(area="main")
            item["visibleIf"] = str(item.get("visibleIf", "$state.activeView === 'feedback'")).replace("$state.activeView", "$state.workbenchView").replace("'feedback'", "'development-feedback'")
            widgets.append(item)
    readme = design.details("design-readme", "README", "README", {}, [design.field("text", "README.md", "README.md", kind="markdown")], visible="$state.workbenchView === 'readme'")
    readme["dataSource"] = source("read_readme")
    readme["inputs"]["stateBindings"] = {"readmeText": "text", "readmeDigest": "digest"}
    widgets += [readme, design.toolbar("design-readme-actions", [design.button("edit", "Редактировать", "Edit", "create-outline")],
                                      [design.modal_action("click:edit", "design-readme-editor")], visible="$state.workbenchView === 'readme'")]
    for position, area, visible in [("side", "conversation", "$state.workbenchView !== 'conversation'"), ("full", "main", "$state.workbenchView === 'conversation'")]:
        controls = copy.deepcopy(specimens[f"design-conversation-controls-{position}"])
        options = controls["inputs"]["buttons"][0]["options"]
        options[0].update(design.text_field("label", "Задание", "Task"))
        controls["actions"] = [action for action in controls["actions"] if action["on"] != "click:trace-messages"]
        controls["actions"].append(design.modal_action("click:trace-messages", "design-trace-messages"))
        widgets.append(controls)
        chat = copy.deepcopy(old_widgets["builder-chat"])
        chat.update(id=f"design-conversation-{position}-task", area=area,
                    visibleIf=visible + " && $state.conversationMode === 'task'")
        chat["inputs"].pop("hint", None)
        chat["inputs"]["invalidateOnMessages"] = TAGS
        widgets.append(chat)
        informal = {"id": f"design-conversation-{position}-informal", "type": "ui.chat", "area": area,
                    "visibleIf": visible + " && $state.conversationMode === 'informal'",
                    "dataSource": source("get_workbench_messages", mode="informal"),
                    "inputs": {"compact": True, "showTimestamps": True}}
        widgets.append(informal)
        composer = design.form(f"design-composer-{position}-informal", [
            design.label("Заметка", "Note", id="text", type="longText", required=True)], "Добавить заметку", "Add note",
            [call("submit", "append_discussion", text="$event.values.text")], area=area)
        composer["visibleIf"] = informal["visibleIf"]
        widgets.append(composer)
    page["widgets"] = widgets
    application["modals"] = modals
    revisions = copy.deepcopy(old_widgets["project-tree"])
    revisions.update(area="main")
    revisions.pop("visibleIf", None)
    modals["design-revisions"] = design.modal("design-revisions", "Редакции", "Revisions", [revisions])
    for action in header["actions"]:
        if action["on"] == "click:history":
            action["params"]["modalId"] = "design-revisions"

    # Retain the mature picker and create flow; only reset context owned by the new view.
    picker = next(w for w in modals["project-picker"]["schema"]["widgets"] if w["id"] == "project-picker-table")
    picker["dataSource"]["params"]["limit"] = 500
    for action in picker["actions"]:
        if action["type"] == "updateState":
            action["params"].update({"applicationTitle": "$event.title", "workbenchView": "result",
                "selectedTrace": {}, "selectedFilePath": None})
    for item in modals["new-project"]["schema"]["widgets"]:
        if item["id"] == "new-project-form":
            item["inputs"]["fields"].insert(1, design.label(
                "Название приложения", "Application name", id="title", type="shortText", required=True))
        for action in item.get("actions", []):
            if action.get("target") == "builder_sdk_control_skill.create_project":
                action["params"]["title"] = "$event.values.title"
            if action["type"] == "updateState" and action["on"] == "submit":
                action["params"].update({"workbenchView": "result",
                    "applicationTitle": "$event.values.title", "selectedProjectTitle": "$event.values.title",
                    "selectedTrace": {}})
    modals["design-trace"] = design.modal("design-trace", "Трассировка", "Trace", [design.details(
        "design-trace-record", "Запись", "Record", "$state.selectedTrace", [
            design.field("title", "Название", "Title"), design.field("id", "ID", "ID"),
            design.field("status", "Статус", "Status"), design.field("source_message_ids", "Сообщения", "Messages"),
            design.field("acceptance_criteria", "Критерии", "Acceptance criteria"),
            design.field("evidence_refs", "Доказательства", "Evidence"), design.field("input_refs", "Исходные данные", "Inputs")])])
    modals["design-context"] = design.modal("design-context", "Контекст", "Context", [design.details(
        "design-context-details", "Контекст изменения", "Change context", "$state.workbench", [
            design.field("change_id", "Изменение", "Change"), design.field("context_digest", "Снимок входных данных", "Input snapshot"),
            design.field("initiator", "Инициатор", "Initiator"), design.field("blockers", "Ограничения", "Blockers")])])
    modals["design-specification"] = design.modal("design-specification", "Спецификация", "Specification", [design.details(
        "design-specification-base", "Принятые требования", "Accepted requirements", "$state.workbench.specification", [
            design.field("prototype", "Прототип", "Prototype"), design.field("automation", "Автоматизация", "Automation")])])
    delta = design.form("design-delta-form", [design.label("Изменение требований (JSON)", "Requirement delta (JSON)",
        id="text", type="longText", default="$state.workbench.specification_delta_text", required=True)], "Сохранить", "Save", [
            call("submit", "save_specification_delta", text="$event.values.text", change_id="$state.workbench.change_id",
                 expected_generation="$state.workflowGeneration")])
    modals["design-delta"] = design.modal("design-delta", "Изменение требований", "Requirement delta", [delta])
    file_viewer = design.details("design-file-content", "Файл", "File", {}, [design.field("content", "Содержимое", "Content")])
    file_viewer["dataSource"] = source("read_project_file", object_type="$state.selectedObjectKind", object_id="$state.selectedObjectId", path="$state.selectedFilePath")
    modals["design-file-viewer"] = design.modal("design-file-viewer", "Файл", "File", [file_viewer])
    readme_editor = design.form("design-readme-form", [design.label("README.md", "README.md", id="text", type="longText", default="$state.readmeText")],
                               "Сохранить", "Save", [call("submit", "save_readme", text="$event.values.text", expected_digest="$state.readmeDigest")])
    modals["design-readme-editor"] = design.modal("design-readme-editor", "README.md", "README.md", [readme_editor])
    settings = copy.deepcopy(old_widgets["chat-side-settings"])
    settings.update(area="main")
    settings.pop("visibleIf", None)
    settings["inputs"]["fields"] = [field for field in settings["inputs"]["fields"] if field["id"] == "llmModel"]
    settings["inputs"]["fields"][0].update({"options": [], "optionsDataSource": source("get_prototype_model_choices"), "optionLabelPaths": ["label"]})
    codex = design.form("design-codex-settings", [
        design.label("Модель Codex", "Codex model", id="model", type="select", required=True,
                     optionsDataSource=source("get_codex_options"), optionLabelPaths=["label"]),
        design.label("Интенсивность", "Reasoning effort", id="reasoning_effort", type="select", default="$state.codexEffort",
                     options=[{"value": value, "label": value} for value in ("low", "medium", "high", "xhigh")])],
        "Сохранить Codex", "Save Codex", [call("submit", "set_codex_profile", model="$event.values.model", reasoning_effort="$event.values.reasoning_effort")])
    model_current = design.details("design-effective-models", "Выбранные модели", "Selected models", {}, [
        design.field("execution_ref", "Компонент исполнения", "Execution component"),
        design.field("prototype_model", "Прототип", "Prototype"), design.field("codex_model", "Codex", "Codex"),
        design.field("codex_effort", "Интенсивность", "Reasoning effort")])
    model_current["dataSource"] = source("get_model_settings")
    model_current["inputs"]["stateBindings"] = {"codexModel": "codex_model", "codexEffort": "codex_effort"}
    codex["inputs"]["fields"][0]["default"] = "$state.codexModel"
    settings_widgets = [model_current, settings, codex]
    for key in ("node-overview", "overview-project-state", "builder-development-session"):
        metadata = copy.deepcopy(old_widgets[key])
        metadata.update(area="main")
        metadata.pop("visibleIf", None)
        settings_widgets.append(metadata)
    for key in ("overview-archive", "overview-restore"):
        if key in old_widgets:
            item = copy.deepcopy(old_widgets[key])
            item.update(area="main")
            item["visibleIf"] = "$state.projectArchived !== true" if key == "overview-archive" else "$state.projectArchived === true"
            settings_widgets.append(item)
    modals["design-settings"] = design.modal("design-settings", "Настройки", "Settings", settings_widgets)
    checkpoint = design.form("design-checkpoint-form", [design.label("Комментарий", "Comment", id="message", type="longText")],
                             "Принять проверку", "Accept verification", [call("submit", "push_project", confirmed=True,
                             message="$event.values.message", checkpoint_id="$state.workbench.change_id")])
    modals["design-checkpoint"] = design.modal("design-checkpoint", "Принять проверку", "Accept verification", [checkpoint])
    return_form = design.toolbar("design-return-form", [design.button("confirm", "Вернуться к прототипу", "Return to prototype", "return-up-back-outline")],
                                 [call("click:confirm", "return_to_prototype")])
    modals["design-return"] = design.modal("design-return", "Вернуться к прототипу", "Return to prototype", [return_form])
    messages = {"id": "design-source-messages", "type": "ui.chat", "area": "main", "dataSource": source("get_workbench_messages"),
                "inputs": {"compact": True, "showTimestamps": True}}
    modals["design-trace-messages"] = design.modal("design-trace-messages", "Исходные сообщения", "Source messages", [messages])
    modals["design-conversation-channels"] = design.modal("design-conversation-channels", "Канал обсуждения", "Conversation channel", [design.details(
        "design-conversation-route", "Привязка", "Binding", "$state.workbench", [
            design.field("conversation_id", "Диалог", "Conversation"), design.field("thread_id", "Тема задания", "Task thread")])])
    phases = {"prototype": ("Прототип", "Prototype"), "automation": ("Автоматизация", "Automation"),
              "publication": ("Публикация", "Publication")}
    statuses = {"prototype_editing": ("Доработка прототипа", "Prototype in progress"),
                "prototype_review": ("Ожидается проверка прототипа", "Prototype review needed"),
                "automation_ready": ("Готово к автоматизации", "Ready for automation"),
                "verification_review": ("Ожидается проверка реализации", "Implementation review needed"),
                "trial_ready": ("Реализация зафиксирована", "Implementation checkpointed"),
                "trial_review": ("Ожидается решение по Beta", "Beta review needed"),
                "publication_ready": ("Готово к публикации", "Ready for publication"),
                "published": ("Опубликовано", "Published")}
    for prefix, labels in (("phase", phases), ("state", statuses)):
        for key, (ru, en) in labels.items():
            design.LOCALES["ru"][f"builder.workbench.{prefix}.{key}"] = ru
            design.LOCALES["en"][f"builder.workbench.{prefix}.{key}"] = en
    return normalize_slots(live)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = build()
    output = ROOT / "e2e/artifacts/builder/workbench-automation-20260914/live-candidate.json"
    design.write(output, result)
    if args.apply:
        design.write(SCENARIO / "assets/builder_functional_parity.json",
                     read(ROOT / "docs/architecture/builder-functional-parity.json"))
        for locale, translations in design.LOCALES.items():
            path = SCENARIO / f"assets/i18n/workbench-live-{locale}.json"
            design.write(path, translations)
        design.write(SCENARIO / "webui.json", result)
        scenario = read(SCENARIO / "scenario.json")
        manifest = yaml.safe_load((SCENARIO / "scenario.yaml").read_text(encoding="utf-8"))
        scenario["ui"] = {**copy.deepcopy(result["ui"]), "manifest": "webui.json"}
        scenario["version"] = str(manifest["version"])
        scenario["updated_at"] = str(manifest["updated_at"])
        design.write(SCENARIO / "scenario.json", scenario)
    print(json.dumps({"output": str(output), "applied": args.apply, "widgets": len(result["ui"]["application"]["desktop"]["pageSchema"]["widgets"])}))


if __name__ == "__main__":
    main()
