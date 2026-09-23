"""Application-facing registry and inspection service for Builder CBS compilations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from adaos.domain.capability_binding_state import ApplicationRequirement, CapabilityContract
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.cbs import compile_prototype_cbs, validate_cbs_compilation
from adaos.services.capability_binding_state import SemanticResolver


class ApplicationCBSConflict(ValueError):
    """An optimistic update or application identity check failed."""


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ApplicationCBSService:
    """Store exact compilations and expose read-only viability projections.

    This service owns no binding or state authority. A successful write records
    what Builder compiled; it does not resolve packages or activate a plan.
    """

    state_dir: Path

    @property
    def root(self) -> Path:
        return Path(self.state_dir) / "applications" / "cbs-compilations"

    @property
    def writer_lock_path(self) -> Path:
        return self.root / ".writer.lock"

    def compile_and_register(
        self,
        *,
        application_ref: str,
        acceptance: Mapping[str, Any],
        environment_profile_ref: str = "profile:local/default",
        allowed_modes: Sequence[str] = ("simulation", "production"),
        expected_previous_digest: str | None = None,
    ) -> dict[str, Any]:
        compilation = compile_prototype_cbs(
            acceptance,
            environment_profile_ref=environment_profile_ref,
            allowed_modes=allowed_modes,
        )
        if str(compilation["application_ref"]) != str(application_ref):
            raise ApplicationCBSConflict(
                "route application_ref does not match the accepted Builder prototype"
            )
        return self.register(
            compilation,
            expected_previous_digest=expected_previous_digest,
        )

    def register(
        self,
        compilation: Mapping[str, Any],
        *,
        expected_previous_digest: str | None = None,
    ) -> dict[str, Any]:
        value = validate_cbs_compilation(compilation)
        application_ref = str(value["application_ref"])
        digest = str(value["compilation_digest"])
        directory = self.root / _key(application_ref)
        record_path = directory / "records" / f"{digest.removeprefix('sha256:')}.json"
        latest_path = directory / "latest.json"
        with mutation_lock(self.writer_lock_path):
            current = self._read_pointer(latest_path)
            current_digest = str(current.get("compilation_digest") or "") if current else None
            if current_digest == digest:
                return value
            if expected_previous_digest != current_digest:
                expected = expected_previous_digest or "<none>"
                actual = current_digest or "<none>"
                raise ApplicationCBSConflict(
                    f"stale CBS compilation update: expected {expected}, current {actual}"
                )
            if record_path.is_file():
                existing = json.loads(record_path.read_text(encoding="utf-8"))
                if existing != value:
                    raise ApplicationCBSConflict(f"CBS compilation digest collision: {digest}")
            else:
                atomic_write_json(record_path, value)
            atomic_write_json(
                latest_path,
                {
                    "schema": "adaos.application.cbs_pointer.v1",
                    "application_ref": application_ref,
                    "compilation_digest": digest,
                    "semantic_revision_digest": value["semantic_revision_digest"],
                },
            )
        return value

    def inspect(self, application_ref: str) -> dict[str, Any] | None:
        directory = self.root / _key(str(application_ref))
        pointer = self._read_pointer(directory / "latest.json")
        if not pointer:
            return None
        if str(pointer.get("application_ref") or "") != str(application_ref):
            raise ApplicationCBSConflict("CBS pointer application identity mismatch")
        digest = str(pointer.get("compilation_digest") or "")
        path = directory / "records" / f"{digest.removeprefix('sha256:')}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        compilation = validate_cbs_compilation(value)
        if str(compilation["application_ref"]) != str(application_ref):
            raise ApplicationCBSConflict("CBS compilation application identity mismatch")
        return compilation

    def history(self, application_ref: str) -> list[dict[str, Any]]:
        records = self.root / _key(str(application_ref)) / "records"
        if not records.is_dir():
            return []
        result = []
        for path in sorted(records.glob("*.json")):
            value = validate_cbs_compilation(json.loads(path.read_text(encoding="utf-8")))
            if str(value["application_ref"]) != str(application_ref):
                raise ApplicationCBSConflict("CBS history application identity mismatch")
            result.append(value)
        return sorted(
            result,
            key=lambda item: (
                str(item["semantic_revision_digest"]),
                str(item["compilation_digest"]),
            ),
        )

    def semantic_viability(
        self,
        application_ref: str,
        *,
        capability_contracts: Iterable[Mapping[str, Any] | CapabilityContract],
    ) -> dict[str, Any]:
        compilation = self.inspect(application_ref)
        if compilation is None:
            raise KeyError(application_ref)
        contracts = tuple(
            item if isinstance(item, CapabilityContract) else CapabilityContract.from_mapping(item)
            for item in capability_contracts
        )
        resolver = SemanticResolver()
        requirements = [
            ApplicationRequirement.from_mapping(item)
            for item in compilation["requirements"]
        ]
        assessments = []
        evidence_obligations: list[dict[str, Any]] = []
        for requirement in requirements:
            assessment = resolver.semantic_viability(requirement, contracts)
            required_claim_kinds = list(
                requirement.to_dict()["evidence_threshold"]["required_claim_kinds"]
            )
            assessment["evidence_obligations"] = [
                {"claim_kind": kind, "status": "unassessed"}
                for kind in required_claim_kinds
            ]
            assessments.append(assessment)
            evidence_obligations.extend(
                {
                    "requirement_ref": requirement.requirement_ref,
                    "claim_kind": kind,
                    "status": "unassessed",
                }
                for kind in required_claim_kinds
            )
        unresolved = [
            requirement.requirement_ref
            for requirement, assessment in zip(requirements, assessments, strict=True)
            if not bool(assessment["viable"])
        ]
        return {
            "schema": "adaos.application.cbs_viability.v1",
            "application_ref": application_ref,
            "compilation_digest": compilation["compilation_digest"],
            "semantic_revision_digest": compilation["semantic_revision_digest"],
            "target": "semantic",
            "viable": not unresolved,
            "requirements": assessments,
            "unresolved_requirement_refs": unresolved,
            "capability_gaps": [
                {
                    "requirement_ref": requirement.requirement_ref,
                    "capability_ref": requirement.capability_ref,
                    "contract_range": requirement.to_dict()["contract_range"],
                    "code": "unmet_requirement",
                }
                for requirement in requirements
                if requirement.requirement_ref in unresolved
            ],
            "evidence_obligations": evidence_obligations,
            "activation_performed": False,
        }

    @staticmethod
    def _read_pointer(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping) or value.get("schema") != "adaos.application.cbs_pointer.v1":
            raise ApplicationCBSConflict("invalid Application CBS pointer")
        return dict(value)


__all__ = ["ApplicationCBSConflict", "ApplicationCBSService"]
