"""SDK operations for durable Builder artifact checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.developer import projects
from adaos.services.artifact_pipeline.storage import atomic_write_json


_LOCAL_CHECKPOINT_EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "node_modules",
}
_LOCAL_CHECKPOINT_EXCLUDED_NAMES = {"builder.draft.json", "prompt_state.json", "prep_result.json"}
_LOCAL_CHECKPOINT_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _local_source_files(root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if any(part in _LOCAL_CHECKPOINT_EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unsupported local checkpoint input: {relative.as_posix()}")
        if relative.name in _LOCAL_CHECKPOINT_EXCLUDED_NAMES or relative.suffix.lower() in _LOCAL_CHECKPOINT_EXCLUDED_SUFFIXES:
            continue
        payload = path.read_bytes()
        files.append({"path": relative.as_posix(), "size_bytes": len(payload), "digest": _digest(payload)})
    return files


def _service():
    from adaos.services.builder.workspace import BuilderWorkspaceService

    return BuilderWorkspaceService.from_context()


def checkpoint(
    *,
    kind: str,
    artifact_id: str,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    service = _service()
    return dict(
        service.checkpoint_artifact(
            kind=kind,
            artifact_id=artifact_id,
            message=message,
            metadata=metadata,
        )
        or {}
    )


def local_checkpoint(
    *,
    kind: str,
    artifact_id: str,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze a private pre-automation source identity without publishing it.

    Project-owned ``artifacts/partN`` inputs are intentionally excluded from
    the component source tree because their independently validated manifest
    digests are bound by the AutomationBrief and Development Session.
    """

    normalized = str(kind or "").strip().lower().rstrip("s")
    if normalized not in {"skill", "scenario"}:
        raise ValueError("kind must be skill or scenario")
    root = projects.resolve_root(normalized, artifact_id)
    files = _local_source_files(root)
    if not files:
        raise ValueError("local checkpoint source tree is empty")
    source_tree = _digest(_canonical({"schema": "adaos.builder.local_source_tree.v1", "files": files}))
    identity = {
        "schema": "adaos.builder.local_checkpoint.v1",
        "kind": normalized,
        "artifact_id": str(artifact_id),
        "source_tree": source_tree,
        "artifact_inputs": "manifest-bound-separately",
    }
    package_digest = _digest(_canonical(identity))
    ctx = require_ctx("sdk.builder.artifacts.local_checkpoint")
    checkpoint_path = (
        Path(ctx.paths.state_dir()).resolve()
        / "builder"
        / "checkpoints"
        / normalized
        / str(artifact_id)
        / f"{package_digest.removeprefix('sha256:')}.json"
    ).resolve()
    record = {
        **identity,
        "package_digest": package_digest,
        "source_revision": source_tree,
        "sha256": package_digest,
        "source_path": str(root),
        "files": files,
        "message": " ".join(str(message or "").split()).strip() or None,
        "metadata": dict(metadata or {}),
    }
    atomic_write_json(checkpoint_path, record)
    return {
        "ok": True,
        "scope": "local",
        "kind": normalized,
        "name": str(artifact_id),
        "stored_path": str(checkpoint_path),
        "sha256": package_digest,
        "bytes_uploaded": 0,
        "package_digest": package_digest,
        "source_revision": source_tree,
        "source_tree": source_tree,
        "artifact_inputs": "manifest-bound-separately",
        "metadata": dict(metadata or {}),
    }


def create_draft(
    *,
    kind: str,
    artifact_id: str,
    source_idea: str,
    template_id: str | None = None,
    webspace_id: str | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return dict(
        _service().create_draft(
            kind=kind,
            artifact_id=artifact_id,
            source_idea=source_idea,
            template_id=template_id,
            webspace_id=webspace_id,
            source=source,
        )
        or {}
    )


def get_draft(draft_id: str) -> dict[str, Any]:
    """Read one bounded Builder draft descriptor from runtime state."""

    token = str(draft_id or "").strip()
    if not token or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in token):
        raise ValueError("draft_id contains unsupported characters")
    from adaos.services.runtime_paths import current_state_dir

    path = current_state_dir() / "builder" / "drafts" / token / "builder.draft.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = ["checkpoint", "create_draft", "get_draft", "local_checkpoint"]
