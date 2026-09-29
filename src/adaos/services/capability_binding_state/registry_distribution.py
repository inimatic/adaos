"""Cold-cache thin distribution over the existing semantic/package registry.

This service deliberately stops before local materialization.  It may hydrate
portable records and immutable package bytes, then returns exact
``ApplicationResolution`` records.  It never creates BindingInstances,
StateSpaces, ResolutionPlans, WorkspaceLocks, credentials, or activation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    ApplicationResolution,
    BindingDelivery,
    EnvironmentProfile,
    EvidenceAssessment,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan

from .catalog import PortableContractCatalog
from .registry_projection import SemanticRegistryProjection


THIN_DISTRIBUTION_RECEIPT_SCHEMA = "adaos.semantic_registry.thin_resolution.v1"


class ThinDistributionError(RuntimeError):
    """Thin distribution failed before an ApplicationResolution was admitted."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


class ThinDistributionRemote(Protocol):
    """Existing immutable release/package transport boundary."""

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...

    def fetch_package(self, package: Any) -> bytes: ...


class ReleaseProvenanceAdmission(Protocol):
    """Existing artifact provenance admission boundary."""

    def verify_release_plan(self, plan: ReleasePlan) -> Mapping[str, Any]: ...


def _release_digest(plan: ReleasePlan) -> str:
    return str(plan.release.release_digest or plan.release.computed_digest())


def _package_identity(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("kind") or ""),
        str(value.get("id") or ""),
        str(value.get("version") or ""),
        str(value.get("digest") or ""),
    )


def _candidate_record_digests(candidate: Mapping[str, Any]) -> set[str]:
    result = {
        str(candidate["capability_contract"]["digest"]),
        str(candidate["binding_definition"]["digest"]),
        str(candidate["delivery"]["digest"]),
    }
    result.update(
        str(item["digest"])
        for field in ("supporting_contracts", "state_contracts")
        for item in candidate[field]
    )
    result.update(str(item["claim_digest"]) for item in candidate["evidence"])
    return result


def _selected_contracts(candidate: Mapping[str, Any]) -> list[dict[str, str]]:
    capability = dict(candidate["capability_contract"])
    result = [
        {
            "kind": "capability",
            "ref": str(capability["ref"]),
            "version": str(capability["version"]),
            "digest": str(capability["digest"]),
        }
    ]
    result.extend(
        {
            "kind": "capability",
            "ref": str(item["ref"]),
            "version": str(item["version"]),
            "digest": str(item["digest"]),
        }
        for item in candidate["supporting_contracts"]
    )
    result.extend(
        {
            "kind": "state",
            "ref": str(item["ref"]),
            "version": str(item["version"]),
            "digest": str(item["digest"]),
        }
        for item in candidate["state_contracts"]
    )
    return result


@dataclass(slots=True)
class ThinSemanticDistributionResolver:
    """Resolve one exact thin Application release from a cold local cache."""

    projection: SemanticRegistryProjection
    package_store: ContentAddressedPackageStore
    remote: ThinDistributionRemote
    provenance: ReleaseProvenanceAdmission

    @property
    def portable_catalog(self) -> PortableContractCatalog:
        return PortableContractCatalog(
            self.projection.state_dir / "capability-binding-state" / "portable"
        )

    def resolve(
        self,
        *,
        project_id: str,
        project_release_digest: str,
        query: Mapping[str, Any],
        registry_revision: str,
    ) -> dict[str, Any]:
        """Acquire exact immutable inputs and emit resolutions, never authority."""

        request = dict(query)
        query_result = self.projection.query(
            request,
            registry_revision=registry_revision,
        )
        if query_result["status"] != "matched" or any(
            item["status"] != "matched" for item in query_result["requirements"]
        ):
            raise ThinDistributionError(
                "semantic_query_unmatched",
                "semantic registry query does not satisfy every Application requirement",
            )

        semantic_release = self.projection.read_application_release(
            project_id,
            project_release_digest,
        )
        release_digest = str(semantic_release["project_release_digest"])
        application_requirements = tuple(
            ApplicationRequirement.from_mapping(item)
            for item in semantic_release["requirements"]
        )
        requested_requirements = tuple(
            ApplicationRequirement.from_mapping(item)
            for item in request["requirements"]
        )
        application_by_digest = {
            item.digest: item for item in application_requirements
        }
        requested_by_digest = {item.digest: item for item in requested_requirements}
        if (
            len(application_by_digest) != len(application_requirements)
            or len(requested_by_digest) != len(requested_requirements)
            or set(application_by_digest) != set(requested_by_digest)
        ):
            raise ThinDistributionError(
                "application_requirements_mismatch",
                "query requirements do not exactly match the immutable Application release",
            )

        portable_release_digests = {
            str(digest)
            for digests in semantic_release["portable_artifacts"].values()
            for digest in digests
        }
        selected: list[tuple[ApplicationRequirement, dict[str, Any]]] = []
        selected_record_digests: set[str] = set()
        rows_by_digest = {
            str(item["requirement_digest"]): item
            for item in query_result["requirements"]
        }
        for requirement in application_requirements:
            row = rows_by_digest.get(requirement.digest)
            if row is None:
                raise ThinDistributionError(
                    "application_requirements_mismatch",
                    f"query omitted {requirement.requirement_ref}",
                )
            candidates = [
                dict(item)
                for item in row["candidates"]
                if release_digest in item["publisher_release_digests"]
                and _candidate_record_digests(item).issubset(
                    portable_release_digests
                )
            ]
            if not candidates:
                raise ThinDistributionError(
                    "release_candidate_unavailable",
                    "exact Application release did not publish an eligible candidate for "
                    + requirement.requirement_ref,
                )
            candidate = candidates[0]
            selected.append((requirement, candidate))
            selected_record_digests.update(_candidate_record_digests(candidate))

        try:
            plan = self.remote.get_release(str(project_id), release_digest)
        except Exception as exc:
            raise ThinDistributionError(
                "release_fetch_failed", "cannot fetch exact ProjectRelease"
            ) from exc
        if (
            plan.release.project_id != str(project_id)
            or _release_digest(plan) != release_digest
            or plan.release.version != semantic_release["version"]
        ):
            raise ThinDistributionError(
                "release_identity_mismatch",
                "remote ProjectRelease differs from the semantic Application release",
            )

        published_closure = sorted(
            (
                dict(item)
                for item in semantic_release["distribution"]["resolved"].get(
                    "package_closure", ()
                )
            ),
            key=_package_identity,
        )
        exact_closure = sorted(
            (item.to_dict() for item in plan.packages),
            key=_package_identity,
        )
        if (
            semantic_release["distribution"]["resolved"].get(
                "project_release_digest"
            )
            != release_digest
            or published_closure != exact_closure
        ):
            raise ThinDistributionError(
                "package_closure_mismatch",
                "semantic projection and exact ProjectRelease package closures differ",
            )
        packages_by_identity = {
            (item.kind, item.artifact_id, item.version, item.digest): item
            for item in plan.packages
        }
        for _requirement, candidate in selected:
            package = dict(candidate["delivery"]["package"])
            if _package_identity(package) not in packages_by_identity:
                raise ThinDistributionError(
                    "delivery_package_missing",
                    "exact ProjectRelease does not contain a selected BindingDelivery package",
                )

        try:
            provenance_receipt = dict(self.provenance.verify_release_plan(plan))
        except Exception as exc:
            raise ThinDistributionError(
                "provenance_rejected", "exact ProjectRelease provenance was rejected"
            ) from exc
        if provenance_receipt.get("status") != "verified":
            raise ThinDistributionError(
                "provenance_rejected", "exact ProjectRelease provenance did not verify"
            )
        provenance_receipt_digest = canonical_payload_digest(provenance_receipt)

        package_acquisition: list[dict[str, str]] = []
        for package in sorted(plan.packages, key=lambda item: item.key):
            if self.package_store.has(package.digest):
                try:
                    verified = self.package_store.verify(package.digest)
                except Exception as exc:
                    raise ThinDistributionError(
                        "cached_package_invalid",
                        f"cached package failed verification: {package.key}",
                    ) from exc
                source = "cache"
            else:
                try:
                    archive = self.remote.fetch_package(package)
                    verified = self.package_store.put(
                        archive,
                        expected_digest=package.digest,
                    )
                except Exception as exc:
                    raise ThinDistributionError(
                        "package_fetch_failed",
                        f"cannot acquire exact package: {package.key}",
                    ) from exc
                source = "remote"
            if verified.ref != package:
                raise ThinDistributionError(
                    "package_identity_mismatch",
                    f"verified package identity differs: {package.key}",
                )
            package_acquisition.append(
                {
                    "kind": package.kind,
                    "id": package.artifact_id,
                    "version": package.version,
                    "digest": package.digest,
                    "source": source,
                }
            )

        portable_import = self.projection.import_to_local_catalog(
            digests=selected_record_digests,
            import_applications=False,
        )
        if set(portable_import["record_digests"]) != selected_record_digests:
            raise ThinDistributionError(
                "portable_import_incomplete",
                "local portable cache did not import the selected exact records",
            )

        profile = EnvironmentProfile.from_mapping(request["environment_profile"])
        target_mode = str(request["target_mode"])
        evaluated_at = str(request["evidence_inputs"]["as_of"])
        package_closure = [
            {
                "kind": item.kind,
                "id": item.artifact_id,
                "version": item.version,
                "digest": item.digest,
            }
            for item in sorted(plan.packages, key=lambda item: item.key)
        ]
        resolutions: list[ApplicationResolution] = []
        for requirement, candidate in selected:
            requirement_value = requirement.to_dict()
            policy_digest = canonical_payload_digest(
                {
                    "policy_constraints": requirement_value["policy_constraints"],
                    "evidence_threshold": requirement_value["evidence_threshold"],
                    "registry_policy_inputs": request["policy_inputs"],
                    "registry_evidence_policy": {
                        key: request["evidence_inputs"][key]
                        for key in ("required_claim_kinds", "accepted_results")
                    },
                    "target_mode": target_mode,
                }
            )
            evidence: list[dict[str, str]] = []
            for item in candidate["evidence"]:
                status = (
                    "stale" if item["status"] == "stale_allowed" else "admissible"
                )
                token = str(item["claim_digest"]).removeprefix("sha256:")[:24]
                assessment = EvidenceAssessment.create(
                    assessment_ref=f"evidence-assessment:registry/{token}",
                    claim_ref=str(item["claim_ref"]),
                    claim_digest=str(item["claim_digest"]),
                    evaluated_at=evaluated_at,
                    policy_digest=policy_digest,
                    status=status,
                    reasons=("snapshot-pinned semantic registry admission",),
                )
                evidence.append(
                    {
                        "claim_ref": str(item["claim_ref"]),
                        "claim_digest": str(item["claim_digest"]),
                        "assessment_digest": assessment.digest,
                        "status": status,
                    }
                )
            delivery_record = BindingDelivery.from_mapping(
                self.portable_catalog.load_mapping(
                    str(candidate["delivery"]["digest"])
                )
            )
            delivery_value = delivery_record.to_dict()
            resolution_identity = canonical_payload_digest(
                {
                    "query_digest": query_result["query_digest"],
                    "requirement_digest": requirement.digest,
                    "candidate_digest": candidate["candidate_digest"],
                    "project_release_digest": release_digest,
                    "profile_digest": profile.digest,
                }
            ).removeprefix("sha256:")
            obligations: list[dict[str, Any]] = [
                {
                    "kind": "binding_instance",
                    "status": "required_before_activation",
                    "binding_definition_digest": candidate["binding_definition"][
                        "digest"
                    ],
                    "delivery_digest": candidate["delivery"]["digest"],
                }
            ]
            obligations.extend(
                {
                    "kind": "state_space_attachment",
                    "status": "required_before_activation",
                    "state_contract_ref": item["ref"],
                    "state_contract_digest": item["digest"],
                }
                for item in candidate["state_contracts"]
            )
            resolutions.append(
                ApplicationResolution.create(
                    resolution_ref=(
                        "application-resolution:registry/"
                        + resolution_identity[:32]
                    ),
                    semantic_revision_digest=str(
                        semantic_release["semantic_revision_digest"]
                    ),
                    requirement_ref=requirement.requirement_ref,
                    requirement_digest=requirement.digest,
                    environment_profile_ref=profile.profile_ref,
                    environment_profile_digest=profile.digest,
                    policy_digest=policy_digest,
                    selected_contracts=_selected_contracts(candidate),
                    binding_definition={
                        "ref": str(candidate["binding_definition"]["ref"]),
                        "digest": str(candidate["binding_definition"]["digest"]),
                    },
                    project_release_digest=release_digest,
                    package_closure=package_closure,
                    delivery={
                        "delivery_digest": delivery_record.digest,
                        "package_digest": str(
                            delivery_value["package"]["digest"]
                        ),
                        "physical_member": str(delivery_value["physical_member"]),
                    },
                    binding_instances=(),
                    provisioning_obligations=obligations,
                    state_attachments=(),
                    evidence=evidence,
                    rejection_explanations=(),
                    created_at=evaluated_at,
                )
            )

        result: dict[str, Any] = {
            "schema": THIN_DISTRIBUTION_RECEIPT_SCHEMA,
            "status": "resolved",
            "project_id": str(project_id),
            "application_ref": str(semantic_release["application_ref"]),
            "project_release_digest": release_digest,
            "semantic_revision_digest": str(
                semantic_release["semantic_revision_digest"]
            ),
            "application_projection_digest": str(
                semantic_release["projection_digest"]
            ),
            "query_result_digest": str(query_result["result_digest"]),
            "snapshot": dict(query_result["snapshot"]),
            "provenance_receipt_digest": provenance_receipt_digest,
            "portable_record_digests": sorted(selected_record_digests),
            "package_acquisition": package_acquisition,
            "resolutions": [item.to_dict() for item in resolutions],
            "activation_performed": False,
            "local_authority_created": False,
        }
        result["receipt_digest"] = canonical_payload_digest(result)
        return result


__all__ = [
    "ReleaseProvenanceAdmission",
    "THIN_DISTRIBUTION_RECEIPT_SCHEMA",
    "ThinDistributionError",
    "ThinDistributionRemote",
    "ThinSemanticDistributionResolver",
]
