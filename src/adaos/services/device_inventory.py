from __future__ import annotations

import time
from typing import Any, Literal, Mapping

from adaos.services import access_links as _access_links
from adaos.services.node_display import normalize_node_names
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.registry.subnet_runtime_projection import (
    subnet_runtime_projection_freshness,
)
from adaos.services.redevice_versions import endpoint_version_info

DeviceKind = Literal["browser", "member", "redevice"]


def _now_ts() -> float:
    return float(time.time())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_or_none(value: Any) -> str | None:
    token = _text(value)
    return token or None


def _endpoint_assignment(value: Any) -> dict[str, Any] | None:
    data = _mapping(value)
    role = _text_or_none(data.get("role") or data.get("assignment"))
    if not role:
        return None
    out = dict(data)
    out["role"] = role
    out.setdefault("assignment", role)
    return out


def _float_or_none(value: Any) -> float | None:
    try:
        token = float(value) if value is not None else None
    except Exception:
        return None
    if token is None or token <= 0.0:
        return None
    return token


def _bool_or_none(value: Any) -> bool | None:
    return bool(value) if isinstance(value, bool) else None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _local_redevice_scope() -> tuple[str, str]:
    try:
        from adaos.services.agent_context import get_ctx

        conf = getattr(get_ctx(), "config", None)
    except Exception:
        conf = None
    if conf is None:
        try:
            from adaos.services.node_config import load_config

            conf = load_config()
        except Exception:
            conf = None
    if conf is None:
        return "", ""
    hub_id = _text(getattr(conf, "subnet_id", ""))
    owner_id = _text(getattr(conf, "owner_id", ""))
    root = getattr(conf, "root_settings", None)
    owner = getattr(root, "owner", None)
    owner_id = owner_id or _text(getattr(owner, "owner_id", ""))
    return hub_id, owner_id


def _redevice_scope(entry: Mapping[str, Any]) -> tuple[str, str]:
    policy = _mapping(entry.get("endpoint_policy"))
    manifest = _mapping(entry.get("endpoint_manifest"))
    hub_id = (
        _text(entry.get("hub_id"))
        or _text(entry.get("subnet_id"))
        or _text(policy.get("hub_id"))
        or _text(policy.get("subnet_id"))
        or _text(manifest.get("hub_id"))
        or _text(manifest.get("subnet_id"))
    )
    owner_id = (
        _text(entry.get("owner_id"))
        or _text(policy.get("owner_id"))
        or _text(policy.get("subnet_owner_id"))
        or _text(manifest.get("owner_id"))
    )
    return hub_id, owner_id


def _redevice_entry_matches_local_scope(entry: Mapping[str, Any]) -> bool:
    expected_hub, expected_owner = _local_redevice_scope()
    if not expected_hub and not expected_owner:
        return True
    hub_id, owner_id = _redevice_scope(entry)
    if not hub_id and not owner_id:
        return False
    if expected_hub and hub_id and hub_id != expected_hub:
        return False
    if expected_owner and owner_id and owner_id != expected_owner:
        return False
    return True


def _list_of_texts(value: Any, *, role: str | None = None) -> list[str]:
    items = list(value) if isinstance(value, list) else []
    return normalize_node_names(items, role=role)


def _list_of_raw_texts(value: Any) -> list[str]:
    items = list(value) if isinstance(value, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = _text(item)
        folded = token.casefold()
        if not token or folded in seen:
            continue
        seen.add(folded)
        out.append(token)
    return out


def _list_of_labels(value: Any) -> list[dict[str, Any]]:
    items = list(value) if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        text = _text(item.get("text") or item.get("label") or item.get("value"))
        if not text:
            continue
        locale = _text(item.get("locale")) or "und"
        role = _text(item.get("role")) or "alias"
        key = (text.casefold(), locale.casefold(), role.casefold())
        if key in seen:
            continue
        seen.add(key)
        label = {
            "text": text,
            "locale": locale,
            "role": role,
            "status": _text(item.get("status")) or "confirmed",
        }
        source = _text(item.get("source"))
        if source:
            label["source"] = source
        out.append(label)
    return out


def _unique_runtime_sources(*values: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        token = _text(value)
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _is_expired(entry: Mapping[str, Any] | None, *, now: float) -> bool:
    data = entry if isinstance(entry, Mapping) else {}
    expires_at = _float_or_none(data.get("expires_at"))
    return bool(expires_at is not None and expires_at <= now)


def _connected_to_subnet(data: Mapping[str, Any] | None) -> bool | None:
    payload = data if isinstance(data, Mapping) else {}
    connected = _bool_or_none(payload.get("connected_to_subnet"))
    if connected is not None:
        return connected
    return _bool_or_none(payload.get("connected_to_hub"))


def _request_webspace_reload_for_device(
    kind: DeviceKind,
    link_id: str,
    device_ref: str,
    *,
    reason: str,
) -> dict[str, Any]:
    if kind != "member":
        return {"attempted": False, "emitted": False, "reason": "not_member"}
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.eventbus import emit as bus_emit
        from adaos.services.yjs.webspace import default_webspace_id

        webspace_id = default_webspace_id()
        bus_emit(
            get_ctx().bus,
            "desktop.webspace.reload",
            {
                "webspace_id": webspace_id,
                "reason": reason,
                "source_of_truth": "device_inventory",
                "device_ref": device_ref,
                "device_kind": kind,
                "node_id": link_id,
            },
            source="device_inventory",
        )
        return {"attempted": True, "emitted": True, "webspace_id": webspace_id}
    except Exception:
        import logging

        logging.getLogger("adaos.device_inventory").debug(
            "failed to request webspace reload for device_ref=%s", device_ref, exc_info=True
        )
        return {"attempted": True, "emitted": False, "reason": "emit_failed"}


def make_device_ref(kind: DeviceKind, link_id: str) -> str:
    token = _text(link_id)
    if not token:
        raise ValueError("link id is required")
    return f"{kind}:{token}"


def parse_device_ref(device_ref: str) -> tuple[DeviceKind, str] | None:
    token = _text(device_ref)
    if ":" not in token:
        return None
    kind_token, _, link_id = token.partition(":")
    if kind_token not in {"browser", "member", "redevice"}:
        return None
    link_token = _text(link_id)
    if not link_token:
        return None
    return kind_token, link_token


def _build_policy_block(
    *,
    kind: DeviceKind,
    entry_id: str,
    policy_entry: Mapping[str, Any] | None,
    effective_name: str,
    now: float,
) -> dict[str, Any]:
    entry = policy_entry if isinstance(policy_entry, Mapping) else {}
    present = bool(entry)
    revoked = bool(entry.get("revoked")) if present else False
    admission_policy = _text(entry.get("admission_policy")) if present else ""
    expired = _is_expired(entry, now=now) if present else False
    managed_state = (
        "denied"
        if admission_policy == "deny" or revoked
        else "detached"
        if admission_policy == "detached"
        else "expired"
        if expired
        else "managed"
        if present
        else "observed_only"
    )
    access_class = _text(entry.get("access_class")) or ("device" if kind == "member" else "device")
    lifetime_mode = _text(entry.get("lifetime_mode")) or "permanent"
    return {
        "present": present,
        "managed_state": managed_state,
        "display_name": _text_or_none(entry.get("display_name")),
        "effective_name": effective_name or entry_id,
        "access_class": access_class,
        "lifetime_mode": lifetime_mode,
        "expires_at": _float_or_none(entry.get("expires_at")),
        "revoked": revoked,
        "revoked_at": _float_or_none(entry.get("revoked_at")),
        "admission_policy": admission_policy or ("deny" if revoked else "allow"),
        "detached_at": _float_or_none(entry.get("detached_at")),
        "denied_at": _float_or_none(entry.get("denied_at")),
        "aliases": _list_of_raw_texts(entry.get("aliases")),
        "labels": _list_of_labels(entry.get("labels")),
    }


def _resolve_member_node_names(
    *,
    live_entry: Mapping[str, Any] | None,
    live_snapshot: Mapping[str, Any] | None,
    directory_entry: Mapping[str, Any] | None,
    runtime_projection: Mapping[str, Any] | None,
    policy_entry: Mapping[str, Any] | None,
) -> list[str]:
    for value in (
        live_entry.get("node_names") if isinstance(live_entry, Mapping) else None,
        live_snapshot.get("node_names") if isinstance(live_snapshot, Mapping) else None,
        directory_entry.get("node_names") if isinstance(directory_entry, Mapping) else None,
        runtime_projection.get("node_names") if isinstance(runtime_projection, Mapping) else None,
        policy_entry.get("node_names") if isinstance(policy_entry, Mapping) else None,
    ):
        names = _list_of_texts(value, role="member")
        if names:
            return names
    return []


def _effective_name(
    *,
    policy_entry: Mapping[str, Any] | None,
    node_names: list[str] | None = None,
    hostname: str | None = None,
    fallback_id: str,
) -> str:
    display_name = _text(policy_entry.get("display_name")) if isinstance(policy_entry, Mapping) else ""
    if display_name:
        return display_name
    normalized_names = _list_of_texts(node_names or [], role="member")
    if normalized_names:
        return normalized_names[0]
    host = _text(hostname)
    if host:
        return host
    return fallback_id


def _runtime_projection_like(
    runtime_projection: Mapping[str, Any] | None,
    live_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    projection = _mapping(runtime_projection)
    if projection:
        connected = _connected_to_subnet(projection)
        if connected is not None:
            projection["connected_to_subnet"] = connected
            projection["connected_to_hub"] = connected
    if projection:
        snapshot = _mapping(projection.get("snapshot"))
        if live_snapshot and (
            _float_or_none(live_snapshot.get("captured_at")) or 0.0
        ) >= (_float_or_none(snapshot.get("captured_at")) or 0.0):
            projection["snapshot"] = dict(live_snapshot)
            if live_snapshot.get("captured_at") is not None:
                projection["captured_at"] = live_snapshot.get("captured_at")
            if isinstance(live_snapshot.get("ready"), bool):
                projection["ready"] = live_snapshot.get("ready")
            if live_snapshot.get("node_state") is not None:
                projection["node_state"] = live_snapshot.get("node_state")
            if live_snapshot.get("route_mode") is not None:
                projection["route_mode"] = live_snapshot.get("route_mode")
            connected = _connected_to_subnet(live_snapshot)
            if connected is not None:
                projection["connected_to_subnet"] = connected
                projection["connected_to_hub"] = connected
        return projection
    snapshot = _mapping(live_snapshot)
    if not snapshot:
        return {}
    connected = _connected_to_subnet(snapshot)
    return {
        "snapshot": snapshot,
        "captured_at": _float_or_none(snapshot.get("captured_at")),
        "ready": _bool_or_none(snapshot.get("ready")),
        "node_state": _text_or_none(snapshot.get("node_state")),
        "route_mode": _text_or_none(snapshot.get("route_mode")),
        "connected_to_subnet": connected,
        "connected_to_hub": connected,
    }


def _hub_link_manager_snapshot() -> dict[str, Any]:
    try:
        from adaos.services.subnet.link_manager import hub_link_manager_snapshot

        return _mapping(hub_link_manager_snapshot())
    except Exception:
        return {}


def _run_coro(coro: Any) -> Any:
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    loop.create_task(coro)
    return None


def _get_hub_link_manager():
    try:
        from adaos.services.subnet.link_manager import get_hub_link_manager

        return get_hub_link_manager()
    except Exception:
        return None


class DeviceInventoryService:
    def list_devices(
        self,
        *,
        kind: DeviceKind | None = None,
        include_detached: bool = False,
    ) -> list[dict[str, Any]]:
        now = _now_ts()
        items: list[dict[str, Any]] = []
        if kind in {None, "browser"}:
            items.extend(self._list_browser_devices(now=now))
        if kind in {None, "member"}:
            items.extend(self._list_member_devices(now=now))
        if kind in {None, "redevice"}:
            items.extend(self._list_redevice_devices(now=now))
        if not include_detached:
            items = [
                item
                for item in items
                if _text(_mapping(item.get("policy")).get("managed_state")) not in {"detached", "denied", "revoked"}
            ]
        items.sort(
            key=lambda item: (
                0 if item.get("kind") == "browser" else 1 if item.get("kind") == "member" else 2,
                0 if _text(item.get("policy", {}).get("access_class")) == "device" else 1,
                _text(item.get("policy", {}).get("effective_name")).casefold(),
                _text(item.get("ref")).casefold(),
            )
        )
        return items

    def _list_redevice_devices(self, *, now: float) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            policy_entries = list(_access_links.list_links("redevice") or [])
        except Exception:
            policy_entries = []
        for raw in policy_entries:
            entry = _mapping(raw)
            if not _redevice_entry_matches_local_scope(entry):
                continue
            endpoint_id = _text(entry.get("id") or entry.get("endpoint_id"))
            if not endpoint_id:
                continue
            policy = _mapping(entry.get("endpoint_policy"))
            manifest = _mapping(entry.get("endpoint_manifest"))
            version_info = endpoint_version_info(entry)
            effective_name = _effective_name(
                policy_entry=entry,
                fallback_id=endpoint_id,
            )
            last_seen = _float_or_none(entry.get("last_seen_at"))
            state = _text(entry.get("connection_state")) or ("online" if bool(entry.get("online")) else "offline")
            items.append(
                {
                    "ref": make_device_ref("redevice", endpoint_id),
                    "kind": "redevice",
                    "identity": {
                        "link_id": endpoint_id,
                        "browser_device_id": None,
                        "node_id": endpoint_id,
                        "hostname": None,
                        "node_names": [],
                        "base_url": None,
                        "endpoint_id": endpoint_id,
                        "pair_code": _text_or_none(entry.get("pair_code") or entry.get("code")),
                        "root_url": _text_or_none(entry.get("root_url")),
                        "endpoint_root_url": _text_or_none(entry.get("endpoint_root_url")),
                    },
                    "policy": _build_policy_block(
                        kind="redevice",
                        entry_id=endpoint_id,
                        policy_entry=entry,
                        effective_name=effective_name,
                        now=now,
                    ),
                    "observation": {
                        "online": bool(entry.get("online")),
                        "connection_state": state,
                        "last_seen_at": last_seen,
                        "source": "redevice_link",
                        "last_webspace_id": None,
                    },
                    "runtime": {
                        "snapshot_ready": None,
                        "snapshot_state": _text_or_none(state),
                        "route_mode": "root_command_poll",
                        "root_url": _text_or_none(entry.get("root_url")),
                        "endpoint_root_url": _text_or_none(entry.get("endpoint_root_url")),
                        "connected_to_subnet": bool(entry.get("online")),
                        "runtime_version": _text_or_none(version_info.get("software_version")),
                        "software_version": _text_or_none(version_info.get("software_version")),
                        "software_version_code": _text_or_none(version_info.get("software_version_code")),
                        "served_version": _text_or_none(version_info.get("served_version")),
                        "served_version_code": _text_or_none(version_info.get("served_version_code")),
                        "version_status": _text_or_none(version_info.get("version_status")),
                        "active_app": _mapping(entry.get("active_app")) or None,
                        "active_surface": _mapping(entry.get("active_surface")) or None,
                        "assignment": _text_or_none(entry.get("assignment")),
                        "assignment_updated_at": _float_or_none(entry.get("assignment_updated_at")),
                        "endpoint_assignment": _endpoint_assignment(entry.get("endpoint_assignment")),
                    },
                    "diagnostics": {
                        "policy_source": "access_links",
                        "runtime_sources": ["redevice_link"],
                        "aggregated_at": now,
                        "endpoint_policy": policy or None,
                        "endpoint_manifest": manifest or None,
                        "diagnostic_report": _mapping(entry.get("diagnostic_report")) or None,
                        "endpoint_health": _mapping(entry.get("endpoint_health")) or None,
                        "service_state": _mapping(entry.get("service_state")) or None,
                        "version_info": version_info,
                    },
                }
            )
        return items

    def _find_device(self, device_ref: str, *, include_detached: bool = False) -> dict[str, Any] | None:
        parsed = parse_device_ref(device_ref)
        if parsed is None:
            return None
        kind, token = parsed
        for item in self.list_devices(kind=kind, include_detached=include_detached):
            if _text(item.get("ref")) == make_device_ref(kind, token):
                return item
        return None

    def get_device(self, device_ref: str) -> dict[str, Any] | None:
        return self._find_device(device_ref, include_detached=False)

    def inspect_device(self, device_ref: str) -> dict[str, Any] | None:
        device = self.get_device(device_ref)
        if device is None:
            return None
        diagnostics = _mapping(device.pop("diagnostics", {}))
        try:
            from adaos.services import device_reconciler as _device_reconciler

            reconcile = _device_reconciler.reconcile_device(device_ref)
        except Exception:
            reconcile = None
        return {
            "device": device,
            "diagnostics": diagnostics,
            "reconcile": reconcile,
        }

    def _device_or_error(
        self,
        device_ref: str,
        *,
        include_detached: bool = False,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        token = _text(device_ref)
        parsed = parse_device_ref(token)
        if parsed is None:
            return None, {"ok": False, "error": "invalid_device_ref", "device_ref": token}
        device = get_device(token)
        if device is None and include_detached:
            device = self._find_device(token, include_detached=True)
        if device is None:
            return None, {"ok": False, "error": "device_not_found", "device_ref": token}
        return device, None

    @staticmethod
    def _policy_present(device: Mapping[str, Any]) -> bool:
        return bool(_mapping(device.get("policy")).get("present"))

    @staticmethod
    def _kind_and_link_id(device_ref: str) -> tuple[DeviceKind, str]:
        parsed = parse_device_ref(device_ref)
        if parsed is None:
            raise ValueError("invalid device ref")
        return parsed

    def rename_device(self, device_ref: str, display_name: str) -> dict[str, Any]:
        device, error = self._device_or_error(device_ref)
        if error is not None:
            return error
        assert device is not None
        if not self._policy_present(device):
            return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
        kind, link_id = self._kind_and_link_id(_text(device_ref))
        names = _list_of_raw_texts(str(display_name or "").split(","))
        primary_name = names[0] if names else ""
        entry = _access_links.rename_link(kind, link_id, primary_name, node_names=names)
        runtime_update = {"attempted": False, "applied": False}
        if kind == "member":
            mgr = _get_hub_link_manager()
            if mgr is not None:
                try:
                    if mgr.is_connected(link_id) and names:
                        runtime_update = {"attempted": True, "applied": True}
                        _run_coro(mgr.set_member_node_names(link_id, node_names=names))
                except Exception:
                    import logging

                    logging.getLogger("adaos.device_inventory").debug(
                        "rename_device runtime update failed device_ref=%s", device_ref, exc_info=True
                    )
        return {
            "ok": True,
            "device_ref": _text(device_ref),
            "entry": entry,
            "device": self.get_device(_text(device_ref)),
            "runtime_update": runtime_update,
        }

    def set_device_lifetime(self, device_ref: str, preset: str) -> dict[str, Any]:
        device, error = self._device_or_error(device_ref)
        if error is not None:
            return error
        assert device is not None
        if not self._policy_present(device):
            return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
        kind, link_id = self._kind_and_link_id(_text(device_ref))
        entry = _access_links.set_link_lifetime(kind, link_id, _text(preset) or "permanent")
        return {
            "ok": True,
            "device_ref": _text(device_ref),
            "entry": entry,
            "device": self.get_device(_text(device_ref)),
        }

    def detach_device(self, device_ref: str) -> dict[str, Any]:
        device, error = self._device_or_error(device_ref, include_detached=True)
        if error is not None:
            return error
        assert device is not None
        kind, link_id = self._kind_and_link_id(_text(device_ref))
        policy = _mapping(device.get("policy"))
        if _text(policy.get("managed_state")) in {"detached", "denied", "revoked"}:
            layout_refresh = _request_webspace_reload_for_device(
                kind,
                link_id,
                _text(device_ref),
                reason="device.already_detached",
            )
            return {
                "ok": True,
                "status": "already_detached",
                "device_ref": _text(device_ref),
                "device": device,
                "runtime_update": {"attempted": False, "applied": False},
                "layout_refresh": layout_refresh,
            }
        entry = _access_links.detach_link(kind, link_id)
        runtime_update = {"attempted": False, "applied": False}
        if kind == "member":
            mgr = _get_hub_link_manager()
            if mgr is not None:
                try:
                    if mgr.is_connected(link_id):
                        runtime_update = {"attempted": True, "applied": True}
                        _run_coro(mgr.unregister(link_id))
                except Exception:
                    import logging

                    logging.getLogger("adaos.device_inventory").debug(
                        "detach_device runtime unregister failed device_ref=%s", device_ref, exc_info=True
                    )
        layout_refresh = _request_webspace_reload_for_device(
            kind,
            link_id,
            _text(device_ref),
            reason="device.detached",
        )
        return {
            "ok": True,
            "device_ref": _text(device_ref),
            "entry": entry,
            "device": self.get_device(_text(device_ref)),
            "runtime_update": runtime_update,
            "layout_refresh": layout_refresh,
        }

    def deny_device(self, device_ref: str) -> dict[str, Any]:
        device, error = self._device_or_error(device_ref, include_detached=True)
        if error is not None:
            return error
        assert device is not None
        kind, link_id = self._kind_and_link_id(_text(device_ref))
        policy = _mapping(device.get("policy"))
        if _text(policy.get("managed_state")) in {"denied", "revoked"}:
            layout_refresh = _request_webspace_reload_for_device(
                kind,
                link_id,
                _text(device_ref),
                reason="device.already_denied",
            )
            return {
                "ok": True,
                "status": "already_denied",
                "device_ref": _text(device_ref),
                "device": device,
                "runtime_update": {"attempted": False, "applied": False},
                "layout_refresh": layout_refresh,
            }
        entry = _access_links.deny_link(kind, link_id)
        runtime_update = {"attempted": False, "applied": False}
        if kind == "member":
            mgr = _get_hub_link_manager()
            if mgr is not None:
                try:
                    if mgr.is_connected(link_id):
                        runtime_update = {"attempted": True, "applied": True}
                        _run_coro(mgr.unregister(link_id))
                except Exception:
                    import logging

                    logging.getLogger("adaos.device_inventory").debug(
                        "deny_device runtime unregister failed device_ref=%s", device_ref, exc_info=True
                    )
        layout_refresh = _request_webspace_reload_for_device(
            kind,
            link_id,
            _text(device_ref),
            reason="device.denied",
        )
        return {
            "ok": True,
            "device_ref": _text(device_ref),
            "entry": entry,
            "device": self.get_device(_text(device_ref)),
            "runtime_update": runtime_update,
            "layout_refresh": layout_refresh,
        }

    def _list_browser_devices(self, *, now: float) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            policy_entries = list(_access_links.list_links("browser") or [])
        except Exception:
            policy_entries = []
        for raw in policy_entries:
            entry = _mapping(raw)
            device_id = _text(entry.get("id"))
            if not device_id:
                continue
            hostname = _text_or_none(entry.get("hostname"))
            effective_name = _effective_name(
                policy_entry=entry,
                hostname=hostname,
                fallback_id=device_id,
            )
            items.append(
                {
                    "ref": make_device_ref("browser", device_id),
                    "kind": "browser",
                    "identity": {
                        "link_id": device_id,
                        "browser_device_id": device_id,
                        "node_id": None,
                        "hostname": hostname,
                        "node_names": [],
                        "base_url": None,
                        "browser_family": _text_or_none(entry.get("browser_family")),
                        "os_name": _text_or_none(entry.get("os_name")),
                        "form_factor": _text_or_none(entry.get("form_factor")),
                        "user_agent": _text_or_none(entry.get("user_agent")),
                    },
                    "policy": _build_policy_block(
                        kind="browser",
                        entry_id=device_id,
                        policy_entry=entry,
                        effective_name=effective_name,
                        now=now,
                    ),
                    "observation": {
                        "online": bool(entry.get("online")),
                        "connection_state": _text_or_none(entry.get("connection_state")),
                        "last_seen_at": _float_or_none(entry.get("last_seen_at")),
                        "source": "browser_session",
                        "last_webspace_id": _text_or_none(entry.get("last_webspace_id")),
                    },
                    "runtime": {
                        "snapshot_ready": None,
                        "snapshot_state": None,
                        "route_mode": None,
                        "connected_to_subnet": None,
                        "runtime_version": None,
                    },
                    "diagnostics": {
                        "policy_source": "access_links",
                        "runtime_sources": ["browser_session"],
                        "aggregated_at": now,
                    },
                }
            )
        return items

    def _list_member_devices(self, *, now: float) -> list[dict[str, Any]]:
        try:
            policy_entries = list(_access_links.list_links("member") or [])
        except Exception:
            policy_entries = []
        try:
            directory_nodes = list(get_directory().list_known_nodes() or [])
        except Exception:
            directory_nodes = []
        snapshot = _hub_link_manager_snapshot()
        live_members = list(snapshot.get("members") or [])

        policy_by_id = {
            _text(item.get("id")): _mapping(item)
            for item in policy_entries
            if _text(item.get("id"))
        }
        directory_by_id = {
            _text(item.get("node_id")): _mapping(item)
            for item in directory_nodes
            if _text(item.get("node_id"))
        }
        live_by_id = {
            _text(item.get("node_id")): _mapping(item)
            for item in live_members
            if _text(item.get("node_id"))
        }

        node_ids = sorted({*policy_by_id.keys(), *directory_by_id.keys(), *live_by_id.keys()})
        items: list[dict[str, Any]] = []
        for node_id in node_ids:
            policy_entry = policy_by_id.get(node_id)
            directory_entry = directory_by_id.get(node_id)
            live_entry = live_by_id.get(node_id)
            runtime_projection = _mapping(directory_entry.get("runtime_projection")) if directory_entry else {}
            live_snapshot = _mapping(live_entry.get("node_snapshot")) if live_entry else {}
            projection_like = _runtime_projection_like(runtime_projection, live_snapshot)
            projection_snapshot = _mapping(projection_like.get("snapshot"))
            node_names = _resolve_member_node_names(
                live_entry=live_entry,
                live_snapshot=projection_snapshot,
                directory_entry=directory_entry,
                runtime_projection=projection_like,
                policy_entry=policy_entry,
            )
            hostname = (
                _text_or_none(live_entry.get("hostname")) if live_entry else None
            ) or (
                _text_or_none(directory_entry.get("hostname")) if directory_entry else None
            ) or (
                _text_or_none(policy_entry.get("hostname")) if policy_entry else None
            )
            effective_name = _effective_name(
                policy_entry=policy_entry,
                node_names=node_names,
                hostname=hostname,
                fallback_id=node_id,
            )
            live_connected = bool(live_entry.get("connected")) if live_entry else False
            directory_online = bool(directory_entry.get("online")) if directory_entry else False
            policy_online = bool(policy_entry.get("online")) if policy_entry else False
            online = live_connected or directory_online or policy_online
            runtime_connected = _connected_to_subnet(projection_like)
            if live_connected:
                runtime_connected = True
            freshness = subnet_runtime_projection_freshness(
                projection_like,
                online=online,
                now=now,
            ) if projection_like else {}
            build = _mapping(projection_snapshot.get("build"))
            snapshot_state = _text_or_none(freshness.get("state")) if freshness else None
            last_seen_values = [
                value
                for value in (
                    _float_or_none(policy_entry.get("last_seen_at")) if policy_entry else None,
                    _float_or_none(directory_entry.get("last_seen")) if directory_entry else None,
                )
                if value is not None
            ]
            observation_source = (
                "member_link"
                if live_entry
                else "subnet_directory"
                if directory_entry
                else "member_link"
            )
            connection_state = (
                _text_or_none(policy_entry.get("connection_state")) if policy_entry else None
            ) or (
                "connected" if live_connected else "heartbeat" if directory_online else None
            )
            items.append(
                {
                    "ref": make_device_ref("member", node_id),
                    "kind": "member",
                    "identity": {
                        "link_id": node_id,
                        "browser_device_id": None,
                        "node_id": node_id,
                        "hostname": hostname,
                        "node_names": node_names,
                        "base_url": _text_or_none(directory_entry.get("base_url")) if directory_entry else None,
                    },
                    "policy": _build_policy_block(
                        kind="member",
                        entry_id=node_id,
                        policy_entry=policy_entry,
                        effective_name=effective_name,
                        now=now,
                    ),
                    "observation": {
                        "online": online,
                        "connection_state": connection_state,
                        "last_seen_at": max(last_seen_values) if last_seen_values else None,
                        "source": observation_source,
                        "last_webspace_id": None,
                    },
                    "runtime": {
                        "snapshot_ready": _bool_or_none(projection_like.get("ready")),
                        "snapshot_state": snapshot_state,
                        "route_mode": _text_or_none(projection_like.get("route_mode")),
                        "connected_to_subnet": runtime_connected,
                        "runtime_version": _text_or_none(
                            build.get("runtime_version") or build.get("version")
                        ),
                    },
                    "diagnostics": {
                        "policy_source": "access_links" if policy_entry else "none",
                        "runtime_sources": _unique_runtime_sources(
                            "member_link" if live_entry else "",
                            "subnet_directory" if directory_entry else "",
                        ),
                        "aggregated_at": now,
                    },
                }
            )
        return items


_SERVICE: DeviceInventoryService | None = None


def get_device_inventory_service() -> DeviceInventoryService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DeviceInventoryService()
    return _SERVICE


def list_devices(
    *,
    kind: DeviceKind | None = None,
    include_detached: bool = False,
) -> list[dict[str, Any]]:
    return get_device_inventory_service().list_devices(kind=kind, include_detached=include_detached)


def get_device(device_ref: str) -> dict[str, Any] | None:
    return get_device_inventory_service().get_device(device_ref)


def inspect_device(device_ref: str) -> dict[str, Any] | None:
    return get_device_inventory_service().inspect_device(device_ref)


def rename_device(device_ref: str, display_name: str) -> dict[str, Any]:
    return get_device_inventory_service().rename_device(device_ref, display_name)


def set_device_lifetime(device_ref: str, preset: str) -> dict[str, Any]:
    return get_device_inventory_service().set_device_lifetime(device_ref, preset)


def detach_device(device_ref: str) -> dict[str, Any]:
    return get_device_inventory_service().detach_device(device_ref)


def deny_device(device_ref: str) -> dict[str, Any]:
    return get_device_inventory_service().deny_device(device_ref)


__all__ = [
    "DeviceInventoryService",
    "DeviceKind",
    "get_device",
    "get_device_inventory_service",
    "inspect_device",
    "deny_device",
    "detach_device",
    "list_devices",
    "make_device_ref",
    "parse_device_ref",
    "rename_device",
    "set_device_lifetime",
]
