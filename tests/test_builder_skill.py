from __future__ import annotations

import importlib.util
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
        async def ensure_dev_webspace(self, *args, **kwargs):
            return {"dev_webspace_id": "builder-skill-test-dev"}

        def publish_projection_sync(self, *args, **kwargs):
            return {"ok": True}

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())

    result = skill.create_scenario_draft(
        idea="Строитель, создадим приложение список покупок",
        webspace_id="builder-skill-test",
    )

    assert result["ok"] is True
    assert result["dialog"]["dialog_channel_id"] == "builder"
    assert result["dialog"]["default_tool"] == "builder_skill.chat"
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
    assert published[0]["kind"] == "builder.scenario_draft.review"
    assert published[0]["response_topic"] == "builder.pending_action.response"
    assert published[0]["domain_ref"]["conversation_id"] == "conv.skill.builder_skill.default.builder-pa-ws"
    assert published[0]["domain_ref"]["thread_id"] == "thread.builder.1"
    refs = published[0]["metadata"]["source_refs"]
    assert refs["draft_id"] == "draft.shopping"
    assert refs["turn_trace_id"] == "trace.builder.1"
    assert refs["message_id"] == "msg.builder.1"


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
    assert result["patch"]["pending_action_id"] == "pa.builder.2"


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
        async def ensure_dev_webspace(self, webspace_id, active_draft_id=None, runtime_scenario_id=None):
            calls.append({
                "method": "ensure",
                "webspace_id": webspace_id,
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
            })
            return {
                "source_webspace_id": webspace_id,
                "dev_webspace_id": f"{webspace_id}-dev",
                "scenario_id": "prompt_engineer_scenario",
                "runtime_scenario_id": runtime_scenario_id,
                "active_draft_id": active_draft_id,
                "dialog": {"widget": "voice_chat", "dialog_channel_id": "builder"},
            }

        def publish_projection_sync(self, webspace_id, *, preview_state=None):
            calls.append({"method": "publish", "webspace_id": webspace_id, "preview_state": preview_state})
            return {"ok": True, "published_webspaces": [webspace_id, f"{webspace_id}-dev"]}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _DraftService)
    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())

    result = skill.create_scenario_draft("Builder, create a shopping list app", webspace_id="desktop")

    assert result["ok"] is True
    assert result["workbench"]["binding"]["dev_webspace_id"] == "desktop-dev"
    assert result["workbench"]["binding"]["active_draft_id"] == "draft.shopping"
    assert calls[0] == {
        "method": "ensure",
        "webspace_id": "desktop",
        "active_draft_id": "draft.shopping",
        "runtime_scenario_id": result["scenario_id"],
    }
    assert calls[1]["method"] == "publish"
    assert calls[1]["preview_state"]["current_ui"]["type"] == "page"


def test_workbench_tool_wrappers_use_voice_widget_and_active_draft(monkeypatch) -> None:
    skill = _load_module()
    calls: list[dict] = []

    class _Workbench:
        async def ensure_dev_webspace(self, webspace_id, active_draft_id=None, runtime_scenario_id=None):
            calls.append({
                "method": "ensure",
                "webspace_id": webspace_id,
                "active_draft_id": active_draft_id,
                "runtime_scenario_id": runtime_scenario_id,
            })
            return {"source_webspace_id": webspace_id, "dev_webspace_id": f"{webspace_id}-dev", "active_draft_id": active_draft_id}

        def get_workspace_binding(self, webspace_id):
            return {"source_webspace_id": webspace_id, "dev_webspace_id": f"{webspace_id}-dev", "active_draft_id": "draft.one"}

        def open_dev_webspace(self, webspace_id, *, base_url=None):
            return {"ok": True, "url": f"{base_url}/?webspace={webspace_id}-dev", "webspace_id": f"{webspace_id}-dev"}

        def dialog_widget_config(self, webspace_id):
            return {"widget": "voice_chat", "dialog_channel_id": "builder", "source_webspace_id": webspace_id}

        def set_active_draft(self, *, source_webspace_id=None, active_draft_id=None):
            return {"source_webspace_id": source_webspace_id, "active_draft_id": active_draft_id}

        def list_development_skills(self, webspace_id):
            return {"ok": True, "items": [{"draft_id": "draft.one", "active": True}], "active_draft_id": "draft.one"}

        def delete_development_skill(self, draft_id, webspace_id):
            calls.append({"method": "delete", "webspace_id": webspace_id, "draft_id": draft_id})
            return {"ok": True, "draft_id": draft_id}

    monkeypatch.setattr(skill, "_workbench_service", lambda: _Workbench())

    assert skill.ensure_dev_webspace(webspace_id="desktop", active_draft_id="draft.one")["binding"]["dev_webspace_id"] == "desktop-dev"
    assert skill.get_workspace_binding(webspace_id="desktop")["binding"]["active_draft_id"] == "draft.one"
    assert skill.open_dev_webspace(webspace_id="desktop", base_url="http://localhost:8100")["url"] == "http://localhost:8100/?webspace=desktop-dev"
    assert skill.attach_dialog_widget(webspace_id="desktop")["widget"]["widget"] == "voice_chat"
    assert skill.set_active_draft("draft.two", webspace_id="desktop")["binding"]["active_draft_id"] == "draft.two"
    assert skill.list_development_skills(webspace_id="desktop")["items"][0]["draft_id"] == "draft.one"
    assert skill.delete_development_skill("draft.one", webspace_id="desktop")["ok"] is True
    assert calls[0] == {"method": "ensure", "webspace_id": "desktop", "active_draft_id": "draft.one", "runtime_scenario_id": None}
    assert calls[-1] == {"method": "delete", "webspace_id": "desktop", "draft_id": "draft.one"}
