from __future__ import annotations

from typing import Any

from adaos.services import device_inventory as _service


def _fallback_redevice_devices() -> list[dict[str, Any]]:
    try:
        from adaos.sdk.redevice import compact_endpoint, list_endpoints
    except Exception:
        return []

    devices: list[dict[str, Any]] = []
    for raw in list_endpoints(sync_registry=False):
        compact = compact_endpoint(raw)
        endpoint_id = str(compact.get("endpoint_id") or compact.get("code") or "").strip()
        if not endpoint_id:
            continue
        state = str(compact.get("online_state") or "").strip() or "unknown"
        devices.append(
            {
                "ref": f"redevice:{endpoint_id}",
                "kind": "redevice",
                "identity": {
                    "link_id": endpoint_id,
                    "node_id": endpoint_id,
                    "endpoint_id": endpoint_id,
                    "pair_code": compact.get("code") or None,
                    "node_names": [],
                },
                "policy": {
                    "present": True,
                    "managed_state": "managed",
                    "effective_name": compact.get("display_name") or endpoint_id,
                    "display_name": compact.get("display_name") or None,
                    "access_class": "device",
                    "aliases": list(compact.get("aliases") or []),
                    "labels": list(compact.get("labels") or []),
                },
                "observation": {
                    "online": bool(compact.get("online")),
                    "connection_state": state,
                    "last_seen_at": raw.get("last_seen_at"),
                    "source": "redevice_root_snapshot",
                },
                "runtime": {
                    "snapshot_state": state,
                    "route_mode": "root_command_poll",
                    "connected_to_subnet": bool(compact.get("online")),
                    "runtime_version": compact.get("software_version") or None,
                    "software_version": compact.get("software_version") or None,
                    "software_version_code": compact.get("software_version_code") or None,
                    "served_version": compact.get("served_version") or None,
                    "served_version_code": compact.get("served_version_code") or None,
                    "version_status": compact.get("version_status") or "unknown",
                    "active_app": compact.get("active_app"),
                    "active_surface": compact.get("active_surface"),
                },
                "diagnostics": {
                    "policy_source": "root_snapshot",
                    "endpoint_policy": raw.get("endpoint_policy") or None,
                    "endpoint_manifest": raw.get("endpoint_manifest") or None,
                    "diagnostic_report": raw.get("diagnostic_report") or None,
                    "endpoint_health": raw.get("endpoint_health") or None,
                    "service_state": raw.get("service_state") or None,
                    "last_event": raw.get("last_event") or None,
                    "version_info": compact.get("version_info") or None,
                },
            }
        )
    return devices


def list_devices(kind: str | None = None) -> list[dict[str, Any]]:
    normalized = str(kind or "").strip().lower() or None
    if normalized not in {"browser", "member", "redevice"}:
        normalized = None
    try:
        devices = _service.list_devices(kind=normalized)
    except Exception:
        if normalized == "redevice":
            return _fallback_redevice_devices()
        raise
    if normalized == "redevice" and not devices:
        return _fallback_redevice_devices()
    return devices


def get_device(device_ref: str) -> dict[str, Any] | None:
    token = str(device_ref or "")
    try:
        return _service.get_device(token)
    except Exception:
        if token.startswith("redevice:"):
            for item in _fallback_redevice_devices():
                if str(item.get("ref") or "") == token:
                    return item
        raise


def inspect_device(device_ref: str) -> dict[str, Any] | None:
    token = str(device_ref or "")
    try:
        return _service.inspect_device(token)
    except Exception:
        if token.startswith("redevice:"):
            return get_device(token)
        raise


def make_device_ref(kind: str, link_id: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized not in {"browser", "member", "redevice"}:
        raise ValueError("kind must be 'browser', 'member', or 'redevice'")
    return _service.make_device_ref(normalized, str(link_id or ""))

