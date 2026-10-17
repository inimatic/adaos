import base64
import copy
import io
import json

import pytest
from PIL import Image

from adaos.services.image_generation import (
    IMAGE_JOB_POLL_TIMEOUT_SECONDS,
    ImageGenerationService,
    decode_generated_image,
)


def encoded_image(format="PNG", size=(16, 12)):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format=format)
    return base64.b64encode(output.getvalue()).decode("ascii")


class Broker:
    def __init__(self):
        self.submits = []
        self.polls = []
        self.job = {"job_id": "job-one", "status": "queued", "_client": {"base_url": "https://root.invalid"}}

    def submit_image_job(self, **kwargs):
        self.submits.append(kwargs)
        return copy.deepcopy(self.job)

    def get_response_job(self, job, **kwargs):
        self.polls.append((job, kwargs))
        return copy.deepcopy(self.job)

    def submit_image_edit_job(self, **kwargs):
        self.submits.append(kwargs)
        return copy.deepcopy(self.job)


def test_public_image_budget_reaches_root_and_cannot_drift_on_retry(tmp_path):
    broker = Broker()
    service = ImageGenerationService(tmp_path, 'skill:test|user:test', broker, lambda *args, **kwargs: {})
    args = dict(request_id='edit-1', model='gpt-image-1', prompt='Correct blemishes',
        images=[{'image_url':'fixture-owned-input'}], spend_scope='experiment', spend_run_id='retouch-1', spend_reserve_usd=2)
    service.submit(**args); service.submit(**args)
    assert len(broker.submits) == 1
    assert broker.submits[0]['spend_scope'] == 'experiment'
    with pytest.raises(ValueError, match='different image request'):
        service.submit(**{**args, 'spend_scope': 'another-account'})


def test_edit_binds_source_and_mask_and_recovers_without_resubmitting(tmp_path):
    broker = Broker()
    service = ImageGenerationService(tmp_path, "skill:retouch|user:owner", broker, publisher)
    source = {"image_url": "data:image/png;base64," + encoded_image()}
    mask = {"image_url": "data:image/png;base64," + encoded_image(size=(16, 12))}
    params = dict(request_id="edit-one", model="gpt-image-2.5-sunburst", prompt="Preserve texture",
                  images=[source], mask=mask)
    service.submit(**params)
    recovered = ImageGenerationService(tmp_path, "skill:retouch|user:owner", broker, publisher)
    recovered.submit(**params)
    assert len(broker.submits) == 1
    assert broker.submits[0]["images"] == [source] and broker.submits[0]["mask"] == mask
    with pytest.raises(ValueError, match="different"):
        recovered.submit(**{**params, "mask": {"image_url": "data:image/png;base64," + encoded_image(size=(20, 12))}})


def publisher(path, **kwargs):
    assert path.is_file()
    return {"browser_path": "/media/" + path.name, **kwargs}


def test_completed_owned_output_can_be_read_without_network_or_publication(tmp_path):
    broker = Broker()
    service = ImageGenerationService(tmp_path, "skill:retouch|user:one", broker, publisher)
    service.submit(request_id="candidate", model="gpt-image-1", prompt="Preserve texture")
    with pytest.raises(ValueError, match="not completed"):
        service.read_bytes("candidate")
    assert broker.polls == []
    encoded = encoded_image()
    broker.job.update(status="succeeded", response={"data": [{"b64_json": encoded}]})
    service.get("candidate")
    def no_publish(*args, **kwargs):
        pytest.fail("local byte read must not publish media")
    reopened = ImageGenerationService(tmp_path, "skill:retouch|user:one", broker, no_publish)
    assert reopened.read_bytes("candidate") == base64.b64decode(encoded)
    assert len(broker.submits) == len(broker.polls) == 1
    with pytest.raises(ValueError, match="current owner"):
        ImageGenerationService(tmp_path, "skill:retouch|user:two", broker, no_publish).read_bytes("candidate")
    for limit in (True, 0, 12582913):
        with pytest.raises(ValueError, match="max_bytes"):
            reopened.read_bytes("candidate", max_bytes=limit)
    with pytest.raises(ValueError, match="size limit"):
        reopened.read_bytes("candidate", max_bytes=5)
    next(reopened.root.glob("*.png")).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        reopened.read_bytes("candidate")


def test_image_generation_is_owned_durable_idempotent_and_context_stays_local(tmp_path):
    broker = Broker()
    service = ImageGenerationService(tmp_path, "skill:one|user:one", broker, publisher)
    params = dict(request_id="draft-one", model="gpt-image-1", prompt="An icon", context={"project_ref": "project:one"})
    assert service.submit(**params)["status"] == "queued"
    assert service.submit(**params)["status"] == "queued"
    assert len(broker.submits) == 1
    assert "context" not in broker.submits[0]
    with pytest.raises(ValueError, match="different"):
        service.submit(**{**params, "prompt": "Another icon"})
    with pytest.raises(ValueError, match="current owner"):
        ImageGenerationService(tmp_path, "skill:one|user:two", broker, publisher).get("draft-one")
    broker.job.update(status="succeeded", model="gpt-image-1", response={"data": [{"b64_json": encoded_image()}],
        "usage": {"input_tokens": 5, "output_tokens": 10}})
    recovered = ImageGenerationService(tmp_path, "skill:one|user:one", broker, publisher)
    view = recovered.get("draft-one")
    assert view["status"] == "completed" and view["metering_status"] == "reported"
    assert view["media"]["width"] == 16 and view["media"]["height"] == 12
    assert view["context"] == params["context"]
    assert "b64_json" not in json.dumps(view) and str(tmp_path) not in json.dumps(view)
    assert recovered.get("draft-one") == view
    assert len(broker.submits) == len(broker.polls) == 1
    assert broker.polls[0] == (
        "job-one",
        {
            "timeout": IMAGE_JOB_POLL_TIMEOUT_SECONDS,
        },
    )
    assert all("b64_json" not in path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))


@pytest.mark.parametrize("data", [None, [], [{"url": "https://untrusted.invalid/image"}],
                                 [{"b64_json": "not-base64"}], [{"b64_json": encoded_image()}] * 2])
def test_invalid_output_retains_terminal_failure_without_resubmission(tmp_path, data):
    broker = Broker()
    broker.job.update(status="succeeded", response={"data": data})
    service = ImageGenerationService(tmp_path, "owner", broker, publisher)
    params = dict(request_id="draft", model="gpt-image-1", prompt="An icon")
    view = service.submit(**params)
    assert view["status"] == "invalid_output"
    assert service.get("draft")["status"] == "invalid_output"
    assert service.submit(**params)["status"] == "invalid_output"
    assert len(broker.submits) == 1 and not broker.polls


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_supported_image_formats_are_decoded(format, mime):
    raw, _, actual_mime, size = decode_generated_image(encoded_image(format))
    assert raw and actual_mime == mime and size == (16, 12)


def test_image_bounds_and_non_raster_outputs_are_rejected(monkeypatch):
    import adaos.services.image_generation as module
    with pytest.raises(Exception):
        decode_generated_image(encoded_image("GIF"))
    with pytest.raises(Exception):
        decode_generated_image(base64.b64encode(b"<svg/>").decode())
    monkeypatch.setattr(module, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(ValueError, match="pixel limit"):
        decode_generated_image(encoded_image())
    monkeypatch.setattr(module, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        decode_generated_image(encoded_image())


def test_unknown_usage_is_not_zero_and_modified_file_is_not_published(tmp_path):
    broker = Broker()
    broker.job.update(status="succeeded", response={"data": [{"b64_json": encoded_image()}]})
    service = ImageGenerationService(tmp_path, "owner", broker, publisher)
    view = service.submit(request_id="one", model="gpt-image-1", prompt="An icon")
    assert view["metering_status"] == "not_reported" and view["usage"] == {}
    next(tmp_path.rglob("*.png")).write_bytes(b"modified")
    with pytest.raises(ValueError, match="integrity"):
        service.get("one")
    assert not broker.polls


def test_submission_recovery_uses_same_root_identity(tmp_path):
    broker = Broker()
    original = broker.submit_image_job
    def interrupted(**kwargs):
        original(**kwargs)
        raise TimeoutError("submission acknowledgment lost")
    broker.submit_image_job = interrupted
    service = ImageGenerationService(tmp_path, "owner", broker, publisher)
    params = dict(request_id="one", model="gpt-image-1", prompt="An icon")
    with pytest.raises(TimeoutError):
        service.submit(**params)
    broker.submit_image_job = original
    assert service.submit(**params)["status"] == "queued"
    assert broker.submits[0] == broker.submits[1]


def test_draft_history_is_owned_context_scoped_bounded_and_never_polls(tmp_path):
    broker = Broker()
    service = ImageGenerationService(tmp_path, "owner", broker, publisher)
    for number, project in enumerate(["one", "two", "one"]):
        service.submit(request_id=f"draft-{number}", model="image-model", prompt="Private instruction",
                       context={"project_ref": project, "purpose": "icon"})
    reopened = ImageGenerationService(tmp_path, "owner", broker, publisher)
    rows = reopened.list_drafts(context={"project_ref": "one", "purpose": "icon"})
    assert [row["request_id"] for row in rows] == ["draft-2", "draft-0"]
    assert reopened.list_drafts(context={"project_ref": "one"}, limit=1) == rows[:1]
    assert not reopened.list_drafts(context={"missing": None})
    assert "Private instruction" not in json.dumps(rows)
    assert str(tmp_path) not in json.dumps(rows)
    assert not ImageGenerationService(tmp_path, "another-owner", broker, publisher).list_drafts(context={"project_ref": "one"})
    assert len(broker.submits) == 3 and not broker.polls
    for limit in [0, 51, True, 1.5]:
        with pytest.raises(ValueError, match="limit"):
            reopened.list_drafts(context={"project_ref": "one"}, limit=limit)
    with pytest.raises(ValueError, match="context"):
        reopened.list_drafts(context={})


def test_legacy_completed_draft_can_be_found_after_restart_without_provider(tmp_path):
    broker = Broker()
    broker.job.update(status="succeeded", response={"data": [{"b64_json": encoded_image()}]})
    service = ImageGenerationService(tmp_path, "owner", broker, publisher)
    service.submit(request_id="legacy", model="image-model", prompt="Icon", context={"project_ref": "one"})
    path = next(tmp_path.rglob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record.pop("created_at")
    path.write_text(json.dumps(record), encoding="utf-8")
    def no_media(*args, **kwargs):
        pytest.fail("History must not rehash or publish every image")
    rows = ImageGenerationService(tmp_path, "owner", broker, no_media).list_drafts(context={"project_ref": "one"})
    assert len(rows) == 1 and rows[0]["created_at"] is None and rows[0]["updated_at"] and rows[0]["status"] == "completed"
    assert "media" not in rows[0]
    assert len(broker.submits) == 1 and not broker.polls


@pytest.mark.parametrize("action,right", [("generate", "workspace.write"), ("get", "workspace.read"), ("list_drafts", "workspace.read")])
def test_sdk_requires_verified_actor_before_accessing_context(monkeypatch, action, right):
    from adaos.sdk.llm import images
    calls = []
    def deny(capability):
        calls.append(capability)
        raise PermissionError("denied")
    monkeypatch.setattr(images.access, "require", deny)
    monkeypatch.setattr(images, "require_ctx", lambda *_: pytest.fail("must authorize first"))
    with pytest.raises(PermissionError):
        if action == "generate":
            images.generate(request_id="one", model="gpt-image-1", prompt="Icon")
        elif action == "get":
            images.get("one")
        else:
            images.list_drafts(context={"project_ref": "one"})
    assert calls == [right]
