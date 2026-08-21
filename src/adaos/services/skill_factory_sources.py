from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from uuid import uuid4

from adaos.services.artifact_pipeline.storage import (
    atomic_write_json,
    replace_with_retry,
)


SOURCE_SNAPSHOT_SCHEMA = "adaos.skill_factory.source_snapshot.v1"
_IGNORED_DIRS = {
    ".builder_current_publication",
    ".builder_previous_automation",
    ".git",
    ".pytest_cache",
    "__pycache__",
}
_IGNORED_FILES = {"prompt_state.json"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}
_RESERVED_PROJECT_INPUT_DIRS = {"artifacts"}
_SNAPSHOT_ARCHIVE = "payload.zip"


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


def source_projection_excluded_dirs(item: Mapping[str, Any]) -> frozenset[str]:
    projection = item.get("source_projection")
    if not isinstance(projection, Mapping):
        return frozenset()
    values = {
        str(value or "").strip().replace("\\", "/").strip("/")
        for value in projection.get("excluded_paths") or []
    }
    unsupported = values - _RESERVED_PROJECT_INPUT_DIRS
    if unsupported:
        raise SourceSnapshotError(
            "unsupported source projection exclusion: " + ", ".join(sorted(unsupported))
        )
    return frozenset(values)


def _ignored(
    relative: PurePosixPath, *, excluded_dirs: frozenset[str] = frozenset()
) -> bool:
    return (
        any(part in (_IGNORED_DIRS | excluded_dirs) for part in relative.parts)
        or relative.name in _IGNORED_FILES
        or relative.suffix.lower() in _IGNORED_SUFFIXES
    )


def _source_files(
    root: Path,
    *,
    excluded_dirs: frozenset[str] = frozenset(),
) -> list[tuple[str, Path]]:
    if not root.is_dir():
        raise SourceSnapshotError(f"source directory does not exist: {root}")
    files: list[tuple[str, Path]] = []
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if _ignored(relative, excluded_dirs=excluded_dirs):
            continue
        if path.is_symlink():
            raise SourceSnapshotError(
                f"symbolic links are not allowed in task sources: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise SourceSnapshotError(f"unsupported task source entry: {relative}")
        files.append((relative.as_posix(), path))
    return files


def source_tree_digest(
    root: Path,
    *,
    excluded_dirs: frozenset[str] = frozenset(),
) -> str:
    digest = hashlib.sha256()
    for relative, path in _source_files(Path(root), excluded_dirs=excluded_dirs):
        payload = path.read_bytes()
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def _copy_source_tree(
    source: Path,
    target: Path,
    *,
    excluded_dirs: frozenset[str] = frozenset(),
) -> None:
    target.mkdir(parents=True, exist_ok=False)
    for relative, path in _source_files(source, excluded_dirs=excluded_dirs):
        destination = target / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _snapshot_root(state_dir: Path) -> Path:
    return Path(state_dir) / "skill_factory" / "source_snapshots"


def _write_archive_entry(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(_safe_relative(name), date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (0o100444 & 0xFFFF) << 16
    archive.writestr(info, payload)


def _write_snapshot_archive(
    path: Path,
    *,
    artifact_sources: Iterable[tuple[Mapping[str, Any], Path]],
    attachment_sources: Iterable[tuple[Mapping[str, Any], Path]],
) -> str:
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        for row, source in artifact_sources:
            prefix = _safe_relative(row["path"])
            for relative, item in _source_files(
                source,
                excluded_dirs=frozenset(_RESERVED_PROJECT_INPUT_DIRS),
            ):
                _write_archive_entry(archive, f"{prefix}/{relative}", item.read_bytes())
        for row, source in attachment_sources:
            prefix = _safe_relative(row["path"])
            for relative, item in _source_files(source):
                _write_archive_entry(archive, f"{prefix}/{relative}", item.read_bytes())
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_snapshot_archive(
    root: Path, descriptor: Mapping[str, Any]
) -> dict[str, bytes]:
    relative = _safe_relative(descriptor.get("path"))
    if relative != _SNAPSHOT_ARCHIVE:
        raise SourceSnapshotError("unsupported source snapshot archive path")
    path = root / relative
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SourceSnapshotError("source snapshot archive is unavailable") from exc
    actual_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if actual_digest != str(descriptor.get("digest") or ""):
        raise SourceSnapshotError("source snapshot archive digest mismatch")
    entries: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = _safe_relative(info.filename)
                if name in entries:
                    raise SourceSnapshotError(
                        f"duplicate source snapshot archive entry: {name}"
                    )
                entries[name] = archive.read(info)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise SourceSnapshotError("source snapshot archive is invalid") from exc
    return entries


def _archive_tree_digest(
    entries: Mapping[str, bytes],
    prefix: str,
    *,
    excluded_dirs: frozenset[str] = frozenset(),
) -> tuple[str, set[str]]:
    normalized_prefix = _safe_relative(prefix) + "/"
    selected: list[tuple[str, str, bytes]] = []
    for name, payload in entries.items():
        if not name.startswith(normalized_prefix):
            continue
        relative = _safe_relative(name[len(normalized_prefix) :])
        relative_path = PurePosixPath(relative)
        if _ignored(relative_path, excluded_dirs=excluded_dirs):
            continue
        selected.append((relative, name, payload))
    selected.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    consumed: set[str] = set()
    for relative, name, payload in selected:
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        consumed.add(name)
    return "sha256:" + digest.hexdigest(), consumed


def _extract_archive_tree(
    entries: Mapping[str, bytes], prefix: str, target: Path
) -> None:
    normalized_prefix = _safe_relative(prefix) + "/"
    target.mkdir(parents=True, exist_ok=False)
    for name in sorted(entries):
        if not name.startswith(normalized_prefix):
            continue
        relative = _safe_relative(name[len(normalized_prefix) :])
        destination = target / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(entries[name])


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
        relative = _safe_relative(
            f"{str(kind).strip().lower().rstrip('s')}s/{artifact_id}"
        )
        row = {
            "kind": str(kind).strip().lower().rstrip("s"),
            "id": str(artifact_id).strip(),
            "path": relative,
            "digest": source_tree_digest(
                source,
                excluded_dirs=frozenset(_RESERVED_PROJECT_INPUT_DIRS),
            ),
            "source_projection": {
                "mode": "implementation_source",
                "excluded_paths": ["artifacts/"],
                "reason": "reserved_project_inputs_are_admitted_only_as_context_attachments",
            },
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
        archive_path = staged / _SNAPSHOT_ARCHIVE
        archive_digest = _write_snapshot_archive(
            archive_path,
            artifact_sources=artifact_sources,
            attachment_sources=attachment_sources,
        )
        manifest["archive"] = {
            "format": "zip",
            "path": _SNAPSHOT_ARCHIVE,
            "digest": archive_digest,
        }
        atomic_write_json(staged / "snapshot.json", manifest)
        verify_source_snapshot(
            state_dir=state_dir, reference=manifest, root_override=staged
        )
        try:
            replace_with_retry(staged, destination)
        except FileExistsError:
            shutil.rmtree(staged, ignore_errors=True)
        verify_source_snapshot(state_dir=state_dir, reference=manifest)
        return manifest
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)


def verify_source_snapshot(
    *,
    state_dir: Path,
    reference: Mapping[str, Any],
    root_override: Path | None = None,
) -> dict[str, Any]:
    snapshot_id = str(reference.get("snapshot_id") or "").strip()
    expected_digest = str(reference.get("digest") or "").strip()
    if len(snapshot_id) != 64 or any(
        char not in "0123456789abcdef" for char in snapshot_id
    ):
        raise SourceSnapshotError(
            "source snapshot id must be a lowercase SHA-256 hex digest"
        )
    if expected_digest != f"sha256:{snapshot_id}":
        raise SourceSnapshotError("source snapshot digest does not match snapshot id")
    root = (
        Path(root_override)
        if root_override is not None
        else _snapshot_root(Path(state_dir)) / snapshot_id
    )
    manifest_path = root / "snapshot.json"
    try:
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SourceSnapshotError(
            f"source snapshot manifest is unavailable: {snapshot_id}"
        ) from exc
    if (
        not isinstance(stored, Mapping)
        or stored.get("schema") != SOURCE_SNAPSHOT_SCHEMA
    ):
        raise SourceSnapshotError("invalid source snapshot manifest")
    if str(stored.get("digest") or "") != expected_digest:
        raise SourceSnapshotError(
            "stored source snapshot identity differs from task reference"
        )
    expected_archive = reference.get("archive")
    if expected_archive is not None and dict(expected_archive) != dict(
        stored.get("archive") or {}
    ):
        raise SourceSnapshotError(
            "stored source snapshot archive differs from task reference"
        )
    archive_descriptor = stored.get("archive")
    archive_entries = (
        _read_snapshot_archive(root, archive_descriptor)
        if isinstance(archive_descriptor, Mapping)
        else None
    )
    archive_consumed: set[str] = set()
    for group in ("artifacts", "attachments"):
        expected_rows = reference.get(group)
        if expected_rows is not None and list(expected_rows or []) != list(
            stored.get(group) or []
        ):
            raise SourceSnapshotError(
                f"stored source snapshot {group} differ from task reference"
            )
        for item in stored.get(group) or []:
            if not isinstance(item, Mapping):
                raise SourceSnapshotError(f"invalid source snapshot {group} entry")
            excluded_dirs = (
                source_projection_excluded_dirs(item)
                if group == "artifacts"
                else frozenset()
            )
            if archive_entries is not None:
                actual_digest, consumed = _archive_tree_digest(
                    archive_entries,
                    str(item.get("path") or ""),
                    excluded_dirs=excluded_dirs,
                )
                archive_consumed.update(consumed)
            else:
                path = root / Path(_safe_relative(item.get("path")))
                actual_digest = source_tree_digest(path, excluded_dirs=excluded_dirs)
            if actual_digest != str(item.get("digest") or ""):
                raise SourceSnapshotError(
                    f"source snapshot content mismatch: {item.get('path')}"
                )
    if archive_entries is not None and archive_consumed != set(archive_entries):
        unexpected = sorted(set(archive_entries) - archive_consumed)
        raise SourceSnapshotError(
            "source snapshot archive has unbound entries: " + ", ".join(unexpected[:10])
        )
    return dict(stored)


def materialize_source_snapshot(
    *,
    state_dir: Path,
    reference: Mapping[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    manifest = verify_source_snapshot(state_dir=state_dir, reference=reference)
    snapshot_root = _snapshot_root(Path(state_dir)) / str(manifest["snapshot_id"])
    archive_descriptor = manifest.get("archive")
    archive_entries = (
        _read_snapshot_archive(snapshot_root, archive_descriptor)
        if isinstance(archive_descriptor, Mapping)
        else None
    )
    for item in manifest.get("artifacts") or []:
        relative = _safe_relative(item.get("path"))
        if archive_entries is not None:
            _extract_archive_tree(
                archive_entries, relative, Path(workspace) / Path(relative)
            )
        else:
            _copy_source_tree(
                snapshot_root / Path(relative),
                Path(workspace) / Path(relative),
                excluded_dirs=source_projection_excluded_dirs(item),
            )
    for item in manifest.get("attachments") or []:
        relative = _safe_relative(item.get("path"))
        target = Path(workspace) / Path(_safe_relative(item.get("target_path")))
        if target.exists():
            shutil.rmtree(target)
        if archive_entries is not None:
            _extract_archive_tree(archive_entries, relative, target)
        else:
            _copy_source_tree(snapshot_root / Path(relative), target)
    return manifest


__all__ = [
    "SOURCE_SNAPSHOT_SCHEMA",
    "SourceSnapshotError",
    "capture_source_snapshot",
    "materialize_source_snapshot",
    "source_projection_excluded_dirs",
    "source_tree_digest",
    "verify_source_snapshot",
]
