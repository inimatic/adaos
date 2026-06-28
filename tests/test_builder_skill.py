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
            return {
                "ok": True,
                "draft": {"draft_id": "draft.shopping"},
                "artifact_root": str(artifact_root),
                "kwargs": kwargs,
            }

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Service)

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
