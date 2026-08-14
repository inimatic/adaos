from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

from adaos.services.agent_context import get_ctx
from adaos.services.runtime_paths import current_base_dir
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


MEDIA_RESOURCE_SCHEMA = "adaos.media.resource.v1"
MEDIA_REFERENCE_SCHEMA = "adaos.media.reference.v1"
MEDIA_STORE_SKILL_NAME = "mediaserver"
MEDIA_STORAGE_SUBPATH = "data/files"
MEDIA_RUNTIME_SCOPE = "media_server"
MEDIA_REFERENCE_DB_ENV = "ADAOS_MEDIA_REFERENCE_DB_PATH"
ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES = 2 * 1024 * 1024
ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
# Keep chunks below the default 1 MiB NATS payload limit after base64/json overhead.
ROOT_MEDIA_RELAY_CHUNK_BYTES = 512 * 1024

SUPPORTED_MEDIA_EXTENSIONS = {
    ".mp4",
    ".webm",
    ".ogv",
    ".ogg",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".wmv",
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".opus",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
}

_MEDIA_TYPE_OVERRIDES = {
    ".mkv": "video/x-matroska",
    ".m4v": "video/mp4",
    ".ogv": "video/ogg",
    ".wmv": "video/x-ms-wmv",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class MediaResource:
    id: str
    source: str
    name: str
    path: Path
    mime_type: str
    size_bytes: int
    modified_at: str
    content_path: str
    routed_content_path: str = ""
    playback_id: str = ""
    source_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    modified_ts: float = 0.0

    def to_public_dict(self, *, include_internal: bool = False) -> dict[str, Any]:
        payload = media_resource_descriptor(
            resource_id=self.id,
            source=self.source,
            name=self.name,
            mime_type=self.mime_type,
            size_bytes=self.size_bytes,
            modified_at=self.modified_at,
            content_path=self.content_path,
            routed_content_path=self.routed_content_path,
            playback_id=self.playback_id,
            source_path=self.source_path,
            metadata=self.metadata,
        )
        if include_internal:
            payload["_modified_ts"] = float(self.modified_ts or 0.0)
        return payload


def media_store_runtime_env() -> SkillRuntimeEnvironment:
    ctx = get_ctx()
    env = SkillRuntimeEnvironment(
        skills_root=Path(ctx.paths.skills_dir()),
        skill_name=MEDIA_STORE_SKILL_NAME,
    )
    env.ensure_base()
    return env


def media_store_dir() -> Path:
    path = media_store_runtime_env().files_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_media_filename(filename: str) -> str:
    raw = str(filename or "").strip()
    if not raw:
        raise ValueError("empty_filename")
    if "\x00" in raw:
        raise ValueError("invalid_filename")
    if "/" in raw or "\\" in raw:
        raise ValueError("path_separators_not_allowed")
    if raw in {".", ".."}:
        raise ValueError("invalid_filename")
    name = Path(raw).name
    if name != raw:
        raise ValueError("path_traversal_not_allowed")
    suffix = Path(name).suffix.lower()
    if not suffix:
        raise ValueError("missing_extension")
    if suffix not in SUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError(f"unsupported_extension:{suffix}")
    return name


def media_store_file_path(filename: str) -> Path:
    return media_store_dir() / sanitize_media_filename(filename)


def guess_media_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in _MEDIA_TYPE_OVERRIDES:
        return _MEDIA_TYPE_OVERRIDES[suffix]
    guessed, _enc = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def validate_playback_id(playback_id: str) -> str:
    normalized = str(playback_id or "").strip().lower()
    if not normalized or len(normalized) > 128 or not all(ch in "0123456789abcdef" for ch in normalized):
        raise ValueError("invalid_playback_id")
    return normalized


def media_store_content_path(filename: str, *, browser: bool = True) -> str:
    name = sanitize_media_filename(filename)
    prefix = "/media" if browser else "/api/node/media"
    return f"{prefix}/files/content/{quote(name)}"


def media_indexer_content_path(playback_id: str, *, browser: bool = True) -> str:
    normalized = validate_playback_id(playback_id)
    prefix = "/media" if browser else "/api/node"
    return f"{prefix}/media-indexer/content/{quote(normalized)}"


def validate_media_reference_id(resource_id: str) -> str:
    normalized = str(resource_id or "").strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("invalid_media_reference_id")
    if not all(ch.isalnum() or ch in {"-", "_"} for ch in normalized):
        raise ValueError("invalid_media_reference_id")
    return normalized


def media_reference_content_path(resource_id: str, *, browser: bool = True) -> str:
    normalized = validate_media_reference_id(resource_id)
    prefix = "/media" if browser else "/api/node/media"
    return f"{prefix}/resources/content/{quote(normalized)}"


def media_resource_content_path(
    resource_id: str,
    *,
    source: str = "media_server",
    browser: bool = True,
) -> str:
    source_norm = str(source or "").strip().lower().replace("-", "_")
    if source_norm in {"media", "media_file", "media_store", "media_server", "mediaserver"}:
        return media_store_content_path(resource_id, browser=browser)
    if source_norm in {"media_indexer", "media_indexer_skill"}:
        return media_indexer_content_path(resource_id, browser=browser)
    if source_norm in {"media_reference", "media_resource", "external_media"}:
        return media_reference_content_path(resource_id, browser=browser)
    raise ValueError(f"unsupported_media_source:{source_norm or 'unknown'}")


def media_resource_descriptor(
    *,
    resource_id: str,
    source: str,
    name: str,
    mime_type: str,
    size_bytes: int,
    modified_at: str = "",
    content_path: str = "",
    routed_content_path: str = "",
    playback_id: str = "",
    source_path: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    descriptor = {
        "schema": MEDIA_RESOURCE_SCHEMA,
        "id": str(resource_id or "").strip(),
        "resource_id": str(resource_id or "").strip(),
        "source": str(source or "").strip() or "media",
        "name": str(name or "").strip(),
        "size_bytes": int(size_bytes or 0),
        "mime_type": str(mime_type or "").strip() or "application/octet-stream",
        "modified_at": str(modified_at or "").strip(),
        "content_path": str(content_path or "").strip(),
    }
    if routed_content_path:
        descriptor["routed_content_path"] = str(routed_content_path).strip()
    if playback_id:
        descriptor["playback_id"] = str(playback_id).strip()
    if source_path:
        descriptor["source_path"] = str(source_path).strip()
    if metadata:
        descriptor["metadata"] = dict(metadata)
    return descriptor


def _modified_at_iso(stat: Any) -> str:
    return datetime.fromtimestamp(float(stat.st_mtime), tz=timezone.utc).isoformat()


def media_resource_from_path(
    path: str | Path,
    *,
    source: str = "media_server",
    resource_id: str | None = None,
    name: str | None = None,
    mime_type: str | None = None,
    content_path: str | None = None,
    routed_content_path: str | None = None,
    playback_id: str | None = None,
    source_path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> MediaResource:
    target = Path(path).expanduser().resolve()
    stat = target.stat()
    media_name = str(name or target.name)
    playback_token = str(playback_id or "").strip()
    media_id = str(resource_id or playback_token or media_name).strip()
    source_norm = str(source or "media_server").strip() or "media_server"
    default_content = content_path
    default_routed = routed_content_path
    if default_content is None:
        if source_norm in {"media_indexer", "media_indexer_skill"} and playback_token:
            default_content = media_indexer_content_path(playback_token, browser=False)
        else:
            default_content = media_store_content_path(media_name, browser=False)
    if default_routed is None:
        if source_norm in {"media_indexer", "media_indexer_skill"} and playback_token:
            default_routed = media_indexer_content_path(playback_token, browser=True)
        else:
            default_routed = media_store_content_path(media_name, browser=True)
    return MediaResource(
        id=media_id,
        source=source_norm,
        name=media_name,
        path=target,
        mime_type=str(mime_type or "").strip() or guess_media_type(media_name),
        size_bytes=int(stat.st_size),
        modified_at=_modified_at_iso(stat),
        content_path=str(default_content or ""),
        routed_content_path=str(default_routed or ""),
        playback_id=playback_token,
        source_path=str(source_path or target),
        metadata=dict(metadata or {}),
        modified_ts=float(stat.st_mtime),
    )


def media_store_resource(filename: str) -> MediaResource:
    return media_resource_from_path(
        media_store_file_path(filename),
        source="media_server",
        resource_id=sanitize_media_filename(filename),
    )


def iter_media_store_resources(root: str | Path | None = None) -> Iterator[MediaResource]:
    directory = Path(root).expanduser() if root is not None else media_store_dir()
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            continue
        try:
            yield media_resource_from_path(path, source="media_server", resource_id=path.name)
        except OSError:
            continue


def media_reference_db_path() -> Path:
    override = str(os.getenv(MEDIA_REFERENCE_DB_ENV) or "").strip()
    path = Path(override).expanduser() if override else current_base_dir() / "state" / "media_references.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _media_reference_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else media_reference_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS media_references (
            resource_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            root_path TEXT NOT NULL,
            name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_at TEXT NOT NULL,
            modified_ns INTEGER NOT NULL,
            metadata_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def _resolve_media_reference_target(path: str | Path, root: str | Path) -> tuple[Path, Path]:
    root_path = Path(root).expanduser().resolve(strict=True)
    if not root_path.is_dir():
        raise NotADirectoryError("media_reference_root_not_directory")
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file():
        raise FileNotFoundError("media_reference_file_not_found")
    if target.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError("unsupported_extension")
    if not _is_relative_to(target, root_path):
        raise PermissionError("path_outside_media_reference_root")
    return target, root_path


def register_media_reference(
    path: str | Path,
    *,
    root: str | Path,
    content_ref: str = "",
    namespace: str = "media",
    mime_type: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> MediaResource:
    target, root_path = _resolve_media_reference_target(path, root)
    stat = target.stat()
    namespace_token = str(namespace or "media").strip() or "media"
    identity = str(content_ref or target)
    resource_id = "ref_" + hashlib.sha256(
        f"{namespace_token}\0{identity}".encode("utf-8", errors="replace")
    ).hexdigest()[:40]
    detected_mime = str(mime_type or "").strip() or guess_media_type(target.name)
    modified_at = _modified_at_iso(stat)
    reference_metadata = {
        **dict(metadata or {}),
        "reference_schema": MEDIA_REFERENCE_SCHEMA,
        "storage_mode": "reference",
        "namespace": namespace_token,
        "content_ref": identity,
    }
    updated_at = datetime.now(tz=timezone.utc).isoformat()
    with _media_reference_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO media_references (
                resource_id, source_path, root_path, name, mime_type,
                size_bytes, modified_at, modified_ns, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                source_path = excluded.source_path,
                root_path = excluded.root_path,
                name = excluded.name,
                mime_type = excluded.mime_type,
                size_bytes = excluded.size_bytes,
                modified_at = excluded.modified_at,
                modified_ns = excluded.modified_ns,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                resource_id,
                str(target),
                str(root_path),
                target.name,
                detected_mime,
                int(stat.st_size),
                modified_at,
                int(stat.st_mtime_ns),
                json.dumps(reference_metadata, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                updated_at,
            ),
        )
        connection.commit()
    return MediaResource(
        id=resource_id,
        source="media_server",
        name=target.name,
        path=target,
        mime_type=detected_mime,
        size_bytes=int(stat.st_size),
        modified_at=modified_at,
        content_path=media_reference_content_path(resource_id, browser=False),
        routed_content_path=media_reference_content_path(resource_id, browser=True),
        source_path=str(target),
        metadata=reference_metadata,
        modified_ts=float(stat.st_mtime),
    )


def resolve_media_reference(
    resource_id: str,
    *,
    db_path: str | Path | None = None,
) -> MediaResource:
    normalized = validate_media_reference_id(resource_id)
    with _media_reference_connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM media_references WHERE resource_id = ?",
            (normalized,),
        ).fetchone()
    if row is None:
        raise FileNotFoundError("media_reference_not_found")
    target, _root_path = _resolve_media_reference_target(row["source_path"], row["root_path"])
    stat = target.stat()
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
    except Exception:
        metadata = {}
    return MediaResource(
        id=normalized,
        source="media_server",
        name=str(row["name"] or target.name),
        path=target,
        mime_type=str(row["mime_type"] or "") or guess_media_type(target.name),
        size_bytes=int(stat.st_size),
        modified_at=_modified_at_iso(stat),
        content_path=media_reference_content_path(normalized, browser=False),
        routed_content_path=media_reference_content_path(normalized, browser=True),
        source_path=str(target),
        metadata=metadata if isinstance(metadata, dict) else {},
        modified_ts=float(stat.st_mtime),
    )


def iter_media_reference_resources(db_path: str | Path | None = None) -> Iterator[MediaResource]:
    with _media_reference_connection(db_path) as connection:
        rows = connection.execute("SELECT resource_id FROM media_references ORDER BY updated_at DESC").fetchall()
    for row in rows:
        try:
            yield resolve_media_reference(str(row["resource_id"]), db_path=db_path)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError, OSError):
            continue


def parse_media_range(raw: str | None, *, size: int) -> tuple[int, int] | None:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported_range")
    spec = value[6:].strip()
    start_raw, sep, end_raw = spec.partition("-")
    if not sep:
        raise ValueError("invalid_range")
    if start_raw == "":
        suffix = int(end_raw)
        if suffix <= 0:
            raise ValueError("invalid_range")
        start = max(0, int(size) - suffix)
        end = int(size) - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else int(size) - 1
    if int(size) <= 0 or start < 0 or end < start or start >= int(size):
        raise ValueError("invalid_range")
    return start, min(end, int(size) - 1)


def inline_content_disposition(filename: str) -> str:
    safe = str(filename or "media").replace("\\", "_").replace("/", "_")
    safe = safe.replace('"', "'").replace("\r", "").replace("\n", "").strip() or "media"
    return f'inline; filename="{safe}"'


def media_content_response_parts(
    *,
    filename: str,
    mime_type: str,
    size: int,
    byte_range: tuple[int, int] | None = None,
    include_content_type: bool = True,
    lower_case_headers: bool = False,
) -> tuple[int, str, dict[str, str], int, int]:
    start = 0
    end = max(0, int(size) - 1)
    status = 200
    reason = "OK"
    if byte_range is not None:
        start, end = byte_range
        status = 206
        reason = "Partial Content"
    length = max(0, end - start + 1) if int(size) > 0 else 0
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "Content-Length": str(length),
        "Content-Disposition": inline_content_disposition(filename),
    }
    if include_content_type:
        headers["Content-Type"] = str(mime_type or "application/octet-stream")
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{int(size)}"
    if lower_case_headers:
        headers = {key.lower(): value for key, value in headers.items()}
    return status, reason, headers, start, end


def file_range_iter(path: str | Path, *, start: int, end: int, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
    with Path(path).open("rb") as handle:
        handle.seek(int(start))
        remaining = max(0, int(end) - int(start) + 1)
        while remaining > 0:
            chunk = handle.read(min(int(chunk_size), remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def resolve_external_media_payload_target(
    payload: dict[str, Any],
    indexed_roots: list[Path],
    *,
    path_key: str = "full_path",
) -> Path:
    raw_path = str(payload.get(path_key) or "").strip()
    if not raw_path:
        raise FileNotFoundError("media_item_missing_path")
    target = Path(raw_path).expanduser().resolve()
    if target.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError("unsupported_extension")
    if not any(_is_relative_to(target, root.resolve()) for root in indexed_roots):
        raise PermissionError("path_outside_indexed_directory")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("media_file_not_found")
    return target


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = [
    "MEDIA_REFERENCE_DB_ENV",
    "MEDIA_REFERENCE_SCHEMA",
    "MEDIA_RESOURCE_SCHEMA",
    "MEDIA_RUNTIME_SCOPE",
    "MEDIA_STORAGE_SUBPATH",
    "MEDIA_STORE_SKILL_NAME",
    "ROOT_MEDIA_RELAY_CHUNK_BYTES",
    "ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES",
    "ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES",
    "SUPPORTED_MEDIA_EXTENSIONS",
    "MediaResource",
    "file_range_iter",
    "guess_media_type",
    "inline_content_disposition",
    "iter_media_reference_resources",
    "iter_media_store_resources",
    "media_content_response_parts",
    "media_indexer_content_path",
    "media_reference_content_path",
    "media_reference_db_path",
    "media_resource_content_path",
    "media_resource_descriptor",
    "media_resource_from_path",
    "media_store_content_path",
    "media_store_dir",
    "media_store_file_path",
    "media_store_resource",
    "media_store_runtime_env",
    "parse_media_range",
    "register_media_reference",
    "resolve_media_reference",
    "resolve_external_media_payload_target",
    "sanitize_media_filename",
    "validate_playback_id",
    "validate_media_reference_id",
]
