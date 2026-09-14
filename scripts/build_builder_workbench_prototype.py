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
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos/dev/sn_6acf0c01/scenarios/builder"
OUTPUT = ROOT / "e2e/artifacts/builder/workbench-design-20260914"
LOCALES: dict[str, dict[str, str]] = {"ru": {}, "en": {}}
REVISION = "064"
REQUEST = (
    "Design a new DEV Builder prototype for human review, based on the target "
    "Builder workflow and SOTA interaction patterns. Preserve the operational "
    "Workspace Builder. Model current work, clarification, exact-revision "
    "review, partial results, failure, recovery, context inspection and history. "
    "Do not implement Automation, create Trials, publish, or invoke an LLM from "
    "the design specimen. Do not accept the new design on behalf of the user."
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
    for id, ru, en, status_ru, status_en, summary_ru, summary_en, time_ru, time_en in definitions:
        item = {"id": id, "revision": "003", "run": "DEMO-R17", "synthetic": True,
                **text_field("target_label", "Осмотр оборудования · Change 12 · ДЕМО", "Equipment inspections · Change 12 · DEMO")}
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
        button("specimens", "ДЕМО", "DEMO", "options-outline"),
        button("history", "Редакции", "Revisions", "git-branch-outline"),
        button("settings", "Настройки", "Settings", "settings-outline"),
        button("feedback", "Dev Tickets", "Dev Tickets", "ticket-outline"),
    ], [modal_action("click:applications", "design-applications"),
        modal_action("click:specimens", "design-specimens"),
        modal_action("click:history", "design-history"), modal_action("click:settings", "design-settings"),
        feedback("click:feedback")],
        statusDataSource=source("$state.current"))
    status = details("design-current-work", "", "", "$state.current", [
        field("summary", "Сейчас", "Now"), field("timing", "Активность · образец", "Activity · specimen")])
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
    ]:
        actions.append(toolbar("design-action-" + id, buttons, commands, visible=condition))
    actions = [toolbar("design-primary-actions", [dict(b, visibleIf=w["visibleIf"]) for w in actions for b in w["inputs"]["buttons"]],
                       [a for w in actions for a in w["actions"]])]
    tabs = toolbar("design-workbench-views", [
        button("result", "Результат", "Result", "eye-outline"), button("brief", "Задача", "Task", "document-text-outline"),
        button("checks", "Проверки", "Checks", "shield-checkmark-outline"), button("process", "Процесс", "Process", "git-branch-outline"),
        button("conversation", "Обсуждение", "Conversation", "chatbubbles-outline"),
    ], [update("click", workbenchView="$event.id")], variant="segmented", selectedStateKey="workbenchView")
    result_widgets = [
        details("design-revision-identity", "Прототип $state.previewRevision", "Prototype $state.previewRevision", {
            "application": "$state.applicationTitle", "revision": "$state.previewRevision", "notice": "Демонстрационные данные · локальные взаимодействия",
        }, [field("notice", "ДЕМО", "DEMO")], visible="$state.workbenchView === 'result'"),
        toolbar("design-preview-commands", [button("inspect", "К редакции 003", "Revision 003", "eye-outline"),
                                             button("annotate", "Замечание", "Feedback", "create-outline")],
                [update("click:inspect", previewRevision="003", inspectedRevision="003"), feedback("click:annotate")], visible="$state.workbenchView === 'result'"),
        {"id": "design-assets", "type": "ui.table", "area": "main", **text_field("title", "Объекты", "Assets"),
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
        "design-specimens": modal("design-specimens", "Состояние процесса · ДЕМО", "Process state · DEMO", [
            form("design-specimen-form", [label("Состояние", "State", id="id", type="dropdown", options=[
                {"value": k, "label": v["label"], "label_i18n": v["label_i18n"]} for k, v in samples.items()
            ])], "Показать", "Show", [update("submit", current="$state.samples.$event.values.id", inspectedRevision="003", previewRevision="003", pendingNote="", answer="", reviewNote=""), close]),
        ]),
        "design-accept": modal("design-accept", "Принять прототип 003", "Accept prototype 003", [
            details("design-accept-summary", "Результат для согласования", "Result for acceptance", {
                "revision": "003 · основа 002 · Change 12", "change": "Модалка редактирования объекта. Выбор осмотров сохранен.",
                "checks": "Структура, взаимодействия, desktop и mobile: демонстрационные результаты доступны в Проверках.",
                "boundary": "Согласование интерфейса не запускает автоматизацию и не разрешает внешние действия.",
            }, [field("revision", "Редакция", "Revision"), field("change", "Изменение", "Change"), field("checks", "Проверки", "Checks"), field("boundary", "Последствие решения", "Effect of decision")]),
            form("design-accept-form", [label("Комментарий, необязательно", "Comment, optional", id="note", type="longText")], "Принять 003 в макете", "Accept 003 in specimen", [update("submit", current="$state.samples.accepted", reviewNote="$event.values.note"), close]),
        ]),
        "design-answer": modal("design-answer", "Нужно уточнение", "Clarification needed", [
            details("design-question", "Кому отправлять уведомления?", "Who should receive notifications?", {"reason": "Требование сохранено, но канал и получатель неизвестны. Интерфейс не выдает локальное сохранение за доставку."}, [field("reason", "Почему спрашиваем", "Why this is needed")]),
            form("design-answer-form", [label("Канал и получатель", "Channel and recipient", id="answer", type="longText", required=True)], "Ответить и продолжить в макете", "Answer and continue in specimen", [update("submit", answer="$event.values.answer", current="$state.samples.verifying"), close]),
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
    specimen_form = modals["design-specimens"]["schema"]["widgets"][0]
    specimen_form["dataSource"] = source("$state.current")
    specimen_form["inputs"]["selectedStateKey"] = "current.id"
    resources = {f"builder.design.i18n.{locale}": {"kind": "data", "role": "i18n", "locale": locale, "path": f"assets/i18n/design-{REVISION}-{locale}.json", "mime": "application/json", "delivery": "core"} for locale in LOCALES}
    return {"schema": "adaos.webui.v1", "generated_by": "human-authored-builder-design",
            "ui": {"version": REVISION, "application": {"desktop": {"pageSchema": page}, "modals": modals, "resources": resources},
                   "user_summary": {"assumptions": ["Design specimen only; every workflow command is simulated in local UI state."], "expected_behavior": ["Human approval is pending; no Automation, Trial, publication, or LLM calls are admitted."]}}, "resources": resources}


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
