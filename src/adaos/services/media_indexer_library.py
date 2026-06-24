from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from adaos.services.agent_context import get_ctx


MEDIA_INDEXER_SKILL_NAME = "media_indexer_skill"
MEDIA_INDEXER_METADATA_REL = Path("data") / "internal" / "media_indexer" / "faiss" / "metadata.json"
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

    indexed_root = Path(str(metadata.get("indexed_directory") or "")).expanduser()
    if not indexed_root.exists() or not indexed_root.is_dir():
        raise FileNotFoundError("media_indexer_directory_missing")
    indexed_root = indexed_root.resolve()

    for payload in _iter_payloads(metadata):
        if str(payload.get("playback_id") or "").strip().lower() != normalized:
            continue
        raw_path = str(payload.get("full_path") or "").strip()
        if not raw_path:
            continue
        target = Path(raw_path).expanduser().resolve()
        if target.suffix.lower() not in SUPPORTED_INDEXER_MEDIA_EXTENSIONS:
            raise ValueError("unsupported_extension")
        if not _is_relative_to(target, indexed_root):
            raise PermissionError("path_outside_indexed_directory")
        if not target.exists() or not target.is_file():
            raise FileNotFoundError("media_file_not_found")
        return target, payload

    raise FileNotFoundError("media_indexer_item_not_found")


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
    try:
        skills_root_raw = get_ctx().paths.skills_workspace_dir()
        skills_root = Path(skills_root_raw() if callable(skills_root_raw) else skills_root_raw)
        runtime_root = skills_root / ".runtime" / MEDIA_INDEXER_SKILL_NAME
        if runtime_root.exists():
            paths.extend(runtime_root.glob(f"*/{MEDIA_INDEXER_METADATA_REL.as_posix()}"))
        workspace_data = skills_root / MEDIA_INDEXER_SKILL_NAME / "data" / "internal" / "media_indexer" / "faiss" / "metadata.json"
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
