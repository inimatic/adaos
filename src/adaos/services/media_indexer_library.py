from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from adaos.services.agent_context import get_ctx


MEDIA_INDEXER_SKILL_NAME = "media_indexer_skill"
MEDIA_INDEXER_STATE_METADATA_REL = Path("internal") / "faiss" / "metadata.json"
MEDIA_INDEXER_WORKSPACE_METADATA_REL = Path("data") / "internal" / "media_indexer" / "faiss" / "metadata.json"
SUPPORTED_INDEXER_MEDIA_EXTENSIONS = {
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
_MIME_OVERRIDES = {
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
}


def guess_indexer_media_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[suffix]
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def resolve_media_indexer_content(playback_id: str) -> tuple[Path, dict[str, Any]]:
    normalized = str(playback_id or "").strip().lower()
    if not normalized or len(normalized) > 128 or not all(ch in "0123456789abcdef" for ch in normalized):
        raise ValueError("invalid_playback_id")

    metadata = _latest_index_metadata()
    if not metadata:
        raise FileNotFoundError("media_indexer_index_missing")

    for payload in _iter_payloads(metadata):
        if str(payload.get("playback_id") or "").strip().lower() != normalized:
            continue
        roots = _candidate_index_roots(metadata, payload)
        if not roots:
            raise FileNotFoundError("media_indexer_directory_missing")
        return _resolve_payload_target(payload, roots)

    raise FileNotFoundError("media_indexer_item_not_found")


def resolve_media_indexer_content_by_name(filename: str) -> tuple[Path, dict[str, Any]]:
    raw = str(filename or "").strip()
    name = Path(raw).name
    if not name or name != raw or name in {".", ".."} or "\x00" in name or "/" in raw or "\\" in raw:
        raise ValueError("invalid_filename")

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
        return _resolve_payload_target(payload, roots)

    raise FileNotFoundError("media_indexer_item_not_found")


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


def _resolve_payload_target(payload: dict[str, Any], indexed_roots: list[Path]) -> tuple[Path, dict[str, Any]]:
    raw_path = str(payload.get("full_path") or "").strip()
    if not raw_path:
        raise FileNotFoundError("media_indexer_item_missing_path")
    target = Path(raw_path).expanduser().resolve()
    if target.suffix.lower() not in SUPPORTED_INDEXER_MEDIA_EXTENSIONS:
        raise ValueError("unsupported_extension")
    if not any(_is_relative_to(target, root) for root in indexed_roots):
        raise PermissionError("path_outside_indexed_directory")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("media_file_not_found")
    return target, payload


def _latest_index_metadata() -> dict[str, Any]:
    candidates = [path for path in _metadata_candidates() if path.exists()]
    if not candidates:
        return {}
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
