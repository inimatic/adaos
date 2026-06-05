from __future__ import annotations

from typing import Any, Mapping

from adaos.services import device_access as _service


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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
        candidates = {
            _text(raw.get("code")),
            _text(raw.get("pair_code")),
            _text(raw.get("endpoint_id")),
            _text(_mapping(raw.get("endpoint_manifest")).get("endpoint_id")),
            _text(compact.get("code")),
            _text(compact.get("endpoint_id")),
            _text(compact.get("id")),
        }
        if target in candidates:
            pair_code = _text(compact.get("code")) or _text(raw.get("code")) or target
            return dict(raw), pair_code
    return None, target


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


def list_endpoint_devices(kind: str | None = None, *, sync_registry: bool = True) -> list[dict[str, Any]]:
    normalized = _text(kind).lower() or "redevice"
    if normalized != "redevice":
        from adaos.sdk.data import devices as _devices

        return _devices.list_devices(kind=normalized)
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
