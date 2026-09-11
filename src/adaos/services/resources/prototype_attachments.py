"""Bounded content-addressed files owned by a disposable Prototype resource."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from adaos.services.artifact_pipeline.storage import atomic_write_bytes, mutation_lock
from .prototype import PrototypeResourceService

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_RESOURCE_BYTES = 64 * 1024 * 1024


def _filename(value: str) -> str:
    if not value or len(value) > 200 or value in {".", ".."} or any(char in value for char in '/\\:') or any(ord(char) < 32 for char in value):
        raise ValueError("invalid attachment filename")
    return value


class PrototypeAttachmentStore:
    def __init__(self, prototypes: PrototypeResourceService):
        self.prototypes = prototypes

    def _root(self, resource_type: str) -> Path:
        if not self.prototypes.definition(resource_type):
            raise KeyError(resource_type)
        return self.prototypes.root / "attachments" / hashlib.sha256(resource_type.encode()).hexdigest()

    def put(self, resource_type: str, field_id: str, filename: str, content: bytes) -> dict:
        filename = _filename(filename)
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError("attachment must contain between 1 byte and 10 MiB")
        root = self._root(resource_type)
        definition = self.prototypes.definition(resource_type)
        field = definition["record_schema"].get("properties", {}).get(field_id, {})
        if field.get("format") != "adaos-attachment" and field.get("items", {}).get("format") != "adaos-attachment":
            raise ValueError("field is not a declared attachment")
        if not any(operation.get("kind", operation.get("id")) in {"create", "update"} for operation in definition.get("operations", [])):
            raise ValueError("resource does not allow attachment capture")
        digest = hashlib.sha256(content).hexdigest()
        path = root / digest
        with mutation_lock(root / ".attachments.lock"):
            if not path.exists():
                total = sum(item.stat().st_size for item in root.iterdir() if item.is_file() and re.fullmatch(r"[0-9a-f]{64}", item.name))
                if total + len(content) > MAX_RESOURCE_BYTES:
                    raise ValueError("prototype attachment quota exceeded (64 MiB)")
                atomic_write_bytes(path, content)
        return {"ref": f"/api/resources/prototypes/{quote(resource_type, safe='')}/attachments/{digest}/{quote(filename, safe='')}",
                "sha256": digest, "size_bytes": len(content), "name": filename}

    def get(self, resource_type: str, digest: str, filename: str) -> tuple[Path, str]:
        _filename(filename)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid attachment digest")
        path = self._root(resource_type) / digest
        if not path.is_file():
            raise KeyError(digest)
        guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        allowed = {"image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm", "audio/mpeg", "audio/wav", "audio/ogg"}
        return path, guessed if guessed in allowed else "application/octet-stream"
