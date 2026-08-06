from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from adaos.services.nats_config import nats_url_uses_websocket, public_nats_ws_api
from adaos.services.node_runtime_state import (
    load_nats_runtime_config,
    migrate_legacy_nats_runtime_config,
    save_nats_runtime_config,
)
from adaos.services.runtime_identity import runtime_identity_snapshot
from adaos.services.zone_hosts import DEFAULT_PUBLIC_ROOT_BASE_URL, zone_public_base_url


class NatsCredentialService:
    """Own persisted hub-root credentials, refresh throttling, and canonical identity."""

    def __init__(self, service: Any, *, hub_id: str | None) -> None:
        self._service = service
        self.hub_id = str(hub_id or "").strip() or None
        self._last_fetch_at = 0.0

    def update_hub_id(self, hub_id: str | None) -> None:
        resolved = str(hub_id or "").strip()
        if resolved:
            self.hub_id = resolved

    def read(self) -> tuple[str | None, str | None, str | None]:
        try:
            node_nats = load_nats_runtime_config()
            if not node_nats:
                node_nats = migrate_legacy_nats_runtime_config()
            if not isinstance(node_nats, dict) or not node_nats:
                return None, None, None
            override = str(os.getenv("HUB_NATS_URL_OVERRIDE", "") or "").strip() or None
            raw_url = str(node_nats.get("ws_url") or "").strip() or None
            if override:
                raw_url = override
            requested_transport = str(os.getenv("HUB_NATS_TRANSPORT", "") or "").strip().lower()
            if (
                requested_transport in {"ws", "websocket", "websockets"}
                and raw_url
                and not nats_url_uses_websocket(raw_url)
            ):
                ws_candidates = self._service._nats_policy.public_ws_candidates(raw_url)
                raw_url = next(
                    (str(item).strip() for item in ws_candidates if nats_url_uses_websocket(item)),
                    public_nats_ws_api(),
                )
            normalized_url = self._service._nats_policy.normalize_ws_url(raw_url)
            user = str(node_nats.get("user") or "") or None
            password = str(node_nats.get("pass") or "") or None
            if normalized_url and raw_url and normalized_url != raw_url:
                save_nats_runtime_config(ws_url=normalized_url, user=user, password=password)
            elif (
                requested_transport in {"ws", "websocket", "websockets"}
                and normalized_url
                and raw_url
                and normalized_url != str(node_nats.get("ws_url") or "").strip()
            ):
                save_nats_runtime_config(ws_url=normalized_url, user=user, password=password)
            return normalized_url, user, password
        except Exception:
            return None, None, None

    async def fetch(self) -> bool:
        now = time.monotonic()
        if now - self._last_fetch_at < 30.0:
            return False
        self._last_fetch_at = now
        debug = os.getenv("HUB_NATS_VERBOSE", "0") == "1"
        try:
            from adaos.services.node_config import _expand_path as expand_path
            from adaos.services.node_config import load_config
            from adaos.services.root.client import RootHttpClient
        except Exception:
            return False

        service = self._service
        try:
            config = getattr(service.ctx, "config", None) or load_config(ctx=service.ctx)
        except Exception:
            config = None

        zone_id = str(
            os.getenv("ADAOS_ZONE_ID")
            or getattr(config, "zone_id", None)
            or ""
        ).strip().lower()
        if zone_id:
            base_url = zone_public_base_url(zone_id)
        else:
            base_url = (
                getattr(service.ctx.settings, "api_base", None)
                or getattr(getattr(config, "root_settings", None), "base_url", None)
                or DEFAULT_PUBLIC_ROOT_BASE_URL
            )
        try:
            ca = expand_path(
                getattr(getattr(config, "root_settings", None), "ca_cert", None),
                "keys/ca.cert",
            )
            cert = expand_path(
                getattr(getattr(getattr(config, "subnet_settings", None), "hub", None), "cert", None),
                "keys/hub_cert.pem",
            )
            key = expand_path(
                getattr(getattr(getattr(config, "subnet_settings", None), "hub", None), "key", None),
                "keys/hub_private.pem",
            )
        except Exception:
            ca = None
            cert = None
            key = None

        verify: Any = True
        if os.getenv("ADAOS_ROOT_VERIFY_CA", "0") == "1" and ca is not None:
            try:
                if ca.exists():
                    verify = str(ca)
            except Exception:
                pass
        cert_tuple = None
        if cert is not None and key is not None:
            try:
                if cert.exists() and key.exists():
                    cert_tuple = (str(cert), str(key))
            except Exception:
                cert_tuple = None

        client = RootHttpClient(base_url=str(base_url), verify=verify, cert=cert_tuple)
        if not client.cert:
            if debug:
                logging.getLogger("adaos.hub_io").warning(
                    "nats.mtls_missing",
                    extra={
                        "extra": {
                            "base_url": str(base_url),
                            "verify": str(verify),
                            "ca_path": str(ca) if ca is not None else None,
                            "cert_path": str(cert) if cert is not None else None,
                            "key_path": str(key) if key is not None else None,
                            "have_ca": bool(ca and ca.exists()),
                            "have_cert": bool(cert and cert.exists()),
                            "have_key": bool(key and key.exists()),
                        }
                    },
                )
            return False

        def _request_token() -> dict[str, Any] | None:
            try:
                identity = runtime_identity_snapshot()
                data = client.request(
                    "POST",
                    "/v1/hub/nats/token",
                    json={
                        "runtime_instance_id": str(identity.get("runtime_instance_id") or ""),
                        "transition_role": str(identity.get("transition_role") or "active"),
                        "active_slot": str(os.getenv("ADAOS_ACTIVE_CORE_SLOT") or ""),
                        "runtime_host": str(os.getenv("ADAOS_RUNTIME_HOST") or ""),
                        "runtime_port": str(os.getenv("ADAOS_RUNTIME_PORT") or ""),
                    },
                )
                return dict(data) if isinstance(data, dict) else None
            except Exception as exc:
                if debug:
                    logging.getLogger("adaos.hub_io").warning(
                        "nats.token_request_failed",
                        extra={
                            "extra": {
                                "base_url": str(base_url),
                                "verify": str(verify),
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            }
                        },
                    )
                return None

        data = await asyncio.to_thread(_request_token)
        if not isinstance(data, dict):
            return False
        token = data.get("hub_nats_token")
        nats_user = data.get("nats_user")
        response_hub_id = data.get("hub_id")
        nats_ws_url = service._nats_policy.normalize_ws_url(data.get("nats_ws_url"))
        if not token or not nats_user or not nats_ws_url:
            if debug:
                logging.getLogger("adaos.hub_io").warning(
                    "nats.token_response_incomplete",
                    extra={"extra": {"data": data}},
                )
            return False

        try:
            resolved_hub_id, resolved_nats_user = service._nats_policy.canonical_identity(
                local_hub_id=getattr(config, "subnet_id", None),
                nats_user=str(nats_user),
                response_hub_id=str(response_hub_id or "").strip() or None,
            )
            transport = str(os.getenv("HUB_NATS_TRANSPORT", "") or "").strip().lower()
            if transport in {"tcp", "nats"}:
                selected_url = str(service._nats_policy.public_tcp_candidates(None)[0])
            else:
                selected_url = str(nats_ws_url)
            save_nats_runtime_config(
                ws_url=selected_url,
                user=str(resolved_nats_user or nats_user),
                password=str(token),
            )
            self.update_hub_id(resolved_hub_id)
            return True
        except Exception:
            return False
