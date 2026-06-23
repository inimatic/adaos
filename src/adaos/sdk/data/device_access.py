from __future__ import annotations

from typing import Any, Mapping

from adaos.services import access_links as _access_links
from adaos.services import device_access as _service


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _fold(value: Any) -> str:
    return " ".join(_text(value).split()).casefold()


def _device_names(device: Mapping[str, Any]) -> list[str]:
    policy = _mapping(device.get("policy"))
    identity = _mapping(device.get("identity"))
    runtime = _mapping(device.get("runtime"))
    names = [
        device.get("ref"),
        policy.get("effective_name"),
        policy.get("display_name"),
        identity.get("endpoint_id"),
        identity.get("pair_code"),
        runtime.get("assignment"),
    ]
    names.extend(_list(policy.get("aliases")))
    for label in _list(policy.get("labels")):
        if isinstance(label, Mapping):
            names.append(label.get("text") or label.get("label") or label.get("value"))
        else:
            names.append(label)
    out: list[str] = []
    seen: set[str] = set()
    for item in names:
        token = _text(item)
        folded = _fold(token)
        if not token or folded in seen:
            continue
        seen.add(folded)
        out.append(token)
    return out


def _match_score(query: str, device: Mapping[str, Any]) -> int:
    folded_query = _fold(query)
    if not folded_query:
        return 0
    best = 0
    for name in _device_names(device):
        folded_name = _fold(name)
        if not folded_name:
            continue
        if folded_query == folded_name:
            best = max(best, 100)
        elif folded_query in folded_name or folded_name in folded_query:
            best = max(best, 80 + min(len(folded_query), len(folded_name)))
    return best


def _normalize_redevice_ref(device_ref: str | None = None, code: str | None = None) -> str:
    token = _text(code)
    if token:
        return token
    ref = _text(device_ref)
    if ref.startswith("redevice:"):
        return ref.split(":", 1)[1].strip()
    return ref


def _resolve_redevice_endpoint(device_ref: str | None = None, code: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """Resolve a ReDevice device ref to a pair code for legacy command delivery.

    This is a transition helper. The target architecture routes all endpoint
    commands through EndpointRouter. The current ReDevice root API still uses
    the short pair code as its command target, so SDK consumers should call this
    helper surface instead of importing the ReDevice bridge directly.
    """

    target = _normalize_redevice_ref(device_ref, code)
    if not target:
        return None, ""
    try:
        from adaos.sdk.redevice import compact_endpoint, list_endpoints
    except Exception:
        return None, target
    for raw in list_endpoints(sync_registry=True):
        if not isinstance(raw, Mapping):
            continue
        compact = compact_endpoint(raw)
        history_codes = {_text(item.get("code")) for item in list(raw.get("admission_history") or []) if isinstance(item, Mapping)}
        candidates = {
            _text(raw.get("code")),
            _text(raw.get("pair_code")),
            _text(raw.get("endpoint_id")),
            _text(_mapping(raw.get("endpoint_manifest")).get("endpoint_id")),
            _text(compact.get("code")),
            _text(compact.get("endpoint_id")),
            _text(compact.get("id")),
        }
        candidates.update(history_codes)
        if target in candidates:
            pair_code = _text(compact.get("code")) or _text(raw.get("code")) or target
            return dict(raw), pair_code
    return None, target


def resolve_endpoint_device(
    device_ref: str | None = None,
    *,
    code: str | None = None,
    query: str | None = None,
    assignment: str | None = None,
    kind: str = "redevice",
    require_online: bool = False,
) -> dict[str, Any]:
    normalized_kind = _text(kind).lower() or "redevice"
    if normalized_kind != "redevice":
        return {"ok": False, "error": "unsupported_endpoint_kind", "kind": normalized_kind}
    direct_ref = _text(device_ref)
    direct_code = _text(code)
    query_token = _text(query)
    assignment_token = _text(assignment)
    devices = list_endpoint_devices("redevice")
    candidates: list[dict[str, Any]] = []
    for item in devices:
        device = dict(item)
        identity = _mapping(device.get("identity"))
        runtime = _mapping(device.get("runtime"))
        policy = _mapping(device.get("policy"))
        ref = _text(device.get("ref"))
        endpoint_id = _text(identity.get("endpoint_id") or identity.get("node_id") or identity.get("link_id"))
        pair_code = _text(identity.get("pair_code") or policy.get("pair_code") or device.get("code"))
        if direct_ref and direct_ref not in {ref, endpoint_id, pair_code, f"redevice:{endpoint_id}"}:
            continue
        if direct_code and direct_code not in {pair_code, endpoint_id}:
            continue
        if assignment_token and _text(runtime.get("assignment")).casefold() != assignment_token.casefold():
            continue
        if require_online and not bool(_mapping(device.get("observation")).get("online")):
            continue
        candidates.append(device)
    if query_token and not direct_ref and not direct_code:
        score_source = candidates if assignment_token or require_online else (candidates or devices)
        scored = [(item, _match_score(query_token, item)) for item in score_source]
        scored = [(item, score) for item, score in scored if score > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        candidates = [item for item, _score in scored]
    if not candidates:
        return {
            "ok": False,
            "error": "endpoint_not_found",
            "device_ref": direct_ref,
            "code": direct_code,
            "query": query_token,
            "assignment": assignment_token,
        }
    device = candidates[0]
    identity = _mapping(device.get("identity"))
    endpoint_id = _text(identity.get("endpoint_id") or identity.get("node_id") or identity.get("link_id"))
    pair_code = _text(identity.get("pair_code") or device.get("code")) or endpoint_id
    return {
        "ok": True,
        "device": device,
        "device_ref": _text(device.get("ref")) or f"redevice:{endpoint_id}",
        "endpoint_id": endpoint_id,
        "code": pair_code,
        "matched_names": _device_names(device),
    }


def assign_endpoint(
    device_ref: str | None = None,
    assignment: str | None = None,
    *,
    code: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_endpoint_device(device_ref, code=code)
    if not resolved.get("ok"):
        return resolved
    endpoint_id = _text(resolved.get("endpoint_id"))
    if not endpoint_id:
        return {"ok": False, "error": "endpoint_id_missing", "device_ref": resolved.get("device_ref")}
    try:
        entry = _access_links.set_redevice_assignment(endpoint_id, _text(assignment) or None)
    except Exception as exc:
        return {"ok": False, "error": "endpoint_assignment_failed", "detail": str(exc), "endpoint_id": endpoint_id}
    if entry is None:
        return {"ok": False, "error": "endpoint_not_found", "endpoint_id": endpoint_id}
    return {
        "ok": True,
        "device_ref": f"redevice:{endpoint_id}",
        "endpoint_id": endpoint_id,
        "code": resolved.get("code"),
        "assignment": _text(assignment) or None,
        "entry": entry,
    }


def get_command_profile(device_ref: str) -> dict | None:
    return _service.get_command_profile(str(device_ref or ""))


def get_device_settings(device_ref: str) -> dict | None:
    return _service.get_device_settings(str(device_ref or ""))


def adopt_device(device_ref: str, display_name: str | None = None, preset: str = "permanent") -> dict:
    return _service.adopt_device(
        str(device_ref or ""),
        str(display_name or "") or None,
        str(preset or "permanent"),
    )


def rename_device(device_ref: str, display_name: str) -> dict:
    return _service.rename_device(str(device_ref or ""), str(display_name or ""))


def add_device_alias(
    device_ref: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict:
    return _service.add_device_alias(
        str(device_ref or ""),
        str(alias or ""),
        locale=locale,
        actor=actor,
        request_id=request_id,
        base_fingerprint=base_fingerprint,
    )


def remove_device_alias(
    device_ref: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict:
    return _service.remove_device_alias(
        str(device_ref or ""),
        str(alias or ""),
        locale=locale,
        actor=actor,
        request_id=request_id,
        base_fingerprint=base_fingerprint,
    )


def deprecate_device_alias(
    device_ref: str,
    alias: str,
    *,
    locale: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    base_fingerprint: str | None = None,
) -> dict:
    return _service.deprecate_device_alias(
        str(device_ref or ""),
        str(alias or ""),
        locale=locale,
        actor=actor,
        request_id=request_id,
        base_fingerprint=base_fingerprint,
    )


def set_device_lifetime(device_ref: str, preset: str) -> dict:
    return _service.set_device_lifetime(str(device_ref or ""), str(preset or ""))


def detach_device(device_ref: str) -> dict:
    return _service.detach_device(str(device_ref or ""))


def deny_device(device_ref: str) -> dict:
    return _service.deny_device(str(device_ref or ""))


def list_endpoint_devices(kind: str | None = None, *, sync_registry: bool = True) -> list[dict[str, Any]]:
    normalized = _text(kind).lower() or "redevice"
    if normalized != "redevice":
        from adaos.sdk.data import devices as _devices

        return _devices.list_devices(kind=normalized)
    root_devices: list[dict[str, Any]] = []
    if sync_registry:
        try:
            from adaos.sdk.redevice import compact_endpoint, list_endpoints

            root_devices = [compact_endpoint(item) for item in list_endpoints(sync_registry=True)]
        except Exception:
            root_devices = []
    if root_devices:
        return root_devices
    try:
        from adaos.sdk.data import devices as _devices

        inventory_devices = _devices.list_devices(kind="redevice")
        if inventory_devices:
            return inventory_devices
    except Exception:
        pass
    try:
        from adaos.sdk.redevice import compact_endpoint, list_endpoints

        return [compact_endpoint(item) for item in list_endpoints(sync_registry=sync_registry)]
    except Exception:
        from adaos.sdk.data import devices as _devices

        return _devices.list_devices(kind="redevice")


def send_endpoint_command(
    device_ref: str | None = None,
    command: Mapping[str, Any] | None = None,
    *,
    code: str | None = None,
) -> dict[str, Any]:
    target = _text(device_ref)
    if target and not target.startswith("redevice:"):
        return {"ok": False, "error": "unsupported_endpoint_kind", "device_ref": target}
    endpoint, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.sdk.redevice import ReDeviceBridge, select_transport

        payload = dict(command or {})
        result = ReDeviceBridge(timeout=12).send_command(pair_code, payload)
        return {
            **result,
            "device_ref": target or f"redevice:{_text(_mapping(endpoint).get('endpoint_id')) or pair_code}",
            "code": pair_code,
            "endpoint": endpoint or None,
            "transport": select_transport(endpoint or {}, intent=_text(payload.get("type")) or "endpoint.command"),
        }
    except Exception as exc:
        return {"ok": False, "error": "endpoint_command_failed", "detail": str(exc), "device_ref": target, "code": pair_code}


def update_endpoint_profile(
    device_ref: str | None = None,
    *,
    code: str | None = None,
    display_name: str | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    target = _text(device_ref)
    if target and not target.startswith("redevice:"):
        return {"ok": False, "error": "unsupported_endpoint_kind", "device_ref": target}
    _, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.sdk.redevice import ReDeviceBridge

        return ReDeviceBridge(timeout=12).update_profile(pair_code, display_name=display_name, aliases=aliases)
    except Exception as exc:
        return {"ok": False, "error": "endpoint_profile_update_failed", "detail": str(exc), "device_ref": target, "code": pair_code}


def revoke_endpoint(device_ref: str | None = None, *, code: str | None = None) -> dict[str, Any]:
    target = _text(device_ref)
    if target and not target.startswith("redevice:"):
        return {"ok": False, "error": "unsupported_endpoint_kind", "device_ref": target}
    _, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.sdk.redevice import ReDeviceBridge

        return ReDeviceBridge(timeout=12).revoke(pair_code)
    except Exception as exc:
        return {"ok": False, "error": "endpoint_revoke_failed", "detail": str(exc), "device_ref": target, "code": pair_code}


def retire_endpoint(device_ref: str | None = None, *, code: str | None = None) -> dict[str, Any]:
    target = _text(device_ref)
    if target and not target.startswith("redevice:"):
        return {"ok": False, "error": "unsupported_endpoint_kind", "device_ref": target}
    _, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.sdk.redevice import ReDeviceBridge

        return ReDeviceBridge(timeout=12).retire(pair_code)
    except Exception as exc:
        return {"ok": False, "error": "endpoint_retire_failed", "detail": str(exc), "device_ref": target, "code": pair_code}
