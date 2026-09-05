from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization

from adaos.domain.artifact_release import canonical_json_bytes
from adaos.domain.development_report import DevelopmentReportEnvelope, utc_now
from adaos.services.applications.report_directory import SubnetKeyDirectoryClient
from adaos.services.applications.report_keys import SubnetPurposeKeyStore


class DevelopmentReportCryptoError(ValueError):
    pass


_KDF_INFO = b"adaos-development-report-envelope-v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _derive(shared_secret: bytes, routing: Mapping[str, Any]) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=_KDF_INFO + canonical_json_bytes(dict(routing)),
    ).derive(shared_secret)


class DevelopmentReportEnvelopeCrypto:
    """X25519/HKDF/AES-GCM profile with an independent Ed25519 envelope signature."""

    def __init__(
        self,
        *,
        key_store: SubnetPurposeKeyStore,
        directory: SubnetKeyDirectoryClient,
        now: Callable[[], datetime] = _now,
        max_plaintext_bytes: int = 2_500_000,
    ) -> None:
        self.key_store = key_store
        self.directory = directory
        self.now = now
        self.max_plaintext_bytes = int(max_plaintext_bytes)

    def seal(
        self,
        payload: Mapping[str, Any],
        *,
        message_kind: str,
        sender_subnet_ref: str,
        recipient_subnet_ref: str,
        ttl: timedelta = timedelta(days=14),
        message_id: str | None = None,
        hop_limit: int = 4,
    ) -> DevelopmentReportEnvelope:
        if ttl.total_seconds() <= 0:
            raise DevelopmentReportCryptoError("envelope TTL must be positive")
        plaintext = canonical_json_bytes(dict(payload))
        if len(plaintext) > self.max_plaintext_bytes:
            raise DevelopmentReportCryptoError("envelope plaintext exceeds byte limit")
        signing_record = self.key_store.active_key(sender_subnet_ref, "message_signing")
        recipient_record = self.directory.active_key(recipient_subnet_ref, "message_encryption")
        signing_private = self.key_store.private_key(signing_record.key_id)
        if not isinstance(signing_private, Ed25519PrivateKey):
            raise DevelopmentReportCryptoError("sender signing key type is invalid")
        try:
            recipient_public = X25519PublicKey.from_public_bytes(base64.b64decode(recipient_record.public_key_b64, validate=True))
        except Exception as exc:
            raise DevelopmentReportCryptoError("recipient encryption key is invalid") from exc
        ephemeral = X25519PrivateKey.generate()
        created = self.now().astimezone(timezone.utc).replace(microsecond=0)
        routing = {
            "schema": "adaos.application.development_report_envelope.v1",
            "message_id": str(message_id or f"msg.{secrets.token_hex(16)}").lower(),
            "message_kind": message_kind,
            "sender_subnet_ref": sender_subnet_ref,
            "sender_key_id": signing_record.key_id,
            "recipient_subnet_ref": recipient_subnet_ref,
            "recipient_key_id": recipient_record.key_id,
            "source_zone": self.directory.home_zone(sender_subnet_ref),
            "destination_zone": self.directory.home_zone(recipient_subnet_ref),
            "route_generation": self.directory.high_water_generation,
            "hop_limit": hop_limit,
            "created_at": created.isoformat(),
            "expires_at": (created + ttl).isoformat(),
        }
        nonce = os.urandom(12)
        key = _derive(ephemeral.exchange(recipient_public), routing)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, canonical_json_bytes(routing))
        unsigned = {
            **routing,
            "ephemeral_public_key_b64": base64.b64encode(ephemeral.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii"),
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        }
        signature = signing_private.sign(canonical_json_bytes(unsigned))
        return DevelopmentReportEnvelope.from_mapping({**unsigned, "signature_b64": base64.b64encode(signature).decode("ascii")})

    def open(self, envelope: DevelopmentReportEnvelope | Mapping[str, Any], *, recipient_subnet_ref: str) -> dict[str, Any]:
        value = envelope if isinstance(envelope, DevelopmentReportEnvelope) else DevelopmentReportEnvelope.from_mapping(envelope)
        if value.recipient_subnet_ref != recipient_subnet_ref:
            raise DevelopmentReportCryptoError("envelope recipient does not match local subnet")
        if self.now() > _parse(value.expires_at):
            raise DevelopmentReportCryptoError("envelope has expired")
        sender_record = self.directory.key(value.sender_subnet_ref, value.sender_key_id, "message_signing", allow_retiring=True)
        recipient_record = self.key_store.get_public(value.recipient_key_id, allow_retiring=True)
        if recipient_record.subnet_ref != recipient_subnet_ref or recipient_record.purpose != "message_encryption":
            raise DevelopmentReportCryptoError("recipient key binding is invalid")
        try:
            sender_public = Ed25519PublicKey.from_public_bytes(base64.b64decode(sender_record.public_key_b64, validate=True))
            sender_public.verify(base64.b64decode(value.signature_b64, validate=True), canonical_json_bytes(value.unsigned_dict()))
            ephemeral_public = X25519PublicKey.from_public_bytes(base64.b64decode(value.ephemeral_public_key_b64, validate=True))
            nonce = base64.b64decode(value.nonce_b64, validate=True)
            ciphertext = base64.b64decode(value.ciphertext_b64, validate=True)
        except (ValueError, InvalidSignature) as exc:
            raise DevelopmentReportCryptoError("envelope signature or encoding is invalid") from exc
        if len(ciphertext) > self.max_plaintext_bytes + 16:
            raise DevelopmentReportCryptoError("envelope ciphertext exceeds byte limit")
        recipient_private = self.key_store.private_key(value.recipient_key_id)
        if not isinstance(recipient_private, X25519PrivateKey):
            raise DevelopmentReportCryptoError("recipient decryption key type is invalid")
        try:
            key = _derive(recipient_private.exchange(ephemeral_public), value.routing_dict())
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, canonical_json_bytes(value.routing_dict()))
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise DevelopmentReportCryptoError("envelope decryption failed") from exc
        if not isinstance(payload, dict):
            raise DevelopmentReportCryptoError("envelope payload must be an object")
        return payload

