from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from uuid import uuid4

from adaos.services.artifact_pipeline.storage import atomic_write_json, replace_with_retry


SOURCE_SNAPSHOT_SCHEMA = "adaos.skill_factory.source_snapshot.v1"
_IGNORED_DIRS = {
    ".builder_previous_automation",
    ".git",
    ".pytest_cache",
    "__pycache__",
}
_IGNORED_FILES = {"prompt_state.json"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


class SourceSnapshotError(RuntimeError):
    pass


def _safe_relative(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or raw.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise SourceSnapshotError(f"unsafe source snapshot path: {value!r}")
    if path.parts and ":" in path.parts[0]:
        raise SourceSnapshotError(f"unsafe source snapshot path: {value!r}")
    return path.as_posix().strip("/")


def _ignored(relative: PurePosixPath) -> bool:
    return (
        any(part in _IGNORED_DIRS for part in relative.parts)
        or relative.name in _IGNORED_FILES
        or relative.suffix.lower() in _IGNORED_SUFFIXES
    )


def _source_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        raise SourceSnapshotError(f"source directory does not exist: {root}")
    files: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if _ignored(relative):
            continue
        if path.is_symlink():
            raise SourceSnapshotError(f"symbolic links are not allowed in task sources: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise SourceSnapshotError(f"unsupported task source entry: {relative}")
        files.append((relative.as_posix(), path))
    return files


def source_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for relative, path in _source_files(Path(root)):
        payload = path.read_bytes()
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def _copy_source_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    for relative, path in _source_files(source):
        destination = target / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _snapshot_root(state_dir: Path) -> Path:
    return Path(state_dir) / "skill_factory" / "source_snapshots"


def capture_source_snapshot(
    *,
    state_dir: Path,
    artifacts: Iterable[tuple[str, str, Path]],
    attachments: Iterable[tuple[str, Path, str]] = (),
    created_at: str,
) -> dict[str, Any]:
    artifact_rows: list[dict[str, Any]] = []
    artifact_sources: list[tuple[dict[str, Any], Path]] = []
    for kind, artifact_id, source in artifacts:
        relative = _safe_relative(f"{str(kind).strip().lower().rstrip('s')}s/{artifact_id}")
        row = {
            "kind": str(kind).strip().lower().rstrip("s"),
            "id": str(artifact_id).strip(),
            "path": relative,
            "digest": source_tree_digest(source),
        }
        artifact_rows.append(row)
        artifact_sources.append((row, Path(source)))

    attachment_rows: list[dict[str, Any]] = []
    attachment_sources: list[tuple[dict[str, Any], Path]] = []
    for name, source, target_path in attachments:
        source_path = Path(source)
        if not source_path.is_dir():
            continue
        row = {
            "name": str(name).strip(),
            "path": _safe_relative(f"attachments/{name}"),
            "target_path": _safe_relative(target_path),
            "digest": source_tree_digest(source_path),
        }
        attachment_rows.append(row)
        attachment_sources.append((row, source_path))

    identity = json.dumps(
        {"artifacts": artifact_rows, "attachments": attachment_rows},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(identity).hexdigest()
    snapshot_id = digest.split(":", 1)[1]
    root = _snapshot_root(Path(state_dir))
    root.mkdir(parents=True, exist_ok=True)
    destination = root / snapshot_id
    manifest = {
        "schema": SOURCE_SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "digest": digest,
        "artifacts": artifact_rows,
        "attachments": attachment_rows,
        "created_at": str(created_at),
    }
    if destination.exists():
        verify_source_snapshot(state_dir=state_dir, reference=manifest)
        return manifest

    staged = root / f".{snapshot_id}.{uuid4().hex}.tmp"
    staged.mkdir(parents=False, exist_ok=False)
    try:
        for row, source in artifact_sources:
            _copy_source_tree(source, staged / Path(row["path"]))
        for row, source in attachment_sources:
            _copy_source_tree(source, staged / Path(row["path"]))
        atomic_write_json(staged / "snapshot.json", manifest)
        try:
            replace_with_retry(staged, destination)
        except FileExistsError:
            shutil.rmtree(staged, ignore_errors=True)
        verify_source_snapshot(state_dir=state_dir, reference=manifest)
        return manifest
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)


def verify_source_snapshot(*, state_dir: Path, reference: Mapping[str, Any]) -> dict[str, Any]:
    snapshot_id = str(reference.get("snapshot_id") or "").strip()
    expected_digest = str(reference.get("digest") or "").strip()
    if len(snapshot_id) != 64 or any(char not in "0123456789abcdef" for char in snapshot_id):
        raise SourceSnapshotError("source snapshot id must be a lowercase SHA-256 hex digest")
    if expected_digest != f"sha256:{snapshot_id}":
        raise SourceSnapshotError("source snapshot digest does not match snapshot id")
    root = _snapshot_root(Path(state_dir)) / snapshot_id
    manifest_path = root / "snapshot.json"
    try:
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SourceSnapshotError(f"source snapshot manifest is unavailable: {snapshot_id}") from exc
    if not isinstance(stored, Mapping) or stored.get("schema") != SOURCE_SNAPSHOT_SCHEMA:
        raise SourceSnapshotError("invalid source snapshot manifest")
    if str(stored.get("digest") or "") != expected_digest:
        raise SourceSnapshotError("stored source snapshot identity differs from task reference")
    for group in ("artifacts", "attachments"):
        expected_rows = reference.get(group)
        if expected_rows is not None and list(expected_rows or []) != list(stored.get(group) or []):
            raise SourceSnapshotError(f"stored source snapshot {group} differ from task reference")
        for item in stored.get(group) or []:
            if not isinstance(item, Mapping):
                raise SourceSnapshotError(f"invalid source snapshot {group} entry")
            path = root / Path(_safe_relative(item.get("path")))
            if source_tree_digest(path) != str(item.get("digest") or ""):
                raise SourceSnapshotError(f"source snapshot content mismatch: {item.get('path')}")
    return dict(stored)


def materialize_source_snapshot(
    *,
    state_dir: Path,
    reference: Mapping[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    manifest = verify_source_snapshot(state_dir=state_dir, reference=reference)
    snapshot_root = _snapshot_root(Path(state_dir)) / str(manifest["snapshot_id"])
    for item in manifest.get("artifacts") or []:
        relative = _safe_relative(item.get("path"))
        _copy_source_tree(snapshot_root / Path(relative), Path(workspace) / Path(relative))
    for item in manifest.get("attachments") or []:
        source = snapshot_root / Path(_safe_relative(item.get("path")))
        target = Path(workspace) / Path(_safe_relative(item.get("target_path")))
        if target.exists():
            shutil.rmtree(target)
        _copy_source_tree(source, target)
    return manifest


__all__ = [
    "SOURCE_SNAPSHOT_SCHEMA",
    "SourceSnapshotError",
    "capture_source_snapshot",
    "materialize_source_snapshot",
    "source_tree_digest",
    "verify_source_snapshot",
]
