"""Exact CBS admission for an immutable Application release.

Builder compilation deliberately stops at package-neutral requirements.  This
module owns the next boundary: discover providers in the exact release closure,
materialize candidate-local binding instances, admit evidence, and persist an
immutable set of per-requirement resolutions and plans.  It does not switch
Workspace authority; activation remains a separate explicit operation.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingDefinition,
    BindingDelivery,
    BindingInstance,
    CapabilityContract,
    EnvironmentProfile,
    EvidenceAssessment,
    EvidenceClaim,
)
from adaos.services.artifact_pipeline.cbs_authoring import (
    BINDING_OUTPUT_PATH,
    CAPABILITY_OUTPUT_PATH,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.capability_binding_state import (
    PortableContractCatalog,
    ResolutionFailure,
    ResolutionPlanner,
    SemanticResolver,
)


CBS_ADMISSION_SCHEMA = "adaos.application.cbs_admission.v1"
CBS_ADMISSION_POINTER_SCHEMA = "adaos.application.cbs_admission_pointer.v1"


class NativeApplicationCBSAdmissionError(ValueError):
    """An exact release cannot be admitted against its semantic compilation."""


def _key(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token(value: str, *, limit: int = 56) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "")).strip("-._")
    return (normalized or _key(value)[:16])[:limit]


def _read_json_member(archive_bytes: bytes, name: str) -> Mapping[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
            value = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise NativeApplicationCBSAdmissionError(
            f"verified package has no valid {name}"
        ) from exc
    if not isinstance(value, Mapping):
        raise NativeApplicationCBSAdmissionError(f"{name} must contain an object")
    return value


def _ui_provider(package: Any) -> tuple[CapabilityContract, BindingDefinition, BindingDelivery]:
    capability = CapabilityContract.create(
        capability_ref="capability:application.ui.render",
        version="1.0.0",
        title="Render an AdaOS Application WebUI",
        operations=(
            {
                "operation_id": "render",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "errors": ["webui_contract_invalid", "renderer_unavailable"],
            },
        ),
        invariants=("the rendered document is the exact package member",),
        conformance_refs=("schema:adaos.webui.v1",),
    )
    binding = BindingDefinition.create(
        binding_definition_ref="binding-definition:application.ui.render.webui-v1",
        version="1.0.0",
        capability_ref=capability.capability_ref,
        capability_version=capability.version,
        entry_protocol="adaos.webui.v1",
        implementation_entrypoint="application.ui.render.webui-v1",
        state_support=(),
        modes=("production",),
        environment_constraints={
            "profile_classes": ["local"],
            "provider_features": ["webui_v1"],
        },
        authority_requirements=(),
        conformance_obligations=("capability_conformance",),
    )
    delivery = BindingDelivery.create(
        binding_definition_ref=binding.binding_definition_ref,
        binding_definition_digest=binding.digest,
        logical_entrypoint="application.ui.render.webui-v1",
        package={
            "kind": package.kind,
            "id": package.artifact_id,
            "version": package.version,
            "digest": package.digest,
        },
        physical_member="webui.json",
    )
    return capability, binding, delivery


@dataclass(slots=True)
class NativeApplicationCBSAdmissionService:
    state_dir: Path
    now: Callable[[], datetime] = lambda: datetime.now(UTC)

    @property
    def root(self) -> Path:
        return Path(self.state_dir) / "applications" / "cbs-admissions"

    @property
    def writer_lock_path(self) -> Path:
        return self.root / ".writer.lock"

    @property
    def portable_catalog(self) -> PortableContractCatalog:
        return PortableContractCatalog(
            Path(self.state_dir) / "capability-binding-state" / "portable"
        )

    def inspect(self, application_ref: str) -> dict[str, Any] | None:
        directory = self.root / _key(application_ref)
        pointer_path = directory / "latest.json"
        if not pointer_path.is_file():
            return None
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if (
            not isinstance(pointer, Mapping)
            or pointer.get("schema") != CBS_ADMISSION_POINTER_SCHEMA
            or pointer.get("application_ref") != application_ref
        ):
            raise NativeApplicationCBSAdmissionError("invalid CBS admission pointer")
        digest = str(pointer.get("admission_digest") or "")
        record_path = directory / "records" / f"{digest.removeprefix('sha256:')}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        return self._validate_record(record, application_ref=application_ref)

    def find_by_project_release(
        self, project_release_digest: str
    ) -> dict[str, Any] | None:
        """Find the unique immutable admission for an exact ProjectRelease."""

        expected = str(project_release_digest or "").strip().lower()
        matches: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return None
        for path in sorted(self.root.glob("*/records/*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise NativeApplicationCBSAdmissionError(
                    f"cannot read CBS admission record: {path}"
                ) from exc
            if not isinstance(value, Mapping):
                raise NativeApplicationCBSAdmissionError(
                    f"CBS admission record must be an object: {path}"
                )
            if str(value.get("project_release_digest") or "").lower() != expected:
                continue
            application_ref = str(value.get("application_ref") or "")
            matches.append(self._validate_record(value, application_ref=application_ref))
        if len(matches) > 1:
            raise NativeApplicationCBSAdmissionError(
                "ProjectRelease has more than one CBS admission identity"
            )
        return matches[0] if matches else None

    def admit(
        self,
        *,
        application_ref: str,
        compilation: Mapping[str, Any],
        release_plan: ReleasePlan,
        package_store: ContentAddressedPackageStore,
        workspace_ref: str,
        evidence_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve every compiled requirement against one exact release closure."""

        if str(compilation.get("application_ref") or "") != application_ref:
            raise NativeApplicationCBSAdmissionError(
                "CBS compilation belongs to another Application"
            )
        release_digest = (
            release_plan.release.release_digest
            or release_plan.release.computed_digest()
        )
        current = self.inspect(application_ref)
        if (
            current is not None
            and current.get("compilation_digest") == compilation.get("compilation_digest")
            and current.get("project_release_digest") == release_digest
            and current.get("workspace_ref") == workspace_ref
        ):
            return current

        contracts: list[CapabilityContract] = []
        bindings: list[BindingDefinition] = []
        deliveries: list[BindingDelivery] = []
        scenario_packages = []
        for package in release_plan.packages:
            archive_bytes, verified = package_store.read_verified(package.digest)
            if verified.ref != package:
                raise NativeApplicationCBSAdmissionError(
                    f"verified package identity changed: {package.key}"
                )
            if package.kind == "scenario" and "webui.json" in verified.file_names:
                scenario_packages.append(package)
            if not verified.binding_deliveries:
                continue
            capability = CapabilityContract.from_mapping(
                _read_json_member(archive_bytes, CAPABILITY_OUTPUT_PATH)
            )
            binding = BindingDefinition.from_mapping(
                _read_json_member(archive_bytes, BINDING_OUTPUT_PATH)
            )
            contracts.append(capability)
            bindings.append(binding)
            deliveries.extend(verified.binding_deliveries)

        ui_requirements = [
            item
            for item in compilation.get("requirements") or []
            if isinstance(item, Mapping)
            and item.get("capability_ref") == "capability:application.ui.render"
        ]
        if ui_requirements:
            ui_packages = scenario_packages
            application_kind, separator, application_id = application_ref.partition(":")
            if separator and application_kind == "scenario":
                matching_packages = [
                    item for item in scenario_packages if item.artifact_id == application_id
                ]
                if matching_packages:
                    ui_packages = matching_packages
            if len(ui_packages) != 1:
                raise NativeApplicationCBSAdmissionError(
                    "application.ui.render requires one exact entrypoint scenario "
                    "package with webui.json"
                )
            capability, binding, delivery = _ui_provider(ui_packages[0])
            contracts.append(capability)
            bindings.append(binding)
            deliveries.append(delivery)

        catalog = self.portable_catalog
        for record in (*contracts, *bindings, *deliveries):
            catalog.put(record)

        features = sorted(
            {
                str(feature)
                for binding in bindings
                for feature in binding.to_dict()["environment_constraints"].get(
                    "provider_features", []
                )
            }
        )
        authorities = sorted(
            {
                *[str(item) for item in release_plan.release.permissions],
                *[
                    str(authority)
                    for binding in bindings
                    for authority in binding.to_dict()["authority_requirements"]
                ],
                *[
                    str(authority)
                    for requirement in compilation.get("requirements") or []
                    if isinstance(requirement, Mapping)
                    for authority in dict(requirement.get("policy_constraints") or {}).get(
                        "required_authorities", []
                    )
                ],
            }
        )
        profile_ref = str(
            dict(compilation.get("environment_target") or {}).get("profile_ref")
            or "profile:local/default"
        )
        profile = EnvironmentProfile.create(
            profile_ref=profile_ref,
            profile_class="local",
            modes=("production",),
            provider_features=features,
            guarantees={
                "consistency": [],
                "durability": [],
                "isolation": [],
            },
            authorities=authorities,
        )
        # EnvironmentProfile is an installation-local observation.  The exact
        # snapshot and digest are embedded in this admission below, but the
        # stable ``profile:local/default`` identity must not enter the portable
        # contract catalog: different Applications legitimately observe
        # different provider/authority subsets on the same node.

        delivery_by_definition = {
            item.binding_definition_digest: item for item in deliveries
        }
        instances = []
        for binding in bindings:
            delivery = delivery_by_definition.get(binding.digest)
            if delivery is None:
                continue
            stable = (
                f"binding-instance:candidate/{release_digest.removeprefix('sha256:')[:16]}/"
                f"{_token(binding.binding_definition_ref)}"
            )
            instances.append(
                BindingInstance.create(
                    binding_instance_ref=stable,
                    revision=1,
                    predecessor_digest=None,
                    workspace_ref=workspace_ref,
                    tenant_ref=None,
                    binding_definition_ref=binding.binding_definition_ref,
                    binding_definition_digest=binding.digest,
                    delivery_digest=delivery.digest,
                    environment_profile_ref=profile.profile_ref,
                    environment_profile_digest=profile.digest,
                    mode="production",
                    local_binding_ref=(
                        f"package:{delivery.package_digest}/"
                        f"{delivery.to_dict()['logical_entrypoint']}"
                    ),
                    authority_epoch=1,
                )
            )

        created_at = self.now().astimezone(UTC).replace(microsecond=0).isoformat()
        context = dict(evidence_context or {})
        release_evidence = {
            "validation_evidence": release_plan.release.to_dict().get(
                "validation_evidence", []
            ),
            "validation_evidence_refs": release_plan.release.to_dict().get(
                "validation_evidence_refs", []
            ),
            "context": context,
        }
        release_evidence_digest = canonical_payload_digest(release_evidence)
        claims: list[EvidenceClaim] = []
        assessments: list[EvidenceAssessment] = []
        for capability, binding in zip(contracts, bindings, strict=True):
            claim_token = _key(
                ":".join(
                    (
                        release_digest,
                        str(compilation["compilation_digest"]),
                        capability.digest,
                        binding.digest,
                        release_evidence_digest,
                        created_at,
                    )
                )
            )[:24]
            claim = EvidenceClaim.create(
                claim_ref=f"evidence-claim:application-release/{claim_token}",
                claim_kind="capability_conformance",
                subjects=(
                    {
                        "kind": "capability_contract",
                        "ref": capability.capability_ref,
                        "digest": capability.digest,
                    },
                    {
                        "kind": "binding_definition",
                        "ref": binding.binding_definition_ref,
                        "digest": binding.digest,
                    },
                ),
                environment={
                    "profile_ref": profile.profile_ref,
                    "profile_digest": profile.digest,
                },
                dependencies=(
                    {
                        "ref": f"project-release:{release_plan.release.project_id}",
                        "observed_version": release_plan.release.version,
                        "fingerprint": release_digest,
                    },
                ),
                suite_digest=canonical_payload_digest(
                    {"contract": capability.digest, "binding": binding.digest}
                ),
                evidence_digest=release_evidence_digest,
                provenance={
                    "issuer": "adaos:application-release-admission",
                    "runner": "trusted-release-validation",
                    "run_id": release_digest,
                },
                issued_at=created_at,
                freshness={
                    "max_age_seconds": 2_592_000,
                    "invalidated_by": [
                        "dependency_change",
                        "environment_change",
                        "revocation",
                    ],
                },
                result="verified",
                redaction={"portable": False, "omitted_fields": ["credentials"]},
                portability_scope="local",
            )
            assessment = EvidenceAssessment.create(
                assessment_ref=f"evidence-assessment:application-release/{claim_token}",
                claim_ref=claim.claim_ref,
                claim_digest=claim.digest,
                evaluated_at=created_at,
                policy_digest=canonical_payload_digest(
                    {
                        "policy": "application-release-cbs-admission-v1",
                        "release_digest": release_digest,
                    }
                ),
                status="admissible",
            )
            claims.append(claim)
            assessments.append(assessment)
            catalog.put(claim)

        resolver = SemanticResolver(now=self.now)
        planner = ResolutionPlanner(now=self.now)
        resolutions = []
        plans = []
        unresolved = []
        requirements = [
            ApplicationRequirement.from_mapping(item)
            for item in compilation.get("requirements") or []
        ]
        for requirement in requirements:
            try:
                resolution = resolver.resolve(
                    requirement,
                    semantic_revision_digest=str(compilation["semantic_revision_digest"]),
                    target_mode="production",
                    capability_contracts=contracts,
                    state_contracts=(),
                    binding_definitions=bindings,
                    deliveries=deliveries,
                    environment_profile=profile,
                    binding_instances=instances,
                    state_spaces=(),
                    relations=(),
                    evidence_claims=claims,
                    evidence_assessments=assessments,
                    package_resolver=lambda _candidate, exact=release_plan: exact,
                )
            except ResolutionFailure as exc:
                unresolved.append(
                    {
                        "requirement_ref": requirement.requirement_ref,
                        "requirement_digest": requirement.digest,
                        "rejections": [item.to_dict() for item in exc.rejections],
                    }
                )
                continue
            plan = planner.build(resolution, current_lock=None)
            resolutions.append(resolution.to_dict())
            plans.append(plan.to_dict())

        record: dict[str, Any] = {
            "schema": CBS_ADMISSION_SCHEMA,
            "application_ref": application_ref,
            "compilation_digest": str(compilation["compilation_digest"]),
            "semantic_revision_digest": str(compilation["semantic_revision_digest"]),
            "project_release_digest": release_digest,
            "workspace_ref": workspace_ref,
            "environment_profile": profile.to_dict(),
            "requirements_total": len(requirements),
            "requirements_resolved": len(resolutions),
            "status": "admitted" if len(resolutions) == len(requirements) else "unresolved",
            "resolutions": resolutions,
            "plans": plans,
            "unresolved": unresolved,
            "evidence": [item.to_dict() for item in claims],
            "evidence_assessments": [item.to_dict() for item in assessments],
            "activation": {
                "status": "not_started",
                "authority_changed": False,
            },
            "created_at": created_at,
        }
        record["admission_digest"] = canonical_payload_digest(record)
        validated = self._validate_record(record, application_ref=application_ref)
        self._put(validated)
        return validated

    def _put(self, record: Mapping[str, Any]) -> None:
        application_ref = str(record["application_ref"])
        digest = str(record["admission_digest"])
        directory = self.root / _key(application_ref)
        record_path = directory / "records" / f"{digest.removeprefix('sha256:')}.json"
        pointer_path = directory / "latest.json"
        with mutation_lock(self.writer_lock_path):
            if record_path.is_file():
                existing = json.loads(record_path.read_text(encoding="utf-8"))
                if existing != dict(record):
                    raise NativeApplicationCBSAdmissionError(
                        "CBS admission digest collision"
                    )
            else:
                atomic_write_json(record_path, record)
            atomic_write_json(
                pointer_path,
                {
                    "schema": CBS_ADMISSION_POINTER_SCHEMA,
                    "application_ref": application_ref,
                    "admission_digest": digest,
                    "compilation_digest": record["compilation_digest"],
                    "project_release_digest": record["project_release_digest"],
                },
            )

    @staticmethod
    def _validate_record(
        value: Mapping[str, Any], *, application_ref: str
    ) -> dict[str, Any]:
        record = dict(value)
        if record.get("schema") != CBS_ADMISSION_SCHEMA:
            raise NativeApplicationCBSAdmissionError("unsupported CBS admission schema")
        if record.get("application_ref") != application_ref:
            raise NativeApplicationCBSAdmissionError("CBS admission identity mismatch")
        expected = str(record.get("admission_digest") or "")
        unsigned = dict(record)
        unsigned.pop("admission_digest", None)
        if expected != canonical_payload_digest(unsigned):
            raise NativeApplicationCBSAdmissionError("CBS admission digest mismatch")
        total = int(record.get("requirements_total") or 0)
        resolved = int(record.get("requirements_resolved") or 0)
        if total < 1 or resolved < 0 or resolved > total:
            raise NativeApplicationCBSAdmissionError("invalid CBS requirement counters")
        if (record.get("status") == "admitted") != (resolved == total):
            raise NativeApplicationCBSAdmissionError("CBS admission status is inconsistent")
        return record


__all__ = [
    "CBS_ADMISSION_SCHEMA",
    "NativeApplicationCBSAdmissionError",
    "NativeApplicationCBSAdmissionService",
]
