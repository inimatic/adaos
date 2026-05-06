from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from adaos.services import access_links as _access_links
from adaos.services import device_inventory as _device_inventory

_log = logging.getLogger("adaos.device_access")
_LIFETIME_PRESETS = ["permanent", "1h", "1d", "7d", "30d"]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _toggle(
    enabled: bool,
    *,
    reason: str | None = None,
    presets: list[str] | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"enabled": bool(enabled)}
    if reason:
        payload["reason"] = reason
    if presets is not None:
        payload["presets"] = list(presets)
    if node_id:
        payload["node_id"] = node_id
    return payload


def _run_coro(coro: Any) -> Any:
    try:
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


def _device_or_error(device_ref: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    token = _text(device_ref)
    parsed = _device_inventory.parse_device_ref(token)
    if parsed is None:
        return None, {"ok": False, "error": "invalid_device_ref", "device_ref": token}
    device = _device_inventory.get_device(token)
    if device is None:
        return None, {"ok": False, "error": "device_not_found", "device_ref": token}
    return device, None


def _policy_present(device: Mapping[str, Any]) -> bool:
    return bool(_mapping(device.get("policy")).get("present"))


def _kind_and_link_id(device_ref: str) -> tuple[str, str]:
    parsed = _device_inventory.parse_device_ref(device_ref)
    if parsed is None:
        raise ValueError("invalid device ref")
    return parsed


def get_command_profile(device_ref: str) -> dict[str, Any] | None:
    device, error = _device_or_error(device_ref)
    if error is not None:
        return None
    assert device is not None
    policy = _mapping(device.get("policy"))
    identity = _mapping(device.get("identity"))
    kind = _text(device.get("kind"))
    managed_state = _text(policy.get("managed_state")) or "observed_only"
    policy_present = bool(policy.get("present"))
    revoked = bool(policy.get("revoked"))
    node_id = _text(identity.get("node_id")) or None

    rename_enabled = policy_present
    rename_reason = None if rename_enabled else "device_policy_missing"
    lifetime_enabled = policy_present
    lifetime_reason = None if lifetime_enabled else "device_policy_missing"
    detach_enabled = policy_present and not revoked
    detach_reason = (
        None
        if detach_enabled
        else "already_detached"
        if revoked and policy_present
        else "device_policy_missing"
    )
    apps_enabled = kind == "member" and bool(node_id) and managed_state != "revoked"
    apps_reason = None if apps_enabled else "browser_has_no_node_context" if kind == "browser" else "device_unavailable"

    return {
        "device_ref": _text(device_ref),
        "kind": kind,
        "rename": _toggle(rename_enabled, reason=rename_reason),
        "set_lifetime": _toggle(
            lifetime_enabled,
            reason=lifetime_reason,
            presets=_LIFETIME_PRESETS,
        ),
        "detach": _toggle(detach_enabled, reason=detach_reason),
        "open_apps": _toggle(apps_enabled, reason=apps_reason, node_id=node_id),
        "open_marketplace": _toggle(apps_enabled, reason=apps_reason, node_id=node_id),
    }


def rename_device(device_ref: str, display_name: str) -> dict[str, Any]:
    device, error = _device_or_error(device_ref)
    if error is not None:
        return error
    assert device is not None
    if not _policy_present(device):
        return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
    kind, link_id = _kind_and_link_id(_text(device_ref))
    entry = _access_links.rename_link(kind, link_id, _text(display_name))
    runtime_update = {"attempted": False, "applied": False}
    if kind == "member":
        mgr = _get_hub_link_manager()
        if mgr is not None:
            try:
                if mgr.is_connected(link_id) and _text(display_name):
                    runtime_update = {"attempted": True, "applied": True}
                    _run_coro(mgr.set_member_node_names(link_id, node_names=[_text(display_name)]))
            except Exception:
                _log.debug("rename_device runtime update failed device_ref=%s", device_ref, exc_info=True)
    return {
        "ok": True,
        "device_ref": _text(device_ref),
        "entry": entry,
        "device": _device_inventory.get_device(_text(device_ref)),
        "runtime_update": runtime_update,
    }


def set_device_lifetime(device_ref: str, preset: str) -> dict[str, Any]:
    device, error = _device_or_error(device_ref)
    if error is not None:
        return error
    assert device is not None
    if not _policy_present(device):
        return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
    kind, link_id = _kind_and_link_id(_text(device_ref))
    entry = _access_links.set_link_lifetime(kind, link_id, _text(preset) or "permanent")
    return {
        "ok": True,
        "device_ref": _text(device_ref),
        "entry": entry,
        "device": _device_inventory.get_device(_text(device_ref)),
    }


def detach_device(device_ref: str) -> dict[str, Any]:
    device, error = _device_or_error(device_ref)
    if error is not None:
        return error
    assert device is not None
    if not _policy_present(device):
        return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
    profile = get_command_profile(_text(device_ref)) or {}
    detach_meta = _mapping(profile.get("detach"))
    if not bool(detach_meta.get("enabled")):
        return {
            "ok": False,
            "error": _text(detach_meta.get("reason")) or "device_detach_not_allowed",
            "device_ref": _text(device_ref),
        }
    kind, link_id = _kind_and_link_id(_text(device_ref))
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
                _log.debug("detach_device runtime unregister failed device_ref=%s", device_ref, exc_info=True)
    return {
        "ok": True,
        "device_ref": _text(device_ref),
        "entry": entry,
        "device": _device_inventory.get_device(_text(device_ref)),
        "runtime_update": runtime_update,
    }


__all__ = [
    "detach_device",
    "get_command_profile",
    "rename_device",
    "set_device_lifetime",
]
