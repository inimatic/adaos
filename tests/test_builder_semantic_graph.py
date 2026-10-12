from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from adaos.sdk.builder import semantic_ui as sdk
from adaos.services.builder import semantic_graph as graph
from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService
from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate


@pytest.fixture
def graph_project(tmp_path):
    skills = tmp_path / "skills"
    root = tmp_path / "scenarios" / "semantic_labels"
    root.mkdir(parents=True)
    skills.mkdir()
    brief, original = _multi_resource_fixture()
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(original), brief=brief, project_ref="project:semantic_labels")
    document = compiled["semantic_document"]
    for name, value in {"semantic.webui.json": document, "webui.json": compiled["webui"],
                        "scenario.json": {"id": "semantic_labels", "version": "0.1.0", "ui": {"manifest": "webui.json"}}}.items():
        (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    (root / "scenario.yaml").write_text("id: semantic_labels\nversion: 0.1.0\n", encoding="utf-8")
    (root / "ui_revisions").mkdir()
    (root / "ui_revisions/001.json").write_text("{}", encoding="utf-8")
    (root / "ui_revisions/current.txt").write_text("001\n", encoding="utf-8")
    for locale, values in compiled["locale_dictionaries"].items():
        path = root / f"assets/i18n/{locale}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    workflow = BuilderWorkflowService(skills, root.parent, state_dir=tmp_path / "state")
    workflow.transition("scenario", root.name, "plan_change_set", metadata={
        "change_set_id": "labels-change", "request": "Rename an existing label", "issues": [
            {"issue_id": "labels", "title": "Rename", "lane": "prototype", "acceptance_criteria": ["Existing behavior remains"]}]})
    return graph.SemanticGraphService(workflow), workflow, root, document


def operation(service, document, **changes):
    label = next(view["title"] for view in document["views"] if view["id"] == "work-list")
    projection = service.workflow.describe("scenario", "semantic_labels")
    return {"schema": graph.SCHEMA, "project_ref": "scenario:semantic_labels", "change_id": "labels-change",
            "transaction_id": "label-1", "source_revision": "001", "base_hash": graph.digest(document),
            "expected_generation": projection["generation"], "write_refs": ["view:work-list"],
            "ops": [{"seq": 1, "kind": "label.set", "ref": "view:work-list", "locale": "en",
                     "expected": label["en"], "text": "Work queue"}], **changes}


def test_public_sdk_replay_is_pure_and_preserves_other_semantics(graph_project):
    service, _, _, document = graph_project
    request = operation(service, document)
    result = sdk.replay(document, request["ops"], write_refs=request["write_refs"])
    expected = copy.deepcopy(document)
    next(view for view in expected["views"] if view["id"] == "work-list")["title"]["en"] = "Work queue"
    assert result["semantic_document"] == expected
    assert sdk.replay(result["semantic_document"], result["inverse_ops"], write_refs=request["write_refs"])["semantic_document"] == document


@pytest.mark.parametrize("change", [
    {"seq": 2}, {"ref": "view:people-list"}, {"expected": "Wrong label"},
    {"kind": "command.execute"}, {"locale": "fr"}, {"text": "   "}, {"extra": True},
])
def test_reducer_rejects_invalid_or_out_of_scope_operations(graph_project, change):
    service, _, _, document = graph_project
    request = operation(service, document)
    with pytest.raises(BuilderWorkflowError):
        sdk.replay(document, [{**request["ops"][0], **change}], write_refs=request["write_refs"])


def test_digest_marker_does_not_mask_actual_runtime_drift(graph_project):
    service, _, root, document = graph_project
    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["inputs"]["title"] = "Silent renderer edit"
    with pytest.raises(BuilderWorkflowError, match="baseline drift"):
        sdk.verify_source(document, webui, project_ref="project:semantic_labels")
    assert service.workflow.current_prototype_revision("scenario", "semantic_labels") == "001"


def test_transaction_compiles_and_survives_exact_retry_and_undo(graph_project, monkeypatch):
    service, workflow, root, document = graph_project
    monkeypatch.setattr(graph.SemanticGraphService, "from_context", classmethod(lambda cls: service))
    request = operation(service, document)
    result = sdk.apply(request)
    assert result["revision"] == "002" and result["model_calls"] == 0
    assert not (root / "semantic_operations").exists()
    after = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
    replay = sdk.replay(document, request["ops"], write_refs=request["write_refs"])
    assert graph.digest(after) == replay["semantic_digest"] == result["semantic_digest"]
    actual_ui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    assert sdk.verify_source(after, actual_ui, project_ref="project:semantic_labels")["semantic_digest"] == result["semantic_digest"]
    assert graph.SemanticGraphService(workflow).apply(request)["replayed"] is True
    with pytest.raises(BuilderWorkflowError, match="identity conflicts"):
        service.apply({**request, "ops": [{**request["ops"][0], "text": "Conflicting label"}]})
    undo = {**request, "transaction_id": "undo-label-1", "base_hash": result["semantic_digest"],
            "source_revision": "002", "expected_generation": workflow.describe("scenario", "semantic_labels")["generation"],
            "ops": result["inverse_ops"]}
    assert sdk.apply(undo)["revision"] == "003"
    assert json.loads((root / "semantic.webui.json").read_text(encoding="utf-8")) == document
    assert workflow.describe("scenario", "semantic_labels")["change"]["runs"][-1]["run_id"] == "undo-label-1"


def test_locale_changes_do_not_replace_other_translations(graph_project):
    service, _, root, document = graph_project
    request = operation(service, document)
    original = next(view["title"] for view in document["views"] if view["id"] == "work-list")
    request["ops"][0].update(locale="ru", expected=original["ru"], text="Очередь работ")
    before_en = json.loads((root / "assets/i18n/en.json").read_text(encoding="utf-8"))
    service.apply(request)
    assert json.loads((root / "assets/i18n/en.json").read_text(encoding="utf-8")) == before_en
    assert json.loads((root / "assets/i18n/ru.json").read_text(encoding="utf-8"))[original["key"]] == "Очередь работ"


def test_generation_precondition_is_enforced_without_mutation(graph_project):
    service, _, root, document = graph_project
    request = operation(service, document, expected_generation=9999)
    before = (root / "webui.json").read_bytes()
    with pytest.raises(BuilderWorkflowError, match="stale"):
        service.apply(request)
    assert (root / "webui.json").read_bytes() == before


def test_ordinary_request_prepare_apply_retry_and_inverse(graph_project, monkeypatch):
    service, workflow, root, document = graph_project
    monkeypatch.setattr(graph.SemanticGraphService, "from_context", classmethod(lambda cls: service))
    title = next(view["title"]["en"] for view in document["views"] if view["id"] == "work-list")
    text = f'Rename view "{title}" to "Work queue" in English.'
    before = (root / "semantic.webui.json").read_bytes()
    prepared = sdk.prepare_label_request(project_ref="scenario:semantic_labels", instruction=text,
                                         source_message_id="message-rename", expected_revision="001")
    assert (root / "semantic.webui.json").read_bytes() == before
    assert prepared["request"]["write_refs"] == ["view:work-list"]
    result = sdk.apply(prepared["request"])
    assert result["source_request"] == {"instruction": text, "source_message_id": "message-rename"}
    retry = sdk.prepare_label_request(project_ref="scenario:semantic_labels", instruction=text,
                                      source_message_id="message-rename", expected_revision="001")
    assert retry["replayed"] and retry["request"] == prepared["request"]
    assert sdk.apply(retry["request"])["replayed"]
    with pytest.raises(BuilderWorkflowError, match="identity conflicts"):
        sdk.prepare_label_request(project_ref="scenario:semantic_labels", instruction=text.replace("Work queue", "New title"),
                                  source_message_id="message-rename")
    undo = sdk.prepare_label_request(project_ref="scenario:semantic_labels", instruction="Undo the last label change",
                                     source_message_id="message-undo", expected_revision="002")
    assert undo["request"]["ops"] == result["inverse_ops"]
    assert sdk.apply(undo["request"])["revision"] == "003"
    assert json.loads((root / "semantic.webui.json").read_text(encoding="utf-8")) == document


@pytest.mark.parametrize("instruction", [
    'Rename view "not present" to "Queue"',
    'Rename field Name to Subject',
    'Rename view "not present" to "Queue" and delete the records',
])
def test_incomplete_label_requests_clarify_without_mutation(graph_project, instruction):
    service, workflow, root, _ = graph_project
    before = (root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]
    assert service.prepare_label_request(project_ref="scenario:semantic_labels", instruction=instruction,
                                         source_message_id="clarify")["status"] == "clarification_required"
    assert ((root / "webui.json").read_bytes(), workflow.describe("scenario", "semantic_labels")["generation"]) == before


def test_request_resolution_requires_exact_locale_and_one_semantic_node(graph_project):
    from adaos.services.builder.semantic_label_requests import parse_request, plan
    _, _, _, document = graph_project
    first = document["views"][0]
    first["title"].update(en="Shared", ru="Shared")
    assert plan(document, parse_request('Rename view "Shared" to "Queue"'))["reason"] == "ambiguous_label"
    selected = plan(document, parse_request('Переименуй экран «Shared» в «Queue» на английском'))
    assert selected["ops"][0]["locale"] == "en"
    document["views"][1]["title"]["en"] = "Shared"
    assert plan(document, parse_request('Rename view "Shared" to "Queue" in English'))["reason"] == "ambiguous_label"


def test_stale_request_and_undo_without_label_receipt_fail_closed(graph_project):
    service, _, _, _ = graph_project
    with pytest.raises(BuilderWorkflowError, match="stale"):
        service.prepare_label_request(project_ref="scenario:semantic_labels", instruction='Rename view "Work" to "Queue"',
                                       source_message_id="stale", expected_revision="999")
    with pytest.raises(BuilderWorkflowError, match="no exact"):
        service.prepare_label_request(project_ref="scenario:semantic_labels", instruction="Undo the last label change",
                                       source_message_id="undo")


def test_label_request_does_not_trust_drifted_ui_or_a_forged_inverse(graph_project):
    service, _, root, document = graph_project
    request = operation(service, document)
    service.apply(request)
    revision_path = root / "ui_revisions/002.json"
    revision = json.loads(revision_path.read_text(encoding="utf-8"))
    revision["semantic_transaction"]["inverse_ops"][0]["text"] = "Forged original"
    revision_path.write_text(json.dumps(revision), encoding="utf-8")
    with pytest.raises(BuilderWorkflowError, match="exact retained"):
        service.prepare_label_request(project_ref="scenario:semantic_labels", instruction="Undo the last label change",
                                       source_message_id="forged-undo")
    actual = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    actual["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["inputs"]["title"] = "Drift"
    (root / "webui.json").write_text(json.dumps(actual), encoding="utf-8")
    with pytest.raises(BuilderWorkflowError, match="baseline drift"):
        service.prepare_label_request(project_ref="scenario:semantic_labels", instruction='Rename view "Work queue" to "New queue"',
                                       source_message_id="drift")


def test_shared_translation_key_does_not_authorize_other_label_changes(graph_project):
    service, _, _, document = graph_project
    other = document["resources"][0]["fields"][0]["label"]
    label = next(view["title"] for view in document["views"] if view["id"] == "work-list")
    other["key"] = label["key"]
    request = operation(service, document)
    with pytest.raises(BuilderWorkflowError, match="shared locale key"):
        sdk.replay(document, request["ops"], write_refs=request["write_refs"])


@pytest.mark.parametrize("commit", [False, True])
def test_real_process_exit_releases_lock_and_recovers_without_inference(graph_project, commit):
    service, workflow, root, document = graph_project
    request = operation(service, document)
    script = '''import json, os, sys
from pathlib import Path
from adaos.services.builder.semantic_graph import SemanticGraphService
from adaos.services.builder.workflow import BuilderWorkflowService
base = Path(sys.argv[1])
workflow = BuilderWorkflowService(base / "skills", base / "scenarios", state_dir=base / "state")
original = BuilderWorkflowService.transition
def stop(self, *args, **kwargs):
    if sys.argv[3] == "1":
        original(self, *args, **kwargs)
    os._exit(91)
BuilderWorkflowService.transition = stop
SemanticGraphService(workflow).apply(json.loads(sys.argv[2]))
'''
    process = subprocess.run([sys.executable, "-c", script, str(root.parent.parent), json.dumps(request), str(int(commit))],
                             capture_output=True, timeout=90)
    assert process.returncode == 91, process.stderr.decode("utf-8", errors="replace")
    if commit:
        assert graph.SemanticGraphService(workflow).apply(request)["replayed"] is True
        assert workflow.current_prototype_revision("scenario", root.name) == "002"
    else:
        with pytest.raises(BuilderWorkflowError, match="aborted"):
            graph.SemanticGraphService(workflow).apply(request)
        assert workflow.current_prototype_revision("scenario", root.name) == "001"
        assert json.loads((root / "semantic.webui.json").read_text(encoding="utf-8")) == document


@pytest.mark.parametrize("commit", [False, True])
def test_interrupted_commit_recovers_from_workflow_authority(graph_project, monkeypatch, commit):
    service, workflow, root, document = graph_project
    request = operation(service, document)
    transition = workflow.transition

    def crash(*args, **kwargs):
        if commit:
            transition(*args, **kwargs)
        # BaseException emulates termination: ordinary exception rollback is skipped.
        raise KeyboardInterrupt("injected process termination")

    monkeypatch.setattr(BuilderWorkflowService, "transition", lambda self, *args, **kwargs: crash(*args, **kwargs))
    with pytest.raises(KeyboardInterrupt):
        service.apply(request)
    monkeypatch.undo()
    recovered = graph.SemanticGraphService(workflow)
    if commit:
        result = recovered.apply(request)
        assert result["replayed"] is True and result["revision"] == "002"
    else:
        with pytest.raises(BuilderWorkflowError, match="aborted"):
            recovered.apply(request)
        assert json.loads((root / "semantic.webui.json").read_text(encoding="utf-8")) == document
        assert workflow.current_prototype_revision("scenario", "semantic_labels") == "001"
    assert not (root / "ui_revisions/003.json").exists()
