"""Canonical, capability-neutral navigation destinations for AdaOS surfaces.

Skills describe *where* the user should arrive.  The client resolves that
destination against authentication, zone, subnet, Webspace and synchronized
scenario state.  Building a link never grants access or mutates runtime state.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from adaos.services.zone_hosts import DEFAULT_PUBLIC_APP_BASE_URL

DESTINATION_SCHEMA = "adaos.navigation.destination.v1"
RESOLUTION_SCHEMA = "adaos.navigation.resolution.v1"

CONNECT_REGISTER = "connect.register"
NODE_CONNECT = "node.connect"
AUTH_LOGIN = "auth.login"
WEBSPACE_OPEN = "webspace.open"
DRIVE_DOWNLOAD = "drive.download"
DRIVE_VIEW = "drive.view"

INTENTS = frozenset({CONNECT_REGISTER, NODE_CONNECT, AUTH_LOGIN, WEBSPACE_OPEN, DRIVE_DOWNLOAD, DRIVE_VIEW})
SPACE_KINDS = frozenset({"workspace", "development", "preview", "trial"})
PREVIEW_STAGES = frozenset({"prototype", "automation", "trial", "publication"})

_QUERY_ORDER = (
    "intent",
    "zone",
    "public_token",
    "subnet_id",
    "root_url",
    "join_code",
    "webspace_id",
    "space_kind",
    "expected_scenario_id",
    "expected_revision",
    "preview_stage",
    "user_code",
    "pair_code",
    "auto_login",
    "try_local_hub",
)
_BOOL_FIELDS = frozenset({"auto_login", "try_local_hub"})
_ZONE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    token = _text(value).lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return None


def _validate_absolute_http_url(value: str, *, field: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute HTTP(S) URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _clean_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"schema": DESTINATION_SCHEMA}
    for key in _QUERY_ORDER:
        raw = payload.get(key)
        if key in _BOOL_FIELDS:
            value = _bool(raw)
            if value is not None:
                out[key] = value
            continue
        value = _text(raw)
        if value:
            out[key] = value
    return out


def validate_destination(destination: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize and validate one destination, rejecting ambiguous input."""

    if not isinstance(destination, Mapping):
        raise TypeError("navigation destination must be a mapping")
    unknown = set(destination) - {"schema", *_QUERY_ORDER}
    if unknown:
        raise ValueError(f"unsupported navigation destination fields: {', '.join(sorted(unknown))}")
    normalized = _clean_fields(destination)
    intent = _text(normalized.get("intent"))
    if intent not in INTENTS:
        raise ValueError(f"unsupported navigation intent: {intent or '<empty>'}")
    normalized["intent"] = intent
    zone = _text(normalized.get("zone")).lower()
    if zone:
        if not _ZONE_RE.fullmatch(zone):
            raise ValueError(f"invalid navigation zone: {zone}")
        normalized["zone"] = zone
    kind = _text(normalized.get("space_kind")).lower()
    if kind:
        if kind not in SPACE_KINDS:
            raise ValueError(f"unsupported navigation space_kind: {kind}")
        normalized["space_kind"] = kind
    stage = _text(normalized.get("preview_stage")).lower()
    if stage:
        if stage not in PREVIEW_STAGES:
            raise ValueError(f"unsupported navigation preview_stage: {stage}")
        normalized["preview_stage"] = stage
    if intent == CONNECT_REGISTER and not _text(normalized.get("user_code")):
        raise ValueError("connect.register requires user_code")
    if intent == NODE_CONNECT:
        missing = [key for key in ("root_url", "join_code") if not _text(normalized.get(key))]
        if missing:
            raise ValueError(f"node.connect requires: {', '.join(missing)}")
        normalized["root_url"] = _validate_absolute_http_url(
            _text(normalized.get("root_url")),
            field="node.connect root_url",
        )
    if intent in {DRIVE_DOWNLOAD, DRIVE_VIEW}:
        missing = [key for key in ("zone", "public_token") if not _text(normalized.get(key))]
        if missing:
            raise ValueError(f"{intent} requires: {', '.join(missing)}")
    if intent == WEBSPACE_OPEN:
        missing = [
            key
            for key in ("zone", "subnet_id", "webspace_id", "space_kind")
            if not _text(normalized.get(key))
        ]
        if missing:
            raise ValueError(f"webspace.open requires: {', '.join(missing)}")
    return normalized


def registration_destination(
    user_code: str,
    *,
    zone: str | None = None,
    subnet_id: str | None = None,
) -> dict[str, Any]:
    return validate_destination(
        {
            "intent": CONNECT_REGISTER,
            "user_code": user_code,
            "zone": zone,
            "subnet_id": subnet_id,
        }
    )


def node_connect_destination(
    join_code: str,
    *,
    root_url: str,
    zone: str | None = None,
    subnet_id: str | None = None,
    try_local_hub: bool = True,
) -> dict[str, Any]:
    return validate_destination(
        {
            "intent": NODE_CONNECT,
            "zone": zone,
            "subnet_id": subnet_id,
            "root_url": root_url,
            "join_code": join_code,
            "try_local_hub": try_local_hub,
        }
    )


def login_destination(
    *,
    zone: str | None = None,
    subnet_id: str | None = None,
    pair_code: str | None = None,
    auto_login: bool = True,
    try_local_hub: bool | None = None,
) -> dict[str, Any]:
    return validate_destination(
        {
            "intent": AUTH_LOGIN,
            "zone": zone,
            "subnet_id": subnet_id,
            "pair_code": pair_code,
            "auto_login": auto_login,
            "try_local_hub": try_local_hub,
        }
    )


def webspace_destination(
    *,
    zone: str,
    subnet_id: str,
    webspace_id: str,
    space_kind: str,
    expected_scenario_id: str | None = None,
    expected_revision: str | None = None,
    preview_stage: str | None = None,
) -> dict[str, Any]:
    return validate_destination(
        {
            "intent": WEBSPACE_OPEN,
            "zone": zone,
            "subnet_id": subnet_id,
            "webspace_id": webspace_id,
            "space_kind": space_kind,
            "expected_scenario_id": expected_scenario_id,
            "expected_revision": expected_revision,
            "preview_stage": preview_stage,
        }
    )


def drive_download_destination(
    public_token: str,
    *,
    zone: str,
) -> dict[str, Any]:
    return validate_destination(
        {
            "intent": DRIVE_DOWNLOAD,
            "zone": zone,
            "public_token": public_token,
        }
    )


def drive_view_destination(
    public_token: str,
    *,
    zone: str,
) -> dict[str, Any]:
    return validate_destination(
        {
            "intent": DRIVE_VIEW,
            "zone": zone,
            "public_token": public_token,
        }
    )


def build_url(destination: Mapping[str, Any], *, base_url: str = DEFAULT_PUBLIC_APP_BASE_URL) -> str:
    normalized = validate_destination(destination)
    base = _text(base_url).rstrip("/") or DEFAULT_PUBLIC_APP_BASE_URL
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("navigation base_url must be an absolute HTTP(S) URL")
    query: list[tuple[str, str]] = []
    for key in _QUERY_ORDER:
        if key not in normalized:
            continue
        value = normalized[key]
        query.append((key, "1" if value is True else "0" if value is False else str(value)))
    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


def parse_url(url: str) -> dict[str, Any]:
    parts = urlsplit(_text(url))
    query = parse_qs(parts.query, keep_blank_values=True)
    if "mode" in query:
        raise ValueError("navigation mode is unsupported; use intent")
    payload: dict[str, Any] = {}
    for key in _QUERY_ORDER:
        values = query.get(key)
        if not values:
            continue
        if len(values) != 1:
            raise ValueError(f"navigation field is repeated: {key}")
        payload[key] = values[0]
    return validate_destination(payload)


def runtime_scope() -> dict[str, str]:
    """Return the local trusted zone/subnet identifiers for SDK consumers."""

    zone = _text(os.getenv("ADAOS_ZONE_ID") or os.getenv("ZONE_ID")).lower()
    subnet_id = _text(os.getenv("ADAOS_SUBNET_ID") or os.getenv("ADAOS_HUB_ID"))
    try:
        from adaos.services.agent_context import get_ctx

        ctx = get_ctx()
        cfg = getattr(ctx, "config", None)
        settings = getattr(ctx, "settings", None)
        zone = zone or _text(getattr(cfg, "zone_id", None) or getattr(settings, "zone_id", None)).lower()
        subnet_id = subnet_id or _text(
            getattr(cfg, "subnet_id", None) or getattr(settings, "subnet_id", None)
        )
    except Exception:
        pass
    return {"zone": zone or "lo", "subnet_id": subnet_id}


def _resolution(
    status: str,
    action: str,
    reason: str,
    destination: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    choices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "schema": RESOLUTION_SCHEMA,
        "status": status,
        "action": action,
        "reason": reason,
        "destination": validate_destination(destination),
        "current": dict(current),
        "choices": list(choices or []),
    }
    return result


def resolve_destination(
    destination: Mapping[str, Any],
    *,
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an explainable, non-mutating plan for the destination."""

    target = validate_destination(destination)
    context = dict(current or {})
    intent = target["intent"]
    if intent == CONNECT_REGISTER:
        return _resolution("ready", "register", "registration_intent_ready", target, context)
    if intent == NODE_CONNECT:
        return _resolution("ready", "connect_node", "node_connect_intent_ready", target, context)
    if intent == AUTH_LOGIN:
        return _resolution("ready", "login", "login_intent_ready", target, context)
    if intent == DRIVE_DOWNLOAD:
        return _resolution("ready", "download", "drive_download_intent_ready", target, context)
    if intent == DRIVE_VIEW:
        return _resolution("ready", "open", "drive_view_intent_ready", target, context)

    if _text(context.get("zone")).lower() != target["zone"]:
        return _resolution(
            "input_required",
            "switch_zone",
            "zone_mismatch",
            target,
            context,
            choices=[
                {"id": "switch_zone", "label": "Switch zone", "mutating": True},
                {"id": "cancel", "label": "Cancel", "mutating": False},
            ],
        )
    if not bool(context.get("authenticated")):
        return _resolution("waiting", "authenticate", "authentication_required", target, context)
    if _text(context.get("subnet_id")) != target["subnet_id"]:
        return _resolution(
            "input_required",
            "switch_subnet",
            "subnet_mismatch",
            target,
            context,
            choices=[
                {"id": "switch_subnet", "label": "Switch subnet", "mutating": True},
                {"id": "cancel", "label": "Cancel", "mutating": False},
            ],
        )
    if _text(context.get("webspace_id")) != target["webspace_id"]:
        return _resolution(
            "input_required",
            "switch_webspace",
            "webspace_mismatch",
            target,
            context,
            choices=[
                {"id": "switch_webspace", "label": "Switch Webspace", "mutating": True},
                {"id": "cancel", "label": "Cancel", "mutating": False},
            ],
        )
    current_kind = _text(context.get("space_kind")).lower()
    if current_kind and current_kind != target["space_kind"]:
        return _resolution("blocked", "unsupported", "space_kind_mismatch", target, context)
    if not bool(context.get("state_sync_fresh")):
        return _resolution("waiting", "wait_for_sync", "state_sync_not_fresh", target, context)
    expected_scenario = _text(target.get("expected_scenario_id"))
    current_scenario = _text(context.get("current_scenario_id"))
    expected_revision = _text(target.get("expected_revision"))
    current_revision = _text(context.get("current_revision"))
    if (expected_scenario and expected_scenario != current_scenario) or (
        expected_revision and current_revision and expected_revision != current_revision
    ):
        return _resolution(
            "input_required",
            "confirm_scenario",
            "scenario_context_mismatch",
            target,
            context,
            choices=[
                {"id": "open_current", "label": "Open current", "mutating": False},
                {"id": "switch_to_expected", "label": "Switch to expected", "mutating": True},
                {"id": "cancel", "label": "Cancel", "mutating": False},
            ],
        )
    return _resolution("ready", "open", "destination_matches_current_context", target, context)


__all__ = [
    "AUTH_LOGIN",
    "CONNECT_REGISTER",
    "DESTINATION_SCHEMA",
    "DRIVE_DOWNLOAD",
    "DRIVE_VIEW",
    "INTENTS",
    "NODE_CONNECT",
    "PREVIEW_STAGES",
    "RESOLUTION_SCHEMA",
    "SPACE_KINDS",
    "WEBSPACE_OPEN",
    "build_url",
    "drive_download_destination",
    "drive_view_destination",
    "login_destination",
    "node_connect_destination",
    "parse_url",
    "registration_destination",
    "resolve_destination",
    "runtime_scope",
    "validate_destination",
    "webspace_destination",
]
