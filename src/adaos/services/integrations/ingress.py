"""Typed OAuth ingress broker shared by provider adapters.

The broker owns correlation, endpoint materialization, expiry and replay.
Provider adapters retain issuer-specific authorization URL construction, code
exchange and long-lived credentials.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
import time
from typing import Any, Callable, Mapping, Protocol

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from adaos.domain.integration_ingress import (
    CallbackAttempt,
    IngressAuditEvidence,
    IngressEndpointRevision,
    IngressProfile,
    RoutedIngressEnvelope,
)


GOOGLE_OAUTH_INGRESS_PROFILE_REF = (
    "ingress-profile:oauth.authorization-code.google@1"
)
GOOGLE_ISSUER_REF = "issuer:google"
LOCAL_DEVELOPMENT_ENVIRONMENT_REF = "environment-profile:local-development@1"
PUBLIC_CONNECTED_ENVIRONMENT_REF = "environment-profile:public-connected@1"
GOOGLE_OAUTH_CALLBACK_PROFILE_ID = "cbp_google_oauth_primary"
LOCAL_GOOGLE_OAUTH_CALLBACK_URI = (
    "http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback"
)
PUBLIC_GOOGLE_OAUTH_CALLBACK_URI = (
    "https://integrations.inimatic.com/v1/oauth/callback/"
    + GOOGLE_OAUTH_CALLBACK_PROFILE_ID
)
_ATTEMPT_SCHEMA = "adaos.integration.oauth_attempt_authority.v1"
_CONSUMED_SCHEMA = "adaos.integration.oauth_attempt_consumed.v1"
_KEY_SCHEMA = "adaos.integration.delivery_key.v1"
_TTL_SECONDS = 10 * 60


class IntegrationIngressError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(str(code or "integration_ingress_failed"))
        self.code = str(code or "integration_ingress_failed")
        self.retryable = bool(retryable)


class PublicAttemptRegistrar(Protocol):
    def register_attempt(self, projection: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(epoch: float) -> str:
    return (
        datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _state_hash(raw_state: str) -> str:
    return "sha256:" + hashlib.sha256(raw_state.encode("utf-8")).hexdigest()


def _opaque_ref(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:32]}"


def _b64url_decode(value: str) -> bytes:
    raw = _text(value)
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def google_oauth_ingress_profile() -> IngressProfile:
    return IngressProfile.create(
        profile_ref=GOOGLE_OAUTH_INGRESS_PROFILE_REF,
        ingress_class="oauth.authorization_response",
        protocol="oauth2.authorization_code",
        issuer_ref=GOOGLE_ISSUER_REF,
        request_schema_ref="schema:oauth.authorization-response@1",
        verification={"state": "required", "pkce": "S256", "issuer_binding": "required"},
        delivery={"mode": "immediate_single_use", "acknowledgement": "local_acceptance"},
        retention={"ttl_seconds": _TTL_SECONDS},
        response={"mode": "neutral_browser_result"},
        compatibility={"major": 1},
    )


def materialize_google_oauth_endpoint(
    environment_profile_ref: str,
) -> IngressEndpointRevision:
    profile = google_oauth_ingress_profile()
    public = environment_profile_ref == PUBLIC_CONNECTED_ENVIRONMENT_REF
    if environment_profile_ref not in {
        LOCAL_DEVELOPMENT_ENVIRONMENT_REF,
        PUBLIC_CONNECTED_ENVIRONMENT_REF,
    }:
        raise IntegrationIngressError("ingress_environment_not_supported")
    suffix = "public" if public else "loopback"
    return IngressEndpointRevision.create(
        endpoint_ref=f"ingress-endpoint:google-oauth-{suffix}",
        revision=1,
        profile_ref=profile.profile_ref,
        profile_digest=profile.digest,
        environment_profile_ref=environment_profile_ref,
        callback_uri=(
            PUBLIC_GOOGLE_OAUTH_CALLBACK_URI
            if public
            else LOCAL_GOOGLE_OAUTH_CALLBACK_URI
        ),
        callback_profile_id=GOOGLE_OAUTH_CALLBACK_PROFILE_ID,
        provider_registration_ref="provider-registration:google-oauth-primary",
        route_binding_ref=f"ingress-route:google-oauth-{suffix}",
        credential_authority_ref="credential-authority:core-local",
        generation=1,
        status="active",
        guarantees={
            "single_use": True,
            "local_acceptance": True,
            "encrypted_delivery": public,
        },
    )


class RootIngressRegistryClient:
    """Minimal authenticated Root registry client; never sends raw OAuth data."""

    def __init__(
        self,
        *,
        root_base_url: str,
        root_token: str,
        hub_id: str,
        transport: Any = requests,
    ) -> None:
        self.root_base_url = _text(root_base_url).rstrip("/")
        self.root_token = _text(root_token)
        self.hub_id = _text(hub_id)
        self.transport = transport

    def register_attempt(self, projection: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.root_base_url or not self.root_token or not self.hub_id:
            raise IntegrationIngressError("public_ingress_not_configured")
        payload = {**dict(projection), "hub_id": self.hub_id}
        try:
            response = self.transport.post(
                self.root_base_url + "/v1/integrations/ingress/oauth/attempts",
                json=payload,
                headers={"X-Root-Token": self.root_token, "Accept": "application/json"},
                timeout=(5.0, 15.0),
            )
        except Exception as exc:
            raise IntegrationIngressError(
                "public_ingress_registry_unavailable", retryable=True
            ) from exc
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise IntegrationIngressError("public_ingress_registration_rejected")
        try:
            value = response.json()
        except Exception as exc:
            raise IntegrationIngressError("public_ingress_registration_invalid") from exc
        if not isinstance(value, Mapping) or value.get("ok") is not True:
            raise IntegrationIngressError("public_ingress_registration_invalid")
        return dict(value)


class IntegrationIngressBroker:
    def __init__(
        self,
        *,
        vault: Any,
        environment_profile_ref: str = LOCAL_DEVELOPMENT_ENVIRONMENT_REF,
        clock: Callable[[], float] = time.time,
        public_registrar: PublicAttemptRegistrar | None = None,
    ) -> None:
        self.vault = vault
        self.environment_profile_ref = _text(environment_profile_ref)
        self.clock = clock
        self.public_registrar = public_registrar

    @property
    def profile(self) -> IngressProfile:
        return google_oauth_ingress_profile()

    @property
    def endpoint(self) -> IngressEndpointRevision:
        return materialize_google_oauth_endpoint(self.environment_profile_ref)

    @staticmethod
    def _pending_key(state_hash: str) -> str:
        return "integration:ingress:oauth-attempt:" + state_hash.removeprefix("sha256:")

    @staticmethod
    def _consumed_key(state_hash: str) -> str:
        return "integration:ingress:oauth-consumed:" + state_hash.removeprefix("sha256:")

    @staticmethod
    def _delivery_key() -> str:
        return "integration:ingress:delivery-key:primary"

    def _get_json(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self.vault.get(key, default=None)
        except Exception as exc:
            raise IntegrationIngressError("ingress_authority_read_failed") from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise IntegrationIngressError("ingress_authority_invalid") from exc
        if not isinstance(value, Mapping):
            raise IntegrationIngressError("ingress_authority_invalid")
        return dict(value)

    def _put_json(self, key: str, value: Mapping[str, Any]) -> None:
        try:
            self.vault.put(
                key,
                json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")),
                meta={"authority": "integration_ingress"},
            )
        except Exception as exc:
            raise IntegrationIngressError("ingress_authority_write_failed") from exc

    def _delete(self, key: str) -> None:
        try:
            self.vault.delete(key)
        except Exception as exc:
            raise IntegrationIngressError("ingress_authority_write_failed") from exc

    def _audit(self, attempt: CallbackAttempt, outcome: str) -> IngressAuditEvidence:
        value = attempt.to_dict()
        evidence = IngressAuditEvidence.create(
            evidence_ref=_opaque_ref(
                "ingress-evidence", attempt.attempt_ref, outcome, _iso(self.clock())
            ),
            attempt_ref=attempt.attempt_ref,
            profile_ref=str(value["profile_ref"]),
            endpoint_ref=str(value["endpoint_ref"]),
            state_hash=str(value["state_hash"]),
            outcome=outcome,
            observed_at=_iso(self.clock()),
            redaction={
                "raw_state": "omitted",
                "authorization_code": "omitted",
                "provider_payload": "omitted",
            },
        )
        self._put_json(
            "integration:ingress:audit:" + evidence.digest.removeprefix("sha256:"),
            evidence.to_dict(),
        )
        return evidence

    def _ensure_delivery_key(self) -> tuple[str, str]:
        stored = self._get_json(self._delivery_key())
        if stored is not None:
            if stored.get("schema") != _KEY_SCHEMA:
                raise IntegrationIngressError("ingress_delivery_key_invalid")
            return str(stored["private_key_pem"]), str(stored["public_key_pem"])
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        self._put_json(
            self._delivery_key(),
            {"schema": _KEY_SCHEMA, "private_key_pem": private_pem, "public_key_pem": public_pem},
        )
        return private_pem, public_pem

    def begin_authorization(
        self,
        *,
        provider_connection_ref: str,
        binding_instance_ref: str,
        application_ref: str,
        subject_ref: str,
        return_intent: str,
        correlation: Mapping[str, Any],
    ) -> dict[str, Any]:
        profile = self.profile
        endpoint = self.endpoint
        now = float(self.clock())
        state = secrets.token_urlsafe(32)
        state_hash = _state_hash(state)
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        attempt = CallbackAttempt.create(
            attempt_ref="callback-attempt:" + secrets.token_urlsafe(24),
            endpoint_ref=endpoint.endpoint_ref,
            endpoint_revision=int(endpoint.to_dict()["revision"]),
            endpoint_revision_digest=endpoint.digest,
            profile_ref=profile.profile_ref,
            issuer_ref=GOOGLE_ISSUER_REF,
            provider_connection_ref=provider_connection_ref,
            binding_instance_ref=binding_instance_ref,
            application_ref=application_ref,
            subject_ref=subject_ref,
            return_intent=return_intent,
            state_hash=state_hash,
            issued_at=_iso(now),
            expires_at=_iso(now + _TTL_SECONDS),
            max_deliveries=1,
            status="pending",
        )
        authority = {
            "schema": _ATTEMPT_SCHEMA,
            "attempt": attempt.to_dict(),
            "endpoint": endpoint.to_dict(),
            "correlation": dict(correlation),
            "code_verifier": verifier,
            "expires_at_epoch": now + _TTL_SECONDS,
        }
        self._put_json(self._pending_key(state_hash), authority)
        if self.environment_profile_ref == PUBLIC_CONNECTED_ENVIRONMENT_REF:
            if self.public_registrar is None:
                self._delete(self._pending_key(state_hash))
                raise IntegrationIngressError("public_ingress_not_configured")
            _private_pem, public_pem = self._ensure_delivery_key()
            projection = {
                "schema": "adaos.integration.root_oauth_attempt_projection.v1",
                "callback_profile_id": endpoint.to_dict()["callback_profile_id"],
                "profile_ref": profile.profile_ref,
                "issuer_ref": GOOGLE_ISSUER_REF,
                "endpoint_ref": endpoint.endpoint_ref,
                "endpoint_revision": endpoint.to_dict()["revision"],
                "endpoint_revision_digest": endpoint.digest,
                "route_binding_ref": endpoint.to_dict()["route_binding_ref"],
                "generation": endpoint.to_dict()["generation"],
                "attempt_ref": attempt.attempt_ref,
                "state_hash": state_hash,
                "expires_at": attempt.to_dict()["expires_at"],
                "delivery_public_key_pem": public_pem,
            }
            try:
                self.public_registrar.register_attempt(projection)
            except Exception:
                self._delete(self._pending_key(state_hash))
                raise
        self._audit(attempt, "issued")
        return {
            "state": state,
            "state_hash": state_hash,
            "code_verifier": verifier,
            "code_challenge": challenge,
            "attempt": attempt.to_dict(),
            "endpoint": endpoint.to_dict(),
            "redirect_uri": endpoint.callback_uri,
        }

    def consume_authorization(
        self,
        *,
        state: str,
        expected_profile_ref: str,
        expected_issuer_ref: str,
        expected_attempt_ref: str = "",
    ) -> dict[str, Any]:
        raw_state = _text(state)
        if not raw_state:
            raise IntegrationIngressError("oauth_state_missing")
        state_hash = _state_hash(raw_state)
        key = self._pending_key(state_hash)
        authority = self._get_json(key)
        if authority is None:
            if self._get_json(self._consumed_key(state_hash)) is not None:
                raise IntegrationIngressError("oauth_state_replayed")
            raise IntegrationIngressError("oauth_state_invalid")
        self._delete(key)
        if authority.get("schema") != _ATTEMPT_SCHEMA:
            raise IntegrationIngressError("oauth_state_invalid")
        try:
            attempt = CallbackAttempt.from_mapping(dict(authority["attempt"]))
            endpoint = IngressEndpointRevision.from_mapping(dict(authority["endpoint"]))
        except Exception as exc:
            raise IntegrationIngressError("oauth_state_invalid") from exc
        value = attempt.to_dict()
        self._put_json(
            self._consumed_key(state_hash),
            {
                "schema": _CONSUMED_SCHEMA,
                "attempt_ref": attempt.attempt_ref,
                "state_hash": state_hash,
                "consumed_at": _iso(self.clock()),
            },
        )
        if float(authority.get("expires_at_epoch") or 0.0) <= float(self.clock()):
            self._audit(attempt, "expired")
            raise IntegrationIngressError("oauth_state_expired")
        if value["profile_ref"] != expected_profile_ref:
            self._audit(attempt, "rejected")
            raise IntegrationIngressError("oauth_profile_mismatch")
        if expected_attempt_ref and value["attempt_ref"] != expected_attempt_ref:
            self._audit(attempt, "rejected")
            raise IntegrationIngressError("oauth_attempt_mismatch")
        if value["issuer_ref"] != expected_issuer_ref:
            self._audit(attempt, "rejected")
            raise IntegrationIngressError("oauth_issuer_mismatch")
        if value["endpoint_revision_digest"] != endpoint.digest:
            self._audit(attempt, "rejected")
            raise IntegrationIngressError("oauth_endpoint_revision_mismatch")
        return {
            "attempt": value,
            "endpoint": endpoint.to_dict(),
            "correlation": dict(authority.get("correlation") or {}),
            "code_verifier": str(authority.get("code_verifier") or ""),
        }

    def record_outcome(self, attempt: Mapping[str, Any], outcome: str) -> None:
        self._audit(CallbackAttempt.from_mapping(dict(attempt)), outcome)

    def decrypt_routed_envelope(
        self, envelope_value: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            envelope = RoutedIngressEnvelope.from_mapping(dict(envelope_value))
        except Exception as exc:
            raise IntegrationIngressError("ingress_envelope_invalid") from exc
        value = envelope.to_dict()
        endpoint = self.endpoint.to_dict()
        profile = self.profile.to_dict()
        if value["profile_ref"] != endpoint["profile_ref"]:
            raise IntegrationIngressError("ingress_envelope_profile_mismatch")
        if value["profile_ref"] != profile["profile_ref"]:
            raise IntegrationIngressError("ingress_envelope_profile_mismatch")
        if value["issuer_ref"] != profile["issuer_ref"]:
            raise IntegrationIngressError("ingress_envelope_issuer_mismatch")
        if value["audience"] != endpoint["route_binding_ref"]:
            raise IntegrationIngressError("ingress_envelope_audience_mismatch")
        if value["route_binding_ref"] != endpoint["route_binding_ref"]:
            raise IntegrationIngressError("ingress_envelope_route_mismatch")
        if value["endpoint_ref"] != endpoint["endpoint_ref"]:
            raise IntegrationIngressError("ingress_envelope_endpoint_mismatch")
        if int(value["endpoint_revision"]) != int(endpoint["revision"]):
            raise IntegrationIngressError("ingress_envelope_generation_mismatch")
        if int(value["generation"]) != int(endpoint["generation"]):
            raise IntegrationIngressError("ingress_envelope_generation_mismatch")
        expires_at = datetime.fromisoformat(str(value["expires_at"])).timestamp()
        if expires_at <= float(self.clock()):
            raise IntegrationIngressError("ingress_envelope_expired")
        private_pem, _public_pem = self._ensure_delivery_key()
        private_key = serialization.load_pem_private_key(
            private_pem.encode("ascii"), password=None
        )
        encrypted_key = _b64url_decode(str(value["encrypted_key"]))
        content_key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        ciphertext = _b64url_decode(str(value["ciphertext"])) + _b64url_decode(
            str(value["auth_tag"])
        )
        aad = (
            f"{value['attempt_ref']}\0{value['audience']}\0{value['generation']}"
        ).encode("utf-8")
        try:
            plaintext = AESGCM(content_key).decrypt(
                _b64url_decode(str(value["nonce"])), ciphertext, aad
            )
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise IntegrationIngressError("ingress_envelope_decryption_failed") from exc
        if not isinstance(payload, Mapping):
            raise IntegrationIngressError("ingress_envelope_payload_invalid")
        if _text(payload.get("attempt_ref")) != _text(value["attempt_ref"]):
            raise IntegrationIngressError("ingress_envelope_attempt_mismatch")
        return dict(payload)


def broker_from_context(
    ctx: Any, *, clock: Callable[[], float] = time.time
) -> IntegrationIngressBroker:
    vault = getattr(ctx, "credential_vault", None)
    if vault is None:
        raise IntegrationIngressError("credential_vault_unavailable")
    environment_profile_ref = _text(
        getattr(getattr(ctx, "config", None), "environment_profile_ref", None)
        or getattr(getattr(ctx, "settings", None), "environment_profile_ref", None)
        or os.getenv("ADAOS_ENVIRONMENT_PROFILE_REF")
        or LOCAL_DEVELOPMENT_ENVIRONMENT_REF
    )
    registrar: RootIngressRegistryClient | None = None
    if environment_profile_ref == PUBLIC_CONNECTED_ENVIRONMENT_REF:
        from adaos.services.personalization_runtime import current_subnet_id
        from adaos.services.zone_hosts import DEFAULT_PUBLIC_ROOT_BASE_URL

        settings = getattr(ctx, "settings", None)
        config = getattr(ctx, "config", None)
        root_settings = getattr(config, "root_settings", None)
        root_base = _text(
            getattr(settings, "api_base", None)
            or getattr(root_settings, "base_url", None)
            or DEFAULT_PUBLIC_ROOT_BASE_URL
        )
        root_token = _text(
            getattr(settings, "root_token", None)
            or getattr(root_settings, "token", None)
        )
        registrar = RootIngressRegistryClient(
            root_base_url=root_base,
            root_token=root_token,
            hub_id=current_subnet_id(ctx),
        )
    return IntegrationIngressBroker(
        vault=vault,
        environment_profile_ref=environment_profile_ref,
        clock=clock,
        public_registrar=registrar,
    )


__all__ = [
    "GOOGLE_ISSUER_REF",
    "GOOGLE_OAUTH_CALLBACK_PROFILE_ID",
    "GOOGLE_OAUTH_INGRESS_PROFILE_REF",
    "LOCAL_DEVELOPMENT_ENVIRONMENT_REF",
    "LOCAL_GOOGLE_OAUTH_CALLBACK_URI",
    "PUBLIC_CONNECTED_ENVIRONMENT_REF",
    "PUBLIC_GOOGLE_OAUTH_CALLBACK_URI",
    "IntegrationIngressBroker",
    "IntegrationIngressError",
    "RootIngressRegistryClient",
    "broker_from_context",
    "google_oauth_ingress_profile",
    "materialize_google_oauth_endpoint",
]
