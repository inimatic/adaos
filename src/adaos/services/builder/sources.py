from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json, mutation_lock
from adaos.services.runtime_paths import current_state_dir


SOURCE_BUNDLE_SCHEMA = "adaos.builder.source_bundle.v1"
DEFAULT_MAX_SOURCE_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_SOURCES_PER_PROJECT = 500
_ID_RE = re.compile(r"^[a-z0-9_.-]+$")
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\s+|import\s+([^#\n]+))",
    re.MULTILINE,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _safe_project(kind: str, project_id: str) -> tuple[str, str]:
    normalized_kind = str(kind or "").strip().lower().rstrip("s")
    normalized_id = str(project_id or "").strip().lower()
    if normalized_kind not in {"project", "skill", "scenario"}:
        raise ValueError("project kind must be project, skill or scenario")
    if not _ID_RE.fullmatch(normalized_id):
        raise ValueError("project_id must match ^[a-z0-9_.-]+$")
    return normalized_kind, normalized_id


def _safe_name(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip()
    name = Path(normalized).name.strip()
    if not name or name in {".", ".."} or normalized != name:
        raise ValueError("source name must be a plain file name")
    if any(ord(char) < 32 for char in name):
        raise ValueError("source name contains control characters")
    return name[:240]


def _notebook_inventory(payload: bytes) -> dict[str, Any]:
    try:
        notebook = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        return {"kind": "jupyter_notebook", "valid": False, "error": str(exc)[:240]}
    cells = notebook.get("cells") if isinstance(notebook, dict) else None
    if not isinstance(cells, list):
        return {"kind": "jupyter_notebook", "valid": False, "error": "cells must be an array"}
    code = [cell for cell in cells if isinstance(cell, dict) and cell.get("cell_type") == "code"]
    markdown = [cell for cell in cells if isinstance(cell, dict) and cell.get("cell_type") == "markdown"]
    imports: set[str] = set()
    code_chars = 0
    outputs = 0
    executed = 0
    for cell in code:
        source = cell.get("source") or ""
        source_text = "".join(source) if isinstance(source, list) else str(source)
        code_chars += len(source_text)
        if cell.get("execution_count") is not None:
            executed += 1
        cell_outputs = cell.get("outputs")
        outputs += len(cell_outputs) if isinstance(cell_outputs, list) else 0
        for match in _IMPORT_RE.finditer(source_text):
            token = match.group(1) or match.group(2) or ""
            for part in token.split(","):
                root = part.strip().split(" as ", 1)[0].split(".", 1)[0].strip()
                if root:
                    imports.add(root)
    kernelspec = (notebook.get("metadata") or {}).get("kernelspec") if isinstance(notebook, dict) else {}
    return {
        "kind": "jupyter_notebook",
        "valid": True,
        "nbformat": notebook.get("nbformat"),
        "nbformat_minor": notebook.get("nbformat_minor"),
        "kernel": dict(kernelspec or {}) if isinstance(kernelspec, dict) else {},
        "cells": len(cells),
        "code_cells": len(code),
        "markdown_cells": len(markdown),
        "executed_code_cells": executed,
        "output_records": outputs,
        "code_characters": code_chars,
        "imports": sorted(imports)[:200],
        "warnings": (["notebook_outputs_are_untrusted_source_material"] if outputs else []),
    }


def _source_analysis(name: str, media_type: str, payload: bytes) -> dict[str, Any]:
    if name.lower().endswith(".ipynb") or media_type == "application/x-ipynb+json":
        return _notebook_inventory(payload)
    if media_type.startswith("text/") or name.lower().endswith((".md", ".txt", ".py", ".json", ".yaml", ".yml")):
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            return {"kind": "text", "valid_utf8": False}
        return {
            "kind": "text",
            "valid_utf8": True,
            "characters": len(text),
            "lines": text.count("\n") + (1 if text else 0),
            "preview": text[:2000],
        }
    return {"kind": "binary"}


@dataclass(slots=True)
class BuilderProjectSourceService:
    """Content-addressed, immutable source intake for any Builder project.

    Mutable project state only selects a current SourceBundle. Every bundle and
    payload object is immutable and addressed by digest, so a downstream agent
    can bind a handoff to the exact human-reviewed inputs.
    """

    state_dir: Path | None = None
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    max_sources_per_project: int = DEFAULT_MAX_SOURCES_PER_PROJECT

    @classmethod
    def from_context(cls) -> "BuilderProjectSourceService":
        return cls(state_dir=current_state_dir())

    @property
    def root(self) -> Path:
        root = Path(self.state_dir or current_state_dir()).expanduser().resolve() / "builder" / "sources"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _project_root(self, kind: str, project_id: str) -> Path:
        normalized_kind, normalized_id = _safe_project(kind, project_id)
        return self.root / "projects" / normalized_kind / normalized_id

    def _state_path(self, kind: str, project_id: str) -> Path:
        return self._project_root(kind, project_id) / "state.json"

    def _lock_path(self, kind: str, project_id: str) -> Path:
        return self._project_root(kind, project_id) / ".mutation.lock"

    def _object_path(self, digest: str) -> Path:
        token = str(digest).removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ValueError("invalid SHA-256 digest")
        return self.root / "objects" / "sha256" / token[:2] / token

    def _bundle_path(self, digest: str) -> Path:
        token = str(digest).removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ValueError("invalid SourceBundle digest")
        return self.root / "bundles" / f"{token}.json"

    def _empty_state(self, kind: str, project_id: str) -> dict[str, Any]:
        normalized_kind, normalized_id = _safe_project(kind, project_id)
        return {
            "schema": "adaos.builder.project_sources.v1",
            "project": {"kind": normalized_kind, "id": normalized_id, "ref": f"{normalized_kind}:{normalized_id}"},
            "generation": 0,
            "sources": [],
            "current_bundle_digest": None,
            "updated_at": None,
        }

    def get_state(self, kind: str, project_id: str) -> dict[str, Any]:
        path = self._state_path(kind, project_id)
        if not path.is_file():
            return self._empty_state(kind, project_id)
        value = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Builder project source state must be an object")
        return value

    def _make_bundle(self, state: Mapping[str, Any]) -> dict[str, Any]:
        identity = {
            "schema": SOURCE_BUNDLE_SCHEMA,
            "schema_version": "1.0.0",
            "project": dict(state["project"]),
            "generation": int(state.get("generation") or 0),
            "sources": [dict(item) for item in state.get("sources") or []],
        }
        digest = _digest(_canonical_json(identity))
        return {**identity, "digest": digest, "created_at": _now()}

    def add_bytes(
        self,
        *,
        kind: str,
        project_id: str,
        name: str,
        payload: bytes,
        media_type: str | None = None,
        role: str = "source",
        origin: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_kind, normalized_id = _safe_project(kind, project_id)
        safe_name = _safe_name(name)
        content = bytes(payload)
        if not content:
            raise ValueError("source payload must not be empty")
        if len(content) > int(self.max_source_bytes):
            raise ValueError(f"source exceeds max size: {self.max_source_bytes} bytes")
        content_digest = _digest(content)
        media = str(media_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream").strip()
        source = {
            "source_id": f"source-{content_digest.removeprefix('sha256:')[:20]}",
            "name": safe_name,
            "digest": content_digest,
            "size_bytes": len(content),
            "media_type": media,
            "role": str(role or "source").strip() or "source",
            "object_ref": f"builder-source:{content_digest}",
            "analysis": _source_analysis(safe_name, media, content),
            "origin": dict(origin or {}),
            "ingested_at": _now(),
        }
        with mutation_lock(self._lock_path(normalized_kind, normalized_id)):
            state = self.get_state(normalized_kind, normalized_id)
            existing = next((item for item in state.get("sources") or [] if item.get("digest") == content_digest), None)
            if existing is not None:
                bundle = self.get_bundle(str(state.get("current_bundle_digest") or ""))
                return {"ok": True, "idempotent": True, "source": dict(existing), "bundle": bundle}
            if len(state.get("sources") or []) >= int(self.max_sources_per_project):
                raise ValueError(f"project source count exceeds limit: {self.max_sources_per_project}")
            object_path = self._object_path(content_digest)
            if not object_path.is_file():
                atomic_write_bytes(object_path, content)
            state["sources"] = [*list(state.get("sources") or []), source]
            state["generation"] = int(state.get("generation") or 0) + 1
            state["updated_at"] = _now()
            bundle = self._make_bundle(state)
            state["current_bundle_digest"] = bundle["digest"]
            bundle_path = self._bundle_path(bundle["digest"])
            if not bundle_path.is_file():
                atomic_write_json(bundle_path, bundle)
            atomic_write_json(self._state_path(normalized_kind, normalized_id), state)
        return {"ok": True, "idempotent": False, "source": source, "bundle": bundle}

    def add_path(
        self,
        path: str | Path,
        *,
        kind: str,
        project_id: str,
        name: str | None = None,
        media_type: str | None = None,
        role: str = "source",
        origin: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_path = Path(path).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if source_path.stat().st_size > int(self.max_source_bytes):
            raise ValueError(f"source exceeds max size: {self.max_source_bytes} bytes")
        selected_origin = {"kind": "local_file", "original_name": source_path.name, **dict(origin or {})}
        return self.add_bytes(
            kind=kind,
            project_id=project_id,
            name=name or source_path.name,
            payload=source_path.read_bytes(),
            media_type=media_type,
            role=role,
            origin=selected_origin,
        )

    def get_bundle(self, digest: str) -> dict[str, Any]:
        token = str(digest or "").strip()
        if not token:
            return {}
        path = self._bundle_path(token)
        if not path.is_file():
            raise FileNotFoundError(f"SourceBundle not found: {token}")
        value = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        if not isinstance(value, dict) or value.get("digest") != token:
            raise ValueError("SourceBundle identity mismatch")
        return value

    def current_bundle(self, kind: str, project_id: str) -> dict[str, Any]:
        state = self.get_state(kind, project_id)
        digest = str(state.get("current_bundle_digest") or "")
        return self.get_bundle(digest) if digest else self._make_bundle(state)

    def read_source(self, digest: str, *, max_bytes: int | None = None) -> bytes:
        path = self._object_path(digest)
        if not path.is_file():
            raise FileNotFoundError(f"Builder source object not found: {digest}")
        limit = int(max_bytes or self.max_source_bytes)
        if path.stat().st_size > limit:
            raise ValueError(f"source exceeds read limit: {limit} bytes")
        payload = path.read_bytes()
        if _digest(payload) != digest:
            raise ValueError("Builder source object digest mismatch")
        return payload

    def read_text(self, digest: str, *, max_characters: int = 120_000) -> str:
        payload = self.read_source(digest, max_bytes=max(self.max_source_bytes, max_characters * 4))
        text = payload.decode("utf-8-sig")
        return text[: max(1, int(max_characters))]


__all__ = [
    "BuilderProjectSourceService",
    "DEFAULT_MAX_SOURCE_BYTES",
    "DEFAULT_MAX_SOURCES_PER_PROJECT",
    "SOURCE_BUNDLE_SCHEMA",
]
