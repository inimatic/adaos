import pytest
import base64
import io
from PIL import Image

from adaos.sdk.llm import llm_client

def edit_image_reference(mode="RGBA", size=(16, 16)):
    output = io.BytesIO()
    Image.new(mode, size).save(output, format="PNG")
    return {"image_url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode('ascii')}


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


def test_image_edit_preserves_source_mask_and_durable_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_submit_root_job", lambda body, **kw: calls.append(body) or {"job_id": "edit.test"})
    image = edit_image_reference()
    llm_client.submit_image_edit_job(request_id="edit.test", model="gpt-image-2.5-sunburst-2026-09-08",
        prompt="Preserve skin texture", images=[image], mask=image, size="1536x2048")
    assert calls[0]["api"] == "images.edit"
    assert calls[0]["image"]["images"] == [image]
    assert calls[0]["image"]["mask"] == image
    assert calls[0]["request_id"] == "edit.test"


@pytest.mark.parametrize("patch", [
    {"images": []}, {"images": [{"image_url": "https://example.com/image.png"}]},
    {"images": [{"image_url": "data:image/png;base64,%%%"}]},
    {"mask": {"image_url": "data:image/jpeg;base64,aGVsbG8="}},
    {"size": "2049x2048"}, {"size": "3840x3840"}, {"size": "16x16"}, {"quality": "unknown"},
    {"mask": edit_image_reference(mode="RGB")}, {"mask": edit_image_reference(size=(32, 16))},
])
def test_invalid_image_edit_is_rejected_before_billable_submission(monkeypatch, patch):
    monkeypatch.setattr(llm_client, "_submit_root_job", lambda *a, **kw: pytest.fail("Invalid edit reached Root"))
    with pytest.raises(ValueError):
        llm_client.submit_image_edit_job(**{"request_id": "edit.test", "model": "gpt-image-2.5-sunburst",
            "prompt": "Retouch", "images": [edit_image_reference()], **patch})
