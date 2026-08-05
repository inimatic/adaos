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
    endpoint_assignment = _mapping(runtime.get("endpoint_assignment"))
    names.append(endpoint_assignment.get("role"))
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


def _resolution_evidence(
    device: Mapping[str, Any],
    *,
    query: str | None = None,
    score: int | None = None,
    assignment: str | None = None,
    active_app: str | None = None,
    require_online: bool = False,
) -> dict[str, Any]:
    runtime = _mapping(device.get("runtime"))
    observation = _mapping(device.get("observation"))
    endpoint_assignment = _mapping(runtime.get("endpoint_assignment"))
    return {
        "schema_version": "endpoint-resolution.v1",
        "query": _text(query) or None,
        "score": int(score or 0),
        "assignment_filter": _text(assignment) or None,
        "active_app_filter": _text(active_app) or None,
        "require_online": bool(require_online),
        "matched_names": _device_names(device),
        "assignment": _text(runtime.get("assignment") or endpoint_assignment.get("role")) or None,
        "endpoint_assignment": endpoint_assignment or None,
        "active_app": _mapping(runtime.get("active_app")) or None,
        "active_surface": _mapping(runtime.get("active_surface")) or None,
        "online": bool(observation.get("online")),
        "online_state": _text(observation.get("connection_state") or runtime.get("snapshot_state")) or None,
    }


def _normalize_redevice_ref(device_ref: str | None = None, code: str | None = None) -> str:
    token = _text(code)
    if token:
        return token
    ref = _text(device_ref)
    if ref.startswith("redevice:"):
        return ref.split(":", 1)[1].strip()
    return ref


def _resolve_redevice_endpoint(device_ref: str | None = None, code: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """Resolve a current ReDevice endpoint identity to its active pair code.

    Revoked or superseded admission/session history is intentionally ignored.
    Skills should fail with endpoint_not_found instead of silently delivering
    commands to an old pair code.
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
    try:
        for raw in _access_links.list_links("redevice"):
            if not isinstance(raw, Mapping):
                continue
            entry = dict(raw)
            policy = _mapping(entry.get("endpoint_policy"))
            profile = _mapping(policy.get("transport_profile")) or _mapping(policy.get("transport_policy"))
            manifest = _mapping(entry.get("endpoint_manifest"))
            endpoint_id = _text(entry.get("id") or entry.get("endpoint_id"))
            canonical_endpoint_id = _text(policy.get("endpoint_id")) or _text(profile.get("endpoint_id")) or endpoint_id
            candidates = {
                endpoint_id,
                f"redevice:{endpoint_id}" if endpoint_id else "",
                canonical_endpoint_id,
                f"redevice:{canonical_endpoint_id}" if canonical_endpoint_id else "",
                _text(manifest.get("endpoint_id")),
                _text(entry.get("pair_code")),
                _text(entry.get("code")),
            }
            if target in candidates:
                return entry, _text(entry.get("pair_code") or entry.get("code")) or target
    except Exception:
        pass
    return None, ""


def resolve_endpoint_device(
    device_ref: str | None = None,
    *,
    code: str | None = None,
    query: str | None = None,
    assignment: str | None = None,
    active_app: str | None = None,
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
    active_app_token = _text(active_app)
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
        if active_app_token:
            app = _mapping(runtime.get("active_app"))
            app_ids = {
                _text(app.get("app_id")).casefold(),
                _text(app.get("skill_id")).casefold(),
            }
            if active_app_token.casefold() not in app_ids:
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
        scores = {id(item): score for item, score in scored}
    else:
        scores = {id(item): _match_score(query_token, item) for item in candidates}
    if not candidates:
        return {
            "ok": False,
            "error": "endpoint_not_found",
            "device_ref": direct_ref,
            "code": direct_code,
            "query": query_token,
            "assignment": assignment_token,
            "active_app": active_app_token,
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
        "resolution": _resolution_evidence(
            device,
            query=query_token,
            score=scores.get(id(device), 0),
            assignment=assignment_token,
            active_app=active_app_token,
            require_online=require_online,
        ),
    }


def assign_endpoint(
    device_ref: str | None = None,
    assignment: str | None = None,
    *,
    code: str | None = None,
    owner_node_id: str | None = None,
    owner_skill_id: str | None = None,
    source: str | None = "sdk.data.device_access",
    reason: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_endpoint_device(device_ref, code=code)
    if not resolved.get("ok"):
        return resolved
    endpoint_id = _text(resolved.get("endpoint_id"))
    if not endpoint_id:
        return {"ok": False, "error": "endpoint_id_missing", "device_ref": resolved.get("device_ref")}
    try:
        entry = _access_links.set_redevice_assignment(
            endpoint_id,
            _text(assignment) or None,
            owner_node_id=owner_node_id,
            owner_skill_id=owner_skill_id,
            source=source,
            reason=reason,
        )
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
        "endpoint_assignment": _mapping(entry.get("endpoint_assignment")) if isinstance(entry, Mapping) else None,
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


def rename_browser_device_name(device_ref: str, device_display_name: str) -> dict:
    return _service.rename_browser_device_name(str(device_ref or ""), str(device_display_name or ""))


def list_registered_device_names(kind: str | None = None) -> list[dict[str, Any]]:
    return _service.list_registered_device_names(str(kind or "") or None)


def identify_device(
    device_ref: str,
    *,
    request_id: str | None = None,
    webspace_id: str | None = None,
    ttl_s: float = 12.0,
) -> dict[str, Any]:
    return _service.identify_device(
        str(device_ref or ""),
        request_id=request_id,
        webspace_id=webspace_id,
        ttl_s=ttl_s,
    )


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
    return _service.set_browser_media_control(
        str(device_ref or ""),
        audio_input_device_id=audio_input_device_id,
        audio_input_label=audio_input_label,
        audio_output_device_id=audio_output_device_id,
        audio_output_label=audio_output_label,
        volume=volume,
        muted=muted,
        media_audio_input_device_id=media_audio_input_device_id,
        media_audio_input_label=media_audio_input_label,
        media_audio_output_device_id=media_audio_output_device_id,
        media_audio_output_label=media_audio_output_label,
        media_audio_output_volume=media_audio_output_volume,
        media_audio_output_muted=media_audio_output_muted,
        request_id=request_id,
        webspace_id=webspace_id,
        ttl_s=ttl_s,
    )


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
    inventory_devices: list[dict[str, Any]] = []
    try:
        from adaos.sdk.data import devices as _devices

        inventory_devices = [dict(item) for item in _devices.list_devices(kind="redevice") if isinstance(item, Mapping)]
    except Exception:
        pass
    if root_devices or inventory_devices:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(item: Mapping[str, Any]) -> None:
            identity = _mapping(item.get("identity"))
            policy = _mapping(item.get("policy"))
            key = (
                _text(identity.get("endpoint_id"))
                or _text(item.get("endpoint_id"))
                or _text(identity.get("pair_code"))
                or _text(policy.get("pair_code"))
                or _text(item.get("code"))
                or _text(item.get("id"))
            )
            if key and key in seen:
                return
            if key:
                seen.add(key)
            merged.append(dict(item))

        for item in root_devices:
            add(item)
        for item in inventory_devices:
            add(item)
        return merged
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
    requested_by: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    timeout: int | float | None = None,
) -> dict[str, Any]:
    target = _text(device_ref)
    if target and not target.startswith("redevice:"):
        return {"ok": False, "error": "unsupported_endpoint_kind", "device_ref": target}
    endpoint, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.services import endpoint_router

        return endpoint_router.send_redevice_command(
            endpoint=endpoint or {},
            code=pair_code,
            command=dict(command or {}),
            device_ref=target or None,
            requested_by=requested_by,
            constraints=constraints,
            timeout=12 if timeout is None else timeout,
        )
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
    endpoint, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.sdk.redevice import ReDeviceBridge, endpoint_root_base

        return ReDeviceBridge(root_base=endpoint_root_base(endpoint or {}), timeout=12).update_profile(pair_code, display_name=display_name, aliases=aliases)
    except Exception as exc:
        return {"ok": False, "error": "endpoint_profile_update_failed", "detail": str(exc), "device_ref": target, "code": pair_code}


def revoke_endpoint(device_ref: str | None = None, *, code: str | None = None) -> dict[str, Any]:
    target = _text(device_ref)
    if target and not target.startswith("redevice:"):
        return {"ok": False, "error": "unsupported_endpoint_kind", "device_ref": target}
    endpoint, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.sdk.redevice import ReDeviceBridge, endpoint_root_base

        return ReDeviceBridge(root_base=endpoint_root_base(endpoint or {}), timeout=12).revoke(pair_code)
    except Exception as exc:
        return {"ok": False, "error": "endpoint_revoke_failed", "detail": str(exc), "device_ref": target, "code": pair_code}


def retire_endpoint(device_ref: str | None = None, *, code: str | None = None) -> dict[str, Any]:
    target = _text(device_ref)
    if target and not target.startswith("redevice:"):
        return {"ok": False, "error": "unsupported_endpoint_kind", "device_ref": target}
    endpoint, pair_code = _resolve_redevice_endpoint(device_ref, code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "device_ref": target}
    try:
        from adaos.sdk.redevice import ReDeviceBridge, endpoint_root_base

        return ReDeviceBridge(root_base=endpoint_root_base(endpoint or {}), timeout=12).retire(pair_code)
    except Exception as exc:
        return {"ok": False, "error": "endpoint_retire_failed", "detail": str(exc), "device_ref": target, "code": pair_code}
