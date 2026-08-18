from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
from adaos.services.settings import _parse_env_file
from adaos.services.runtime_topology import (
    DEFAULT_RUNTIME_PORT,
    http_base,
    is_loopback_http_url,
    runtime_fallback_http_bases,
    supervisor_base_candidates_from_env,
)


def _is_local_url(url: str | None) -> bool:
    return is_loopback_http_url(url)


def _normalize_url(raw: str | None) -> str | None:
    txt = str(raw or "").strip()
    if not txt:
        return None
    return txt.rstrip("/")


def _pick_env_override_url() -> str | None:
    # Explicit control base variables are authoritative and may point to a non-local server.
    for key in ("ADAOS_CONTROL_URL", "ADAOS_CONTROL_BASE"):
        raw = _normalize_url(os.getenv(key, ""))
        if raw:
            return raw
    return None


def _pick_local_env_url() -> str | None:
    # Accept self-advertised URLs only when they point to local host.
    for key in ("ADAOS_SELF_BASE_URL", "ADAOS_HUB_URL"):
        raw = _normalize_url(os.getenv(key, ""))
        if raw and _is_local_url(raw):
            return raw
    # Backward-compat: accept legacy ADAOS_BASE/ADAOS_API_BASE only for local URLs.
    for key in ("ADAOS_BASE", "ADAOS_API_BASE"):
        raw = _normalize_url(os.getenv(key, ""))
        if raw and _is_local_url(raw):
            return raw
    return None


def _pick_env_token() -> str | None:
    for key in ("ADAOS_TOKEN", "ADAOS_HUB_TOKEN", "HUB_TOKEN"):
        raw = str(os.getenv(key, "") or "").strip()
        if raw:
            return raw
    return None


def _autostart_service_token() -> str | None:
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.autostart import status as autostart_status

        info = autostart_status(get_ctx())
        wrapper_env = info.get("wrapper_env") if isinstance(info, dict) and isinstance(info.get("wrapper_env"), dict) else {}
        for key in ("ADAOS_TOKEN", "ADAOS_HUB_TOKEN", "HUB_TOKEN"):
            raw = str(wrapper_env.get(key, "") or "").strip()
            if raw:
                return raw
        shared_dotenv_path = str(info.get("shared_dotenv_path") or "").strip() if isinstance(info, dict) else ""
        if shared_dotenv_path:
            try:
                env_file_vars = _parse_env_file(shared_dotenv_path)
            except Exception:
                env_file_vars = {}
            for key in ("ADAOS_TOKEN", "ADAOS_HUB_TOKEN", "HUB_TOKEN"):
                raw = str(env_file_vars.get(key, "") or "").strip()
                if raw:
                    return raw
    except Exception:
        pass
    return None


def _append_candidate(candidates: list[str], seen: set[str], raw: str | None) -> None:
    url = _normalize_url(raw)
    if not url or url in seen:
        return
    candidates.append(url)
    seen.add(url)


def _node_config_control_url() -> tuple[str | None, str | None]:
    try:
        from adaos.services.node_config import load_config

        conf = load_config()
        role = str(getattr(conf, "role", "") or "").strip().lower() or None
        cfg_url = _normalize_url(getattr(conf, "hub_url", None))
        return role, cfg_url
    except Exception:
        return None, None


def _autostart_control_url() -> str | None:
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.autostart import status as autostart_status

        info = autostart_status(get_ctx())
        raw = _normalize_url((info or {}).get("url") if isinstance(info, dict) else None)
        if raw and _is_local_url(raw):
            return raw
    except Exception:
        pass
    return None


def _autostart_supervisor_urls() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.autostart import status as autostart_status

        info = autostart_status(get_ctx())
        if isinstance(info, dict):
            _append_candidate(candidates, seen, info.get("supervisor_url"))
            wrapper_env = info.get("wrapper_env") if isinstance(info.get("wrapper_env"), dict) else {}
            host = str(wrapper_env.get("ADAOS_SUPERVISOR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
            raw_port = str(wrapper_env.get("ADAOS_SUPERVISOR_PORT") or "").strip()
            if raw_port:
                try:
                    _append_candidate(candidates, seen, http_base(host=host, port=int(raw_port)))
                except Exception:
                    pass
    except Exception:
        pass
    return [candidate for candidate in candidates if _is_local_url(candidate)]


def _supervisor_public_runtime_url() -> str | None:
    supervisor_candidates = _autostart_supervisor_urls()
    seen = set(supervisor_candidates)
    for candidate in supervisor_base_candidates_from_env(
        require_signal=False,
        include_localhost=True,
        include_default_loopback=True,
    ):
        if _is_local_url(candidate):
            _append_candidate(supervisor_candidates, seen, candidate)

    for supervisor_url in supervisor_candidates:
        sess = requests.Session()
        try:
            sess.trust_env = False
        except Exception:
            pass
        try:
            resp = sess.get(supervisor_url + "/api/supervisor/public/update-status", timeout=0.5)
            if int(resp.status_code) != 200:
                continue
            payload = resp.json()
            if not isinstance(payload, dict):
                continue
            runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
            runtime_url = _normalize_url(runtime.get("runtime_url"))
            if not runtime_url or not _is_local_url(runtime_url):
                continue
            transition_role = str(runtime.get("transition_role") or "").strip().lower()
            if transition_role == "candidate" or runtime.get("admin_mutation_allowed") is False:
                continue
            if not bool(runtime.get("listener_running")):
                continue
            if runtime.get("runtime_api_ready") is False:
                continue
            return runtime_url
        except Exception:
            continue
    return None


def _pidfile_control_urls() -> list[str]:
    try:
        from adaos.services.agent_context import get_ctx

        state_root = get_ctx().paths.state_dir()
        state_dir = Path(state_root() if callable(state_root) else state_root)
        api_dir = state_dir / "api"
        if not api_dir.exists():
            return []
        found: list[tuple[float, str]] = []
        for path in api_dir.glob("serve-*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            raw = _normalize_url(data.get("advertised_base"))
            if not raw or not _is_local_url(raw):
                continue
            try:
                started_at = float(data.get("started_at") or 0.0)
            except Exception:
                started_at = 0.0
            found.append((started_at, raw))
        found.sort(key=lambda item: item[0], reverse=True)
        return [url for _, url in found]
    except Exception:
        return []


def _looks_like_control_api_response(code: int | None, payload: dict[str, Any] | None) -> bool:
    if code is None:
        return False
    if isinstance(payload, dict):
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        transition_role = str(runtime.get("transition_role") or "").strip().lower()
        if transition_role == "candidate":
            return False
        if runtime.get("admin_mutation_allowed") is False:
            return False
        return True
    return int(code) in {401, 403}


def _control_token_candidates(*, explicit: str | None = None) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _push(raw: str | None) -> None:
        token = str(raw or "").strip()
        if not token or token in seen:
            return
        candidates.append(token)
        seen.add(token)

    _push(explicit)
    _push(_pick_env_token())
    _push(_autostart_service_token())
    try:
        from adaos.services.node_config import load_config

        conf = load_config()
        _push(getattr(conf, "token", None))
    except Exception:
        pass
    _push("dev-local-token")
    return candidates


def _probe_control_token_status(*, base_url: str, token: str, timeout_s: float = 0.5) -> int | None:
    base = str(base_url).rstrip("/")
    headers = {"X-AdaOS-Token": str(token or "")}
    sess = requests.Session()
    try:
        sess.trust_env = False
    except Exception:
        pass
    try:
        resp = sess.get(base + "/api/node/status", headers=headers, timeout=float(timeout_s))
        return int(resp.status_code)
    except Exception:
        return None


def resolve_control_base_url(
    *,
    explicit: str | None = None,
    hub_url: str | None = None,
    prefer_local: bool = False,
) -> str:
    """
    Resolve which control API base URL to use.

    Precedence:
    1) explicit (if provided)
    2) hub_url (if provided)
    3) env (ADAOS_CONTROL_URL / ADAOS_CONTROL_BASE, plus local-only ADAOS_SELF_BASE_URL / ADAOS_HUB_URL / ADAOS_BASE / ADAOS_API_BASE)
    4) localhost fallback
    """
    if explicit is not None:
        txt = _normalize_url(explicit)
        if txt:
            return txt
    if not prefer_local and hub_url is not None:
        txt = _normalize_url(hub_url)
        if txt:
            return txt

    role, cfg_url = _node_config_control_url()
    if not prefer_local and role == "member" and cfg_url:
        return cfg_url

    env_override = _pick_env_override_url()
    if env_override and (not prefer_local or _is_local_url(env_override)):
        return env_override

    candidates: list[str] = []
    seen: set[str] = set()
    if role == "hub" and cfg_url and _is_local_url(cfg_url):
        _append_candidate(candidates, seen, cfg_url)
    _append_candidate(candidates, seen, _supervisor_public_runtime_url())
    _append_candidate(candidates, seen, _pick_local_env_url())
    _append_candidate(candidates, seen, _autostart_control_url())
    for raw in _pidfile_control_urls():
        _append_candidate(candidates, seen, raw)
    if role == "member" and prefer_local:
        fallback_bases = runtime_fallback_http_bases(prefer_member=True, include_localhost=True)
    else:
        fallback_bases = runtime_fallback_http_bases(include_localhost=True, order="host")
    for raw in fallback_bases:
        _append_candidate(candidates, seen, raw)

    token = resolve_control_token()
    for candidate in candidates:
        code, payload = probe_control_api(base_url=candidate, token=token, timeout_s=0.35)
        if _looks_like_control_api_response(code, payload):
            return candidate
    if candidates:
        if role == "member" and prefer_local:
            for candidate in candidates:
                if ":8778" in str(candidate):
                    return candidate
        return candidates[0]
    return http_base(port=DEFAULT_RUNTIME_PORT)


def resolve_control_token(*, explicit: str | None = None, base_url: str | None = None) -> str:
    candidates = _control_token_candidates(explicit=explicit)
    base = _normalize_url(base_url)
    if base and _is_local_url(base):
        for token in candidates:
            if _probe_control_token_status(base_url=base, token=token, timeout_s=0.35) == 200:
                return token
    return candidates[0] if candidates else "dev-local-token"


def probe_control_api(*, base_url: str, token: str, timeout_s: float = 2.0) -> tuple[int | None, dict[str, Any] | None]:
    """
    Best-effort probe: GET /api/node/status.
    Returns (status_code, json_payload_or_None). status_code None means unreachable.
    """
    base = str(base_url).rstrip("/")
    headers = {"X-AdaOS-Token": str(token or "")}
    sess = requests.Session()
    try:
        sess.trust_env = False
    except Exception:
        pass
    try:
        resp = sess.get(base + "/api/node/status", headers=headers, timeout=float(timeout_s))
    except Exception:
        resp = None
    if resp is not None:
        try:
            payload = resp.json()
        except Exception:
            payload = None
        return int(resp.status_code), payload if isinstance(payload, dict) else None
    try:
        resp = sess.get(base + "/api/ping", headers={"Accept": "application/json"}, timeout=float(timeout_s))
    except Exception:
        return None, None
    if int(resp.status_code) != 200:
        return int(resp.status_code), None
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        return int(resp.status_code), payload
    return int(resp.status_code), {"ok": True, "ping": True}
