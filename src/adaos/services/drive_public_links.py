from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.media_core import file_range_iter, media_content_response_parts, parse_media_range


DRIVE_PUBLIC_LINK_SCHEMA = "adaos.drive.public_link.v1"
DRIVE_PUBLIC_LINK_SKILL = "adaos_drive"
DRIVE_PUBLIC_LINK_CONTENT_PATH_PREFIX = f"/api/skills/{DRIVE_PUBLIC_LINK_SKILL}/public-links"
DRIVE_PUBLIC_LINK_ROOT_PATH_PREFIX = "/v1/drive/public-links"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_MAX_TTL_SECONDS = 90 * 24 * 3600


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


def build_root_public_content_url(base_url: str, public_token: str, *, download: bool = False) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url_required")
    token = validate_public_token(public_token)
    url = f"{base}{DRIVE_PUBLIC_LINK_ROOT_PATH_PREFIX}/{quote(token, safe='')}/content"
    params: list[tuple[str, str]] = []
    if download:
        params.append(("download", "1"))
    return f"{url}?{urlencode(params)}" if params else url


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


def _public_record(record: Mapping[str, Any], *, include_public_token: bool = False, include_routing: bool = False) -> dict[str, Any]:
    payload = {
        "schema": DRIVE_PUBLIC_LINK_SCHEMA,
        "id": str(record.get("public_token_hint") or record.get("id") or ""),
        "skill": DRIVE_PUBLIC_LINK_SKILL,
        "status": str(record.get("status") or "active"),
        "filename": str(record.get("filename") or ""),
        "name": str(record.get("filename") or ""),
        "size_bytes": int(record.get("size_bytes") or 0),
        "mime_type": str(record.get("mime_type") or "application/octet-stream"),
        "modified_at": record.get("modified_at"),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
        "zone": str(record.get("zone") or record.get("zone_id") or ""),
        "url": str(record.get("url") or ""),
        "view_url": str(record.get("view_url") or ""),
        "download_url": str(record.get("download_url") or ""),
        "root_download_url": str(record.get("root_download_url") or ""),
    }
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
    ttl_seconds: Any = None,
    expires_at: Any = None,
    ctx: AgentContext | None = None,
) -> dict[str, Any]:
    token = validate_public_token(public_token)
    grant = validate_hub_token(hub_token)
    root, rel, target = _target_under_root(source_root, rel_path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("selected item is not a downloadable file")
    stat = target.stat()
    expires_epoch, expires_iso = _coerce_expires_at(ttl_seconds=ttl_seconds, expires_at=expires_at)
    now = _now()
    record = {
        "schema": DRIVE_PUBLIC_LINK_SCHEMA,
        "record_scope": "hub",
        "public_token_hash": _token_hash(token),
        "public_token_hint": token[:8],
        "hub_token_hash": _token_hash(grant),
        "skill": DRIVE_PUBLIC_LINK_SKILL,
        "source_id": str(source_id or "").strip(),
        "source_label": str(source_label or "").strip(),
        "source_root": str(root),
        "rel_path": rel,
        "filename": target.name,
        "size_bytes": int(stat.st_size),
        "mime_type": _guess_mime(target.name),
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
    expires_epoch, expires_iso = _coerce_expires_at(ttl_seconds=payload.get("ttl_seconds"), expires_at=payload.get("expires_at"))
    now = _now()
    record = {
        "schema": DRIVE_PUBLIC_LINK_SCHEMA,
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
        "filename": str(payload.get("filename") or payload.get("name") or "").strip(),
        "size_bytes": int(payload.get("size_bytes") or 0),
        "mime_type": str(payload.get("mime_type") or payload.get("mime") or "application/octet-stream").strip() or "application/octet-stream",
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
    }
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        record["metadata"] = dict(metadata)
    data = _load_store("root", ctx)
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    links[record["public_token_hash"]] = record
    data["links"] = links
    _save_store("root", data, ctx)
    return _public_record(record, include_public_token=True, include_routing=True)


def _resolve_record(kind: str, public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = validate_public_token(public_token)
    data = _load_store(kind, ctx)
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    record = links.get(_token_hash(token))
    if not isinstance(record, dict):
        raise DrivePublicLinkNotFound("drive public link not found")
    status = str(record.get("status") or "active").strip().lower()
    if status not in {"active", "ready"}:
        raise DrivePublicLinkForbidden("drive public link is not active")
    expires_epoch = float(record.get("expires_at_epoch") or 0.0)
    if expires_epoch and expires_epoch <= _now():
        raise DrivePublicLinkExpired("drive public link expired")
    return record


def resolve_root_public_link(public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    return _resolve_record("root", public_token, ctx=ctx)


def root_public_link_metadata(public_token: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    record = resolve_root_public_link(public_token, ctx=ctx)
    return _public_record(record, include_public_token=False, include_routing=False)


def resolve_hub_public_link(
    public_token: str,
    hub_token: str,
    *,
    ctx: AgentContext | None = None,
) -> tuple[dict[str, Any], Path]:
    token = validate_public_token(public_token)
    grant = validate_hub_token(hub_token)
    record = _resolve_record("hub", token, ctx=ctx)
    expected_hash = str(record.get("hub_token_hash") or "")
    if not expected_hash or not hmac.compare_digest(expected_hash, _token_hash(grant)):
        raise DrivePublicLinkForbidden("drive public link token mismatch")
    root, rel, target = _target_under_root(str(record.get("source_root") or ""), record.get("rel_path") or "")
    if not target.exists() or not target.is_file():
        raise DrivePublicLinkNotFound("drive public link file not found")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DrivePublicLinkForbidden("drive public link path escaped source root") from exc
    return record, target


def _attachment_content_disposition(filename: str) -> str:
    safe = str(filename or "download").replace("\\", "_").replace("/", "_")
    safe = safe.replace('"', "'").replace("\r", "").replace("\n", "").strip() or "download"
    encoded = quote(safe, safe="")
    return f'attachment; filename="{safe}"; filename*=UTF-8\'\'{encoded}'


def stream_hub_public_link(
    public_token: str,
    hub_token: str,
    request: Request,
    *,
    download: bool = False,
    ctx: AgentContext | None = None,
) -> StreamingResponse | Response:
    record, target = resolve_hub_public_link(public_token, hub_token, ctx=ctx)
    size = int(target.stat().st_size)
    try:
        byte_range = parse_media_range(request.headers.get("range"), size=size)
    except Exception:
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
    filename = str(record.get("filename") or target.name)
    mime_type = str(record.get("mime_type") or "").strip() or _guess_mime(filename)
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
        return Response(status_code=status_code, headers=headers, media_type=mime_type)
    return StreamingResponse(
        file_range_iter(target, start=start, end=end),
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

