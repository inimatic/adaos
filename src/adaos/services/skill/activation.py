from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Literal

import yaml

from adaos.domain.workspace_manifest import SkillActivationPolicy
from adaos.services.workspace_registry import find_workspace_registry_entry


InactiveSubscriptionStrategy = Literal["always_registered", "early_cheap_handlers"]
_STREAM_RECEIVER_CONTROL_TOPICS = {
    "webio.stream.snapshot.requested",
    "webio.stream.subscription.changed",
    "webio.yjs.snapshot.requested",
    "webio.yjs.subscription.changed",
}
_UI_CONTROL_TOPIC_SUFFIXES = (".action",)


def load_skill_activation_policy(
    workspace_root: Path,
    skill_name: str,
    *,
    fallback_to_scan: bool = True,
) -> SkillActivationPolicy | None:
    token = str(skill_name or "").strip()
    if not token:
        return None
    entry = find_workspace_registry_entry(
        workspace_root,
        kind="skills",
        name_or_id=token,
        fallback_to_scan=fallback_to_scan,
    )
    if not isinstance(entry, dict):
        return None
    return SkillActivationPolicy.from_mapping(entry.get("activation"))


def _clean_receiver(value: Any) -> str:
    try:
        token = str(value or "").strip()
    except Exception:
        return ""
    return token


def _append_receiver_pattern(patterns: list[str], value: Any) -> None:
    token = _clean_receiver(value)
    if token and token not in patterns:
        patterns.append(token)


def _candidate_skill_roots(base: Path, skill_name: str) -> list[Path]:
    root = Path(base)
    token = str(skill_name or "").strip()
    if not token:
        return []
    candidates = [
        root / token,
        root / ".runtime" / token,
        root / "skills" / token,
        root / "skills" / ".runtime" / token,
    ]
    seen: set[str] = set()
    result: list[Path] = []
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except Exception:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _receiver_patterns_from_webui(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []
    webio = raw.get("webio")
    if not isinstance(webio, dict):
        return []
    receivers = webio.get("receivers")
    patterns: list[str] = []
    if isinstance(receivers, dict):
        for key in receivers.keys():
            _append_receiver_pattern(patterns, key)
    return patterns


def _receiver_patterns_from_skill_yaml(path: Path) -> list[str]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []
    patterns: list[str] = []
    data_routes = raw.get("data_routes")
    if isinstance(data_routes, list):
        for item in data_routes:
            if not isinstance(item, dict):
                continue
            route = _clean_receiver(item.get("route"))
            if route == "stream":
                _append_receiver_pattern(patterns, item.get("receiver"))
            elif route == "yjs":
                _append_receiver_pattern(patterns, item.get("projection_slot") or item.get("slot"))
    return patterns


def load_skill_stream_receiver_patterns(skills_root: Path, skill_name: str) -> tuple[str, ...]:
    """Return webio control targets declared by a skill's webui/manifest files."""

    patterns: list[str] = []
    for skill_root in _candidate_skill_roots(Path(skills_root), skill_name):
        webui_patterns = _receiver_patterns_from_webui(skill_root / "webui.json")
        for pattern in webui_patterns:
            _append_receiver_pattern(patterns, pattern)
        yaml_patterns = _receiver_patterns_from_skill_yaml(skill_root / "skill.yaml")
        for pattern in yaml_patterns:
            _append_receiver_pattern(patterns, pattern)
    return tuple(patterns)


def _receiver_pattern_matches(pattern: str, receiver: str) -> bool:
    pattern_token = _clean_receiver(pattern)
    receiver_token = _clean_receiver(receiver)
    if not pattern_token or not receiver_token:
        return False
    if pattern_token in {"*", receiver_token}:
        return True
    if "*" in pattern_token:
        return fnmatch.fnmatchcase(receiver_token, pattern_token)
    if "$" in pattern_token:
        wildcard = ".".join("*" if part.startswith("$") else part for part in pattern_token.split("."))
        return fnmatch.fnmatchcase(receiver_token, wildcard)
    return False


def _receiver_from_topic_token(topic_token: str) -> str:
    token = _clean_receiver(topic_token)
    for prefix in ("webio.stream.", "webio.yjs."):
        if not token.startswith(prefix):
            continue
        suffix = token[len(prefix) :]
        parts = [part for part in suffix.split(".") if part]
        if len(parts) < 2:
            return ""
        if parts[0] == "nodes":
            return ".".join(parts[2:]) if len(parts) >= 3 else ""
        receiver_parts = parts[1:]
        if len(receiver_parts) >= 3 and receiver_parts[0] == "nodes":
            receiver_parts = receiver_parts[2:]
        return ".".join(receiver_parts)
    return ""


def _stream_receiver_from_event(evt: Any) -> str:
    payload = _event_payload(evt)
    receiver = _clean_receiver(
        payload.get("receiver")
        or payload.get("projection_slot")
        or payload.get("slot")
        or payload.get("stream")
    )
    if receiver:
        return receiver
    return _receiver_from_topic_token(payload.get("topic"))


def stream_receiver_event_admission(
    receiver_patterns: tuple[str, ...] | list[str],
    evt: Any,
    topic: str,
) -> dict[str, Any]:
    event_type = _event_type(evt, topic)
    if event_type not in _STREAM_RECEIVER_CONTROL_TOPICS:
        return {"allowed": True, "governed": False, "reason": "not_stream_receiver_control"}

    receiver = _stream_receiver_from_event(evt)
    if not receiver:
        return {"allowed": True, "governed": False, "reason": "stream_receiver_unknown"}

    patterns = tuple(_clean_receiver(item) for item in receiver_patterns or () if _clean_receiver(item))
    if not patterns:
        return {"allowed": True, "governed": False, "reason": "stream_receiver_policy_missing"}

    for pattern in patterns:
        if _receiver_pattern_matches(pattern, receiver):
            return {
                "allowed": True,
                "governed": True,
                "reason": "stream_receiver_admitted",
                "receiver": receiver,
                "matched_pattern": pattern,
            }

    return {
        "allowed": False,
        "governed": True,
        "reason": "stream_receiver_not_declared",
        "receiver": receiver,
        "receiver_patterns": list(patterns[:12]),
    }


def _is_ui_control_event_type(event_type: str) -> bool:
    token = str(event_type or "").strip()
    if token in _STREAM_RECEIVER_CONTROL_TOPICS:
        return True
    return any(token.endswith(suffix) for suffix in _UI_CONTROL_TOPIC_SUFFIXES)


def subscription_strategy_for_policy(policy: SkillActivationPolicy | None) -> InactiveSubscriptionStrategy:
    if policy is None:
        return "always_registered"
    if policy.mode in {"lazy", "on_demand"}:
        return "early_cheap_handlers"
    return "always_registered"


def allows_background_refresh(
    policy: SkillActivationPolicy | None,
    *,
    startup: bool = False,
    scenario_active: bool | None = None,
    client_present: bool | None = None,
    webspace_is_target: bool | None = None,
) -> bool:
    if policy is None:
        return True
    if startup and policy.startup_allowed is False:
        return False
    if policy.background_refresh is False:
        return False

    when = policy.when
    if when.scenarios_active and scenario_active is False:
        return False
    if when.client_presence is True and client_present is False:
        return False
    if when.webspace_scope in {"active", "listed"} and webspace_is_target is False:
        return False
    return True


def _event_payload(evt: Any) -> dict[str, Any]:
    if isinstance(evt, dict):
        payload = evt.get("payload") if "payload" in evt and "type" in evt else evt
        return payload if isinstance(payload, dict) else {}
    payload = getattr(evt, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _event_type(evt: Any, topic: str) -> str:
    if isinstance(evt, dict):
        return str(evt.get("type") or topic or "").strip()
    return str(getattr(evt, "type", None) or topic or "").strip()


def _event_webspace_id(evt: Any) -> str | None:
    payload = _event_payload(evt)
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    raw = (
        payload.get("webspace_id")
        or payload.get("workspace_id")
        or meta.get("webspace_id")
        or meta.get("workspace_id")
        or None
    )
    token = str(raw or "").strip()
    if not token:
        return None
    return "desktop" if token == "default" else token


def _event_client_present(evt: Any, *, snapshot_request: bool) -> bool:
    if snapshot_request:
        return True
    payload = _event_payload(evt)
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    for key in ("client_present", "browser_session_id", "device_id", "connection_id", "subscription_id"):
        if payload.get(key) or meta.get(key):
            return True
    return False


def _current_scenario_for_webspace(webspace_id: str | None) -> str | None:
    token = str(webspace_id or "").strip()
    if not token:
        return None
    try:
        from adaos.services.yjs.doc import try_read_live_map_value

        live_hit, raw_current = try_read_live_map_value(token, "ui", "current_scenario")
        if live_hit:
            current = str(raw_current or "").strip()
            if current:
                return current
    except Exception:
        return None
    return None


def subscription_event_admission(
    policy: SkillActivationPolicy | None,
    evt: Any,
    topic: str,
) -> dict[str, Any]:
    if policy is None:
        return {"allowed": True, "governed": False, "reason": "no_activation_policy"}
    if policy.mode == "eager":
        return {"allowed": True, "governed": True, "reason": "eager_skill"}

    event_type = _event_type(evt, topic)
    snapshot_request = event_type in {
        "webio.stream.snapshot.requested",
        "webio.yjs.snapshot.requested",
    }
    ui_control_request = _is_ui_control_event_type(event_type)
    startup = event_type in {"sys.ready", "runtime.ready", "adaos.runtime.ready"}
    if startup and policy.startup_allowed is False:
        return {
            "allowed": False,
            "governed": True,
            "reason": "startup_not_allowed",
            "mode": policy.mode,
        }
    if policy.background_refresh is False and not ui_control_request:
        return {
            "allowed": False,
            "governed": True,
            "reason": "background_refresh_disabled",
            "mode": policy.mode,
        }

    webspace_id = _event_webspace_id(evt)
    when = policy.when

    if when.webspaces and (not webspace_id or webspace_id not in when.webspaces):
        return {
            "allowed": False,
            "governed": True,
            "reason": "webspace_not_admitted",
            "mode": policy.mode,
            "webspace_id": webspace_id,
        }

    if when.webspace_scope in {"active", "listed"} and not webspace_id:
        return {
            "allowed": False,
            "governed": True,
            "reason": "webspace_scope_unknown",
            "mode": policy.mode,
        }

    scenario_id = str(_event_payload(evt).get("scenario_id") or "").strip() or None
    if scenario_id is None:
        scenario_id = _current_scenario_for_webspace(webspace_id)
    scenario_assumed_from_ui_control = False
    if when.scenarios_active and scenario_id not in when.scenarios_active:
        if scenario_id is None and ui_control_request:
            scenario_assumed_from_ui_control = True
        else:
            return {
                "allowed": False,
                "governed": True,
                "reason": "scenario_not_active",
                "mode": policy.mode,
                "webspace_id": webspace_id,
                "active_scenario": scenario_id,
                "required_scenarios": list(when.scenarios_active),
            }

    client_present = _event_client_present(evt, snapshot_request=ui_control_request)
    if when.client_presence is True and not client_present:
        return {
            "allowed": False,
            "governed": True,
            "reason": "client_presence_required",
            "mode": policy.mode,
            "webspace_id": webspace_id,
            "active_scenario": scenario_id,
        }

    return {
        "allowed": True,
        "governed": True,
        "reason": "activation_policy_admitted",
        "mode": policy.mode,
        "webspace_id": webspace_id,
        "active_scenario": scenario_id,
        "snapshot_request": bool(snapshot_request),
        "ui_control_request": bool(ui_control_request),
        "scenario_assumed_from_ui_control": bool(scenario_assumed_from_ui_control),
    }


__all__ = [
    "InactiveSubscriptionStrategy",
    "allows_background_refresh",
    "load_skill_activation_policy",
    "load_skill_stream_receiver_patterns",
    "stream_receiver_event_admission",
    "subscription_event_admission",
    "subscription_strategy_for_policy",
]
