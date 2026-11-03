import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from adaos.e2e.prototype_context_ablation import native_builder
from adaos.services.builder.llm_input_attribution import retain_llm_evidence, read_llm_evidence
from adaos.services.context_control import ContextControlService


@pytest.fixture
def evidence_builder(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    source = Path(os.environ.get("ADAOS_TEST_BUILDER_SOURCE", root / ".adaos/workspace/skills/builder_skill"))
    module = native_builder(source / "handlers/main.py")
    monkeypatch.setattr(module.sdk_builder_observability, "retain_llm_evidence",
                        lambda value: retain_llm_evidence(value, state_dir=tmp_path / "private"))
    monkeypatch.setattr(module.sdk_builder_observability, "read_llm_evidence",
                        lambda receipt: read_llm_evidence(receipt, state_dir=tmp_path / "private"))
    project = tmp_path / "project"
    project.mkdir()
    return module, {"id": "evidence-test", "scenario_id": "evidence-test", "artifact_root": str(project)}, project


def test_native_input_retains_exact_base_and_parent_across_restarts(evidence_builder, tmp_path):
    module, session, root = evidence_builder
    raw = '{ "ui": {"title":"Портрет 👩🏽‍💻"}}\r\n'.encode("utf-8")
    (root / "webui.json").write_bytes(raw)
    messages = [{"role": "user", "content": "Сохрани кожу 👩🏽‍💻" * 3000}]
    primary = module._write_llm_job_request_artifact(session=session, job_id="primary-request", request_id="primary-request",
        model="fixture-model", messages=messages, generation_options={"max_tokens": 100})
    receipt = module._write_llm_job_request_artifact(session=session, job_id="repair", request_id="repair",
        parent_request_id="primary-request", parent_job_id="primary-job", model="fixture-model",
        messages=messages, generation_options={"max_tokens": 100}, stage="repair")
    saved = ContextControlService(state_dir=tmp_path / "private").get_artifact(receipt["evidence_receipt"]["ref"])
    assert saved["messages"] == messages
    assert saved["parent_request_id"] == "primary-request" and saved["parent_job_id"] == "primary-job"
    assert saved["parent_evidence_ref"] == primary["evidence_receipt"]["ref"]
    assert saved["source_inputs"]["webui.json"] == {"sha256": hashlib.sha256(raw).hexdigest(), "content": raw.decode("utf-8")}
    assert receipt["evidence_receipt"]["automatic_gc"] is False
    assert json.loads((root / receipt["path"]).read_text(encoding="utf-8"))["evidence_receipt"] == receipt["evidence_receipt"]


def test_evidence_failure_stops_submit_boundary(evidence_builder, monkeypatch):
    module, session, _ = evidence_builder
    def disk_full(*args, **kwargs):
        raise OSError("No space left on device")
    monkeypatch.setattr(module.sdk_builder_observability, "retain_llm_evidence", disk_full)
    with pytest.raises(RuntimeError, match="before_model_submit"):
        module._write_llm_job_request_artifact(session=session, job_id="never-submitted", request_id="new",
            model="fixture", messages=[{"role": "user", "content": "Task"}], generation_options={})


def test_core_journal_retains_authoring_source_from_the_selected_project(evidence_builder, tmp_path, monkeypatch):
    module, session, root = evidence_builder
    raw = b'{"source":"selected-project"}'
    (root / 'webui.json').write_bytes(raw)
    journal = tmp_path / 'core' / 'journals' / 'owner'
    journal.mkdir(parents=True)
    (journal.parent / 'webui.json').write_text('{"foreign":true}')
    monkeypatch.setattr(module, '_llm_job_journal_dir', lambda _: journal)
    receipt = module._write_llm_job_request_artifact(session=session, job_id='owned', request_id='owned',
        model='fixture', messages=[{'role': 'user', 'content': 'Keep source'}], generation_options={})
    saved = read_llm_evidence(receipt['evidence_receipt'], state_dir=tmp_path / 'private')
    assert saved['source_inputs']['webui.json']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert saved['source_inputs']['webui.json']['content'] == raw.decode()


def test_raw_output_is_retained_before_parsing_and_repair(evidence_builder, tmp_path):
    module, session, _ = evidence_builder
    response = "Незаконченный JSON 👩🏽‍💻 {" * 30000
    receipt = module._write_llm_job_raw_candidate_artifact(session=session, job_id="paid", request_id="request",
        stage="received", output_text=response, output_mode="semantic_v2")
    saved = ContextControlService(state_dir=tmp_path / "private").get_artifact(receipt["evidence_receipt"]["ref"])
    assert saved["response"]["content"] == response and saved["extraction_error"]
    assert saved["response"]["sha256"] == hashlib.sha256(response.encode("utf-8")).hexdigest()


def test_missing_project_journal_never_permits_unattributed_new_call(evidence_builder):
    module, _, _ = evidence_builder
    with pytest.raises(RuntimeError, match="before_model_submit"):
        module._write_llm_job_request_artifact(session={}, job_id="job", request_id="req", model="fixture",
            messages=[{"role": "user", "content": "Task"}], generation_options={})


def test_new_session_uses_core_head_without_falling_back_to_legacy_source(evidence_builder, monkeypatch):
    module, session, root = evidence_builder
    session['project_ref'] = 'project:owned'
    (root/'webui.json').write_text('{"obsolete":"source"}')
    accepted = {'publication_ref': 'prototype-publication:owned', 'project_ref': 'scenario:evidence-test',
        'source_scope': {'project_ref': 'project:owned'}, 'is_current': True, 'revision': '002',
        'webui': {'ui': {'application': {'desktop': {'pageSchema': {'id': 'canonical'}}}}},
        'prototype_brief': {'brief_id': 'retained'}}
    monkeypatch.setattr(module.sdk_builder_workflow, 'get_state', lambda *a: {
        'prototype': {'head_revision': '002', 'publication_ref': accepted['publication_ref']}})
    monkeypatch.setattr(module.sdk_builder_prototype, 'generation_publication_projection', lambda **k: accepted)
    assert module._sync_session_from_artifacts(session)
    assert session['prototype_publication_ref'] == accepted['publication_ref']
    assert session['ui_revision'] == '002' and session['webui_payload'] == accepted['webui']
    assert session['accepted_prototype_brief'] == accepted['prototype_brief']
    assert not module._sync_session_from_artifacts(session)
    assert (root/'webui.json').read_text() == '{"obsolete":"source"}'
    monkeypatch.setattr(module.sdk_builder_prototype, 'generation_publication_projection',
        lambda **k: {**accepted, 'is_current': False})
    with pytest.raises(ValueError, match='stale'):
        module._sync_session_from_artifacts(session)


def test_unbalanced_large_response_is_rejected_with_bounded_parse_work(evidence_builder):
    module, _, _ = evidence_builder
    started = time.monotonic()
    assert module._balanced_json_object('Незавершённый ответ {' * 30000) is None
    assert time.monotonic() - started < 3
    assert module._extract_json_object('prefix {"literal":"{ bracket }", "value":2} suffix')["value"] == 2


def test_replay_repairs_the_exact_retained_child_attempt_instead_of_claiming_its_parent(evidence_builder, monkeypatch):
    from adaos.services.builder.semantic_prototype import SemanticPrototypeValidationError
    module, session, root = evidence_builder
    session.update(scenario_id='demo', ui_revision='001', project_ref='scenario:demo',
        accepted_prototype_brief={'brief_id': 'brief:accepted-base', 'digest': 'accepted-base'},
        pending_llm_jobs={'primary': {'status': 'failed', 'request_text': 'Request', 'root_job_id': 'primary'}})
    folder = module._llm_job_journal_dir(session)
    folder.mkdir(parents=True, exist_ok=True)
    source = {'scenario_id': 'demo', 'job_id': 'primary', 'request_id': 'request',
        'generation': {'options': {'output_mode': 'semantic_v2'}},
        'messages': [{'content': json.dumps({'builder_request': {'instruction': 'Request',
            'prototype_brief': {'brief_ref': 'brief:owned', 'brief_digest': 'owned'}}})}]}
    raw = json.dumps(source).encode()
    (folder/'primary.request.json').write_bytes(raw)
    candidate = lambda label: json.dumps({'schema': 'adaos.builder.semantic_prototype_candidate.v2',
        'document_id': label, 'title': {'en': label}})
    (folder/'primary.json').write_text(json.dumps({'status': 'failed', 'scenario_id': 'demo',
        'request_id': 'request', 'input_artifact': {'path': 'primary.request.json', 'sha256': hashlib.sha256(raw).hexdigest()},
        'diagnostic': {'response': {'content': candidate('child'), 'truncated': False},
                       'result': {'repair': {'job_id': 'repair-child'}}}}))
    monkeypatch.setattr(module, '_current_semantic_context', lambda *args: None)
    monkeypatch.setattr(module, '_initial_prototype_replay_context', lambda *args: {
        'status': 'admitted', 'brief': {'brief_id': 'brief:owned', 'digest': 'owned'}})
    monkeypatch.setattr(module, '_semantic_scoped_repair', lambda *args: (None, None))
    monkeypatch.setattr('adaos.sdk.llm.llm_client.get_response_job', lambda *args, **kwargs: {
        'status': 'succeeded', 'job_id': 'primary', 'output_text': candidate('primary')})
    def rejected(**kwargs):
        raise SemanticPrototypeValidationError([{'code': 'semantic.validation_failed', 'detail': 'One remaining defect'}])
    monkeypatch.setattr(module, '_parse_llm_webui_transform_output', rejected)
    result = module._replay_failed_llm_webui_result(session=session, job_id='primary', request_text='Request',
        expected_ui_revision='001', previous_preview={}, before_webui={})
    assert result['semantic_replay_input']['source_job_id'] == 'repair-child'
    assert json.loads(result['semantic_replay_input']['output_text'])['document_id'] == 'child'
