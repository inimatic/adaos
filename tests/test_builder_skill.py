from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
import time
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1] / "src" / "adaos" / "skills_templates" / "BuilderSkill"


def _load_module():
    spec = importlib.util.spec_from_file_location("builder_skill_under_test", SKILL_ROOT / "handlers" / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_declares_builder_dialog_agent() -> None:
    manifest = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))

    tools = {item["name"] for item in manifest["tools"]}
    assert {"start", "chat", "create_scenario_draft", "update_current_scenario", "get_preview_state"}.issubset(tools)
    assert manifest["default_tool"] == "chat"
    assert manifest["conversation"]["dialog_channel"]["id"] == "builder"
    assert manifest["conversation"]["agents"][0]["id"] == "agent:builder_skill:builder"


def test_create_shopping_list_scenario_draft_writes_declarative_webui(tmp_path, monkeypatch) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            (artifact_root / "scenario.json").write_text(
                '{"id":"shopping_list","version":"0.1.0","name":"shopping_list","steps":[]}',
                encoding="utf-8",
            )
            return {
                "ok": True,
                "draft": {"draft_id": "draft.shopping"},
                "artifact_root": str(artifact_root),
                "kwargs": kwargs,
            }

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    class _Workbench:
        def set_active_draft(self, **kwargs):
            return {"dev_webspace_id": "builder-skill-test-dev", "active_draft_id": kwargs.get("active_draft_id")}

        def snapshot(self, *args, **kwargs):
            return {"preview_state": kwargs.get("preview_state") or {}}

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())

    result = skill.create_scenario_draft(
        idea="Строитель, создадим приложение список покупок",
        webspace_id="builder-skill-test",
    )

    assert result["ok"] is True
    assert result["dialog"]["dialog_channel_id"] == "builder"
    assert result["dialog"]["default_tool"] == "builder_skill.chat"
    assert result["topic"]["thread_id"] == "thread.builder.builder-skill-test.draft.shopping"
    assert result["dialog"]["thread_id"] == "thread.builder.builder-skill-test.draft.shopping"
    assert result["scenario_id"].startswith("shopping_list_")
    assert result["preview_state"]["current_ui"]["type"] == "page"
    assert result["preview_state"]["datasources"][0]["type"] == "internal_crud"
    webui = artifact_root / "webui.json"
    assert webui.exists()
    assert "preview_state" in webui.read_text(encoding="utf-8")
    scenario = yaml.safe_load((artifact_root / "scenario.json").read_text(encoding="utf-8"))
    page_schema = scenario["ui"]["application"]["desktop"]["pageSchema"]
    assert page_schema["title"] == "Список покупок"
    assert {item["type"] for item in page_schema["widgets"]} >= {"ui.form", "ui.table"}


def test_create_draft_publishes_pending_action_with_conversation_refs(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    published: list[dict] = []

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "draft": {"draft_id": "draft.shopping"}, "artifact_root": str(artifact_root)}

    import adaos.services.builder.workspace as workspace
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)

    def _publish_pending_action(**kwargs):
        published.append(dict(kwargs))
        return {
            "id": "pa.builder.draft",
            "kind": kwargs["kind"],
            "domain_ref": kwargs["domain_ref"],
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(pending_actions, "publish_pending_action", _publish_pending_action)

    result = skill.create_scenario_draft(
        idea="Builder, create a shopping list app",
        webspace_id="builder-pa-ws",
        _meta={
            "conversation_id": "conv.skill.builder_skill.default.builder-pa-ws",
            "thread_id": "thread.builder.1",
            "turn_trace_id": "trace.builder.1",
            "request_id": "req.builder.1",
            "message_id": "msg.builder.1",
        },
    )

    assert result["pending_action"]["id"] == "pa.builder.draft"
    assert result["topic"]["thread_id"] == "thread.builder.1"
    assert result["dialog"]["thread_id"] == "thread.builder.1"
    assert published[0]["kind"] == "builder.scenario_draft.review"
    assert published[0]["response_topic"] == "builder.pending_action.response"
    assert published[0]["domain_ref"]["conversation_id"] == "conv.skill.builder_skill.default.builder-pa-ws"
    assert published[0]["domain_ref"]["thread_id"] == "thread.builder.1"
    refs = published[0]["metadata"]["source_refs"]
    assert refs["draft_id"] == "draft.shopping"
    assert refs["thread_id"] == "thread.builder.1"
    assert refs["turn_trace_id"] == "trace.builder.1"
    assert refs["message_id"] == "msg.builder.1"
    risk = published[0]["metadata"]["approval_policy"]["action_risk"]
    assert risk["schema"] == "adaos.conversation.action_risk.v1"
    assert risk["risk_class"] == "local_write"


def test_update_current_scenario_adds_card_view(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "draft": {"draft_id": "draft.shopping"}, "artifact_root": str(artifact_root)}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    skill.create_scenario_draft("создай список покупок", webspace_id="builder-skill-cards")

    result = skill.update_current_scenario("покажи ответы карточками", webspace_id="builder-skill-cards")

    assert result["ok"] is True
    assert result["patch"]["operation"] == "change_view_representation"
    assert any(item["type"] == "card_list" for item in result["preview_state"]["current_ui"]["children"])


def test_card_view_hides_table_in_generated_page_schema(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "todo_cards"

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            (artifact_root / "scenario.json").write_text(
                '{"id":"todo_cards","version":"0.1.0","name":"todo_cards","steps":[]}',
                encoding="utf-8",
            )
            return {"ok": True, "draft": {"draft_id": "draft.todo.cards"}, "artifact_root": str(artifact_root)}

    class _Workbench:
        def set_active_draft(self, **kwargs):
            return {"dev_webspace_id": "builder-cards-dev", "active_draft_id": kwargs.get("active_draft_id")}

        def snapshot(self, *args, **kwargs):
            return {"preview_state": kwargs.get("preview_state") or {}}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})

    skill.create_scenario_draft("create todo list", webspace_id="builder-cards")
    result = skill.update_current_scenario("Покажи список карточками", webspace_id="builder-cards")

    assert result["patch"]["diff"]["hide_table"] is True
    page = json.loads((artifact_root / "scenario.json").read_text(encoding="utf-8"))
    widgets = page["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    assert any(item["id"] == "prototype-cards" for item in widgets)
    assert not any(item["id"] == "prototype-table" for item in widgets)
    cards = next(item for item in widgets if item["id"] == "prototype-cards")
    assert cards["inputs"]["previewKey"]


def test_update_current_scenario_swaps_input_and_cards(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "todo_swap"

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            (artifact_root / "scenario.json").write_text(
                '{"id":"todo_swap","version":"0.1.0","name":"todo_swap","steps":[]}',
                encoding="utf-8",
            )
            return {"ok": True, "draft": {"draft_id": "draft.todo.swap"}, "artifact_root": str(artifact_root)}

    class _Workbench:
        def set_active_draft(self, **kwargs):
            return {"dev_webspace_id": "builder-swap-dev", "active_draft_id": kwargs.get("active_draft_id")}

        def snapshot(self, *args, **kwargs):
            return {"preview_state": kwargs.get("preview_state") or {}}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})

    skill.create_scenario_draft("create todo list", webspace_id="builder-swap")
    result = skill.update_current_scenario("Переставь местами область Input и Cards", webspace_id="builder-swap")

    assert result["patch"]["operation"] == "swap_layout_areas"
    assert result["preview_state"]["layout_order"] == "cards_first"
    page = json.loads((artifact_root / "scenario.json").read_text(encoding="utf-8"))
    widgets = page["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    form = next(item for item in widgets if item["id"] == "prototype-form")
    cards = next(item for item in widgets if item["id"] == "prototype-cards")
    assert form["area"] == "right"
    assert cards["area"] == "main"
    assert cards["inputs"]["previewKey"]
    assert not any(item["id"] == "prototype-table" for item in widgets)


def test_update_current_scenario_adds_execution_checkbox(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "todo_checkbox"

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "draft": {"draft_id": "draft.todo.checkbox"}, "artifact_root": str(artifact_root)}

    class _Workbench:
        def set_active_draft(self, **kwargs):
            return {"dev_webspace_id": "builder-checkbox-dev", "active_draft_id": kwargs.get("active_draft_id")}

        def snapshot(self, *args, **kwargs):
            return {"preview_state": kwargs.get("preview_state") or {}}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})

    skill.create_scenario_draft("create todo list", webspace_id="builder-checkbox")
    result = skill.update_current_scenario("Добавь чекбокс исполнения", webspace_id="builder-checkbox")

    assert result["patch"]["operation"] == "add_field"
    field = next(item for item in result["preview_state"]["datasources"][0]["fields"] if item["id"] == "done")
    assert field["type"] == "boolean"
    assert field["label"] == "\u0418\u0441\u043f\u043e\u043b\u043d\u0435\u043d\u043e"


def test_update_current_scenario_uses_llm_webui_fallback(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "llm_fallback"
    monkeypatch.setenv("ADAOS_BUILDER_LLM_IN_TESTS", "1")

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            (artifact_root / "scenario.json").write_text(
                '{"id":"llm_fallback","version":"0.1.0","name":"llm_fallback","steps":[]}',
                encoding="utf-8",
            )
            return {"ok": True, "draft": {"draft_id": "draft.llm"}, "artifact_root": str(artifact_root)}

    class _Workbench:
        def set_active_draft(self, **kwargs):
            return {"dev_webspace_id": "builder-llm-dev", "active_draft_id": kwargs.get("active_draft_id")}

        def snapshot(self, *args, **kwargs):
            return {"preview_state": kwargs.get("preview_state") or {}}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})

    created = skill.create_scenario_draft("create todo list", webspace_id="builder-llm")
    preview = dict(created["preview_state"])
    preview["title"] = "English Todo"
    payload = {"schema": "adaos.webui.prototype.v1", "generated_by": "builder_skill", "preview_state": preview}
    monkeypatch.setattr(
        skill,
        "_apply_llm_webui_transform",
        lambda **_kwargs: {"ok": True, "payload": payload, "preview_state": preview, "validation": {"ok": True}},
    )

    result = skill.update_current_scenario("Напиши текст на английском языке", webspace_id="builder-llm")

    assert result["patch"]["operation"] == "llm_webui_transform"
    assert result["ui_revision"]["revision"] == "002"

    result = skill.update_current_scenario("Сделай более компактный ввод", webspace_id="builder-llm")

    assert result["patch"]["operation"] == "llm_webui_transform"
    assert result["preview_state"]["title"] == "English Todo"
    assert result["ui_revision"]["revision"] == "003"
    saved = json.loads((artifact_root / "webui.json").read_text(encoding="utf-8"))
    assert saved["preview_state"]["title"] == "English Todo"


def test_set_ui_revision_current_restores_stored_webui(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "revision_restore"

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            (artifact_root / "scenario.json").write_text(
                '{"id":"revision_restore","version":"0.1.0","name":"revision_restore","steps":[]}',
                encoding="utf-8",
            )
            return {"ok": True, "draft": {"draft_id": "draft.revision"}, "artifact_root": str(artifact_root)}

    class _Workbench:
        def set_active_draft(self, **kwargs):
            return {"dev_webspace_id": "builder-revision-dev", "active_draft_id": kwargs.get("active_draft_id")}

        def snapshot(self, *args, **kwargs):
            return {"preview_state": kwargs.get("preview_state") or {}}

    import adaos.services.builder.workspace as workspace
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(pending_actions, "publish_pending_action", lambda **kwargs: {"id": "pa.builder.revision"})

    created = skill.create_scenario_draft("create todo list", webspace_id="builder-revision")
    assert created["ui_revision"]["revision"] == "001"
    created_revision = json.loads((artifact_root / "ui_revisions" / "001.json").read_text(encoding="utf-8"))
    assert created_revision["preview_state"]["version"] == "v001"
    assert created_revision["after_webui"]["preview_state"]["version"] == "v001"
    updated = skill.update_current_scenario("show cards", webspace_id="builder-revision")
    assert updated["ui_revision"]["revision"] == "002"
    updated_revision = json.loads((artifact_root / "ui_revisions" / "002.json").read_text(encoding="utf-8"))
    assert updated_revision["preview_state"]["version"] == "v002"
    assert updated_revision["after_webui"]["preview_state"]["version"] == "v002"
    assert any(item["type"] == "card_list" for item in updated["preview_state"]["current_ui"]["children"])

    restored = skill.set_ui_revision_current("001", webspace_id="builder-revision")

    assert restored["ok"] is True
    assert restored["revision"] == "001"
    assert not any(item["type"] == "card_list" for item in restored["preview_state"]["current_ui"]["children"])
    saved = json.loads((artifact_root / "webui.json").read_text(encoding="utf-8"))
    assert not any(item["type"] == "card_list" for item in saved["preview_state"]["current_ui"]["children"])


def test_write_webui_keeps_builder_skill_out_of_runtime_dependencies(tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "prototype"
    artifact_root.mkdir(parents=True)
    (artifact_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": "prototype",
                "name": "prototype",
                "depends": ["builder_skill", "voice_chat_skill"],
                "runtime": {"skills": {"required": ["builder_skill", "voice_chat_skill"]}},
            }
        ),
        encoding="utf-8",
    )
    preview = {
        "title": "Prototype",
        "current_ui": {
            "id": "prototype",
            "type": "page",
            "children": [
                {"id": "editor", "type": "section", "children": []},
                {"id": "items_table", "type": "table", "columns": [], "visible": True},
            ],
        },
        "datasources": [{"id": "items", "fields": []}],
        "mock_data": {"items": []},
    }

    skill._write_webui(str(artifact_root), preview)

    scenario = json.loads((artifact_root / "scenario.json").read_text(encoding="utf-8"))
    manifest = (artifact_root / "scenario.yaml").read_text(encoding="utf-8")
    assert "builder_skill" not in scenario["depends"]
    assert "builder_skill" not in scenario["runtime"]["skills"]["required"]
    assert "voice_chat_skill" in scenario["depends"]
    assert "voice_chat_skill" in manifest
    assert "builder_skill" not in manifest


def test_chat_meta_uses_prompt_project_topic_for_selected_scenario() -> None:
    skill = _load_module()

    meta = skill._chat_meta(
        None,
        webspace_id="desktop",
        session={"scenario_id": "todo_list_5b9319fa"},
        binding={"runtime_scenario_id": "todo_list_5b9319fa"},
    )

    assert meta["conversation_topic_id"] == "prompt-project:scenario:todo_list_5b9319fa"


def test_chat_first_idea_creates_preview_and_accepts_correction(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "first_idea"
    emitted: list[dict] = []
    published: list[dict] = []

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            (artifact_root / "scenario.json").write_text(
                '{"id":"first_idea","version":"0.1.0","name":"first_idea","steps":[]}',
                encoding="utf-8",
            )
            return {
                "ok": True,
                "draft": {"draft_id": "draft.first.idea"},
                "artifact_root": str(artifact_root),
                "kwargs": kwargs,
            }

    class _Workbench:
        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None, runtime_scenario_id=None, persist_projection=True):
            return {
                "source_webspace_id": source_webspace_id,
                "dev_webspace_id": f"{source_webspace_id}-dev",
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
                "dialog": {"widget": "voice_chat", "dialog_channel_id": "builder"},
            }

        def snapshot(self, webspace_id, *, preview_state=None):
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    import adaos.services.builder.workspace as workspace
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(skill, "_safe_emit_chat", lambda text, **kwargs: emitted.append({"text": text, "kwargs": kwargs}))
    monkeypatch.setattr(
        pending_actions,
        "publish_pending_action",
        lambda **kwargs: published.append(dict(kwargs)) or {"id": f"pa.builder.{len(published)}", "kind": kwargs["kind"]},
    )

    created = skill.chat("I have an idea. Let's build it.", webspace_id="builder-first-idea")

    assert created["ok"] is True
    assert created["scenario_id"].startswith("i_have_an_idea_let_s_build_it")
    assert created["dialog"]["dialog_channel_id"] == "builder"
    assert created["preview_state"]["current_ui"]["type"] == "page"
    assert created["preview_state"]["user_summary"]["assumptions"]
    assert "Assumptions:" in created["message"]
    assert (artifact_root / "webui.json").exists()
    assert published[0]["kind"] == "builder.scenario_draft.review"
    assert emitted[0]["kwargs"]["topic_ref"]["thread_id"] == created["topic"]["thread_id"]

    updated = skill.chat("show the result as cards", webspace_id="builder-first-idea")

    assert updated["ok"] is True
    assert updated["patch"]["operation"] == "change_view_representation"
    assert updated["topic"]["thread_id"] == created["topic"]["thread_id"]
    assert any(item["type"] == "card_list" for item in updated["preview_state"]["current_ui"]["children"])
    assert "card_list" in (artifact_root / "webui.json").read_text(encoding="utf-8")
    assert published[-1]["kind"] == "builder.scenario_patch.review"


def test_chat_guides_underspecified_first_idea(monkeypatch) -> None:
    skill = _load_module()
    emitted: list[dict] = []

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            return {
                "source_webspace_id": webspace_id,
                "dev_webspace_id": f"{webspace_id}-dev",
                "dialog": {"dialog_channel_id": "builder"},
            }

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_safe_emit_chat", lambda text, **kwargs: emitted.append({"text": text, "kwargs": kwargs}))

    result = skill.chat("I have an idea", webspace_id="builder-clarify")

    assert result["ok"] is True
    assert result["status"] == "clarification_required"
    assert result["needs_clarification"] is True
    assert result["dialog"]["dialog_channel_id"] == "builder"
    assert result["topic"]["thread_id"].startswith("thread.builder.builder-clarify")
    assert result["clarification"]["schema"] == "adaos.builder.guided_clarification.v1"
    assert [item["id"] for item in result["clarification"]["questions"]] == [
        "user_goal",
        "primary_objects",
        "first_action",
    ]
    assert result["clarification"]["next_turn_policy"]["creates_draft_when_answered"] is True
    assert "scenario_id" not in result
    assert emitted[0]["kwargs"]["topic_ref"]["thread_id"] == result["topic"]["thread_id"]


def test_update_current_scenario_handles_layout_column_and_date_requests(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "scenario.json").write_text(
        '{"id":"shopping_list","version":"0.1.0","name":"shopping_list","steps":[]}',
        encoding="utf-8",
    )

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            return {
                "source_webspace_id": webspace_id,
                "dev_webspace_id": f"{webspace_id}-dev",
                "active_draft_id": "draft.shopping",
                "runtime_scenario_id": "shopping_list",
            }

        def set_active_draft(self, **kwargs):
            return {
                "source_webspace_id": kwargs.get("source_webspace_id"),
                "dev_webspace_id": f"{kwargs.get('source_webspace_id')}-dev",
                "active_draft_id": kwargs.get("active_draft_id"),
                "runtime_scenario_id": kwargs.get("runtime_scenario_id"),
            }

        def snapshot(self, webspace_id, *, preview_state=None):
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(pending_actions, "publish_pending_action", lambda **kwargs: {"id": "pa.builder.layout"})
    skill._save_session(
        "builder-layout",
        {
            "id": "builder_session_layout",
            "webspace_id": "builder-layout",
            "status": "drafting",
            "title": "Shopping list",
            "scenario_id": "shopping_list",
            "draft_id": "draft.shopping",
            "artifact_root": str(artifact_root),
            "datasource_id": "shopping_items",
            "fields": [
                {"id": "item", "type": "string", "label": "Товар", "required": True},
                {"id": "quantity", "type": "number", "label": "Кол-во", "required": False},
                {"id": "category", "type": "string", "label": "Категория", "required": False},
                {"id": "done", "type": "boolean", "label": "Куплено", "required": False},
            ],
            "patches": [],
            "version": "v1",
        },
    )

    moved = skill.update_current_scenario("Переместим кнопку Add над формой", webspace_id="builder-layout")
    assert moved["patch"]["operation"] == "move_form_action"
    form = next(item for item in moved["preview_state"]["current_ui"]["children"] if item["id"] == "editor")
    assert form["action_position"] == "top"
    scenario = yaml.safe_load((artifact_root / "scenario.json").read_text(encoding="utf-8"))
    page_schema = scenario["ui"]["application"]["desktop"]["pageSchema"]
    page_form = next(item for item in page_schema["widgets"] if item["id"] == "prototype-form")
    assert page_form["inputs"]["submitPlacement"] == "top"

    checkbox = skill.update_current_scenario("Сделаем первой колонкой таблицы чекбокс (куплено)", webspace_id="builder-layout")
    assert checkbox["patch"]["operation"] == "set_checkbox_column"
    assert checkbox["preview_state"]["datasources"][0]["fields"][0]["id"] == "done"
    page_schema = yaml.safe_load((artifact_root / "scenario.json").read_text(encoding="utf-8"))["ui"]["application"]["desktop"]["pageSchema"]
    page_table = next(item for item in page_schema["widgets"] if item["id"] == "prototype-table")
    assert page_table["inputs"]["columns"][0] == {"key": "done", "label": "Куплено", "kind": "boolean", "width": "72px"}

    date_result = skill.update_current_scenario("Добвь данные в поле дата в таблицу", webspace_id="builder-layout")
    assert date_result["patch"]["operation"] == "add_field"
    assert any(item["id"] == "date" and item["type"] == "date" for item in date_result["preview_state"]["datasources"][0]["fields"])
    rows = date_result["preview_state"]["mock_data"]["shopping_items"]
    assert [row["date"] for row in rows] == ["2026-07-01", "2026-07-02", "2026-07-03"]

    filled = skill.update_current_scenario(
        'Заполни колонку дата не словом "дата", а произвольными значениями типа дата',
        webspace_id="builder-layout",
    )
    assert filled["patch"]["operation"] == "update_mock_data"
    assert [row["date"] for row in filled["preview_state"]["mock_data"]["shopping_items"]] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    ]


def test_update_current_scenario_publishes_patch_pending_action(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    published: list[dict] = []

    class _Service:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **_kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "draft": {"draft_id": "draft.shopping"}, "artifact_root": str(artifact_root)}

    import adaos.services.builder.workspace as workspace
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)
    monkeypatch.setattr(
        pending_actions,
        "publish_pending_action",
        lambda **kwargs: published.append(dict(kwargs)) or {"id": f"pa.builder.{len(published)}"},
    )

    skill.create_scenario_draft("create shopping list", webspace_id="builder-pa-patch")
    result = skill.update_current_scenario(
        "show cards",
        webspace_id="builder-pa-patch",
        _meta={"turn_trace_id": "trace.patch.1", "conversation_id": "conv.skill.builder_skill.default.builder-pa-patch"},
    )

    patch_actions = [item for item in published if item["kind"] == "builder.scenario_patch.review"]
    assert patch_actions
    action = patch_actions[-1]
    assert action["domain_ref"]["patch_id"] == result["patch"]["id"]
    assert action["metadata"]["source_refs"]["patch_id"] == result["patch"]["id"]
    assert action["metadata"]["source_refs"]["turn_trace_id"] == "trace.patch.1"
    assert action["metadata"]["approval_policy"]["action_risk"]["risk_class"] == "local_write"
    assert result["patch"]["pending_action_id"] == "pa.builder.2"


def test_update_current_scenario_adds_product_units_and_filters(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    artifact_root.mkdir(parents=True)
    (artifact_root / "scenario.json").write_text(
        '{"id":"shopping_list","version":"0.1.0","name":"shopping_list","steps":[]}',
        encoding="utf-8",
    )

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            return {}

        def set_active_draft(self, **kwargs):
            return dict(kwargs)

        def snapshot(self, webspace_id, *, preview_state=None):
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(pending_actions, "publish_pending_action", lambda **kwargs: {"id": "pa.builder.filters"})
    skill._save_session(
        "builder-filters",
        {
            "id": "builder_session_filters",
            "webspace_id": "builder-filters",
            "status": "drafting",
            "title": "Shopping list",
            "scenario_id": "shopping_list",
            "draft_id": "draft.shopping",
            "artifact_root": str(artifact_root),
            "datasource_id": "shopping_items",
            "fields": [
                {"id": "item", "type": "string", "label": "Товар", "required": True},
                {"id": "quantity", "type": "number", "label": "Кол-во", "required": False},
                {"id": "done", "type": "boolean", "label": "Куплено", "required": False},
            ],
            "patches": [],
            "version": "v1",
        },
    )

    unit_result = skill.update_current_scenario("Добавь меру по товарам. Типа. шт., кг, г., л.", webspace_id="builder-filters")
    assert unit_result["patch"]["operation"] == "add_field"
    assert any(item["id"] == "unit" and item["options"] == ["шт", "кг", "г", "л"] for item in unit_result["preview_state"]["datasources"][0]["fields"])

    filter_result = skill.update_current_scenario("Добавь поле Наличие. Добавь фильтр по Куплено и Наличие.", webspace_id="builder-filters")
    assert filter_result["patch"]["operation"] == "multi_update"
    assert filter_result["patch"]["diff"]["not_implemented"] == []
    filters = filter_result["preview_state"]["filters"]
    assert {item["field_id"] for item in filters} == {"done", "availability"}

    page_schema = yaml.safe_load((artifact_root / "scenario.json").read_text(encoding="utf-8"))["ui"]["application"]["desktop"]["pageSchema"]
    widget_ids = {widget["id"] for widget in page_schema["widgets"]}
    assert {"prototype-filter-done", "prototype-filter-availability", "prototype-table"}.issubset(widget_ids)
    table = next(widget for widget in page_schema["widgets"] if widget["id"] == "prototype-table")
    assert {item["key"] for item in table["inputs"]["filters"]} == {"done", "availability"}


def test_builder_pending_action_approve_marks_patch_and_emits_chat(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    artifact_root.mkdir(parents=True)
    (artifact_root / "scenario.json").write_text(
        '{"id":"shopping_list","version":"0.1.0","name":"shopping_list","steps":[]}',
        encoding="utf-8",
    )
    emitted: list[str] = []

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            return {}

        def set_active_draft(self, **kwargs):
            return dict(kwargs)

        def snapshot(self, webspace_id, *, preview_state=None):
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    import adaos.sdk.io.out as io_out

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(io_out, "chat_append", lambda text, **_kwargs: emitted.append(text))
    skill._save_session(
        "builder-approve",
        {
            "id": "builder_session_approve",
            "webspace_id": "builder-approve",
            "status": "drafting",
            "title": "Shopping list",
            "scenario_id": "shopping_list",
            "draft_id": "draft.shopping",
            "artifact_root": str(artifact_root),
            "datasource_id": "shopping_items",
            "fields": [{"id": "item", "type": "string", "label": "Товар", "required": True}],
            "patches": [{"id": "patch_1", "operation": "add_field", "status": "applied", "pending_action_id": "pa.builder.1"}],
            "pending_action_id": "pa.builder.1",
            "version": "v2",
        },
    )

    asyncio.run(
        skill._on_builder_pending_action_response(
            {
                "pending_action_id": "pa.builder.1",
                "response_action_id": "approve",
                "webspace_id": "builder-approve",
                "domain_ref": {
                    "session_id": "builder_session_approve",
                    "scenario_id": "shopping_list",
                    "patch_id": "patch_1",
                },
                "pending_action": {"id": "pa.builder.1", "webspace_id": "builder-approve"},
                "response": {"response_action_id": "approve"},
            }
        )
    )

    session = skill._load_session("builder-approve", "builder_session_approve")
    assert session["patches"][0]["review_status"] == "approved"
    assert "pending_action_id" not in session
    assert any("утверждены" in text for text in emitted)


def test_chat_from_dev_webspace_updates_source_session_and_mirrors_response(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "scenario.json").write_text(
        '{"id":"shopping_list","version":"0.1.0","name":"shopping_list","steps":[]}',
        encoding="utf-8",
    )
    emitted: list[dict] = []

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            assert webspace_id == "desktop"
            return {
                "source_webspace_id": "desktop",
                "dev_webspace_id": "desktop-dev",
                "active_draft_id": "draft.shopping",
                "runtime_scenario_id": "shopping_list",
            }

        def set_active_draft(self, **kwargs):
            return {
                "source_webspace_id": kwargs.get("source_webspace_id"),
                "dev_webspace_id": "desktop-dev",
                "active_draft_id": kwargs.get("active_draft_id"),
                "runtime_scenario_id": kwargs.get("runtime_scenario_id"),
            }

        def snapshot(self, webspace_id, *, preview_state=None):
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    import adaos.sdk.io.out as io_out
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(pending_actions, "publish_pending_action", lambda **kwargs: {"id": "pa.sample"})
    monkeypatch.setattr(
        io_out,
        "chat_append",
        lambda text, *, from_="hub", msg_id=None, ts=None, _meta=None: emitted.append({"text": text, "meta": dict(_meta or {})}) or {"ok": True},
    )
    skill._save_session(
        "desktop",
        {
            "id": "builder_session_test",
            "webspace_id": "desktop",
            "status": "drafting",
            "title": "Shopping list",
            "scenario_id": "shopping_list",
            "draft_id": "draft.shopping",
            "artifact_root": str(artifact_root),
            "datasource_id": "shopping_items",
            "fields": [
                {"id": "item", "type": "string", "label": "Товар", "required": True},
                {"id": "quantity", "type": "number", "label": "Кол-во", "required": False},
                {"id": "category", "type": "string", "label": "Категория", "required": False},
                {"id": "done", "type": "boolean", "label": "Куплено", "required": False},
                {"id": "price", "type": "number", "label": "Цена", "required": False},
            ],
            "patches": [],
            "version": "v1",
        },
    )

    result = skill.chat("Сделай пример данных на основе продуктов питания", webspace_id="desktop-dev")

    assert result["ok"] is True
    assert result["patch"]["operation"] == "update_mock_data"
    rows = result["preview_state"]["mock_data"]["shopping_items"]
    assert rows[0]["item"] == "Молоко"
    assert {item["meta"]["webspace_id"] for item in emitted} == {"desktop", "desktop-dev"}


def test_chat_requires_selected_builder_target(monkeypatch) -> None:
    skill = _load_module()

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            return {
                "source_webspace_id": webspace_id,
                "dev_webspace_id": f"{webspace_id}-dev",
                "active_draft_id": None,
                "runtime_scenario_id": "demo_scenario",
            }

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_safe_emit_chat", lambda *args, **kwargs: None)

    result = skill.chat("добавь поле цена", webspace_id="desktop")

    assert result["ok"] is True
    assert result["status"] == "target_required"
    assert result["needs_selection"] is True
    assert "demo_scenario" in result["message"]


def test_builder_command_parser_prioritises_project_commands() -> None:
    skill = _load_module()

    switch = skill._parse_builder_command("\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c, \u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438\u0441\u044c \u043d\u0430 \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439 demo_scenario", has_session=True)
    delete_field = skill._parse_builder_command("\u0443\u0434\u0430\u043b\u0438 \u043f\u043e\u043b\u0435 \u0446\u0435\u043d\u0430", has_session=True)
    create = skill._parse_builder_command("\u0441\u043e\u0437\u0434\u0430\u0439 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u0441\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u043a\u0443\u043f\u043e\u043a", has_session=True)

    assert switch["intent"] == "project.switch"
    assert switch["project_ref"] == "demo_scenario"
    assert delete_field["intent"] == "none"
    assert create["intent"] == "project.create"


def test_prompt_project_selection_defers_heavy_events(monkeypatch) -> None:
    skill = _load_module()
    calls: list[str] = []
    async_seen = threading.Event()

    import adaos.sdk.data.events as events

    def _publish(topic, payload, source=None):
        calls.append(topic)
        if topic == "prompt.project.changed":
            time.sleep(0.3)
        if topic == "builder.preview.selected":
            async_seen.set()

    monkeypatch.setattr(events, "publish", _publish)

    started = time.perf_counter()
    result = skill._publish_prompt_project_selection(
        "desktop",
        session={"scenario_id": "todo_list", "draft_id": "draft.todo"},
        reason="test",
    )
    elapsed = time.perf_counter() - started

    assert result["ok"] is True
    assert result["published"] == ["scenario.workflow.set_state"]
    assert result["scheduled"] == ["prompt.project.changed", "builder.preview.selected"]
    assert elapsed < 0.2
    assert calls[:1] == ["scenario.workflow.set_state"]
    assert async_seen.wait(timeout=1.0)


def test_chat_handles_builder_project_commands(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    emitted: list[dict] = []
    published: list[dict] = []
    calls: list[dict] = []
    binding = {
        "source_webspace_id": "desktop",
        "dev_webspace_id": "desktop-dev",
        "active_draft_id": "draft.beta",
        "runtime_scenario_id": "beta_scenario",
    }

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            return dict(binding)

        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None, runtime_scenario_id=None, persist_projection=True):
            binding.update(
                {
                    "source_webspace_id": source_webspace_id,
                    "dev_webspace_id": f"{source_webspace_id}-dev",
                    "active_draft_id": active_draft_id,
                    "runtime_scenario_id": runtime_scenario_id,
                }
            )
            calls.append(
                {
                    "method": "set_active_draft",
                    "active_draft_id": active_draft_id,
                    "runtime_scenario_id": runtime_scenario_id,
                    "persist_projection": persist_projection,
                }
            )
            return dict(binding)

        def snapshot(self, webspace_id, *, preview_state=None):
            calls.append({"method": "snapshot", "webspace_id": webspace_id})
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(skill, "_safe_emit_chat", lambda text, **kwargs: emitted.append({"text": text, "kwargs": kwargs}))
    monkeypatch.setattr(
        pending_actions,
        "publish_pending_action",
        lambda **kwargs: published.append(dict(kwargs)) or {"id": f"pa.builder.{len(published)}", "kind": kwargs["kind"]},
    )

    base_session = {
        "webspace_id": "desktop",
        "status": "drafting",
        "datasource_id": "items",
        "fields": [{"id": "title", "type": "string", "label": "Title", "required": True}],
        "patches": [],
        "version": "v1",
        "artifact_root": str(tmp_path),
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    skill._save_session(
        "desktop",
        {
            **base_session,
            "id": "session_alpha",
            "title": "Alpha",
            "scenario_id": "alpha_scenario",
            "draft_id": "draft.alpha",
        },
    )
    skill._save_session(
        "desktop",
        {
            **base_session,
            "id": "session_beta",
            "title": "Beta",
            "scenario_id": "beta_scenario",
            "draft_id": "draft.beta",
        },
    )

    listed = skill.chat("\u043f\u043e\u043a\u0430\u0436\u0438 \u043f\u0440\u043e\u0435\u043a\u0442\u044b", webspace_id="desktop")
    current = skill.chat("\u0447\u0442\u043e \u0432\u044b\u0431\u0440\u0430\u043d\u043e", webspace_id="desktop")
    switched = skill.chat("\u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438\u0441\u044c \u043d\u0430 \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439 alpha_scenario", webspace_id="desktop")
    delete = skill.chat("\u0443\u0434\u0430\u043b\u0438 \u0442\u0435\u043a\u0443\u0449\u0438\u0439", webspace_id="desktop")

    assert listed["status"] == "project_list"
    assert {item["scenario_id"] for item in listed["items"]} == {"alpha_scenario", "beta_scenario"}
    assert current["status"] == "project_current"
    assert current["scenario_id"] == "beta_scenario"
    assert switched["status"] == "project_switched"
    assert switched["scenario_id"] == "alpha_scenario"
    assert binding["active_draft_id"] == "draft.alpha"
    assert binding["runtime_scenario_id"] == "alpha_scenario"
    assert delete["status"] == "delete_review_required"
    assert delete["pending_action"]["id"] == "pa.builder.1"
    assert published[0]["kind"] == "builder.scenario_delete.review"
    assert published[0]["domain_ref"]["operation"] == "delete_draft"
    assert published[0]["domain_ref"]["draft_id"] == "draft.alpha"
    assert emitted[-1]["kwargs"]["topic_ref"]["thread_id"] == delete["topic"]["thread_id"]
    assert any(item["method"] == "set_active_draft" and item["active_draft_id"] == "draft.alpha" for item in calls)


def test_builder_delete_pending_action_approve_deletes_draft(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    calls: list[dict] = []
    emitted: list[dict] = []

    class _Workbench:
        def get_workspace_binding(self, webspace_id):
            return {
                "source_webspace_id": webspace_id,
                "dev_webspace_id": f"{webspace_id}-dev",
                "active_draft_id": "draft.to_delete",
                "runtime_scenario_id": "delete_scenario",
            }

        def delete_development_skill(self, draft_id, webspace_id):
            calls.append({"method": "delete", "draft_id": draft_id, "webspace_id": webspace_id})
            return {"ok": True, "draft_id": draft_id}

        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None, runtime_scenario_id=None, persist_projection=True):
            calls.append({"method": "set_active_draft", "active_draft_id": active_draft_id})
            return {
                "source_webspace_id": source_webspace_id,
                "dev_webspace_id": f"{source_webspace_id}-dev",
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
            }

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_safe_emit_chat", lambda text, **kwargs: emitted.append({"text": text, "kwargs": kwargs}))
    skill._save_session(
        "desktop",
        {
            "id": "session_delete",
            "webspace_id": "desktop",
            "status": "drafting",
            "title": "Delete me",
            "scenario_id": "delete_scenario",
            "draft_id": "draft.to_delete",
            "artifact_root": str(tmp_path),
            "datasource_id": "items",
            "fields": [{"id": "title", "type": "string", "label": "Title"}],
            "patches": [],
            "version": "v1",
        },
    )

    asyncio.run(
        skill._on_builder_pending_action_response(
            {
                "webspace_id": "desktop",
                "response_action_id": "approve",
                "pending_action_id": "pa.delete",
                "domain_ref": {
                    "session_id": "session_delete",
                    "scenario_id": "delete_scenario",
                    "draft_id": "draft.to_delete",
                    "operation": "delete_draft",
                },
            }
        )
    )

    assert calls[0] == {"method": "delete", "draft_id": "draft.to_delete", "webspace_id": "desktop"}
    assert skill._load_session("desktop", "session_delete") is None
    assert "draft.to_delete" in emitted[0]["text"]


def test_builder_skill_exposes_workbench_tools() -> None:
    manifest = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))

    tools = {item["name"] for item in manifest["tools"]}
    assert {
        "ensure_dev_webspace",
        "get_workspace_binding",
        "open_dev_webspace",
        "attach_dialog_widget",
        "set_active_draft",
        "list_development_skills",
        "delete_development_skill",
    }.issubset(tools)
    routes = {item["path"] for item in manifest["data_routes"]}
    assert "data.builder" in routes


def test_get_session_exposes_developer_evidence(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    artifact_root.mkdir(parents=True)
    (artifact_root / "webui.json").write_text('{"preview_state":{}}', encoding="utf-8")
    (artifact_root / "scenario.json").write_text('{"id":"shopping_list"}', encoding="utf-8")

    class _Workbench:
        def set_active_draft(self, **kwargs):
            return {
                "source_webspace_id": kwargs.get("source_webspace_id"),
                "dev_webspace_id": f"{kwargs.get('source_webspace_id')}-dev",
                "active_draft_id": kwargs.get("active_draft_id"),
                "runtime_scenario_id": kwargs.get("runtime_scenario_id"),
            }

        def snapshot(self, webspace_id, *, preview_state=None):
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(skill, "_request_workbench_refresh", lambda payload: {"ok": True, "payload": dict(payload)})
    monkeypatch.setattr(
        skill,
        "_builder_topic_ref",
        lambda webspace_id, **_kwargs: {
            "schema": "adaos.conversation.topic_ref.v1",
            "topic_id": f"builder:{webspace_id}:shopping_list",
            "thread_id": f"thread.builder.{webspace_id}.shopping_list",
            "conversation_id": f"conv.skill.builder_skill.default.{webspace_id}",
            "channel_id": "builder",
            "owner": "skill:builder_skill",
        },
    )
    skill._save_session(
        "builder-evidence",
        {
            "id": "builder_session_evidence",
            "webspace_id": "builder-evidence",
            "status": "drafting",
            "title": "Shopping list",
            "scenario_id": "shopping_list",
            "draft_id": "draft.shopping",
            "artifact_root": str(artifact_root),
            "datasource_id": "shopping_items",
            "fields": [{"id": "item", "type": "string", "label": "Item", "required": True}],
            "preview_state": {
                "current_ui": {"type": "page"},
                "datasources": [{"id": "shopping_items", "type": "internal_crud"}],
                "pending_patches": [{"id": "patch_1"}],
            },
            "patches": [
                {
                    "id": "patch_1",
                    "operation": "add_field",
                    "status": "applied",
                    "pending_action_id": "pa.patch",
                    "diff": {"fields": [{"id": "price"}], "not_implemented": []},
                }
            ],
            "pending_action_id": "pa.draft",
            "version": "v2",
        },
    )

    session_result = skill.get_session(webspace_id="builder-evidence")
    evidence = session_result["developer_evidence"]

    assert session_result["ok"] is True
    assert evidence["schema"] == "adaos.builder.developer_evidence.v1"
    assert evidence["route_plan"]["thread_id"] == "thread.builder.builder-evidence.shopping_list"
    assert evidence["route_plan"]["default_tool"] == "builder_skill.chat"
    assert evidence["preview_refs"]["current_ui_type"] == "page"
    assert evidence["preview_refs"]["datasource_ids"] == ["shopping_items"]
    assert set(evidence["pending_action_ids"]) == {"pa.draft", "pa.patch"}
    assert evidence["patches"][0]["diff_keys"] == ["fields", "not_implemented"]
    files = {item["role"]: item for item in evidence["files"]}
    assert files["runtime_preview"]["exists"] is True
    assert files["scenario_manifest_json"]["exists"] is True

    preview_result = skill.get_preview_state(webspace_id="builder-evidence")
    assert preview_result["developer_evidence"]["preview_refs"]["pending_patch_count"] == 1


def test_create_scenario_draft_updates_builder_workbench(monkeypatch, tmp_path) -> None:
    skill = _load_module()
    artifact_root = tmp_path / "shopping_list"
    calls: list[dict] = []

    class _DraftService:
        @classmethod
        def from_context(cls):
            return cls()

        def create_draft(self, **kwargs):
            artifact_root.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "draft": {"draft_id": "draft.shopping"}, "artifact_root": str(artifact_root), "kwargs": kwargs}

    class _Workbench:
        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None, runtime_scenario_id=None, persist_projection=True):
            calls.append({
                "method": "set_active_draft",
                "webspace_id": source_webspace_id,
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
                "persist_projection": persist_projection,
            })
            return {
                "source_webspace_id": source_webspace_id,
                "dev_webspace_id": f"{source_webspace_id}-dev",
                "scenario_id": "prompt_engineer_scenario",
                "runtime_scenario_id": runtime_scenario_id,
                "active_draft_id": active_draft_id,
                "dialog": {"widget": "voice_chat", "dialog_channel_id": "builder"},
            }

        def snapshot(self, webspace_id, *, preview_state=None):
            calls.append({"method": "snapshot", "webspace_id": webspace_id, "preview_state": preview_state})
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _DraftService)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(
        skill,
        "_request_workbench_refresh",
        lambda payload: calls.append({"method": "event", "payload": dict(payload)}) or {"ok": True},
    )

    result = skill.create_scenario_draft("Builder, create a shopping list app", webspace_id="desktop")

    assert result["ok"] is True
    assert result["workbench"]["binding"]["dev_webspace_id"] == "desktop-dev"
    assert result["workbench"]["binding"]["active_draft_id"] == "draft.shopping"
    assert calls[0] == {
        "method": "set_active_draft",
        "webspace_id": "desktop",
        "active_draft_id": "draft.shopping",
        "runtime_scenario_id": result["scenario_id"],
        "persist_projection": False,
    }
    assert calls[1]["method"] == "snapshot"
    assert calls[1]["preview_state"]["current_ui"]["type"] == "page"
    assert calls[2]["method"] == "event"
    assert calls[2]["payload"]["runtime_scenario_id"] == result["scenario_id"]


def test_ensure_workbench_prefers_direct_dev_runtime_switch(monkeypatch) -> None:
    skill = _load_module()
    calls: list[dict] = []

    class _Workbench:
        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None, runtime_scenario_id=None, persist_projection=True):
            calls.append({
                "method": "set_active_draft",
                "source_webspace_id": source_webspace_id,
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
                "persist_projection": persist_projection,
            })
            return {
                "source_webspace_id": source_webspace_id,
                "dev_webspace_id": f"{source_webspace_id}-dev",
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
            }

        def snapshot(self, webspace_id, *, preview_state=None):
            calls.append({"method": "snapshot", "webspace_id": webspace_id, "preview_state": preview_state})
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

        def ensure_dev_webspace(self, source_webspace_id, *, active_draft_id=None, runtime_scenario_id=None, preview_state=None, wait_for_rebuild=None):
            calls.append({
                "method": "ensure_dev_webspace",
                "source_webspace_id": source_webspace_id,
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
                "preview_state": preview_state,
                "wait_for_rebuild": wait_for_rebuild,
            })
            return {
                "source_webspace_id": source_webspace_id,
                "dev_webspace_id": f"{source_webspace_id}-dev",
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
                "runtime": {"ok": True, "scenario_id": runtime_scenario_id},
            }

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(
        skill,
        "_request_workbench_refresh",
        lambda payload: calls.append({"method": "event", "payload": dict(payload)}) or {"ok": True},
    )

    result = skill._ensure_workbench(
        "desktop",
        active_draft_id="draft.todo",
        runtime_scenario_id="todo_scenario",
        preview_state={"title": "Todo"},
    )

    assert result["ok"] is True
    assert result["binding"]["runtime_scenario_id"] == "todo_scenario"
    assert result["projection"]["event"]["skipped"] == "direct_workbench_ensure"
    assert result["projection"]["direct"]["result"]["runtime"]["ok"] is True
    assert [item["method"] for item in calls] == ["set_active_draft", "snapshot", "ensure_dev_webspace"]
    assert calls[2]["runtime_scenario_id"] == "todo_scenario"
    assert calls[2]["wait_for_rebuild"] is False


def test_ensure_workbench_schedules_async_direct_runtime_switch(monkeypatch) -> None:
    skill = _load_module()
    calls: list[dict] = []

    class _Workbench:
        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None, runtime_scenario_id=None, persist_projection=True):
            calls.append({"method": "set_active_draft", "runtime_scenario_id": runtime_scenario_id})
            return {
                "source_webspace_id": source_webspace_id,
                "dev_webspace_id": f"{source_webspace_id}-dev",
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
            }

        def snapshot(self, webspace_id, *, preview_state=None):
            calls.append({"method": "snapshot", "webspace_id": webspace_id})
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

        async def ensure_dev_webspace(self, source_webspace_id, *, active_draft_id=None, runtime_scenario_id=None, preview_state=None, wait_for_rebuild=None):
            calls.append({"method": "ensure_dev_webspace", "runtime_scenario_id": runtime_scenario_id})
            await asyncio.sleep(1.0)
            return {"source_webspace_id": source_webspace_id, "runtime_scenario_id": runtime_scenario_id}

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(
        skill,
        "_request_workbench_refresh",
        lambda payload: calls.append({"method": "event", "payload": dict(payload)}) or {"ok": True, "payload": dict(payload)},
    )

    started = time.perf_counter()
    result = skill._ensure_workbench(
        "desktop",
        active_draft_id="draft.todo",
        runtime_scenario_id="todo_scenario",
        preview_state={"title": "Todo"},
    )
    elapsed = time.perf_counter() - started

    assert result["ok"] is True
    assert result["projection"]["direct"]["scheduled"] is True
    assert result["projection"]["direct"]["mode"] == "thread"
    assert result["projection"]["event"]["skipped"] == "direct_workbench_ensure"
    assert elapsed < 0.5
    assert [item["method"] for item in calls] == ["set_active_draft", "snapshot"]


def test_safe_emit_chat_does_not_wait_for_stuck_append(monkeypatch) -> None:
    skill = _load_module()

    import adaos.sdk.io.out as io_out

    calls: list[str] = []

    def _slow_chat_append(text, **_kwargs):
        calls.append(text)
        time.sleep(1.0)

    monkeypatch.setattr(skill, "CHAT_APPEND_TIMEOUT_S", 0.02)
    monkeypatch.setattr(io_out, "chat_append", _slow_chat_append)

    started = time.perf_counter()
    skill._safe_emit_chat("hello", webspace_id="desktop")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert calls


def test_workbench_tool_wrappers_use_voice_widget_and_active_draft(monkeypatch) -> None:
    skill = _load_module()
    calls: list[dict] = []

    class _Workbench:
        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None, runtime_scenario_id=None, persist_projection=True):
            calls.append({
                "method": "set_active_draft",
                "webspace_id": source_webspace_id,
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
                "persist_projection": persist_projection,
            })
            return {"source_webspace_id": source_webspace_id, "dev_webspace_id": f"{source_webspace_id}-dev", "active_draft_id": active_draft_id}

        def get_workspace_binding(self, webspace_id):
            return {"source_webspace_id": webspace_id, "dev_webspace_id": f"{webspace_id}-dev", "active_draft_id": "draft.one"}

        def open_dev_webspace(self, webspace_id, *, base_url=None):
            return {"ok": True, "url": f"{base_url}/?webspace={webspace_id}-dev", "webspace_id": f"{webspace_id}-dev"}

        def snapshot(self, webspace_id, *, preview_state=None):
            calls.append({"method": "snapshot", "webspace_id": webspace_id, "preview_state": preview_state})
            return {"source_webspace_id": webspace_id, "preview_state": preview_state or {}}

        def dialog_widget_config(self, webspace_id):
            return {"widget": "voice_chat", "dialog_channel_id": "builder", "source_webspace_id": webspace_id}

        def list_development_skills(self, webspace_id):
            return {"ok": True, "items": [{"draft_id": "draft.one", "active": True}], "active_draft_id": "draft.one"}

        def delete_development_skill(self, draft_id, webspace_id):
            calls.append({"method": "delete", "webspace_id": webspace_id, "draft_id": draft_id})
            return {"ok": True, "draft_id": draft_id}

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())
    monkeypatch.setattr(
        skill,
        "_request_workbench_refresh",
        lambda payload: calls.append({"method": "event", "payload": dict(payload)}) or {"ok": True},
    )

    assert skill.ensure_dev_webspace(webspace_id="desktop", active_draft_id="draft.one")["binding"]["dev_webspace_id"] == "desktop-dev"
    assert skill.get_workspace_binding(webspace_id="desktop")["binding"]["active_draft_id"] == "draft.one"
    assert skill.open_dev_webspace(webspace_id="desktop", base_url="http://localhost:8100")["url"] == "http://localhost:8100/?webspace=desktop-dev"
    assert skill.attach_dialog_widget(webspace_id="desktop")["widget"]["widget"] == "voice_chat"
    assert skill.set_active_draft("draft.two", webspace_id="desktop")["binding"]["active_draft_id"] == "draft.two"
    assert skill.list_development_skills(webspace_id="desktop")["items"][0]["draft_id"] == "draft.one"
    assert skill.delete_development_skill("draft.one", webspace_id="desktop")["ok"] is True
    assert calls[0] == {
        "method": "set_active_draft",
        "webspace_id": "desktop",
        "active_draft_id": "draft.one",
        "runtime_scenario_id": None,
        "persist_projection": False,
    }
    assert calls[-1] == {"method": "delete", "webspace_id": "desktop", "draft_id": "draft.one"}
