from pathlib import Path

import pytest

from adaos.e2e.prototype_context_ablation import native_builder
from adaos.sdk.builder import workflow as workflow_sdk
from adaos.services.builder import semantic_graph
from test_builder_semantic_graph import graph_project


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
