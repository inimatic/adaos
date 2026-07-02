from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.skill.runtime import SkillDirectoryNotFoundError, find_skill_dir


PUBLIC_ASSET_URL_PREFIX = "/assets"
PUBLIC_ASSET_MAX_BYTES = int(os.getenv("ADAOS_BROWSER_ASSET_MAX_BYTES", str(5 * 1024 * 1024)) or str(5 * 1024 * 1024))
_SAFE_OWNER_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_. -]+")
_PUBLIC_MIME_PREFIXES = ("image/", "font/")
_PUBLIC_MIME_TYPES = {
    "application/json",
    "image/svg+xml",
    "text/plain",
}


class BrowserAssetPublishError(RuntimeError):
    pass


def _paths_base(ctx: AgentContext | None = None) -> Path:
    agent_ctx = ctx or get_ctx()
    return Path(agent_ctx.paths.base_dir()).expanduser().resolve()


def assets_root(ctx: AgentContext | None = None) -> Path:
    return (_paths_base(ctx) / "assets").resolve()


def public_assets_root(ctx: AgentContext | None = None) -> Path:
    return (assets_root(ctx) / "public").resolve()


def static_assets_directory(ctx: AgentContext | None = None) -> Path:
    path = public_assets_root(ctx)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_owner(value: str) -> str:
    return _SAFE_OWNER_RE.sub("_", str(value or "").strip()).strip("._-") or "unknown"


def _safe_filename(value: str) -> str:
    name = Path(str(value or "").replace("\\", "/")).name
    safe = _SAFE_FILENAME_RE.sub("_", name).strip(" .")
    return safe or "asset.bin"


def _guess_mime(path: Path, explicit: str | None = None) -> str:
    token = str(explicit or "").strip()
    if token:
        return token
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _is_public_mime(mime: str) -> bool:
    token = str(mime or "").strip().lower().split(";", 1)[0]
    if token in _PUBLIC_MIME_TYPES:
        return True
    return any(token.startswith(prefix) for prefix in _PUBLIC_MIME_PREFIXES)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_relative_asset_path(path: str) -> Path:
    raw_path = str(path or "").strip().replace("\\", "/")
    if not raw_path or raw_path.startswith("/") or "\x00" in raw_path:
        raise BrowserAssetPublishError("invalid_asset_path")
    relative = Path(raw_path)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise BrowserAssetPublishError("invalid_asset_path")
    if relative.parts[0] != "assets":
        raise BrowserAssetPublishError("asset_path_must_start_with_assets")
    return relative


def _resolve_skill_dir(skill_name: str, *, skill_dir: str | Path | None = None) -> Path:
    if skill_dir is not None:
        path = Path(skill_dir).expanduser().resolve()
        if path.is_dir():
            return path
    try:
        return find_skill_dir(skill_name)
    except SkillDirectoryNotFoundError as exc:
        raise BrowserAssetPublishError("skill_not_found") from exc


def _copy_immutable_blob(source: Path, target: Path) -> None:
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)


def _write_owner_manifest(*, owner_kind: str, owner_id: str, resource_id: str, descriptor: Mapping[str, Any], ctx: AgentContext | None = None) -> None:
    manifest_root = assets_root(ctx) / "manifests" / f"{_safe_owner(owner_kind)}s"
    manifest_root.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_root / f"{_safe_owner(owner_id)}.json"
    try:
        current = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}
    resources = current.get("resources") if isinstance(current.get("resources"), dict) else {}
    resources[str(resource_id)] = dict(descriptor)
    current.update(
        {
            "schema": "adaos.browser_assets.manifest.v1",
            "ownerKind": owner_kind,
            "ownerId": owner_id,
            "updatedAt": time.time(),
            "resources": resources,
        }
    )
    tmp = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(tmp, manifest_path)


def publish_skill_resource_descriptor(
    resource_id: str,
    descriptor: Mapping[str, Any],
    *,
    skill_name: str,
    skill_dir: str | Path | None = None,
    ctx: AgentContext | None = None,
) -> dict[str, Any]:
    out = dict(descriptor)
    delivery = str(out.get("delivery") or "core").strip().lower()
    if delivery == "external" or out.get("url") or out.get("src") or out.get("href"):
        return out
    relative = _resolve_relative_asset_path(str(out.get("path") or ""))
    resolved_skill_dir = _resolve_skill_dir(skill_name, skill_dir=skill_dir)
    source = (resolved_skill_dir / relative).resolve()
    assets_dir = (resolved_skill_dir / "assets").resolve()
    try:
        source.relative_to(assets_dir)
    except ValueError as exc:
        raise BrowserAssetPublishError("asset_path_forbidden") from exc
    if not source.is_file():
        raise BrowserAssetPublishError("asset_not_found")
    size = source.stat().st_size
    if size > PUBLIC_ASSET_MAX_BYTES:
        raise BrowserAssetPublishError("asset_too_large")
    mime = _guess_mime(source, str(out.get("mime") or "").strip() or None)
    if not _is_public_mime(mime):
        raise BrowserAssetPublishError("asset_mime_not_public")
    digest = _sha256_file(source)
    filename = _safe_filename(source.name)
    blob_rel = Path("blobs") / "sha256" / digest[:2] / digest[2:4] / digest / filename
    target = public_assets_root(ctx) / blob_rel
    _copy_immutable_blob(source, target)
    url = f"{PUBLIC_ASSET_URL_PREFIX}/{'/'.join(quote(part, safe='') for part in blob_rel.parts)}"
    out.update(
        {
            "scope": out.get("scope") or "skill",
            "owner": out.get("owner") or f"skill:{skill_name}",
            "url": url,
            "mime": mime,
            "sizeBytes": int(size),
            "cacheKey": f"sha256:{digest}",
            "published": True,
        }
    )
    _write_owner_manifest(
        owner_kind="skill",
        owner_id=skill_name,
        resource_id=resource_id,
        descriptor=out,
        ctx=ctx,
    )
    return out
