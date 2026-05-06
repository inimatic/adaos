from __future__ import annotations

from typing import Any, Mapping

from adaos.services import access_links as _access_links
from adaos.services import device_inventory as _device_inventory


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(values: list[Any]) -> str:
    for value in values:
        token = _text(value)
        if token:
            return token
    return ""


def _device_or_none(device_ref: str) -> dict[str, Any] | None:
    token = _text(device_ref)
    if not token:
        return None
    return _device_inventory.get_device(token)


def _runtime_member_name(device: Mapping[str, Any]) -> str:
    identity = _mapping(device.get("identity"))
    names = identity.get("node_names")
    if isinstance(names, list):
        for item in names:
            token = _text(item)
            if token:
                return token
    return ""


def _identity_tokens(device: Mapping[str, Any]) -> set[str]:
    identity = _mapping(device.get("identity"))
    tokens = {
        _text(identity.get("link_id")),
        _text(identity.get("node_id")),
        _text(identity.get("browser_device_id")),
    }
    return {token for token in tokens if token}


def _default_adopt_display_name(device: Mapping[str, Any]) -> str:
    policy = _mapping(device.get("policy"))
    hostname = _text(_mapping(device.get("identity")).get("hostname"))
    effective_name = _text(policy.get("effective_name"))
    if not effective_name:
        return ""
    if effective_name in _identity_tokens(device):
        return ""
    if hostname and effective_name == hostname:
        return hostname
    return effective_name


def reconcile_device(device_ref: str) -> dict[str, Any] | None:
    device = _device_or_none(device_ref)
    if device is None:
        return None
    policy = _mapping(device.get("policy"))
    observation = _mapping(device.get("observation"))
    runtime = _mapping(device.get("runtime"))
    kind = _text(device.get("kind"))
    effective_name = _text(policy.get("effective_name")) or _text(device.get("ref"))
    issues: list[dict[str, Any]] = []

    managed_state = _text(policy.get("managed_state")) or "observed_only"
    if managed_state == "observed_only":
        issues.append(
            {
                "id": "device_policy_missing",
                "severity": "warning",
                "summary": "Device is observed by runtime state but not yet managed by access policy.",
                "action": "adopt_device",
            }
        )

    if managed_state == "revoked" and bool(observation.get("online")):
        issues.append(
            {
                "id": "revoked_device_still_online",
                "severity": "critical",
                "summary": "Revoked device still appears online in runtime state.",
                "action": "detach_runtime",
            }
        )

    if managed_state == "expired" and bool(observation.get("online")):
        issues.append(
            {
                "id": "expired_device_still_online",
                "severity": "warning",
                "summary": "Expired device still appears online in runtime state.",
                "action": "detach_runtime",
            }
        )

    if kind == "member":
        display_name = _text(policy.get("display_name"))
        runtime_name = _runtime_member_name(device)
        if display_name and runtime_name and runtime_name != display_name and bool(observation.get("online")):
            issues.append(
                {
                    "id": "display_name_runtime_drift",
                    "severity": "info",
                    "summary": "Runtime member name differs from managed device display name.",
                    "action": "sync_runtime_name",
                    "expected_name": display_name,
                    "observed_name": runtime_name,
                }
            )
        if managed_state == "revoked" and not bool(observation.get("online")):
            issues.append(
                {
                    "id": "offline_detach_pending",
                    "severity": "info",
                    "summary": "Detached member is currently offline and will be denied on its next reconnect.",
                    "action": None,
                }
            )

    if any(issue.get("severity") == "critical" for issue in issues):
        state = "critical"
    elif any(issue.get("severity") == "warning" for issue in issues):
        state = "attention"
    else:
        state = "steady"

    return {
        "device_ref": _text(device_ref),
        "kind": kind,
        "title": effective_name,
        "state": state,
        "consistent": not any(issue.get("severity") in {"critical", "warning"} for issue in issues),
        "issue_total": len(issues),
        "issues": issues,
        "actions": {
            "adopt_device": {
                "enabled": managed_state == "observed_only",
                "suggested_display_name": _default_adopt_display_name(device),
                "preset": "permanent",
            },
            "sync_runtime_name": {
                "enabled": any(issue.get("action") == "sync_runtime_name" for issue in issues),
            },
            "detach_runtime": {
                "enabled": any(issue.get("action") == "detach_runtime" for issue in issues),
            },
        },
        "runtime": {
            "connected_to_subnet": runtime.get("connected_to_subnet"),
            "observation_source": _text(observation.get("source")) or None,
        },
    }


def adopt_device(device_ref: str, *, display_name: str | None = None, preset: str = "permanent") -> dict[str, Any]:
    token = _text(device_ref)
    parsed = _device_inventory.parse_device_ref(token)
    if parsed is None:
        return {"ok": False, "error": "invalid_device_ref", "device_ref": token}
    device = _device_or_none(token)
    if device is None:
        return {"ok": False, "error": "device_not_found", "device_ref": token}
    policy = _mapping(device.get("policy"))
    if bool(policy.get("present")):
        return {"ok": False, "error": "device_already_managed", "device_ref": token}

    kind, link_id = parsed
    identity = _mapping(device.get("identity"))
    observation = _mapping(device.get("observation"))
    chosen_name = _text(display_name)
    if not chosen_name:
        chosen_name = _default_adopt_display_name(device)

    entry_patch: dict[str, Any] = {
        "display_name": chosen_name,
        "online": bool(observation.get("online")),
        "connection_state": _text(observation.get("connection_state")) or None,
        "last_seen_at": observation.get("last_seen_at"),
        "revoked": False,
        "revoked_at": None,
    }
    if kind == "browser":
        entry_patch["hostname"] = _text(identity.get("hostname")) or None
        entry_patch["last_webspace_id"] = _text(observation.get("last_webspace_id")) or None
    else:
        entry_patch["hostname"] = _text(identity.get("hostname")) or None
        entry_patch["node_names"] = list(identity.get("node_names") or [])

    _access_links.upsert_link(kind, link_id, entry_patch)
    entry = _access_links.set_link_lifetime(kind, link_id, _text(preset) or "permanent")
    return {
        "ok": True,
        "device_ref": token,
        "entry": entry,
        "device": _device_inventory.get_device(token),
        "reconcile": reconcile_device(token),
    }


__all__ = [
    "adopt_device",
    "reconcile_device",
]
