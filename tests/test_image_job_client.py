import pytest

from adaos.sdk.llm import llm_client


def test_image_job_uses_shared_transport_with_explicit_model_and_no_text_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_llm_root_payload", lambda *a, **kw: pytest.fail("No text profile in image generation"))
    monkeypatch.setattr(llm_client, "_submit_root_job", lambda body, **kw: calls.append((body, kw)) or {"job_id": "image.test"})
    assert llm_client.submit_image_job(request_id="image.test", model="gpt-image-1", prompt="Application icon")["job_id"] == "image.test"
    body, options = calls[0]
    assert body == {"api": "images.generate", "model": "gpt-image-1", "request_id": "image.test",
                    "image": {"prompt": "Application icon", "size": "1024x1024", "quality": "low",
                              "output_format": "png", "background": "opaque"}}
    assert options["request_id"] == "image.test"


@pytest.mark.parametrize("patch", [
    {"model": None}, {"model": "gpt-5"}, {"model": "$state.model"}, {"prompt": ""},
    {"size": "4096x4096"}, {"quality": "invalid"}, {"output_format": "svg"},
    {"output_format": "jpeg", "background": "transparent"}, {"request_id": ""},
])
def test_invalid_image_request_never_reaches_root(monkeypatch, patch):
    monkeypatch.setattr(llm_client, "_submit_root_job", lambda *a, **kw: pytest.fail("Invalid request reached Root"))
    with pytest.raises(ValueError):
        llm_client.submit_image_job(**{"request_id": "image.test", "model": "gpt-image-1", "prompt": "Icon", **patch})
