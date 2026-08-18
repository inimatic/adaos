"""Reusable media helpers for browser-facing skill surfaces."""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from adaos.services.agent_context import get_ctx
from adaos.services.media_core import (
    MEDIA_STORE_SKILL_NAME,
    MediaResource,
    iter_media_store_resources,
    iter_media_reference_resources,
    media_indexer_content_path as _core_media_indexer_content_path,
    media_reference_content_path as _core_media_reference_content_path,
    media_resource_content_path as _core_media_resource_content_path,
    media_resource_descriptor as _core_media_resource_descriptor,
    media_store_content_path,
    media_store_file_path,
    register_media_reference as _core_register_media_reference,
    sanitize_media_filename,
)
from adaos.services.media_reference_registry import (
    unregister_media_references as _core_unregister_media_references,
)
from adaos.services.runtime_paths import current_base_dir
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


MEDIA_SKILL_NAME = MEDIA_STORE_SKILL_NAME
media_file_path = media_store_file_path


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
    create: bool = True,
) -> tuple[Path, bool]:
    source = Path(path)
    safe_label = "".join(ch for ch in str(label or "").lower() if ch.isalnum() or ch in {"-", "_"}) or "image"
    cache_path = source_image_cache_dir(source, fallback_dir=fallback_dir) / f"{image_fingerprint(source)}-{safe_label}.jpg"
    if cache_path.exists():
        return cache_path, True
    if not create:
        return cache_path, False

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
    return f"{media_store_content_path(filename, browser=browser)}{query}"


def media_content_path(filename: str, *, browser: bool = True) -> str:
    return media_store_content_path(filename, browser=browser)


def media_indexer_content_path(playback_id: str, *, browser: bool = True) -> str:
    return _core_media_indexer_content_path(playback_id, browser=browser)


def media_reference_content_path(resource_id: str, *, browser: bool = True) -> str:
    return _core_media_reference_content_path(resource_id, browser=browser)


def media_resource_content_path(
    resource_id: str,
    *,
    source: str = "media_server",
    browser: bool = True,
) -> str:
    return _core_media_resource_content_path(resource_id, source=source, browser=browser)


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
    return _core_media_resource_descriptor(
        resource_id=resource_id,
        source=source,
        name=name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        modified_at=modified_at,
        content_path=content_path,
        routed_content_path=routed_content_path,
        playback_id=playback_id,
        source_path=source_path,
        metadata=metadata,
    )


def list_media_resources(
    source: str = "media_server",
    *,
    include_internal: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return normalized media resource descriptors from core-backed sources.

    Skills use this as the stable SDK surface for cataloging and playback
    planning. Product semantics stay in the skill; the SDK only exposes source
    adapters as ``adaos.media.resource.v1`` dictionaries.
    """

    source_norm = str(source or "media_server").strip().lower() or "media_server"
    if source_norm in {"media", "media_store", "mediaserver"}:
        source_norm = "media_server"
    if source_norm in {"indexer", "media_indexer_skill"}:
        source_norm = "media_indexer"
    if source_norm not in {"all", "media_server", "media_indexer"}:
        raise ValueError("unsupported_media_source")

    max_items = int(limit) if limit is not None else None
    if max_items is not None and max_items <= 0:
        return []

    resources: list[MediaResource] = []

    if source_norm in {"all", "media_server"}:
        try:
            resources.extend(iter_media_store_resources())
        except Exception:
            pass
        try:
            resources.extend(iter_media_reference_resources())
        except Exception:
            pass
    if source_norm in {"all", "media_indexer"}:
        try:
            from adaos.services.media_indexer_library import iter_media_indexer_resources

            resources.extend(iter_media_indexer_resources())
        except Exception:
            if source_norm == "media_indexer":
                return []

    resources.sort(key=lambda item: (item.modified_ts, item.source, item.name), reverse=True)
    if max_items is not None:
        resources = resources[:max_items]
    return [item.to_public_dict(include_internal=include_internal) for item in resources]


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _normalized_base_url(value: str | None) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _is_loopback_host(host: str | None) -> bool:
    token = str(host or "").strip().lower()
    return token in {"localhost", "127.0.0.1", "::1", "[::1]"}


def _local_ipv4_addresses() -> list[str]:
    candidates: list[str] = []

    def add(value: str | None) -> None:
        token = str(value or "").strip()
        if not token or token.startswith("127.") or token.startswith("169.254."):
            return
        if ":" in token:
            return
        if token not in candidates:
            candidates.append(token)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            add(sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        pass
    try:
        for item in socket.gethostbyname_ex(socket.gethostname())[2]:
            add(item)
    except Exception:
        pass
    return candidates


def direct_media_base_urls() -> list[str]:
    """Return endpoint-reachable hub media bases, preferring explicit config.

    ReDevice endpoints cannot use browser-only media paths. They need a concrete
    hub HTTP base. Explicit ``ADAOS_REDEVICE_MEDIA_BASES`` /
    ``ADAOS_MEDIA_DIRECT_BASES`` values are treated as operator-provided
    endpoint routes. Runtime-managed loopback URLs are not expanded to LAN
    addresses by default: a process bound to 127.0.0.1 is not reachable from a
    legacy tablet even if the host has a LAN IP. Set
    ``ADAOS_MEDIA_DIRECT_EXPAND_LOOPBACK=1`` only when a matching LAN listener or
    port-forward is known to exist.
    """

    bases: list[tuple[str, bool]] = []

    def add(value: str | None, *, implicit: bool) -> None:
        base = _normalized_base_url(value)
        if base and (base, implicit) not in bases:
            bases.append((base, implicit))

    for value in _split_csv(os.getenv("ADAOS_REDEVICE_MEDIA_BASES") or os.getenv("ADAOS_MEDIA_DIRECT_BASES")):
        add(value, implicit=False)
    add(os.getenv("ADAOS_SELF_BASE_URL"), implicit=True)

    configured = ""
    try:
        configured = str(getattr(get_ctx().config, "local_api_url", "") or "").strip()
    except Exception:
        configured = ""
    add(configured, implicit=True)

    expanded: list[str] = []
    expand_loopback = str(os.getenv("ADAOS_MEDIA_DIRECT_EXPAND_LOOPBACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    for base, implicit in bases:
        parsed = urlparse(base)
        host = parsed.hostname or ""
        if not _is_loopback_host(host):
            if base not in expanded:
                expanded.append(base)
            continue
        if expand_loopback and parsed.port:
            for address in _local_ipv4_addresses():
                candidate = urlunparse((parsed.scheme, f"{address}:{parsed.port}", parsed.path.rstrip("/"), "", "", ""))
                if candidate not in expanded:
                    expanded.append(candidate)
        elif not implicit:
            # Explicit loopback values are preserved only for local development
            # diagnostics. They are not useful for remote endpoints, but keeping
            # the operator-provided value makes the configuration observable.
            if base not in expanded:
                expanded.append(base)
    return expanded


def direct_media_content_urls(filename: str, *, api_token: str | None = None) -> list[str]:
    path = media_content_url(filename, api_token=api_token, browser=False)
    return [base.rstrip("/") + path for base in direct_media_base_urls()]


def direct_media_reference_urls(resource_id: str, *, api_token: str | None = None) -> list[str]:
    token = str(api_token or _api_token() or "").strip()
    query = f"?token={quote(token)}" if token else ""
    path = media_reference_content_path(resource_id, browser=False) + query
    return [base.rstrip("/") + path for base in direct_media_base_urls()]


def _media_file_path_for_publish(filename: str) -> Path:
    try:
        return media_file_path(filename)
    except RuntimeError as exc:
        if "AgentContext is not initialized" not in str(exc):
            raise
    name = sanitize_media_filename(filename)
    env = SkillRuntimeEnvironment(
        skills_root=current_base_dir() / "workspace" / "skills",
        skill_name=MEDIA_SKILL_NAME,
    )
    env.ensure_base()
    active_version = env.resolve_active_version()
    if active_version:
        env.ensure_data_dirs(active_version)
        return env.files_dir(active_version) / name
    env.ensure_data_dirs()
    return env.files_dir() / name


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
    target = _media_file_path_for_publish(filename)
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, target)
    direct_urls = direct_media_content_urls(target.name, api_token=api_token)
    size_bytes = int(target.stat().st_size)
    descriptor = media_resource_descriptor(
        resource_id=target.name,
        source="media_server",
        name=target.name,
        mime_type=mime,
        size_bytes=size_bytes,
        content_path=media_content_path(target.name, browser=False),
        routed_content_path=media_content_path(target.name, browser=True),
        source_path=str(target),
        metadata={"content_ref": content_ref, "namespace": safe_namespace, "variant": safe_variant},
    )
    descriptor.update(
        {
            "ok": True,
            "filename": target.name,
            "path": str(target),
            "url": media_content_url(target.name, api_token=api_token),
            "node_url": media_content_url(target.name, api_token=api_token),
            "browser_url": media_content_url(target.name, api_token=api_token, browser=True),
            "content_path": media_content_path(target.name, browser=False),
            "browser_path": media_content_path(target.name, browser=True),
            "direct_urls": direct_urls,
            "content_url_candidates": [*direct_urls, media_content_url(target.name, api_token=api_token)],
            "mime": mime,
            "mime_type": mime,
            "size_bytes": size_bytes,
            "content_ref": content_ref,
            "route": "node_media_file",
            "browser_route": "hub_browser_media",
            "delivery": {
                "schema_version": "media-delivery.v1",
                "preferred_route": "hub_direct_http" if direct_urls else "node_media_file",
                "fallback_route": "root_relay_inline",
                "direct_candidate_count": len(direct_urls),
            },
        }
    )
    return descriptor


def register_media_file(
    path: str | Path,
    *,
    root: str | Path,
    content_ref: str = "",
    namespace: str = "media",
    mime: str = "",
    metadata: dict[str, Any] | None = None,
    api_token: str | None = None,
) -> dict[str, Any]:
    """Register an original media file for playback without copying its bytes."""

    resource = _core_register_media_reference(
        path,
        root=root,
        content_ref=content_ref,
        namespace=namespace,
        mime_type=mime,
        metadata=metadata,
    )
    token = str(api_token or _api_token() or "").strip()
    query = f"?token={quote(token)}" if token else ""
    node_url = media_reference_content_path(resource.id, browser=False) + query
    browser_url = media_reference_content_path(resource.id, browser=True) + query
    direct_urls = direct_media_reference_urls(resource.id, api_token=api_token)
    descriptor = resource.to_public_dict()
    descriptor.update(
        {
            "ok": True,
            "filename": resource.name,
            "path": str(resource.path),
            "url": browser_url,
            "node_url": node_url,
            "browser_url": browser_url,
            "browser_path": resource.routed_content_path,
            "direct_urls": direct_urls,
            "content_url_candidates": [*direct_urls, node_url],
            "mime": resource.mime_type,
            "route": "node_media_reference",
            "browser_route": "hub_browser_media_reference",
            "delivery": {
                "schema_version": "media-delivery.v1",
                "preferred_route": "hub_direct_http" if direct_urls else "node_media_reference",
                "fallback_route": "root_relay_range",
                "direct_candidate_count": len(direct_urls),
                "storage_mode": "reference",
            },
        }
    )
    return descriptor


def unregister_media_references(resource_ids: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
    """Unregister exact core media references without deleting source files."""

    return _core_unregister_media_references(resource_ids)


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
    "direct_media_base_urls",
    "direct_media_content_urls",
    "direct_media_reference_urls",
    "image_fingerprint",
    "list_media_resources",
    "media_content_path",
    "media_content_url",
    "media_indexer_content_path",
    "media_reference_content_path",
    "media_resource_content_path",
    "media_resource_descriptor",
    "publish_media_file",
    "register_media_file",
    "source_image_cache_dir",
    "unregister_media_references",
]
