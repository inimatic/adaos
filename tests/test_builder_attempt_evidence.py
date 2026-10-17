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


def test_unbalanced_large_response_is_rejected_with_bounded_parse_work(evidence_builder):
    module, _, _ = evidence_builder
    started = time.monotonic()
    assert module._balanced_json_object('Незавершённый ответ {' * 30000) is None
    assert time.monotonic() - started < 3
    assert module._extract_json_object('prefix {"literal":"{ bracket }", "value":2} suffix')["value"] == 2
