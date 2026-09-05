from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from adaos.domain.artifact_release import canonical_json_bytes
from adaos.domain.development_report import DevelopmentReportAck, DevelopmentReportEnvelope
from adaos.services.applications.report_directory import SubnetKeyDirectoryClient
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class DevelopmentReportRelayError(ValueError):
    pass


class DevelopmentReportRelayBackpressure(DevelopmentReportRelayError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _key_id(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class DurableDevelopmentReportRelay:
    """Durable opaque mailbox. It never receives payload decryption keys."""

    def __init__(
        self,
        state_dir: Path,
        *,
        zone_id: str,
        directory: SubnetKeyDirectoryClient,
        now: Callable[[], datetime] = _now,
        max_messages: int = 10_000,
        max_per_recipient: int = 1_000,
        max_delivery_attempts: int = 12,
        dead_letter_retention: timedelta = timedelta(days=30),
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.zone_id = str(zone_id or "").strip().lower()
        if not self.zone_id:
            raise DevelopmentReportRelayError("zone_id is required")
        self.directory = directory
        self.now = now
        self.max_messages = int(max_messages)
        self.max_per_recipient = int(max_per_recipient)
        self.max_delivery_attempts = int(max_delivery_attempts)
        self.dead_letter_retention = dead_letter_retention

    @property
    def root(self) -> Path:
        path = self.state_dir / "root" / "development_report_relay" / self.zone_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def identity_path(self) -> Path:
        return self.root / "identity.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _read(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"schema": "adaos.application.development_report_relay.v1", "messages": {}, "dead_letters": {}, "receipts": {}, "trusted_peers": {}}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DevelopmentReportRelayError("relay state is unreadable") from exc
        if not isinstance(state, dict) or state.get("schema") != "adaos.application.development_report_relay.v1":
            raise DevelopmentReportRelayError("relay state is invalid")
        for field in ("messages", "dead_letters", "receipts", "trusted_peers"):
            if not isinstance(state.get(field), dict):
                raise DevelopmentReportRelayError(f"relay {field} state is invalid")
        return state

    def _private(self) -> Ed25519PrivateKey:
        if self.identity_path.is_file():
            try:
                payload = json.loads(self.identity_path.read_text(encoding="utf-8"))
                return Ed25519PrivateKey.from_private_bytes(base64.b64decode(payload["private_key_b64"], validate=True))
            except Exception as exc:
                raise DevelopmentReportRelayError("Root relay identity is invalid") from exc
        private = Ed25519PrivateKey.generate()
        raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        atomic_write_json(self.identity_path, {"schema": "adaos.root.relay_identity.v1", "zone_id": self.zone_id, "private_key_b64": base64.b64encode(raw).decode("ascii")})
        return private

    def public_identity(self) -> dict[str, str]:
        raw = self._private().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"zone_id": self.zone_id, "key_id": _key_id(raw), "public_key_b64": base64.b64encode(raw).decode("ascii")}

    def trust_peer(self, identity: Mapping[str, Any]) -> None:
        zone = str(identity.get("zone_id") or "").strip().lower()
        try:
            raw = base64.b64decode(str(identity.get("public_key_b64") or ""), validate=True)
            Ed25519PublicKey.from_public_bytes(raw)
        except Exception as exc:
            raise DevelopmentReportRelayError("peer Root identity is invalid") from exc
        if not zone or identity.get("key_id") != _key_id(raw):
            raise DevelopmentReportRelayError("peer Root identity binding is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            state["trusted_peers"][zone] = {"key_id": identity["key_id"], "public_key_b64": identity["public_key_b64"]}
            atomic_write_json(self.state_path, state)

    def _signed(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        return {**body, "signature_b64": base64.b64encode(self._private().sign(canonical_json_bytes(body))).decode("ascii")}

    @staticmethod
    def _verify_signed(value: Mapping[str, Any], peer: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(value)
        signature_b64 = payload.pop("signature_b64", None)
        try:
            public = Ed25519PublicKey.from_public_bytes(base64.b64decode(str(peer["public_key_b64"]), validate=True))
            public.verify(base64.b64decode(str(signature_b64), validate=True), canonical_json_bytes(payload))
        except (KeyError, ValueError, InvalidSignature) as exc:
            raise DevelopmentReportRelayError("Root-to-Root signature is invalid") from exc
        return payload

    def _active_count(self, state: Mapping[str, Any], recipient: str | None = None) -> int:
        values = state["messages"].values()
        if recipient is not None:
            values = (item for item in values if item["recipient_subnet_ref"] == recipient)
        return sum(1 for item in values if item.get("status") in {"queued", "delivering"})

    def _verify_envelope(self, envelope: DevelopmentReportEnvelope, *, allow_retiring: bool) -> None:
        if self.directory.home_zone(envelope.sender_subnet_ref) != envelope.source_zone:
            raise DevelopmentReportRelayError("relay source conflicts with signed directory")
        sender = self.directory.key(
            envelope.sender_subnet_ref, envelope.sender_key_id,
            "message_signing", allow_retiring=allow_retiring,
        )
        self.directory.key(
            envelope.recipient_subnet_ref, envelope.recipient_key_id,
            "message_encryption", allow_retiring=allow_retiring,
        )
        try:
            public = Ed25519PublicKey.from_public_bytes(base64.b64decode(sender.public_key_b64, validate=True))
            public.verify(base64.b64decode(envelope.signature_b64, validate=True), canonical_json_bytes(envelope.unsigned_dict()))
        except (ValueError, InvalidSignature) as exc:
            raise DevelopmentReportRelayError("relay envelope signature is invalid") from exc
        created = _parse(envelope.created_at)
        expires = _parse(envelope.expires_at)
        if expires <= created or expires - created > timedelta(days=30):
            raise DevelopmentReportRelayError("relay envelope retention window is invalid")

    def _admit(self, state: dict[str, Any], envelope: DevelopmentReportEnvelope, *, hop_count: int) -> dict[str, Any]:
        existing = state["messages"].get(envelope.message_id) or state["receipts"].get(envelope.message_id) or state["dead_letters"].get(envelope.message_id)
        if existing is not None:
            existing_digest = existing.get("envelope_digest")
            digest = f"sha256:{hashlib.sha256(canonical_json_bytes(envelope.to_dict())).hexdigest()}"
            if existing_digest != digest:
                raise DevelopmentReportRelayError("relay message identity collision")
            return {"accepted": True, "duplicate": True, "message_id": envelope.message_id}
        if self.now() > _parse(envelope.expires_at):
            raise DevelopmentReportRelayError("relay envelope has expired")
        if hop_count > envelope.hop_limit:
            raise DevelopmentReportRelayError("relay envelope hop limit exceeded")
        if self.directory.home_zone(envelope.recipient_subnet_ref) != envelope.destination_zone:
            raise DevelopmentReportRelayError("relay destination conflicts with signed directory")
        if envelope.route_generation > self.directory.high_water_generation:
            raise DevelopmentReportRelayError("relay directory generation is stale")
        if self._active_count(state) >= self.max_messages:
            raise DevelopmentReportRelayBackpressure("Root relay global mailbox limit reached")
        if self._active_count(state, envelope.recipient_subnet_ref) >= self.max_per_recipient:
            raise DevelopmentReportRelayBackpressure("Root relay recipient mailbox limit reached")
        digest = f"sha256:{hashlib.sha256(canonical_json_bytes(envelope.to_dict())).hexdigest()}"
        state["messages"][envelope.message_id] = {
            "message_id": envelope.message_id,
            "recipient_subnet_ref": envelope.recipient_subnet_ref,
            "destination_zone": envelope.destination_zone,
            "envelope": envelope.to_dict(),
            "envelope_digest": digest,
            "status": "queued",
            "attempts": 0,
            "hop_count": hop_count,
            "accepted_at": _iso(self.now()),
            "last_delivery_at": None,
            "delivery_id": None,
        }
        return {"accepted": True, "duplicate": False, "message_id": envelope.message_id}

    def enqueue(self, envelope: DevelopmentReportEnvelope | Mapping[str, Any]) -> dict[str, Any]:
        value = envelope if isinstance(envelope, DevelopmentReportEnvelope) else DevelopmentReportEnvelope.from_mapping(envelope)
        if value.source_zone != self.zone_id:
            raise DevelopmentReportRelayError("sender must enqueue through its home-zone Root")
        self._verify_envelope(value, allow_retiring=False)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            result = self._admit(state, value, hop_count=0)
            atomic_write_json(self.state_path, state)
            return result

    def poll(self, recipient_subnet_ref: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if self.directory.home_zone(recipient_subnet_ref) != self.zone_id:
            raise DevelopmentReportRelayError("recipient does not belong to this Root zone")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            self._sweep_state(state)
            selected: list[dict[str, Any]] = []
            for item in sorted(state["messages"].values(), key=lambda value: (value["accepted_at"], value["message_id"])):
                if item["recipient_subnet_ref"] != recipient_subnet_ref or item["status"] not in {"queued", "delivering"}:
                    continue
                if int(item["attempts"]) >= self.max_delivery_attempts:
                    self._dead_letter(state, item["message_id"], "delivery_attempts_exhausted")
                    continue
                item["attempts"] = int(item["attempts"]) + 1
                item["status"] = "delivering"
                item["last_delivery_at"] = _iso(self.now())
                item["delivery_id"] = f"delivery.{secrets.token_hex(12)}"
                selected.append({"delivery_id": item["delivery_id"], "attempt": item["attempts"], "envelope": dict(item["envelope"])})
                if len(selected) >= max(1, min(int(limit), 100)):
                    break
            atomic_write_json(self.state_path, state)
            return selected

    def acknowledge(self, ack: DevelopmentReportAck | Mapping[str, Any]) -> dict[str, Any]:
        value = ack if isinstance(ack, DevelopmentReportAck) else DevelopmentReportAck.from_mapping(ack)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            receipt = state["receipts"].get(value.message_id)
            if receipt is not None:
                if receipt.get("delivery_id") != value.delivery_id:
                    raise DevelopmentReportRelayError("ACK conflicts with durable receipt")
                return dict(receipt)
            item = state["messages"].get(value.message_id)
            if item is None or item.get("recipient_subnet_ref") != value.recipient_subnet_ref:
                raise DevelopmentReportRelayError("ACK message is unknown")
            if item.get("delivery_id") != value.delivery_id:
                raise DevelopmentReportRelayError("ACK delivery identity is stale")
            receipt = {
                **value.to_dict(), "envelope_digest": item["envelope_digest"],
                "ciphertext_deleted_at": _iso(self.now()), "attempts": item["attempts"],
            }
            state["receipts"][value.message_id] = receipt
            state["messages"].pop(value.message_id, None)
            atomic_write_json(self.state_path, state)
            return dict(receipt)

    def _dead_letter(self, state: dict[str, Any], message_id: str, reason: str) -> None:
        item = state["messages"].pop(message_id)
        state["dead_letters"][message_id] = {**item, "status": "dead_letter", "reason": reason, "dead_lettered_at": _iso(self.now())}

    def _sweep_state(self, state: dict[str, Any]) -> None:
        for message_id, item in list(state["messages"].items()):
            if self.now() > _parse(item["envelope"]["expires_at"]):
                self._dead_letter(state, message_id, "ttl_expired")
        for item in state["dead_letters"].values():
            if item.get("envelope") is not None and self.now() > _parse(item["dead_lettered_at"]) + self.dead_letter_retention:
                item.pop("envelope", None)
                item["ciphertext_deleted_at"] = _iso(self.now())

    def sweep(self) -> dict[str, int]:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            self._sweep_state(state)
            atomic_write_json(self.state_path, state)
            return {"queued": self._active_count(state), "dead_letters": len(state["dead_letters"]), "receipts": len(state["receipts"])}

    def forward(self, message_id: str, destination: "DurableDevelopmentReportRelay") -> dict[str, Any]:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            item = state["messages"].get(message_id)
            if item is None:
                receipt = state["receipts"].get(message_id)
                if receipt is not None and receipt.get("disposition") == "forwarded":
                    return dict(receipt)
                raise DevelopmentReportRelayError("forward message is unknown")
            if item["destination_zone"] == self.zone_id or destination.zone_id != item["destination_zone"]:
                raise DevelopmentReportRelayError("Root-to-Root destination is invalid")
            peer = state["trusted_peers"].get(destination.zone_id)
            if peer is None or peer.get("key_id") != destination.public_identity()["key_id"]:
                raise DevelopmentReportRelayError("destination Root is not trusted")
            offer = self._signed({
                "schema": "adaos.root.relay_forward_offer.v1", "message_id": message_id,
                "envelope_digest": item["envelope_digest"], "source_zone": self.zone_id,
                "destination_zone": destination.zone_id, "hop_count": int(item["hop_count"]) + 1,
                "offered_at": _iso(self.now()),
            })
            destination_receipt = destination.accept_forward(item["envelope"], offer=offer, source_identity=self.public_identity())
            verified = self._verify_signed(destination_receipt, peer)
            if verified.get("message_id") != message_id or verified.get("envelope_digest") != item["envelope_digest"] or not verified.get("durably_accepted"):
                raise DevelopmentReportRelayError("destination Root receipt does not match transfer")
            receipt = {**verified, "schema": "adaos.root.relay_forward_receipt.local.v1", "disposition": "forwarded", "ciphertext_deleted_at": _iso(self.now())}
            state["receipts"][message_id] = receipt
            state["messages"].pop(message_id, None)
            atomic_write_json(self.state_path, state)
            return receipt

    def accept_forward(self, envelope: Mapping[str, Any], *, offer: Mapping[str, Any], source_identity: Mapping[str, Any]) -> dict[str, Any]:
        value = DevelopmentReportEnvelope.from_mapping(envelope)
        if value.destination_zone != self.zone_id:
            raise DevelopmentReportRelayError("forwarded envelope belongs to another zone")
        self._verify_envelope(value, allow_retiring=True)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            source_zone = str(source_identity.get("zone_id") or "")
            peer = state["trusted_peers"].get(source_zone)
            if peer is None or peer.get("key_id") != source_identity.get("key_id"):
                raise DevelopmentReportRelayError("source Root is not trusted")
            verified_offer = self._verify_signed(offer, peer)
            digest = f"sha256:{hashlib.sha256(canonical_json_bytes(value.to_dict())).hexdigest()}"
            if verified_offer.get("message_id") != value.message_id or verified_offer.get("envelope_digest") != digest or verified_offer.get("destination_zone") != self.zone_id:
                raise DevelopmentReportRelayError("Root forwarding offer does not match envelope")
            result = self._admit(state, value, hop_count=int(verified_offer.get("hop_count") or 0))
            atomic_write_json(self.state_path, state)
        return self._signed({
            "schema": "adaos.root.relay_forward_receipt.v1", "message_id": value.message_id,
            "envelope_digest": digest, "source_zone": source_zone, "destination_zone": self.zone_id,
            "durably_accepted": bool(result["accepted"]), "duplicate": bool(result["duplicate"]),
            "accepted_at": _iso(self.now()),
        })
