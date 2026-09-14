"""Bounded, optimistic-concurrency edits of application-owned text documents."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.developer import compositions, projects
from adaos.services.artifact_pipeline.storage import atomic_write_bytes, mutation_lock


def _target(kind: str, object_id: str, path: str) -> tuple[str, Path]:
    root = compositions.resolve_root(object_id) if kind == "project" else projects.resolve_root(kind, object_id)
    relative, full = projects._file(root, path)
    editable, reason = projects._editable(relative, full)
    if not editable or full.suffix.lower() not in {".md", ".txt"}:
        raise ValueError(f"Document is not editable: {reason or 'unsupported_document_type'}")
    return relative, full


def read(kind: str, object_id: str, path: str = "README.md") -> dict[str, Any]:
    relative, full = _target(kind, object_id, path)
    if not full.exists():
        return {"path": relative, "text": "", "digest": "missing", "exists": False}
    if full.stat().st_size > 131072:
        raise ValueError("Document exceeds 128 KiB; partial content cannot be edited")
    raw = full.read_bytes()
    if len(raw) > 131072:
        raise ValueError("Document exceeds 128 KiB")
    return {"path": relative, "text": raw.decode("utf-8-sig"), "exists": True,
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}


def write(kind: str, object_id: str, text: str, *, expected_digest: str,
          path: str = "README.md") -> dict[str, Any]:
    relative, full = _target(kind, object_id, path)
    raw = text.encode("utf-8")
    if len(raw) > 131072:
        raise ValueError("Document exceeds 128 KiB")
    ctx = require_ctx("sdk.developer.documents.write")
    lock_id = hashlib.sha256(str(full).casefold().encode("utf-8")).hexdigest()
    lock = Path(ctx.paths.state_dir()) / "developer" / "document-locks" / lock_id
    with mutation_lock(lock):
        before = read(kind, object_id, relative)
        if before["digest"] != expected_digest:
            raise ValueError("Document changed since it was opened; reopen before saving")
        atomic_write_bytes(full, raw)
        result = read(kind, object_id, relative)
    if kind != "project":
        projects._publish_content_changed(kind, object_id, reason="document_written", changed_paths=[relative])
    return {"ok": True, **result}


__all__ = ["read", "write"]
