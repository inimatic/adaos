"""Durable image drafts. Generation never changes an application's owned assets."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json, mutation_lock
from adaos.services.content_generation import _digest


MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
_TERMINAL = {"completed", "failed", "cancelled", "invalid_output"}


def decode_generated_image(encoded: str) -> tuple[bytes, str, str, tuple[int, int]]:
    from PIL import Image

    if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("Image output is empty or exceeds the encoded size limit")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image output exceeds the byte limit")
    with Image.open(io.BytesIO(raw), formats=["PNG", "JPEG", "WEBP"]) as image:
        formats = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}
        if image.format not in formats or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Only one still PNG, JPEG or WebP image is supported")
        if min(image.size) < 1 or image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image output exceeds the pixel limit")
        suffix, mime = formats[image.format]
        size = image.size
        image.verify()
    # Decode pixels as well: a syntactically valid header is not sufficient.
    with Image.open(io.BytesIO(raw), formats=["PNG", "JPEG", "WEBP"]) as image:
        image.load()
    return raw, suffix, mime, size


class ImageGenerationService:
    def __init__(self, root: Path, owner: str, broker: Any, publish: Callable[..., dict[str, Any]]):
        if not owner:
            raise ValueError("Image generation requires a verified owner")
        self.root = root / hashlib.sha256(owner.encode("utf-8")).hexdigest()
        self.broker = broker
        self.publish = publish

    def _path(self, request_id: str) -> Path:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValueError("A stable request_id (1..200 characters) is required")
        return self.root / (hashlib.sha256(request_id.encode("utf-8")).hexdigest() + ".json")

    def submit(self, *, request_id: str, model: str, prompt: str, size: str = "1024x1024",
               quality: str = "low", output_format: str = "png", background: str = "opaque",
               context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from PIL import Image

        Image.init()  # Verify local decoder availability before starting a paid job.
        request = dict(model=model, prompt=prompt, size=size, quality=quality, output_format=output_format, background=background)
        local_context = dict(context or {})
        if len(json.dumps(local_context, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 8192:
            raise ValueError("Image draft context exceeds 8 KiB; use references, not asset contents")
        digest = _digest([request, local_context])
        path = self._path(request_id)
        with mutation_lock(path.with_suffix(".lock")):
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["input_digest"] != digest:
                    raise ValueError("request_id already belongs to a different image request")
                if record.get("job"):
                    return self._view(record)
            else:
                record = {"schema": "adaos.image_draft.v1", "request_id": request_id, "input_digest": digest,
                          "request": request, "context": local_context, "status": "submitting",
                          "root_request_id": "image-" + _digest([str(self.root), request_id])}
                atomic_write_json(path, record)
            job = self.broker.submit_image_job(request_id=record["root_request_id"], **request)
            if not isinstance(job, Mapping) or not job.get("job_id"):
                raise ValueError("Root did not return a durable job; retry only the same request_id")
            record["job"] = {"job_id": job["job_id"], "base_url": (job.get("_client") or {}).get("base_url")}
            atomic_write_json(path, record)
            self._capture(record, job)
            atomic_write_json(path, record)
            return self._view(record)

    def get(self, request_id: str) -> dict[str, Any]:
        path = self._path(request_id)
        with mutation_lock(path.with_suffix(".lock")):
            if not path.exists():
                raise ValueError("Image request was not found for the current owner")
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["status"] not in _TERMINAL and record.get("job"):
                job = record["job"]
                self._capture(record, self.broker.get_response_job(job["job_id"], base_url=job.get("base_url")))
                atomic_write_json(path, record)
            return self._view(record)

    def _capture(self, record: dict[str, Any], job: Mapping[str, Any]) -> None:
        from PIL import Image

        response = job.get("response") or {}
        if not isinstance(response, Mapping):
            record.update(status="invalid_output", message="Invalid provider response envelope")
            return
        record["status"] = {"succeeded": "completed", "canceled": "cancelled"}.get(str(job.get("status")), str(job.get("status") or "queued"))
        record["model"] = job.get("model") or record["request"]["model"]
        record["usage"] = response.get("usage") or (job.get("_protocol") or {}).get("usage") or {}
        record["metering_status"] = "reported" if response.get("usage") else "not_reported"
        if record["status"] != "completed":
            record["message"] = str((job.get("error") or {}).get("code") or record["status"]) if isinstance(job.get("error"), Mapping) else record["status"]
            return
        try:
            data = response.get("data")
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
                raise ValueError("Provider must return exactly one encoded image, not a remote URL")
            raw, suffix, mime, size = decode_generated_image(data[0].get("b64_json"))
        except (ValueError, OSError, TypeError, Image.DecompressionBombError) as exc:
            record.update(status="invalid_output", message=f"Invalid image output ({type(exc).__name__})")
            return
        digest = hashlib.sha256(raw).hexdigest()
        atomic_write_bytes(self.root / f"{digest}.{suffix}", raw)
        record["asset"] = {"sha256": digest, "suffix": suffix, "mime": mime, "width": size[0], "height": size[1], "size_bytes": len(raw)}
        record["message"] = ""

    def _view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        view = {key: record.get(key) for key in ("request_id", "root_request_id", "input_digest", "status", "model", "usage", "metering_status", "context", "message")}
        if record["status"] == "completed":
            asset = record["asset"]
            path = self.root / f"{asset['sha256']}.{asset['suffix']}"
            if hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
                raise ValueError("Generated image integrity check failed")
            view["media"] = {**self.publish(path, content_ref="generated-image:" + asset["sha256"], mime=asset["mime"]),
                             **{key: asset[key] for key in ("sha256", "width", "height", "size_bytes")}}
        return view
