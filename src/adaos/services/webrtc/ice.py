"""Authenticated, expiring Root ICE leases shared by signaling and native peers."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import logging
from pathlib import Path
import threading
import time
from typing import Any

_log = logging.getLogger(__name__)
FALLBACK_SERVERS = [
    {"urls": ["stun:stun.l.google.com:19302"]},
    {"urls": ["stun:stun1.l.google.com:19302"]},
]
_lock = threading.RLock()
_leases: dict[tuple[str, str, str], dict[str, Any]] = {}
_selections: dict[tuple[str, str], dict[str, Any]] = {}
_next_region: dict[str, int] = {}
_unavailable_until: dict[tuple[str, str], float] = {}
_fetching: set[tuple[str, str, str]] = set()


def _root_client():
    from adaos.services.agent_context import get_ctx
    from adaos.services.node_config import load_config
    from adaos.services.root.client import RootHttpClient

    ctx = get_ctx()
    config = getattr(ctx, "config", None) or load_config(ctx=ctx)
    base = str(getattr(getattr(config, "root_settings", None), "base_url", None)
               or getattr(ctx.settings, "api_base", None) or "").rstrip("/")
    cert, key = config.hub_cert_path(), config.hub_key_path()
    if not base.startswith("https://") or not cert.is_file() or not key.is_file():
        return None
    # Root's public HTTPS chain is verified by the system trust store. The
    # client identity certificate is independent of the HTTPS server issuer.
    return RootHttpClient(base_url=base, verify=True, cert=(str(cert), str(key)))


def _validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != "inimatic.webrtc.ice.v1":
        raise ValueError("invalid_root_ice_configuration")
    expiry = value.get("expires_at")
    if type(expiry) not in (int, float) or not time.time() + 60 < expiry <= time.time() + 86405:
        raise ValueError("expired_root_ice_configuration")
    for key in ("iceServers", "nodeIceServers"):
        servers = value.get(key)
        if not isinstance(servers, list) or not 1 <= len(servers) <= 8:
            raise ValueError("invalid_root_ice_servers")
        for server in servers:
            urls = server.get("urls") if isinstance(server, dict) else None
            if not isinstance(urls, list) or not 1 <= len(urls) <= 8 or any(not isinstance(url, str) or len(url) > 512 or not url.startswith(("stun:", "turn:", "turns:")) for url in urls):
                raise ValueError("invalid_root_ice_urls")
            if key == "nodeIceServers" and (len(urls) != 1 or not urls[0].startswith("turns:") or not urls[0].endswith("?transport=tcp")):
                raise ValueError("native_root_ice_requires_turn_tls")
            if any(url.startswith(("turn:", "turns:")) for url in urls):
                if any(not isinstance(server.get(field), str) or not 1 <= len(server[field]) <= 512 for field in ("username", "credential")):
                    raise ValueError("missing_root_turn_credentials")
    return deepcopy(value)


def _load_lease(peer_id: str) -> dict[str, Any] | None:
    key = None
    fetching = False
    try:
        client = _root_client()
        if client is None:
            return None
        cert = getattr(client, "cert", None)
        identity = hashlib.sha256(Path(cert[0]).read_bytes()).hexdigest() if cert else ""
        key = (str(client.base_url), identity, peer_id)
        with _lock:
            cached = _leases.get(key)
            if cached and cached["until"] > time.monotonic() and cached["lease"]["expires_at"] > time.time() + 90:
                return deepcopy(cached["lease"])
            if key in _fetching or _unavailable_until.get(key[:2], 0) > time.monotonic():
                return None
            _fetching.add(key)
            fetching = True
        response = client.request("POST", "/v1/hub/webrtc/ice", json={"peer_id": hashlib.sha256(peer_id.encode()).hexdigest()}, timeout=2.0)
        lease = _validate(response)
        with _lock:
            # Expired credentials must never survive through a stale cache hit.
            for old_key, old in list(_leases.items()):
                if old["until"] <= time.monotonic() or old["lease"]["expires_at"] <= time.time() + 90:
                    _leases.pop(old_key, None)
            if len(_leases) >= 256:
                _leases.pop(next(iter(_leases)))
            _leases[key] = {"until": time.monotonic() + 60, "lease": lease}
        return deepcopy(lease)
    except Exception as error:
        if key is not None:
            with _lock:
                if len(_unavailable_until) >= 256:
                    _unavailable_until.pop(next(iter(_unavailable_until)))
                _unavailable_until[key[:2]] = time.monotonic() + 5
        # No response bodies, credentials or local identity paths in logs.
        _log.info("managed ICE unavailable; retaining bootstrap discovery error_type=%s", type(error).__name__)
        return None
    finally:
        if fetching and key is not None:
            with _lock:
                _fetching.discard(key)


async def browser_configuration(peer_id: str, generation_id: str = "") -> dict[str, Any]:
    try:
        lease = await asyncio.wait_for(asyncio.to_thread(_load_lease, peer_id), timeout=2.5)
    except asyncio.TimeoutError:
        lease = None
    if lease is None:
        return {"schema": "inimatic.webrtc.ice.v1", "expires_at": 0, "iceServers": deepcopy(FALLBACK_SERVERS), "source": "bootstrap"}
    with _lock:
        key = (peer_id, generation_id)
        selection = _selections.get(key)
        if not selection or selection["expires_at"] <= time.time() + 60:
            for old_key, old in list(_selections.items()):
                if old["expires_at"] <= time.time() + 60:
                    _selections.pop(old_key, None)
                    _next_region.pop(old_key[0], None)
            if len(_selections) >= 256:
                old_key = next(iter(_selections))
                _selections.pop(old_key)
                _next_region.pop(old_key[0], None)
            index = _next_region.get(peer_id, 0) % len(lease["nodeIceServers"])
            _next_region[peer_id] = index + 1
            selection = {"expires_at": lease["expires_at"], "index": index, "server": lease["nodeIceServers"][index]}
            _selections[key] = selection
        return {"schema": lease["schema"], "expires_at": lease["expires_at"],
                "iceServers": deepcopy(lease["iceServers"]), "source": "root", "region_index": selection["index"]}


async def native_configuration(peer_id: str, generation_id: str = "") -> list[dict[str, Any]] | None:
    with _lock:
        selection = _selections.get((peer_id, generation_id))
        if selection and selection["expires_at"] > time.time() + 60:
            return [deepcopy(selection["server"])]
    await browser_configuration(peer_id, generation_id)
    with _lock:
        selection = _selections.get((peer_id, generation_id))
        return [deepcopy(selection["server"])] if selection and selection["expires_at"] > time.time() + 60 else None


def mark_connected(peer_id: str, generation_id: str) -> None:
    with _lock:
        selection = _selections.get((peer_id, generation_id))
        if selection and selection["expires_at"] > time.time() + 60:
            _next_region[peer_id] = selection["index"]
