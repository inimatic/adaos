from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping


PROJECT_PLACEMENT_SCHEMA = "adaos.project.placement.v1"
_PLACEMENT_KINDS = {"stable", "trial"}
_PLACEMENT_STATUSES = {"active", "detached", "expired", "failed", "reconciling"}
_DATA_MODES = {"empty", "mock", "fixture", "sandbox", "read_only", "live_readonly", "real", "live"}


class BuilderPlacementError(ValueError):
    """Raised when a project result cannot be bound to an exact Webspace."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _token(value: Any, field: str, *, max_length: int = 300) -> str:
    token = str(value or "").strip()
    if not token:
        raise BuilderPlacementError(f"{field} is required")
    if len(token) > max_length:
        raise BuilderPlacementError(f"{field} exceeds {max_length} characters")
    return token


def normalize_project_placement(
    value: Mapping[str, Any],
    *,
    project_ref: str,
    now: str | None = None,
) -> dict[str, Any]:
    raw = dict(value or {})
    kind = _token(raw.get("kind"), "placement kind", max_length=20).lower()
    if kind not in _PLACEMENT_KINDS:
        raise BuilderPlacementError("placement kind must be stable or trial")
    status = str(raw.get("status") or "active").strip().lower()
    if status not in _PLACEMENT_STATUSES:
        raise BuilderPlacementError("placement status is invalid")
    target = dict(raw.get("target") or {})
    webspace_id = _token(target.get("webspace_id"), "target.webspace_id")
    space_kind = str(target.get("space_kind") or "workspace").strip().lower()
    if space_kind not in {"workspace", "development"}:
        raise BuilderPlacementError("target.space_kind must be workspace or development")
    result_ref = dict(raw.get("result_ref") or {})
    _token(result_ref.get("id"), "result_ref.id", max_length=500)
    data_mode = str(raw.get("data_mode") or ("mock" if kind == "trial" else "real")).strip().lower()
    if data_mode not in _DATA_MODES:
        raise BuilderPlacementError("placement data_mode is invalid")
    if kind == "trial" and data_mode in {"real", "live"}:
        safety = dict(raw.get("safety") or {})
        if not bool(safety.get("approved")) or not bool(safety.get("reversible")):
            raise BuilderPlacementError(
                "writable Trial placement requires explicit approval and reversible effects"
            )
    timestamp = str(now or raw.get("updated_at") or _now())
    placement_id = _token(
        raw.get("placement_id") or f"{kind}:{project_ref}:{webspace_id}",
        "placement_id",
        max_length=500,
    )
    return {
        "schema": PROJECT_PLACEMENT_SCHEMA,
        "placement_id": placement_id,
        "project_ref": project_ref,
        "kind": kind,
        "result_ref": copy.deepcopy(result_ref),
        "channel": str(raw.get("channel") or ("stable" if kind == "stable" else "trial")).strip(),
        "target": {
            "zone": str(target.get("zone") or "").strip() or None,
            "subnet_id": str(target.get("subnet_id") or "").strip() or None,
            "webspace_id": webspace_id,
            "space_kind": space_kind,
        },
        "host_capability": str(raw.get("host_capability") or "adaos.desktop.host.v1").strip(),
        "scenario_id": str(raw.get("scenario_id") or project_ref.partition(":")[2]).strip() or None,
        "audience": str(raw.get("audience") or ("trial" if kind == "trial" else "workspace")).strip(),
        "data_mode": data_mode,
        "runtime_binding": copy.deepcopy(dict(raw.get("runtime_binding") or {})),
        "trial_activation_ref": str(raw.get("trial_activation_ref") or "").strip() or None,
        "safety": copy.deepcopy(dict(raw.get("safety") or {})),
        "status": status,
        "created_at": str(raw.get("created_at") or timestamp),
        "updated_at": timestamp,
        "detached_at": str(raw.get("detached_at") or "").strip() or None,
    }


def normalize_project_placements(value: Any, *, project_ref: str) -> list[dict[str, Any]]:
    placements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value or []:
        if not isinstance(item, Mapping):
            continue
        placement = normalize_project_placement(item, project_ref=project_ref)
        if placement["placement_id"] in seen:
            placements = [
                current
                for current in placements
                if current["placement_id"] != placement["placement_id"]
            ]
        seen.add(placement["placement_id"])
        placements.append(placement)
    return placements[-100:]


def active_project_placement(
    placements: list[Mapping[str, Any]],
    *,
    kind: str,
) -> dict[str, Any] | None:
    selected = str(kind or "").strip().lower()
    for item in reversed(placements):
        if str(item.get("kind") or "") == selected and str(item.get("status") or "") == "active":
            return copy.deepcopy(dict(item))
    return None


__all__ = [
    "BuilderPlacementError",
    "PROJECT_PLACEMENT_SCHEMA",
    "active_project_placement",
    "normalize_project_placement",
    "normalize_project_placements",
]
