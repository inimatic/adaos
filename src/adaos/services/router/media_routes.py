from __future__ import annotations

import time
from typing import Any, Callable


_ROUTE_LABELS: dict[str, str] = {
    "local_http": "direct local hub HTTP",
    "root_media_relay": "bounded root media relay",
    "hub_webrtc_loopback": "browser-hub direct WebRTC media",
    "member_browser_direct": "browser-member direct WebRTC media",
}


def _normalize_need(need: str | None) -> str:
    token = str(need or "").strip().lower()
    if token in {"upload", "playback", "live_stream", "scenario_response_media"}:
        return token
    return "scenario_response_media"


def _normalize_producer_preference(token: str | None) -> str:
    value = str(token or "").strip().lower()
    if value in {"hub", "member", "router_selected"}:
        return value
    return "hub"


def _topology_state(
    *,
    topology_id: str,
    available: bool,
    producer_authority: str,
    ready_reason: str,
    unavailable_reason: str,
) -> dict[str, Any]:
    return {
        "topology_id": topology_id,
        "label": _ROUTE_LABELS.get(topology_id, topology_id),
        "available": bool(available),
        "producer_authority": str(producer_authority or "none"),
        "reason": ready_reason if available else unavailable_reason,
    }


def resolve_media_route_intent(
    *,
    need: str | None,
    target_webspace_id: str | None = None,
    producer_preference: str | None = None,
    preferred_member_id: str | None = None,
    candidate_member_ids: list[str] | None = None,
    direct_local_ready: bool,
    root_routed_ready: bool,
    hub_webrtc_ready: bool,
    member_browser_direct_possible: bool = False,
    member_browser_direct_admitted: bool = False,
    member_browser_direct_reason: str | None = None,
    candidate_member_total: int = 0,
    browser_session_total: int = 0,
    observed_failure: str | None = None,
) -> dict[str, Any]:
    need_norm = _normalize_need(need)
    producer_pref = _normalize_producer_preference(producer_preference)
    target_ws = str(target_webspace_id or "").strip() or None
    preferred_member = str(preferred_member_id or "").strip() or None
    candidate_members = [
        str(item or "").strip()
        for item in list(candidate_member_ids or [])
        if str(item or "").strip()
    ]
    if not preferred_member and candidate_members:
        preferred_member = candidate_members[0]
    member_direct_possible = bool(member_browser_direct_possible)
    member_direct_admitted = bool(member_browser_direct_admitted)
    member_direct_ready = (
        member_direct_possible
        and member_direct_admitted
        and int(candidate_member_total) > 0
        and int(browser_session_total) > 0
    )
    member_direct_reason = (
        str(member_browser_direct_reason or "").strip()
        or (
            "member_browser_direct_ready"
            if member_direct_ready
            else "member_browser_direct_not_possible"
            if not member_direct_possible
            else "member_browser_direct_not_admitted"
            if not member_direct_admitted
            else "member_browser_direct_missing_live_participants"
        )
    )

    abilities = {
        "local_http": _topology_state(
            topology_id="local_http",
            available=bool(direct_local_ready),
            producer_authority="hub",
            ready_reason="local_hub_api_authority_available",
            unavailable_reason="local_hub_api_authority_unavailable",
        ),
        "root_media_relay": _topology_state(
            topology_id="root_media_relay",
            available=bool(root_routed_ready),
            producer_authority="shared",
            ready_reason="root_media_relay_available",
            unavailable_reason="root_media_relay_unavailable",
        ),
        "hub_webrtc_loopback": _topology_state(
            topology_id="hub_webrtc_loopback",
            available=bool(hub_webrtc_ready),
            producer_authority="hub",
            ready_reason="hub_webrtc_media_available",
            unavailable_reason="hub_webrtc_media_unavailable",
        ),
        "member_browser_direct": _topology_state(
            topology_id="member_browser_direct",
            available=member_direct_ready,
            producer_authority="member",
            ready_reason="member_browser_direct_ready",
            unavailable_reason=member_direct_reason,
        ),
    }

    fallback_chain: list[str]
    if need_norm == "upload":
        fallback_chain = ["local_http", "root_media_relay"]
    elif need_norm == "playback":
        fallback_chain = ["local_http", "root_media_relay"]
    elif need_norm == "live_stream":
        fallback_chain = (
            ["member_browser_direct", "hub_webrtc_loopback", "root_media_relay"]
            if producer_pref == "member"
            else ["hub_webrtc_loopback", "member_browser_direct", "root_media_relay"]
        )
    else:
        fallback_chain = (
            ["member_browser_direct", "local_http", "root_media_relay", "hub_webrtc_loopback"]
            if producer_pref == "member"
            else ["local_http", "root_media_relay", "hub_webrtc_loopback", "member_browser_direct"]
        )

    preferred_topology = fallback_chain[0] if fallback_chain else None
    selected_topology = next(
        (topology_id for topology_id in fallback_chain if bool(abilities.get(topology_id, {}).get("available"))),
        None,
    )
    selected_state = abilities.get(selected_topology or "") if selected_topology else {}

    if selected_topology == "member_browser_direct":
        producer_target: dict[str, Any] | None = {
            "kind": "member",
            "member_id": preferred_member,
            "webspace_id": target_ws,
        }
    elif selected_topology:
        producer_target = {
            "kind": "hub",
            "webspace_id": target_ws,
        }
    else:
        producer_target = None

    degradation_reason: str | None = None
    if selected_topology is None:
        degradation_reason = "no_media_route_is_currently_available"
    elif preferred_topology and selected_topology != preferred_topology:
        preferred_state = abilities.get(preferred_topology, {})
        degradation_reason = str(preferred_state.get("reason") or f"{preferred_topology}_unavailable")

    selection_reason = (
        str(selected_state.get("reason") or "").strip()
        or degradation_reason
        or "no_media_route_selected"
    )
    active_route = selected_topology
    producer_authority = (
        str(selected_state.get("producer_authority") or "none")
        if selected_topology
        else "none"
    )
    observed_failure_token = str(observed_failure or "").strip() or None

    return {
        "route_intent": need_norm,
        "target_webspace_id": target_ws,
        "producer_preference": producer_pref,
        "preferred_member_id": preferred_member,
        "preferred_route": preferred_topology,
        "active_route": active_route,
        "delivery_topology": active_route,
        "producer_authority": producer_authority,
        "producer_target": producer_target,
        "selection_reason": selection_reason,
        "degradation_reason": degradation_reason,
        "fallback_chain": list(fallback_chain),
        "capabilities": {
            "candidate_routes": list(fallback_chain),
            "ability": abilities,
        },
        "attempt": {
            "sequence": 1,
            "state": "selected" if active_route else "unavailable",
            "active_route": active_route,
            "delivery_topology": active_route,
            "preferred_route": preferred_topology,
            "preferred_member_id": preferred_member,
            "producer_target": producer_target,
            "selection_reason": selection_reason,
            "degradation_reason": degradation_reason,
            "observed_failure": observed_failure_token,
            "switch_total": 0,
        },
        "member_browser_direct": {
            "possible": member_direct_possible,
            "admitted": member_direct_admitted,
            "ready": member_direct_ready,
            "reason": member_direct_reason,
            "candidate_member_total": int(candidate_member_total),
            "candidate_members": list(candidate_members),
            "preferred_member_id": preferred_member,
            "browser_session_total": int(browser_session_total),
        },
        "monitoring": {
            "watch_signals": [
                "local_http_ready",
                "root_media_relay_ready",
                "hub_webrtc_ready",
                "member_browser_direct_admitted",
                "browser_session_total",
                "candidate_member_total",
            ],
            "observed_failure": observed_failure_token,
        },
    }


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _route_ability_available(route_state: dict[str, Any], topology_id: str) -> bool:
    capabilities = route_state.get("capabilities") if isinstance(route_state.get("capabilities"), dict) else {}
    abilities = capabilities.get("ability") if isinstance(capabilities.get("ability"), dict) else {}
    entry = abilities.get(topology_id) if isinstance(abilities.get(topology_id), dict) else {}
    return _coerce_bool(entry.get("available"))


def _route_target_member_id(route_state: dict[str, Any]) -> str:
    preferred_member_id = str(route_state.get("preferred_member_id") or "").strip()
    if preferred_member_id:
        return preferred_member_id
    producer_target = route_state.get("producer_target") if isinstance(route_state.get("producer_target"), dict) else {}
    return str(producer_target.get("member_id") or "").strip()


def _route_signature(route_state: dict[str, Any] | None) -> tuple[str, str, str, str, str]:
    state = route_state if isinstance(route_state, dict) else {}
    producer_target = state.get("producer_target") if isinstance(state.get("producer_target"), dict) else {}
    return (
        str(state.get("active_route") or "").strip(),
        str(state.get("delivery_topology") or "").strip(),
        _route_target_member_id(state),
        str(producer_target.get("kind") or "").strip(),
        str(producer_target.get("webspace_id") or "").strip(),
    )


def _build_media_route_attempt(
    previous_route_state: dict[str, Any] | None,
    normalized_route_state: dict[str, Any],
    *,
    cause: str,
    ts: float,
    observed_failure: str | None = None,
    coerce_value: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    coerce = coerce_value or (lambda value: value)
    previous = previous_route_state if isinstance(previous_route_state, dict) else {}
    previous_attempt = coerce(previous.get("attempt"))
    previous_attempt = dict(previous_attempt) if isinstance(previous_attempt, dict) else {}
    previous_signature = _route_signature(previous)
    next_signature = _route_signature(normalized_route_state)
    has_previous_selection = any(previous_signature)
    route_changed = next_signature != previous_signature
    sequence = _coerce_int(previous_attempt.get("sequence"))
    if sequence <= 0:
        sequence = 1
    elif route_changed and has_previous_selection:
        sequence += 1
    switch_total = _coerce_int(previous_attempt.get("switch_total"))
    if route_changed and has_previous_selection:
        switch_total += 1
    selected_at = _coerce_float(previous_attempt.get("selected_at"))
    if selected_at is None or (route_changed and has_previous_selection):
        selected_at = ts
    last_switch_at = _coerce_float(previous_attempt.get("last_switch_at"))
    if route_changed and has_previous_selection:
        last_switch_at = ts
    previous_route = str(previous.get("active_route") or "").strip()
    previous_delivery_topology = str(previous.get("delivery_topology") or "").strip()
    previous_member_id = _route_target_member_id(previous)
    producer_target = (
        normalized_route_state.get("producer_target")
        if isinstance(normalized_route_state.get("producer_target"), dict)
        else {}
    )
    current_failure = str(observed_failure or "").strip() or None
    if current_failure is None:
        current_failure = str(previous_attempt.get("observed_failure") or "").strip() or None

    attempt = {
        "sequence": sequence,
        "state": "selected" if str(normalized_route_state.get("active_route") or "").strip() else "unavailable",
        "active_route": normalized_route_state.get("active_route"),
        "delivery_topology": normalized_route_state.get("delivery_topology"),
        "preferred_route": normalized_route_state.get("preferred_route"),
        "preferred_member_id": normalized_route_state.get("preferred_member_id"),
        "producer_target": dict(producer_target) if producer_target else None,
        "selection_reason": normalized_route_state.get("selection_reason"),
        "degradation_reason": normalized_route_state.get("degradation_reason"),
        "refresh_cause": cause,
        "observed_failure": current_failure,
        "switch_total": switch_total,
        "selected_at": selected_at,
        "last_switch_at": last_switch_at,
    }
    if route_changed and has_previous_selection:
        if previous_route:
            attempt["previous_route"] = previous_route
        if previous_delivery_topology:
            attempt["previous_delivery_topology"] = previous_delivery_topology
        if previous_member_id:
            attempt["previous_member_id"] = previous_member_id
    else:
        prior_route = str(previous_attempt.get("previous_route") or "").strip()
        prior_topology = str(previous_attempt.get("previous_delivery_topology") or "").strip()
        prior_member = str(previous_attempt.get("previous_member_id") or "").strip()
        if prior_route:
            attempt["previous_route"] = prior_route
        if prior_topology:
            attempt["previous_delivery_topology"] = prior_topology
        if prior_member:
            attempt["previous_member_id"] = prior_member
    return attempt


def build_media_route_refresh_payload(
    route_state: dict[str, Any],
    *,
    cause: str,
    browser_session_totals: tuple[int, int],
    observed_failure: str | None = None,
) -> dict[str, Any]:
    member_browser = (
        route_state.get("member_browser_direct")
        if isinstance(route_state.get("member_browser_direct"), dict)
        else {}
    )
    browser_session_total, connected_browser_session_total = browser_session_totals
    payload: dict[str, Any] = {
        "need": str(route_state.get("route_intent") or "scenario_response_media"),
        "producer_preference": str(route_state.get("producer_preference") or ""),
        "direct_local_ready": _route_ability_available(route_state, "local_http"),
        "root_routed_ready": _route_ability_available(route_state, "root_media_relay"),
        "hub_webrtc_ready": _route_ability_available(route_state, "hub_webrtc_loopback"),
        "browser_session_total": browser_session_total,
        "connected_browser_session_total": connected_browser_session_total,
        "refresh_cause": cause,
    }
    if member_browser:
        payload["member_browser_direct"] = {}
        if "admitted" in member_browser:
            payload["member_browser_direct"]["admitted"] = _coerce_bool(member_browser.get("admitted"))
    monitoring = route_state.get("monitoring") if isinstance(route_state.get("monitoring"), dict) else {}
    existing_failure = str(monitoring.get("observed_failure") or "").strip()
    if observed_failure:
        payload["observed_failure"] = observed_failure
    elif existing_failure:
        payload["observed_failure"] = existing_failure
    return payload


def resolve_media_route_state(
    payload: dict[str, Any],
    *,
    webspace_id: str,
    browser_session_totals: tuple[int, int],
    previous_route_state: dict[str, Any] | None = None,
    coerce_value: Callable[[Any], Any] | None = None,
) -> dict[str, Any] | None:
    coerce = coerce_value or (lambda value: value)
    raw_route = payload.get("route")
    if not isinstance(raw_route, dict) and isinstance(payload.get("route_intent"), dict):
        raw_route = payload.get("route_intent")

    route_state = coerce(raw_route) if isinstance(raw_route, dict) else None
    member_browser = payload.get("member_browser_direct")
    member_browser = member_browser if isinstance(member_browser, dict) else {}
    current_browser_session_total, current_connected_browser_session_total = browser_session_totals
    route_producer_target = (
        route_state.get("producer_target")
        if isinstance(route_state, dict) and isinstance(route_state.get("producer_target"), dict)
        else {}
    )
    preferred_member_id = str(payload.get("preferred_member_id") or "").strip()
    if not preferred_member_id and isinstance(route_state, dict):
        preferred_member_id = str(route_state.get("preferred_member_id") or "").strip()
    if not preferred_member_id:
        preferred_member_id = str(route_producer_target.get("member_id") or "").strip()
    raw_candidate_members = (
        member_browser.get("candidate_members")
        if isinstance(member_browser.get("candidate_members"), list)
        else payload.get("candidate_member_ids")
    )
    candidate_member_ids = (
        [str(item or "").strip() for item in raw_candidate_members if str(item or "").strip()]
        if isinstance(raw_candidate_members, list)
        else []
    )
    admitted_member_browser = (
        _coerce_bool(member_browser.get("admitted"))
        if member_browser and "admitted" in member_browser
        else _coerce_bool(payload.get("member_browser_direct_admitted"))
    )
    auto_member_browser: dict[str, Any] = {}
    if not preferred_member_id or not candidate_member_ids:
        try:
            from adaos.services.media_capability import member_browser_direct_foundation

            auto_member_browser = member_browser_direct_foundation(
                browser_session_total=(
                    _coerce_int(member_browser.get("browser_session_total"))
                    if member_browser and "browser_session_total" in member_browser
                    else (
                        _coerce_int(payload.get("browser_session_total"))
                        if "browser_session_total" in payload
                        else current_browser_session_total
                    )
                ),
                connected_browser_session_total=(
                    _coerce_int(member_browser.get("connected_browser_session_total"))
                    if member_browser and "connected_browser_session_total" in member_browser
                    else (
                        _coerce_int(payload.get("connected_browser_session_total"))
                        if "connected_browser_session_total" in payload
                        else current_connected_browser_session_total
                    )
                ),
                admitted=admitted_member_browser,
            )
        except Exception:
            auto_member_browser = {}
    if not preferred_member_id:
        preferred_member_id = str(auto_member_browser.get("preferred_member_id") or "").strip()
    if not candidate_member_ids:
        candidate_member_ids = [
            str(item or "").strip()
            for item in list(auto_member_browser.get("candidate_members") or [])
            if str(item or "").strip()
        ]

    if route_state is None:
        route_state = resolve_media_route_intent(
            need=str(payload.get("need") or payload.get("route_intent") or "scenario_response_media"),
            target_webspace_id=webspace_id,
            producer_preference=str(payload.get("producer_preference") or ""),
            preferred_member_id=preferred_member_id or None,
            candidate_member_ids=candidate_member_ids,
            direct_local_ready=_coerce_bool(payload.get("direct_local_ready")),
            root_routed_ready=_coerce_bool(payload.get("root_routed_ready")),
            hub_webrtc_ready=_coerce_bool(payload.get("hub_webrtc_ready")),
            member_browser_direct_possible=(
                _coerce_bool(member_browser.get("possible"))
                if member_browser and "possible" in member_browser
                else (
                    _coerce_bool(payload.get("member_browser_direct_possible"))
                    if "member_browser_direct_possible" in payload
                    else _coerce_bool(auto_member_browser.get("possible"))
                )
            ),
            member_browser_direct_admitted=(
                _coerce_bool(member_browser.get("admitted"))
                if member_browser and "admitted" in member_browser
                else (
                    _coerce_bool(payload.get("member_browser_direct_admitted"))
                    if "member_browser_direct_admitted" in payload
                    else _coerce_bool(auto_member_browser.get("admitted"))
                )
            ),
            member_browser_direct_reason=(
                str(member_browser.get("reason") or "").strip()
                or str(payload.get("member_browser_direct_reason") or "").strip()
                or str(auto_member_browser.get("reason") or "").strip()
                or None
            ),
            candidate_member_total=(
                _coerce_int(member_browser.get("candidate_member_total"))
                if member_browser and "candidate_member_total" in member_browser
                else (
                    _coerce_int(payload.get("candidate_member_total"))
                    if "candidate_member_total" in payload
                    else _coerce_int(auto_member_browser.get("candidate_member_total"))
                )
            ),
            browser_session_total=(
                _coerce_int(member_browser.get("browser_session_total"))
                if member_browser and "browser_session_total" in member_browser
                else (
                    _coerce_int(payload.get("browser_session_total"))
                    if "browser_session_total" in payload
                    else _coerce_int(auto_member_browser.get("browser_session_total"))
                )
            ),
            observed_failure=str(payload.get("observed_failure") or "").strip() or None,
        )

    if not isinstance(route_state, dict):
        return None

    monitoring = coerce(route_state.get("monitoring"))
    monitoring = dict(monitoring) if isinstance(monitoring, dict) else {}
    observed_failure = str(payload.get("observed_failure") or "").strip()
    if observed_failure and not monitoring.get("observed_failure"):
        monitoring["observed_failure"] = observed_failure

    normalized = dict(route_state)
    normalized_member_browser = coerce(normalized.get("member_browser_direct"))
    normalized_member_browser = dict(normalized_member_browser) if isinstance(normalized_member_browser, dict) else {}
    if preferred_member_id and not normalized.get("preferred_member_id"):
        normalized["preferred_member_id"] = preferred_member_id
    if candidate_member_ids and not isinstance(normalized_member_browser.get("candidate_members"), list):
        normalized_member_browser["candidate_members"] = list(candidate_member_ids)
    if preferred_member_id and not normalized_member_browser.get("preferred_member_id"):
        normalized_member_browser["preferred_member_id"] = preferred_member_id
    if candidate_member_ids and not normalized_member_browser.get("candidate_member_total"):
        normalized_member_browser["candidate_member_total"] = len(candidate_member_ids)
    if normalized_member_browser:
        normalized["member_browser_direct"] = normalized_member_browser
    refresh_cause = str(payload.get("refresh_cause") or "io.out.media.route").strip() or "io.out.media.route"
    updated_at = float(payload.get("ts") or time.time())
    effective_observed_failure = str(monitoring.get("observed_failure") or "").strip() or None
    attempt = _build_media_route_attempt(
        previous_route_state,
        normalized,
        cause=refresh_cause,
        ts=updated_at,
        observed_failure=effective_observed_failure,
        coerce_value=coerce,
    )
    normalized["attempt"] = attempt
    normalized["target_webspace_id"] = webspace_id
    normalized["route_administrator"] = "router"
    normalized["updated_at"] = updated_at
    monitoring["refresh_cause"] = refresh_cause
    monitoring["attempt_sequence"] = attempt.get("sequence")
    monitoring["switch_total"] = attempt.get("switch_total")
    monitoring["last_switch_at"] = attempt.get("last_switch_at")
    if monitoring:
        normalized["monitoring"] = monitoring
    return normalized


__all__ = [
    "build_media_route_refresh_payload",
    "resolve_media_route_intent",
    "resolve_media_route_state",
]
