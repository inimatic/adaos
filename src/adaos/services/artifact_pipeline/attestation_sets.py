from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.attestations import (
    PACKAGE_PROVENANCE_PREDICATE,
    RELEASE_PROVENANCE_PREDICATE,
    ArtifactAttestation,
    package_provenance_digest,
    release_provenance_digest,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan


RELEASE_ATTESTATION_SET_SCHEMA = "adaos.artifact.release_attestation_set.v1"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_SUBJECT_KINDS = {"package", "release"}


class ReleaseAttestationSetError(ValueError):
    pass


def _digest(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(token):
        raise ReleaseAttestationSetError(
            f"{field} must be sha256:<64 lowercase hex characters>"
        )
    return token


def _text(value: Any, *, field: str, maximum: int = 512) -> str:
    token = str(value or "").strip()
    if not token or len(token) > maximum:
        raise ReleaseAttestationSetError(f"{field} must contain 1..{maximum} characters")
    return token


@dataclass(frozen=True, slots=True)
class ArtifactAttestationRef:
    subject_kind: str
    subject_digest: str
    project_id: str
    attestation_digest: str
    issuer: str
    key_id: str
    predicate_type: str
    predicate_digest: str

    def __post_init__(self) -> None:
        if self.subject_kind not in _SUBJECT_KINDS:
            raise ReleaseAttestationSetError("subject_kind must be package or release")
        object.__setattr__(
            self,
            "subject_digest",
            _digest(self.subject_digest, field="subject_digest"),
        )
        project_id = _text(self.project_id, field="project_id", maximum=255)
        if not _PROJECT_ID_RE.fullmatch(project_id):
            raise ReleaseAttestationSetError("project_id is invalid")
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(
            self,
            "attestation_digest",
            _digest(self.attestation_digest, field="attestation_digest"),
        )
        object.__setattr__(self, "issuer", _text(self.issuer, field="issuer"))
        object.__setattr__(self, "key_id", _digest(self.key_id, field="key_id"))
        object.__setattr__(
            self,
            "predicate_type",
            _text(self.predicate_type, field="predicate_type"),
        )
        object.__setattr__(
            self,
            "predicate_digest",
            _digest(self.predicate_digest, field="predicate_digest"),
        )

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.subject_kind,
            self.subject_digest,
            self.key_id,
            self.attestation_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject_digest": self.subject_digest,
            "project_id": self.project_id,
            "attestation_digest": self.attestation_digest,
            "issuer": self.issuer,
            "key_id": self.key_id,
            "predicate_type": self.predicate_type,
            "predicate_digest": self.predicate_digest,
        }

    @classmethod
    def from_attestation(cls, attestation: ArtifactAttestation) -> "ArtifactAttestationRef":
        sealed = attestation.seal()
        return cls(
            subject_kind=sealed.subject_kind,
            subject_digest=sealed.subject_digest,
            project_id=sealed.project_id,
            attestation_digest=str(sealed.attestation_digest),
            issuer=sealed.issuer,
            key_id=sealed.key_id,
            predicate_type=sealed.predicate_type,
            predicate_digest=sealed.predicate_digest,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ArtifactAttestationRef":
        fields = {
            "subject_kind",
            "subject_digest",
            "project_id",
            "attestation_digest",
            "issuer",
            "key_id",
            "predicate_type",
            "predicate_digest",
        }
        if set(value) != fields:
            raise ReleaseAttestationSetError("attestation reference has invalid fields")
        return cls(**{field: value.get(field) for field in fields})


@dataclass(frozen=True, slots=True)
class ReleaseAttestationSet:
    project_id: str
    release_digest: str
    attestations: tuple[ArtifactAttestationRef, ...]
    attestation_set_digest: str | None = None

    def __post_init__(self) -> None:
        project_id = _text(self.project_id, field="project_id", maximum=255)
        if not _PROJECT_ID_RE.fullmatch(project_id):
            raise ReleaseAttestationSetError("project_id is invalid")
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(
            self,
            "release_digest",
            _digest(self.release_digest, field="release_digest"),
        )
        if not self.attestations:
            raise ReleaseAttestationSetError("release attestation set must not be empty")
        if len(self.attestations) > 64:
            raise ReleaseAttestationSetError(
                "release attestation set must contain at most 64 references"
            )
        if any(not isinstance(item, ArtifactAttestationRef) for item in self.attestations):
            raise ReleaseAttestationSetError("attestations must contain references")
        ordered = tuple(sorted(self.attestations, key=lambda item: item.identity))
        if len({item.identity for item in ordered}) != len(ordered):
            raise ReleaseAttestationSetError("release attestation set contains duplicate references")
        if any(item.project_id != project_id for item in ordered):
            raise ReleaseAttestationSetError("attestation reference belongs to another project")
        object.__setattr__(self, "attestations", ordered)
        if self.attestation_set_digest is not None:
            object.__setattr__(
                self,
                "attestation_set_digest",
                _digest(self.attestation_set_digest, field="attestation_set_digest"),
            )

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": RELEASE_ATTESTATION_SET_SCHEMA,
            "project_id": self.project_id,
            "release_digest": self.release_digest,
            "attestations": [item.to_dict() for item in self.attestations],
        }

    def computed_digest(self) -> str:
        return canonical_payload_digest(self.unsigned_dict())

    def seal(self) -> "ReleaseAttestationSet":
        digest = self.computed_digest()
        if self.attestation_set_digest is not None and self.attestation_set_digest != digest:
            raise ReleaseAttestationSetError(
                "attestation_set_digest does not match release attestation set content"
            )
        return replace(self, attestation_set_digest=digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.unsigned_dict(),
            "attestation_set_digest": self.attestation_set_digest or self.computed_digest(),
        }

    def validate_plan(self, plan: ReleasePlan) -> "ReleaseAttestationSet":
        try:
            validated = ReleasePlan.from_mapping(
                {"schema": "adaos.artifact.release_plan.v1", **plan.explain()}
            )
        except Exception as exc:
            raise ReleaseAttestationSetError(f"release plan is invalid: {exc}") from exc
        release = validated.release.seal()
        if release.project_id != self.project_id or release.release_digest != self.release_digest:
            raise ReleaseAttestationSetError("attestation set belongs to another release")
        expected: dict[tuple[str, str], tuple[str, str]] = {
            ("release", str(release.release_digest)): (
                RELEASE_PROVENANCE_PREDICATE,
                release_provenance_digest(release),
            ),
            **{
                ("package", package.digest): (
                    PACKAGE_PROVENANCE_PREDICATE,
                    package_provenance_digest(package),
                )
                for package in validated.packages
            },
        }
        covered: set[tuple[str, str]] = set()
        for reference in self.attestations:
            subject = (reference.subject_kind, reference.subject_digest)
            predicate = expected.get(subject)
            if predicate is None:
                raise ReleaseAttestationSetError(
                    "attestation set references a subject outside the release plan"
                )
            if (reference.predicate_type, reference.predicate_digest) != predicate:
                raise ReleaseAttestationSetError(
                    "attestation reference does not match exact release provenance"
                )
            covered.add(subject)
        missing = sorted(set(expected) - covered)
        if missing:
            raise ReleaseAttestationSetError(
                "attestation set does not cover every release subject: "
                + ", ".join(f"{kind}:{digest}" for kind, digest in missing)
            )
        return self.seal()

    @classmethod
    def from_references(
        cls,
        plan: ReleasePlan,
        references: Iterable[ArtifactAttestationRef],
    ) -> "ReleaseAttestationSet":
        release = plan.release.seal()
        return cls(
            project_id=release.project_id,
            release_digest=str(release.release_digest),
            attestations=tuple(references),
        ).validate_plan(plan)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReleaseAttestationSet":
        fields = {
            "schema",
            "project_id",
            "release_digest",
            "attestations",
            "attestation_set_digest",
        }
        if set(value) != fields or value.get("schema") != RELEASE_ATTESTATION_SET_SCHEMA:
            raise ReleaseAttestationSetError("unsupported release attestation set")
        raw = value.get("attestations")
        if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
            raise ReleaseAttestationSetError("attestations must be a list of objects")
        return cls(
            project_id=value.get("project_id"),
            release_digest=value.get("release_digest"),
            attestations=tuple(ArtifactAttestationRef.from_mapping(item) for item in raw),
            attestation_set_digest=value.get("attestation_set_digest"),
        ).seal()


__all__ = [
    "RELEASE_ATTESTATION_SET_SCHEMA",
    "ArtifactAttestationRef",
    "ReleaseAttestationSet",
    "ReleaseAttestationSetError",
]
