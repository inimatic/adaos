"""Durable local acceptance for routed OAuth authorization responses.

Root owns the short-lived public callback route, while Core owns provider
credentials and the authorization-code exchange.  A Root acknowledgement must
therefore mean that the selected Core has durably accepted the encrypted
delivery, not that every provider network request has already completed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from typing import Any, Callable, Mapping

from adaos.domain.integration_ingress import (
    IngressAcknowledgement,
    RoutedIngressEnvelope,
)


OAUTH_DELIVERY_SCHEMA = "adaos.integration.oauth_delivery.v1"
_DELIVERY_PREFIX = "integration:ingress:oauth-delivery:"
_LOCK = threading.RLock()


class OAuthIngressDeliveryError(ValueError):
    """Raised when a durable delivery record cannot be admitted safely."""


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _epoch(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError) as exc:
        raise OAuthIngressDeliveryError("oauth_delivery_expiry_invalid") from exc


def _token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class OAuthIngressDeliveryStore:
    """Credential-vault-backed inbox for encrypted routed OAuth envelopes."""

    def __init__(self, vault: Any, *, clock: Callable[[], float] = time.time) -> None:
        if vault is None:
            raise OAuthIngressDeliveryError("credential_vault_unavailable")
        self.vault = vault
        self.clock = clock

    @staticmethod
    def key_for(envelope_ref: str) -> str:
        value = str(envelope_ref or "").strip()
        if not value.startswith("ingress-envelope:"):
            raise OAuthIngressDeliveryError("oauth_delivery_envelope_ref_invalid")
        return _DELIVERY_PREFIX + _token(value)

    def _read(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self.vault.get(key, default=None)
        except Exception as exc:
            raise OAuthIngressDeliveryError("oauth_delivery_read_failed") from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise OAuthIngressDeliveryError("oauth_delivery_record_invalid") from exc
        if not isinstance(value, Mapping) or value.get("schema") != OAUTH_DELIVERY_SCHEMA:
            raise OAuthIngressDeliveryError("oauth_delivery_record_invalid")
        return dict(value)

    def _write(self, key: str, value: Mapping[str, Any]) -> None:
        try:
            self.vault.put(
                key,
                json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")),
                meta={
                    "authority": "integration_ingress",
                    "record": "oauth_delivery",
                    "status": str(value.get("status") or ""),
                },
            )
        except Exception as exc:
            raise OAuthIngressDeliveryError("oauth_delivery_write_failed") from exc

    def accept(self, envelope_value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            envelope = RoutedIngressEnvelope.from_mapping(dict(envelope_value))
        except Exception as exc:
            raise OAuthIngressDeliveryError("oauth_delivery_envelope_invalid") from exc
        value = envelope.to_dict()
        key = self.key_for(str(value["envelope_ref"]))
        now = float(self.clock())
        if _epoch(value["expires_at"]) <= now:
            raise OAuthIngressDeliveryError("oauth_delivery_expired")
        with _LOCK:
            existing = self._read(key)
            if existing is not None:
                if str(existing.get("envelope_digest") or "") != envelope.digest:
                    raise OAuthIngressDeliveryError("oauth_delivery_digest_conflict")
                return existing
            acknowledgement = IngressAcknowledgement.create(
                ack_ref="ingress-ack:" + _token(str(value["envelope_ref"])),
                envelope_ref=str(value["envelope_ref"]),
                attempt_ref=str(value["attempt_ref"]),
                endpoint_ref=str(value["endpoint_ref"]),
                audience=str(value["audience"]),
                accepted_at=_iso(now),
                status="accepted",
            )
            record = {
                "schema": OAUTH_DELIVERY_SCHEMA,
                "delivery_key": key,
                "envelope_ref": str(value["envelope_ref"]),
                "envelope_digest": envelope.digest,
                "attempt_ref": str(value["attempt_ref"]),
                "profile_ref": str(value["profile_ref"]),
                "expires_at": str(value["expires_at"]),
                "accepted_at": acknowledgement.to_dict()["accepted_at"],
                "updated_at": _iso(now),
                "status": "accepted",
                "attempts": 0,
                # The provider payload remains protected twice: by the routed
                # envelope and by the Core credential vault.
                "envelope": value,
                "acknowledgement": acknowledgement.to_dict(),
            }
            self._write(key, record)
            return record

    def claim(self, key: str) -> dict[str, Any] | None:
        with _LOCK:
            record = self._read(key)
            if record is None or record.get("status") in {"completed", "failed"}:
                return None
            now = float(self.clock())
            if _epoch(record.get("expires_at")) <= now:
                self._write(
                    key,
                    {
                        **record,
                        "status": "failed",
                        "error": "oauth_delivery_expired",
                        "updated_at": _iso(now),
                        "envelope": None,
                    },
                )
                return None
            claimed = {
                **record,
                "status": "processing",
                "attempts": int(record.get("attempts") or 0) + 1,
                "updated_at": _iso(now),
            }
            self._write(key, claimed)
            return claimed

    def complete(self, key: str, result: Mapping[str, Any]) -> None:
        with _LOCK:
            record = self._read(key)
            if record is None:
                raise OAuthIngressDeliveryError("oauth_delivery_record_missing")
            self._write(
                key,
                {
                    **record,
                    "status": "completed",
                    "updated_at": _iso(float(self.clock())),
                    "envelope": None,
                    "result": {
                        "provider_id": str(result.get("provider_id") or ""),
                        "account_id": str(result.get("account_id") or ""),
                        "status": str(result.get("status") or "connected"),
                    },
                    "error": None,
                },
            )

    def fail(self, key: str, error: str) -> None:
        with _LOCK:
            record = self._read(key)
            if record is None:
                return
            self._write(
                key,
                {
                    **record,
                    "status": "failed",
                    "updated_at": _iso(float(self.clock())),
                    "envelope": None,
                    "error": str(error or "oauth_delivery_failed")[:160],
                },
            )

    def pending_keys(self) -> list[str]:
        try:
            indexed = self.vault.list()
        except Exception as exc:
            raise OAuthIngressDeliveryError("oauth_delivery_list_failed") from exc
        keys: list[str] = []
        for item in indexed if isinstance(indexed, list) else ():
            key = str((item or {}).get("key") or "") if isinstance(item, Mapping) else ""
            if not key.startswith(_DELIVERY_PREFIX):
                continue
            record = self._read(key)
            if record is not None and record.get("status") in {"accepted", "processing"}:
                keys.append(key)
        return keys


__all__ = [
    "OAUTH_DELIVERY_SCHEMA",
    "OAuthIngressDeliveryError",
    "OAuthIngressDeliveryStore",
]
