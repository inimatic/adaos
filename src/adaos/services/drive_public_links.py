from __future__ import annotations

import hashlib
import heapq
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, urlencode

from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.media_core import file_range_iter, media_content_response_parts, parse_media_range
from adaos.services.public_grants import (
    FOLDER_READ_ONLY_CAPABILITIES,
    PUBLIC_GRANT_SCHEMA,
    READ_ONLY_CAPABILITIES,
    normalize_public_capabilities,
    public_grant_descriptor,
)


DRIVE_PUBLIC_LINK_SCHEMA = "adaos.drive.public_link.v1"
DRIVE_PUBLIC_LINK_SKILL = "adaos_drive"
DRIVE_PUBLIC_FACE_ID = "adaos_drive.files.public"
DRIVE_PUBLIC_GRANT_KIND = "drive.files"
DRIVE_PUBLIC_LINK_CONTENT_PATH_PREFIX = f"/api/skills/{DRIVE_PUBLIC_LINK_SKILL}/public-links"
DRIVE_PUBLIC_LINK_ROOT_PATH_PREFIX = "/v1/drive/public-links"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_MAX_TTL_SECONDS = 90 * 24 * 3600
_PUBLIC_LABEL_MAX = 120
_DOWNLOAD_RECENT_MAX = 2000
_DOWNLOAD_EVENT_TEXT_MAX = 240
_DOWNLOAD_USER_AGENT_MAX = 240
_DOWNLOAD_STORE_SCHEMA = "adaos.drive.public_downloads.v1"
_DOWNLOAD_LEGACY_FILENAME = "hub_downloads.json"
_DOWNLOAD_EVENTS_FILENAME = "events.jsonl"
_DOWNLOAD_SUMMARIES_FILENAME = "summaries.json"
_DOWNLOAD_LOCK = threading.Lock()


class DrivePublicLinkError(RuntimeError):
    pass


class DrivePublicLinkNotFound(DrivePublicLinkError):
    pass


class DrivePublicLinkExpired(DrivePublicLinkError):
    pass


class DrivePublicLinkForbidden(DrivePublicLinkError):
    pass


def issue_public_token() -> str:
    return secrets.token_urlsafe(24).rstrip("=")


def issue_hub_token() -> str:
    return secrets.token_urlsafe(32).rstrip("=")


def validate_public_token(value: Any) -> str:
    token = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("invalid_public_token")
    return token


def validate_hub_token(value: Any) -> str:
    token = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("invalid_hub_token")
    return token


def drive_public_link_content_path(public_token: str) -> str:
    token = validate_public_token(public_token)
    return f"{DRIVE_PUBLIC_LINK_CONTENT_PATH_PREFIX}/{quote(token, safe='')}/content"


def build_root_public_content_url(
    base_url: str,
    public_token: str,
    *,
    download: bool = False,
    path: Any = "",
) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url_required")
    token = validate_public_token(public_token)
    url = f"{base}{DRIVE_PUBLIC_LINK_ROOT_PATH_PREFIX}/{quote(token, safe='')}/content"
    params: list[tuple[str, str]] = []
    rel = _clean_rel(path)
    if rel:
        params.append(("path", rel))
    if download:
        params.append(("download", "1"))
    return f"{url}?{urlencode(params)}" if params else url


def build_root_public_list_url(base_url: str, public_token: str, *, path: Any = "") -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url_required")
    token = validate_public_token(public_token)
    url = f"{base}{DRIVE_PUBLIC_LINK_ROOT_PATH_PREFIX}/{quote(token, safe='')}/list"
    rel = _clean_rel(path)
    return f"{url}?{urlencode([('path', rel)])}" if rel else url


def _now() -> float:
    return time.time()


def _iso_utc(value: float | None = None) -> str:
    return datetime.fromtimestamp(float(value if value is not None else _now()), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_expiry_epoch(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        return stamp / 1000.0 if stamp > 10_000_000_000 else stamp
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = float(text)
        return stamp / 1000.0 if stamp > 10_000_000_000 else stamp
    except ValueError:
        pass
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return None


def _coerce_expires_at(*, ttl_seconds: Any = None, expires_at: Any = None) -> tuple[float, str]:
    explicit = _parse_expiry_epoch(expires_at)
    now = _now()
    if explicit and explicit > now:
        epoch = explicit
    else:
        try:
            ttl = int(ttl_seconds or _DEFAULT_TTL_SECONDS)
        except Exception:
            ttl = _DEFAULT_TTL_SECONDS
        ttl = max(60, min(_MAX_TTL_SECONDS, ttl))
        epoch = now + ttl
    return epoch, _iso_utc(epoch)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _base_dir(ctx: AgentContext | None = None) -> Path:
    try:
        raw = (ctx or get_ctx()).paths.base_dir()
        return Path(raw).expanduser().resolve()
    except Exception:
        raw = os.getenv("ADAOS_BASE_DIR") or ""
        return Path(raw).expanduser().resolve() if raw else (Path.cwd() / ".adaos").resolve()


def _store_path(kind: str, ctx: AgentContext | None = None) -> Path:
    root = _base_dir(ctx) / "state" / "drive_public_links"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{kind}_links.json"


def _load_store(kind: str, ctx: AgentContext | None = None) -> dict[str, Any]:
    path = _store_path(kind, ctx)
    if not path.exists():
        return {"v": 1, "links": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {"v": 1, "links": {}}
    if not isinstance(data, dict):
        return {"v": 1, "links": {}}
    links = data.get("links")
    if not isinstance(links, dict):
        data["links"] = {}
    data.setdefault("v", 1)
    return data


def _save_store(kind: str, data: Mapping[str, Any], ctx: AgentContext | None = None) -> None:
    path = _store_path(kind, ctx)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _ctx_path_value(ctx: AgentContext | None, name: str) -> Path | None:
    try:
        paths = (ctx or get_ctx()).paths
        raw = getattr(paths, name, None)
        value = raw() if callable(raw) else raw
        if value:
            return Path(value).expanduser().resolve()
    except Exception:
        return None
    return None


def _download_legacy_store_path(ctx: AgentContext | None = None) -> Path:
    root = _base_dir(ctx) / "state" / "drive_public_links"
    return root / _DOWNLOAD_LEGACY_FILENAME


def _download_skill_root(ctx: AgentContext | None = None) -> Path:
    override = str(os.getenv("ADAOS_DRIVE_PUBLIC_DOWNLOAD_DIR") or "").strip()
    if override:
        root = Path(override).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    memory_path = str(os.getenv("ADAOS_SKILL_MEMORY_PATH") or "").strip()
    if memory_path:
        base = Path(memory_path).expanduser().resolve()
        data_root = base.parent.parent if base.name == "skill_env.json" and base.parent.name == "db" else (base if base.is_dir() else base.parent)
        root = data_root / "files" / "public_downloads"
        root.mkdir(parents=True, exist_ok=True)
        return root
    for path_name in ("skills_dir", "dev_skills_dir", "skills_workspace_dir"):
        skills_root = _ctx_path_value(ctx, path_name)
        if not skills_root:
            continue
        try:
            from adaos.services.skill.runtime_env import SkillRuntimeEnvironment

            root = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=DRIVE_PUBLIC_LINK_SKILL).files_dir() / "public_downloads"
            root.mkdir(parents=True, exist_ok=True)
            return root
        except Exception:
            continue
    root = _base_dir(ctx) / "workspace" / "skills" / ".runtime" / DRIVE_PUBLIC_LINK_SKILL / "v0.0" / "data" / "files" / "public_downloads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _download_events_path(ctx: AgentContext | None = None) -> Path:
    return _download_skill_root(ctx) / _DOWNLOAD_EVENTS_FILENAME


def _download_summaries_path(ctx: AgentContext | None = None) -> Path:
    return _download_skill_root(ctx) / _DOWNLOAD_SUMMARIES_FILENAME


def _empty_download_summary_store() -> dict[str, Any]:
    return {"schema": _DOWNLOAD_STORE_SCHEMA, "v": 1, "summaries": {}}


def _load_legacy_download_store(ctx: AgentContext | None = None) -> dict[str, Any]:
    path = _download_legacy_store_path(ctx)
    if not path.exists():
        return {"schema": _DOWNLOAD_STORE_SCHEMA, "v": 1, "summaries": {}, "events": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {"schema": _DOWNLOAD_STORE_SCHEMA, "v": 1, "summaries": {}, "events": []}
    if not isinstance(data, dict):
        return {"schema": _DOWNLOAD_STORE_SCHEMA, "v": 1, "summaries": {}, "events": []}
    data["schema"] = str(data.get("schema") or _DOWNLOAD_STORE_SCHEMA)
    try:
        data["v"] = int(data.get("v") or 1)
    except Exception:
        data["v"] = 1
    if not isinstance(data.get("summaries"), dict):
        data["summaries"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    return data


def _load_download_summary_store(ctx: AgentContext | None = None) -> dict[str, Any]:
    path = _download_summaries_path(ctx)
    if not path.exists():
        legacy = _load_legacy_download_store(ctx)
        return {
            "schema": _DOWNLOAD_STORE_SCHEMA,
            "v": 1,
            "summaries": dict(legacy.get("summaries") or {}),
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return _empty_download_summary_store()
    if not isinstance(data, dict):
        return _empty_download_summary_store()
    data["schema"] = str(data.get("schema") or _DOWNLOAD_STORE_SCHEMA)
    try:
        data["v"] = int(data.get("v") or 1)
    except Exception:
        data["v"] = 1
    if not isinstance(data.get("summaries"), dict):
        data["summaries"] = {}
    return data


def _save_download_summary_store(data: Mapping[str, Any], ctx: AgentContext | None = None) -> None:
    path = _download_summaries_path(ctx)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_download_event(event: Mapping[str, Any], ctx: AgentContext | None = None) -> None:
    path = _download_events_path(ctx)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n")


def _read_recent_download_events(ctx: AgentContext | None = None, *, limit: int = _DOWNLOAD_RECENT_MAX) -> list[dict[str, Any]]:
    max_lines = max(1, min(_DOWNLOAD_RECENT_MAX, int(limit or _DOWNLOAD_RECENT_MAX)))
    path = _download_events_path(ctx)
    if not path.exists():
        legacy = _load_legacy_download_store(ctx)
        return [dict(item) for item in list(legacy.get("events") or [])[:max_lines] if isinstance(item, Mapping)]
    recent: deque[str] = deque(maxlen=max_lines)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if text:
                    recent.append(text)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(recent):
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, Mapping):
            out.append(dict(item))
    return out


def _limited_text(value: Any, *, max_len: int = _DOWNLOAD_EVENT_TEXT_MAX) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:max(0, int(max_len))]


def _safe_guest_device_id(value: Any) -> str:
    text = _limited_text(value, max_len=160)
    if not text:
        return ""
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)[:160]


def _first_header(headers: Mapping[str, Any] | None, *names: str) -> str:
    if not isinstance(headers, Mapping):
        return ""
    lower = {str(k).lower(): v for k, v in headers.items()}
    for name in names:
        value = lower.get(str(name).lower())
        if value is not None:
            return _limited_text(value, max_len=_DOWNLOAD_USER_AGENT_MAX)
    return ""


def _query_value(*, request: Request | None = None, search: str = "", key: str) -> str:
    try:
        if request is not None:
            return _limited_text(request.query_params.get(key, ""))
    except Exception:
        pass
    try:
        raw = str(search or "")
        query = raw[1:] if raw.startswith("?") else raw
        parsed = parse_qs(query, keep_blank_values=True)
        values = parsed.get(key) or []
        return _limited_text(values[0] if values else "")
    except Exception:
        return ""


def _client_ip_hash(value: Any) -> str:
    text = _limited_text(value, max_len=128)
    if not text:
        return ""
    first = text.split(",", 1)[0].strip()
    if not first:
        return ""
    return hashlib.sha256(f"adaos-drive-public-client:{first}".encode("utf-8")).hexdigest()[:16]


def _download_request_context(
    *,
    request: Request | None = None,
    request_headers: Mapping[str, Any] | None = None,
    search: str = "",
    method: str = "",
) -> dict[str, Any]:
    headers: Mapping[str, Any] = request_headers if isinstance(request_headers, Mapping) else {}
    try:
        if request is not None:
            headers = request.headers
    except Exception:
        pass
    remote_ip = _first_header(headers, "cf-connecting-ip", "x-real-ip", "x-forwarded-for")
    if not remote_ip and request is not None:
        try:
            remote_ip = str(getattr(request.client, "host", "") or "")
        except Exception:
            remote_ip = ""
    guest_device_id = (
        _query_value(request=request, search=search, key="guest_device_id")
        or _first_header(headers, "x-adaos-public-device-id", "x-adaos-device-id")
    )
    return {
        "method": (str(method or "") or (str(getattr(request, "method", "") or "") if request is not None else "") or "GET").upper(),
        "guest_device_id": _safe_guest_device_id(guest_device_id),
        "client_ip_hash": _client_ip_hash(remote_ip),
        "user_agent": _limited_text(_first_header(headers, "user-agent"), max_len=_DOWNLOAD_USER_AGENT_MAX),
        "referer": _limited_text(_first_header(headers, "referer", "referrer"), max_len=_DOWNLOAD_USER_AGENT_MAX),
        "range": _limited_text(_first_header(headers, "range"), max_len=120),
    }


def _clean_rel(value: Any) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw == ".":
        return ""
    if raw.startswith("/") or ":" in raw:
        raise ValueError("path_must_be_relative")
    parts: list[str] = []
    for part in raw.split("/"):
        token = part.strip()
        if not token or token == ".":
            continue
        if token == "..":
            raise ValueError("path_traversal_not_allowed")
        if "\x00" in token:
            raise ValueError("path_contains_null_byte")
        parts.append(token)
    return "/".join(parts)


def _target_under_root(source_root: str | Path, rel_path: Any) -> tuple[Path, str, Path]:
    root = Path(source_root).expanduser().resolve()
    rel = _clean_rel(rel_path)
    target = (root / Path(*rel.split("/"))).resolve() if rel else root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path_escapes_source_root") from exc
    return root, rel, target


def _guess_mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _resource_kind_for_path(path: Path) -> str:
    return "folder" if path.is_dir() else "file"


def _capabilities_for_kind(resource_kind: str, value: Any = None) -> tuple[str, ...]:
    default = FOLDER_READ_ONLY_CAPABILITIES if resource_kind == "folder" else READ_ONLY_CAPABILITIES
    return normalize_public_capabilities(value if value is not None else default, resource_kind=resource_kind)


def _record_resource_kind(record: Mapping[str, Any]) -> str:
    resource = record.get("resource")
    if isinstance(resource, Mapping):
        kind = str(resource.get("kind") or "").strip().lower()
        if kind in {"file", "folder"}:
            return kind
    kind = str(record.get("resource_kind") or "").strip().lower()
    if kind in {"file", "folder"}:
        return kind
    mime_type = str(record.get("mime_type") or "").strip().lower()
    return "folder" if mime_type == "inode/directory" else "file"


def public_file_response_metadata(record: Mapping[str, Any], target: Path) -> tuple[str, str]:
    filename = target.name or str(record.get("filename") or record.get("name") or "download")
    record_mime = str(record.get("mime_type") or record.get("mime") or "").strip()
    record_filename = str(record.get("filename") or record.get("name") or "").strip()
    if (
        _record_resource_kind(record) == "file"
        and record_filename
        and filename == record_filename
        and record_mime
        and record_mime.lower() != "inode/directory"
    ):
        return filename, record_mime
    return filename, _guess_mime(filename)


def _public_label(value: Any) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:_PUBLIC_LABEL_MAX]


def _public_owner_name(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    for source in (record, metadata_map):
        for key in (
            "assistant_name",
            "subnet_name",
            "subnet_display_name",
            "owner_name",
            "display_name",
        ):
            label = _public_label(source.get(key))
            if label:
                return label
    return ""


def _public_record(record: Mapping[str, Any], *, include_public_token: bool = False, include_routing: bool = False) -> dict[str, Any]:
    resource_kind = _record_resource_kind(record)
    capabilities = _capabilities_for_kind(resource_kind, record.get("capabilities"))
    name = str(record.get("filename") or record.get("name") or "").strip()
    owner_name = _public_owner_name(record)
    grant = public_grant_descriptor(
        grant_kind=str(record.get("grant_kind") or DRIVE_PUBLIC_GRANT_KIND),
        face_id=str(record.get("face_id") or DRIVE_PUBLIC_FACE_ID),
        resource_kind=resource_kind,
        resource_name=name,
        capabilities=capabilities,
        readonly=True,
        status=str(record.get("status") or "active"),
        expires_at=record.get("expires_at"),
        metadata=record.get("grant_metadata") if isinstance(record.get("grant_metadata"), Mapping) else None,
    )
    if owner_name:
        grant["owner"] = {"name": owner_name}
    payload = {
        "schema": DRIVE_PUBLIC_LINK_SCHEMA,
        "grant_schema": PUBLIC_GRANT_SCHEMA,
        "grant_kind": grant["grant_kind"],
        "public_face": {
            "id": DRIVE_PUBLIC_FACE_ID,
            "skill": DRIVE_PUBLIC_LINK_SKILL,
            "kind": "files",
            "mode": "readonly",
        },
        "grant": grant,
        "id": str(record.get("public_token_hint") or record.get("id") or ""),
        "skill": DRIVE_PUBLIC_LINK_SKILL,
        "status": str(record.get("status") or "active"),
        "resource_kind": resource_kind,
        "readonly": True,
        "capabilities": list(capabilities),
        "filename": name,
        "name": name,
        "size_bytes": int(record.get("size_bytes") or 0),
        "mime_type": str(record.get("mime_type") or ("inode/directory" if resource_kind == "folder" else "application/octet-stream")),
        "modified_at": record.get("modified_at"),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
        "zone": str(record.get("zone") or record.get("zone_id") or ""),
        "url": str(record.get("url") or ""),
        "view_url": str(record.get("view_url") or ""),
        "download_url": str(record.get("download_url") or ""),
        "root_download_url": str(record.get("root_download_url") or ""),
        "list_url": str(record.get("list_url") or ""),
    }
    if owner_name:
        payload["assistant_name"] = owner_name
        payload["subnet_name"] = owner_name
        payload["owner_name"] = owner_name
        payload["public_face"]["owner"] = {"name": owner_name}
    if include_public_token:
        payload["public_token"] = str(record.get("public_token") or "")
    if include_routing:
        payload["subnet_id"] = str(record.get("subnet_id") or record.get("hub_id") or "")
        payload["hub_id"] = str(record.get("hub_id") or record.get("subnet_id") or "")
        payload["node_id"] = str(record.get("node_id") or "")
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping) and metadata:
        payload["metadata"] = dict(metadata)
    return payload


def register_hub_public_link(
    *,
    public_token: str,
    hub_token: str,
    source_root: str | Path,
    rel_path: Any,
    source_id: str = "",
    source_label: str = "",
    subnet_id: str = "",
    node_id: str = "",
    zone: str = "",
    assistant_name: str = "",
    subnet_name: str = "",
    ttl_seconds: Any = None,
    expires_at: Any = None,
    capabilities: Any = None,
    ctx: AgentContext | None = None,
) -> dict[str, Any]:
    token = validate_public_token(public_token)
    grant = validate_hub_token(hub_token)
    root, rel, target = _target_under_root(source_root, rel_path)
    if not target.exists() or not (target.is_file() or target.is_dir()):
        raise FileNotFoundError("selected item is not available")
    stat = target.stat()
    resource_kind = _resource_kind_for_path(target)
    caps = _capabilities_for_kind(resource_kind, capabilities)
    expires_epoch, expires_iso = _coerce_expires_at(ttl_seconds=ttl_seconds, expires_at=expires_at)
    now = _now()
    record = {
        "schema": DRIVE_PUBLIC_LINK_SCHEMA,
        "grant_schema": PUBLIC_GRANT_SCHEMA,
        "grant_kind": DRIVE_PUBLIC_GRANT_KIND,
        "face_id": DRIVE_PUBLIC_FACE_ID,
        "record_scope": "hub",
        "public_token_hash": _token_hash(token),
        "public_token_hint": token[:8],
        "public_token": token,
        "hub_token_hash": _token_hash(grant),
        "skill": DRIVE_PUBLIC_LINK_SKILL,
        "source_id": str(source_id or "").strip(),
        "source_label": str(source_label or "").strip(),
        "source_root": str(root),
        "rel_path": rel,
        "resource_kind": resource_kind,
        "resource": {
            "kind": resource_kind,
            "name": target.name or str(source_label or "Shared folder"),
            "path": rel,
        },
        "readonly": True,
        "capabilities": list(caps),
        "filename": target.name or str(source_label or "Shared folder"),
        "size_bytes": int(stat.st_size) if target.is_file() else 0,
        "mime_type": _guess_mime(target.name) if target.is_file() else "inode/directory",
        "modified_at": _iso_utc(float(stat.st_mtime)),
        "modified_epoch": float(stat.st_mtime),
        "created_at": _iso_utc(now),
        "created_epoch": now,
        "expires_at": expires_iso,
        "expires_at_epoch": expires_epoch,
        "status": "active",
        "subnet_id": str(subnet_id or "").strip(),
        "hub_id": str(subnet_id or "").strip(),
        "node_id": str(node_id or "").strip(),
        "zone": str(zone or "").strip().lower(),
    }
    owner_name = _public_label(assistant_name) or _public_label(subnet_name)
    if owner_name:
        record["assistant_name"] = owner_name
        record["subnet_name"] = owner_name
        record["owner_name"] = owner_name
    data = _load_store("hub", ctx)
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    links[record["public_token_hash"]] = record
    data["links"] = links
    _save_store("hub", data, ctx)
    return _public_record(record, include_public_token=True, include_routing=True)


def register_root_public_link(payload: Mapping[str, Any], *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = validate_public_token(payload.get("public_token"))
    hub_token = validate_hub_token(payload.get("hub_token"))
    subnet_id = str(payload.get("subnet_id") or payload.get("hub_id") or "").strip()
    if not subnet_id:
        raise ValueError("subnet_id_required")
    skill = str(payload.get("skill") or DRIVE_PUBLIC_LINK_SKILL).strip()
    if skill != DRIVE_PUBLIC_LINK_SKILL:
        raise ValueError("unsupported_drive_public_link_skill")
    resource_kind = str(payload.get("resource_kind") or "").strip().lower()
    if resource_kind not in {"file", "folder"}:
        resource = payload.get("resource")
        resource_kind = str(resource.get("kind") or "").strip().lower() if isinstance(resource, Mapping) else ""
    if resource_kind not in {"file", "folder"}:
        mime_type0 = str(payload.get("mime_type") or payload.get("mime") or "").strip().lower()
        resource_kind = "folder" if mime_type0 == "inode/directory" else "file"
    caps = _capabilities_for_kind(resource_kind, payload.get("capabilities"))
    expires_epoch, expires_iso = _coerce_expires_at(ttl_seconds=payload.get("ttl_seconds"), expires_at=payload.get("expires_at"))
    now = _now()
    name = str(payload.get("filename") or payload.get("name") or "").strip()
    owner_name = _public_owner_name(payload)
    record = {
        "schema": DRIVE_PUBLIC_LINK_SCHEMA,
        "grant_schema": PUBLIC_GRANT_SCHEMA,
        "grant_kind": str(payload.get("grant_kind") or DRIVE_PUBLIC_GRANT_KIND).strip() or DRIVE_PUBLIC_GRANT_KIND,
        "face_id": str(payload.get("face_id") or DRIVE_PUBLIC_FACE_ID).strip() or DRIVE_PUBLIC_FACE_ID,
        "record_scope": "root",
        "public_token_hash": _token_hash(token),
        "public_token_hint": token[:8],
        "public_token": token,
        "hub_token": hub_token,
        "skill": DRIVE_PUBLIC_LINK_SKILL,
        "subnet_id": subnet_id,
        "hub_id": subnet_id,
        "node_id": str(payload.get("node_id") or "").strip(),
        "zone": str(payload.get("zone") or payload.get("zone_id") or "").strip().lower(),
        "resource_kind": resource_kind,
        "resource": {
            "kind": resource_kind,
            "name": name,
            "path": "",
        },
        "readonly": True,
        "capabilities": list(caps),
        "filename": name,
        "size_bytes": int(payload.get("size_bytes") or 0),
        "mime_type": str(payload.get("mime_type") or payload.get("mime") or ("inode/directory" if resource_kind == "folder" else "application/octet-stream")).strip() or "application/octet-stream",
        "modified_at": payload.get("modified_at"),
        "created_at": _iso_utc(now),
        "created_epoch": now,
        "expires_at": expires_iso,
        "expires_at_epoch": expires_epoch,
        "status": str(payload.get("status") or "active").strip() or "active",
        "url": str(payload.get("url") or "").strip(),
        "view_url": str(payload.get("view_url") or "").strip(),
        "download_url": str(payload.get("download_url") or "").strip(),
        "root_download_url": str(payload.get("root_download_url") or "").strip(),
        "list_url": str(payload.get("list_url") or "").strip(),
    }
    if owner_name:
        record["assistant_name"] = owner_name
        record["subnet_name"] = owner_name
        record["owner_name"] = owner_name
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        record["metadata"] = dict(metadata)
    data = _load_store("root", ctx)
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    links[record["public_token_hash"]] = record
    data["links"] = links
    _save_store("root", data, ctx)
    return _public_record(record, include_public_token=True, include_routing=True)


def _lookup_record(kind: str, public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = validate_public_token(public_token)
    data = _load_store(kind, ctx)
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    record = links.get(_token_hash(token))
    if not isinstance(record, dict):
        raise DrivePublicLinkNotFound("drive public link not found")
    return record


def _resolve_record(kind: str, public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    record = _lookup_record(kind, public_token, ctx=ctx)
    status = str(record.get("status") or "active").strip().lower()
    if status not in {"active", "ready"}:
        raise DrivePublicLinkForbidden("drive public link is not active")
    expires_epoch = float(record.get("expires_at_epoch") or 0.0)
    if expires_epoch and expires_epoch <= _now():
        raise DrivePublicLinkExpired("drive public link expired")
    return record


def _update_record(kind: str, public_token: str, update: Mapping[str, Any], *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = validate_public_token(public_token)
    data = _load_store(kind, ctx)
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    key = _token_hash(token)
    current = links.get(key)
    if not isinstance(current, dict):
        raise DrivePublicLinkNotFound("drive public link not found")
    next_record = dict(current)
    next_record.update(dict(update))
    links[key] = next_record
    data["links"] = links
    _save_store(kind, data, ctx)
    return next_record


def revoke_hub_public_link(public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    record = _update_record("hub", public_token, {"status": "revoked", "revoked_at": _iso_utc()}, ctx=ctx)
    return _public_record(record, include_public_token=True, include_routing=True)


def revoke_root_public_link(public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    record = _update_record("root", public_token, {"status": "revoked", "revoked_at": _iso_utc()}, ctx=ctx)
    return _public_record(record, include_public_token=True, include_routing=True)


def _download_event_path(record: Mapping[str, Any], rel_path: Any, filename: str) -> str:
    try:
        rel = _clean_rel(rel_path)
    except Exception:
        rel = _limited_text(rel_path, max_len=300).replace("\\", "/").lstrip("/")
    if rel:
        return rel
    if _record_resource_kind(record) == "file":
        return str(record.get("rel_path") or filename or record.get("filename") or "").strip()
    return str(filename or record.get("filename") or "").strip()


def _download_summary_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "public_token_hint": str(record.get("public_token_hint") or ""),
        "name": str(record.get("filename") or record.get("name") or ""),
        "resource_kind": _record_resource_kind(record),
        "events_total": 0,
        "started_total": 0,
        "completed_total": 0,
        "failed_total": 0,
        "aborted_total": 0,
        "download_started_total": 0,
        "download_completed_total": 0,
        "download_failed_total": 0,
        "download_aborted_total": 0,
        "preview_started_total": 0,
        "preview_completed_total": 0,
        "bytes_completed": 0,
        "last_at": None,
        "last_status": "",
        "last_error": "",
        "by_error": {},
        "files": {},
    }


def _download_public_token_hash(record: Mapping[str, Any]) -> str:
    token_hash = str(record.get("public_token_hash") or "").strip()
    if token_hash:
        return token_hash
    token = str(record.get("public_token") or "").strip()
    return _token_hash(token) if token else ""


def _coerce_download_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in {"started", "completed", "failed", "aborted"} else "started"


def _coerce_download_action(value: Any, *, download: bool | None = None) -> str:
    action = str(value or "").strip().lower()
    if action in {"download", "preview", "content", "metadata"}:
        return action
    if download is not None:
        return "download" if download else "preview"
    return "download"


def record_hub_public_download_event(
    record: Mapping[str, Any],
    *,
    status: str,
    action: str = "",
    download: bool | None = None,
    rel_path: Any = "",
    target: Path | None = None,
    filename: str = "",
    size_bytes: Any = None,
    bytes_sent: Any = None,
    status_code: Any = None,
    error: Any = "",
    reason: Any = "",
    phase: str = "",
    method: str = "",
    request: Request | None = None,
    request_headers: Mapping[str, Any] | None = None,
    search: str = "",
    ctx: AgentContext | None = None,
) -> dict[str, Any] | None:
    token_hash = _download_public_token_hash(record)
    if not token_hash:
        return None
    now = _now()
    status0 = _coerce_download_status(status)
    action0 = _coerce_download_action(action, download=download)
    filename0 = str(filename or (target.name if target is not None else "") or record.get("filename") or "download").strip()
    try:
        size0 = int(size_bytes if size_bytes is not None else (target.stat().st_size if target is not None and target.exists() else record.get("size_bytes") or 0))
    except Exception:
        size0 = 0
    try:
        bytes0 = int(bytes_sent) if bytes_sent is not None else None
    except Exception:
        bytes0 = None
    try:
        status_code0 = int(status_code) if status_code is not None else None
    except Exception:
        status_code0 = None
    ctx0 = _download_request_context(request=request, request_headers=request_headers, search=search, method=method)
    path0 = _download_event_path(record, rel_path, filename0)
    event = {
        "id": secrets.token_hex(8),
        "schema": "adaos.drive.public_download_event.v1",
        "at": _iso_utc(now),
        "epoch": now,
        "public_token_hint": str(record.get("public_token_hint") or ""),
        "public_token_hash": token_hash,
        "action": action0,
        "status": status0,
        "phase": _limited_text(phase or ("stream" if status0 in {"started", "completed", "aborted"} else "resolve"), max_len=80),
        "method": ctx0.get("method") or "GET",
        "path": path0,
        "filename": filename0,
        "size_bytes": size0,
        "bytes_sent": bytes0,
        "status_code": status_code0,
        "error": _limited_text(error),
        "reason": _limited_text(reason),
        "guest_device_id": ctx0.get("guest_device_id") or "",
        "client_ip_hash": ctx0.get("client_ip_hash") or "",
        "user_agent": ctx0.get("user_agent") or "",
        "referer": ctx0.get("referer") or "",
        "range": ctx0.get("range") or "",
    }
    with _DOWNLOAD_LOCK:
        data = _load_download_summary_store(ctx)
        summaries = data.get("summaries") if isinstance(data.get("summaries"), dict) else {}
        summary = summaries.get(token_hash) if isinstance(summaries.get(token_hash), dict) else _download_summary_from_record(record)
        summary["public_token_hint"] = str(record.get("public_token_hint") or summary.get("public_token_hint") or "")
        summary["name"] = str(record.get("filename") or summary.get("name") or "")
        summary["resource_kind"] = _record_resource_kind(record)
        summary["events_total"] = int(summary.get("events_total") or 0) + 1
        status_key = f"{status0}_total"
        summary[status_key] = int(summary.get(status_key) or 0) + 1
        action_status_key = f"{action0}_{status0}_total"
        summary[action_status_key] = int(summary.get(action_status_key) or 0) + 1
        if status0 == "completed" and bytes0 is not None:
            summary["bytes_completed"] = int(summary.get("bytes_completed") or 0) + max(0, bytes0)
        summary["last_at"] = event["at"]
        summary["last_status"] = status0
        summary["last_error"] = event["error"] if status0 == "failed" else ""
        if status0 == "failed" and event["error"]:
            by_error = summary.get("by_error") if isinstance(summary.get("by_error"), dict) else {}
            by_error[event["error"]] = int(by_error.get(event["error"]) or 0) + 1
            summary["by_error"] = by_error
        files = summary.get("files") if isinstance(summary.get("files"), dict) else {}
        file_key = path0 or filename0 or "__root__"
        file_summary = files.get(file_key) if isinstance(files.get(file_key), dict) else {
            "path": file_key,
            "filename": filename0,
            "started_total": 0,
            "completed_total": 0,
            "failed_total": 0,
            "aborted_total": 0,
            "bytes_completed": 0,
            "last_at": None,
            "last_status": "",
        }
        file_summary["filename"] = filename0
        file_summary[status_key] = int(file_summary.get(status_key) or 0) + 1
        if status0 == "completed" and bytes0 is not None:
            file_summary["bytes_completed"] = int(file_summary.get("bytes_completed") or 0) + max(0, bytes0)
        file_summary["last_at"] = event["at"]
        file_summary["last_status"] = status0
        files[file_key] = file_summary
        summary["files"] = files
        summaries[token_hash] = summary
        data["summaries"] = summaries
        data["schema"] = _DOWNLOAD_STORE_SCHEMA
        data["v"] = 1
        _append_download_event(event, ctx)
        _save_download_summary_store(data, ctx)
    return event


def record_hub_public_download_failure(
    public_token: str,
    *,
    error: Any,
    status_code: Any = None,
    action: str = "",
    download: bool | None = None,
    rel_path: Any = "",
    reason: Any = "",
    phase: str = "resolve",
    method: str = "",
    request: Request | None = None,
    request_headers: Mapping[str, Any] | None = None,
    search: str = "",
    ctx: AgentContext | None = None,
) -> dict[str, Any] | None:
    try:
        record = _lookup_record("hub", public_token, ctx=ctx)
    except Exception:
        return None
    return record_hub_public_download_event(
        record,
        status="failed",
        action=action,
        download=download,
        rel_path=rel_path,
        status_code=status_code,
        error=error,
        reason=reason,
        phase=phase,
        method=method,
        request=request,
        request_headers=request_headers,
        search=search,
        ctx=ctx,
    )


def _download_summary_for_record(record: Mapping[str, Any], data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    token_hash = _download_public_token_hash(record)
    summaries = data.get("summaries") if isinstance(data, Mapping) and isinstance(data.get("summaries"), dict) else {}
    summary = summaries.get(token_hash) if token_hash and isinstance(summaries.get(token_hash), dict) else {}
    merged = _download_summary_from_record(record)
    merged.update(dict(summary))
    return merged


def list_hub_public_download_events(
    public_token: str = "",
    *,
    limit: Any = 100,
    ctx: AgentContext | None = None,
) -> dict[str, Any]:
    try:
        max_items = max(1, min(500, int(limit or 100)))
    except Exception:
        max_items = 100
    token_hash = ""
    summary: dict[str, Any] = {}
    if str(public_token or "").strip():
        record = _lookup_record("hub", public_token, ctx=ctx)
        token_hash = _download_public_token_hash(record)
    with _DOWNLOAD_LOCK:
        data = _load_download_summary_store(ctx)
        events0 = _read_recent_download_events(ctx, limit=_DOWNLOAD_RECENT_MAX)
        summaries = data.get("summaries") if isinstance(data.get("summaries"), dict) else {}
        if token_hash:
            events = [dict(item) for item in events0 if isinstance(item, Mapping) and str(item.get("public_token_hash") or "") == token_hash]
            summary = dict(summaries.get(token_hash) or {})
        else:
            events = [dict(item) for item in events0 if isinstance(item, Mapping)]
            summary = {
                "links_total": len(summaries),
                "events_total": sum(int(item.get("events_total") or 0) for item in summaries.values() if isinstance(item, Mapping)),
                "completed_total": sum(int(item.get("completed_total") or 0) for item in summaries.values() if isinstance(item, Mapping)),
                "failed_total": sum(int(item.get("failed_total") or 0) for item in summaries.values() if isinstance(item, Mapping)),
                "aborted_total": sum(int(item.get("aborted_total") or 0) for item in summaries.values() if isinstance(item, Mapping)),
                "bytes_completed": sum(int(item.get("bytes_completed") or 0) for item in summaries.values() if isinstance(item, Mapping)),
            }
    return {
        "ok": True,
        "schema": _DOWNLOAD_STORE_SCHEMA,
        "summary": summary,
        "events": events[:max_items],
    }


def list_hub_public_links(
    *,
    limit: Any = None,
    include_download_stats: bool = True,
    ctx: AgentContext | None = None,
) -> list[dict[str, Any]]:
    data = _load_store("hub", ctx)
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    records = (record for record in links.values() if isinstance(record, Mapping))
    bounded_limit: int | None = None
    if limit is not None:
        try:
            bounded_limit = max(1, min(500, int(limit)))
        except Exception:
            bounded_limit = 100
        records = iter(
            heapq.nlargest(
                bounded_limit,
                records,
                key=lambda record: str(record.get("created_at") or ""),
            )
        )
    download_data = _load_download_summary_store(ctx) if include_download_stats else {}
    out: list[dict[str, Any]] = []
    for record in records:
        item = _public_record(record, include_public_token=True, include_routing=True)
        item["source_id"] = str(record.get("source_id") or "")
        item["source_label"] = str(record.get("source_label") or "")
        item["rel_path"] = str(record.get("rel_path") or "")
        item["revoked_at"] = record.get("revoked_at")
        if include_download_stats:
            stats = _download_summary_for_record(record, download_data)
            item["download_stats"] = stats
            item["download_summary"] = (
                f"{int(stats.get('download_completed_total') or 0)} downloads, "
                f"{int(stats.get('failed_total') or 0)} failures, "
                f"{int(stats.get('aborted_total') or 0)} aborted"
            )
        out.append(item)
    if bounded_limit is None:
        out.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return out


def resolve_root_public_link(public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    return _resolve_record("root", public_token, ctx=ctx)


def root_public_link_metadata(public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    record = resolve_root_public_link(public_token, ctx=ctx)
    return _public_record(record, include_public_token=False, include_routing=False)


def resolve_hub_public_link(
    public_token: str,
    hub_token: str,
    *,
    rel_path: Any = "",
    require_file: bool = True,
    ctx: AgentContext | None = None,
) -> tuple[dict[str, Any], Path]:
    token = validate_public_token(public_token)
    grant = validate_hub_token(hub_token)
    record = _resolve_record("hub", token, ctx=ctx)
    expected_hash = str(record.get("hub_token_hash") or "")
    if not expected_hash or not hmac.compare_digest(expected_hash, _token_hash(grant)):
        raise DrivePublicLinkForbidden("drive public link token mismatch")
    root, _rel, target = _target_under_root(str(record.get("source_root") or ""), record.get("rel_path") or "")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DrivePublicLinkForbidden("drive public link path escaped source root") from exc
    resource_kind = _record_resource_kind(record)
    child_rel = _clean_rel(rel_path)
    if child_rel:
        if resource_kind != "folder":
            raise DrivePublicLinkForbidden("drive public link child paths require a folder grant")
        grant_root = target
        target = (grant_root / Path(*child_rel.split("/"))).resolve()
        try:
            target.relative_to(grant_root)
        except ValueError as exc:
            raise DrivePublicLinkForbidden("drive public link path escaped grant root") from exc
    if not target.exists():
        raise DrivePublicLinkNotFound("drive public link target not found")
    if require_file and not target.is_file():
        raise DrivePublicLinkForbidden("drive public link target is not a file")
    return record, target


def _public_relative_path(record: Mapping[str, Any], target: Path) -> str:
    _root, _rel, grant_target = _target_under_root(str(record.get("source_root") or ""), record.get("rel_path") or "")
    try:
        rel = target.resolve().relative_to(grant_target.resolve())
    except ValueError:
        return ""
    return "" if str(rel) == "." else rel.as_posix()


def _human_size(value: int | None) -> str:
    if value is None:
        return ""
    size = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            text = f"{size:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        size /= 1024
    return f"{int(value)} B"


def _public_item(record: Mapping[str, Any], target: Path, *, is_parent: bool = False, parent_path: str = "") -> dict[str, Any]:
    if is_parent:
        return {
            "id": "__parent__",
            "name": "..",
            "extension": "",
            "path": parent_path,
            "kind": "parent",
            "is_dir": True,
            "is_file": False,
            "is_parent": True,
            "size": "",
            "size_bytes": None,
            "modified_at": None,
            "mime_type": "inode/directory",
            "can_expand": True,
            "can_preview": False,
            "can_download": False,
        }
    stat = target.stat()
    is_dir = target.is_dir()
    rel = _public_relative_path(record, target)
    size_bytes = None if is_dir else int(stat.st_size)
    suffix = "" if is_dir else target.suffix.lower().lstrip(".")
    return {
        "id": rel or "__root__",
        "name": target.name or str(record.get("filename") or "Shared files"),
        "extension": suffix,
        "path": rel,
        "kind": "folder" if is_dir else "file",
        "is_dir": is_dir,
        "is_file": target.is_file(),
        "is_parent": False,
        "size": _human_size(size_bytes),
        "size_bytes": size_bytes,
        "modified_at": _iso_utc(float(stat.st_mtime)),
        "mime_type": "inode/directory" if is_dir else _guess_mime(target.name),
        "can_expand": is_dir,
        "can_preview": target.is_file(),
        "can_download": target.is_file(),
    }


def _breadcrumbs(path: str) -> list[dict[str, str]]:
    items = [{"name": "Shared root", "path": ""}]
    parts: list[str] = []
    for part in _clean_rel(path).split("/"):
        if not part:
            continue
        parts.append(part)
        items.append({"name": part, "path": "/".join(parts)})
    return items


def list_hub_public_link(
    public_token: str,
    hub_token: str,
    *,
    rel_path: Any = "",
    limit: Any = 500,
    ctx: AgentContext | None = None,
) -> dict[str, Any]:
    record, target = resolve_hub_public_link(public_token, hub_token, rel_path=rel_path, require_file=False, ctx=ctx)
    current_rel = _public_relative_path(record, target)
    try:
        max_items = max(1, min(500, int(limit or 500)))
    except Exception:
        max_items = 500
    items: list[dict[str, Any]] = []
    truncated = False
    if target.is_file():
        items.append(_public_item(record, target))
    else:
        if current_rel:
            parent_rel = "/".join(current_rel.split("/")[:-1])
            items.append(_public_item(record, target.parent, is_parent=True, parent_path=parent_rel))
        try:
            children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except PermissionError as exc:
            raise DrivePublicLinkForbidden("drive public link folder is not readable") from exc
        truncated = len(children) > max_items
        for child in children[:max_items]:
            try:
                items.append(_public_item(record, child))
            except OSError:
                continue
    return {
        "ok": True,
        "schema": "adaos.drive.public_listing.v1",
        "link": _public_record(record, include_public_token=False, include_routing=False),
        "path": current_rel,
        "breadcrumbs": _breadcrumbs(current_rel),
        "items": items,
        "truncated": truncated,
        "readonly": True,
        "updated_at": _iso_utc(),
    }


def _attachment_content_disposition(filename: str) -> str:
    safe = str(filename or "download").replace("\\", "_").replace("/", "_")
    safe = safe.replace('"', "'").replace("\r", "").replace("\n", "").strip() or "download"
    encoded = quote(safe, safe="")
    return f'attachment; filename="{safe}"; filename*=UTF-8\'\'{encoded}'


def _public_download_file_iter(
    record: Mapping[str, Any],
    target: Path,
    *,
    start: int,
    end: int,
    filename: str,
    size: int,
    status_code: int,
    action: str,
    rel_path: Any,
    request: Request,
    ctx: AgentContext | None = None,
):
    sent = 0
    try:
        for chunk in file_range_iter(target, start=start, end=end):
            sent += len(chunk)
            yield chunk
    except GeneratorExit:
        record_hub_public_download_event(
            record,
            status="aborted",
            action=action,
            rel_path=rel_path,
            target=target,
            filename=filename,
            size_bytes=size,
            bytes_sent=sent,
            status_code=status_code,
            phase="stream",
            request=request,
            ctx=ctx,
        )
        raise
    except Exception as exc:
        record_hub_public_download_event(
            record,
            status="failed",
            action=action,
            rel_path=rel_path,
            target=target,
            filename=filename,
            size_bytes=size,
            bytes_sent=sent,
            status_code=502,
            error=type(exc).__name__,
            reason=str(exc),
            phase="stream",
            request=request,
            ctx=ctx,
        )
        raise
    else:
        record_hub_public_download_event(
            record,
            status="completed",
            action=action,
            rel_path=rel_path,
            target=target,
            filename=filename,
            size_bytes=size,
            bytes_sent=sent,
            status_code=status_code,
            phase="stream",
            request=request,
            ctx=ctx,
        )


def stream_hub_public_link(
    public_token: str,
    hub_token: str,
    request: Request,
    *,
    download: bool = False,
    rel_path: Any = "",
    ctx: AgentContext | None = None,
) -> StreamingResponse | Response:
    requested_path = rel_path if rel_path not in (None, "") else request.query_params.get("path", "")
    action = "download" if download else "preview"
    try:
        record, target = resolve_hub_public_link(public_token, hub_token, rel_path=requested_path, require_file=True, ctx=ctx)
    except Exception as exc:
        status_code, detail = map_public_link_exception(exc)
        if request.method.upper() != "HEAD":
            record_hub_public_download_failure(
                public_token,
                error=detail,
                status_code=status_code,
                action=action,
                rel_path=requested_path,
                phase="resolve",
                request=request,
                ctx=ctx,
            )
        raise
    size = int(target.stat().st_size)
    try:
        byte_range = parse_media_range(request.headers.get("range"), size=size)
    except Exception:
        if request.method.upper() != "HEAD":
            filename0, _mime0 = public_file_response_metadata(record, target)
            record_hub_public_download_event(
                record,
                status="failed",
                action=action,
                rel_path=requested_path,
                target=target,
                filename=filename0,
                size_bytes=size,
                bytes_sent=0,
                status_code=416,
                error="invalid_range",
                phase="range",
                request=request,
                ctx=ctx,
            )
        return Response(
            status_code=416,
            content=b"",
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
                "Content-Range": f"bytes */{size}",
                "Content-Length": "0",
            },
            media_type="text/plain",
        )
    filename, mime_type = public_file_response_metadata(record, target)
    status_code, _reason, headers, start, end = media_content_response_parts(
        filename=filename,
        mime_type=mime_type,
        size=size,
        byte_range=byte_range,
        include_content_type=False,
    )
    headers["Cache-Control"] = "no-store"
    headers["X-AdaOS-Resource-Scope"] = "drive-public-link"
    headers["X-AdaOS-Drive-Public-Link"] = str(record.get("public_token_hint") or "")
    if download:
        headers["Content-Disposition"] = _attachment_content_disposition(filename)
    if request.method.upper() == "HEAD" or int(headers.get("Content-Length") or 0) <= 0:
        if request.method.upper() != "HEAD":
            record_hub_public_download_event(
                record,
                status="started",
                action=action,
                rel_path=requested_path,
                target=target,
                filename=filename,
                size_bytes=size,
                bytes_sent=0,
                status_code=status_code,
                phase="stream",
                request=request,
                ctx=ctx,
            )
            record_hub_public_download_event(
                record,
                status="completed",
                action=action,
                rel_path=requested_path,
                target=target,
                filename=filename,
                size_bytes=size,
                bytes_sent=0,
                status_code=status_code,
                phase="stream",
                request=request,
                ctx=ctx,
            )
        return Response(status_code=status_code, headers=headers, media_type=mime_type)
    record_hub_public_download_event(
        record,
        status="started",
        action=action,
        rel_path=requested_path,
        target=target,
        filename=filename,
        size_bytes=size,
        status_code=status_code,
        phase="stream",
        request=request,
        ctx=ctx,
    )
    return StreamingResponse(
        _public_download_file_iter(
            record,
            target,
            start=start,
            end=end,
            filename=filename,
            size=size,
            status_code=status_code,
            action=action,
            rel_path=requested_path,
            request=request,
            ctx=ctx,
        ),
        status_code=status_code,
        media_type=mime_type,
        headers=headers,
    )


def map_public_link_exception(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, DrivePublicLinkExpired):
        return 410, "drive_public_link_expired"
    if isinstance(exc, DrivePublicLinkForbidden):
        return 403, "drive_public_link_forbidden"
    if isinstance(exc, DrivePublicLinkNotFound):
        return 404, "drive_public_link_not_found"
    if isinstance(exc, ValueError):
        return 400, str(exc) or "invalid_drive_public_link"
    return 500, "drive_public_link_failed"
