from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.attestations import (
    PACKAGE_PROVENANCE_PREDICATE,
    RELEASE_PROVENANCE_PREDICATE,
    ArtifactAttestation,
    ArtifactAttestationStore,
    Ed25519ArtifactSigner,
    package_provenance_digest,
    release_provenance_digest,
)
from adaos.services.artifact_pipeline.attestation_sets import (
    ArtifactAttestationRef,
    ReleaseAttestationSet,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


ATTESTATION_PUBLICATION_OPERATION_SCHEMA = (
    "adaos.artifact.attestation_publication.v1"
)
ATTESTATION_PUBLICATION_RESULT_SCHEMA = (
    "adaos.artifact.attestation_publication_result.v1"
)

_OPERATION_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_ITEM_STATUSES = {"pending", "dispatching", "uncertain", "completed"}
_OPERATION_STATUSES = {"ready", "publishing", "uncertain", "completed"}


class AttestationPublicationError(RuntimeError):
    pass


class AttestationPublicationConflict(AttestationPublicationError):
    pass


class AttestationPublicationUncertain(AttestationPublicationError):
    def __init__(self, operation_id: str, item_id: str) -> None:
        self.operation_id = operation_id
        self.item_id = item_id
        super().__init__(
            "attestation publication has an unknown external outcome; "
            f"reconcile operation {operation_id} before continuing (item={item_id})"
        )


@dataclass(frozen=True, slots=True)
class PublishedAttestationRef:
    subject_kind: str
    subject_digest: str
    attestation_digest: str
    issuer: str
    key_id: str
    predicate_type: str
    predicate_digest: str
    status: str
    reconciled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject_digest": self.subject_digest,
            "attestation_digest": self.attestation_digest,
            "issuer": self.issuer,
            "key_id": self.key_id,
            "predicate_type": self.predicate_type,
            "predicate_digest": self.predicate_digest,
            "status": self.status,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True, slots=True)
class AttestationPublicationResult:
    operation_id: str
    status: str
    release_digest: str
    attestations: tuple[PublishedAttestationRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ATTESTATION_PUBLICATION_RESULT_SCHEMA,
            "operation_id": self.operation_id,
            "status": self.status,
            "release_digest": self.release_digest,
            "attestations": [item.to_dict() for item in self.attestations],
        }

    def release_attestation_set(self, plan: ReleasePlan) -> ReleaseAttestationSet:
        if self.status != "completed" or any(
            item.status != "completed" for item in self.attestations
        ):
            raise AttestationPublicationError(
                "only a completed publication can bind a release attestation set"
            )
        if str(plan.release.seal().release_digest) != self.release_digest:
            raise AttestationPublicationError(
                "attestation publication belongs to another release plan"
            )
        return ReleaseAttestationSet.from_references(
            plan,
            (
                ArtifactAttestationRef(
                    subject_kind=item.subject_kind,
                    subject_digest=item.subject_digest,
                    project_id=plan.release.project_id,
                    attestation_digest=item.attestation_digest,
                    issuer=item.issuer,
                    key_id=item.key_id,
                    predicate_type=item.predicate_type,
                    predicate_digest=item.predicate_digest,
                )
                for item in self.attestations
            ),
        )


class ArtifactAttestationPublisher:
    """Journal exact detached signatures before any external mutation.

    An item in ``dispatching`` is treated as uncertain after interruption.  The
    publisher never guesses whether the remote write happened and therefore
    never repeats it.  ``reconcile`` performs remote reads only; callers must
    invoke ``publish`` again explicitly after a successful reconciliation to
    continue other pending items.
    """

    def __init__(
        self,
        *,
        state_root: Path,
        store: ArtifactAttestationStore,
        signer: Ed25519ArtifactSigner,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.operation_root = self.state_root / "attestation-publications"
        self.store = store
        self.signer = signer
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _idempotency_digest(idempotency_key: str) -> str:
        token = str(idempotency_key or "").strip()
        if not token:
            raise AttestationPublicationError("idempotency_key must not be empty")
        if len(token) > 1024:
            raise AttestationPublicationError("idempotency_key exceeds 1024 characters")
        return canonical_payload_digest(
            {
                "schema": "adaos.artifact.attestation_publication_idempotency.v1",
                "idempotency_key": token,
            }
        )

    @classmethod
    def operation_id(cls, idempotency_key: str) -> str:
        return cls._idempotency_digest(idempotency_key).split(":", 1)[1]

    def operation_path(self, operation_id: str) -> Path:
        token = str(operation_id or "").strip().lower()
        if not _OPERATION_ID_RE.fullmatch(token):
            raise AttestationPublicationError("operation_id must contain 64 lowercase hex characters")
        return self.operation_root / f"{token}.json"

    def operation_lock_path(self, operation_id: str) -> Path:
        return self.operation_path(operation_id).with_suffix(".lock")

    def _timestamp(self) -> str:
        observed = self.clock()
        if observed.tzinfo is None:
            raise AttestationPublicationError("attestation publication clock must be timezone-aware")
        return (
            observed.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _plan_payload(plan: ReleasePlan) -> dict[str, Any]:
        return {"schema": "adaos.artifact.release_plan.v1", **plan.explain()}

    @classmethod
    def _plan_digest(cls, plan: ReleasePlan) -> str:
        return canonical_payload_digest(cls._plan_payload(plan))

    @classmethod
    def _validated_plan(cls, plan: ReleasePlan) -> ReleasePlan:
        try:
            return ReleasePlan.from_mapping(cls._plan_payload(plan))
        except Exception as exc:
            raise AttestationPublicationError(
                f"release plan is not internally consistent: {exc}"
            ) from exc

    @staticmethod
    def _journal_digest(operation: Mapping[str, Any]) -> str:
        return canonical_payload_digest(
            {key: value for key, value in operation.items() if key != "journal_digest"}
        )

    def _save(self, operation: dict[str, Any]) -> None:
        operation["revision"] = int(operation.get("revision") or 0) + 1
        operation["updated_at"] = self._timestamp()
        operation["journal_digest"] = self._journal_digest(operation)
        atomic_write_json(self.operation_path(str(operation["operation_id"])), operation)

    @staticmethod
    def _reference(item: Mapping[str, Any]) -> PublishedAttestationRef:
        attestation = ArtifactAttestation.from_mapping(item["attestation"])
        return PublishedAttestationRef(
            subject_kind=attestation.subject_kind,
            subject_digest=attestation.subject_digest,
            attestation_digest=str(attestation.attestation_digest),
            issuer=attestation.issuer,
            key_id=attestation.key_id,
            predicate_type=attestation.predicate_type,
            predicate_digest=attestation.predicate_digest,
            status=str(item["status"]),
            reconciled=item.get("completed_via") == "reconciliation",
        )

    @classmethod
    def _result(cls, operation: Mapping[str, Any]) -> AttestationPublicationResult:
        return AttestationPublicationResult(
            operation_id=str(operation["operation_id"]),
            status=str(operation["status"]),
            release_digest=str(operation["release_digest"]),
            attestations=tuple(cls._reference(item) for item in operation["items"]),
        )

    def _load(self, operation_id: str) -> dict[str, Any]:
        path = self.operation_path(operation_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AttestationPublicationError(
                f"attestation publication operation does not exist: {operation_id}"
            ) from exc
        except Exception as exc:
            raise AttestationPublicationError(
                f"cannot read attestation publication operation: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise AttestationPublicationError("attestation publication journal must contain an object")
        allowed = {
            "schema",
            "operation_id",
            "idempotency_digest",
            "plan_digest",
            "release_digest",
            "project_id",
            "issuer",
            "key_id",
            "created_at",
            "updated_at",
            "status",
            "revision",
            "items",
            "journal_digest",
        }
        if set(payload) != allowed:
            raise AttestationPublicationError("attestation publication journal has unsupported fields")
        if payload.get("schema") != ATTESTATION_PUBLICATION_OPERATION_SCHEMA:
            raise AttestationPublicationError("unsupported attestation publication journal schema")
        if payload.get("operation_id") != operation_id:
            raise AttestationPublicationError("attestation publication operation identity mismatch")
        if payload.get("status") not in _OPERATION_STATUSES:
            raise AttestationPublicationError("attestation publication journal has invalid status")
        if payload.get("journal_digest") != self._journal_digest(payload):
            raise AttestationPublicationError("attestation publication journal digest mismatch")
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise AttestationPublicationError("attestation publication journal has no items")
        seen: set[str] = set()
        for sequence, item in enumerate(items):
            if not isinstance(item, dict):
                raise AttestationPublicationError("attestation publication item must be an object")
            required = {"item_id", "sequence", "status", "attestation"}
            optional = {
                "dispatch_started_at",
                "completed_at",
                "completed_via",
                "uncertain_since",
                "last_error",
                "store_digest",
            }
            if not required.issubset(item) or set(item) - required - optional:
                raise AttestationPublicationError("attestation publication item has invalid fields")
            if item.get("sequence") != sequence or item.get("status") not in _ITEM_STATUSES:
                raise AttestationPublicationError("attestation publication item order or status is invalid")
            attestation = ArtifactAttestation.from_mapping(item["attestation"])
            item_id = str(attestation.attestation_digest).split(":", 1)[1]
            if item.get("item_id") != item_id or item_id in seen:
                raise AttestationPublicationError("attestation publication item identity mismatch")
            if attestation.project_id != payload.get("project_id"):
                raise AttestationPublicationError("attestation publication item project mismatch")
            if attestation.issuer != payload.get("issuer") or attestation.key_id != payload.get("key_id"):
                raise AttestationPublicationError("attestation publication signer identity mismatch")
            seen.add(item_id)
        return payload

    @staticmethod
    def _item(attestation: ArtifactAttestation, *, sequence: int) -> dict[str, Any]:
        sealed = attestation.seal()
        return {
            "item_id": str(sealed.attestation_digest).split(":", 1)[1],
            "sequence": sequence,
            "status": "pending",
            "attestation": sealed.to_dict(),
        }

    def _create(
        self,
        plan: ReleasePlan,
        *,
        operation_id: str,
        idempotency_digest: str,
    ) -> dict[str, Any]:
        release = plan.release.seal()
        issued_at = self._timestamp()
        attestations = [
            self.signer.sign(
                subject_kind="package",
                subject_digest=package.digest,
                project_id=release.project_id,
                predicate_type=PACKAGE_PROVENANCE_PREDICATE,
                predicate_digest=package_provenance_digest(package),
                issued_at=issued_at,
            )
            for package in sorted(plan.packages, key=lambda item: item.key)
        ]
        attestations.append(
            self.signer.sign(
                subject_kind="release",
                subject_digest=str(release.release_digest),
                project_id=release.project_id,
                predicate_type=RELEASE_PROVENANCE_PREDICATE,
                predicate_digest=release_provenance_digest(release),
                issued_at=issued_at,
            )
        )
        key_id = str(self.signer.trusted_key().key_id)
        operation: dict[str, Any] = {
            "schema": ATTESTATION_PUBLICATION_OPERATION_SCHEMA,
            "operation_id": operation_id,
            "idempotency_digest": idempotency_digest,
            "plan_digest": self._plan_digest(plan),
            "release_digest": str(release.release_digest),
            "project_id": release.project_id,
            "issuer": self.signer.issuer,
            "key_id": key_id,
            "created_at": issued_at,
            "updated_at": issued_at,
            "status": "ready",
            "revision": 0,
            "items": [self._item(item, sequence=index) for index, item in enumerate(attestations)],
            "journal_digest": "",
        }
        self._save(operation)
        return operation

    def _open_or_create(
        self,
        plan: ReleasePlan,
        *,
        operation_id: str,
        idempotency_digest: str,
    ) -> dict[str, Any]:
        path = self.operation_path(operation_id)
        if not path.exists():
            return self._create(
                plan,
                operation_id=operation_id,
                idempotency_digest=idempotency_digest,
            )
        operation = self._load(operation_id)
        if (
            operation["idempotency_digest"] != idempotency_digest
            or operation["plan_digest"] != self._plan_digest(plan)
            or operation["release_digest"] != str(plan.release.seal().release_digest)
        ):
            raise AttestationPublicationConflict(
                "idempotency key is already bound to a different release plan"
            )
        return operation

    def publish(
        self,
        plan: ReleasePlan,
        *,
        idempotency_key: str,
    ) -> AttestationPublicationResult:
        plan = self._validated_plan(plan)
        idempotency_digest = self._idempotency_digest(idempotency_key)
        operation_id = idempotency_digest.split(":", 1)[1]
        with mutation_lock(self.operation_lock_path(operation_id)):
            operation = self._open_or_create(
                plan,
                operation_id=operation_id,
                idempotency_digest=idempotency_digest,
            )
            for item in operation["items"]:
                if item["status"] == "dispatching":
                    item["status"] = "uncertain"
                    item["uncertain_since"] = self._timestamp()
                    item["last_error"] = "publisher interrupted after dispatch intent was persisted"
                    operation["status"] = "uncertain"
                    self._save(operation)
                if item["status"] == "uncertain":
                    raise AttestationPublicationUncertain(operation_id, str(item["item_id"]))
            if operation["status"] == "completed":
                return self._result(operation)

            for item in operation["items"]:
                if item["status"] == "completed":
                    continue
                attestation = ArtifactAttestation.from_mapping(item["attestation"])
                item["status"] = "dispatching"
                item["dispatch_started_at"] = self._timestamp()
                operation["status"] = "publishing"
                self._save(operation)
                try:
                    observed = self.store.put(attestation)
                    if observed != attestation.attestation_digest:
                        raise AttestationPublicationError(
                            "attestation store returned a different attestation digest"
                        )
                except Exception as exc:
                    item["status"] = "uncertain"
                    item["uncertain_since"] = self._timestamp()
                    item["last_error"] = f"{type(exc).__name__}: {exc}"[:1024]
                    operation["status"] = "uncertain"
                    self._save(operation)
                    raise AttestationPublicationUncertain(
                        operation_id,
                        str(item["item_id"]),
                    ) from exc
                item["status"] = "completed"
                item["completed_at"] = self._timestamp()
                item["completed_via"] = "write_acknowledgement"
                item["store_digest"] = str(observed)
                item.pop("uncertain_since", None)
                item.pop("last_error", None)
                self._save(operation)

            operation["status"] = "completed"
            self._save(operation)
            return self._result(operation)

    def reconcile(self, operation_id: str) -> AttestationPublicationResult:
        token = str(operation_id or "").strip().lower()
        with mutation_lock(self.operation_lock_path(token)):
            operation = self._load(token)
            changed = False
            for item in operation["items"]:
                if item["status"] == "dispatching":
                    item["status"] = "uncertain"
                    item["uncertain_since"] = self._timestamp()
                    item["last_error"] = "publisher interrupted after dispatch intent was persisted"
                    changed = True
                if item["status"] != "uncertain":
                    continue
                expected = ArtifactAttestation.from_mapping(item["attestation"])
                observed = self.store.list_for_subject(
                    expected.subject_kind,
                    expected.subject_digest,
                )
                exact = [
                    candidate
                    for candidate in observed
                    if candidate.attestation_digest == expected.attestation_digest
                ]
                if exact and exact[0] != expected:
                    raise AttestationPublicationError(
                        "external attestation digest resolves to different signed content"
                    )
                if not exact:
                    continue
                item["status"] = "completed"
                item["completed_at"] = self._timestamp()
                item["completed_via"] = "reconciliation"
                item["store_digest"] = str(expected.attestation_digest)
                item.pop("uncertain_since", None)
                item.pop("last_error", None)
                changed = True

            statuses = {str(item["status"]) for item in operation["items"]}
            if statuses == {"completed"}:
                next_status = "completed"
            elif "uncertain" in statuses or "dispatching" in statuses:
                next_status = "uncertain"
            else:
                next_status = "ready"
            if operation["status"] != next_status:
                operation["status"] = next_status
                changed = True
            if changed:
                self._save(operation)
            return self._result(operation)

    def load(self, operation_id: str) -> AttestationPublicationResult:
        return self._result(self._load(str(operation_id or "").strip().lower()))


__all__ = [
    "ATTESTATION_PUBLICATION_OPERATION_SCHEMA",
    "ATTESTATION_PUBLICATION_RESULT_SCHEMA",
    "ArtifactAttestationPublisher",
    "AttestationPublicationConflict",
    "AttestationPublicationError",
    "AttestationPublicationResult",
    "AttestationPublicationUncertain",
    "PublishedAttestationRef",
]
