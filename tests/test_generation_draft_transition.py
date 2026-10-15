import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.applications.generation_draft_transition import preserve_generation_drafts
from adaos.services.content_generation import ContentGenerationService


def test_completed_analysis_is_polled_after_beta_upgrade_without_submit(tmp_path):
    from test_content_generation import setup_service, request
    old = tmp_path / "old/.runtime/state/content-generation"
    result = {"status": "succeeded", "output_text": '{"status":"completed","data":{"text":"done"},"message":""}'}
    service, calls = setup_service(old, result)
    request(service)
    owner = "skill:one|user:one"
    options = dict(source_runtime=tmp_path / "old", target_runtime=tmp_path / "new",
        recovery=tmp_path / "recovery", private=tmp_path, operation_key="upgrade-1")
    assert preserve_generation_drafts(**options)["submissions"] == 0
    assert preserve_generation_drafts(**options)["submissions"] == 0
    new = ContentGenerationService(tmp_path / "new/.runtime/state/content-generation", owner, None)
    new.broker = SimpleNamespace(get_response_job=lambda *a, **kw: result)
    assert new.get("draft-1")["status"] == "completed"
    assert len(calls) == 1
    assert service.get("draft-1")["status"] == "completed"


def test_preserves_png_and_unknown_job_identity_and_rejects_conflict(tmp_path):
    owner = hashlib.sha256(b"owner").hexdigest()
    request_id = "existing-edit"
    name = hashlib.sha256(request_id.encode()).hexdigest() + ".json"
    source = tmp_path / "old/.runtime/state/image-generation" / owner
    source.mkdir(parents=True)
    record = {"request_id": request_id, "root_request_id": "existing-root-job", "status": "unknown"}
    (source / name).write_text(json.dumps(record), encoding="utf8")
    raw = b"owned-image-bytes"
    image = hashlib.sha256(raw).hexdigest() + ".png"
    (source / image).write_bytes(raw)
    options = dict(source_runtime=tmp_path / "old", target_runtime=tmp_path / "new",
        recovery=tmp_path / "recovery", private=tmp_path, operation_key="upgrade")
    preserve_generation_drafts(**options)
    target = tmp_path / "new/.runtime/state/image-generation" / owner
    assert json.loads((target / name).read_text())["root_request_id"] == "existing-root-job"
    assert (target / image).read_bytes() == raw
    (target / name).write_text(json.dumps({**record, "status": "submitting"}))
    with pytest.raises(ValueError, match="conflict"):
        preserve_generation_drafts(**options)


def test_refuses_corrupt_asset_before_install(tmp_path):
    source = tmp_path / "old/.runtime/state/image-generation" / ("a" * 64)
    source.mkdir(parents=True)
    (source / ("b" * 64 + ".png")).write_bytes(b"wrong hash")
    with pytest.raises(ValueError, match="digest mismatch"):
        preserve_generation_drafts(source_runtime=tmp_path / "old", target_runtime=tmp_path / "new",
            recovery=tmp_path / "recovery", private=tmp_path, operation_key="upgrade")
    assert not (tmp_path / "new").exists()
