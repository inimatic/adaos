from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from adaos.adapters.db import sqlite as sqlite_db

LinkKind = Literal["browser", "member"]

_NS = "access_links"
_KEY = "registry"


def _now_ts() -> float:
    return float(time.time())


def _iso_from_ts(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        token = float(value)
    except Exception:
        return None
    if token <= 0:
        return None
    return datetime.fromtimestamp(token, tz=timezone.utc).replace(microsecond=0).isoformat()


def _load_registry() -> dict[str, Any]:
    payload = sqlite_db.durable_state_get(_NS, _KEY) or {}
    browsers = payload.get("browsers")
    members = payload.get("members")
    return {
        "browsers": dict(browsers) if isinstance(browsers, Mapping) else {},
        "members": dict(members) if isinstance(members, Mapping) else {},
    }


def _save_registry(registry: Mapping[str, Any]) -> None:
    sqlite_db.durable_state_put(
        _NS,
        _KEY,
        {
            "browsers": dict(registry.get("browsers") or {}),
            "members": dict(registry.get("members") or {}),
        },
    )


def _entry_bucket(kind: LinkKind) -> str:
    return "browsers" if kind == "browser" else "members"


def _normalize_entry(kind: LinkKind, entry_id: str, raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = dict(raw or {})
    now = _now_ts()
    lifetime_mode = str(data.get("lifetime_mode") or "permanent").strip().lower() or "permanent"
    expires_at = data.get("expires_at")
    try:
        expires_at_value = float(expires_at) if expires_at is not None else None
    except Exception:
        expires_at_value = None
    access_class = str(data.get("access_class") or "").strip().lower()
    if access_class not in {"device", "client"}:
        access_class = "device" if lifetime_mode == "permanent" else "client"
    return {
        "id": entry_id,
        "kind": kind,
        "display_name": str(data.get("display_name") or "").strip(),
        "access_class": access_class,
        "lifetime_mode": "permanent" if lifetime_mode == "permanent" else "fixed",
        "expires_at": None if lifetime_mode == "permanent" else expires_at_value,
        "autorotate": bool(data.get("autorotate", True)),
        "revoked": bool(data.get("revoked", False)),
        "revoked_at": float(data.get("revoked_at") or 0.0) or None,
        "created_at": float(data.get("created_at") or now),
        "updated_at": float(data.get("updated_at") or now),
        "last_seen_at": float(data.get("last_seen_at") or 0.0) or None,
        "online": bool(data.get("online", False)),
        "last_webspace_id": str(data.get("last_webspace_id") or "").strip() or None,
        "connection_state": str(data.get("connection_state") or "").strip().lower() or None,
        "hostname": str(data.get("hostname") or "").strip() or None,
        "node_names": [
            str(item or "").strip()
            for item in list(data.get("node_names") or [])
            if str(item or "").strip()
        ],
    }


def _get_entry(registry: Mapping[str, Any], kind: LinkKind, entry_id: str) -> dict[str, Any] | None:
    token = str(entry_id or "").strip()
    if not token:
        return None
    bucket = registry.get(_entry_bucket(kind))
    if not isinstance(bucket, Mapping):
        return None
    raw = bucket.get(token)
    if not isinstance(raw, Mapping):
        return None
    return _normalize_entry(kind, token, raw)


def _put_entry(registry: dict[str, Any], kind: LinkKind, entry: Mapping[str, Any]) -> dict[str, Any]:
    token = str(entry.get("id") or "").strip()
    if not token:
        raise ValueError("entry id is required")
    bucket_name = _entry_bucket(kind)
    bucket = registry.setdefault(bucket_name, {})
    if not isinstance(bucket, dict):
        bucket = {}
        registry[bucket_name] = bucket
    normalized = _normalize_entry(kind, token, entry)
    bucket[token] = normalized
    return normalized


def _delete_entry(registry: dict[str, Any], kind: LinkKind, entry_id: str) -> None:
    bucket = registry.get(_entry_bucket(kind))
    if isinstance(bucket, dict):
        bucket.pop(str(entry_id or "").strip(), None)


def _is_expired(entry: Mapping[str, Any], *, now: float | None = None) -> bool:
    expires_at = entry.get("expires_at")
    if expires_at in (None, "", 0, 0.0):
        return False
    try:
        expiry = float(expires_at)
    except Exception:
        return False
    return expiry > 0 and expiry <= float(now if now is not None else _now_ts())


def _purge_expired_browsers(registry: dict[str, Any]) -> None:
    bucket = registry.get("browsers")
    if not isinstance(bucket, dict):
        return
    now = _now_ts()
    for entry_id in list(bucket.keys()):
        entry = _normalize_entry("browser", entry_id, bucket.get(entry_id))
        if _is_expired(entry, now=now):
            bucket.pop(entry_id, None)


def _updated(entry: dict[str, Any]) -> dict[str, Any]:
    entry["updated_at"] = _now_ts()
    return entry


def get_link(kind: LinkKind, entry_id: str) -> dict[str, Any] | None:
    registry = _load_registry()
    if kind == "browser":
        _purge_expired_browsers(registry)
    entry = _get_entry(registry, kind, entry_id)
    if entry is None:
        return None
    if kind == "browser" and _is_expired(entry):
        return None
    return entry


def list_links(kind: LinkKind | None = None) -> list[dict[str, Any]]:
    registry = _load_registry()
    _purge_expired_browsers(registry)
    result: list[dict[str, Any]] = []
    kinds: list[LinkKind] = [kind] if kind else ["browser", "member"]
    now = _now_ts()
    for token in kinds:
        bucket = registry.get(_entry_bucket(token))
        if not isinstance(bucket, Mapping):
            continue
        for entry_id, raw in bucket.items():
            if not isinstance(raw, Mapping):
                continue
            entry = _normalize_entry(token, str(entry_id or "").strip(), raw)
            if token == "browser" and _is_expired(entry, now=now):
                continue
            result.append(entry)
    result.sort(
        key=lambda item: (
            0 if item.get("access_class") == "device" else 1,
            str(item.get("last_webspace_id") or ""),
            str(item.get("display_name") or item.get("hostname") or item.get("id") or ""),
        )
    )
    return result


def authorize_link(kind: LinkKind, entry_id: str) -> tuple[bool, str | None]:
    entry = get_link(kind, entry_id)
    if entry is None:
        return True, None
    if bool(entry.get("revoked")):
        return False, "revoked"
    if _is_expired(entry):
        return False, "expired"
    return True, None


def touch_browser_session(
    device_id: str,
    *,
    webspace_id: str | None = None,
    connection_state: str | None = None,
    online: bool | None = None,
) -> dict[str, Any] | None:
    token = str(device_id or "").strip()
    if not token:
        return None
    registry = _load_registry()
    _purge_expired_browsers(registry)
    entry = _get_entry(registry, "browser", token) or _normalize_entry("browser", token, {})
    entry.setdefault("display_name", "")
    entry.setdefault("access_class", "device")
    entry.setdefault("autorotate", True)
    entry.setdefault("lifetime_mode", "permanent")
    entry["last_seen_at"] = _now_ts()
    if webspace_id is not None:
        entry["last_webspace_id"] = str(webspace_id or "").strip() or None
    if connection_state is not None:
        entry["connection_state"] = str(connection_state or "").strip().lower() or None
    if online is not None:
        entry["online"] = bool(online)
    entry = _updated(entry)
    saved = _put_entry(registry, "browser", entry)
    _save_registry(registry)
    return saved


def touch_member_link(
    node_id: str,
    *,
    hostname: str | None = None,
    node_names: list[str] | None = None,
    online: bool | None = None,
    connection_state: str | None = None,
) -> dict[str, Any] | None:
    token = str(node_id or "").strip()
    if not token:
        return None
    registry = _load_registry()
    entry = _get_entry(registry, "member", token) or _normalize_entry("member", token, {})
    entry.setdefault("access_class", "device")
    entry.setdefault("autorotate", True)
    entry.setdefault("lifetime_mode", "permanent")
    if hostname is not None:
        entry["hostname"] = str(hostname or "").strip() or None
    if node_names is not None:
        entry["node_names"] = [str(item or "").strip() for item in list(node_names or []) if str(item or "").strip()]
    if online is not None:
        entry["online"] = bool(online)
    if connection_state is not None:
        entry["connection_state"] = str(connection_state or "").strip().lower() or None
    entry["last_seen_at"] = _now_ts()
    entry = _updated(entry)
    saved = _put_entry(registry, "member", entry)
    _save_registry(registry)
    return saved


def rename_link(kind: LinkKind, entry_id: str, display_name: str) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    if not token:
        raise ValueError("entry id is required")
    registry = _load_registry()
    entry = _get_entry(registry, kind, token) or _normalize_entry(kind, token, {})
    entry["display_name"] = str(display_name or "").strip()
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    return saved


def upsert_link(kind: LinkKind, entry_id: str, patch: Mapping[str, Any] | None = None) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    if not token:
        raise ValueError("entry id is required")
    registry = _load_registry()
    entry = _get_entry(registry, kind, token) or _normalize_entry(kind, token, {})
    payload = dict(patch or {})
    for key, value in payload.items():
        if key in {"id", "kind"}:
            continue
        entry[key] = value
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    return saved


def set_link_lifetime(kind: LinkKind, entry_id: str, preset: str) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    if not token:
        raise ValueError("entry id is required")
    preset_token = str(preset or "").strip().lower() or "permanent"
    ttl_map = {
        "1h": 3600.0,
        "1d": 86400.0,
        "7d": 7 * 86400.0,
        "30d": 30 * 86400.0,
    }
    registry = _load_registry()
    entry = _get_entry(registry, kind, token) or _normalize_entry(kind, token, {})
    entry["revoked"] = False
    entry["revoked_at"] = None
    entry["autorotate"] = True
    if preset_token in {"permanent", "device", "indefinite"}:
        entry["lifetime_mode"] = "permanent"
        entry["access_class"] = "device"
        entry["expires_at"] = None
    else:
        ttl = ttl_map.get(preset_token, 86400.0)
        entry["lifetime_mode"] = "fixed"
        entry["access_class"] = "client" if kind == "browser" else "device"
        entry["expires_at"] = _now_ts() + ttl
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    return saved


def detach_link(kind: LinkKind, entry_id: str) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    if not token:
        raise ValueError("entry id is required")
    registry = _load_registry()
    entry = _get_entry(registry, kind, token) or _normalize_entry(kind, token, {})
    entry["revoked"] = True
    entry["revoked_at"] = _now_ts()
    entry["online"] = False
    entry["connection_state"] = "revoked"
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    return saved


def browser_snapshot() -> list[dict[str, Any]]:
    return [entry for entry in list_links("browser") if entry.get("last_seen_at")]


def member_snapshot() -> list[dict[str, Any]]:
    return list_links("member")


def lifetime_label(entry: Mapping[str, Any]) -> str:
    if str(entry.get("lifetime_mode") or "").strip().lower() == "permanent":
        return "Permanent"
    expires_at = _iso_from_ts(entry.get("expires_at"))
    return f"Until {expires_at}" if expires_at else "Fixed"
