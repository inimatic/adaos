from pathlib import Path

import pytest

from adaos.e2e.prototype_context_ablation import native_builder
from adaos.sdk.builder import workflow as workflow_sdk
from adaos.services.builder import semantic_graph

pytest_plugins = ("test_builder_semantic_graph",)


@pytest.fixture
def native_label_builder(graph_project, monkeypatch):
    service, workflow, root, document = graph_project
    module = native_builder(Path(__file__).resolve().parents[1] / ".adaos/workspace/skills/builder_skill/handlers/main.py")
    session = {"id": "fixture", "scenario_id": "semantic_labels", "artifact_root": str(root), "ui_revision": "001"}
    monkeypatch.setattr(semantic_graph.SemanticGraphService, "from_context", classmethod(lambda cls: service))
    monkeypatch.setattr(workflow_sdk, "_service", lambda: workflow)
    monkeypatch.setattr(module, "_source_webspace_id", lambda *a: "builder-host")
    monkeypatch.setattr(module, "_routing_meta_for_explicit_scenario", lambda *a: {})
    monkeypatch.setattr(module, "_align_workbench_binding_to_meta", lambda *a: None)
    monkeypatch.setattr(module, "_target_session", lambda *a: (session, {}))
    monkeypatch.setattr(module, "_builder_topic_ref", lambda *a, **kw: {"topic_id": "fixture-thread"})
    monkeypatch.setattr(module, "_project_artifact_root", lambda *a: root)
    monkeypatch.setattr(module, "_save_session", lambda *a: None)
    monkeypatch.setattr(module, "_sync_session_from_artifacts", lambda *a: None)
    monkeypatch.setattr(module, "_request_workbench_refresh", lambda *a: {"ok": True})
    monkeypatch.setattr(module, "_refresh_follow_active_preview", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(module.developer_prototypes, "carry_forward_resources", lambda **kw: {"ok": True})
    monkeypatch.setattr(module, "_upsert_builder_change", lambda **kw: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("D0 must not build LLM context or submit inference")
    monkeypatch.setattr(module, "_builder_development_context_packet", forbidden)
    monkeypatch.setattr(module, "_submit_llm_webui_transform_job", forbidden)
    return module, workflow, root, document


def test_native_intake_routes_rename_retry_and_undo_without_llm_context(native_label_builder):
    module, workflow, root, document = native_label_builder
    title = next(view["title"]["en"] for view in document["views"] if view["id"] == "work-list")
    instruction = f'Rename view "{title}" to "Work queue" in English'
    kwargs = {"instruction": instruction, "webspace_id": "builder-host", "_meta": {"message_id": "user-label-1"}}
    result = module.update_current_scenario(**kwargs)
    assert result["status"] == "semantic_label_applied", result
    assert result["semantic_transaction"]["revision"] == "002"
    assert "user-label-1" in workflow.describe("scenario", "semantic_labels")["change_set"]["source_message_ids"]
    generation = workflow.describe("scenario", "semantic_labels")["generation"]
    retry = module.update_current_scenario(**kwargs)
    assert retry["semantic_transaction"]["replayed"]
    assert workflow.describe("scenario", "semantic_labels")["generation"] == generation
    undo = module.update_current_scenario(instruction="Undo the last label change", webspace_id="builder-host",
                                          _meta={"message_id": "user-undo"})
    assert undo["semantic_transaction"]["revision"] == "003", undo


def test_native_ambiguous_or_incomplete_label_request_cannot_fall_back_to_generation(native_label_builder):
    module, workflow, root, _ = native_label_builder
    before = (root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]
    result = module.update_current_scenario(instruction="Rename field Name to Subject", webspace_id="builder-host",
                                             _meta={"message_id": "user-clarify"})
    assert result["status"] == "clarification_required" and result["model_calls"] == 0
    assert ((root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]) == before


def test_chat_reuses_its_authoritative_target_instead_of_resolving_it_twice(native_label_builder, monkeypatch):
    module, _, _, document = native_label_builder
    monkeypatch.setattr(module, "_resolve_builder_context_for_turn", lambda *a: {"builder_webspace_id": "builder-host"})
    monkeypatch.setattr(module, "_parse_builder_command", lambda *a, **kw: {"intent": ""})
    monkeypatch.setattr(module, "_requested_scenario_id_from_meta", lambda *a: "")
    monkeypatch.setattr(module, "_project_external_user_turn", lambda *a, **kw: None)
    monkeypatch.setattr(module, "_route_automation_chat", lambda **kw: None)
    monkeypatch.setattr(module, "_safe_emit_chat", lambda *a, **kw: None)
    monkeypatch.setattr(module, "_dialog_state", lambda *a, **kw: {})
    def duplicate_resolution(*args, **kwargs):
        raise AssertionError("chat already admitted this exact target")
    monkeypatch.setattr(module, "update_current_scenario", duplicate_resolution)
    title = next(view["title"]["en"] for view in document["views"] if view["id"] == "work-list")
    result = module.chat(text=f'Rename view "{title}" to "Work queue" in English', webspace_id="builder-host",
                         _meta={"message_id": "user-chat-label"})
    assert result["status"] == "semantic_label_applied"
    assert "routing_ms" in result["message_meta"]["builder_d0_turn_timing"]
    assert set(result["message_meta"]["builder_d0_turn_timing"]["routing_stages_ms"]) == {
        "target_guard_ms", "builder_context_ms", "binding_alignment_ms", "target_session_ms",
        "conversation_topic_ms", "command_and_route_ms",
    }


def test_missing_managed_source_cannot_be_reclassified_as_legacy(native_label_builder):
    module, workflow, root, _ = native_label_builder
    (root / "semantic.webui.json").unlink()
    before = (root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]
    result = module.update_current_scenario(instruction='Rename field "Name" to "Subject"', webspace_id="builder-host",
                                             _meta={"message_id": "missing-source"})
    assert result["status"] == "semantic_label_rejected" and result["model_calls"] == 0
    assert ((root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]) == before


def test_selected_project_and_conversation_mismatch_cannot_realign_or_mutate(native_label_builder, monkeypatch):
    module, workflow, root, _ = native_label_builder
    def forbidden(*args, **kwargs):
        raise AssertionError("mismatch must stop before resolving or changing the Builder binding")
    monkeypatch.setattr(module, "_resolve_builder_context_for_turn", forbidden)
    before = (root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]
    result = module.chat(text='Rename view "Work" to "Queue"', _meta={
        "builder_selected_target": {"object_type": "scenario", "object_id": "semantic_labels"},
        "thread_id": "prompt-project:scenario:other-project"})
    assert result["status"] == "builder_target_mismatch" and result["model_calls"] == 0
    assert ((root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]) == before
