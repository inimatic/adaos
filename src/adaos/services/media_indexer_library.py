from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from adaos.services.agent_context import get_ctx
from adaos.services.media_core import (
    SUPPORTED_MEDIA_EXTENSIONS,
    MediaResource,
    guess_media_type,
    media_indexer_content_path,
    media_resource_from_path,
    media_store_content_path,
    resolve_external_media_payload_target,
    validate_playback_id,
)


MEDIA_INDEXER_SKILL_NAME = "media_indexer_skill"
MEDIA_INDEXER_STATE_METADATA_REL = Path("internal") / "faiss" / "metadata.json"
MEDIA_INDEXER_WORKSPACE_METADATA_REL = Path("data") / "internal" / "media_indexer" / "faiss" / "metadata.json"
MEDIA_INDEXER_PLAYBACK_INDEX = "playback.sqlite3"
SUPPORTED_INDEXER_MEDIA_EXTENSIONS = {
    *SUPPORTED_MEDIA_EXTENSIONS,
}
_METADATA_CACHE: dict[str, Any] = {"key": None, "value": {}}


def guess_indexer_media_type(filename: str) -> str:
    return guess_media_type(filename)


def resolve_media_indexer_resource(playback_id: str) -> MediaResource:
    normalized = validate_playback_id(playback_id)

    indexed, sidecar_available = _lookup_playback_index(playback_id=normalized)
    if indexed is not None:
        payload, metadata = indexed
        roots = _candidate_index_roots(metadata, payload)
        if not roots:
            raise FileNotFoundError("media_indexer_directory_missing")
        return _resource_from_payload(payload, metadata, roots, playback_id_hint=normalized)
    if sidecar_available:
        raise FileNotFoundError("media_indexer_item_not_found")

    metadata = _latest_index_metadata()
    if not metadata:
        raise FileNotFoundError("media_indexer_index_missing")

    for payload in _iter_payloads(metadata):
        if str(payload.get("playback_id") or "").strip().lower() != normalized:
            continue
        roots = _candidate_index_roots(metadata, payload)
        if not roots:
            raise FileNotFoundError("media_indexer_directory_missing")
        return _resource_from_payload(payload, metadata, roots, playback_id_hint=normalized)

    raise FileNotFoundError("media_indexer_item_not_found")


def resolve_media_indexer_content(playback_id: str) -> tuple[Path, dict[str, Any]]:
    resource = resolve_media_indexer_resource(playback_id)
    return resource.path, _resource_payload(resource)


def resolve_media_indexer_resource_by_name(filename: str) -> MediaResource:
    raw = str(filename or "").strip()
    name = Path(raw).name
    if not name or name != raw or name in {".", ".."} or "\x00" in name or "/" in raw or "\\" in raw:
        raise ValueError("invalid_filename")

    indexed, sidecar_available = _lookup_playback_index(filename=name)
    if indexed is not None:
        payload, metadata = indexed
        roots = _candidate_index_roots(metadata, payload)
        if not roots:
            raise FileNotFoundError("media_indexer_directory_missing")
        return _resource_from_payload(payload, metadata, roots)
    if sidecar_available:
        raise FileNotFoundError("media_indexer_item_not_found")

    metadata = _latest_index_metadata()
    if not metadata:
        raise FileNotFoundError("media_indexer_index_missing")

    for payload in _iter_payloads(metadata):
        raw_path = str(payload.get("full_path") or "").strip()
        payload_names = {
            str(payload.get("real_file_name") or "").strip(),
            Path(raw_path).name if raw_path else "",
        }
        if name not in payload_names:
            continue
        roots = _candidate_index_roots(metadata, payload)
        if not roots:
            raise FileNotFoundError("media_indexer_directory_missing")
        return _resource_from_payload(payload, metadata, roots)

    raise FileNotFoundError("media_indexer_item_not_found")


def resolve_media_indexer_content_by_name(filename: str) -> tuple[Path, dict[str, Any]]:
    resource = resolve_media_indexer_resource_by_name(filename)
    return resource.path, _resource_payload(resource)


def iter_media_indexer_resources(*, limit: int | None = None) -> Iterator[MediaResource]:
    """Yield legacy media-indexer entries as normalized media resources.

    This is a compatibility adapter for cataloging skills. It preserves the
    resolver semantics used by playback routes while hiding legacy
    ``metadata.json`` / ``playback.sqlite3`` details behind ``MediaResource``.
    """

    max_items = int(limit) if limit is not None else None
    if max_items is not None and max_items <= 0:
        return

    seen: set[tuple[str, str]] = set()
    yielded = 0

    for payload, metadata in _iter_playback_index_payloads():
        try:
            roots = _candidate_index_roots(metadata, payload)
            if not roots:
                continue
            resource = _resource_from_payload(payload, metadata, roots)
        except (FileNotFoundError, OSError, PermissionError, ValueError):
            continue
        key = (resource.source, resource.id)
        if key in seen:
            continue
        seen.add(key)
        yield resource
        yielded += 1
        if max_items is not None and yielded >= max_items:
            return

    metadata = _latest_index_metadata()
    if not metadata:
        return
    for payload in _iter_payloads(metadata):
        try:
            roots = _candidate_index_roots(metadata, payload)
            if not roots:
                continue
            resource = _resource_from_payload(payload, metadata, roots)
        except (FileNotFoundError, OSError, PermissionError, ValueError):
            continue
        key = (resource.source, resource.id)
        if key in seen:
            continue
        seen.add(key)
        yield resource
        yielded += 1
        if max_items is not None and yielded >= max_items:
            return


def _candidate_index_roots(metadata: dict[str, Any], payload: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    indexed_root = Path(str(metadata.get("indexed_directory") or "")).expanduser()
    if indexed_root.exists() and indexed_root.is_dir():
        roots.append(indexed_root.resolve())

    alias_root = _payload_alias_root(str(metadata.get("indexed_directory") or ""), str(payload.get("full_path") or ""))
    if alias_root and alias_root.exists() and alias_root.is_dir():
        resolved = alias_root.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _payload_alias_root(indexed_directory: str, payload_path: str) -> Path | None:
    indexed_raw = str(indexed_directory or "").strip()
    payload_raw = str(payload_path or "").strip()
    if not indexed_raw or not payload_raw:
        return None
    indexed = Path(indexed_raw).expanduser()
    payload_parent = Path(payload_raw).expanduser().parent
    indexed_tail = _path_tail_parts(indexed)
    payload_parts = list(payload_parent.parts)
    payload_norm = [_norm_part(part) for part in payload_parts]
    if len(indexed_tail) < 2:
        return None
    tail_len = len(indexed_tail)
    for start in range(0, len(payload_norm) - tail_len + 1):
        if payload_norm[start : start + tail_len] == indexed_tail:
            return Path(*payload_parts[: start + tail_len])
    return None


def _path_tail_parts(path: Path) -> list[str]:
    anchor = _norm_part(path.anchor)
    parts: list[str] = []
    for part in path.parts:
        normalized = _norm_part(part)
        if not normalized or normalized == anchor:
            continue
        if not parts and len(normalized) == 2 and normalized[1] == ":" and normalized[0].isalpha():
            continue
        parts.append(normalized)
    return parts


def _norm_part(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().casefold()


def _resource_from_payload(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    indexed_roots: list[Path],
    *,
    playback_id_hint: str | None = None,
) -> MediaResource:
    try:
        target = resolve_external_media_payload_target(payload, indexed_roots)
    except FileNotFoundError as exc:
        if str(exc) == "media_item_missing_path":
            raise FileNotFoundError("media_indexer_item_missing_path") from exc
        raise
    raw_playback_id = str(payload.get("playback_id") or playback_id_hint or "").strip().lower()
    playback_id = validate_playback_id(raw_playback_id) if raw_playback_id else ""
    name = str(payload.get("real_file_name") or "").strip() or target.name
    payload_mime = str(payload.get("mime_type") or "").strip()
    node_content_path = (
        media_indexer_content_path(playback_id, browser=False)
        if playback_id
        else media_store_content_path(name, browser=False)
    )
    browser_content_path = (
        media_indexer_content_path(playback_id, browser=True)
        if playback_id
        else media_store_content_path(name, browser=True)
    )
    return media_resource_from_path(
        target,
        source="media_indexer",
        resource_id=playback_id or name,
        name=name,
        mime_type=payload_mime or guess_indexer_media_type(name),
        playback_id=playback_id,
        content_path=node_content_path,
        routed_content_path=browser_content_path,
        source_path=str(payload.get("full_path") or target),
        metadata={
            "payload": dict(payload),
            "indexed_directory": str(metadata.get("indexed_directory") or ""),
            "provider": MEDIA_INDEXER_SKILL_NAME,
        },
    )


def _resource_payload(resource: MediaResource) -> dict[str, Any]:
    raw_payload = resource.metadata.get("payload") if isinstance(resource.metadata, dict) else {}
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    payload.setdefault("playback_id", resource.playback_id)
    payload.setdefault("real_file_name", resource.name)
    payload.setdefault("full_path", str(resource.path))
    payload.setdefault("mime_type", resource.mime_type)
    payload.setdefault("content_path", resource.content_path)
    payload.setdefault("routed_content_path", resource.routed_content_path)
    payload.setdefault("source", resource.source)
    return payload


def _latest_index_metadata() -> dict[str, Any]:
    candidates = [path for path in _metadata_candidates() if path.exists()]
    if not candidates:
        return {}
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        stat = path.stat()
        cache_key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
        if _METADATA_CACHE.get("key") == cache_key:
            return dict(_METADATA_CACHE.get("value") or {})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            _METADATA_CACHE["key"] = cache_key
            _METADATA_CACHE["value"] = data
            return data
    return {}


def _lookup_playback_index(
    *,
    playback_id: str | None = None,
    filename: str | None = None,
) -> tuple[tuple[dict[str, Any], dict[str, Any]] | None, bool]:
    sidecar_available = False
    for metadata_path in _metadata_candidates():
        path = metadata_path.with_name(MEDIA_INDEXER_PLAYBACK_INDEX)
        if not path.exists():
            continue
        sidecar_available = True
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1.0)
            try:
                if playback_id:
                    row = connection.execute(
                        "SELECT payload_json FROM items WHERE playback_id = ? LIMIT 1",
                        (playback_id,),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT payload_json FROM items WHERE name = ? LIMIT 1",
                        (str(filename or ""),),
                    ).fetchone()
                if not row:
                    continue
                root_row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'indexed_directory' LIMIT 1"
                ).fetchone()
                payload = json.loads(str(row[0] or "{}"))
                if not isinstance(payload, dict):
                    continue
                metadata = {"indexed_directory": str(root_row[0] if root_row else "")}
                return (payload, metadata), sidecar_available
            finally:
                connection.close()
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None, sidecar_available


def _iter_playback_index_payloads() -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for metadata_path in _metadata_candidates():
        path = metadata_path.with_name(MEDIA_INDEXER_PLAYBACK_INDEX)
        if not path.exists():
            continue
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1.0)
            try:
                root_row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'indexed_directory' LIMIT 1"
                ).fetchone()
                metadata = {"indexed_directory": str(root_row[0] if root_row else "")}
                try:
                    rows = connection.execute("SELECT payload_json FROM items ORDER BY name").fetchall()
                except sqlite3.Error:
                    rows = connection.execute("SELECT payload_json FROM items").fetchall()
                for row in rows:
                    payload = json.loads(str(row[0] or "{}"))
                    if isinstance(payload, dict):
                        yield payload, metadata
            finally:
                connection.close()
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            continue


def _metadata_candidates() -> list[Path]:
    paths: list[Path] = []
    env_data_dir = str(os.getenv("MEDIA_INDEXER_DATA_DIR") or "").strip()
    if env_data_dir:
        paths.append(Path(env_data_dir).expanduser() / MEDIA_INDEXER_STATE_METADATA_REL)
    base_dir_env = str(os.getenv("ADAOS_BASE_DIR") or "").strip()
    if base_dir_env:
        paths.append(Path(base_dir_env).expanduser() / "state" / MEDIA_INDEXER_SKILL_NAME / MEDIA_INDEXER_STATE_METADATA_REL)
    try:
        base_dir_raw = get_ctx().paths.base_dir()
        base_dir = Path(base_dir_raw() if callable(base_dir_raw) else base_dir_raw)
        paths.append(base_dir / "state" / MEDIA_INDEXER_SKILL_NAME / MEDIA_INDEXER_STATE_METADATA_REL)
    except Exception:
        pass
    try:
        skills_root_raw = get_ctx().paths.skills_workspace_dir()
        skills_root = Path(skills_root_raw() if callable(skills_root_raw) else skills_root_raw)
        runtime_root = skills_root / ".runtime" / MEDIA_INDEXER_SKILL_NAME
        if runtime_root.exists():
            paths.extend(runtime_root.glob(f"*/{MEDIA_INDEXER_WORKSPACE_METADATA_REL.as_posix()}"))
            paths.extend(runtime_root.glob(f"*/{MEDIA_INDEXER_STATE_METADATA_REL.as_posix()}"))
        workspace_data = skills_root / MEDIA_INDEXER_SKILL_NAME / MEDIA_INDEXER_WORKSPACE_METADATA_REL
        paths.append(workspace_data)
    except Exception:
        pass
    return list(dict.fromkeys(paths))


def _iter_payloads(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for doc_key in ("text_docs", "image_docs"):
        docs = metadata.get(doc_key)
        if not isinstance(docs, list):
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            payload = doc.get("payload")
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads
