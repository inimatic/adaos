import json
from types import SimpleNamespace

import pytest

from adaos.services.content_generation import ContentGenerationService, draft_schema


SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}


def setup_service(tmp_path, result=None):
    calls = []
    def submit(messages, **kwargs):
        calls.append((messages, kwargs))
        return {"job_id": "job-1", "status": "queued", "_client": {"base_url": "https://root.invalid"}}
    def get(job_id, **kwargs):
        assert job_id == "job-1" and kwargs["base_url"] == "https://root.invalid"
        return result or {"status": "succeeded", "response": {"model": "test", "usage": {"total_tokens": 21}},
                          "output_text": json.dumps({"status": "completed", "data": {"text": "Пример"}, "message": ""}, ensure_ascii=False)}
    return ContentGenerationService(tmp_path, "skill:one|user:one", SimpleNamespace(submit_response_job=submit, get_response_job=get)), calls


def request(service, **kwargs):
    return service.submit(**{"request_id": "draft-1", "purpose": "Write a note", "prompt": "A short note", "schema": SCHEMA, **kwargs})


def test_durable_draft_preserves_input_and_unicode_without_regeneration(tmp_path):
    service, calls = setup_service(tmp_path)
    assert request(service)["status"] == "queued"
    assert request(service)["status"] == "queued"
    draft = service.get("draft-1")
    assert draft["data"] == {"text": "Пример"}
    assert draft["usage"]["total_tokens"] == 21
    assert service.get("draft-1") == draft
    assert len(calls) == 1
    assert calls[0][1]["profile_scope"] == "runtime"
    assert "Write a note" in calls[0][0][0]["content"]
    assert "A short note" not in calls[0][0][0]["content"]
    assert "Пример" in next(service.root.glob("*.json")).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="different content"):
        request(service, prompt="Changed")


@pytest.mark.parametrize("result,expected", [
    ({"status": "succeeded", "output_text": '{"status":"completed","data":{},"message":""}'}, "invalid_output"),
    ({"status": "succeeded", "output_text": '{"status":"out_of_scope","data":null,"message":"Only notes"}'}, "out_of_scope"),
    ({"status": "succeeded", "output_text": '{"status":"out_of_scope","data":{"text":"bad"},"message":""}'}, "invalid_output"),
    ({"status": "succeeded", "output_text": "```json\n{}\n```"}, "invalid_output"),
    ({"status": "succeeded", "response": {"status": "incomplete"}, "output_text": "partial"}, "incomplete"),
    ({"status": "succeeded", "response": {"output": [{"content": [{"type": "refusal", "refusal": "No"}]}]}}, "refused"),
    ({"status": "failed", "error": "upstream"}, "failed"),
])
def test_result_admission(tmp_path, result, expected):
    service, _ = setup_service(tmp_path, result)
    request(service)
    assert service.get("draft-1")["status"] == expected


def test_no_external_schema_fetch_or_owner_escape(tmp_path):
    with pytest.raises(ValueError, match="external"):
        draft_schema({"$ref": "https://example.org/schema"})
    service, _ = setup_service(tmp_path)
    request(service, request_id="../../outside")
    assert len(list(service.root.glob("*.json"))) == 1
    other = ContentGenerationService(tmp_path, "skill:other|user:one", service.broker)
    with pytest.raises(ValueError, match="current owner"):
        other.get("../../outside")


def test_failed_submission_resumes_same_root_identity(tmp_path):
    service, calls = setup_service(tmp_path)
    submit = service.broker.submit_response_job
    def failed(*args, **kwargs):
        submit(*args, **kwargs)
        raise TimeoutError("reply lost")
    service.broker.submit_response_job = failed
    with pytest.raises(TimeoutError):
        request(service)
    service.broker.submit_response_job = submit
    request(service)
    assert calls[0][1]["request_id"] == calls[1][1]["request_id"]


def test_recursive_schema_keeps_its_original_root():
    schema = {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#"}}}}
    result = draft_schema(schema)
    assert result["$defs"]["content"]["properties"]["children"]["items"]["$ref"] == "#/$defs/content"


@pytest.mark.parametrize("schema", [
    {"$id": "https://example.org/schema", "type": "object"},
    {"$dynamicRef": "#node"},
    {"$defs": {"node": {"$dynamicAnchor": "node"}}},
])
def test_rejects_schema_resource_scope_changes(schema):
    with pytest.raises(ValueError, match="local JSON Pointer"):
        draft_schema(schema)


def test_content_facades_are_discoverable_without_application_specific_examples():
    from adaos.sdk.core.exporter import _public_facade_symbols
    symbols = {item["name"]: item for item in _public_facade_symbols("rich")}
    generate = symbols["adaos.sdk.llm.content.generate"]
    assert {"purpose", "schema", "request_id", "images"} <= {arg["name"] for arg in generate["signature_detail"]["args"]}
    assert "adaos.sdk.llm.media.image_input" in symbols
    assert "adaos.sdk.llm.media.validate_image_input" not in symbols
    assert "adaos.sdk.developer.documents.write" in symbols


def test_missing_root_identity_is_retryable(tmp_path):
    service, _ = setup_service(tmp_path)
    service.broker.submit_response_job = lambda *args, **kwargs: {"status": "queued"}
    with pytest.raises(ValueError, match="durable job identity"):
        request(service)
    assert service.get("draft-1")["status"] == "submitting"


def test_unresolved_model_is_rejected_before_root_admission(tmp_path):
    service, calls = setup_service(tmp_path)
    with pytest.raises(ValueError, match="unresolved UI expression"):
        request(service, model="$state.model")
    assert calls == []


def test_multimodal_input_is_explicit_bounded_and_separate_from_instructions(tmp_path):
    from adaos.sdk.llm.media import image_input, validate_image_input
    from adaos.sdk.llm.llm_client import _message_list, _responses_payload
    picture = image_input(b"\x89PNG\r\n\x1a\nexample", media_type="image/png")
    service, calls = setup_service(tmp_path)
    request(service, images=[picture])
    messages, _ = calls[0]
    assert picture["data_url"] not in messages[0]["content"]
    payload = _responses_payload({}, _message_list(messages))
    assert payload["input"][0]["content"][1]["image_url"] == picture["data_url"]
    with pytest.raises(ValueError, match="digest"):
        validate_image_input({**picture, "sha256": "wrong"})
    with pytest.raises(ValueError, match="URLs"):
        validate_image_input({**picture, "data_url": "http://localhost/private"})
    with pytest.raises(ValueError, match="four"):
        request(service, images=[picture] * 5)
