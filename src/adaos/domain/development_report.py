from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence


DEVELOPMENT_REPORT_SCHEMA = "adaos.application.development_report.v1"
DEVELOPMENT_REPORT_ENVELOPE_SCHEMA = "adaos.application.development_report_envelope.v1"
DEVELOPMENT_REPORT_INTAKE_SCHEMA = "adaos.application.development_report_intake.v1"
DEVELOPMENT_REPORT_STATUS_EVENT_SCHEMA = "adaos.application.development_report_status_event.v1"
DEVELOPMENT_REPORT_ACK_SCHEMA = "adaos.application.development_report_ack.v1"
DEVELOPMENT_REPORT_RESYNC_SCHEMA = "adaos.application.development_report_resync.v1"
DEVELOPMENT_REPORT_APPEAL_SCHEMA = "adaos.application.development_report_appeal.v1"

ReportStatus = Literal[
    "draft", "queued", "delivered", "received", "triaged", "accepted",
    "declined", "duplicate", "planned", "prerelease_available", "released",
    "awaiting_local_verification", "verified", "still_reproduces",
]

PUBLIC_REPORT_STATES = (
    "draft", "queued", "delivered", "received", "triaged", "accepted",
    "declined", "duplicate", "planned", "prerelease_available", "released",
    "awaiting_local_verification", "verified", "still_reproduces",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class DevelopmentReportContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any, name: str, maximum: int) -> str:
    token = str(value or "").strip()
    if not token:
        raise DevelopmentReportContractError(f"{name} is required")
    if len(token) > maximum:
        raise DevelopmentReportContractError(f"{name} exceeds {maximum} characters")
    return token


def _id(value: Any, name: str) -> str:
    token = _text(value, name, 128).lower()
    if not _ID_RE.fullmatch(token):
        raise DevelopmentReportContractError(f"{name} must be a canonical identifier")
    return token


def _subnet(value: Any, name: str) -> str:
    token = _text(value, name, 167)
    if not token.startswith("subnet:") or not _ID_RE.fullmatch(token.split(":", 1)[1]):
        raise DevelopmentReportContractError(f"{name} must use subnet:<id>")
    return token


def _digest(value: Any, name: str) -> str:
    token = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(token):
        raise DevelopmentReportContractError(f"{name} must be a sha256 digest")
    return token


def _key_id(value: Any, name: str) -> str:
    token = str(value or "").strip().lower()
    if not _KEY_RE.fullmatch(token):
        raise DevelopmentReportContractError(f"{name} must be a sha256 key id")
    return token


def _timestamp(value: Any, name: str) -> str:
    token = _text(value, name, 80)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DevelopmentReportContractError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DevelopmentReportContractError(f"{name} must include a timezone")
    return token


def _revision(value: Any, *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise DevelopmentReportContractError("revision must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DevelopmentReportContractError("revision must be an integer") from exc
    if parsed < minimum:
        raise DevelopmentReportContractError(f"revision must be at least {minimum}")
    return parsed


def _objects(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise DevelopmentReportContractError(f"{name} must be an array")
    if any(not isinstance(item, Mapping) for item in value):
        raise DevelopmentReportContractError(f"{name} must contain objects")
    return tuple(dict(item) for item in value)


def _strict(
    value: Mapping[str, Any], *, schema: str, allowed: set[str], required: set[str], name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DevelopmentReportContractError(f"{name} must be an object")
    payload = dict(value)
    if payload.get("schema") != schema:
        raise DevelopmentReportContractError(f"unsupported {name} schema")
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown:
        raise DevelopmentReportContractError(f"{name} contains unsupported fields: {', '.join(sorted(unknown))}")
    if missing:
        raise DevelopmentReportContractError(f"{name} is missing fields: {', '.join(sorted(missing))}")
    return payload


@dataclass(frozen=True, slots=True)
class DevelopmentReport:
    report_id: str
    application_id: str
    publisher_ref: str
    reporter_subnet_ref: str
    reporter_key_id: str
    installed_release_digest: str
    installation_proof: Mapping[str, Any]
    idempotency_key: str
    summary: str
    details: str
    evidence: tuple[Mapping[str, Any], ...] = ()
    status: ReportStatus = "draft"
    revision: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _id(self.report_id, "report_id"))
        object.__setattr__(self, "application_id", _id(self.application_id, "application_id"))
        object.__setattr__(self, "publisher_ref", _subnet(self.publisher_ref, "publisher_ref"))
        object.__setattr__(self, "reporter_subnet_ref", _subnet(self.reporter_subnet_ref, "reporter_subnet_ref"))
        object.__setattr__(self, "reporter_key_id", _key_id(self.reporter_key_id, "reporter_key_id"))
        object.__setattr__(self, "installed_release_digest", _digest(self.installed_release_digest, "installed_release_digest"))
        if not isinstance(self.installation_proof, Mapping):
            raise DevelopmentReportContractError("installation_proof must be an object")
        proof = dict(self.installation_proof)
        required = {"installation_id", "application_id", "release_digest", "installation_revision"}
        if set(proof) != required:
            raise DevelopmentReportContractError("installation_proof fields are invalid")
        if _id(proof["application_id"], "installation_proof.application_id") != self.application_id:
            raise DevelopmentReportContractError("installation proof Application does not match")
        if _digest(proof["release_digest"], "installation_proof.release_digest") != self.installed_release_digest:
            raise DevelopmentReportContractError("installation proof release does not match")
        proof["installation_id"] = _text(proof["installation_id"], "installation_id", 180)
        proof["application_id"] = self.application_id
        proof["release_digest"] = self.installed_release_digest
        proof["installation_revision"] = _revision(proof["installation_revision"])
        object.__setattr__(self, "installation_proof", proof)
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, "idempotency_key", 180))
        object.__setattr__(self, "summary", _text(self.summary, "summary", 500))
        object.__setattr__(self, "details", _text(self.details, "details", 16000))
        object.__setattr__(self, "evidence", _objects(self.evidence, "evidence"))
        if self.status not in PUBLIC_REPORT_STATES:
            raise DevelopmentReportContractError("report status is invalid")
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DEVELOPMENT_REPORT_SCHEMA,
            "report_id": self.report_id,
            "application_id": self.application_id,
            "publisher_ref": self.publisher_ref,
            "reporter_subnet_ref": self.reporter_subnet_ref,
            "reporter_key_id": self.reporter_key_id,
            "installed_release_digest": self.installed_release_digest,
            "installation_proof": dict(self.installation_proof),
            "idempotency_key": self.idempotency_key,
            "summary": self.summary,
            "details": self.details,
            "evidence": [dict(item) for item in self.evidence],
            "status": self.status,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentReport":
        required = {
            "schema", "report_id", "application_id", "publisher_ref", "reporter_subnet_ref",
            "reporter_key_id", "installed_release_digest", "installation_proof", "idempotency_key",
            "summary", "details", "evidence", "status", "revision", "created_at", "updated_at",
        }
        payload = _strict(value, schema=DEVELOPMENT_REPORT_SCHEMA, allowed=required, required=required, name="DevelopmentReport")
        payload.pop("schema")
        payload["evidence"] = _objects(payload["evidence"], "evidence")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DevelopmentReportIntake:
    intake_id: str
    report_id: str
    application_id: str
    reporter_subnet_ref: str
    raw_payload_digest: str
    normalized_summary: str
    normalized_details: str
    redaction_findings: tuple[str, ...]
    model_classification: Mapping[str, Any] | None
    admission: Mapping[str, Any]
    status: str = "quarantined"
    revision: int = 1
    internal_ticket_refs: tuple[str, ...] = ()
    received_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "intake_id", _id(self.intake_id, "intake_id"))
        object.__setattr__(self, "report_id", _id(self.report_id, "report_id"))
        object.__setattr__(self, "application_id", _id(self.application_id, "application_id"))
        object.__setattr__(self, "reporter_subnet_ref", _subnet(self.reporter_subnet_ref, "reporter_subnet_ref"))
        object.__setattr__(self, "raw_payload_digest", _digest(self.raw_payload_digest, "raw_payload_digest"))
        object.__setattr__(self, "normalized_summary", _text(self.normalized_summary, "normalized_summary", 500))
        object.__setattr__(self, "normalized_details", _text(self.normalized_details, "normalized_details", 16000))
        object.__setattr__(self, "redaction_findings", tuple(sorted({_id(item, "redaction_finding") for item in self.redaction_findings})))
        if self.model_classification is not None and not isinstance(self.model_classification, Mapping):
            raise DevelopmentReportContractError("model_classification must be an object")
        if not isinstance(self.admission, Mapping):
            raise DevelopmentReportContractError("admission must be an object")
        if self.status not in {"quarantined", "triaged", "accepted", "declined", "duplicate"}:
            raise DevelopmentReportContractError("publisher intake status is invalid")
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "internal_ticket_refs", tuple(sorted({_text(item, "internal_ticket_ref", 180) for item in self.internal_ticket_refs})))
        object.__setattr__(self, "received_at", _timestamp(self.received_at, "received_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DEVELOPMENT_REPORT_INTAKE_SCHEMA, "intake_id": self.intake_id,
            "report_id": self.report_id, "application_id": self.application_id,
            "reporter_subnet_ref": self.reporter_subnet_ref,
            "raw_payload_digest": self.raw_payload_digest,
            "normalized_summary": self.normalized_summary,
            "normalized_details": self.normalized_details,
            "redaction_findings": list(self.redaction_findings),
            "model_classification": dict(self.model_classification) if self.model_classification is not None else None,
            "admission": dict(self.admission), "status": self.status,
            "revision": self.revision, "internal_ticket_refs": list(self.internal_ticket_refs),
            "received_at": self.received_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentReportIntake":
        required = {
            "schema", "intake_id", "report_id", "application_id", "reporter_subnet_ref",
            "raw_payload_digest", "normalized_summary", "normalized_details", "redaction_findings",
            "model_classification", "admission", "status", "revision", "internal_ticket_refs",
            "received_at", "updated_at",
        }
        payload = _strict(value, schema=DEVELOPMENT_REPORT_INTAKE_SCHEMA, allowed=required, required=required, name="DevelopmentReportIntake")
        payload.pop("schema")
        payload["redaction_findings"] = tuple(payload["redaction_findings"])
        payload["internal_ticket_refs"] = tuple(payload["internal_ticket_refs"])
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DevelopmentReportEnvelope:
    message_id: str
    message_kind: str
    sender_subnet_ref: str
    sender_key_id: str
    recipient_subnet_ref: str
    recipient_key_id: str
    source_zone: str
    destination_zone: str
    route_generation: int
    hop_limit: int
    ephemeral_public_key_b64: str
    nonce_b64: str
    ciphertext_b64: str
    signature_b64: str
    created_at: str
    expires_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _id(self.message_id, "message_id"))
        if self.message_kind not in {
            "report", "status", "verification", "resync", "resync_snapshot",
            "appeal", "appeal_response",
        }:
            raise DevelopmentReportContractError("message_kind is invalid")
        object.__setattr__(self, "sender_subnet_ref", _subnet(self.sender_subnet_ref, "sender_subnet_ref"))
        object.__setattr__(self, "sender_key_id", _key_id(self.sender_key_id, "sender_key_id"))
        object.__setattr__(self, "recipient_subnet_ref", _subnet(self.recipient_subnet_ref, "recipient_subnet_ref"))
        object.__setattr__(self, "recipient_key_id", _key_id(self.recipient_key_id, "recipient_key_id"))
        object.__setattr__(self, "source_zone", _id(self.source_zone, "source_zone"))
        object.__setattr__(self, "destination_zone", _id(self.destination_zone, "destination_zone"))
        object.__setattr__(self, "route_generation", _revision(self.route_generation))
        object.__setattr__(self, "hop_limit", _revision(self.hop_limit))
        for name in ("ephemeral_public_key_b64", "nonce_b64", "ciphertext_b64", "signature_b64"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 4_000_000))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))

    def routing_dict(self) -> dict[str, Any]:
        return {
            "schema": DEVELOPMENT_REPORT_ENVELOPE_SCHEMA,
            "message_id": self.message_id,
            "message_kind": self.message_kind,
            "sender_subnet_ref": self.sender_subnet_ref,
            "sender_key_id": self.sender_key_id,
            "recipient_subnet_ref": self.recipient_subnet_ref,
            "recipient_key_id": self.recipient_key_id,
            "source_zone": self.source_zone,
            "destination_zone": self.destination_zone,
            "route_generation": self.route_generation,
            "hop_limit": self.hop_limit,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            **self.routing_dict(),
            "ephemeral_public_key_b64": self.ephemeral_public_key_b64,
            "nonce_b64": self.nonce_b64,
            "ciphertext_b64": self.ciphertext_b64,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "signature_b64": self.signature_b64}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentReportEnvelope":
        required = {
            "schema", "message_id", "message_kind", "sender_subnet_ref", "sender_key_id",
            "recipient_subnet_ref", "recipient_key_id", "source_zone", "destination_zone",
            "route_generation", "hop_limit", "ephemeral_public_key_b64", "nonce_b64",
            "ciphertext_b64", "signature_b64", "created_at", "expires_at",
        }
        payload = _strict(value, schema=DEVELOPMENT_REPORT_ENVELOPE_SCHEMA, allowed=required, required=required, name="DevelopmentReportEnvelope")
        payload.pop("schema")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DevelopmentReportStatusEvent:
    event_id: str
    report_id: str
    application_id: str
    publisher_ref: str
    reporter_subnet_ref: str
    status: ReportStatus
    revision: int
    reason_code: str | None = None
    release_digest: str | None = None
    occurred_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _id(self.event_id, "event_id"))
        object.__setattr__(self, "report_id", _id(self.report_id, "report_id"))
        object.__setattr__(self, "application_id", _id(self.application_id, "application_id"))
        object.__setattr__(self, "publisher_ref", _subnet(self.publisher_ref, "publisher_ref"))
        object.__setattr__(self, "reporter_subnet_ref", _subnet(self.reporter_subnet_ref, "reporter_subnet_ref"))
        if self.status not in PUBLIC_REPORT_STATES:
            raise DevelopmentReportContractError("status event state is invalid")
        object.__setattr__(self, "revision", _revision(self.revision))
        if self.reason_code is not None:
            object.__setattr__(self, "reason_code", _id(self.reason_code, "reason_code"))
        if self.release_digest is not None:
            object.__setattr__(self, "release_digest", _digest(self.release_digest, "release_digest"))
        if self.status in {"prerelease_available", "released", "awaiting_local_verification", "verified", "still_reproduces"} and self.release_digest is None:
            raise DevelopmentReportContractError("release-bound status requires release_digest")
        object.__setattr__(self, "occurred_at", _timestamp(self.occurred_at, "occurred_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DEVELOPMENT_REPORT_STATUS_EVENT_SCHEMA,
            "event_id": self.event_id, "report_id": self.report_id,
            "application_id": self.application_id, "publisher_ref": self.publisher_ref,
            "reporter_subnet_ref": self.reporter_subnet_ref, "status": self.status,
            "revision": self.revision, "reason_code": self.reason_code,
            "release_digest": self.release_digest, "occurred_at": self.occurred_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentReportStatusEvent":
        required = {"schema", "event_id", "report_id", "application_id", "publisher_ref", "reporter_subnet_ref", "status", "revision", "reason_code", "release_digest", "occurred_at"}
        payload = _strict(value, schema=DEVELOPMENT_REPORT_STATUS_EVENT_SCHEMA, allowed=required, required=required, name="DevelopmentReportStatusEvent")
        payload.pop("schema")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DevelopmentReportAck:
    message_id: str
    recipient_subnet_ref: str
    disposition: str
    delivery_id: str
    accepted_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _id(self.message_id, "message_id"))
        object.__setattr__(self, "recipient_subnet_ref", _subnet(self.recipient_subnet_ref, "recipient_subnet_ref"))
        if self.disposition not in {"accepted", "duplicate", "rejected", "dead_letter"}:
            raise DevelopmentReportContractError("ACK disposition is invalid")
        object.__setattr__(self, "delivery_id", _id(self.delivery_id, "delivery_id"))
        object.__setattr__(self, "accepted_at", _timestamp(self.accepted_at, "accepted_at"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": DEVELOPMENT_REPORT_ACK_SCHEMA, "message_id": self.message_id, "recipient_subnet_ref": self.recipient_subnet_ref, "disposition": self.disposition, "delivery_id": self.delivery_id, "accepted_at": self.accepted_at}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentReportAck":
        required = {"schema", "message_id", "recipient_subnet_ref", "disposition", "delivery_id", "accepted_at"}
        payload = _strict(value, schema=DEVELOPMENT_REPORT_ACK_SCHEMA, allowed=required, required=required, name="DevelopmentReportAck")
        payload.pop("schema")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DevelopmentReportResync:
    request_id: str
    report_id: str
    requester_subnet_ref: str
    after_revision: int
    limit: int = 100
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _id(self.request_id, "request_id"))
        object.__setattr__(self, "report_id", _id(self.report_id, "report_id"))
        object.__setattr__(self, "requester_subnet_ref", _subnet(self.requester_subnet_ref, "requester_subnet_ref"))
        object.__setattr__(self, "after_revision", _revision(self.after_revision, minimum=0))
        object.__setattr__(self, "limit", _revision(self.limit))
        if self.limit > 200:
            raise DevelopmentReportContractError("resync limit exceeds 200")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": DEVELOPMENT_REPORT_RESYNC_SCHEMA, "request_id": self.request_id, "report_id": self.report_id, "requester_subnet_ref": self.requester_subnet_ref, "after_revision": self.after_revision, "limit": self.limit, "created_at": self.created_at}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentReportResync":
        required = {"schema", "request_id", "report_id", "requester_subnet_ref", "after_revision", "limit", "created_at"}
        payload = _strict(value, schema=DEVELOPMENT_REPORT_RESYNC_SCHEMA, allowed=required, required=required, name="DevelopmentReportResync")
        payload.pop("schema")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DevelopmentReportAppeal:
    appeal_id: str
    report_id: str
    application_id: str
    publisher_ref: str
    reporter_subnet_ref: str
    idempotency_key: str
    statement: str
    status: str = "submitted"
    resolution: str | None = None
    rationale: str | None = None
    revision: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "appeal_id", _id(self.appeal_id, "appeal_id"))
        object.__setattr__(self, "report_id", _id(self.report_id, "report_id"))
        object.__setattr__(self, "application_id", _id(self.application_id, "application_id"))
        object.__setattr__(self, "publisher_ref", _subnet(self.publisher_ref, "publisher_ref"))
        object.__setattr__(
            self, "reporter_subnet_ref",
            _subnet(self.reporter_subnet_ref, "reporter_subnet_ref"),
        )
        object.__setattr__(
            self, "idempotency_key", _text(self.idempotency_key, "idempotency_key", 180),
        )
        object.__setattr__(self, "statement", _text(self.statement, "statement", 4000))
        if self.status not in {"submitted", "received", "resolved"}:
            raise DevelopmentReportContractError("appeal status is invalid")
        if self.resolution is not None and self.resolution not in {
            "reopened", "corrected", "upheld",
        }:
            raise DevelopmentReportContractError("appeal resolution is invalid")
        if self.rationale is not None:
            object.__setattr__(self, "rationale", _text(self.rationale, "rationale", 4000))
        if self.status == "resolved":
            if self.resolution is None or self.rationale is None:
                raise DevelopmentReportContractError(
                    "resolved appeal requires resolution and rationale"
                )
        elif self.resolution is not None or self.rationale is not None:
            raise DevelopmentReportContractError(
                "unresolved appeal cannot include resolution or rationale"
            )
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DEVELOPMENT_REPORT_APPEAL_SCHEMA,
            "appeal_id": self.appeal_id,
            "report_id": self.report_id,
            "application_id": self.application_id,
            "publisher_ref": self.publisher_ref,
            "reporter_subnet_ref": self.reporter_subnet_ref,
            "idempotency_key": self.idempotency_key,
            "statement": self.statement,
            "status": self.status,
            "resolution": self.resolution,
            "rationale": self.rationale,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentReportAppeal":
        required = {
            "schema", "appeal_id", "report_id", "application_id", "publisher_ref",
            "reporter_subnet_ref", "idempotency_key", "statement", "status",
            "resolution", "rationale", "revision", "created_at", "updated_at",
        }
        payload = _strict(
            value,
            schema=DEVELOPMENT_REPORT_APPEAL_SCHEMA,
            allowed=required,
            required=required,
            name="DevelopmentReportAppeal",
        )
        payload.pop("schema")
        return cls(**payload)
