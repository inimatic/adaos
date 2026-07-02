from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from adaos.domain.workspace_manifest import SkillActivationPolicy
from adaos.services.workspace_registry import find_workspace_registry_entry


InactiveSubscriptionStrategy = Literal["always_registered", "early_cheap_handlers"]


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
    startup = event_type in {"sys.ready", "runtime.ready", "adaos.runtime.ready"}
    if startup and policy.startup_allowed is False:
        return {
            "allowed": False,
            "governed": True,
            "reason": "startup_not_allowed",
            "mode": policy.mode,
        }
    if policy.background_refresh is False and not snapshot_request:
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
    if when.scenarios_active and scenario_id not in when.scenarios_active:
        return {
            "allowed": False,
            "governed": True,
            "reason": "scenario_not_active",
            "mode": policy.mode,
            "webspace_id": webspace_id,
            "active_scenario": scenario_id,
            "required_scenarios": list(when.scenarios_active),
        }

    client_present = _event_client_present(evt, snapshot_request=snapshot_request)
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
    }


__all__ = [
    "InactiveSubscriptionStrategy",
    "allows_background_refresh",
    "load_skill_activation_policy",
    "subscription_event_admission",
    "subscription_strategy_for_policy",
]
