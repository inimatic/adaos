from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import WebSocket

from adaos.domain import Event as DomainEvent
from adaos.services.agent_context import get_ctx
from adaos.services.node_display import node_display_from_directory_node
from adaos.services.yjs.doc import apply_update_to_live_room, async_get_ydoc, mutate_live_room
from adaos.services.yjs.store import get_ystore_for_webspace, suppress_ystore_write_notifications, ystore_write_metadata

_log = logging.getLogger("adaos.subnet.link")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = int(default)
    value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _hub_yjs_broadcast_skip_source_prefixes() -> tuple[str, ...]:
    raw = str(
        os.getenv(
            "ADAOS_SUBNET_HUB_YJS_BROADCAST_SKIP_SOURCES",
            "subnet.link_manager,subnet.link_client",
        )
        or ""
    ).strip()
    return tuple(str(item or "").strip() for item in raw.split(",") if str(item or "").strip())


def _hub_yjs_broadcast_max_bytes() -> int:
    return _env_int(
        "ADAOS_SUBNET_HUB_YJS_BROADCAST_MAX_BYTES",
        64 * 1024,
        minimum=0,
        maximum=8 * 1024 * 1024,
    )


def _hub_yjs_broadcast_policy(update: bytes, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
    if not _env_flag("ADAOS_SUBNET_HUB_YJS_BROADCAST", True):
        return False, "disabled"
    payload_len = len(update or b"")
    max_bytes = _hub_yjs_broadcast_max_bytes()
    if max_bytes > 0 and payload_len > max_bytes:
        return False, "payload_too_large"
    meta = dict(metadata or {})
    source = str(meta.get("source") or "").strip()
    channel = str(meta.get("channel") or "").strip()
    for prefix in _hub_yjs_broadcast_skip_source_prefixes():
        if (source and source.startswith(prefix)) or (channel and channel.startswith(prefix)):
            return False, "source_suppressed"
    return True, ""


def _normalize_snapshot_material(value: Any, *, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str in {"captured_at", "updated_at", "last_seen"}:
                continue
            normalized[key_str] = _normalize_snapshot_material(item, path=(*path, key_str))
        return normalized
    if isinstance(value, list):
        items = [_normalize_snapshot_material(item, path=path) for item in value]
        if path in {("capacity", "io"), ("capacity", "skills"), ("capacity", "scenarios")}:
            try:
                return sorted(
                    items,
                    key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                )
            except Exception:
                return items
        return items
    return value


def _snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    if not isinstance(snapshot, dict):
        return ""
    capacity = snapshot.get("capacity") if isinstance(snapshot.get("capacity"), dict) else {}
    desktop_catalog = snapshot.get("desktop_catalog") if isinstance(snapshot.get("desktop_catalog"), dict) else {}
    material = {
        "role": snapshot.get("role"),
        "ready": snapshot.get("ready"),
        "node_state": snapshot.get("node_state"),
        "route_mode": snapshot.get("route_mode"),
        "connected_to_subnet": snapshot.get("connected_to_subnet"),
        "connected_to_hub": snapshot.get("connected_to_hub"),
        "node_names": snapshot.get("node_names") if isinstance(snapshot.get("node_names"), list) else [],
        "build": snapshot.get("build") if isinstance(snapshot.get("build"), dict) else {},
        "update_status": snapshot.get("update_status") if isinstance(snapshot.get("update_status"), dict) else {},
        "slots": snapshot.get("slots") if isinstance(snapshot.get("slots"), dict) else {},
        "capacity": _compact_snapshot_catalog(capacity, ("io", "skills", "scenarios")),
        "desktop_catalog": _compact_snapshot_catalog(
            desktop_catalog,
            ("apps", "widgets", "modals", "webio", "ydoc_defaults"),
        ),
    }
    try:
        return json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return repr(material)


def _merge_member_snapshot(existing: Any, heartbeat: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in dict(heartbeat or {}).items():
        key_str = str(key)
        if key_str in {"capacity", "desktop_catalog"} and value is None:
            continue
        merged[key_str] = value
    return merged


def _snapshot_captured_at(snapshot: dict[str, Any], *, fallback: float | None = None) -> float:
    try:
        value = snapshot.get("captured_at")
        if value is not None:
            return float(value)
    except Exception:
        pass
    return float(fallback or time.time())


def _compact_snapshot_catalog(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if isinstance(item, list):
            tokens = []
            for entry in item:
                if isinstance(entry, dict):
                    tokens.append(
                        str(
                            entry.get("id")
                            or entry.get("name")
                            or entry.get("path")
                            or entry.get("kind")
                            or ""
                        ).strip()
                    )
                else:
                    tokens.append(str(entry or "").strip())
            out[key] = sorted(token for token in tokens if token)
        elif isinstance(item, dict):
            out[key] = sorted(str(token) for token in item.keys())
        elif item is not None:
            out[key] = str(item)
    return out


def _snapshot_event_payload(node_id: str, *, node_names: list[str], snapshot: dict[str, Any], captured_at: float) -> dict[str, Any]:
    capacity = snapshot.get("capacity") if isinstance(snapshot.get("capacity"), dict) else {}
    build = snapshot.get("build") if isinstance(snapshot.get("build"), dict) else {}
    update_status = snapshot.get("update_status") if isinstance(snapshot.get("update_status"), dict) else {}
    return {
        "node_id": node_id,
        "node_names": list(node_names),
        "captured_at": captured_at,
        "snapshot_role": str(snapshot.get("role") or "").strip(),
        "snapshot_ready": bool(snapshot.get("ready")),
        "snapshot_node_state": str(snapshot.get("node_state") or "").strip(),
        "snapshot_route_mode": str(snapshot.get("route_mode") or "").strip(),
        "snapshot_connected_to_subnet": snapshot.get("connected_to_subnet"),
        "snapshot_connected_to_hub": snapshot.get("connected_to_hub"),
        "snapshot_capacity": {
            "io_total": len(capacity.get("io") or []) if isinstance(capacity.get("io"), list) else 0,
            "skill_total": len(capacity.get("skills") or []) if isinstance(capacity.get("skills"), list) else 0,
            "scenario_total": len(capacity.get("scenarios") or []) if isinstance(capacity.get("scenarios"), list) else 0,
        },
        "snapshot_build": {
            "runtime_version": str(build.get("runtime_version") or "").strip(),
            "runtime_git_short_commit": str(build.get("runtime_git_short_commit") or "").strip(),
        },
        "snapshot_update": {
            "state": str(update_status.get("state") or "").strip(),
            "phase": str(update_status.get("phase") or "").strip(),
            "action": str(update_status.get("action") or "").strip(),
        },
    }


def _snapshot_has_desktop_material(snapshot: dict[str, Any]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    catalog = snapshot.get("desktop_catalog") if isinstance(snapshot.get("desktop_catalog"), dict) else {}
    apps = catalog.get("apps") if isinstance(catalog.get("apps"), list) else []
    widgets = catalog.get("widgets") if isinstance(catalog.get("widgets"), list) else []
    modals = catalog.get("modals") if isinstance(catalog.get("modals"), list) else []
    webio = catalog.get("webio") if isinstance(catalog.get("webio"), list) else []
    ydoc_defaults = catalog.get("ydoc_defaults") if isinstance(catalog.get("ydoc_defaults"), dict) else {}
    return bool(apps or widgets or modals or webio or ydoc_defaults)


def _publish_link_event(event_type: str, payload: dict[str, Any]) -> bool:
    try:
        get_ctx().bus.publish(
            DomainEvent(
                type=str(event_type),
                payload=payload,
                source="subnet.link",
                ts=time.time(),
            )
        )
        return True
    except Exception:
        node_id = payload.get("node_id") if isinstance(payload, dict) else None
        _log.warning(
            "failed to publish subnet link event type=%s node_id=%s",
            event_type,
            node_id,
            exc_info=True,
        )
        return False


def _coerce_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return dict(decoded) if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            decoded = to_json()
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
            return dict(decoded) if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    return {}


def _member_infrastate_webspaces() -> list[str]:
    raw = str(os.getenv("ADAOS_SUBNET_YJS_REPLICATION_WEBSPACES") or "desktop").strip()
    out: list[str] = []
    for item in raw.split(","):
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return out or ["desktop"]


def _member_infrastate_projection_min_interval_s() -> float:
    raw = str(os.getenv("ADAOS_SUBNET_MEMBER_INFRASTATE_PROJECTION_MIN_INTERVAL_S") or "").strip()
    try:
        value = float(raw or 30.0)
    except Exception:
        value = 30.0
    return max(5.0, min(300.0, value))


def _member_snapshot_refresh_event_min_interval_s() -> float:
    raw = str(os.getenv("ADAOS_SUBNET_MEMBER_SNAPSHOT_REFRESH_EVENT_MIN_INTERVAL_S") or "").strip()
    try:
        value = float(raw or 30.0)
    except Exception:
        value = 30.0
    return max(0.0, min(300.0, value))


def _core_public_version(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    public, _, _local = text.partition("+")
    return public.strip() or text


def _core_slot_manifest(slots_payload: dict[str, Any], active_slot: str | None = None) -> dict[str, Any]:
    active = str(active_slot or slots_payload.get("active_slot") or "").strip()
    lookup_active = active.upper()
    manifest = slots_payload.get("active_manifest") if isinstance(slots_payload.get("active_manifest"), dict) else {}
    raw_slots = slots_payload.get("slots") if isinstance(slots_payload.get("slots"), dict) else {}
    slot_meta = (raw_slots.get(active) or raw_slots.get(lookup_active)) if active else {}
    slot_manifest = slot_meta.get("manifest") if isinstance(slot_meta, dict) and isinstance(slot_meta.get("manifest"), dict) else {}
    merged = dict(slot_manifest)
    merged.update(manifest)
    if active:
        merged.setdefault("slot", active)
    return merged


def _core_slot_version(manifest: dict[str, Any], build: dict[str, Any]) -> str:
    for value in (
        manifest.get("build_version"),
        manifest.get("base_version"),
        build.get("runtime_build_version"),
        build.get("runtime_base_version"),
        build.get("runtime_version"),
        build.get("version"),
        manifest.get("target_version"),
    ):
        label = _core_public_version(value)
        if label:
            return label
    return ""


def _core_slot_commit(manifest: dict[str, Any], build: dict[str, Any]) -> str:
    for value in (
        manifest.get("git_short_commit"),
        manifest.get("git_commit"),
        build.get("runtime_git_short_commit"),
        build.get("runtime_git_commit"),
        build.get("git_short_sha"),
        build.get("git_sha"),
        manifest.get("target_rev"),
    ):
        text = str(value or "").strip()
        if text:
            return text[:7] if len(text) >= 40 and all(ch in "0123456789abcdefABCDEF" for ch in text) else text
    return ""


def _core_slot_summary_subtitle(slots_payload: dict[str, Any], build: dict[str, Any], *, active_slot: str | None = None) -> str:
    active = str(active_slot or slots_payload.get("active_slot") or "").strip() or "--"
    manifest = _core_slot_manifest(slots_payload, active)
    parts = [f"slot {active}"]
    version = _core_slot_version(manifest, build)
    commit = _core_slot_commit(manifest, build)
    if version:
        parts.append(version)
    if commit:
        parts.append(commit)
    if len(parts) == 1:
        parts.append("unknown")
    return " | ".join(parts)


def _member_build_meta(snapshot: dict[str, Any]) -> dict[str, Any]:
    build = snapshot.get("build") if isinstance(snapshot.get("build"), dict) else {}
    runtime_build_version = str(build.get("runtime_build_version") or "").strip()
    runtime_base_version = str(build.get("runtime_base_version") or "").strip()
    runtime_target_version = str(build.get("runtime_target_version") or build.get("runtime_version") or "").strip()
    return {
        "version": str(build.get("version") or "unknown"),
        "build_date": str(build.get("build_date") or ""),
        "git_sha": "",
        "git_short_sha": "",
        "git_branch": "",
        "git_subject": "",
        "repo_root": "",
        "runtime_version": runtime_build_version or runtime_base_version or str(build.get("runtime_version") or build.get("version") or "unknown"),
        "runtime_base_version": runtime_base_version,
        "runtime_build_version": runtime_build_version or str(build.get("version") or ""),
        "runtime_target_version": runtime_target_version,
        "runtime_git_commit": str(build.get("runtime_git_commit") or ""),
        "runtime_git_short_commit": str(build.get("runtime_git_short_commit") or ""),
        "runtime_git_branch": str(build.get("runtime_git_branch") or ""),
        "runtime_git_subject": str(build.get("runtime_git_subject") or ""),
    }


def _member_slots_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    slots = snapshot.get("slots") if isinstance(snapshot.get("slots"), dict) else {}
    update = snapshot.get("update_status") if isinstance(snapshot.get("update_status"), dict) else {}
    active_manifest = slots.get("active_manifest") if isinstance(slots.get("active_manifest"), dict) else {}
    active_slot = str(slots.get("active_slot") or active_manifest.get("slot") or update.get("target_slot") or "")
    previous_slot = str(slots.get("previous_slot") or "")
    raw_slots = slots.get("slots") if isinstance(slots.get("slots"), dict) else {}
    slot_items = json.loads(json.dumps(raw_slots)) if raw_slots else {}
    if active_slot and active_slot not in slot_items:
        slot_items[active_slot] = {
            "manifest": {
                "slot": str(active_manifest.get("slot") or active_slot),
                "target_rev": str(active_manifest.get("target_rev") or ""),
                "target_version": str(active_manifest.get("target_version") or ""),
                "base_version": str(active_manifest.get("base_version") or ""),
                "build_version": str(active_manifest.get("build_version") or ""),
                "git_commit": str(active_manifest.get("git_commit") or ""),
                "git_short_commit": str(active_manifest.get("git_short_commit") or ""),
                "git_branch": str(active_manifest.get("git_branch") or ""),
                "git_subject": str(active_manifest.get("git_subject") or ""),
            },
            "path": "",
        }
    if previous_slot and previous_slot not in slot_items:
        slot_items[previous_slot] = {"manifest": {"slot": previous_slot}, "path": ""}
    return {
        "active_slot": active_slot,
        "previous_slot": previous_slot,
        "slots": slot_items,
        "active_manifest": dict(active_manifest),
    }


def _member_status_payload(snapshot: dict[str, Any], *, captured_at: float) -> dict[str, Any]:
    update = snapshot.get("update_status") if isinstance(snapshot.get("update_status"), dict) else {}
    state = str(update.get("state") or snapshot.get("node_state") or "connected").strip()
    message = str(update.get("message") or "").strip()
    if not message and snapshot:
        message = "remote member snapshot"
    return {
        "state": state,
        "phase": str(update.get("phase") or ""),
        "action": str(update.get("action") or ""),
        "message": message or "remote snapshot pending",
        "reason": str(update.get("reason") or "subnet.member.snapshot"),
        "target_rev": str(update.get("target_rev") or ""),
        "target_version": str(update.get("target_version") or ""),
        "target_slot": str(update.get("target_slot") or ""),
        "scheduled_for": update.get("scheduled_for"),
        "updated_at": update.get("updated_at") or captured_at,
        "finished_at": update.get("finished_at"),
    }


def _member_last_result_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    last = snapshot.get("last_result") if isinstance(snapshot.get("last_result"), dict) else {}
    return {
        "state": str(last.get("state") or ""),
        "phase": str(last.get("phase") or ""),
        "message": str(last.get("message") or ""),
        "target_slot": str(last.get("target_slot") or ""),
        "finished_at": last.get("finished_at"),
        "validated_at": last.get("validated_at"),
    }


def _member_lifecycle_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_state": str(snapshot.get("node_state") or "connected"),
        "reason": str(snapshot.get("reason") or "remote member snapshot"),
        "draining": bool(snapshot.get("draining")),
    }


def _member_infrastate_projection(node_id: str, *, node_names: list[str], snapshot: dict[str, Any], captured_at: float) -> dict[str, Any]:
    node_key = str(node_id or "").strip()
    snap = snapshot if isinstance(snapshot, dict) else {}
    captured = float(captured_at or _snapshot_captured_at(snap))
    build = _member_build_meta(snap)
    slots_payload = _member_slots_payload(snap)
    status = _member_status_payload(snap, captured_at=captured)
    last_result = _member_last_result_payload(snap)
    lifecycle = _member_lifecycle_payload(snap)
    subtitle = _core_slot_summary_subtitle(slots_payload, build, active_slot=str(slots_payload.get("active_slot") or ""))
    label = next((str(item or "").strip() for item in node_names if str(item or "").strip()), node_key or "member")
    return {
        "summary": {
            "label": "Infra State",
            "value": str(status.get("state") or lifecycle.get("node_state") or "connected"),
            "subtitle": subtitle,
            "description": str(status.get("message") or "remote member snapshot"),
            "updated_at": captured,
            "node_id": node_key,
            "selected_node_id": node_key,
            "selected_node_label": label,
            "source": "subnet.member.snapshot",
        },
        "status": status,
        "last_result": last_result,
        "lifecycle": lifecycle,
        "slots_meta": slots_payload,
        "build_meta": build,
        "last_refresh_ts": captured,
        "projection_diag": {
            "source": "subnet.link_manager.member_snapshot",
            "node_id": node_key,
            "captured_at": captured,
        },
    }


def _member_infrastate_projection_fingerprint(projection: dict[str, Any]) -> str:
    material = json.loads(json.dumps(projection, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    if isinstance(material.get("summary"), dict):
        material["summary"].pop("updated_at", None)
    if isinstance(material.get("status"), dict):
        material["status"].pop("updated_at", None)
    material.pop("last_refresh_ts", None)
    material.pop("projection_diag", None)
    return json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _is_member_infrastate_projection(value: Any) -> bool:
    infrastate = _coerce_json_dict(value)
    if not infrastate:
        return False
    summary = _coerce_json_dict(infrastate.get("summary"))
    diag = _coerce_json_dict(infrastate.get("projection_diag"))
    return (
        str(summary.get("source") or "").strip() == "subnet.member.snapshot"
        or str(diag.get("source") or "").strip() == "subnet.link_manager.member_snapshot"
    )


def _member_node_state_for_ingest(existing: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    state = json.loads(json.dumps(incoming or {}, ensure_ascii=False, separators=(",", ":")))
    existing_state = _coerce_json_dict(existing)
    existing_infrastate = _coerce_json_dict(existing_state.get("infrastate"))
    if _is_member_infrastate_projection(existing_infrastate):
        state["infrastate"] = existing_infrastate
    else:
        state.pop("infrastate", None)
    return state


def _target_node_id_for_hub_event(event_type: str, payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    target = str(
        payload.get("target_node_id")
        or payload.get("node_target_id")
        or meta.get("target_node_id")
        or meta.get("node_target_id")
        or ""
    ).strip()
    if target:
        return target
    if str(event_type or "").strip() in {
        "webio.stream.snapshot.requested",
        "webio.stream.subscription.changed",
    }:
        return str(payload.get("node_id") or "").strip()
    return ""


@dataclass
class HubMemberLink:
    node_id: str
    websocket: WebSocket
    hostname: str | None = None
    roles: list[str] = field(default_factory=list)
    node_names: list[str] = field(default_factory=list)
    connected_at: float = field(default_factory=lambda: time.time())
    last_message_at: float = field(default_factory=lambda: time.time())
    last_hub_event_at: float | None = None
    last_hub_event_type: str | None = None
    last_hub_core_update_state: str | None = None
    last_hub_core_update_action: str | None = None
    last_control_request_id: str | None = None
    last_control_request_at: float | None = None
    last_control_action: str | None = None
    last_control_reason: str | None = None
    last_control_result_at: float | None = None
    last_control_result: dict[str, Any] = field(default_factory=dict)
    last_snapshot_at: float | None = None
    last_snapshot_fingerprint: str | None = None
    node_snapshot: dict[str, Any] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_rpc: Dict[str, asyncio.Future] = field(default_factory=dict)

    async def send_json(self, msg: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(msg)


class HubLinkManager:
    """
    Hub-side manager for member WebSocket links.

    Responsibilities:
    - Track online members connected via `/ws/subnet`
    - Provide RPC (hub -> member) used by tool routing
    - Relay Yjs updates between members and the hub's YStore
    - Ingest selected bus events (member -> hub)
    """

    def __init__(self) -> None:
        self._links: dict[str, HubMemberLink] = {}
        self._lock = asyncio.Lock()
        self._hub_event_total = 0
        self._hub_core_update_broadcast_total = 0
        self._snapshot_refresh_tasks: dict[str, asyncio.Task] = {}
        self._yjs_ingest_total = 0
        self._yjs_ingest_bytes = 0
        self._yjs_live_apply_total = 0
        self._yjs_live_apply_failed_total = 0
        self._yjs_broadcast_total = 0
        self._yjs_broadcast_failed_total = 0
        self._yjs_broadcast_suppressed_total = 0
        self._yjs_broadcast_suppressed_bytes = 0
        self._last_yjs_broadcast_suppressed_at = 0.0
        self._last_yjs_broadcast_suppressed_reason = ""
        self._last_yjs_broadcast_suppressed_source = ""
        self._last_yjs_broadcast_suppressed_channel = ""
        self._last_yjs_broadcast_suppressed_webspace_id = ""
        self._last_yjs_broadcast_suppressed_bytes = 0
        self._last_yjs_ingest_at = 0.0
        self._last_yjs_ingest_node_id = ""
        self._last_yjs_ingest_webspace_id = ""
        self._last_yjs_ingest_bytes = 0
        self._member_infrastate_projection_fingerprints: dict[tuple[str, str], str] = {}
        self._member_infrastate_projection_last_at: dict[tuple[str, str], float] = {}
        self._member_infrastate_projection_total = 0
        self._member_infrastate_projection_failed_total = 0
        self._last_member_infrastate_projection_at = 0.0
        self._last_member_infrastate_projection_node_id = ""
        self._last_member_infrastate_projection_webspace_id = ""
        self._member_snapshot_refresh_event_last_at: dict[str, float] = {}

    @staticmethod
    def _member_snapshot_followup_delay_s() -> float:
        raw = str(os.getenv("ADAOS_SUBNET_MEMBER_SNAPSHOT_FOLLOWUP_DELAY_S") or "").strip()
        try:
            value = float(raw or 3.0)
        except Exception:
            value = 3.0
        return max(0.5, min(30.0, value))

    def _cancel_snapshot_refresh_task(self, node_id: str) -> None:
        task = self._snapshot_refresh_tasks.pop(str(node_id or "").strip(), None)
        if task and not task.done():
            task.cancel()

    def _schedule_snapshot_followup_refresh(self, node_id: str) -> None:
        node_key = str(node_id or "").strip()
        if not node_key:
            return
        self._cancel_snapshot_refresh_task(node_key)

        async def _runner() -> None:
            try:
                await asyncio.sleep(self._member_snapshot_followup_delay_s())
                link = await self._get_link(node_key)
                if not link:
                    return
                if _snapshot_has_desktop_material(link.node_snapshot):
                    return
                await self.request_member_snapshot(node_key, reason="member_link_followup")
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.debug(
                    "failed to request follow-up member snapshot node_id=%s",
                    node_key,
                    exc_info=True,
                )
            finally:
                current = self._snapshot_refresh_tasks.get(node_key)
                if current is task:
                    self._snapshot_refresh_tasks.pop(node_key, None)

        task = asyncio.create_task(
            _runner(),
            name=f"member-snapshot-followup:{node_key}",
        )
        self._snapshot_refresh_tasks[node_key] = task

    async def _push_node_display_assignment(self, node_id: str) -> None:
        link = await self._get_link(node_id)
        if not link:
            return
        try:
            from adaos.services.registry.subnet_directory import get_directory

            node = get_directory().get_node(node_id)
        except Exception:
            node = None
        payload = node_display_from_directory_node(node if isinstance(node, dict) else {"node_id": node_id, "roles": ["member"]})
        if not payload:
            return
        await link.send_json({"t": "node.display.assignment", "node_display": payload, "ts": time.time()})

    async def _push_current_core_update_status(self, node_id: str) -> None:
        link = await self._get_link(node_id)
        if not link:
            return
        try:
            from adaos.services.core_update import read_status as read_core_update_status

            payload = read_core_update_status() or {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict) or not payload:
            return
        await link.send_json(
            {
                "t": "hub.event",
                "event": {
                    "type": "core.update.status",
                    "payload": payload,
                    "source": "hub.register",
                    "ts": time.time(),
                },
            }
        )
        link.last_hub_event_at = time.time()
        link.last_hub_event_type = "core.update.status"
        link.last_hub_core_update_state = str(payload.get("state") or "").strip() or None
        link.last_hub_core_update_action = str(payload.get("action") or "").strip() or None

    async def register(
        self,
        node_id: str,
        ws: WebSocket,
        *,
        hostname: str | None,
        roles: list[str] | None,
        node_names: list[str] | None = None,
    ) -> HubMemberLink:
        link = HubMemberLink(
            node_id=node_id,
            websocket=ws,
            hostname=hostname,
            roles=list(roles or []),
            node_names=list(node_names or []),
        )
        async with self._lock:
            # replace existing link if reconnecting
            prev = self._links.get(node_id)
            self._links[node_id] = link
        if prev is not None:
            try:
                for rid, fut in list(prev.pending_rpc.items()):
                    if not fut.done():
                        fut.set_exception(ConnectionError("link_replaced"))
            except Exception:
                pass
        _publish_link_event(
            "subnet.member.link.up",
            {
                "node_id": node_id,
                "hostname": hostname,
                "roles": list(roles or []),
                "node_names": list(node_names or []),
            },
        )
        try:
            await self._push_node_display_assignment(node_id)
        except Exception:
            _log.debug("failed to push node display assignment on register node_id=%s", node_id, exc_info=True)
        try:
            await self._push_current_core_update_status(node_id)
        except Exception:
            _log.debug("failed to push current core.update.status on register node_id=%s", node_id, exc_info=True)
        try:
            await self.request_member_snapshot(node_id, reason="member_link_up")
        except Exception:
            _log.debug("failed to request initial member snapshot on register node_id=%s", node_id, exc_info=True)
        self._schedule_snapshot_followup_refresh(node_id)
        return link

    async def unregister(self, node_id: str) -> None:
        self._cancel_snapshot_refresh_task(node_id)
        async with self._lock:
            link = self._links.pop(node_id, None)
        if not link:
            return
        try:
            for rid, fut in list(link.pending_rpc.items()):
                if not fut.done():
                    fut.set_exception(ConnectionError("link_closed"))
        except Exception:
            pass
        _publish_link_event("subnet.member.link.down", {"node_id": node_id})

    def is_connected(self, node_id: str) -> bool:
        return node_id in self._links

    async def _get_link(self, node_id: str) -> HubMemberLink | None:
        async with self._lock:
            return self._links.get(node_id)

    async def note_member_activity(self, node_id: str, *, message_type: str | None = None) -> None:
        link = await self._get_link(node_id)
        if not link:
            return
        link.last_message_at = time.time()

    async def update_member_metadata(self, node_id: str, *, node_names: list[str] | None = None) -> dict[str, Any]:
        link = await self._get_link(node_id)
        if not link:
            return {"ok": False, "error": "member_not_connected"}
        if node_names is not None:
            link.node_names = list(node_names)
        link.last_message_at = time.time()
        payload = {
            "node_id": node_id,
            "node_names": list(link.node_names),
        }
        _publish_link_event("subnet.member.meta.changed", payload)
        return {"ok": True, **payload}

    async def _publish_member_infrastate_projection(
        self,
        node_id: str,
        *,
        node_names: list[str],
        snapshot: dict[str, Any],
        captured_at: float,
    ) -> None:
        node_key = str(node_id or "").strip()
        if not node_key or not isinstance(snapshot, dict):
            return
        projection = _member_infrastate_projection(
            node_key,
            node_names=list(node_names or []),
            snapshot=snapshot,
            captured_at=captured_at,
        )
        fingerprint = _member_infrastate_projection_fingerprint(projection)
        now = time.time()
        min_interval = _member_infrastate_projection_min_interval_s()
        for webspace_id in _member_infrastate_webspaces():
            ws_id = str(webspace_id or "").strip() or "default"
            cache_key = (node_key, ws_id)
            if (
                self._member_infrastate_projection_fingerprints.get(cache_key) == fingerprint
                and now - float(self._member_infrastate_projection_last_at.get(cache_key) or 0.0) < min_interval
            ):
                continue
            projection_copy = json.loads(json.dumps(projection, ensure_ascii=False, separators=(",", ":")))

            def _merge_projection(ydoc: Any, txn: Any) -> None:
                data_map = ydoc.get_map("data")
                nodes = _coerce_json_dict(data_map.get("nodes"))
                node_state = _coerce_json_dict(nodes.get(node_key))
                if _coerce_json_dict(node_state.get("infrastate")) == projection_copy:
                    return
                node_state["infrastate"] = projection_copy
                nodes[node_key] = node_state
                data_map.set(txn, "nodes", nodes)

            live_scheduled = mutate_live_room(
                ws_id,
                _merge_projection,
                root_names=["data"],
                source="subnet.link_manager.member_infrastate",
                owner="core:subnet_link_manager",
                channel="core.subnet.link.member_infrastate",
                governed=True,
            )
            if not live_scheduled:
                try:
                    async with ystore_write_metadata(
                        root_names=["data"],
                        source="subnet.link_manager.member_infrastate",
                        owner="core:subnet_link_manager",
                        channel="core.subnet.link.member_infrastate",
                        governed=True,
                    ):
                        async with async_get_ydoc(ws_id, load_mark_roots=["data"], governed=True) as ydoc:
                            data_map = ydoc.get_map("data")
                            nodes = _coerce_json_dict(data_map.get("nodes"))
                            node_state = _coerce_json_dict(nodes.get(node_key))
                            if _coerce_json_dict(node_state.get("infrastate")) == projection_copy:
                                self._member_infrastate_projection_fingerprints[cache_key] = fingerprint
                                self._member_infrastate_projection_last_at[cache_key] = now
                                continue
                            node_state["infrastate"] = projection_copy
                            nodes[node_key] = node_state
                            with ydoc.begin_transaction() as txn:
                                data_map.set(txn, "nodes", nodes)
                except Exception:
                    self._member_infrastate_projection_failed_total += 1
                    _log.debug(
                        "failed to publish member infrastate projection node_id=%s webspace=%s",
                        node_key,
                        ws_id,
                        exc_info=True,
                    )
                    continue
            self._member_infrastate_projection_total += 1
            self._member_infrastate_projection_fingerprints[cache_key] = fingerprint
            self._member_infrastate_projection_last_at[cache_key] = now
            self._last_member_infrastate_projection_at = now
            self._last_member_infrastate_projection_node_id = node_key
            self._last_member_infrastate_projection_webspace_id = ws_id

    def _maybe_publish_member_snapshot_refreshed_event(
        self,
        node_id: str,
        *,
        snapshot: dict[str, Any],
        payload: dict[str, Any],
        changed: bool,
    ) -> None:
        node_key = str(node_id or "").strip()
        if not node_key or changed or not _snapshot_has_desktop_material(snapshot):
            return
        now = time.time()
        min_interval = _member_snapshot_refresh_event_min_interval_s()
        last_at = float(self._member_snapshot_refresh_event_last_at.get(node_key) or 0.0)
        if min_interval > 0 and last_at > 0 and now - last_at < min_interval:
            return
        event_payload = dict(payload or {})
        event_payload["refresh_reason"] = "unchanged_snapshot_with_desktop_material"
        if _publish_link_event("subnet.member.snapshot.refreshed", event_payload):
            self._member_snapshot_refresh_event_last_at[node_key] = now

    async def update_member_snapshot(self, node_id: str, *, snapshot: dict[str, Any]) -> dict[str, Any]:
        link = await self._get_link(node_id)
        if not link:
            return {"ok": False, "error": "member_not_connected"}
        snap = dict(snapshot or {})
        snapshot_fingerprint = _snapshot_fingerprint(snap)
        changed = snapshot_fingerprint != str(link.last_snapshot_fingerprint or "")
        node_names = snap.get("node_names")
        if isinstance(node_names, list):
            link.node_names = [str(item or "").strip() for item in node_names if str(item or "").strip()]
        link.node_snapshot = snap
        link.last_snapshot_fingerprint = snapshot_fingerprint
        link.last_snapshot_at = time.time()
        link.last_message_at = link.last_snapshot_at
        update_status = snap.get("update_status")
        if isinstance(update_status, dict):
            state = str(update_status.get("state") or "").strip()
            action = str(update_status.get("action") or "").strip()
            if state:
                link.last_hub_core_update_state = state
            if action:
                link.last_hub_core_update_action = action
        payload = _snapshot_event_payload(
            node_id,
            node_names=list(link.node_names),
            snapshot=snap,
            captured_at=_snapshot_captured_at(snap, fallback=link.last_snapshot_at),
        )
        if _snapshot_has_desktop_material(snap):
            self._cancel_snapshot_refresh_task(node_id)
        try:
            from adaos.services.registry.subnet_directory import get_directory

            directory = get_directory()
            if changed:
                directory.on_member_runtime_snapshot(node_id, snap)
            else:
                try:
                    projection_captured_at = (
                        float(snap.get("captured_at"))
                        if snap.get("captured_at") is not None
                        else None
                    )
                except Exception:
                    projection_captured_at = None
                directory.on_member_runtime_snapshot_heartbeat(
                    node_id,
                    captured_at=projection_captured_at,
                    node_state=str(snap.get("node_state") or "").strip() or None,
                )
        except Exception:
            _log.warning("failed to update subnet directory from member snapshot node_id=%s", node_id, exc_info=True)
        try:
            await self._push_node_display_assignment(node_id)
        except Exception:
            _log.debug("failed to push node display assignment after snapshot node_id=%s", node_id, exc_info=True)
        try:
            await self._publish_member_infrastate_projection(
                node_id,
                node_names=list(link.node_names),
                snapshot=snap,
                captured_at=_snapshot_captured_at(snap, fallback=link.last_snapshot_at),
            )
        except Exception:
            _log.debug("failed to schedule member infrastate projection after snapshot node_id=%s", node_id, exc_info=True)
        if changed:
            _publish_link_event("subnet.member.snapshot.changed", payload)
        else:
            self._maybe_publish_member_snapshot_refreshed_event(
                node_id,
                snapshot=snap,
                payload=payload,
                changed=changed,
            )
        return {"ok": True, "changed": changed, **payload}

    async def update_member_snapshot_heartbeat(self, node_id: str, *, snapshot: dict[str, Any]) -> dict[str, Any]:
        link = await self._get_link(node_id)
        if not link:
            return {"ok": False, "error": "member_not_connected"}
        snap = dict(snapshot or {})
        merged_snapshot = _merge_member_snapshot(link.node_snapshot, snap)
        snapshot_fingerprint = _snapshot_fingerprint(merged_snapshot)
        changed = snapshot_fingerprint != str(link.last_snapshot_fingerprint or "")
        node_names = snap.get("node_names")
        if isinstance(node_names, list):
            link.node_names = [str(item or "").strip() for item in node_names if str(item or "").strip()]
        link.node_snapshot = merged_snapshot
        if changed:
            link.last_snapshot_fingerprint = snapshot_fingerprint
        link.last_snapshot_at = time.time()
        link.last_message_at = link.last_snapshot_at
        update_status = snap.get("update_status")
        if isinstance(update_status, dict):
            state = str(update_status.get("state") or "").strip()
            action = str(update_status.get("action") or "").strip()
            if state:
                link.last_hub_core_update_state = state
            if action:
                link.last_hub_core_update_action = action
        payload = _snapshot_event_payload(
            node_id,
            node_names=list(link.node_names),
            snapshot=merged_snapshot,
            captured_at=_snapshot_captured_at(snap, fallback=link.last_snapshot_at),
        )
        if _snapshot_has_desktop_material(merged_snapshot):
            self._cancel_snapshot_refresh_task(node_id)
        try:
            from adaos.services.registry.subnet_directory import get_directory

            directory = get_directory()
            if changed:
                directory.on_member_runtime_snapshot(node_id, merged_snapshot)
            else:
                try:
                    projection_captured_at = (
                        float(snap.get("captured_at"))
                        if snap.get("captured_at") is not None
                        else None
                    )
                except Exception:
                    projection_captured_at = None
                directory.on_member_runtime_snapshot_heartbeat(
                    node_id,
                    captured_at=projection_captured_at,
                    node_state=str(snap.get("node_state") or "").strip() or None,
                )
        except Exception:
            _log.warning(
                "failed to update subnet directory from member snapshot heartbeat node_id=%s",
                node_id,
                exc_info=True,
            )
        try:
            await self._push_node_display_assignment(node_id)
        except Exception:
            _log.debug("failed to push node display assignment after snapshot heartbeat node_id=%s", node_id, exc_info=True)
        try:
            await self._publish_member_infrastate_projection(
                node_id,
                node_names=list(link.node_names),
                snapshot=merged_snapshot,
                captured_at=_snapshot_captured_at(snap, fallback=link.last_snapshot_at),
            )
        except Exception:
            _log.debug("failed to schedule member infrastate projection after snapshot heartbeat node_id=%s", node_id, exc_info=True)
        if changed:
            _publish_link_event("subnet.member.snapshot.changed", payload)
        else:
            self._maybe_publish_member_snapshot_refreshed_event(
                node_id,
                snapshot=merged_snapshot,
                payload=payload,
                changed=changed,
            )
        return {"ok": True, "changed": changed, **payload}

    async def set_member_node_names(self, node_id: str, *, node_names: list[str]) -> dict[str, Any]:
        link = await self._get_link(node_id)
        if not link:
            return {"ok": False, "error": "member_not_connected"}
        await link.send_json({"t": "node.names.set", "node_names": list(node_names)})
        return {"ok": True, "node_id": node_id, "node_names": list(node_names)}

    async def request_member_snapshot(self, node_id: str, *, reason: str = "manual_refresh") -> dict[str, Any]:
        link = await self._get_link(node_id)
        if not link:
            return {"ok": False, "accepted": False, "error": "member_not_connected", "node_id": node_id}
        await link.send_json(
            {
                "t": "node.snapshot.request",
                "reason": str(reason or "manual_refresh"),
                "ts": time.time(),
            }
        )
        link.last_hub_event_at = time.time()
        link.last_hub_event_type = "node.snapshot.request"
        payload = {
            "node_id": node_id,
            "reason": str(reason or "manual_refresh"),
        }
        _publish_link_event("subnet.member.snapshot.requested", payload)
        return {"ok": True, "accepted": True, **payload}

    async def request_member_update(
        self,
        node_id: str,
        *,
        action: str,
        target_rev: str = "",
        target_version: str = "",
        countdown_sec: float | None = None,
        drain_timeout_sec: float | None = None,
        signal_delay_sec: float | None = None,
        reason: str = "hub.member_control",
    ) -> dict[str, Any]:
        link = await self._get_link(node_id)
        if not link:
            return {"ok": False, "accepted": False, "error": "member_not_connected", "node_id": node_id}
        action_norm = str(action or "").strip().lower()
        if action_norm == "start":
            action_norm = "update"
        if action_norm not in {"update", "cancel", "rollback", "drain"}:
            return {"ok": False, "accepted": False, "error": "invalid_action", "node_id": node_id, "action": action_norm}
        request_id = f"member_update_{uuid.uuid4().hex}"
        msg = {
            "t": "core.update.request",
            "request_id": request_id,
            "action": action_norm,
            "target_rev": str(target_rev or ""),
            "target_version": str(target_version or ""),
            "reason": str(reason or "hub.member_control"),
            "ts": time.time(),
        }
        if countdown_sec is not None:
            msg["countdown_sec"] = float(countdown_sec)
        if drain_timeout_sec is not None:
            msg["drain_timeout_sec"] = float(drain_timeout_sec)
        if signal_delay_sec is not None:
            msg["signal_delay_sec"] = float(signal_delay_sec)
        await link.send_json(msg)
        link.last_hub_event_at = time.time()
        link.last_hub_event_type = "core.update.request"
        link.last_control_request_id = request_id
        link.last_control_request_at = time.time()
        link.last_control_action = action_norm
        link.last_control_reason = str(reason or "hub.member_control")
        link.last_control_result_at = None
        link.last_control_result = {"ok": None, "state": "requested", "request_id": request_id}
        payload = {
            "node_id": node_id,
            "request_id": request_id,
            "action": action_norm,
            "target_rev": str(target_rev or ""),
            "target_version": str(target_version or ""),
            "reason": str(reason or "hub.member_control"),
        }
        _publish_link_event("subnet.member.update.requested", payload)
        return {"ok": True, "accepted": True, **payload}

    async def update_member_control_result(self, node_id: str, *, result: dict[str, Any]) -> dict[str, Any]:
        link = await self._get_link(node_id)
        if not link:
            return {"ok": False, "error": "member_not_connected", "node_id": node_id}
        payload = dict(result or {})
        link.last_control_result_at = time.time()
        link.last_control_result = payload
        request_id = str(payload.get("request_id") or "").strip()
        action = str(payload.get("action") or "").strip()
        if request_id:
            link.last_control_request_id = request_id
        if action:
            link.last_control_action = action
        outbound = {
            "node_id": node_id,
            "result": dict(link.last_control_result),
            "captured_at": link.last_control_result_at,
        }
        _publish_link_event("subnet.member.update.result", outbound)
        return {"ok": True, **outbound}

    async def broadcast_event(self, *, event_type: str, payload: dict[str, Any], source: str = "hub") -> dict[str, Any]:
        event_type_norm = str(event_type or "").strip()
        if not event_type_norm:
            return {"sent": 0, "failed": 0}
        payload_dict = payload if isinstance(payload, dict) else {"value": payload}
        target_node_id = _target_node_id_for_hub_event(event_type_norm, payload_dict)
        msg = {
            "t": "hub.event",
            "event": {
                "type": event_type_norm,
                "payload": payload_dict,
                "source": str(source or "hub"),
                "ts": time.time(),
            },
        }
        async with self._lock:
            links = list(self._links.values())
        sent = 0
        failed = 0
        for link in links:
            if target_node_id and link.node_id != target_node_id:
                continue
            try:
                await link.send_json(msg)
                link.last_hub_event_at = time.time()
                link.last_hub_event_type = event_type_norm
                if event_type_norm == "core.update.status":
                    link.last_hub_core_update_state = str((payload or {}).get("state") or "").strip() or None
                    link.last_hub_core_update_action = str((payload or {}).get("action") or "").strip() or None
                sent += 1
            except Exception:
                failed += 1
        self._hub_event_total += sent
        if event_type_norm == "core.update.status":
            self._hub_core_update_broadcast_total += sent
        return {"sent": sent, "failed": failed}

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        items: list[dict[str, Any]] = []
        for link in sorted(self._links.values(), key=lambda item: item.node_id):
            items.append(
                {
                    "node_id": link.node_id,
                    "hostname": link.hostname,
                    "roles": list(link.roles),
                    "node_names": list(link.node_names),
                    "connected_at": link.connected_at,
                    "connected_ago_s": round(max(0.0, now - float(link.connected_at or now)), 3),
                    "last_message_ago_s": round(max(0.0, now - float(link.last_message_at or now)), 3),
                    "last_hub_event_ago_s": (
                        round(max(0.0, now - float(link.last_hub_event_at)), 3)
                        if link.last_hub_event_at
                        else None
                    ),
                    "last_snapshot_ago_s": (
                        round(max(0.0, now - float(link.last_snapshot_at)), 3)
                        if link.last_snapshot_at
                        else None
                    ),
                    "last_hub_event_type": link.last_hub_event_type,
                    "last_hub_core_update_state": link.last_hub_core_update_state,
                    "last_hub_core_update_action": link.last_hub_core_update_action,
                    "last_control_request_id": link.last_control_request_id,
                    "last_control_request_ago_s": (
                        round(max(0.0, now - float(link.last_control_request_at)), 3)
                        if link.last_control_request_at
                        else None
                    ),
                    "last_control_action": link.last_control_action,
                    "last_control_reason": link.last_control_reason,
                    "last_control_result_ago_s": (
                        round(max(0.0, now - float(link.last_control_result_at)), 3)
                        if link.last_control_result_at
                        else None
                    ),
                    "last_control_result": dict(link.last_control_result) if isinstance(link.last_control_result, dict) else {},
                    "node_snapshot": dict(link.node_snapshot) if isinstance(link.node_snapshot, dict) else {},
                    "pending_rpc": len(link.pending_rpc),
                    "connected": True,
                }
            )
        return {
            "role": "hub",
            "member_total": len(items),
            "connected_total": len(items),
            "hub_event_total": self._hub_event_total,
            "hub_core_update_broadcast_total": self._hub_core_update_broadcast_total,
            "yjs_replication": {
                "ingest_total": int(self._yjs_ingest_total),
                "ingest_bytes": int(self._yjs_ingest_bytes),
                "live_apply_total": int(self._yjs_live_apply_total),
                "live_apply_failed_total": int(self._yjs_live_apply_failed_total),
                "broadcast_total": int(self._yjs_broadcast_total),
                "broadcast_failed_total": int(self._yjs_broadcast_failed_total),
                "broadcast_suppressed_total": int(self._yjs_broadcast_suppressed_total),
                "broadcast_suppressed_bytes": int(self._yjs_broadcast_suppressed_bytes),
                "last_broadcast_suppressed_ago_s": (
                    round(max(0.0, now - self._last_yjs_broadcast_suppressed_at), 3)
                    if self._last_yjs_broadcast_suppressed_at
                    else None
                ),
                "last_broadcast_suppressed_reason": self._last_yjs_broadcast_suppressed_reason or None,
                "last_broadcast_suppressed_source": self._last_yjs_broadcast_suppressed_source or None,
                "last_broadcast_suppressed_channel": self._last_yjs_broadcast_suppressed_channel or None,
                "last_broadcast_suppressed_webspace_id": self._last_yjs_broadcast_suppressed_webspace_id or None,
                "last_broadcast_suppressed_bytes": int(self._last_yjs_broadcast_suppressed_bytes),
                "last_ingest_ago_s": round(max(0.0, now - self._last_yjs_ingest_at), 3) if self._last_yjs_ingest_at else None,
                "last_ingest_node_id": self._last_yjs_ingest_node_id or None,
                "last_ingest_webspace_id": self._last_yjs_ingest_webspace_id or None,
                "last_ingest_bytes": int(self._last_yjs_ingest_bytes),
                "member_infrastate_projection_total": int(self._member_infrastate_projection_total),
                "member_infrastate_projection_failed_total": int(self._member_infrastate_projection_failed_total),
                "last_member_infrastate_projection_ago_s": (
                    round(max(0.0, now - self._last_member_infrastate_projection_at), 3)
                    if self._last_member_infrastate_projection_at
                    else None
                ),
                "last_member_infrastate_projection_node_id": self._last_member_infrastate_projection_node_id or None,
                "last_member_infrastate_projection_webspace_id": self._last_member_infrastate_projection_webspace_id or None,
            },
            "members": items,
            "updated_at": now,
        }

    async def handle_rpc_response(self, node_id: str, msg: dict[str, Any]) -> bool:
        rid = msg.get("id")
        if not isinstance(rid, str) or not rid:
            return False
        link = await self._get_link(node_id)
        if not link:
            return False
        fut = link.pending_rpc.pop(rid, None)
        if not fut:
            return False
        if fut.done():
            return True
        ok = bool(msg.get("ok", False))
        if ok:
            fut.set_result(msg.get("result"))
        else:
            err = msg.get("error") or "rpc_failed"
            fut.set_exception(RuntimeError(str(err)))
        return True

    async def rpc_tools_call(self, node_id: str, *, tool: str, arguments: dict[str, Any] | None, timeout: float | None, dev: bool) -> Any:
        link = await self._get_link(node_id)
        if not link:
            raise ConnectionError("member_not_connected")
        rid = f"rpc_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        link.pending_rpc[rid] = fut
        await link.send_json(
            {
                "t": "rpc.req",
                "id": rid,
                "method": "tools.call",
                "params": {
                    "tool": tool,
                    "arguments": arguments or {},
                    "timeout": timeout,
                    "dev": bool(dev),
                },
            }
        )
        try:
            if timeout is None:
                return await asyncio.wait_for(fut, timeout=30.0)
            return await asyncio.wait_for(fut, timeout=float(timeout) + 5.0)
        finally:
            link.pending_rpc.pop(rid, None)

    def _note_yjs_broadcast_suppressed(
        self,
        *,
        webspace_id: str,
        update: bytes,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload_len = len(update or b"")
        meta = dict(metadata or {})
        self._yjs_broadcast_suppressed_total += 1
        self._yjs_broadcast_suppressed_bytes += payload_len
        self._last_yjs_broadcast_suppressed_at = time.time()
        self._last_yjs_broadcast_suppressed_reason = str(reason or "").strip()
        self._last_yjs_broadcast_suppressed_source = str(meta.get("source") or "").strip()
        self._last_yjs_broadcast_suppressed_channel = str(meta.get("channel") or "").strip()
        self._last_yjs_broadcast_suppressed_webspace_id = str(webspace_id or "").strip() or "default"
        self._last_yjs_broadcast_suppressed_bytes = payload_len

    async def broadcast_yjs_update(
        self,
        *,
        webspace_id: str,
        update: bytes,
        origin_node_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Broadcast an update to all connected members except the origin.
        """
        if not update:
            return
        allowed, reason = _hub_yjs_broadcast_policy(update, metadata)
        if not allowed:
            self._note_yjs_broadcast_suppressed(
                webspace_id=webspace_id,
                update=update,
                reason=reason,
                metadata=metadata,
            )
            return
        b64 = base64.b64encode(update).decode("ascii")
        async with self._lock:
            links = list(self._links.values())
        for link in links:
            if origin_node_id and link.node_id == origin_node_id:
                continue
            try:
                await link.send_json(
                    {
                        "t": "yjs.update",
                        "webspace_id": webspace_id,
                        "update_b64": b64,
                        "origin_node_id": origin_node_id,
                        "ts": time.time(),
                    }
                )
                self._yjs_broadcast_total += 1
            except Exception:
                # best-effort
                self._yjs_broadcast_failed_total += 1
                continue

    async def ingest_member_yjs_update(self, *, node_id: str, webspace_id: str, update: bytes) -> None:
        """
        Apply member-provided Yjs update to hub, then fan it out to other members.
        """
        if not update:
            return
        self._yjs_ingest_total += 1
        self._yjs_ingest_bytes += len(update)
        self._last_yjs_ingest_at = time.time()
        self._last_yjs_ingest_node_id = str(node_id or "")
        self._last_yjs_ingest_webspace_id = str(webspace_id or "default")
        self._last_yjs_ingest_bytes = len(update)
        store = get_ystore_for_webspace(webspace_id)
        async with suppress_ystore_write_notifications():
            await store.write(update)
        applied = apply_update_to_live_room(
            webspace_id,
            update,
            root_names=["data", "ui"],
            source="subnet.link_manager",
            owner="core:subnet_link_manager",
            channel="core.subnet.link.update",
        )
        if applied:
            self._yjs_live_apply_total += 1
        else:
            self._yjs_live_apply_failed_total += 1
        await self.broadcast_yjs_update(webspace_id=webspace_id, update=update, origin_node_id=node_id)

    async def ingest_member_node_state(self, *, node_id: str, webspace_id: str, state: dict[str, Any]) -> None:
        """
        Merge a member-owned data.nodes/<node_id> state branch into the hub YDoc.

        Member-local YDocs store node-owned skill state under the shared
        data.nodes JSON envelope. Raw Yjs updates for that envelope can clobber
        sibling nodes, so member-link replication uses this semantic merge for
        the runtime-owned hub document.
        """
        node_key = str(node_id or "").strip()
        if not node_key or not isinstance(state, dict):
            return
        ws_id = str(webspace_id or "").strip() or "default"
        encoded_size = 0
        try:
            encoded_size = len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except Exception:
            encoded_size = 0
        self._yjs_ingest_total += 1
        self._yjs_ingest_bytes += int(encoded_size)
        self._last_yjs_ingest_at = time.time()
        self._last_yjs_ingest_node_id = node_key
        self._last_yjs_ingest_webspace_id = ws_id
        self._last_yjs_ingest_bytes = int(encoded_size)
        state_copy = json.loads(json.dumps(state))

        def _merge_node_state(ydoc: Any, txn: Any) -> None:
            data_map = ydoc.get_map("data")
            nodes = _coerce_json_dict(data_map.get("nodes"))
            merged_state = _member_node_state_for_ingest(nodes.get(node_key), state_copy)
            if nodes.get(node_key) == merged_state:
                return
            nodes[node_key] = merged_state
            data_map.set(txn, "nodes", nodes)

        live_scheduled = mutate_live_room(
            ws_id,
            _merge_node_state,
            root_names=["data"],
            source="subnet.link_manager.node_state",
            owner="core:subnet_link_manager",
            channel="core.subnet.link.node_state",
            governed=True,
        )
        if live_scheduled:
            self._yjs_live_apply_total += 1
            _log.info(
                "ingested member node state via live room node_id=%s webspace=%s bytes=%d",
                node_key,
                ws_id,
                int(encoded_size),
            )
            return

        try:
            async with ystore_write_metadata(
                root_names=["data"],
                source="subnet.link_manager.node_state",
                owner="core:subnet_link_manager",
                channel="core.subnet.link.node_state",
                governed=True,
            ):
                async with async_get_ydoc(ws_id, load_mark_roots=["data"], governed=True) as ydoc:
                    data_map = ydoc.get_map("data")
                    nodes = _coerce_json_dict(data_map.get("nodes"))
                    merged_state = _member_node_state_for_ingest(nodes.get(node_key), state_copy)
                    if nodes.get(node_key) == merged_state:
                        return
                    nodes[node_key] = merged_state
                    with ydoc.begin_transaction() as txn:
                        data_map.set(txn, "nodes", nodes)
            self._yjs_live_apply_total += 1
            _log.info(
                "ingested member node state via store node_id=%s webspace=%s bytes=%d",
                node_key,
                ws_id,
                int(encoded_size),
            )
        except Exception:
            self._yjs_live_apply_failed_total += 1
            _log.warning("failed to ingest member node state node_id=%s webspace=%s", node_key, ws_id, exc_info=True)

    async def ingest_member_bus_event(self, *, node_id: str, event: dict[str, Any]) -> None:
        """
        Publish a member event on hub local bus so hub router/UI can react.
        """
        try:
            typ = event.get("type")
            payload = event.get("payload") or {}
            source = event.get("source") or "subnet.member"
            ts = float(event.get("ts") or time.time())
            if not isinstance(typ, str) or not typ:
                return
            if not isinstance(payload, dict):
                payload = {"value": payload}
            meta = payload.get("_meta") if isinstance(payload, dict) else None
            if not isinstance(meta, dict):
                meta = {}
            payload["_meta"] = {**meta, "subnet_origin_node_id": node_id}
            get_ctx().bus.publish(DomainEvent(type=typ, payload=payload, source=str(source), ts=ts))
        except Exception:
            _log.debug("failed to ingest member bus event node_id=%s", node_id, exc_info=True)


_HUB_MANAGER: HubLinkManager | None = None


def get_hub_link_manager() -> HubLinkManager:
    global _HUB_MANAGER
    if _HUB_MANAGER is None:
        _HUB_MANAGER = HubLinkManager()
    return _HUB_MANAGER


def hub_link_manager_snapshot() -> dict[str, Any]:
    return get_hub_link_manager().snapshot()
