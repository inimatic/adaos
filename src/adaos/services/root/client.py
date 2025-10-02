from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx


class RootHttpError(RuntimeError):
    def __init__(self, message: str, *, status_code: int, error_code: str | None = None, payload: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.payload = payload


@dataclass(slots=True)
class RootHttpClient:
    """Typed HTTP client for the Inimatic Root API."""

    timeout: float = 15.0
    base_url: str = "https://api.inimatic.com"
    _client: httpx.Client | None = None

    def __post_init__(self) -> None:
        if not self._client:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def close(self) -> None:
        if self._client:
            self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cert: tuple[str, str] | None = None,
        verify: str | bool | None = None,
    ) -> Any:
        assert self._client is not None
        try:
            response = self._client.request(method, path, json=json, headers=headers, cert=cert, verify=verify)
        except httpx.RequestError as exc:  # pragma: no cover - network errors in tests
            raise RootHttpError(f"{method} {path} failed: {exc}", status_code=0) from exc

        content: Any | None = None
        if response.content:
            try:
                content = response.json()
            except ValueError:
                content = response.text

        if response.status_code >= 400:
            error_code: str | None = None
            message = response.text or f"HTTP {response.status_code}"
            if isinstance(content, Mapping):
                detail = content.get("detail") or content.get("message") or content.get("error")
                if isinstance(detail, str):
                    message = detail
                code = content.get("code") or content.get("error")
                if isinstance(code, str):
                    error_code = code
            raise RootHttpError(message, status_code=response.status_code, error_code=error_code, payload=content)

        return content

    # Owner auth
    def owner_start(self, owner_id: str) -> dict:
        return dict(self._request("POST", "/v1/auth/owner/start", json={"owner_id": owner_id}))

    def owner_poll(self, device_code: str) -> dict:
        return dict(self._request("POST", "/v1/auth/owner/poll", json={"device_code": device_code}))

    def token_refresh(self, refresh_token: str) -> dict:
        return dict(self._request("POST", "/v1/auth/owner/refresh", json={"refresh_token": refresh_token}))

    def whoami(self, access_token: str) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        result = self._request("GET", "/v1/whoami", headers=headers)
        return dict(result) if isinstance(result, Mapping) else {}

    # Owner hubs
    def owner_hubs_list(self, access_token: str) -> list[dict]:
        headers = {"Authorization": f"Bearer {access_token}"}
        result = self._request("GET", "/v1/owner/hubs", headers=headers)
        if isinstance(result, list):
            return [dict(item) for item in result if isinstance(item, Mapping)]
        return []

    def owner_hubs_add(self, access_token: str, hub_id: str) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        payload = {"hub_id": hub_id}
        return dict(self._request("POST", "/v1/owner/hubs", headers=headers, json=payload))

    # PKI
    def pki_enroll(self, access_token: str, hub_id: str, csr_pem: str, ttl: str | None) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        payload: dict[str, Any] = {"hub_id": hub_id, "csr_pem": csr_pem}
        if ttl:
            payload["ttl"] = ttl
        return dict(self._request("POST", "/v1/pki/enroll", headers=headers, json=payload))

    # Legacy bootstrap
    def subnets_register(self, csr_pem: str, bootstrap_token: str, subnet_name: str | None) -> dict:
        payload: dict[str, Any] = {"csr_pem": csr_pem}
        if subnet_name:
            payload["subnet_name"] = subnet_name
        headers = {"X-Bootstrap-Token": bootstrap_token}
        return dict(self._request("POST", "/v1/subnets/register", json=payload, headers=headers))

    def nodes_register(
        self,
        csr_pem: str,
        *,
        bootstrap_token: str | None,
        mtls: tuple[str, str, str] | None,
        subnet_id: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"csr_pem": csr_pem}
        if subnet_id:
            payload["subnet_id"] = subnet_id
        headers: dict[str, str] | None = None
        cert: tuple[str, str] | None = None
        verify: str | bool | None = None
        if bootstrap_token:
            headers = {"X-Bootstrap-Token": bootstrap_token}
        elif mtls:
            cert_path, key_path, ca_path = mtls
            cert = (cert_path, key_path)
            verify = ca_path
        return dict(self._request("POST", "/v1/nodes/register", json=payload, headers=headers, cert=cert, verify=verify))


__all__ = ["RootHttpClient", "RootHttpError"]
