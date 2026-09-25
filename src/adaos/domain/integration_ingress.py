"""Canonical contracts for typed external integration ingress.

Only :class:`IngressProfile` is portable. Endpoint revisions, attempts,
encrypted envelopes and acknowledgements are local/operational authority and
must never be packaged as Application semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

from adaos.domain.capability_binding_state import CanonicalRecord


INGRESS_PROFILE_SCHEMA = "adaos.integration.ingress_profile.v1"
INGRESS_ENDPOINT_REVISION_SCHEMA = "adaos.integration.ingress_endpoint_revision.v1"
CALLBACK_ATTEMPT_SCHEMA = "adaos.integration.callback_attempt.v1"
ROUTED_INGRESS_ENVELOPE_SCHEMA = "adaos.integration.routed_envelope.v1"
INGRESS_ACKNOWLEDGEMENT_SCHEMA = "adaos.integration.ingress_acknowledgement.v1"
INGRESS_AUDIT_EVIDENCE_SCHEMA = "adaos.integration.ingress_audit_evidence.v1"


@dataclass(frozen=True, slots=True)
class IngressProfile(CanonicalRecord):
    SCHEMA: ClassVar[str] = INGRESS_PROFILE_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "profile_digest"

    @classmethod
    def create(
        cls,
        *,
        profile_ref: str,
        ingress_class: str,
        protocol: str,
        issuer_ref: str,
        request_schema_ref: str,
        verification: Mapping[str, Any],
        delivery: Mapping[str, Any],
        retention: Mapping[str, Any],
        response: Mapping[str, Any],
        compatibility: Mapping[str, Any],
    ) -> "IngressProfile":
        return cls._create(
            {
                "profile_ref": profile_ref,
                "ingress_class": ingress_class,
                "protocol": protocol,
                "issuer_ref": issuer_ref,
                "request_schema_ref": request_schema_ref,
                "verification": dict(verification),
                "delivery": dict(delivery),
                "retention": dict(retention),
                "response": dict(response),
                "compatibility": dict(compatibility),
            }
        )

    @property
    def profile_ref(self) -> str:
        return str(self._payload["profile_ref"])


@dataclass(frozen=True, slots=True)
class IngressEndpointRevision(CanonicalRecord):
    SCHEMA: ClassVar[str] = INGRESS_ENDPOINT_REVISION_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "revision_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(cls, **values: Any) -> "IngressEndpointRevision":
        return cls._create(values)

    @property
    def endpoint_ref(self) -> str:
        return str(self._payload["endpoint_ref"])

    @property
    def callback_uri(self) -> str:
        return str(self._payload["callback_uri"])


@dataclass(frozen=True, slots=True)
class CallbackAttempt(CanonicalRecord):
    SCHEMA: ClassVar[str] = CALLBACK_ATTEMPT_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "attempt_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(cls, **values: Any) -> "CallbackAttempt":
        return cls._create(values)

    @property
    def attempt_ref(self) -> str:
        return str(self._payload["attempt_ref"])


@dataclass(frozen=True, slots=True)
class RoutedIngressEnvelope(CanonicalRecord):
    SCHEMA: ClassVar[str] = ROUTED_INGRESS_ENVELOPE_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "envelope_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(cls, **values: Any) -> "RoutedIngressEnvelope":
        return cls._create(values)


@dataclass(frozen=True, slots=True)
class IngressAcknowledgement(CanonicalRecord):
    SCHEMA: ClassVar[str] = INGRESS_ACKNOWLEDGEMENT_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "ack_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(cls, **values: Any) -> "IngressAcknowledgement":
        return cls._create(values)


@dataclass(frozen=True, slots=True)
class IngressAuditEvidence(CanonicalRecord):
    SCHEMA: ClassVar[str] = INGRESS_AUDIT_EVIDENCE_SCHEMA
    DIGEST_FIELD: ClassVar[str] = "evidence_digest"
    PORTABLE: ClassVar[bool] = False

    @classmethod
    def create(cls, **values: Any) -> "IngressAuditEvidence":
        return cls._create(values)


__all__ = [
    "CALLBACK_ATTEMPT_SCHEMA",
    "INGRESS_ACKNOWLEDGEMENT_SCHEMA",
    "INGRESS_AUDIT_EVIDENCE_SCHEMA",
    "INGRESS_ENDPOINT_REVISION_SCHEMA",
    "INGRESS_PROFILE_SCHEMA",
    "ROUTED_INGRESS_ENVELOPE_SCHEMA",
    "CallbackAttempt",
    "IngressAcknowledgement",
    "IngressAuditEvidence",
    "IngressEndpointRevision",
    "IngressProfile",
    "RoutedIngressEnvelope",
]
