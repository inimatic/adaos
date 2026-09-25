"""Application-facing registry and inspection service for Builder CBS compilations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    CapabilityContract,
    EvidenceAssessment,
    EvidenceClaim,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.cbs import compile_prototype_cbs, validate_cbs_compilation
from adaos.services.capability_binding_state import (
    SemanticResolver,
    explain_evidence_assessment,
)
from adaos.services.applications.cbs_admission import (
    NativeApplicationCBSAdmissionService,
)


class ApplicationCBSConflict(ValueError):
    """An optimistic update or application identity check failed."""


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ApplicationCBSService:
    """Store semantic requirement sources and expose viability projections.

    A source may be a full local Builder compilation or the compact, immutable
    requirement set carried by a public registry release.  This service owns no
    binding or state authority: recording either source does not resolve
    packages or activate a plan.
    """

    state_dir: Path

    @property
    def root(self) -> Path:
        return Path(self.state_dir) / "applications" / "cbs-compilations"

    @property
    def writer_lock_path(self) -> Path:
        return self.root / ".writer.lock"

    @property
    def semantic_sources_root(self) -> Path:
        return self.root / "semantic-sources"

    @property
    def semantic_source_lock_path(self) -> Path:
        return self.semantic_sources_root / ".writer.lock"

    def import_semantic_requirement_set(
        self,
        *,
        application_id: str,
        semantic_release: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist the compact requirement source from a public release.

        The public projection intentionally does not carry Builder acceptance,
        simulation, or authoring telemetry.  Only package-neutral requirements
        and their publisher digests cross the registry boundary; resolution,
        evidence, BindingInstances, and activation remain local.
        """

        application_token = str(application_id or "").strip()
        value = dict(semantic_release)
        if not application_token:
            raise ApplicationCBSConflict("Application id is required")
        if value.get("schema") != "adaos.semantic_registry.application_release.v1":
            raise ApplicationCBSConflict("unsupported semantic Application release")
        expected_projection = str(value.get("projection_digest") or "")
        unsigned = dict(value)
        unsigned.pop("projection_digest", None)
        if expected_projection != canonical_payload_digest(unsigned):
            raise ApplicationCBSConflict("semantic Application projection digest mismatch")

        application_ref = str(value.get("application_ref") or "").strip()
        project_id = str(value.get("project_id") or "").strip()
        release_digest = str(value.get("project_release_digest") or "").strip()
        compilation_digest = str(value.get("compilation_digest") or "").strip()
        semantic_revision_digest = str(
            value.get("semantic_revision_digest") or ""
        ).strip()
        if not application_ref or not project_id:
            raise ApplicationCBSConflict("semantic Application identity is incomplete")
        for label, digest in (
            ("project release", release_digest),
            ("compilation", compilation_digest),
            ("semantic revision", semantic_revision_digest),
        ):
            if not digest.startswith("sha256:") or len(digest) != 71:
                raise ApplicationCBSConflict(f"semantic {label} digest is invalid")

        raw_requirements = value.get("requirements")
        if not isinstance(raw_requirements, list) or not raw_requirements:
            raise ApplicationCBSConflict(
                "semantic Application release has no requirements"
            )
        requirements = [
            ApplicationRequirement.from_mapping(item).to_dict()
            for item in raw_requirements
            if isinstance(item, Mapping)
        ]
        if len(requirements) != len(raw_requirements):
            raise ApplicationCBSConflict("semantic requirements contain a malformed item")
        targets = [dict(item.get("environment_target") or {}) for item in requirements]
        if any(target != targets[0] for target in targets[1:]):
            raise ApplicationCBSConflict(
                "semantic requirements must share one environment target"
            )

        record: dict[str, Any] = {
            "schema": "adaos.application.semantic_requirement_set.v1",
            "application_id": application_token,
            "application_ref": application_ref,
            "project_id": project_id,
            "project_release_digest": release_digest,
            "compilation_digest": compilation_digest,
            "semantic_revision_digest": semantic_revision_digest,
            "environment_target": targets[0],
            "requirements": requirements,
            "source_projection_digest": expected_projection,
        }
        record["requirement_set_digest"] = canonical_payload_digest(record)
        digest = str(record["requirement_set_digest"])
        record_path = (
            self.semantic_sources_root
            / "records"
            / f"{digest.removeprefix('sha256:')}.json"
        )
        canonical_pointer = (
            self.semantic_sources_root / "canonical" / f"{_key(application_ref)}.json"
        )
        alias_ref = f"application:{application_token}"
        alias_pointer = (
            self.semantic_sources_root / "aliases" / f"{_key(alias_ref)}.json"
        )
        pointer = {
            "schema": "adaos.application.semantic_requirement_pointer.v1",
            "application_ref": application_ref,
            "application_alias": alias_ref,
            "project_release_digest": release_digest,
            "compilation_digest": compilation_digest,
            "requirement_set_digest": digest,
        }
        with mutation_lock(self.semantic_source_lock_path):
            if record_path.is_file():
                existing = json.loads(record_path.read_text(encoding="utf-8"))
                if existing != record:
                    raise ApplicationCBSConflict(
                        "semantic requirement-set digest collision"
                    )
            else:
                atomic_write_json(record_path, record)
            atomic_write_json(canonical_pointer, pointer)
            atomic_write_json(alias_pointer, pointer)
        return record

    def resolve_application_ref(self, application_ref: str) -> str:
        """Resolve a local Application aggregate alias to its semantic identity."""

        requested = str(application_ref or "").strip()
        alias_path = self.semantic_sources_root / "aliases" / f"{_key(requested)}.json"
        pointer = self._read_semantic_pointer(alias_path)
        return str(pointer.get("application_ref") or requested) if pointer else requested

    def inspect_requirement_source(
        self,
        application_ref: str,
        *,
        project_release_digest: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the exact full compilation or compact imported requirement set."""

        requested = str(application_ref or "").strip()
        canonical_ref = self.resolve_application_ref(requested)
        expected_release = str(project_release_digest or "").strip()
        if expected_release:
            admission = NativeApplicationCBSAdmissionService(
                self.state_dir
            ).find_by_project_release(expected_release)
            if (
                admission is not None
                and admission.get("application_ref") == canonical_ref
            ):
                exact = self.inspect_digest(
                    canonical_ref, str(admission.get("compilation_digest") or "")
                )
                if exact is not None:
                    return exact

        pointer_path = (
            self.semantic_sources_root
            / ("aliases" if requested != canonical_ref else "canonical")
            / f"{_key(requested if requested != canonical_ref else canonical_ref)}.json"
        )
        pointer = self._read_semantic_pointer(pointer_path)
        if pointer is not None and (
            not expected_release
            or pointer.get("project_release_digest") == expected_release
        ):
            digest = str(pointer.get("requirement_set_digest") or "")
            path = (
                self.semantic_sources_root
                / "records"
                / f"{digest.removeprefix('sha256:')}.json"
            )
            return self._read_semantic_source(
                path,
                application_ref=canonical_ref,
                project_release_digest=expected_release or None,
            )
        if expected_release:
            matches: list[dict[str, Any]] = []
            records = self.semantic_sources_root / "records"
            for path in sorted(records.glob("*.json")) if records.is_dir() else ():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ApplicationCBSConflict(
                        "semantic requirement source is unreadable"
                    ) from exc
                if (
                    isinstance(raw, Mapping)
                    and raw.get("application_ref") == canonical_ref
                    and raw.get("project_release_digest") == expected_release
                ):
                    matches.append(
                        self._read_semantic_source(
                            path,
                            application_ref=canonical_ref,
                            project_release_digest=expected_release,
                        )
                    )
            if len(matches) > 1:
                raise ApplicationCBSConflict(
                    "release has ambiguous semantic requirement sources"
                )
            if matches:
                return matches[0]
        return self.inspect(canonical_ref)

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

    def inspect_digest(
        self, application_ref: str, compilation_digest: str
    ) -> dict[str, Any] | None:
        """Return one exact immutable compilation instead of the latest pointer."""

        digest = str(compilation_digest or "").strip().lower()
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise ApplicationCBSConflict("CBS compilation digest is invalid")
        path = (
            self.root
            / _key(str(application_ref))
            / "records"
            / f"{digest.removeprefix('sha256:')}.json"
        )
        if not path.is_file():
            return None
        value = validate_cbs_compilation(json.loads(path.read_text(encoding="utf-8")))
        if str(value["application_ref"]) != str(application_ref):
            raise ApplicationCBSConflict("CBS compilation application identity mismatch")
        if str(value["compilation_digest"]) != digest:
            raise ApplicationCBSConflict("CBS compilation digest mismatch")
        return value

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

    def inspect_admission(
        self,
        application_ref: str,
        *,
        project_release_digest: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the latest exact release admission, when one exists."""

        canonical_ref = self.resolve_application_ref(application_ref)
        service = NativeApplicationCBSAdmissionService(self.state_dir)
        expected_release = str(project_release_digest or "").strip()
        if expected_release:
            admission = service.find_by_project_release(expected_release)
            if (
                admission is not None
                and admission.get("application_ref") == canonical_ref
            ):
                return admission
            return None
        return service.inspect(canonical_ref)

    def semantic_viability(
        self,
        application_ref: str,
        *,
        capability_contracts: Iterable[Mapping[str, Any] | CapabilityContract],
        evidence_claims: Iterable[Mapping[str, Any] | EvidenceClaim] = (),
        evidence_assessments: Iterable[Mapping[str, Any] | EvidenceAssessment] = (),
    ) -> dict[str, Any]:
        compilation = self.inspect_requirement_source(application_ref)
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
        claims = tuple(
            item if isinstance(item, EvidenceClaim) else EvidenceClaim.from_mapping(item)
            for item in evidence_claims
        )
        current_assessments = tuple(
            item
            if isinstance(item, EvidenceAssessment)
            else EvidenceAssessment.from_mapping(item)
            for item in evidence_assessments
        )
        assessment_by_claim = {
            str(item.to_dict()["claim_digest"]): item
            for item in sorted(
                current_assessments,
                key=lambda item: (
                    str(item.to_dict()["evaluated_at"]),
                    item.digest,
                ),
            )
        }
        assessments = []
        evidence_obligations: list[dict[str, Any]] = []
        for requirement in requirements:
            assessment = resolver.semantic_viability(requirement, contracts)
            required_claim_kinds = list(
                requirement.to_dict()["evidence_threshold"]["required_claim_kinds"]
            )
            matching_claims = [
                claim
                for claim in claims
                if any(
                    str(subject["ref"]) == requirement.capability_ref
                    for subject in claim.to_dict()["subjects"]
                )
            ]
            obligations = []
            for kind in required_claim_kinds:
                candidates = [
                    claim
                    for claim in matching_claims
                    if claim.to_dict()["claim_kind"] == kind
                    and claim.digest in assessment_by_claim
                ]
                if not candidates:
                    obligations.append({"claim_kind": kind, "status": "unassessed"})
                    continue
                claim = sorted(
                    candidates,
                    key=lambda item: (
                        str(assessment_by_claim[item.digest].to_dict()["evaluated_at"]),
                        item.digest,
                    ),
                )[-1]
                explanation = explain_evidence_assessment(
                    claim,
                    assessment_by_claim[claim.digest],
                    purpose="production",
                )
                obligations.append(
                    {
                        "claim_kind": kind,
                        "status": explanation["status"],
                        "claim_ref": claim.claim_ref,
                        "claim_digest": claim.digest,
                        "explanation": explanation,
                    }
                )
            assessment["evidence_obligations"] = obligations
            assessments.append(assessment)
            evidence_obligations.extend(
                {
                    "requirement_ref": requirement.requirement_ref,
                    **obligation,
                }
                for obligation in obligations
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

    def lifecycle_projection(
        self,
        application_ref: str,
        *,
        runtime_selection: Mapping[str, Any] | None = None,
        local_development: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the compact read-only CBS lifecycle shown by Applications.

        Missing resolution and plan records remain explicit. An Application
        Trial is an operational observation, not implicit CBS admission, and
        this projection never becomes a second activation authority.
        """

        selection = dict(runtime_selection or {})
        expected_release = str(selection.get("release_digest") or "").strip()
        compilation = self.inspect_requirement_source(
            application_ref,
            project_release_digest=expected_release or None,
        )
        admission = self.inspect_admission(
            application_ref,
            project_release_digest=expected_release or None,
        )
        development = dict(local_development or {})
        trial = (
            dict(development.get("trial"))
            if isinstance(development.get("trial"), Mapping)
            else {}
        )
        matching_admission = False
        if compilation is None:
            requirement = {
                "status": "not_compiled",
                "summary": "No CBS semantic compilation is registered.",
                "count": 0,
            }
            resolution_status = "not_started"
            semantic_revision_digest = None
            compilation_digest = None
        else:
            requirements = list(compilation.get("requirements") or [])
            requirement = {
                "status": "compiled",
                "summary": f"{len(requirements)} semantic requirements compiled",
                "count": len(requirements),
            }
            matching_admission = bool(
                admission
                and admission.get("compilation_digest")
                == compilation.get("compilation_digest")
                and (
                    not selection.get("release_digest")
                    or admission.get("project_release_digest")
                    == selection.get("release_digest")
                )
            )
            resolution_status = (
                str(admission.get("status") or "unresolved")
                if matching_admission and admission is not None
                else "unresolved"
            )
            semantic_revision_digest = compilation.get("semantic_revision_digest")
            compilation_digest = compilation.get("compilation_digest")

        resolution_admitted = resolution_status in {"accepted", "admitted", "resolved"}
        admitted_resolutions = (
            list(admission.get("resolutions") or [])
            if resolution_admitted and admission is not None
            else []
        )
        resolution = {
            "status": "admitted" if resolution_admitted else resolution_status,
            "summary": (
                f"{len(admitted_resolutions)} exact production resolutions admitted"
                if resolution_admitted
                else "Production resolution is not admitted"
            ),
            "admission_digest": (
                admission.get("admission_digest")
                if resolution_admitted and admission is not None
                else None
            ),
            "project_release_digest": (
                admission.get("project_release_digest")
                if resolution_admitted and admission is not None
                else None
            ),
            "requirements_resolved": (
                admission.get("requirements_resolved")
                if matching_admission and admission is not None
                else 0
            ),
            "requirements_total": (
                admission.get("requirements_total")
                if matching_admission and admission is not None
                else requirement["count"]
            ),
        }
        admitted_plans = (
            list(admission.get("plans") or [])
            if resolution_admitted and admission is not None
            else []
        )
        plan = {
            "status": (
                "not_created"
                if not resolution_admitted
                else "ready"
                if len(admitted_plans) == len(admitted_resolutions)
                else "incomplete"
            ),
            "summary": (
                "ResolutionPlan awaits an admitted production resolution"
                if not resolution_admitted
                else f"{len(admitted_plans)} exact candidate plans are ready"
            ),
            "plan_digests": [item.get("plan_digest") for item in admitted_plans],
        }

        source = str(selection.get("source") or "")
        if source == "local_trial":
            activation_status = "trial_active"
            activation_summary = "An isolated beta Trial is selected"
        elif source == "stable_installation":
            activation_status = "active"
            activation_summary = "The Workspace runtime is selected"
        elif trial.get("evidence_present"):
            activation_status = "trial_observed"
            activation_summary = "Trial evidence exists; no current selection was observed"
        else:
            activation_status = "inactive"
            activation_summary = "No Trial or Workspace runtime selection is active"
        activation = {
            "status": activation_status,
            "summary": activation_summary,
            "source": source or None,
            "release_digest": selection.get("release_digest"),
            "revision": selection.get("revision"),
        }

        workspace_committed = source == "stable_installation"
        lock = {
            "status": "committed" if workspace_committed else "unchanged",
            "summary": (
                "Stable installation and Workspace runtime authority are observed"
                if workspace_committed
                else "Workspace authority remains unchanged by this view"
            ),
        }
        return {
            "schema": "adaos.application.cbs_lifecycle_projection.v1",
            "application_ref": application_ref,
            "authoritative": False,
            "authority": "derived_read_only",
            "authority_note": (
                "Derived view only; ApplicationResolution, ResolutionPlan, activation journal, "
                "and WorkspaceLock remain authoritative in their owning stores."
            ),
            "semantic_revision_digest": semantic_revision_digest,
            "compilation_digest": compilation_digest,
            "admission_digest": (
                admission.get("admission_digest")
                if matching_admission and admission is not None
                else None
            ),
            "requirement": requirement,
            "resolution": resolution,
            "plan": plan,
            "activation": activation,
            "lock": lock,
            "stages": [
                {"id": "requirement", **requirement},
                {"id": "resolution", **resolution},
                {"id": "plan", **plan},
                {"id": "activation", **activation},
                {"id": "lock", **lock},
            ],
        }

    @staticmethod
    def _read_pointer(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping) or value.get("schema") != "adaos.application.cbs_pointer.v1":
            raise ApplicationCBSConflict("invalid Application CBS pointer")
        return dict(value)

    @staticmethod
    def _read_semantic_pointer(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, Mapping)
            or value.get("schema")
            != "adaos.application.semantic_requirement_pointer.v1"
        ):
            raise ApplicationCBSConflict("invalid semantic requirement pointer")
        return dict(value)

    @staticmethod
    def _read_semantic_source(
        path: Path,
        *,
        application_ref: str,
        project_release_digest: str | None,
    ) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplicationCBSConflict(
                "semantic requirement source is unavailable"
            ) from exc
        if (
            not isinstance(value, Mapping)
            or value.get("schema")
            != "adaos.application.semantic_requirement_set.v1"
            or value.get("application_ref") != application_ref
        ):
            raise ApplicationCBSConflict("semantic requirement source identity mismatch")
        if (
            project_release_digest
            and value.get("project_release_digest") != project_release_digest
        ):
            raise ApplicationCBSConflict("semantic requirement release mismatch")
        record = dict(value)
        expected = str(record.pop("requirement_set_digest", ""))
        if expected != canonical_payload_digest(record):
            raise ApplicationCBSConflict("semantic requirement-set digest mismatch")
        record["requirement_set_digest"] = expected
        requirements = record.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ApplicationCBSConflict("semantic requirement source is empty")
        record["requirements"] = [
            ApplicationRequirement.from_mapping(item).to_dict()
            for item in requirements
            if isinstance(item, Mapping)
        ]
        if len(record["requirements"]) != len(requirements):
            raise ApplicationCBSConflict("semantic requirement source is malformed")
        return record


__all__ = ["ApplicationCBSConflict", "ApplicationCBSService"]
