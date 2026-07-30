from __future__ import annotations

import socket
import time
import uuid
from typing import Any, Mapping

from adaos.services import access_links as _access_links
from adaos.services import device_inventory as _device_inventory
from adaos.services import device_reconciler as _device_reconciler

_LIFETIME_PRESETS = ["permanent", "1h", "1d", "7d", "30d"]
_LIFETIME_PRESET_LABELS = {
    "permanent": "Permanent",
    "1h": "1h",
    "1d": "1d",
    "7d": "7d",
    "30d": "30d",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _name_list(value: Any) -> list[str]:
    raw_items = str(value or "").split(",") if not isinstance(value, list) else value
    names: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = _text(item)
        if not token:
            continue
        folded = token.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        names.append(token)
    return names


def _policy_name_list(policy: Mapping[str, Any], identity: Mapping[str, Any], fallback: str) -> list[str]:
    names = _name_list(policy.get("display_name"))
    for item in _name_list(identity.get("node_names")):
        if item.casefold() not in {name.casefold() for name in names}:
            names.append(item)
    if not names:
        names = _name_list(fallback)
    return names


def _max_float(*values: Any) -> float | None:
    out: float | None = None
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if out is None or number > out:
            out = number
    return out


def _clamped_unit_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, number))


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    token = _text(value).casefold()
    if not token:
        return None
    if token in {"1", "true", "yes", "on", "muted"}:
        return True
    if token in {"0", "false", "no", "off", "unmuted"}:
        return False
    return None


def _first_provided(*values: Any) -> tuple[bool, Any]:
    for value in values:
        if value is not None:
            return True, value
    return False, None


def _browser_parent_device_id(device_ref: str, identity: Mapping[str, Any] | None = None) -> str:
    parsed = _device_inventory.parse_device_ref(_text(device_ref))
    if parsed is None:
        return ""
    kind, link_id = parsed
    if kind != "browser":
        return ""
    identity_map = _mapping(identity)
    parent = _text(identity_map.get("parent_browser_device_id"))
    if parent:
        return parent
    if "::" in link_id:
        return _text(link_id.split("::", 1)[0])
    return link_id


def _browser_parent_device_ref(device_ref: str, identity: Mapping[str, Any] | None = None) -> str:
    parent_id = _browser_parent_device_id(device_ref, identity)
    return f"browser:{parent_id}" if parent_id else _text(device_ref)


def _registered_option_subtitle(kinds: list[str], refs: list[str], online_count: int) -> str:
    kind_label = ", ".join(kinds[:3])
    if len(kinds) > 3:
        kind_label = f"{kind_label}, +{len(kinds) - 3}"
    parts = [
        f"{online_count}/{len(refs)} online" if refs else "",
        kind_label,
    ]
    return " | ".join([part for part in parts if part])


def _append_registered_name_option(
    by_key: dict[str, dict[str, Any]],
    *,
    name: str,
    device_ref: str,
    kind: str,
    online: bool,
    last_seen_at: Any = None,
    source: str = "device_inventory",
) -> None:
    value = _text(name)
    if not value:
        return
    key = " ".join(value.split()).casefold()
    entry = by_key.setdefault(
        key,
        {
            "value": value,
            "label": value,
            "device_refs": [],
            "kinds": [],
            "online_count": 0,
            "last_seen_at": None,
            "source": source,
        },
    )
    refs = entry.setdefault("device_refs", [])
    if device_ref and device_ref not in refs:
        refs.append(device_ref)
    kinds = entry.setdefault("kinds", [])
    if kind and kind not in kinds:
        kinds.append(kind)
    if online:
        entry["online_count"] = int(entry.get("online_count") or 0) + 1
    entry["last_seen_at"] = _max_float(entry.get("last_seen_at"), last_seen_at)


def _finalize_registered_name_options(by_key: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for raw in by_key.values():
        item = dict(raw)
        refs = [_text(ref) for ref in list(item.get("device_refs") or []) if _text(ref)]
        kinds = [_text(kind) for kind in list(item.get("kinds") or []) if _text(kind)]
        item["device_refs"] = sorted(dict.fromkeys(refs))
        item["kinds"] = sorted(dict.fromkeys(kinds))
        item["online_count"] = int(item.get("online_count") or 0)
        item["device_count"] = len(item["device_refs"])
        item["subtitle"] = _registered_option_subtitle(item["kinds"], item["device_refs"], item["online_count"])
        options.append(item)
    options.sort(
        key=lambda item: (
            0 if int(item.get("online_count") or 0) > 0 else 1,
            -float(item.get("last_seen_at") or 0.0),
            _text(item.get("value")).casefold(),
        )
    )
    return options


def list_registered_device_names(kind: str | None = None) -> list[dict[str, Any]]:
    """Return finite, human device-name candidates from the core device registry."""

    normalized_kind = _text(kind).lower() or None
    if normalized_kind not in {None, "browser", "member", "redevice"}:
        normalized_kind = None
    by_key: dict[str, dict[str, Any]] = {}

    if normalized_kind in {None, "member"}:
        try:
            conf = _load_node_config()
            subnet_id = _text(getattr(conf, "subnet_id_value", None) or getattr(conf, "subnet_id", None))
            for name in _hub_display_names(conf):
                _append_registered_name_option(
                    by_key,
                    name=name,
                    device_ref=f"hub:{subnet_id or 'local'}",
                    kind="hub",
                    online=True,
                    source="node_config",
                )
        except Exception:
            pass

    try:
        devices = list(_device_inventory.list_devices(kind=normalized_kind) or [])
    except Exception:
        devices = []
    for raw in devices:
        device = _mapping(raw)
        ref = _text(device.get("ref"))
        kind_token = _text(device.get("kind"))
        identity = _mapping(device.get("identity"))
        policy = _mapping(device.get("policy"))
        observation = _mapping(device.get("observation"))
        online = bool(observation.get("online"))
        last_seen = observation.get("last_seen_at")
        if kind_token == "browser":
            names = [
                identity.get("device_display_name"),
                policy.get("device_display_name"),
            ]
            for name in names:
                _append_registered_name_option(
                    by_key,
                    name=_text(name),
                    device_ref=_browser_parent_device_ref(ref, identity),
                    kind="browser",
                    online=online,
                    last_seen_at=last_seen,
                )
            continue
        names = []
        names.extend(_name_list(identity.get("node_names")))
        names.extend(_name_list(policy.get("display_name")))
        names.extend(_name_list(policy.get("effective_name")))
        for name in names:
            _append_registered_name_option(
                by_key,
                name=name,
                device_ref=ref,
                kind=kind_token,
                online=online,
                last_seen_at=last_seen,
            )
    return _finalize_registered_name_options(by_key)


def _registered_device_name_suggestions(current_names: list[str] | None = None) -> list[dict[str, Any]]:
    by_key = {
        " ".join(_text(item.get("value")).split()).casefold(): dict(item)
        for item in list_registered_device_names()
        if _text(item.get("value"))
    }
    for name in current_names or []:
        _append_registered_name_option(
            by_key,
            name=name,
            device_ref="",
            kind="current",
            online=False,
            source="current_settings",
        )
    return _finalize_registered_name_options(by_key)


def _toggle(
    enabled: bool,
    *,
    reason: str | None = None,
    presets: list[str] | None = None,
    node_id: str | None = None,
    target: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"enabled": bool(enabled)}
    if reason:
        payload["reason"] = reason
    if presets is not None:
        payload["presets"] = list(presets)
    if node_id:
        payload["node_id"] = node_id
    if target:
        payload["target"] = target
    if isinstance(params, Mapping):
        normalized_params = {
            str(key): value
            for key, value in dict(params).items()
            if value is not None
        }
        if normalized_params:
            payload["params"] = normalized_params
    return payload


def _lifetime_label(policy: Mapping[str, Any]) -> str:
    return _access_links.lifetime_label(
        {
            "lifetime_mode": _text(policy.get("lifetime_mode")) or "permanent",
            "expires_at": policy.get("expires_at"),
        }
    )


def _lifetime_options(meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    enabled = bool(meta.get("enabled"))
    reason = _text(meta.get("reason")) or None
    presets = [
        _text(item)
        for item in list(meta.get("presets") or _LIFETIME_PRESETS)
        if _text(item)
    ] or list(_LIFETIME_PRESETS)
    options: list[dict[str, Any]] = []
    for preset in presets:
        option = {
            "id": preset,
            "label": _LIFETIME_PRESET_LABELS.get(preset, preset),
            "enabled": enabled,
        }
        if reason:
            option["reason"] = reason
        options.append(option)
    return options


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


def _hub_ref_id(device_ref: str) -> str | None:
    token = _text(device_ref)
    if not token.startswith("hub:"):
        return None
    hub_id = _text(token.split(":", 1)[1])
    return hub_id or None


def _config_text(conf: Any, *names: str) -> str:
    for name in names:
        value = _text(getattr(conf, name, None))
        if value:
            return value
    return ""


def _load_node_config():
    from adaos.services.node_config import load_config

    return load_config()


def _set_local_node_names(names: list[str]):
    from adaos.services.node_config import set_node_names

    return set_node_names(names)


def _local_hub_ref_from_member_alias(device_ref: str) -> str | None:
    token = _text(device_ref)
    try:
        parsed = _device_inventory.parse_device_ref(token)
    except Exception:
        return None
    if parsed is None:
        return None
    kind, link_id = parsed
    if kind != "member":
        return None
    try:
        conf = _load_node_config()
    except Exception:
        return None
    role = _config_text(conf, "role", "node_role").lower()
    node_id = _config_text(conf, "node_id_value", "node_id")
    subnet_id = _config_text(conf, "subnet_id_value", "subnet_id")
    if role == "hub" and node_id and link_id == node_id and subnet_id:
        return f"hub:{subnet_id}"
    return None


def _hub_ref_for_device_ref(device_ref: str) -> str | None:
    return _hub_ref_id(device_ref) and _text(device_ref) or _local_hub_ref_from_member_alias(device_ref)


def _hub_config_matches(device_ref: str) -> tuple[Any | None, dict[str, Any] | None]:
    hub_id = _hub_ref_id(device_ref)
    if not hub_id:
        return None, {"ok": False, "error": "invalid_device_ref", "device_ref": _text(device_ref)}
    conf = _load_node_config()
    subnet_id = _text(getattr(conf, "subnet_id_value", None) or getattr(conf, "subnet_id", None))
    if subnet_id and hub_id != subnet_id:
        return None, {
            "ok": False,
            "error": "hub_ref_not_local",
            "device_ref": _text(device_ref),
            "local_hub_id": subnet_id,
        }
    return conf, None


def _hub_display_names(conf: Any) -> list[str]:
    names = _name_list(getattr(conf, "node_names", []))
    if names:
        return names
    return _name_list(socket.gethostname()) or ["hub"]


def _hub_device_settings(device_ref: str) -> dict[str, Any] | None:
    conf, error = _hub_config_matches(device_ref)
    if error is not None:
        return None
    assert conf is not None
    subnet_id = _text(getattr(conf, "subnet_id_value", None) or getattr(conf, "subnet_id", None))
    node_id = _text(getattr(conf, "node_id_value", None) or getattr(conf, "node_id", None))
    names = _hub_display_names(conf)
    primary = names[0]
    device_ref_token = f"hub:{subnet_id or _hub_ref_id(device_ref) or 'local'}"
    command_params = {"device_ref": device_ref_token}
    storage = ".adaos/node.yaml: node.node_names"
    return {
        "device_ref": device_ref_token,
        "kind": "hub",
        "title": primary,
        "id": {
            "value": device_ref_token,
            "kind": "hub",
            "node_id": node_id or None,
            "link_id": subnet_id or None,
        },
        "device": {
            "ref": device_ref_token,
            "kind": "hub",
            "identity": {
                "node_id": node_id or None,
                "subnet_id": subnet_id or None,
                "hostname": socket.gethostname(),
                "node_names": names,
            },
            "policy": {
                "present": True,
                "managed_state": "local_config",
                "display_name": primary,
                "effective_name": primary,
            },
        },
        "status": {
            "online": True,
            "managed_state": "local_config",
            "connection_state": "local",
            "observation_source": "node_config",
            "connected_to_subnet": True,
        },
        "name": {
            "value": ", ".join(names),
            "primary": primary,
            "names": names,
            "label": "Device name",
            "placeholder": "Main hub, Workstation",
            "suggestions": _registered_device_name_suggestions(names),
            "save": _toggle(
                True,
                target="browsers_skill.rename_device",
                params=command_params,
            ),
            "policy": {
                "can_edit": True,
                "status": "local_config",
                "storage": storage,
                "field": "node.node_names",
                "mode": "rename",
                "reason": None,
            },
            "helper": (
                "Primary name is the first comma-separated value. "
                f"canEdit=true status=local_config storage={storage}"
            ),
        },
        "aliases": {
            "labels": [],
            "add": _toggle(False, reason="hub_alias_registry_not_implemented"),
        },
        "lifetime": {
            "current_label": "Local hub",
            "current_mode": "local_config",
            "expires_at": None,
            "set": _toggle(False, reason="hub_lifetime_not_applicable"),
            "options": [],
        },
        "detach": {
            **_toggle(False, reason="hub_detach_not_applicable"),
            "confirm_title": "Detach hub",
            "confirm_message": "Local hub cannot be detached from device settings.",
        },
        "deny": {
            **_toggle(False, reason="hub_deny_not_applicable"),
            "confirm_title": "Deny hub",
            "confirm_message": "Local hub cannot be denied from device settings.",
        },
        "actions": {
            "open_apps": _toggle(bool(node_id), node_id=node_id or None),
            "open_marketplace": _toggle(bool(node_id), node_id=node_id or None),
        },
        "reconcile": {"state": "ok", "issue_total": 0, "issues": [], "actions": {}},
        "adopt": {
            "enabled": False,
            "suggested_display_name": primary,
            "preset": "local_config",
            "target": "browsers_skill.adopt_device",
            "params": command_params,
        },
        "identity": {
            "node_id": node_id or None,
            "subnet_id": subnet_id or None,
            "hostname": socket.gethostname(),
        },
    }


def get_command_profile(device_ref: str) -> dict[str, Any] | None:
    hub_ref = _hub_ref_for_device_ref(device_ref)
    if hub_ref:
        conf, error = _hub_config_matches(hub_ref)
        if error is not None or conf is None:
            return None
        node_id = _text(getattr(conf, "node_id_value", None) or getattr(conf, "node_id", None)) or None
        return {
            "device_ref": hub_ref,
            "kind": "hub",
            "rename": _toggle(True),
            "set_lifetime": _toggle(False, reason="hub_lifetime_not_applicable"),
            "detach": _toggle(False, reason="hub_detach_not_applicable"),
            "deny": _toggle(False, reason="hub_deny_not_applicable"),
            "open_apps": _toggle(bool(node_id), node_id=node_id),
            "open_marketplace": _toggle(bool(node_id), node_id=node_id),
        }
    device, error = _device_or_error(device_ref)
    if error is not None:
        return None
    assert device is not None
    policy = _mapping(device.get("policy"))
    identity = _mapping(device.get("identity"))
    runtime = _mapping(device.get("runtime"))
    kind = _text(device.get("kind"))
    managed_state = _text(policy.get("managed_state")) or "observed_only"
    policy_present = bool(policy.get("present"))
    revoked = bool(policy.get("revoked"))
    node_id = _text(identity.get("node_id")) or None

    rename_enabled = policy_present
    rename_reason = None if rename_enabled else "device_policy_missing"
    lifetime_enabled = policy_present
    lifetime_reason = None if lifetime_enabled else "device_policy_missing"
    detached_or_denied = managed_state in {"detached", "denied", "revoked"} or revoked
    detach_enabled = not detached_or_denied
    detach_reason = None if detach_enabled else "already_detached"
    deny_enabled = managed_state not in {"denied", "revoked"}
    deny_reason = None if deny_enabled else "already_denied"
    apps_enabled = kind == "member" and bool(node_id) and managed_state not in {"detached", "denied", "revoked"}
    apps_reason = None if apps_enabled else "browser_has_no_node_context" if kind == "browser" else "device_unavailable"
    observation = _mapping(device.get("observation"))
    media_enabled = kind == "browser" and bool(observation.get("online"))
    media_reason = None if media_enabled else "browser_offline" if kind == "browser" else "media_control_browser_only"
    media_device_ref = _browser_parent_device_ref(_text(device_ref), identity) if kind == "browser" else _text(device_ref)

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
        "deny": _toggle(deny_enabled, reason=deny_reason),
        "open_apps": _toggle(apps_enabled, reason=apps_reason, node_id=node_id),
        "open_marketplace": _toggle(apps_enabled, reason=apps_reason, node_id=node_id),
        "media_control": _toggle(
            media_enabled,
            reason=media_reason,
            target="browsers_skill.set_browser_media_control",
            params={"device_ref": media_device_ref},
        ),
    }


def get_device_settings(device_ref: str) -> dict[str, Any] | None:
    hub_ref = _hub_ref_for_device_ref(device_ref)
    if hub_ref:
        return _hub_device_settings(hub_ref)
    device, error = _device_or_error(device_ref)
    if error is not None:
        return None
    assert device is not None
    profile = get_command_profile(_text(device_ref)) or {}
    identity = _mapping(device.get("identity"))
    policy = _mapping(device.get("policy"))
    observation = _mapping(device.get("observation"))
    runtime = _mapping(device.get("runtime"))
    name_meta = _mapping(profile.get("rename"))
    lifetime_meta = _mapping(profile.get("set_lifetime"))
    detach_meta = _mapping(profile.get("detach"))
    deny_meta = _mapping(profile.get("deny"))
    reconcile = _device_reconciler.reconcile_device(_text(device_ref)) or {}
    adopt_meta = _mapping(reconcile.get("actions")).get("adopt_device")
    adopt_payload = _mapping(adopt_meta)
    device_ref_token = _text(device_ref)
    effective_name = _text(policy.get("effective_name")) or _text(device.get("ref"))
    current_names = _policy_name_list(policy, identity, effective_name)
    current_name = current_names[0] if current_names else effective_name
    kind_token = _text(device.get("kind"))
    browser_parent_ref = _browser_parent_device_ref(device_ref_token, identity) if kind_token == "browser" else device_ref_token
    action_device_ref = browser_parent_ref if kind_token == "browser" else device_ref_token
    command_params = {"device_ref": action_device_ref}
    browser_device_name = (
        _text(identity.get("device_display_name"))
        or _text(policy.get("device_display_name"))
        or (_text(policy.get("effective_name")) if _text(policy.get("access_class")) != "client" else "")
    )
    browser_device_names = _name_list(browser_device_name)
    browser_endpoint_name = (
        _text(identity.get("endpoint_display_name"))
        or _text(policy.get("endpoint_display_name"))
        or _text(policy.get("display_name"))
        or current_name
    )
    rename_enabled = bool(name_meta.get("enabled"))
    adopt_enabled = bool(adopt_payload.get("enabled"))
    save_action = (
        _toggle(
            True,
            target="browsers_skill.rename_device",
            params=command_params,
        )
        if rename_enabled
        else _toggle(
            True,
            target="browsers_skill.adopt_device",
            params=command_params,
        )
        if adopt_enabled
        else _toggle(
            False,
            reason=_text(name_meta.get("reason")) or "device_policy_missing",
            target="browsers_skill.rename_device",
            params=command_params,
        )
    )
    policy_status = _text(policy.get("managed_state")) or "observed_only"
    policy_storage = "access_links.display_name + access_links.node_names"
    policy_mode = "rename" if rename_enabled else "adopt" if adopt_enabled else "disabled"
    media_control = _mapping(runtime.get("media_control"))
    media_services = _mapping(runtime.get("services"))
    audio_input_endpoint = _mapping(media_services.get("audio_input_endpoint"))
    audio_output_endpoint = _mapping(media_services.get("audio_output_endpoint"))
    media_action = _mapping(profile.get("media_control"))
    return {
        "device_ref": device_ref_token,
        "kind": kind_token,
        "title": effective_name,
        "endpoint_title": _text(policy.get("endpoint_display_name")) or _text(policy.get("display_name")) or None,
        "id": {
            "value": device_ref_token,
            "kind": _text(device.get("kind")),
            "node_id": _text(identity.get("node_id")) or None,
            "link_id": _kind_and_link_id(device_ref_token)[1],
        },
        "device": device,
        "status": {
            "online": bool(observation.get("online")),
            "managed_state": _text(policy.get("managed_state")) or "observed_only",
            "connection_state": _text(observation.get("connection_state")) or None,
            "observation_source": _text(observation.get("source")) or None,
            "connected_to_subnet": runtime.get("connected_to_subnet"),
        },
        "name": {
            "value": ", ".join(current_names),
            "primary": current_name,
            "names": current_names,
            "label": "Endpoint name" if kind_token == "browser" else "Device name",
            "placeholder": "Living room TV, Kitchen display",
            "suggestions": [] if kind_token == "browser" else _registered_device_name_suggestions(current_names),
            "save": save_action,
            "policy": {
                "can_edit": bool(save_action.get("enabled")),
                "status": policy_status,
                "storage": policy_storage,
                "field": "display_name,node_names",
                "mode": policy_mode,
                "reason": _text(save_action.get("reason")) or None,
            },
            "helper": (
                f"Primary name is the first comma-separated value. "
                f"canEdit={str(bool(save_action.get('enabled'))).lower()} "
                f"status={policy_status} storage={policy_storage}"
            ),
        },
        "device_name": {
            "value": ", ".join(browser_device_names),
            "primary": browser_device_names[0] if browser_device_names else browser_device_name,
            "names": browser_device_names,
            "label": "Device name",
            "placeholder": "My laptop, Kitchen tablet",
            "suggestions": _registered_device_name_suggestions(browser_device_names),
            "save": _toggle(
                kind_token == "browser",
                reason=None if kind_token == "browser" else "device_name_field_browser_only",
                target="browsers_skill.rename_browser_device_name",
                params={"device_ref": browser_parent_ref} if browser_parent_ref else command_params,
            ),
            "policy": {
                "can_edit": kind_token == "browser",
                "status": policy_status,
                "storage": "access_links.device_display_name",
                "field": "device_display_name",
                "mode": "rename" if kind_token == "browser" else "disabled",
                "reason": None if kind_token == "browser" else "device_name_field_browser_only",
            },
            "helper": "Stored in the hub device registry as the physical device name.",
        } if kind_token == "browser" else None,
        "endpoint_name": {
            "value": browser_endpoint_name,
            "primary": browser_endpoint_name,
            "names": [browser_endpoint_name] if browser_endpoint_name else [],
            "label": "Endpoint name",
            "placeholder": "Chrome, Work profile",
            "save": save_action,
            "policy": {
                "can_edit": bool(save_action.get("enabled")),
                "status": policy_status,
                "storage": "access_links.display_name",
                "field": "display_name",
                "mode": policy_mode,
                "reason": _text(save_action.get("reason")) or None,
            },
            "helper": "Stored in the hub device registry as the browser endpoint name.",
        } if kind_token == "browser" else None,
        "aliases": {
            "labels": list(policy.get("labels") or []),
            "add": _toggle(
                bool(name_meta.get("enabled")),
                reason=_text(name_meta.get("reason")) or None,
                target="browsers_skill.add_device_alias",
                params=command_params,
            ),
        },
        "lifetime": {
            "current_label": _lifetime_label(policy),
            "current_mode": _text(policy.get("lifetime_mode")) or "permanent",
            "expires_at": policy.get("expires_at"),
            "set": _toggle(
                bool(lifetime_meta.get("enabled")),
                reason=_text(lifetime_meta.get("reason")) or None,
                presets=[
                    _text(item)
                    for item in list(lifetime_meta.get("presets") or _LIFETIME_PRESETS)
                    if _text(item)
                ] or list(_LIFETIME_PRESETS),
                target="browsers_skill.set_device_lifetime",
                params=command_params,
            ),
            "options": _lifetime_options(lifetime_meta),
        },
        "detach": {
            **_toggle(
                bool(detach_meta.get("enabled")),
                reason=_text(detach_meta.get("reason")) or None,
                target="browsers_skill.detach_device",
                params=command_params,
            ),
            "confirm_title": "Detach device",
            "confirm_message": f'Detach device "{effective_name}"?',
        },
        "deny": {
            **_toggle(
                bool(deny_meta.get("enabled")),
                reason=_text(deny_meta.get("reason")) or None,
                target="browsers_skill.deny_device",
                params=command_params,
            ),
            "confirm_title": "Deny device",
            "confirm_message": f'Deny future connections from "{effective_name}"?',
        },
        "actions": {
            "open_apps": _mapping(profile.get("open_apps")),
            "open_marketplace": _mapping(profile.get("open_marketplace")),
        },
        "media_control": {
            "schema_version": _text(media_control.get("schema_version")) or "browser-media-control.v1",
            "selected_audio_input": _mapping(media_control.get("selected_audio_input")),
            "selected_audio_output": _mapping(media_control.get("selected_audio_output")),
            "volume": _clamped_unit_float(media_control.get("volume")),
            "muted": _optional_bool(media_control.get("muted")),
            "capabilities": _mapping(media_control.get("capabilities")),
            "route_status": _mapping(media_control.get("route_status")),
            "services": {
                "audio_input_endpoint": audio_input_endpoint,
                "audio_output_endpoint": audio_output_endpoint,
            },
            "set": _toggle(
                bool(media_action.get("enabled")),
                reason=_text(media_action.get("reason")) or None,
                target="browsers_skill.set_browser_media_control",
                params={"device_ref": action_device_ref},
            ),
            "helper": "Browser media channel preferences are stored per user and applied by the active browser session.",
        } if kind_token == "browser" else None,
        "identify": _toggle(
            kind_token == "browser" and bool(observation.get("online")),
            reason=None if kind_token == "browser" and bool(observation.get("online")) else "browser_offline" if kind_token == "browser" else "identify_browser_only",
            target="browsers_skill.identify_device",
            params={"device_ref": browser_parent_ref} if kind_token == "browser" else command_params,
        ),
        "reconcile": reconcile,
        "adopt": {
            "enabled": bool(adopt_payload.get("enabled")),
            "suggested_display_name": _text(adopt_payload.get("suggested_display_name")) or current_name,
            "preset": _text(adopt_payload.get("preset")) or "permanent",
            "target": "browsers_skill.adopt_device",
            "params": command_params,
        },
        "identity": {
            "node_id": _text(identity.get("node_id")) or None,
            "browser_device_id": _text(identity.get("browser_device_id")) or None,
            "hostname": _text(identity.get("hostname")) or None,
            "device_display_name": _text(identity.get("device_display_name")) or _text(policy.get("device_display_name")) or None,
            "endpoint_display_name": _text(identity.get("endpoint_display_name")) or _text(policy.get("endpoint_display_name")) or _text(policy.get("display_name")) or None,
        },
    }


def rename_device(device_ref: str, display_name: str) -> dict[str, Any]:
    hub_ref = _hub_ref_for_device_ref(device_ref)
    if hub_ref:
        names = _name_list(display_name)
        if not names:
            return {"ok": False, "error": "name_required", "device_ref": _text(device_ref)}
        conf, error = _hub_config_matches(hub_ref)
        if error is not None:
            return error
        updated = _set_local_node_names(names)
        subnet_id = _text(getattr(updated, "subnet_id_value", None) or getattr(updated, "subnet_id", None))
        return {
            "ok": True,
            "device_ref": f"hub:{subnet_id or _hub_ref_id(hub_ref) or 'local'}",
            "entry": {
                "kind": "hub",
                "id": subnet_id or _hub_ref_id(hub_ref),
                "display_name": names[0],
                "node_names": names,
                "storage": ".adaos/node.yaml: node.node_names",
            },
            "device": _hub_device_settings(f"hub:{subnet_id or _hub_ref_id(hub_ref) or 'local'}"),
            "runtime_update": {"attempted": False, "applied": True},
        }
    return _device_inventory.get_device_inventory_service().rename_device(_text(device_ref), display_name)


def rename_browser_device_name(device_ref: str, device_display_name: str) -> dict[str, Any]:
    parsed = _device_inventory.parse_device_ref(_text(device_ref))
    if parsed is None:
        return {"ok": False, "error": "invalid_device_ref", "device_ref": _text(device_ref)}
    kind, link_id = parsed
    if kind != "browser":
        return {"ok": False, "error": "unsupported_device_kind", "device_ref": _text(device_ref), "kind": kind}
    name = _text(device_display_name)
    if not name:
        return {"ok": False, "error": "name_required", "device_ref": _text(device_ref)}
    if "::" in link_id:
        link_id = _text(link_id.split("::", 1)[0])
    entry = _access_links.rename_browser_device_name(link_id, name)
    return {
        "ok": True,
        "device_ref": f"browser:{link_id}",
        "entry": entry,
        "device": _device_inventory.get_device(_text(device_ref)),
        "runtime_update": {"attempted": False, "applied": True},
    }


def add_device_alias(
    device_ref: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    device, error = _device_or_error(device_ref)
    if error is not None:
        return error
    assert device is not None
    if not _policy_present(device):
        return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
    kind, link_id = _kind_and_link_id(_text(device_ref))
    result = _access_links.add_link_alias(
        kind,
        link_id,
        _text(alias),
        locale=locale,
        actor=actor,
        source="device_access",
        request_id=request_id,
        base_fingerprint=base_fingerprint,
    )
    if not bool(result.get("ok")):
        return result
    return {
        **result,
        "device": _device_inventory.get_device(_text(device_ref)),
    }


def remove_device_alias(
    device_ref: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    device, error = _device_or_error(device_ref)
    if error is not None:
        return error
    assert device is not None
    if not _policy_present(device):
        return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
    kind, link_id = _kind_and_link_id(_text(device_ref))
    result = _access_links.remove_link_alias(
        kind,
        link_id,
        _text(alias),
        locale=locale,
        actor=actor,
        source="device_access",
        request_id=request_id,
        base_fingerprint=base_fingerprint,
    )
    if not bool(result.get("ok")):
        return result
    return {
        **result,
        "device": _device_inventory.get_device(_text(device_ref)),
    }


def deprecate_device_alias(
    device_ref: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    device, error = _device_or_error(device_ref)
    if error is not None:
        return error
    assert device is not None
    if not _policy_present(device):
        return {"ok": False, "error": "device_policy_missing", "device_ref": _text(device_ref)}
    kind, link_id = _kind_and_link_id(_text(device_ref))
    result = _access_links.deprecate_link_alias(
        kind,
        link_id,
        _text(alias),
        locale=locale,
        actor=actor,
        source="device_access",
        request_id=request_id,
        base_fingerprint=base_fingerprint,
    )
    if not bool(result.get("ok")):
        return result
    return {
        **result,
        "device": _device_inventory.get_device(_text(device_ref)),
    }


def set_device_lifetime(device_ref: str, preset: str) -> dict[str, Any]:
    if _hub_ref_for_device_ref(device_ref):
        return {"ok": False, "error": "hub_lifetime_not_applicable", "device_ref": _text(device_ref)}
    return _device_inventory.get_device_inventory_service().set_device_lifetime(_text(device_ref), preset)


def detach_device(device_ref: str) -> dict[str, Any]:
    if _hub_ref_for_device_ref(device_ref):
        return {"ok": False, "error": "hub_detach_not_applicable", "device_ref": _text(device_ref)}
    return _device_inventory.get_device_inventory_service().detach_device(_text(device_ref))


def deny_device(device_ref: str) -> dict[str, Any]:
    if _hub_ref_for_device_ref(device_ref):
        return {"ok": False, "error": "hub_deny_not_applicable", "device_ref": _text(device_ref)}
    return _device_inventory.get_device_inventory_service().deny_device(_text(device_ref))


def adopt_device(device_ref: str, display_name: str | None = None, preset: str = "permanent") -> dict[str, Any]:
    return _device_reconciler.adopt_device(
        _text(device_ref),
        display_name=_text(display_name) or None,
        preset=_text(preset) or "permanent",
    )


def identify_device(
    device_ref: str,
    *,
    request_id: str | None = None,
    webspace_id: str | None = None,
    ttl_s: float = 12.0,
) -> dict[str, Any]:
    token = _text(device_ref)
    parsed = _device_inventory.parse_device_ref(token)
    if parsed is None:
        return {"ok": False, "error": "invalid_device_ref", "device_ref": token}
    kind, _link_id = parsed
    if kind != "browser":
        return {"ok": False, "error": "identify_browser_only", "device_ref": token, "kind": kind}
    device = _device_inventory.get_device(token)
    if device is None:
        return {"ok": False, "error": "device_not_found", "device_ref": token}
    identity = _mapping(device.get("identity"))
    policy = _mapping(device.get("policy"))
    observation = _mapping(device.get("observation"))
    parent_ref = _browser_parent_device_ref(token, identity)
    parent_id = _browser_parent_device_id(token, identity)
    issued_at = time.time()
    rid = _text(request_id) or f"identify-{uuid.uuid4().hex[:12]}"
    payload = {
        "schema": "adaos.browser.identify.request.v1",
        "request_id": rid,
        "device_ref": parent_ref,
        "target_device_ref": parent_ref,
        "target_browser_device_id": parent_id,
        "browser_device_id": parent_id,
        "requested_ref": token,
        "webspace_id": _text(webspace_id) or _text(observation.get("last_webspace_id")) or None,
        "title": _text(policy.get("effective_name")) or parent_id,
        "device_display_name": _text(identity.get("device_display_name")) or _text(policy.get("device_display_name")) or None,
        "endpoint_display_name": _text(identity.get("endpoint_display_name")) or _text(policy.get("endpoint_display_name")) or _text(policy.get("display_name")) or None,
        "issued_at": issued_at,
        "expires_at": issued_at + max(1.0, float(ttl_s or 12.0)),
    }
    emitted = False
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.eventbus import emit as bus_emit

        bus_emit(get_ctx().bus, "browser.identify.requested", payload, source="device_access")
        emitted = True
    except Exception:
        emitted = False
    return {
        "ok": True,
        "request_id": rid,
        "event": "browser.identify.requested",
        "emitted": emitted,
        "payload": payload,
        "device_ref": parent_ref,
    }


def set_browser_media_control(
    device_ref: str,
    *,
    audio_input_device_id: str | None = None,
    audio_input_label: str | None = None,
    audio_output_device_id: str | None = None,
    audio_output_label: str | None = None,
    volume: float | int | str | None = None,
    muted: bool | str | None = None,
    media_audio_input_device_id: str | None = None,
    media_audio_input_label: str | None = None,
    media_audio_output_device_id: str | None = None,
    media_audio_output_label: str | None = None,
    media_audio_output_volume: float | int | str | None = None,
    media_audio_output_muted: bool | str | None = None,
    request_id: str | None = None,
    webspace_id: str | None = None,
    ttl_s: float = 12.0,
) -> dict[str, Any]:
    token = _text(device_ref)
    parsed = _device_inventory.parse_device_ref(token)
    if parsed is None:
        return {"ok": False, "error": "invalid_device_ref", "device_ref": token}
    kind, _link_id = parsed
    if kind != "browser":
        return {"ok": False, "error": "media_control_browser_only", "device_ref": token, "kind": kind}
    device = _device_inventory.get_device(token)
    if device is None:
        return {"ok": False, "error": "device_not_found", "device_ref": token}
    identity = _mapping(device.get("identity"))
    observation = _mapping(device.get("observation"))
    parent_ref = _browser_parent_device_ref(token, identity)
    parent_id = _browser_parent_device_id(token, identity)
    input_device_id_present, input_device_id_raw = _first_provided(media_audio_input_device_id, audio_input_device_id)
    input_label_present, input_label_raw = _first_provided(media_audio_input_label, audio_input_label)
    output_device_id_present, output_device_id_raw = _first_provided(media_audio_output_device_id, audio_output_device_id)
    output_label_present, output_label_raw = _first_provided(media_audio_output_label, audio_output_label)
    input_device_id = _text(input_device_id_raw) if input_device_id_present else ""
    input_label = _text(input_label_raw) if input_label_present else ""
    output_device_id = _text(output_device_id_raw) if output_device_id_present else ""
    output_label = _text(output_label_raw) if output_label_present else ""
    output_volume = _clamped_unit_float(
        media_audio_output_volume if media_audio_output_volume is not None else volume
    )
    output_muted = _optional_bool(
        media_audio_output_muted if media_audio_output_muted is not None else muted
    )
    issued_at = time.time()
    rid = _text(request_id) or f"browser-media-{uuid.uuid4().hex[:12]}"
    media_preferences: dict[str, Any] = {}
    if input_device_id_present:
        media_preferences["audioInputDeviceId"] = input_device_id
    if input_label_present:
        media_preferences["audioInputLabel"] = input_label
    if output_device_id_present:
        media_preferences["audioOutputDeviceId"] = output_device_id
    if output_label_present:
        media_preferences["audioOutputLabel"] = output_label
    if output_volume is not None:
        media_preferences["audioOutputVolume"] = output_volume
    if output_muted is not None:
        media_preferences["audioOutputMuted"] = output_muted
    payload = {
        "schema": "adaos.browser.media_control.request.v1",
        "request_id": rid,
        "device_ref": parent_ref,
        "target_device_ref": parent_ref,
        "target_browser_device_id": parent_id,
        "browser_device_id": parent_id,
        "requested_ref": token,
        "webspace_id": _text(webspace_id) or _text(observation.get("last_webspace_id")) or None,
        "media_preferences": media_preferences,
        "issued_at": issued_at,
        "expires_at": issued_at + max(1.0, float(ttl_s or 12.0)),
    }
    try:
        _access_links.touch_browser_session(
            parent_id,
            webspace_id=_text(webspace_id) or None,
            media_audio_input_device_id=input_device_id if input_device_id_present else None,
            media_audio_input_label=input_label if input_label_present else None,
            media_audio_output_device_id=output_device_id if output_device_id_present else None,
            media_audio_output_label=output_label if output_label_present else None,
            media_volume=output_volume,
            media_muted=output_muted,
        )
    except Exception:
        pass
    emitted = False
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.eventbus import emit as bus_emit

        bus_emit(get_ctx().bus, "browser.media_control.requested", payload, source="device_access")
        emitted = True
    except Exception:
        emitted = False
    return {
        "ok": True,
        "request_id": rid,
        "event": "browser.media_control.requested",
        "emitted": emitted,
        "payload": payload,
        "device_ref": parent_ref,
    }


__all__ = [
    "adopt_device",
    "add_device_alias",
    "deprecate_device_alias",
    "deny_device",
    "detach_device",
    "get_device_settings",
    "get_command_profile",
    "identify_device",
    "list_registered_device_names",
    "remove_device_alias",
    "rename_browser_device_name",
    "rename_device",
    "set_browser_media_control",
    "set_device_lifetime",
]
