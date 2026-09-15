"""Explicit image input objects for typed content requests, without URL fetching."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Mapping


def image_input(data: bytes, *, media_type: str, detail: str = "auto") -> dict[str, Any]:
    if not isinstance(data, bytes) or not data or len(data) > 5 * 1024 * 1024:
        raise ValueError("An image must contain 1 byte to 5 MiB of explicitly supplied bytes")
    signatures = {"image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                  "image/jpeg": data.startswith(b"\xff\xd8\xff"),
                  "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
    if not signatures.get(media_type) or detail not in {"auto", "low", "high"}:
        raise ValueError("Unsupported image type, signature or detail")
    return {"media_type": media_type, "detail": detail, "sha256": hashlib.sha256(data).hexdigest(),
            "data_url": f"data:{media_type};base64," + base64.b64encode(data).decode("ascii")}


def validate_image_input(value: Mapping[str, Any]) -> dict[str, Any]:
    url = str(value.get("data_url") or "")
    media_type = str(value.get("media_type") or "")
    prefix = f"data:{media_type};base64,"
    if not url.startswith(prefix) or len(url) > 7 * 1024 * 1024:
        raise ValueError("Only bounded, explicit image data is accepted; URLs are not fetched")
    checked = image_input(base64.b64decode(url[len(prefix):], validate=True), media_type=media_type,
                          detail=str(value.get("detail") or "auto"))
    if checked["sha256"] != value.get("sha256"):
        raise ValueError("Image input digest mismatch")
    return checked


__all__ = ["image_input"]
