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


def _normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        folded = token.casefold()
        if not token or folded in seen:
            continue
        seen.add(folded)
        out.append(token)
    return out


def _normalize_label_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if isinstance(item, Mapping):
            text = str(item.get("text") or item.get("label") or item.get("value") or "").strip()
            locale = str(item.get("locale") or "und").strip() or "und"
            role = str(item.get("role") or "alias").strip() or "alias"
            status = str(item.get("status") or "confirmed").strip() or "confirmed"
            source = str(item.get("source") or "").strip()
            actor = str(item.get("actor") or "").strip()
            request_id = str(item.get("request_id") or "").strip()
            created_at = item.get("created_at")
        else:
            text = str(item or "").strip()
            locale = "und"
            role = "alias"
            status = "confirmed"
            source = ""
            actor = ""
            request_id = ""
            created_at = None
        if not text:
            continue
        key = (text.casefold(), locale.casefold(), role.casefold())
        if key in seen:
            continue
        seen.add(key)
        label: dict[str, Any] = {
            "text": text,
            "locale": locale,
            "role": role,
            "status": status,
        }
        if source:
            label["source"] = source
        if actor:
            label["actor"] = actor
        if request_id:
            label["request_id"] = request_id
        if isinstance(created_at, (int, float)) and created_at > 0:
            label["created_at"] = float(created_at)
        out.append(label)
    return out


def _normalized_alias_key(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _label_matches_alias(label: Mapping[str, Any], alias: str, locale: str | None) -> bool:
    return (
        str(label.get("role") or "alias").strip().casefold() == "alias"
        and _normalized_alias_key(label.get("text")) == _normalized_alias_key(alias)
        and str(label.get("locale") or "und").strip().casefold() == str(locale or "und").strip().casefold()
    )


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
        "aliases": _normalize_text_list(data.get("aliases")),
        "labels": _normalize_label_list(data.get("labels")),
        "browser_family": str(data.get("browser_family") or "").strip() or None,
        "os_name": str(data.get("os_name") or "").strip() or None,
        "form_factor": str(data.get("form_factor") or "").strip() or None,
        "user_agent": str(data.get("user_agent") or "").strip() or None,
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


_ENTITY_REGISTRY_FIELDS = {
    "access_class",
    "aliases",
    "browser_family",
    "display_name",
    "form_factor",
    "hostname",
    "labels",
    "last_webspace_id",
    "node_names",
    "os_name",
    "revoked",
    "user_agent",
}


def _entity_registry_view(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        return {}
    kind: LinkKind = "member" if str(entry.get("kind") or "").strip() == "member" else "browser"
    normalized = _normalize_entry(kind, str(entry.get("id") or ""), entry)
    return {key: normalized.get(key) for key in sorted(_ENTITY_REGISTRY_FIELDS)}


def _entity_registry_fields_changed(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> bool:
    return _entity_registry_view(previous) != _entity_registry_view(current)


def _emit_entity_registry_changed(
    kind: LinkKind,
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    reason: str,
) -> None:
    try:
        from adaos.services import named_entities
        from adaos.services.agent_context import get_ctx
        from adaos.services.eventbus import emit as bus_emit

        entry_id = str(current.get("id") or "").strip()
        if not entry_id:
            return
        webspace_id = str(current.get("last_webspace_id") or "").strip()
        payload = named_entities.entity_event_payload(
            entity_ref=f"device:{kind}:{entry_id}",
            entity_kind=f"device.{kind}",
            source="access_links",
            scope={
                "device_id": entry_id,
                "link_kind": kind,
                **({"webspace_id": webspace_id} if webspace_id else {}),
            },
            previous=_entity_registry_view(previous),
            current=_entity_registry_view(current),
            reason=reason,
        )
        bus_emit(get_ctx().bus, named_entities.ENTITY_REGISTRY_CHANGED, payload, source="access_links")
    except Exception:
        # Device naming must never break the browser/member access path.
        return


def _emit_entity_registry_changed_if_needed(
    kind: LinkKind,
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    reason: str,
) -> None:
    if _entity_registry_fields_changed(previous, current):
        _emit_entity_registry_changed(kind, previous, current, reason=reason)


def _emit_entity_event_envelopes(events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> None:
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.eventbus import emit as bus_emit

        bus = get_ctx().bus
    except Exception:
        return
    for event in list(events or []):
        if not isinstance(event, Mapping):
            continue
        topic = str(event.get("topic") or "").strip()
        payload = event.get("payload")
        if not topic or not isinstance(payload, Mapping):
            continue
        try:
            bus_emit(bus, topic, dict(payload), source="access_links")
        except Exception:
            continue


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
    browser_family: str | None = None,
    os_name: str | None = None,
    form_factor: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any] | None:
    token = str(device_id or "").strip()
    if not token:
        return None
    registry = _load_registry()
    _purge_expired_browsers(registry)
    entry = _get_entry(registry, "browser", token) or _normalize_entry("browser", token, {})
    previous = dict(entry)
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
    if browser_family is not None:
        entry["browser_family"] = str(browser_family or "").strip() or None
    if os_name is not None:
        entry["os_name"] = str(os_name or "").strip() or None
    if form_factor is not None:
        entry["form_factor"] = str(form_factor or "").strip() or None
    if user_agent is not None:
        entry["user_agent"] = str(user_agent or "").strip() or None
    entry = _updated(entry)
    saved = _put_entry(registry, "browser", entry)
    _save_registry(registry)
    _emit_entity_registry_changed_if_needed("browser", previous, saved, reason="browser_session.changed")
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
    previous = dict(entry)
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
    _emit_entity_registry_changed_if_needed("member", previous, saved, reason="member_link.changed")
    return saved


def rename_link(
    kind: LinkKind,
    entry_id: str,
    display_name: str,
    *,
    node_names: list[str] | None = None,
) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    if not token:
        raise ValueError("entry id is required")
    registry = _load_registry()
    entry = _get_entry(registry, kind, token) or _normalize_entry(kind, token, {})
    previous = dict(entry)
    entry["display_name"] = str(display_name or "").strip()
    if node_names is not None:
        entry["node_names"] = [
            str(item or "").strip()
            for item in list(node_names or [])
            if str(item or "").strip()
        ]
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    _emit_entity_registry_changed_if_needed(kind, previous, saved, reason="display_name.changed")
    return saved


def add_link_alias(
    kind: LinkKind,
    entry_id: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    source: str = "access_links",
    request_id: str | None = None,
    webspace_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    alias_text = str(alias or "").strip()
    if not token:
        return {"ok": False, "status": "invalid", "error": "entry_id_required"}
    registry = _load_registry()
    if kind == "browser":
        _purge_expired_browsers(registry)
    entry = _get_entry(registry, kind, token)
    if entry is None:
        return {
            "ok": False,
            "status": "not_found",
            "error": "managed_link_not_found",
            "device_ref": f"{kind}:{token}",
        }

    try:
        from adaos.services import named_entities

        service = named_entities.get_named_entity_service()
        proposal = service.propose_alias_add(
            canonical_ref=f"device:{kind}:{token}",
            alias=alias_text,
            locale=locale,
            kind=f"device.{kind}",
            webspace_id=webspace_id or entry.get("last_webspace_id"),
            actor=actor,
            source=source,
            request_id=request_id,
            base_fingerprint=base_fingerprint,
        )
        result = service.apply_alias_add(proposal)
    except Exception as exc:
        return {
            "ok": False,
            "status": "invalid",
            "error": "alias_policy_failed",
            "message": str(exc),
            "device_ref": f"{kind}:{token}",
        }

    events = tuple(result.events or ())
    if result.status in {"conflict", "stale"}:
        _emit_entity_event_envelopes(events)
    if not result.ok:
        return {
            "ok": False,
            "status": result.status,
            "proposal": result.proposal.to_dict(),
            "events": [dict(item) for item in events],
            "device_ref": f"{kind}:{token}",
        }
    if result.status == "noop":
        return {
            "ok": True,
            "status": "noop",
            "proposal": result.proposal.to_dict(),
            "events": [],
            "entry": entry,
            "device_ref": f"{kind}:{token}",
        }

    label = {
        "text": alias_text,
        "locale": str(locale or "und").strip() or "und",
        "role": "alias",
        "status": "confirmed",
        "source": source,
        "created_at": _now_ts(),
    }
    if actor:
        label["actor"] = str(actor or "").strip()
    if request_id:
        label["request_id"] = str(request_id or "").strip()
    previous_labels = [
        item
        for item in _normalize_label_list(entry.get("labels"))
        if not _label_matches_alias(item, alias_text, locale)
    ]
    entry["labels"] = _normalize_label_list([*previous_labels, label])
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    _emit_entity_event_envelopes(events)
    return {
        "ok": True,
        "status": result.status,
        "proposal": result.proposal.to_dict(),
        "events": [dict(item) for item in events],
        "updated_record": result.updated_record.to_dict() if result.updated_record is not None else None,
        "entry": saved,
        "device_ref": f"{kind}:{token}",
    }


def _change_link_alias_state(
    action: Literal["remove", "deprecate"],
    kind: LinkKind,
    entry_id: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    source: str = "access_links",
    request_id: str | None = None,
    webspace_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    alias_text = str(alias or "").strip()
    if not token:
        return {"ok": False, "status": "invalid", "error": "entry_id_required"}
    registry = _load_registry()
    if kind == "browser":
        _purge_expired_browsers(registry)
    entry = _get_entry(registry, kind, token)
    if entry is None:
        return {
            "ok": False,
            "status": "not_found",
            "error": "managed_link_not_found",
            "device_ref": f"{kind}:{token}",
        }

    try:
        from adaos.services import named_entities

        service = named_entities.get_named_entity_service()
        proposal_factory = (
            service.propose_alias_remove if action == "remove" else service.propose_alias_deprecate
        )
        apply_factory = service.apply_alias_remove if action == "remove" else service.apply_alias_deprecate
        proposal = proposal_factory(
            canonical_ref=f"device:{kind}:{token}",
            alias=alias_text,
            locale=locale,
            kind=f"device.{kind}",
            webspace_id=webspace_id or entry.get("last_webspace_id"),
            actor=actor,
            source=source,
            request_id=request_id,
            base_fingerprint=base_fingerprint,
        )
        result = apply_factory(proposal)
    except Exception as exc:
        return {
            "ok": False,
            "status": "invalid",
            "error": "alias_policy_failed",
            "message": str(exc),
            "device_ref": f"{kind}:{token}",
        }

    events = tuple(result.events or ())
    if result.status in {"conflict", "stale"}:
        _emit_entity_event_envelopes(events)
    if not result.ok:
        return {
            "ok": False,
            "status": result.status,
            "proposal": result.proposal.to_dict(),
            "events": [dict(item) for item in events],
            "device_ref": f"{kind}:{token}",
        }
    if result.status == "noop":
        return {
            "ok": True,
            "status": "noop",
            "proposal": result.proposal.to_dict(),
            "events": [],
            "entry": entry,
            "device_ref": f"{kind}:{token}",
        }

    labels = _normalize_label_list(entry.get("labels"))
    if action == "remove":
        labels = [label for label in labels if not _label_matches_alias(label, alias_text, locale)]
    else:
        found = False
        updated_labels: list[dict[str, Any]] = []
        for label in labels:
            if _label_matches_alias(label, alias_text, locale):
                label = {**label, "status": "deprecated", "source": label.get("source") or source}
                found = True
            updated_labels.append(label)
        if not found:
            updated_labels.append(
                {
                    "text": alias_text,
                    "locale": str(locale or "und").strip() or "und",
                    "role": "alias",
                    "status": "deprecated",
                    "source": source,
                    "created_at": _now_ts(),
                }
            )
        labels = updated_labels
    entry["aliases"] = [
        item
        for item in _normalize_text_list(entry.get("aliases"))
        if not (str(locale or "und").strip().casefold() == "und" and _normalized_alias_key(item) == _normalized_alias_key(alias_text))
    ]
    entry["labels"] = _normalize_label_list(labels)
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    _emit_entity_event_envelopes(events)
    return {
        "ok": True,
        "status": result.status,
        "proposal": result.proposal.to_dict(),
        "events": [dict(item) for item in events],
        "updated_record": result.updated_record.to_dict() if result.updated_record is not None else None,
        "entry": saved,
        "device_ref": f"{kind}:{token}",
    }


def remove_link_alias(
    kind: LinkKind,
    entry_id: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    source: str = "access_links",
    request_id: str | None = None,
    webspace_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    return _change_link_alias_state(
        "remove",
        kind,
        entry_id,
        alias,
        locale=locale,
        actor=actor,
        source=source,
        request_id=request_id,
        webspace_id=webspace_id,
        base_fingerprint=base_fingerprint,
    )


def deprecate_link_alias(
    kind: LinkKind,
    entry_id: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    source: str = "access_links",
    request_id: str | None = None,
    webspace_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    return _change_link_alias_state(
        "deprecate",
        kind,
        entry_id,
        alias,
        locale=locale,
        actor=actor,
        source=source,
        request_id=request_id,
        webspace_id=webspace_id,
        base_fingerprint=base_fingerprint,
    )


def upsert_link(kind: LinkKind, entry_id: str, patch: Mapping[str, Any] | None = None) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    if not token:
        raise ValueError("entry id is required")
    registry = _load_registry()
    entry = _get_entry(registry, kind, token) or _normalize_entry(kind, token, {})
    previous = dict(entry)
    payload = dict(patch or {})
    for key, value in payload.items():
        if key in {"id", "kind"}:
            continue
        entry[key] = value
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    _emit_entity_registry_changed_if_needed(kind, previous, saved, reason="link.upserted")
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
    previous = dict(entry)
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
    _emit_entity_registry_changed_if_needed(kind, previous, saved, reason="lifetime.changed")
    return saved


def detach_link(kind: LinkKind, entry_id: str) -> dict[str, Any]:
    token = str(entry_id or "").strip()
    if not token:
        raise ValueError("entry id is required")
    registry = _load_registry()
    entry = _get_entry(registry, kind, token) or _normalize_entry(kind, token, {})
    previous = dict(entry)
    entry["revoked"] = True
    entry["revoked_at"] = _now_ts()
    entry["online"] = False
    entry["connection_state"] = "revoked"
    entry = _updated(entry)
    saved = _put_entry(registry, kind, entry)
    _save_registry(registry)
    _emit_entity_registry_changed_if_needed(kind, previous, saved, reason="link.detached")
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
