"""External API access with local/zone/global fallback policy.

The client keeps a small channel-health policy in skill memory.  A skill still
describes the API it wants to call, but the route selection is handled here:
try local first when allowed, fall back to a zone proxy, then to the global
proxy, and periodically re-check local access so central proxy traffic can drop
back down when the network recovers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
import time
from typing import Any, Mapping, MutableMapping
from urllib.parse import urlparse

import requests

from adaos.sdk.data.skill_memory import get as memory_get, set as memory_set

_log = logging.getLogger("adaos.sdk.net.external_api")

DEFAULT_GLOBAL_PROXY_URL = "https://api.inimatic.com/v1/external-api/proxy"
DEFAULT_RECHECK_INTERVAL_S = 7 * 24 * 60 * 60
DEFAULT_CONNECT_TIMEOUT_S = 3.0
DEFAULT_READ_TIMEOUT_S = 10.0
_POLICY_KEY_PREFIX = "external_api.channel."


@dataclass(frozen=True, slots=True)
class ExternalApiResult:
    ok: bool
    response: requests.Response | None
    mode: str
    url: str
    error: str | None = None
    policy_changed: bool = False
    attempts: tuple[dict[str, Any], ...] = ()


def get(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    service: str | None = None,
    timeout: float | tuple[float, float] | None = None,
    recheck_interval_s: float = DEFAULT_RECHECK_INTERVAL_S,
    zone_proxy_url: str | None = None,
    global_proxy_url: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> ExternalApiResult:
    return request(
        "GET",
        url,
        params=params,
        service=service,
        timeout=timeout,
        recheck_interval_s=recheck_interval_s,
        zone_proxy_url=zone_proxy_url,
        global_proxy_url=global_proxy_url,
        headers=headers,
    )


def request(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    json: Any | None = None,
    service: str | None = None,
    timeout: float | tuple[float, float] | None = None,
    recheck_interval_s: float = DEFAULT_RECHECK_INTERVAL_S,
    zone_proxy_url: str | None = None,
    global_proxy_url: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> ExternalApiResult:
    method = str(method or "GET").upper()
    normalized_timeout = _timeout_tuple(timeout)
    channel_id = _channel_id(service, url)
    policy = _load_policy(channel_id)
    now = time.time()
    modes = _candidate_modes(policy, now=now, recheck_interval_s=recheck_interval_s)
    attempts: list[dict[str, Any]] = []
    policy_changed = False

    for mode in modes:
        started = time.time()
        try:
            response = _send(
                mode,
                method,
                url,
                params=params,
                json=json,
                timeout=normalized_timeout,
                zone_proxy_url=zone_proxy_url,
                global_proxy_url=global_proxy_url,
                headers=headers,
            )
            attempts.append(
                {
                    "mode": mode,
                    "ok": True,
                    "status_code": response.status_code,
                    "elapsed_ms": int((time.time() - started) * 1000),
                }
            )
            policy_changed = _record_success(policy, channel_id, mode, now=time.time()) or policy_changed
            return ExternalApiResult(
                ok=True,
                response=response,
                mode=mode,
                url=url,
                policy_changed=policy_changed,
                attempts=tuple(attempts),
            )
        except Exception as exc:
            error = _summarize_exception(exc)
            attempts.append(
                {
                    "mode": mode,
                    "ok": False,
                    "error": error,
                    "elapsed_ms": int((time.time() - started) * 1000),
                }
            )
            policy_changed = _record_failure(policy, channel_id, mode, error, now=time.time()) or policy_changed
            if not _is_channel_failure(exc):
                break

    return ExternalApiResult(
        ok=False,
        response=None,
        mode=str(policy.get("mode") or "local"),
        url=url,
        error=attempts[-1].get("error") if attempts else "external_api_request_failed",
        policy_changed=policy_changed,
        attempts=tuple(attempts),
    )


def _send(
    mode: str,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    json: Any | None,
    timeout: tuple[float, float],
    zone_proxy_url: str | None,
    global_proxy_url: str | None,
    headers: Mapping[str, str] | None,
) -> requests.Response:
    if mode == "local":
        return requests.request(method, url, params=params, json=json, headers=headers, timeout=timeout)

    proxy_url = _proxy_url_for_mode(mode, zone_proxy_url=zone_proxy_url, global_proxy_url=global_proxy_url)
    if not proxy_url:
        raise RuntimeError(f"{mode}_proxy_not_configured")
    payload: dict[str, Any] = {
        "method": method,
        "url": url,
        "params": dict(params or {}),
    }
    if json is not None:
        payload["json"] = json
    if headers:
        payload["headers"] = dict(headers)
    proxy_headers: dict[str, str] = {}
    token = os.getenv("ADAOS_EXTERNAL_API_PROXY_TOKEN", "").strip()
    if token:
        proxy_headers["Authorization"] = f"Bearer {token}"
    response = requests.post(proxy_url, json=payload, headers=proxy_headers, timeout=timeout)
    if response.status_code >= 400 and response.headers.get("x-adaos-external-api-proxy") != "1":
        raise RuntimeError(f"{mode}_proxy_http_{response.status_code}")
    return response


def _proxy_url_for_mode(mode: str, *, zone_proxy_url: str | None, global_proxy_url: str | None) -> str:
    if mode == "zone_proxy":
        return (
            str(zone_proxy_url or "").strip()
            or os.getenv("ADAOS_ZONE_API_PROXY_URL", "").strip()
            or os.getenv("ADAOS_EXTERNAL_API_ZONE_PROXY_URL", "").strip()
        )
    if mode == "global_proxy":
        return (
            str(global_proxy_url or "").strip()
            or os.getenv("ADAOS_GLOBAL_API_PROXY_URL", "").strip()
            or os.getenv("ADAOS_EXTERNAL_API_GLOBAL_PROXY_URL", "").strip()
            or DEFAULT_GLOBAL_PROXY_URL
        )
    return ""


def _candidate_modes(policy: Mapping[str, Any], *, now: float, recheck_interval_s: float) -> list[str]:
    mode = str(policy.get("mode") or "local")
    last_local_probe_at = _to_float(policy.get("last_local_probe_at")) or 0.0
    should_recheck_local = mode != "local" and now - last_local_probe_at >= max(0.0, recheck_interval_s)
    modes: list[str] = []
    if mode == "local" or should_recheck_local:
        modes.append("local")
    if mode == "zone_proxy":
        modes.append("zone_proxy")
    elif mode == "global_proxy":
        modes.extend(["zone_proxy", "global_proxy"])
    else:
        modes.extend(["zone_proxy", "global_proxy"])
    return _dedupe_modes(modes)


def _dedupe_modes(modes: list[str]) -> list[str]:
    out: list[str] = []
    for mode in modes:
        if mode not in out:
            out.append(mode)
    return out


def _load_policy(channel_id: str) -> dict[str, Any]:
    try:
        raw = memory_get(_policy_key(channel_id), {})
    except Exception:
        raw = {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _save_policy(channel_id: str, policy: Mapping[str, Any]) -> None:
    try:
        memory_set(_policy_key(channel_id), dict(policy))
    except Exception:
        _log.debug("failed to persist external API policy channel=%s", channel_id, exc_info=True)


def _record_success(policy: MutableMapping[str, Any], channel_id: str, mode: str, *, now: float) -> bool:
    previous = dict(policy)
    policy["mode"] = mode
    policy["last_success_mode"] = mode
    policy["last_success_at"] = now
    policy["last_error"] = None
    policy["updated_at"] = now
    if mode == "local":
        policy["last_local_probe_at"] = now
        policy["local_ok"] = True
    changed = previous != dict(policy)
    if changed:
        _save_policy(channel_id, policy)
        _log.info("external API policy changed channel=%s mode=%s local_ok=%s", channel_id, mode, policy.get("local_ok"))
    return changed


def _record_failure(policy: MutableMapping[str, Any], channel_id: str, mode: str, error: str, *, now: float) -> bool:
    previous = dict(policy)
    if mode == "local":
        policy["mode"] = "zone_proxy"
        policy["local_ok"] = False
        policy["last_local_probe_at"] = now
    elif mode == "zone_proxy":
        policy["mode"] = "global_proxy"
    policy["last_error"] = {"mode": mode, "error": error, "at": now}
    policy["updated_at"] = now
    changed = previous != dict(policy)
    if changed:
        _save_policy(channel_id, policy)
        _log.info("external API policy changed channel=%s mode=%s failed_mode=%s error=%s", channel_id, policy.get("mode"), mode, error)
    return changed


def _is_channel_failure(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ReadTimeout,
            requests.exceptions.Timeout,
            RuntimeError,
        ),
    )


def _summarize_exception(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    text = str(exc).strip()
    return text[:240] if text else exc.__class__.__name__


def _timeout_tuple(timeout: float | tuple[float, float] | None) -> tuple[float, float]:
    if isinstance(timeout, tuple):
        connect, read = timeout
        return float(connect), float(read)
    if timeout is not None:
        value = float(timeout)
        return value, value
    return DEFAULT_CONNECT_TIMEOUT_S, DEFAULT_READ_TIMEOUT_S


def _channel_id(service: str | None, url: str) -> str:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower()
    raw = str(service or host or url).strip().lower()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{_safe_token(raw)[:48]}.{digest}"


def _policy_key(channel_id: str) -> str:
    return f"{_POLICY_KEY_PREFIX}{channel_id}"


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value).strip("._") or "api"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["ExternalApiResult", "get", "request"]
