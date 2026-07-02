from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from adaos.services.agent_context import AgentContext


PUBLIC_ASSET_URL_PREFIX = "/assets"
PUBLIC_ASSET_MAX_BYTES = int(os.getenv("ADAOS_BROWSER_ASSET_MAX_BYTES", str(5 * 1024 * 1024)) or str(5 * 1024 * 1024))
SYSTEM_ASSET_OWNER_ID = "adaos-core"
SYSTEM_ASSET_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "system_assets"
SYSTEM_BROWSER_RESOURCES: dict[str, dict[str, Any]] = {
    "assistant.default.avatar": {
        "kind": "image",
        "scope": "system",
        "path": "assets/avatars/assistant-default.svg",
        "mime": "image/svg+xml",
        "title": "Assistant",
        "alt": "Default assistant avatar",
    },
    "assistant.voice.avatar": {
        "kind": "image",
        "scope": "system",
        "path": "assets/avatars/assistant-voice.svg",
        "mime": "image/svg+xml",
        "title": "Voice assistant",
        "alt": "Voice assistant avatar",
    },
    "assistant.helper.avatar": {
        "kind": "image",
        "scope": "system",
        "path": "assets/avatars/assistant-helper.svg",
        "mime": "image/svg+xml",
        "title": "Helper assistant",
        "alt": "Helper assistant avatar",
    },
}
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


def get_ctx():
    from adaos.services.agent_context import get_ctx as _get_ctx

    return _get_ctx()


def _paths_base(ctx: AgentContext | None = None, *, base_dir: str | Path | None = None) -> Path:
    if base_dir is not None:
        return Path(base_dir).expanduser().resolve()
    agent_ctx = ctx or get_ctx()
    return Path(agent_ctx.paths.base_dir()).expanduser().resolve()


def assets_root(ctx: AgentContext | None = None, *, base_dir: str | Path | None = None) -> Path:
    return (_paths_base(ctx, base_dir=base_dir) / "assets").resolve()


def public_assets_root(ctx: AgentContext | None = None, *, base_dir: str | Path | None = None) -> Path:
    return (assets_root(ctx, base_dir=base_dir) / "public").resolve()


def static_assets_directory(ctx: AgentContext | None = None, *, base_dir: str | Path | None = None) -> Path:
    path = public_assets_root(ctx, base_dir=base_dir)
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
    from adaos.services.skill.runtime import SkillDirectoryNotFoundError, find_skill_dir

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


def _read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_owner_manifest(
    *,
    owner_kind: str,
    owner_id: str,
    resource_id: str,
    descriptor: Mapping[str, Any],
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> None:
    manifest_root = assets_root(ctx, base_dir=base_dir) / "manifests" / f"{_safe_owner(owner_kind)}s"
    manifest_root.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_root / f"{_safe_owner(owner_id)}.json"
    try:
        current = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}
    resources = current.get("resources") if isinstance(current.get("resources"), dict) else {}
    next_descriptor = dict(descriptor)
    if (
        current.get("schema") == "adaos.browser_assets.manifest.v1"
        and current.get("ownerKind") == owner_kind
        and current.get("ownerId") == owner_id
        and resources.get(str(resource_id)) == next_descriptor
    ):
        return
    resources[str(resource_id)] = next_descriptor
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
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    resolved_skill_dir = _resolve_skill_dir(skill_name, skill_dir=skill_dir)
    return publish_owner_resource_descriptor(
        resource_id,
        descriptor,
        owner_kind="skill",
        owner_id=skill_name,
        package_dir=resolved_skill_dir,
        default_scope="skill",
        ctx=ctx,
        base_dir=base_dir,
    )


def publish_scenario_resource_descriptor(
    resource_id: str,
    descriptor: Mapping[str, Any],
    *,
    scenario_id: str,
    scenario_dir: str | Path,
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    return publish_owner_resource_descriptor(
        resource_id,
        descriptor,
        owner_kind="scenario",
        owner_id=scenario_id,
        package_dir=scenario_dir,
        default_scope="scenario",
        ctx=ctx,
        base_dir=base_dir,
    )


def publish_system_resource_descriptor(
    resource_id: str,
    descriptor: Mapping[str, Any],
    *,
    owner_id: str = SYSTEM_ASSET_OWNER_ID,
    package_dir: str | Path = SYSTEM_ASSET_PACKAGE_DIR,
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    return publish_owner_resource_descriptor(
        resource_id,
        descriptor,
        owner_kind="system",
        owner_id=owner_id,
        package_dir=package_dir,
        default_scope="system",
        ctx=ctx,
        base_dir=base_dir,
    )


def publish_owner_resource_descriptor(
    resource_id: str,
    descriptor: Mapping[str, Any],
    *,
    owner_kind: str,
    owner_id: str,
    package_dir: str | Path,
    default_scope: str,
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    out = dict(descriptor)
    delivery = str(out.get("delivery") or "core").strip().lower()
    if delivery == "external" or out.get("url") or out.get("src") or out.get("href"):
        return out
    relative = _resolve_relative_asset_path(str(out.get("path") or ""))
    resolved_package_dir = Path(package_dir).expanduser().resolve()
    source = (resolved_package_dir / relative).resolve()
    assets_dir = (resolved_package_dir / "assets").resolve()
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
    target = public_assets_root(ctx, base_dir=base_dir) / blob_rel
    _copy_immutable_blob(source, target)
    url = f"{PUBLIC_ASSET_URL_PREFIX}/{'/'.join(quote(part, safe='') for part in blob_rel.parts)}"
    owner_token = f"{owner_kind}:{owner_id}"
    out.update(
        {
            "scope": out.get("scope") or default_scope,
            "owner": out.get("owner") or owner_token,
            "url": url,
            "mime": mime,
            "sizeBytes": int(size),
            "cacheKey": f"sha256:{digest}",
            "published": True,
        }
    )
    _write_owner_manifest(
        owner_kind=owner_kind,
        owner_id=owner_id,
        resource_id=resource_id,
        descriptor=out,
        ctx=ctx,
        base_dir=base_dir,
    )
    return out


def publish_owner_resource_descriptors(
    resources: Mapping[str, Any],
    *,
    owner_kind: str,
    owner_id: str,
    package_dir: str | Path,
    default_scope: str,
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    published: dict[str, Any] = {}
    skipped: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, value in resources.items():
        resource_id = str(key or "").strip()
        if not resource_id or not isinstance(value, Mapping):
            continue
        try:
            descriptor = publish_owner_resource_descriptor(
                resource_id,
                value,
                owner_kind=owner_kind,
                owner_id=owner_id,
                package_dir=package_dir,
                default_scope=default_scope,
                ctx=ctx,
                base_dir=base_dir,
            )
            if descriptor.get("published"):
                published[resource_id] = descriptor
            else:
                skipped[resource_id] = descriptor
        except BrowserAssetPublishError as exc:
            item = dict(value)
            item["published"] = False
            item["publishError"] = str(exc)
            errors[resource_id] = str(exc)
            skipped[resource_id] = item
        except Exception:
            item = dict(value)
            item["published"] = False
            item["publishError"] = "publish_failed"
            errors[resource_id] = "publish_failed"
            skipped[resource_id] = item
    return {
        "ok": not errors,
        "ownerKind": owner_kind,
        "ownerId": owner_id,
        "published": published,
        "skipped": skipped,
        "errors": errors,
        "counts": {
            "published": len(published),
            "skipped": len(skipped),
            "errors": len(errors),
        },
    }


def _resources_from_webui(path: Path) -> dict[str, Any]:
    payload = _read_json_mapping(path)
    catalog = payload.get("catalog") if isinstance(payload.get("catalog"), Mapping) else {}
    resources = payload.get("resources") if isinstance(payload.get("resources"), Mapping) else {}
    if not resources and isinstance(catalog, Mapping):
        resources = catalog.get("resources") if isinstance(catalog.get("resources"), Mapping) else {}
    return dict(resources) if isinstance(resources, Mapping) else {}


def _resources_from_scenario_content(path: Path) -> dict[str, Any]:
    payload = _read_json_mapping(path)
    ui = payload.get("ui") if isinstance(payload.get("ui"), Mapping) else {}
    application = ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
    catalog = payload.get("catalog") if isinstance(payload.get("catalog"), Mapping) else {}
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    data_catalog = data.get("catalog") if isinstance(data.get("catalog"), Mapping) else {}
    for source in (application, catalog, data_catalog, payload):
        resources = source.get("resources") if isinstance(source, Mapping) else {}
        if isinstance(resources, Mapping) and resources:
            return dict(resources)
    return {}


def publish_skill_assets_from_webui(
    skill_name: str,
    *,
    skill_dir: str | Path | None = None,
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    resolved_skill_dir = _resolve_skill_dir(skill_name, skill_dir=skill_dir)
    resources = _resources_from_webui(resolved_skill_dir / "webui.json")
    return publish_owner_resource_descriptors(
        resources,
        owner_kind="skill",
        owner_id=skill_name,
        package_dir=resolved_skill_dir,
        default_scope="skill",
        ctx=ctx,
        base_dir=base_dir,
    )


def publish_scenario_assets_from_content(
    scenario_id: str,
    *,
    scenario_dir: str | Path,
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    resolved_scenario_dir = Path(scenario_dir).expanduser().resolve()
    resources = _resources_from_scenario_content(resolved_scenario_dir / "scenario.json")
    return publish_owner_resource_descriptors(
        resources,
        owner_kind="scenario",
        owner_id=scenario_id,
        package_dir=resolved_scenario_dir,
        default_scope="scenario",
        ctx=ctx,
        base_dir=base_dir,
    )


def publish_system_resource_descriptors(
    *,
    ctx: AgentContext | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    return publish_owner_resource_descriptors(
        SYSTEM_BROWSER_RESOURCES,
        owner_kind="system",
        owner_id=SYSTEM_ASSET_OWNER_ID,
        package_dir=SYSTEM_ASSET_PACKAGE_DIR,
        default_scope="system",
        ctx=ctx,
        base_dir=base_dir,
    )
