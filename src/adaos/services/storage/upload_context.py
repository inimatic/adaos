"""Trusted binary upload context bound by the HTTP tool ingress."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import re
from typing import Iterator


MAX_TOOL_ATTACHMENT_BYTES = 10 * 1024 * 1024
_FIELD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def attachment_filename(value: str) -> str:
    filename = str(value or "").strip()
    if (
        not filename
        or len(filename) > 200
        or filename in {".", ".."}
        or any(character in filename for character in "/\\:")
        or any(ord(character) < 32 for character in filename)
    ):
        raise ValueError("invalid attachment filename")
    return filename


def attachment_field_id(value: str) -> str:
    field_id = str(value or "").strip()
    if not _FIELD_RE.fullmatch(field_id):
        raise ValueError("invalid attachment field id")
    return field_id


@dataclass(slots=True)
class VerifiedToolUpload:
    filename: str
    field_id: str
    media_type: str
    content: bytes
    consumed: bool = False

    def __post_init__(self) -> None:
        self.filename = attachment_filename(self.filename)
        self.field_id = attachment_field_id(self.field_id)
        self.media_type = str(self.media_type or "application/octet-stream").strip().lower()
        self.content = bytes(self.content)
        if not self.content or len(self.content) > MAX_TOOL_ATTACHMENT_BYTES:
            raise ValueError("attachment must contain between 1 byte and 10 MiB")

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.content).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.content)

    def metadata(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "field_id": self.field_id,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "digest": self.digest,
        }


_UPLOAD: ContextVar[VerifiedToolUpload | None] = ContextVar(
    "adaos_verified_tool_upload",
    default=None,
)


@contextmanager
def verified_tool_upload(upload: VerifiedToolUpload) -> Iterator[VerifiedToolUpload]:
    marker = _UPLOAD.set(upload)
    try:
        yield upload
    finally:
        _UPLOAD.reset(marker)


def consume_verified_tool_upload() -> VerifiedToolUpload:
    upload = _UPLOAD.get()
    if upload is None:
        raise RuntimeError("verified tool upload context is unavailable")
    if upload.consumed:
        raise RuntimeError("verified tool upload was already consumed")
    upload.consumed = True
    return upload


__all__ = [
    "MAX_TOOL_ATTACHMENT_BYTES",
    "VerifiedToolUpload",
    "attachment_field_id",
    "attachment_filename",
    "consume_verified_tool_upload",
    "verified_tool_upload",
]
