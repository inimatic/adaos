"""Reusable media helpers for browser-facing skill surfaces."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from adaos.services.agent_context import get_ctx
from adaos.services.media_library import media_file_path


def image_fingerprint(path: str | Path) -> str:
    source = Path(path)
    stat = source.stat()
    raw = f"{source.resolve()}:{stat.st_size}:{int(stat.st_mtime)}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


def source_image_cache_dir(path: str | Path, *, fallback_dir: str | Path | None = None) -> Path:
    source = Path(path)
    target = source.parent / ".adaos-thumbs"
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception:
        if fallback_dir is None:
            raise
        fallback = Path(fallback_dir)
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def cached_image_variant(
    path: str | Path,
    *,
    max_size: tuple[int, int],
    label: str,
    quality: int = 80,
    background: str = "black",
    fallback_dir: str | Path | None = None,
) -> tuple[Path, bool]:
    source = Path(path)
    safe_label = "".join(ch for ch in str(label or "").lower() if ch.isalnum() or ch in {"-", "_"}) or "image"
    cache_path = source_image_cache_dir(source, fallback_dir=fallback_dir) / f"{image_fingerprint(source)}-{safe_label}.jpg"
    if cache_path.exists():
        return cache_path, True

    from PIL import Image, ImageOps  # type: ignore

    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if getattr(image, "is_animated", False):
            image.seek(0)
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", image.size, background)
        if image.mode in {"RGBA", "LA"}:
            canvas.paste(image, mask=image.getchannel("A"))
        else:
            canvas.paste(image.convert("RGB"))
        canvas.save(cache_path, "JPEG", quality=max(40, min(95, int(quality))), optimize=True)
    return cache_path, False


def media_content_url(filename: str, *, api_token: str | None = None, browser: bool = False) -> str:
    token = str(api_token or _api_token() or "").strip()
    query = f"?token={quote(token)}" if token else ""
    prefix = "/media" if browser else "/api/node/media"
    return f"{prefix}/files/content/{quote(filename)}{query}"


def media_content_path(filename: str, *, browser: bool = True) -> str:
    prefix = "/media" if browser else "/api/node/media"
    return f"{prefix}/files/content/{quote(filename)}"


def publish_media_file(
    path: str | Path,
    *,
    content_ref: str,
    namespace: str = "media",
    variant: str = "media",
    mime: str = "image/jpeg",
    api_token: str | None = None,
) -> dict[str, Any]:
    source = Path(path)
    safe_namespace = _safe_token(namespace) or "media"
    safe_variant = _safe_token(variant) or "media"
    suffix = source.suffix.lower() if source.suffix else ".jpg"
    filename = f"{safe_namespace}-{hashlib.sha256(str(content_ref or source).encode('utf-8')).hexdigest()[:24]}-{safe_variant}{suffix}"
    target = media_file_path(filename)
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, target)
    return {
        "ok": True,
        "filename": target.name,
        "path": str(target),
        "url": media_content_url(target.name, api_token=api_token),
        "node_url": media_content_url(target.name, api_token=api_token),
        "browser_url": media_content_url(target.name, api_token=api_token, browser=True),
        "content_path": media_content_path(target.name, browser=False),
        "browser_path": media_content_path(target.name, browser=True),
        "mime": mime,
        "size_bytes": int(target.stat().st_size),
        "content_ref": content_ref,
        "route": "node_media_file",
        "browser_route": "hub_browser_media",
    }


def browser_media_descriptor(media: dict[str, Any], *, content_ref: str | None = None) -> dict[str, Any]:
    return {
        "route": media.get("browser_route") or media.get("route") or "hub_browser_media",
        "path": str(media.get("browser_path") or media.get("content_path") or ""),
        "filename": str(media.get("filename") or ""),
        "mime": str(media.get("mime") or "application/octet-stream"),
        "content_ref": content_ref or media.get("content_ref") or "",
        "size_bytes": int(media.get("size_bytes") or 0),
    }


def _api_token() -> str:
    try:
        return str(get_ctx().config.token or "").strip()
    except Exception:
        return ""


def _safe_token(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum() or ch in {"-", "_"})


__all__ = [
    "browser_media_descriptor",
    "cached_image_variant",
    "image_fingerprint",
    "media_content_path",
    "media_content_url",
    "publish_media_file",
    "source_image_cache_dir",
]
