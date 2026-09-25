from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping


_LOCK = threading.Lock()
_RUNTIME_DECLARATIONS: dict[tuple[str, str], dict[str, Any]] = {}


def _runtime_scope() -> str:
    from adaos.services.agent_context import get_ctx

    try:
        ctx = get_ctx()
        if getattr(ctx, "authority_state_dir", None) is not None:
            return str(Path(ctx.paths.state_dir()).resolve())
    except RuntimeError:
        pass
    return ""


def _append_receiver(patterns: list[str], value: Any) -> None:
    token = str(value or "").strip()
    if token and token not in patterns:
        patterns.append(token)


def _receiver_from_yjs_path(value: Any) -> str:
    path = str(value or "").strip().strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2 or parts[0] != "data":
        return ""
    logical_parts = parts[1:]
    if len(logical_parts) >= 3 and logical_parts[0] == "nodes":
        logical_parts = logical_parts[2:]
    return ".".join(logical_parts)


def receiver_patterns_from_webui_payload(payload: Any) -> list[str]:
    """Collect transport receivers and YDoc demand slots from a WebUI tree."""

    patterns: list[str] = []
    if isinstance(payload, Mapping):
        webio = payload.get("webio")
        receivers = webio.get("receivers") if isinstance(webio, Mapping) else None
        if isinstance(receivers, Mapping):
            for receiver in receivers:
                _append_receiver(patterns, receiver)

        kind = str(payload.get("kind") or "").strip().lower()
        if kind == "y":
            _append_receiver(patterns, _receiver_from_yjs_path(payload.get("path")))
        for value in payload.values():
            for pattern in receiver_patterns_from_webui_payload(value):
                _append_receiver(patterns, pattern)
    elif isinstance(payload, list):
        for value in payload:
            for pattern in receiver_patterns_from_webui_payload(value):
                _append_receiver(patterns, pattern)
    return patterns


def _manifest_receiver_patterns(manifest: Mapping[str, Any]) -> list[str]:
    patterns: list[str] = []
    routes = manifest.get("data_routes")
    if not isinstance(routes, list):
        return patterns
    for route in routes:
        if not isinstance(route, Mapping):
            continue
        kind = str(route.get("route") or "").strip().lower()
        if kind == "stream":
            _append_receiver(patterns, route.get("receiver"))
        elif kind == "yjs":
            _append_receiver(patterns, route.get("projection_slot") or route.get("slot"))
    return patterns


def _webui_receiver_patterns(artifact_root: Path) -> list[str]:
    path = artifact_root / "webui.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return receiver_patterns_from_webui_payload(payload)


def load_runtime_skill_declarations(
    skill_name: str,
    manifest: Mapping[str, Any],
    *,
    artifact_root: Path,
) -> dict[str, Any]:
    """Cache the active artifact's routing metadata before skill code runs."""

    name = str(skill_name or "").strip()
    if not name:
        raise ValueError("skill name is required for runtime declarations")
    root = Path(artifact_root).resolve()
    patterns = _manifest_receiver_patterns(manifest)
    for pattern in _webui_receiver_patterns(root):
        _append_receiver(patterns, pattern)
    projections = manifest.get("data_projections")
    routes = manifest.get("data_routes")
    runtime = manifest.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    activation = runtime.get("activation")
    activation = activation if isinstance(activation, Mapping) else {}
    activation_mode = str(activation.get("mode") or "eager").strip().lower()
    if activation_mode not in {"eager", "lazy", "on_demand"}:
        activation_mode = "on_demand"
    record = {
        "skill": name,
        "artifact_root": str(root),
        "projection_total": len(projections) if isinstance(projections, list) else 0,
        "route_total": len(routes) if isinstance(routes, list) else 0,
        "receiver_patterns": tuple(patterns),
        "runtime_kind": str(runtime.get("kind") or "module").strip().lower(),
        "activation_mode": activation_mode,
        "startup_allowed": activation.get("startup_allowed") is True,
        "loaded_at": time.time(),
    }
    with _LOCK:
        _RUNTIME_DECLARATIONS[(_runtime_scope(), name)] = record
    return dict(record)


def runtime_stream_receiver_patterns(skill_name: str) -> tuple[str, ...] | None:
    """Return ``None`` when activation has not loaded this skill yet."""

    name = str(skill_name or "").strip()
    with _LOCK:
        record = _RUNTIME_DECLARATIONS.get((_runtime_scope(), name))
        if record is None:
            return None
        return tuple(record.get("receiver_patterns") or ())


def runtime_skill_declarations_snapshot(skill_name: str | None = None) -> dict[str, Any]:
    token = str(skill_name or "").strip()
    with _LOCK:
        if token:
            record = _RUNTIME_DECLARATIONS.get((_runtime_scope(), token))
            return dict(record) if record is not None else {}
        scope = _runtime_scope()
        return {name: dict(record) for (root, name), record in _RUNTIME_DECLARATIONS.items() if root == scope}


def runtime_service_activation_summary() -> dict[str, Any]:
    """Return the already-loaded service activation inventory without I/O."""

    snapshot = runtime_skill_declarations_snapshot()
    services = [
        record
        for record in snapshot.values()
        if str(record.get("runtime_kind") or "").strip().lower() == "service"
    ]
    eager = [
        str(record.get("skill") or "").strip()
        for record in services
        if bool(record.get("startup_allowed"))
        and str(record.get("activation_mode") or "eager").strip().lower() == "eager"
    ]
    return {
        "known_service_total": len(services),
        "eager_startup_skills": tuple(sorted(name for name in eager if name)),
    }


def clear_runtime_skill_declarations(skill_name: str | None = None) -> None:
    token = str(skill_name or "").strip()
    with _LOCK:
        if token:
            _RUNTIME_DECLARATIONS.pop((_runtime_scope(), token), None)
        else:
            scope = _runtime_scope()
            for key in list(_RUNTIME_DECLARATIONS):
                if key[0] == scope:
                    _RUNTIME_DECLARATIONS.pop(key)


__all__ = [
    "clear_runtime_skill_declarations",
    "load_runtime_skill_declarations",
    "receiver_patterns_from_webui_payload",
    "runtime_service_activation_summary",
    "runtime_skill_declarations_snapshot",
    "runtime_stream_receiver_patterns",
]
