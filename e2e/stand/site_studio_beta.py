"""Connect the reviewed workbench to the admitted Site Studio application tools."""
from copy import deepcopy

from site_studio_workbench import compose, toolbar, update, modal, open_dialog, walk

APP = "site_studio_test_20261003_e2eadc45d49732_26215830"
SKILL = APP + "_skill"
SCOPE = {"project_id": "$state.site_project_id", "selection_revision": "$state.project_selection_revision"}
TAGS = ["site-studio:sections", "site-studio:assets", "site-studio:preview", "site-studio:history", "site-studio:structure", "site-studio:properties"]


def query(name, **params):
    return {"kind": "skill", "name": SKILL + "." + name, "params": {**SCOPE, **params},
            "invalidationTags": ["site-studio:sources", "site-studio:assets", "site-studio:properties"],
            "cacheTtlMs": 0, "preserveLastValue": False, "maxRequestHz": 1, "startPolicy": "materialization_ready"}


def command(on, name, *, params=None, **extra):
    return {"on": on, "type": "callSkill", "target": SKILL + "." + name,
            "params": {**SCOPE, **(params or {})}, **extra}


def connected_beta(legacy):
    doc = compose(legacy)
    app = doc["ui"]["application"]
    page = app["desktop"]["pageSchema"]
    page["id"] = "site_studio_beta_workbench_v1"
    state = page["initialState"]
    # Materialization or the selected connection can settle after the first
    # mount. A failed one-shot hydration otherwise leaves an empty workbench.
    for automatic in page.get("autoActions", []):
        if automatic.get("id") == "hydrate-active-project":
            automatic.update(once=False, intervalMs=3000, enabledIf="$state.project_hydrated !== true")
    state.update(authoring_tool="chat", materials_view="media", preview_change_set_id="", preview_change_source_digest="",
                 property_node_path="", component_property_tab="typed", chat_run_pending=False, chat_run={})
    widgets = {widget["id"]: widget for widget in page["widgets"]}
    chat = widgets["site_discussion"]
    chat["inputs"].pop("placeholder_i18n", None)
    chat["inputs"]["placeholder"] = "Опишите задачу. Через @ можно выбрать исходный материал или медиа сайта."
    chat["inputs"]["conversation"]["owner"] = "site-studio:$state.site_project_id"
    chat["inputs"].setdefault("meta", {})["conversation_id"] = "$state.site_project_id"
    chat["inputs"]["conversation"]["references"] = {"sources": [query("query_chat_references")], "maxSelected": 12}
    chat["inputs"]["conversation"]["webSearch"] = {"source": {"kind": "static", "value": {"available": False, "enabled": False}}}
    for action in chat["actions"]:
        if action.get("on") == "send":
            action["params"].update(references="$event.references", web_search="$event.web_search")
            action["resultStateKey"] = "chat_run"
            action["invalidates"] += ["site-studio:history"]
            action["followups"] = [{"type": "updateState", "params": {"chat_run_pending": {
                "kind": "expression", "op": "equals", "args": ["$state.chat_run.status", "pending"]}}}]
    # Follow the one user-requested run. The server never starts work when none is pending.
    page.setdefault("autoActions", []).append({"id": "continue-chat-authoring", "intervalMs": 4000,
        "enabledIf": "$state.project_hydrated === true && $state.chat_run_pending === true",
        "action": command("tick", "continue_authoring", resultStateKey="chat_run", timeoutMs=45000,
            invalidates=["site-studio:discussion", "site-studio:history"],
            followups=[{"type": "updateState", "params": {"chat_run_pending": {
                "kind": "expression", "op": "equals", "args": ["$state.chat_run.status", "pending"]}}}])})
    context = widgets["chat_context"]
    for action in context["actions"]:
        if action.get("on") == "select:context":
            action["params"]["property_node_path"] = ""
    preview = widgets["site_preview"]
    preview["inputs"].update(showInteractionControls=True, interactionModeStateKey="canvas_mode", inspectorModeStateKey="inspector_mode",
                              selectedSection="$state.selected_sections_id")
    preview["dataSource"]["params"]["candidate_change_set_id"] = "$state.preview_change_set_id"
    review = toolbar("chat_candidate_review", [
        {"id": "apply", "label": "Применить предложение"}, {"id": "reject", "label": "Отклонить"},
        {"id": "back", "label": "К текущему черновику"}], [
        command("click:apply", "commit_site_change", params={"change_set_id": "$state.preview_change_set_id", "expected_source_digest": "$state.preview_change_source_digest"},
                invalidates=TAGS, followups=[{"type": "updateState", "params": {"preview_change_set_id": ""}}]),
        command("click:reject", "reject_site_change", params={"change_set_id": "$state.preview_change_set_id", "expected_source_digest": "$state.preview_change_source_digest"},
                invalidates=TAGS, followups=[{"type": "updateState", "params": {"preview_change_set_id": ""}}]),
        update("click:back", preview_change_set_id="")], singleLine=True)
    review.update(area="preview", visibleIf="$state.preview_change_set_id !== ''")
    for index, action in enumerate(review["actions"]):
        if action["type"] == "callSkill":
            action["params"].update(change_set_id="$state.review_action_change_set_id", expected_source_digest="$state.review_action_source_digest")
            review["actions"][index] = update(action["on"], preview_change_set_id="",
                review_action_change_set_id="$state.preview_change_set_id", review_action_source_digest="$state.preview_change_source_digest")
            review["actions"][index]["followups"] = [action]
    page["widgets"].insert(page["widgets"].index(preview), review)
    for widget in [widgets["v_sections"], widgets["site_structure"]]:
        for action in widget.get("actions", []):
            if action.get("on") == "select":
                action.setdefault("params", {}).update(selected_sections_id="$event.id", selected_source_anchor="$event.source_anchor",
                    selected_section_label="$event.heading", property_node_path="", discussion_subject="block")
                if widget["id"] == "v_sections":
                    action["params"]["selected_source_anchor"] = ""
                else:
                    is_section = {"kind": "expression", "op": "equals", "args": ["$event.kind", "section"]}
                    action["params"]["selected_sections_id"] = {"kind": "expression", "op": "if", "condition": is_section, "then": "$event.id", "else": ""}
                    action["params"]["discussion_subject"] = {"kind": "expression", "op": "if", "condition": is_section, "then": "block", "else": "site"}
    # A tree and a dynamic typed form share exact paths and source guards.
    properties = app["modals"]["section_properties"]["schema"]["widgets"]
    for widget in properties:
        widget["visibleIf"] = "$state.component_property_tab === 'quick'"
    tabs = toolbar("component_property_tabs", [{"id": "typed", "label": "Параметры и элементы"}, {"id": "quick", "label": "Основное"}],
        [update("click:typed", component_property_tab="typed"), update("click:quick", component_property_tab="quick")],
        variant="tabs", selectedStateKey="component_property_tab")
    tabs["area"] = "main"
    tree = {"id": "component_property_tree", "type": "collection.tree", "area": "main", "title": "Состав компонента",
        "visibleIf": "$state.component_property_tab === 'typed'", "dataSource": query("query_component_fields", id="$state.selected_sections_id"),
        "inputs": {"expanded": True, "selectedStateKey": "property_node_path", "maxNodes": 600},
        "actions": [update("select", property_node_path="$event.id")]}
    editor = {"id": "component_property_editor", "type": "ui.form", "area": "main",
        "visibleIf": "$state.component_property_tab === 'typed'", "dataSource": query("query_component_fields", id="$state.selected_sections_id", node_path="$state.property_node_path", resource="component_fields"),
        "inputs": {"selectedStateKey": "selected_sections_id", "fieldsPath": "fields", "fields": [], "layout": "responsiveGrid",
            "buttons": [{"id": "save", "label": "Сохранить", "kind": "primary"}, {"id": "close", "label": "Закрыть"}]},
        "actions": [command("submit", "update_component_fields", id="save", params={"operation": "update", "resource": "component_fields", "id": "$event.record.id",
            "node_path": "$event.record.node_path", "expected_revision": "$event.record.revision", "expected_source_digest": "$event.record.source_digest", "payload": "$event.values"}, invalidates=TAGS),
            {"id": "close", "on": "submit", "type": "closeModal"}]}
    properties[:0] = [tabs, tree, editor]
    _sources(page, app)
    changes = widgets["change_proposals"]
    changes["inputs"].setdefault("buttons", []).append({"id": "undo", "label": "Отменить", "enabledIf": "$item.latest_undo_available === true"})
    changes["actions"].append(command("click:undo", "undo_site_change", params={"change_set_id": "$event.change_set_id", "expected_source_digest": "$event.expected_source_digest"}, invalidates=TAGS,
        confirmation={"message": "Отменить эту правку черновика?", "confirmLabel": "Отменить правку"}))
    for widget in page["widgets"]:
        if widget["id"] not in {"site_identity", "authoring_tools"}:
            condition = widget.get("visibleIf")
            widget["visibleIf"] = "$state.project_hydrated === true" + (" && (" + condition + ")" if condition else "")
    # Media bundles travel through authenticated Root forwarding and both CDN
    # zones. Match the SDK's bounded long-operation window rather than timing
    # out an admitted upload at the ordinary form-action deadline.
    for node in walk(doc):
        if node.get("type") == "callSkill" and str(node.get("target", "")).split(".")[-1] in {
            "prepare_publication", "publish_candidate", "prepare_component_upgrade", "publish_component_upgrade",
            "prepare_site_rollback", "publish_site_rollback",
        }:
            node["timeoutMs"] = 600000
    doc["generated_by"] = "site_studio_beta_workbench"
    return doc


def _sources(page, app):
    page["widgets"].append({"id": "source_files", "type": "ui.list", "area": "authoring", "title": "Исходные материалы",
        "visibleIf": "$state.authoring_tool === 'materials' && $state.materials_view === 'sources'", "dataSource": query("query_source_files"),
        "inputs": {"itemIdKey": "id", "titleKey": "title", "groupBy": "folder", "variant": "list",
            "headerActions": [{"id": "add", "label": "Загрузить исходный файл", "icon": "add-outline"}],
            "meta": [{"key": "path", "label": "Путь", "kind": "text"}, {"key": "media_type", "label": "Тип", "kind": "text"}, {"key": "note", "label": "Назначение", "kind": "text"}]},
        "actions": [open_dialog("select", "source_details", selected_source_material_id="$event.id"),
                    open_dialog("click:add", "source_details", selected_source_material_id="")]})
    fields = [{"id": "title", "title": "Название файла", "type": "shortText", "required": True},
              {"id": "folder", "title": "Папка", "type": "shortText"},
              {"id": "role", "title": "Роль", "type": "singleChoice", "default": "orientation", "options": [
                  {"value": "orientation", "label": "Пример для ориентира"}, {"value": "authoritative", "label": "Источник фактов"}]},
              {"id": "file_ref", "title": "Исходный файл", "type": "fileUpload", "fileStorage": "skill",
               "uploadTarget": SKILL + ".upload_source_file", "readTarget": SKILL + ".read_source_attachment", "maxFiles": 1,
               "maxBytes": 10 * 1024 * 1024, "accept": ".html,.css,.js,.md,.txt,.json,.csv,.svg,.png,.jpg,.jpeg,.webp,.pdf"},
              {"id": "note", "title": "Как использовать", "type": "longText"},
              {"id": "content", "title": "Просмотр текста", "type": "longText", "disabled": True}]
    form = {"id": "source_editor", "type": "ui.form", "area": "main", "dataSource": query("query_source_files", id="$state.selected_source_material_id", resource="source_files"),
        "inputs": {"selectedStateKey": "selected_source_material_id", "fields": fields, "buttons": [
            {"id": "create", "label": "Добавить", "kind": "primary", "enabledIf": "$state.selected_source_material_id === ''"},
            {"id": "save", "label": "Сохранить", "kind": "primary", "enabledIf": "$state.selected_source_material_id !== ''"},
            {"id": "delete", "label": "Удалить", "kind": "danger", "enabledIf": "$state.selected_source_material_id !== ''"}]}, "actions": []}
    for button_id, operation in [("create", "create"), ("save", "update"), ("delete", "delete")]:
        params = {"resource": "source_files", "operation": operation}
        if operation != "create":
            params.update(id="$event.record.id", expected_revision="$event.record.revision")
        if operation == "delete":
            params["confirmed"] = True
        else:
            params["payload"] = {field["id"]: "$event.values." + field["id"] for field in fields if not field.get("disabled")}
        form["actions"].append(command("submit", "mutate_source_files", id=button_id, params=params,
            invalidates=["site-studio:sources"], followups=[{"type": "closeModal"}],
            **({"confirmation": {"message": "Удалить исходный материал?", "confirmLabel": "Удалить"}} if operation == "delete" else {})))
    app["modals"]["source_details"] = modal("Исходный материал", [form])
