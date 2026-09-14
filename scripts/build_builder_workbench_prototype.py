"""Author a Builder-owned design prototype, not a generic generation template.

All process commands in this specimen change local presentation state only.
The retained baseline is a new observation of today's source, not reconstructed
historical evidence. Workspace source and installed runtimes are read-only.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos/dev/sn_6acf0c01/scenarios/builder"
OUTPUT = ROOT / "e2e/artifacts/builder/workbench-design-20260914"
LOCALES: dict[str, dict[str, str]] = {"ru": {}, "en": {}}
REVISION = "066"
REQUEST = (
    "Design a new DEV Builder prototype for human review, based on the target "
    "Builder workflow and SOTA interaction patterns. Preserve the operational "
    "Workspace Builder. Model current work, clarification, exact-revision "
    "review, partial results, failure, recovery, context inspection and history. "
    "Do not implement Automation, create Trials, publish, or invoke an LLM from "
    "the design specimen. Do not accept the new design on behalf of the user. "
    "Revision 066: compact process menu; visible revision identity; task actors "
    "and milestones; separate reference inputs from generated file trees; batch "
    "clarification with retained drafts; explicit local Alpha, Beta and Stable "
    "delivery decisions. All delivery records and transitions remain fixtures."
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tr(key, ru, en):
    token = "builder.design." + key
    LOCALES["ru"][token], LOCALES["en"][token] = ru, en
    return {"key": token, "fallback": ru}


def text_field(property_name, ru, en, **extra):
    return {**extra, property_name: ru, property_name + "_i18n": tr(ru, ru, en)}


def label(ru, en, **extra):
    return text_field("label", ru, en, **extra)


def source(value):
    return {"kind": "static", "value": value}


def update(on, **patch):
    return {"on": on, "type": "updateState", "params": patch}


def modal_action(on, target):
    return {"on": on, "type": "openModal", "params": {"modalId": target}}


def feedback(on):
    return {"on": on, "type": "openDevTickets", "params": {
        "surface": "builder_design_review", "design_revision": REVISION,
        "specimen_state": "$state.current.id", "specimen_preview_revision": "$state.previewRevision",
        "target_scope": {"type": "scenario", "id": "builder", "source": "dev",
                         "project_id": "builder", "scenario_id": "builder", "revision": REVISION},
    }}


def choose(condition, yes, no):
    return {"kind": "expression", "op": "if", "condition": condition, "then": yes, "else": no}


def equals(left, right):
    return {"kind": "expression", "op": "equals", "args": [left, right]}


def button(id, ru, en, icon="", **extra):
    return label(ru, en, id=id, icon=icon, **extra)


def toolbar(id, buttons, actions, *, area="main", visible=None, **inputs):
    value = {"id": id, "type": "ui.actions", "area": area,
             "inputs": {"variant": "adaptiveToolbar", "size": "small", "buttons": buttons, **inputs},
             "actions": actions}
    if visible:
        value["visibleIf"] = visible
    return value


def details(id, ru, en, value, fields, *, area="main", visible=None, **inputs):
    value = {"id": id, "type": "item.details", "area": area,
             **text_field("title", ru, en), "dataSource": source(value),
             "inputs": {"presentation": "section", "fields": fields, **inputs}}
    if visible:
        value["visibleIf"] = visible
    return value


def field(key, ru, en, **extra):
    return label(ru, en, key=key, **extra)


def form(id, fields, submit_ru, submit_en, actions, *, area="main"):
    return {"id": id, "type": "ui.form", "area": area,
            "inputs": {"fields": fields, "showSubmit": True,
                       **text_field("submitLabel", submit_ru, submit_en)}, "actions": actions}


def modal(id, ru, en, widgets):
    return {**text_field("title", ru, en), "presentation": {"kind": "modal"},
            "schema": {"id": id, "layout": {"type": "stack", "areas": [{"id": "main", "role": "main"}]},
                       "widgets": widgets}}


def specimens():
    definitions = [
        ("planning", "Понимание задачи", "Task understanding", "Готов к прототипированию", "Ready to prototype",
         "Уточняем форму редактирования объекта, сохраняя выбор осмотра.", "Add asset editing while preserving inspection selection.",
         "Не начато", "Not started"),
        ("working", "Создание прототипа", "Creating prototype", "Выполняется", "Working",
         "Форма объекта подготовлена. Проверяются переходы и сохранность выбора.", "Asset form prepared. Checking navigation and selection preservation.",
         "1 мин 42 с · последнее событие 8 с назад", "1m 42s · last event 8s ago"),
        ("input", "Уточнение автоматизации", "Implementation clarification", "Нужен ваш ответ", "Your input needed",
         "Для уведомлений не выбран канал и получатель. Исполнение ожидает решения.", "Notification channel and recipient are missing. Execution awaits your decision.",
         "Ожидание человека · расход модели остановлен", "Waiting for you · model spending stopped"),
        ("review", "Редактирование объекта", "Asset editing", "Готово к просмотру", "Ready for review",
         "Добавлена отдельная команда редактирования. Клик по объекту сохраняет выбор осмотров.", "Added a separate edit command. Selecting an asset still shows its inspections.",
         "2 мин 14 с · результат сохранен", "2m 14s · result saved"),
        ("accepted", "Прототип согласован", "Prototype accepted", "Готов к автоматизации", "Ready for implementation",
         "Прототип 003 зафиксирован. Автоматизация еще не запущена.", "Prototype 003 is frozen. Implementation has not started.",
         "Ожидание запуска", "Awaiting start"),
        ("verifying", "Проверка автоматизации", "Verifying implementation", "Проверяется", "Verifying",
         "Исполнитель завершил работу. Независимая проверка сохранения данных еще идет.", "The worker has finished. Independent persistence checks are still running.",
         "4 мин 08 с · проверка после перезапуска", "4m 08s · checking after restart"),
        ("implementation_review", "Автоматизация проверена", "Implementation verified", "Нужна приемка", "Acceptance needed",
         "Данные сохраняются после перезапуска. Согласованные формы и навигация сохранены.", "Data survives restart. Approved forms and navigation are preserved.",
         "5 мин 02 с · проверки завершены", "5m 02s · checks completed"),
        ("completed", "Автоматизация принята", "Implementation accepted", "Принято в DEV", "Accepted in DEV",
         "Принята проверенная реализация на основе прототипа 003. В Workspace ничего не опубликовано.", "Verified implementation based on prototype 003 accepted. Nothing published to Workspace.",
         "Ожидание следующей задачи", "Awaiting the next task"),
        ("partial", "Автоматизация с ограничением", "Partial implementation", "Частичный результат", "Partial result",
         "Создание и редактирование работают. Доставка уведомлений не реализована; полная приемка недоступна.", "Create and edit work. Notification delivery is unresolved; full acceptance is unavailable.",
         "5 мин 10 с · требуется решение", "5m 10s · decision needed"),
        ("failed", "Проверка не пройдена", "Verification failed", "Нужны исправления", "Changes needed",
         "После перезапуска исчезает изменение объекта. Принятый прототип и предыдущая реализация сохранены.", "Asset edits disappear after restart. The accepted prototype and prior implementation are retained.",
         "3 мин 28 с · попытка сохранена", "3m 28s · attempt retained"),
        ("offline", "Связь потеряна", "Connection lost", "Актуальность неизвестна", "Freshness unknown",
         "Показан последний полученный результат. Потеря связи не означает остановку исполнителя.", "Showing the last received result. Disconnection does not mean the worker stopped.",
         "Последнее подтверждение: 14:32:08", "Last confirmation: 14:32:08"),
        ("cancelled", "Работа остановлена", "Work stopped", "Остановлено", "Stopped",
         "Новые действия не выполняются. Уже созданный кандидат сохранен, изменений в Workspace нет.", "No further actions will run. The candidate is retained; Workspace is unchanged.",
         "Остановка подтверждена", "Stop acknowledged"),
    ]
    result = {}
    definitions.extend([
        ("beta_ready", "Beta подготовлена", "Beta prepared", "Нужно решение", "Decision needed",
         "Кандидат 1.4.0-beta.1 готов к локальной апробации. Установка и публикация еще не разрешены.", "Candidate 1.4.0-beta.1 is ready for local testing. Installation and publication are not authorized.",
         "Ожидание человека", "Waiting for you"),
        ("beta_active", "Апробация Beta", "Beta testing", "Beta локально", "Local Beta",
         "1.4.0-beta.1 активна в Trials. Stable 1.3.0 сохранена; внешнего доступа к Beta нет.", "1.4.0-beta.1 is active in Trials. Stable 1.3.0 is retained; Beta has no external access.",
         "Ожидание результатов апробации", "Awaiting testing results"),
        ("stable_ready", "Выпуск Stable", "Stable release", "Beta принята", "Beta accepted",
         "Проверки Beta приняты. Переход на Stable 1.4.0 требует отдельного решения и резервной копии данных.", "Beta checks are accepted. Moving to Stable 1.4.0 requires a separate decision and a data backup.",
         "Ожидание выпуска", "Awaiting release"),
        ("stable_local", "Stable установлена", "Stable installed", "Stable локально", "Local Stable",
         "1.4.0 установлена в Workspace. Beta завершена; публикация в Marketplace не выполнена.", "1.4.0 is installed in Workspace. Beta is retired; no Marketplace publication has occurred.",
         "Ожидание разрешения публикации", "Awaiting publication permission"),
        ("stable_published", "Stable опубликована", "Stable published", "Опубликовано", "Published",
         "Publisher разрешил публикацию 1.4.0. История Beta сохранена, активный пакет Beta выведен из обращения.", "Publisher authorized publication of 1.4.0. Beta history is retained; its active package is retired.",
         "Веха завершена", "Milestone complete"),
    ])
    for id, ru, en, status_ru, status_en, summary_ru, summary_en, time_ru, time_en in definitions:
        actor = ("ИИ · задача", "AI · task", "construct-outline") if id == "working" else (
            ("Система · проверка", "System · check", "shield-checkmark-outline") if id == "verifying" else (
            ("Веха · зафиксировано", "Milestone · recorded", "checkmark-outline") if id in {"accepted", "completed", "stable_published"} else
            ("Человек · решение", "Human · decision", "person-outline")))
        item = {"id": id, "revision": "003", "run": "SPECIMEN-R17", "synthetic": True, "icon": actor[2],
                **text_field("actor", actor[0], actor[1]),
                **text_field("target_label", "Осмотр оборудования · Change 12", "Equipment inspections · Change 12")}
        for key, a, b in (("label", ru, en), ("title", ru, en), ("state", status_ru, status_en),
                          ("summary", summary_ru, summary_en), ("timing", time_ru, time_en)):
            item.update(text_field(key, a, b))
        result_state = ("Не выполнялась", "Not run") if id in {"planning", "working"} else ("Пройдена", "Passed")
        persistence = {
            "verifying": ("Выполняется", "Running"), "failed": ("Не пройдена", "Failed"),
            "partial": ("Пройдена; доставка не реализована", "Passed; delivery unresolved"),
            "offline": ("Актуальность неизвестна", "Freshness unknown"),
            "cancelled": ("Остановлена", "Stopped"),
            "implementation_review": ("Пройдена", "Passed"), "completed": ("Пройдена", "Passed"),
        }.get(id, ("На этапе автоматизации", "Implementation stage"))
        item["checks"] = [
            {"id": name, **text_field("name", ru, en), **text_field("result", *status), **text_field("scope", scope_ru, scope_en)}
            for name, ru, en, status, scope_ru, scope_en in (
                ("structure", "Структура интерфейса", "UI structure", result_state, "Прототип 003", "Prototype 003"),
                ("navigation", "Выбор, модалка и компоновка", "Selection, modal and layout", result_state, "Desktop и mobile", "Desktop and mobile"),
                ("persistence", "Хранение после перезапуска", "Persistence after restart", persistence, "Автоматизация; не прототип", "Implementation; not prototype"),
            )]
        item["process"] = [{"id": "scope", **text_field("title", "Задача · Change 12", "Task · Change 12"),
                            **text_field("preview", "Сохранить осмотры и добавить редактирование объекта.", "Preserve inspections and add asset editing.")},
                           {"id": "state", "title": item["state"], "title_i18n": item["state_i18n"],
                            "preview": item["summary"], "preview_i18n": item["summary_i18n"], "subtitle": "DEMO-R17"}]
        result[id] = item
    return result


def build_document():
    samples = specimens()
    header = toolbar("design-workbench-header", [
        button("applications", "Осмотр оборудования", "Equipment inspections", "folder-open-outline"),
        button("history", "Редакции", "Revisions", "git-branch-outline"),
        button("specimens", "Состояние процесса", "Process state", "options-outline",
               displaySelectedLabel=False, selectedStateKey="current.id", optionMetaPaths=["actor"],
               options=[{k: v[k] for k in ("id", "label", "label_i18n", "icon", "actor", "actor_i18n")} for v in samples.values()]),
        button("deliveries", "Поставки", "Deliveries", "cube-outline"),
        button("feedback", "Dev Tickets", "Dev Tickets", "ticket-outline"),
        button("settings", "Настройки", "Settings", "settings-outline"),
    ], [modal_action("click:applications", "design-applications"),
        update("select:specimens", current="$state.samples.$event.id", previewRevision="003", inspectedRevision="003"),
        update("click:deliveries", workbenchView="deliveries"),
        modal_action("click:history", "design-history"), modal_action("click:settings", "design-settings"),
        feedback("click:feedback")],
        statusDataSource=source({"title": "$state.current.title", "title_i18n": "$state.current.title_i18n",
            "state": "$state.current.state", "state_i18n": "$state.current.state_i18n",
            **text_field("target_label", f"Прототип $state.previewRevision · текущий 003 · Change 12 · макет {REVISION}", f"Prototype $state.previewRevision · head 003 · Change 12 · design {REVISION}")}))
    status = details("design-current-work", "", "", "$state.current", [
        field("actor", "Ответственность", "Responsibility"), field("summary", "Сейчас", "Now"), field("timing", "Активность · образец", "Activity · specimen")])
    actions = []
    for id, condition, buttons, commands in [
        ("planning", "$state.current.id === 'planning'", [button("generate", "Создать прототип", "Create prototype", "construct-outline")], [update("click:generate", current="$state.samples.working")]),
        ("working", "$state.current.id === 'working' || $state.current.id === 'verifying'", [button("stop", "Остановить", "Stop", "stop-circle-outline"), button("progress", "Ход работы", "Progress", "list-outline")], [modal_action("click:stop", "design-stop"), update("click:progress", workbenchView="process")]),
        ("input", "$state.current.id === 'input'", [button("answer", "Ответить", "Answer", "chatbox-outline")], [modal_action("click:answer", "design-answer")]),
        ("review", "$state.current.id === 'review' && $state.previewRevision === '003'", [button("accept", "Принять 003", "Accept 003", "checkmark-outline"), button("correct", "Изменить", "Request changes", "create-outline")], [modal_action("click:accept", "design-accept"), update("click:correct", workbenchView="conversation")]),
        ("accepted", "$state.current.id === 'accepted'", [button("implement", "Начать автоматизацию", "Start implementation", "construct-outline")], [modal_action("click:implement", "design-implementation")]),
        ("implementation-review", "$state.current.id === 'implementation_review'", [button("accept-implementation", "Принять автоматизацию", "Accept implementation", "checkmark-done-outline")], [modal_action("click:accept-implementation", "design-accept-implementation")]),
        ("partial", "$state.current.id === 'partial'", [button("resolve", "Уточнить ограничение", "Resolve limitation", "chatbox-outline"), button("checks", "Посмотреть проверки", "Inspect checks", "shield-checkmark-outline")], [modal_action("click:resolve", "design-answer"), update("click:checks", workbenchView="checks")]),
        ("failed", "$state.current.id === 'failed'", [button("repair", "Запросить исправление", "Request a fix", "construct-outline"), button("checks", "Посмотреть проверки", "Inspect checks", "shield-checkmark-outline")], [update("click:repair", workbenchView="conversation"), update("click:checks", workbenchView="checks")]),
        ("offline", "$state.current.id === 'offline'", [button("reconnect", "Проверить связь", "Check connection", "refresh-outline")], [update("click:reconnect", current="$state.samples.review")]),
        ("cancelled", "$state.current.id === 'cancelled'", [button("inspect", "Посмотреть сохраненное", "Inspect retained result", "eye-outline")], [update("click:inspect", workbenchView="result")]),
        *[(key, f"$state.current.id === '{key}'", [button(command, ru, en, "cube-outline")], [modal_action("click:" + command, "design-" + command)])
          for key, command, ru, en in (
              ("completed", "prepare-beta", "Подготовить Beta", "Prepare Beta"),
              ("beta_ready", "install-beta", "Установить Beta", "Install Beta"),
              ("beta_active", "accept-beta", "Принять Beta", "Accept Beta"),
              ("stable_ready", "release-stable", "Выпустить Stable", "Release Stable"),
              ("stable_local", "publish-stable", "Опубликовать Stable", "Publish Stable"))],
    ]:
        actions.append(toolbar("design-action-" + id, buttons, commands, visible=condition))
    actions = [toolbar("design-primary-actions", [dict(b, visibleIf=w["visibleIf"]) for w in actions for b in w["inputs"]["buttons"]],
                       [a for w in actions for a in w["actions"]])]
    tabs = toolbar("design-workbench-views", [
        button("result", "Результат", "Result", "eye-outline"), button("brief", "Задача", "Task", "document-text-outline"),
        button("checks", "Проверки", "Checks", "shield-checkmark-outline"), button("process", "Процесс", "Process", "git-branch-outline"),
        button("conversation", "Обсуждение", "Conversation", "chatbubbles-outline"),
        button("inputs", "Материалы", "Inputs", "attach-outline"),
        button("files", "Файлы", "Files", "folder-outline"),
    ], [update("click", workbenchView="$event.id")], variant="segmented", selectedStateKey="workbenchView")
    result_widgets = [
        details("design-revision-identity", "Прототип $state.previewRevision", "Prototype $state.previewRevision", {
            "application": "$state.applicationTitle", "revision": "$state.previewRevision", "notice": "Демонстрационные данные · локальные взаимодействия",
        }, [field("notice", "ДЕМО", "DEMO")], visible="$state.workbenchView === 'result'"),
        toolbar("design-preview-commands", [button("inspect", "К редакции 003", "Revision 003", "eye-outline"),
                                             button("annotate", "Замечание", "Feedback", "create-outline")],
                [update("click:inspect", previewRevision="003", inspectedRevision="003"), feedback("click:annotate")], visible="$state.workbenchView === 'result'"),
        {"id": "design-assets", "type": "ui.table", "area": "main", **text_field("title", "Оборудование", "Equipment"),
         "visibleIf": "$state.workbenchView === 'result'", "dataSource": source(choose(equals("$state.previewRevision", "002"), "$state.baselineAssets", ["$state.assetPump", "$state.assetFan"])),
         "inputs": {"columns": [field("name", "Название", "Name"), field("location", "Локация", "Location")],
                    "selectable": True, "rowKey": "id", "selectedStateKey": "selectedAssetId", "search": False,
                    "pagination": {"enabled": False}},
         "actions": [update("select", selectedAssetId="$event.id", selectedAsset="$event")]},
        toolbar("design-asset-actions", [button("edit", "Редактировать объект", "Edit asset", "create-outline", visibleIf="$state.previewRevision === '003'", enabledIf="$state.selectedAssetId"),
                                         button("details", "Осмотры объекта", "Asset inspections", "list-outline", enabledIf="$state.selectedAssetId")],
                [modal_action("click:edit", "design-edit-asset"), modal_action("click:details", "design-inspections")], visible="$state.workbenchView === 'result'"),
        details("design-selection", "Выбранный объект", "Selected asset", "$state.selectedAsset", [field("name", "Название", "Name"), field("location", "Локация", "Location")], visible="$state.workbenchView === 'result' && $state.selectedAssetId"),
    ]
    brief = details("design-task-brief", "Что меняем", "What will change", {
        "goal": "Редактировать объект, не теряя связанный список осмотров.",
        "requested": "Клик по строке выбирает объект. Отдельная команда открывает модалку редактирования.",
        "preserve": "Объекты, осмотры, пункты проверки, выбор и фильтры; текущая работа в Workspace.",
        "assumption": "Форма открывается в модалке. Это обратимое решение, а не дополнительное требование.",
        "later": "Постоянное хранение и уведомления относятся к автоматизации.",
        "origin": "Чат пользователя · приложение Осмотр оборудования · Change 12 (образец)",
    }, [field("goal", "Цель", "Goal"), field("requested", "Запрошено", "Requested"), field("preserve", "Сохранить", "Preserve"),
        field("assumption", "Допущение", "Assumption"), field("later", "На следующем этапе", "Next stage"), field("origin", "Источник", "Origin")], visible="$state.workbenchView === 'brief'")
    checks = {"id": "design-checks", "type": "ui.table", "area": "main", **text_field("title", "Проверки · демонстрационные результаты", "Checks · demonstration results"),
              "visibleIf": "$state.workbenchView === 'checks'", "dataSource": source("$state.current.checks"),
              "inputs": {"columns": [field("name", "Проверка", "Check"), field("result", "Результат", "Result"), field("scope", "Область", "Scope")], "pagination": {"enabled": False}},
              "actions": [update("select", inspectedCheck="$event"), modal_action("select", "design-check-evidence")]}
    process = {"id": "design-process", "type": "ui.list", "area": "main", **text_field("title", "История задачи · образец", "Task history · specimen"),
               "visibleIf": "$state.workbenchView === 'process'", "dataSource": source("$state.current.process"),
               "inputs": {"titleKey": "title", "subtitleKey": "subtitle", "previewKey": "preview", "previewOverflow": "wrap", "search": False}}
    context_button = toolbar("design-context-command", [button("context", "Контекст и расходы", "Context and cost", "code-slash-outline")],
                             [modal_action("click:context", "design-context")], visible="$state.workbenchView === 'process' || $state.workbenchView === 'brief'")
    diagnostics = details("design-diagnostics", "Диагностика · образец", "Diagnostics · specimen", {"run": "$state.current.run", "state": "$state.current.id", "revision": "$state.previewRevision"},
                          [field("run", "Запуск", "Run"), field("state", "Состояние", "State"), field("revision", "Preview", "Preview")], visible="$state.designSettings.diagnostics")
    conversations = []
    for suffix, area, condition in (("side", "conversation", "$state.workbenchView !== 'conversation'"), ("full", "main", "$state.workbenchView === 'conversation'")):
        conversations.append(details("design-conversation-" + suffix, "Обсуждение задачи", "Task conversation", {
            "user": "Нужно редактировать объект. При выборе строки должны оставаться видны его осмотры.",
            "builder": "Сохраню выбор по клику. Редактирование вынесу в отдельную команду и модальное окно.",
            "result": "$state.current.summary", "result_i18n": "$state.current.summary_i18n",
        }, [field("user", "Вы · 14:28", "You · 14:28"), field("builder", "Builder · 14:29", "Builder · 14:29"), field("result", "Последний результат", "Latest result")], area=area, visible=condition))
        composer = form("design-composer-" + suffix, [
            label("Намерение сообщения", "Message intent", id="intent", type="dropdown", defaultValue="correction", options=[
                label("Исправить результат", "Correct result", value="correction"), label("Добавить требование", "Add requirement", value="requirement"),
                label("Обсудить без изменений", "Discuss without changes", value="discussion")]),
            label("Сообщение", "Message", id="text", type="longText", required=True, stateKey="draftMessage"),
        ], "Сохранить сообщение в макете", "Keep message in specimen", [update("submit", pendingNote="$event.values.text", pendingIntent="$event.values.intent")], area=area)
        composer["visibleIf"] = condition
        conversations.append(composer)
        conversations.append(details("design-pending-note-" + suffix, "Для следующего шага", "For the next step", {"text": "$state.pendingNote", "intent": "$state.pendingIntent"},
                                     [field("text", "Сообщение сохранено", "Message retained"), field("intent", "Намерение", "Intent")], area=area, visible=condition + " && $state.pendingNote"))
    widgets = [header, status, *actions, tabs, *result_widgets, brief, checks, process, context_button, diagnostics, *conversations]
    page = {"id": "builder", "title": "Builder", "layout": {"type": "split", "areas": [{"id": "main", "role": "main"}, {"id": "conversation", "role": "aux", "width": 360}],
            "auxWidth": 360, "variants": [{"id": "conversation-focus", "when": "$state.workbenchView === 'conversation'", "type": "single", "areas": [{"id": "main", "role": "main"}]}]},
            "presentation": {"defaultProfile": "desktop", "profiles": {"desktop": {"density": "compact", "maxContentWidthPx": 1600}}},
            "initialState": {"samples": samples, "current": samples["review"], "workbenchView": "result", "previewRevision": "003", "inspectedRevision": "003",
                             "applicationTitle": "Осмотр оборудования", "selectedAssetId": "pump", "selectedAsset": {"id": "pump", "name": "Насос К-1", "location": "Цех 1"},
                             "assetPump": {"id": "pump", "name": "Насос К-1", "location": "Цех 1"}, "assetFan": {"id": "fan", "name": "Вентилятор Н-7", "location": "Котельная"},
                             "baselineAssets": [{"id": "pump", "name": "Насос К-1", "location": "Цех 1"}, {"id": "fan", "name": "Вентилятор Н-7", "location": "Котельная"}],
                             "settingsId": "settings",
                             "designSettings": {"diagnostics": False},
                             "pendingNote": "", "draftMessage": "", "reviewNote": "", "answer": ""},
            "meta": {"builder": {"scenario_id": "builder", "functional": False, "design_prototype": True, "binding_mode": "mock",
                                 "workflow_contract": "adaos.builder.workflow.v1", "human_acceptance": "pending",
                                 "live_commands": False, "specimen_states": list(samples)}}, "widgets": widgets}
    close = {"on": "submit", "type": "closeModal"}
    modals = {
        "design-accept": modal("design-accept", "Принять прототип 003", "Accept prototype 003", [
            details("design-accept-summary", "Результат для согласования", "Result for acceptance", {
                "revision": "003 · основа 002 · Change 12", "change": "Модалка редактирования объекта. Выбор осмотров сохранен.",
                "checks": "Структура, взаимодействия, desktop и mobile: демонстрационные результаты доступны в Проверках.",
                "boundary": "Согласование интерфейса не запускает автоматизацию и не разрешает внешние действия.",
            }, [field("revision", "Редакция", "Revision"), field("change", "Изменение", "Change"), field("checks", "Проверки", "Checks"), field("boundary", "Последствие решения", "Effect of decision")]),
            form("design-accept-form", [label("Комментарий, необязательно", "Comment, optional", id="note", type="longText")], "Принять 003 в макете", "Accept 003 in specimen", [update("submit", current="$state.samples.accepted", reviewNote="$event.values.note"), close]),
        ]),
        "design-answer": modal("design-answer", "Нужно уточнение", "Clarification needed", [
            details("design-question", "Уведомления: два обязательных решения", "Notifications: two required decisions", {
                **text_field("reason", "Канал и получатель нужны для доставки. Черновик ответов не возобновляет исполнение; дополнительное пожелание необязательно.", "Delivery needs a channel and a recipient. Saving a draft does not resume execution; extra preferences are optional.")}, [field("reason", "Почему спрашиваем", "Why this is needed")]),
            form("design-answer-form", [
                label("1. Канал доставки", "1. Delivery channel", id="channel", type="dropdown", required=True, stateKey="answerChannel", options=[label("Telegram", "Telegram", value="telegram"), label("Электронная почта", "Email", value="email")]),
                label("2. Получатель", "2. Recipient", id="recipient", type="shortText", required=True, stateKey="answerRecipient"),
                label("3. Дополнительное пожелание", "3. Extra preference", id="preference", type="longText", stateKey="answerPreference"),
            ], "Ответить и продолжить в макете", "Answer and continue in specimen", [update("submit", answers="$event.values", current="$state.samples.verifying"), close]),
            toolbar("design-answer-draft", [button("save-draft", "Сохранить черновик", "Save draft", "save-outline")], [{"on": "click:save-draft", "type": "closeModal"}]),
        ]),
        "design-stop": modal("design-stop", "Остановить текущую работу?", "Stop current work?", [
            details("design-stop-effect", "Сохраненное останется доступным", "Saved work remains available", {"effect": "Новые действия прекратятся после подтверждения исполнителя. Уже совершенные изменения не откатываются. Workspace не затронут."}, [field("effect", "Последствия", "Consequences")]),
            toolbar("design-stop-actions", [button("confirm", "Остановить в макете", "Stop in specimen", "stop-circle-outline")], [update("click:confirm", current="$state.samples.cancelled"), {"on": "click:confirm", "type": "closeModal"}]),
        ]),
        "design-implementation": modal("design-implementation", "Начать автоматизацию", "Start implementation", [
            details("design-implementation-contract", "Основа и границы", "Base and scope", {"base": "Принятый прототип 003", "scope": "Постоянное хранение объекта, проверка после перезапуска; без Trial и публикации.", "preserve": "Компоновка, формы, навигация и идентификаторы согласованного прототипа."}, [field("base", "Основа", "Base"), field("scope", "Реализовать", "Implement"), field("preserve", "Сохранить", "Preserve")]),
            toolbar("design-implementation-actions", [button("start", "Начать в макете", "Start in specimen", "construct-outline")], [update("click:start", current="$state.samples.verifying"), {"on": "click:start", "type": "closeModal"}]),
        ]),
        "design-accept-implementation": modal("design-accept-implementation", "Принять автоматизацию", "Accept implementation", [
            details("design-implementation-review", "Проверенная реализация", "Verified implementation", {
                **text_field("base", "Прототип 003; форма и навигация сохранены", "Prototype 003; form and navigation preserved"),
                **text_field("checks", "Хранение после перезапуска: пройдено (образец).", "Persistence after restart: passed (specimen)."),
                **text_field("effect", "Приемка в DEV. Не публикация в Workspace, Trial или GitHub.", "Acceptance in DEV. No Workspace, Trial or GitHub publication."),
            }, [field("base", "Основа", "Base"), field("checks", "Проверки", "Checks"), field("effect", "Последствия", "Effect")]),
            toolbar("design-accept-implementation-actions", [button("confirm-implementation", "Принять в макете", "Accept in specimen", "checkmark-outline")],
                    [update("click:confirm-implementation", current="$state.samples.completed"), {"on": "click:confirm-implementation", "type": "closeModal"}]),
        ]),
        "design-history": modal("design-history", "Редакции и происхождение", "Revisions and lineage", [
            {"id": "design-history-table", "type": "ui.table", "area": "main", "dataSource": source([
                {"id": "003", "name": "003 · текущий кандидат", "base": "002", "status": "Ожидает решения", "updated": "14:32"},
                {"id": "002", "name": "002 · согласованная основа", "base": "001", "status": "Зафиксирована", "updated": "Вчера, 18:12"},
            ]), "inputs": {"columns": [field("name", "Редакция", "Revision"), field("status", "Состояние", "State"), field("updated", "Обновлено", "Updated")], "selectable": True, "pagination": {"enabled": False}}, "actions": [update("select", inspectedRevision="$event.id")]},
            details("design-history-inspection", "Редакция $state.inspectedRevision", "Revision $state.inspectedRevision", {"effect": "Просмотр истории не меняет цель Preview и основу следующего сообщения."}, [field("effect", "Навигация", "Navigation")]),
            toolbar("design-history-actions", [button("show", "Показать эту редакцию", "Show this revision", "eye-outline")], [update("click:show", previewRevision="$state.inspectedRevision", workbenchView="result", selectedAssetId="", selectedAsset={}), {"on": "click:show", "type": "closeModal"}]),
        ]),
        "design-applications": modal("design-applications", "Приложения · демонстрационные записи", "Applications · demonstration records", [
            {"id": "design-application-table", "type": "ui.table", "area": "main", "dataSource": source([
                {"id": "equipment", "name": "Осмотр оборудования", "status": "На согласовании", "updated": "2026-09-14 14:32"},
            ]), "inputs": {"columns": [field("name", "Приложение", "Application", sortable=True), field("status", "Состояние", "State"), field("updated", "Обновлено", "Updated", sortable=True)], "search": True, "selectable": True, "pagination": {"enabled": False}},
             "actions": [{"on": "select", "type": "closeModal"}]},
        ]),
        "design-context": modal("design-context", "Контекст и расходы · образец", "Context and cost · specimen", [
            details("design-context-details", "Что получил исполнитель", "What the executor received", {"input": "Постановка пользователя; согласованный прототип 002; замечание к объекту; контракт доступных компонентов.", "excluded": "Другие приложения, внешние Dev Tickets и неподтвержденные предпочтения не включены.", "scope": "D1: ограниченное изменение формы. Не пересоздание приложения.", "usage": "Демонстрационный запуск: 8 420 входных, 5 100 кешированных, 2 160 выходных токенов.", "cost": "Фактического вызова модели в этом макете нет. Денежная стоимость не рассчитана."}, [field("input", "Входные данные", "Inputs"), field("excluded", "Исключено", "Excluded"), field("scope", "Маршрут", "Route"), field("usage", "Использование · пример", "Usage · example"), field("cost", "Стоимость", "Cost")]),
        ]),
        "design-check-evidence": modal("design-check-evidence", "Детали проверки · образец", "Check details · specimen", [details("design-evidence", "Проверка", "Check", "$state.inspectedCheck", [field("name", "Проверка", "Check"), field("result", "Результат", "Result"), field("scope", "Область", "Scope")])]),
        "design-edit-asset": modal("design-edit-asset", "Редактирование объекта", "Edit asset", [
            form("design-edit-asset-form", [label("Название", "Name", id="name", type="shortText", required=True), label("Локация", "Location", id="location", type="shortText", required=True)], "Сохранить в макете", "Save in specimen", [update("submit", selectedAsset={"id": "$state.selectedAssetId", "name": "$event.values.name", "location": "$event.values.location"}),
                update("submit", assetPump=choose(equals("$state.selectedAssetId", "pump"), "$state.selectedAsset", "$state.assetPump"), assetFan=choose(equals("$state.selectedAssetId", "fan"), "$state.selectedAsset", "$state.assetFan")), close]),
        ]),
        "design-inspections": modal("design-inspections", "Осмотры выбранного объекта", "Selected asset inspections", [details("design-inspection", "$state.selectedAsset.name", "$state.selectedAsset.name", {"name": "Ежедневный осмотр", "status": "Черновик", "items": "Вибрация; утечки; температура подшипника"}, [field("name", "Осмотр", "Inspection"), field("status", "Состояние", "State"), field("items", "Пункты проверки", "Checklist")])]),
        "design-settings": modal("design-settings", "Настройки приложения · макет", "Application settings · specimen", [
            form("design-settings-form", [label("Показывать технические подробности", "Show technical details", id="diagnostics", type="toggle", defaultValue=False)], "Сохранить в макете", "Save in specimen", [update("submit", designSettings="$event.values"), close]),
        ]),
    }
    editor = modals["design-edit-asset"]["schema"]["widgets"][0]
    editor["dataSource"] = source("$state.selectedAsset")
    editor["inputs"]["selectedStateKey"] = "selectedAssetId"
    settings = modals["design-settings"]["schema"]["widgets"][0]
    settings["dataSource"] = source({"id": "settings", "diagnostics": "$state.designSettings.diagnostics"})
    settings["inputs"]["selectedStateKey"] = "settingsId"
    modals["design-answer"]["schema"]["widgets"][1]["inputs"]["autoCommit"] = True
    add_material_views(page, modals)
    add_delivery_views(page, modals)
    resources = {f"builder.design.i18n.{locale}": {"kind": "data", "role": "i18n", "locale": locale, "path": f"assets/i18n/design-{REVISION}-{locale}.json", "mime": "application/json", "delivery": "core"} for locale in LOCALES}
    resources["builder.design.reference"] = {"kind": "image", "scope": "scenario", "path": f"assets/design-{REVISION}-reference.png", "mime": "image/png", "delivery": "core"}
    return {"schema": "adaos.webui.v1", "generated_by": "human-authored-builder-design",
            "ui": {"version": REVISION, "application": {"desktop": {"pageSchema": page}, "modals": modals, "resources": resources},
                   "user_summary": {"assumptions": ["Design specimen only; every workflow command is simulated in local UI state."], "expected_behavior": ["Human approval is pending; no Automation, Trial, publication, or LLM calls are admitted."]}}, "resources": resources}


def add_material_views(page, modals):
    files = {"id": "application", "title": "equipment-inspections", "children": [
        {"id": "scenario", "title": "scenarios / equipment", "children": [
            {"id": "webui", "title": "webui.json", "path": "scenarios/equipment/webui.json", "kind": "JSON",
             "content": '```json\n{"revision": "003", "layout": "split", "interaction": "select + edit modal"}\n```'},
            {"id": "assets", "title": "assets", "children": [
                {"id": "fixtures", "title": "equipment.json", "path": "scenarios/equipment/assets/equipment.json", "kind": "JSON",
                 "content": '```json\n[{"id": "pump", "name": "Насос К-1", "location": "Цех 1"}]\n```'},
                {"id": "locales", "title": "i18n", "children": [
                    {"id": "ru", "title": "ru.json", "path": "scenarios/equipment/assets/i18n/ru.json", "kind": "JSON",
                     "content": '```json\n{"equipment.edit": "Редактировать объект"}\n```'}]}]}]},
        {"id": "skill", "title": "skills / equipment", "children": [
            {"id": "handler", "title": "handlers/main.py", "path": "skills/equipment/handlers/main.py", "kind": "Python",
             **text_field("content", "Код реализации появляется на этапе автоматизации. В прототипе 003 его еще нет.", "Implementation code is added during Automation. Prototype 003 has no implementation yet.")}]}]}
    baseline = copy.deepcopy(files)
    baseline["children"][0]["children"][0]["content"] = '```json\n{"revision": "002", "layout": "split", "interaction": "select"}\n```'
    page["initialState"].update(fileTree=files, baselineFileTree=baseline, selectedFile={}, selectedFileId="",
        attachmentDraft=[], includeReference=False, referenceId="reference", inputRecordId="input-record")
    visible = "$state.workbenchView === 'files'"
    page["widgets"].extend([
        details("design-files-scope", "Файлы приложения · прототип $state.previewRevision", "Application files · prototype $state.previewRevision",
                {**text_field("scope", "Снимок редакции. Содержимое файлов в этом макете сокращено; это не проводник рабочего компьютера.", "Revision snapshot. File contents in this specimen are abbreviated; this is not a filesystem browser.")},
                [field("scope", "Область", "Scope")], visible=visible),
        {"id": "design-file-tree", "type": "visual.taigaTree", "area": "main", "visibleIf": visible,
         "dataSource": source(choose(equals("$state.previewRevision", "002"), "$state.baselineFileTree", "$state.fileTree")),
         "inputs": {"hideRoot": True, "expanded": True, "wrapTitles": True, "selectionMode": "leaf", "selectedStateKey": "selectedFileId"},
         "actions": [update("select", selectedFile="$event", selectedFileId="$event.id"), modal_action("select", "design-file-viewer")]},
        details("design-inputs-scope", "Материалы для Builder", "Materials for Builder", {
            **text_field("scope", "Change 12 · исходные материалы, не файлы поставки. Подключение к контексту требует выбора пользователя.", "Change 12 · input references, not release files. Context inclusion requires an explicit choice.")},
            [field("scope", "Область", "Scope")], visible="$state.workbenchView === 'inputs'"),
        {"id": "design-reference-table", "type": "ui.table", "area": "main", "visibleIf": "$state.workbenchView === 'inputs'",
         "dataSource": source([
             {"id": "code", "name": "editing-example.py", **text_field("role", "Пример исходного кода", "Code reference"),
              "content": '```python\ndef select_equipment(equipment_id):\n    return {"selected": equipment_id}\n```'},
             {"id": "screen", "name": "builder-reference.png", "media": "resource:builder.design.reference", "mediaKind": "image", **text_field("role", "Визуальный ориентир", "Visual reference"),
              **text_field("content", "Пример экрана прикладывается как ориентир, не как инструкция и не как часть поставки.", "A screenshot is a reference, not an instruction or a release asset.")}]),
         "inputs": {"columns": [field("name", "Материал", "Material"), field("role", "Назначение", "Role")], "pagination": {"enabled": False}},
         "actions": [update("select", selectedInput="$event"), modal_action("select", "design-input-viewer")]},
    ])
    attach = form("design-attach-form", [
        label("Исходные материалы", "Reference files", id="files", type="fileUpload", multiple=True, maxFiles=5, stateKey="attachmentDraft"),
        label("Включить пример кода в следующий контекст", "Include code example in next context", id="include", type="toggle", stateKey="includeReference", defaultValue=False),
    ], "Сохранить выбор в макете", "Keep selection in specimen", [update("submit", pendingAttachments="$event.values.files", referenceIncluded="$event.values.include")])
    attach["visibleIf"] = "$state.workbenchView === 'inputs'"
    attach["inputs"]["autoCommit"] = True
    page["widgets"].extend([attach, details("design-input-delivery", "Состояние материалов", "Material status", {
        **text_field("delivery", "Выбранные файлы: только имена и метаданные в памяти браузера. Байты не загружены; модель ничего не получила.", "Selected files: names and metadata in browser memory only. Bytes are not uploaded; nothing was sent to the model."),
        "included": "$state.includeReference", "files": "$state.attachmentDraft",
    }, [field("delivery", "Доставка", "Delivery"), field("included", "Пример кода выбран", "Code reference selected"), field("files", "Локальный выбор", "Local selection")], visible="$state.workbenchView === 'inputs'")])
    modals["design-file-viewer"] = modal("design-file-viewer", "Файл · прототип $state.previewRevision", "File · prototype $state.previewRevision", [
        details("design-file-content", "$state.selectedFile.title", "$state.selectedFile.title", "$state.selectedFile", [field("path", "Путь", "Path"), field("kind", "Формат", "Format"), field("content", "Содержимое", "Content", kind="markdown")]),
        toolbar("design-file-request", [button("request-file-change", "Предложить изменение", "Request a change", "chatbox-outline", enabledIf="$state.previewRevision === '003'")],
                [update("click:request-file-change", workbenchView="conversation", requestedFile="$state.selectedFile.path", requestRevision="$state.previewRevision"), {"on": "click:request-file-change", "type": "closeModal"}]),
    ])
    modals["design-input-viewer"] = modal("design-input-viewer", "Исходный материал", "Input reference", [
        details("design-input-content", "$state.selectedInput.name", "$state.selectedInput.name", "$state.selectedInput", [field("role", "Назначение", "Role"), field("content", "Содержимое", "Content", kind="markdown")]),
        details("design-reference-image", "Скриншот-ориентир", "Reference screenshot", "$state.selectedInput", [], mediaKey="media", mediaKindKey="mediaKind", visible="$state.selectedInput.id === 'screen'"),
    ])
    page["widgets"].append(details("design-file-request-scope", "Предложение к файлу", "File change request", {"file": "$state.requestedFile", "revision": "$state.requestRevision"},
        [field("file", "Файл", "File"), field("revision", "Основа сообщения", "Message base")], visible="$state.workbenchView === 'conversation' && $state.requestedFile"))


def add_delivery_views(page, modals):
    page["widgets"].extend([
        details("design-delivery-summary", "Каналы поставки · образец", "Delivery channels · specimen", {
            **text_field("alpha", "DEV · прототип 003 / реализация A17. Персональный preview, не отдельная веха приемки.", "DEV · prototype 003 / implementation A17. Personal preview, not another acceptance milestone."),
            **text_field("beta", "Trials · кандидат 1.4.0-beta.1 на основе A17. Установка отдельно от разрешения внешнего доступа.", "Trials · candidate 1.4.0-beta.1 from A17. Installation is separate from permission for external access."),
            **text_field("stable", "Workspace · 1.3.0 до выпуска 1.4.0. Установка отдельно от публикации в Marketplace.", "Workspace · 1.3.0 until 1.4.0 is released. Installation is separate from Marketplace publication."),
            "status": "$state.current.summary", "status_i18n": "$state.current.summary_i18n",
        }, [field("alpha", "Alpha / DEV", "Alpha / DEV"), field("beta", "Beta / Trials", "Beta / Trials"), field("stable", "Stable / Workspace", "Stable / Workspace"), field("status", "Текущее состояние", "Current state")], visible="$state.workbenchView === 'deliveries'"),
    ])
    for command, target, ru, en, effect_ru, effect_en in [
        ("prepare-beta", "beta_ready", "Подготовить Beta", "Prepare Beta", "Создать неизменяемый пакет из принятой реализации A17. Ничего не устанавливать и не публиковать.", "Create an immutable package from accepted implementation A17. No installation or publication."),
        ("install-beta", "beta_active", "Установить Beta", "Install Beta", "Проверить пакет и создать резервную копию перед активацией 1.4.0-beta.1 в Trials. Stable сохраняется.", "Verify the package and back up data before activating 1.4.0-beta.1 in Trials. Stable is retained."),
        ("accept-beta", "stable_ready", "Принять Beta", "Accept Beta", "Зафиксировать приемку кандидата 1.4.0-beta.1. Не выпускать Stable автоматически.", "Record acceptance of candidate 1.4.0-beta.1. Do not release Stable automatically."),
        ("release-stable", "stable_local", "Выпустить Stable", "Release Stable", "Резервная копия, проверка перехода, активация 1.4.0 в Workspace. Завершить Beta; история и Dev Tickets остаются.", "Back up data, verify the transition, activate 1.4.0 in Workspace. Retire Beta; retain history and Dev Tickets."),
        ("publish-stable", "stable_published", "Опубликовать Stable", "Publish Stable", "Только publisher: разрешить публичное распространение 1.4.0 в Marketplace. Это отдельное согласие.", "Publisher only: authorize public distribution of 1.4.0 in Marketplace. This is a separate permission."),
    ]:
        modals["design-" + command] = modal("design-" + command, ru, en, [
            details("design-effect-" + command, "Последствия решения", "Decision effects", {
                **text_field("effect", effect_ru, effect_en),
                **text_field("simulation", "Макет: изменится только изображаемое состояние. Реальные пакеты, данные и публикации не затрагиваются.", "Specimen: only the displayed state changes. Real packages, data and publications are untouched.")},
                [field("effect", "Действие", "Action"), field("simulation", "Исполнение", "Execution")]),
            toolbar("design-confirm-" + command, [button("confirm-" + command, "Подтвердить в макете", "Confirm in specimen", "checkmark-outline")],
                    [update("click:confirm-" + command, current="$state.samples." + target, workbenchView="deliveries"), {"on": "click:confirm-" + command, "type": "closeModal"}]),
        ])


def audit_safety(value):
    if isinstance(value, dict):
        if "on" in value and value.get("type") not in {"updateState", "openModal", "closeModal", "openDevTickets"}:
            raise ValueError("Only local state, modal navigation and the existing feedback panel are allowed")
        if value.get("type") == "openModal" and not value.get("params", {}).get("modalId"):
            raise ValueError("Modal navigation must use the Client modalId contract")
        if value.get("type") in {"callSkill", "callMcp", "callHost", "resourceOperation", "openWorkspace", "openUrl"}:
            raise ValueError("Design specimen must not execute external or live commands")
        if value.get("kind") in {"api", "skill", "mcp", "stream", "resourceQuery", "projection"}:
            raise ValueError("Design specimen must not query live process data")
        for child in value.values():
            audit_safety(child)
    elif isinstance(value, list):
        for child in value:
            audit_safety(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    after = build_document()
    audit_safety(after)
    schema = read(ROOT / "src/adaos/abi/webui.v1.schema.json")
    Draft202012Validator(schema).validate(after)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / "candidate.webui.json", after)
    if not args.apply:
        print("Design candidate validated; --apply records a fresh baseline and DEV prototype.")
        return
    revision_dir = SCENARIO / "ui_revisions"
    if (revision_dir / f"{REVISION}.json").exists():
        raise SystemExit(f"Prototype {REVISION} already exists; do not overwrite immutable evidence")
    before = read(SCENARIO / "webui.json")
    now = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256((SCENARIO / "webui.json").read_bytes()).hexdigest()
    snapshots = [(REVISION, after, REQUEST)]
    if not (revision_dir / "060.json").exists():
        snapshots.insert(0, ("060", before, "Fresh snapshot of operational DEV Builder before UX design; historical revision files were absent."))
    for revision, doc, summary in snapshots:
        payload = {"schema": "adaos.builder.ui_revision.v1", "revision": revision, "created_at": now, "scenario_id": "builder",
                   "request": {"text": summary}, "patch": {"id": f"builder-workbench-design-{revision}", "operation": "observed_baseline" if revision == "060" else "design_prototype", "status": "applied", "source_digest": digest},
                   "after_webui": doc, "preview_state": {"scenario_id": "builder", "title": "Builder", "version": revision},
                   "before_webui": before, "provenance": {"author": "codex.design", "model_generation_benchmark": False, "human_acceptance": "pending"}}
        write(revision_dir / f"{revision}.json", payload)
    for locale, data in LOCALES.items():
        write(SCENARIO / f"assets/i18n/design-{REVISION}-{locale}.json", data)
    shutil.copyfile(ROOT / "scripts/fixtures/builder-workbench-reference.png", SCENARIO / f"assets/design-{REVISION}-reference.png")
    write(SCENARIO / "webui.json", after)
    scenario = read(SCENARIO / "scenario.json")
    scenario["ui"] = copy.deepcopy(after["ui"])
    scenario["ui"]["manifest"] = "webui.json"
    scenario["updated_at"] = now
    write(SCENARIO / "scenario.json", scenario)
    (revision_dir / "current.txt").write_text(REVISION + "\n", encoding="utf-8")
    write(OUTPUT / "design-receipt.json", {"revision": REVISION, "baseline": "060", "observed_source_sha256": digest,
                                          "created_at": now, "request": REQUEST, "synthetic_data": True,
                                          "human_acceptance": "pending", "workspace_modified": False})
    print(json.dumps({"ok": True, "revision": REVISION, "baseline": "060", "widgets": len(after["ui"]["application"]["desktop"]["pageSchema"]["widgets"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
