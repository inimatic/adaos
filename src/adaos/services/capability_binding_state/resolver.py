"""Pure bounded semantic resolution followed by exact package admission."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

from packaging.version import Version

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    ApplicationResolution,
    BindingDefinition,
    BindingDelivery,
    BindingInstance,
    CapabilityContract,
    EnvironmentProfile,
    EvidenceAssessment,
    EvidenceClaim,
    StateAccessRelation,
    StateContract,
    StateSpace,
    version_satisfies,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.capability_binding_state.local_state import (
    StateAttachmentError,
    validate_state_attachment,
)


@dataclass(frozen=True, slots=True)
class ResolutionRejection:
    code: str
    message: str
    subject_ref: str | None = None

    def to_dict(self) -> dict[str, str]:
        value = {"code": self.code, "message": self.message}
        if self.subject_ref:
            value["subject_ref"] = self.subject_ref
        return value


class ResolutionFailure(RuntimeError):
    def __init__(self, rejections: Iterable[ResolutionRejection]) -> None:
        self.rejections = tuple(rejections)
        message = "; ".join(f"{item.code}: {item.message}" for item in self.rejections)
        super().__init__(message or "no satisfying semantic candidate")


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    capability_contract: CapabilityContract
    state_contracts: tuple[StateContract, ...]
    binding_definition: BindingDefinition
    delivery: BindingDelivery
    binding_instance: BindingInstance
    state_spaces: tuple[StateSpace, ...]
    relations: tuple[StateAccessRelation, ...]
    effective_guarantees: tuple[Mapping[str, tuple[str, ...]], ...]

    @property
    def sort_key(self) -> tuple[Version, str, str, str]:
        return (
            Version(self.capability_contract.version),
            self.binding_definition.binding_definition_ref,
            self.binding_definition.digest,
            self.delivery.digest,
        )


ExactPackageResolver = Callable[[SemanticCandidate], ReleasePlan]


class SemanticResolver:
    """Resolve one bounded ApplicationRequirement without mutating local state."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))

    def semantic_viability(
        self,
        requirement: ApplicationRequirement,
        contracts: Iterable[CapabilityContract],
    ) -> dict[str, object]:
        value = requirement.to_dict()
        matching = sorted(
            (
                item
                for item in contracts
                if item.capability_ref == requirement.capability_ref
                and version_satisfies(item.version, value["contract_range"])
            ),
            key=lambda item: (Version(item.version), item.digest),
            reverse=True,
        )
        return {
            "target": "semantic",
            "viable": bool(matching),
            "capability_contracts": [item.digest for item in matching],
            "requirement_digest": requirement.digest,
        }

    def resolve(
        self,
        requirement: ApplicationRequirement,
        *,
        semantic_revision_digest: str,
        target_mode: str,
        capability_contracts: Iterable[CapabilityContract],
        state_contracts: Iterable[StateContract],
        binding_definitions: Iterable[BindingDefinition],
        deliveries: Iterable[BindingDelivery],
        environment_profile: EnvironmentProfile,
        binding_instances: Iterable[BindingInstance],
        state_spaces: Iterable[StateSpace],
        relations: Iterable[StateAccessRelation],
        evidence_claims: Iterable[EvidenceClaim],
        evidence_assessments: Iterable[EvidenceAssessment],
        package_resolver: ExactPackageResolver,
    ) -> ApplicationResolution:
        rejections: list[ResolutionRejection] = []
        candidates = self._semantic_candidates(
            requirement,
            target_mode=target_mode,
            capability_contracts=tuple(capability_contracts),
            state_contracts=tuple(state_contracts),
            binding_definitions=tuple(binding_definitions),
            deliveries=tuple(deliveries),
            environment_profile=environment_profile,
            binding_instances=tuple(binding_instances),
            state_spaces=tuple(state_spaces),
            relations=tuple(relations),
            rejections=rejections,
        )
        claims = tuple(evidence_claims)
        assessments = tuple(evidence_assessments)
        for candidate in sorted(candidates, key=lambda item: item.sort_key, reverse=True):
            try:
                plan = package_resolver(candidate)
                self._validate_package_plan(candidate, plan)
            except Exception as exc:
                rejections.append(
                    ResolutionRejection(
                        "package_conflict",
                        str(exc),
                        candidate.binding_definition.binding_definition_ref,
                    )
                )
                continue
            admitted_evidence, rejection = self._admit_evidence(
                requirement,
                candidate,
                environment_profile,
                claims,
                assessments,
            )
            if rejection is not None:
                rejections.append(rejection)
                continue
            return self._resolution(
                requirement,
                semantic_revision_digest=semantic_revision_digest,
                target_mode=target_mode,
                profile=environment_profile,
                candidate=candidate,
                plan=plan,
                evidence=admitted_evidence,
                rejections=rejections,
            )
        if not rejections:
            rejections.append(
                ResolutionRejection(
                    "unmet_requirement",
                    f"no contract satisfies {requirement.capability_ref}",
                    requirement.requirement_ref,
                )
            )
        raise ResolutionFailure(rejections)

    def _semantic_candidates(
        self,
        requirement: ApplicationRequirement,
        *,
        target_mode: str,
        capability_contracts: tuple[CapabilityContract, ...],
        state_contracts: tuple[StateContract, ...],
        binding_definitions: tuple[BindingDefinition, ...],
        deliveries: tuple[BindingDelivery, ...],
        environment_profile: EnvironmentProfile,
        binding_instances: tuple[BindingInstance, ...],
        state_spaces: tuple[StateSpace, ...],
        relations: tuple[StateAccessRelation, ...],
        rejections: list[ResolutionRejection],
    ) -> tuple[SemanticCandidate, ...]:
        requirement_value = requirement.to_dict()
        target = requirement_value["environment_target"]
        profile = environment_profile.to_dict()
        if target["profile_ref"] != environment_profile.profile_ref:
            rejections.append(
                ResolutionRejection(
                    "environment_mismatch",
                    "requirement targets another EnvironmentProfile",
                    environment_profile.profile_ref,
                )
            )
            return ()
        if target_mode not in target["allowed_modes"] or target_mode not in profile["modes"]:
            rejections.append(
                ResolutionRejection(
                    "environment_mismatch",
                    f"target mode is not admitted: {target_mode}",
                    environment_profile.profile_ref,
                )
            )
            return ()
        required_authorities = set(
            requirement_value["policy_constraints"].get("required_authorities") or ()
        )
        if not required_authorities.issubset(set(profile["authorities"])):
            rejections.append(
                ResolutionRejection(
                    "missing_authority",
                    "EnvironmentProfile does not grant all required authorities",
                    environment_profile.profile_ref,
                )
            )
            return ()
        matching_contracts = [
            item
            for item in capability_contracts
            if item.capability_ref == requirement.capability_ref
            and version_satisfies(item.version, requirement_value["contract_range"])
        ]
        if not matching_contracts:
            rejections.append(
                ResolutionRejection(
                    "unmet_requirement",
                    "no CapabilityContract satisfies the requested range",
                    requirement.capability_ref,
                )
            )
            return ()
        result: list[SemanticCandidate] = []
        for contract in matching_contracts:
            contract_value = contract.to_dict()
            definitions = [
                item
                for item in binding_definitions
                if item.capability_ref == contract.capability_ref
                and item.to_dict()["capability_version"] == contract.version
            ]
            for definition in definitions:
                definition_value = definition.to_dict()
                constraints = definition_value["environment_constraints"]
                if target_mode not in definition_value["modes"]:
                    continue
                if profile["profile_class"] not in constraints["profile_classes"]:
                    continue
                if not set(constraints["provider_features"]).issubset(
                    set(profile["provider_features"])
                ):
                    continue
                if not set(definition_value["authority_requirements"]).issubset(
                    set(profile["authorities"])
                ):
                    continue
                matching_deliveries = [
                    item
                    for item in deliveries
                    if item.binding_definition_digest == definition.digest
                ]
                for delivery in matching_deliveries:
                    instances = [
                        item
                        for item in binding_instances
                        if item.to_dict()["binding_definition_digest"] == definition.digest
                        and item.to_dict()["delivery_digest"] == delivery.digest
                        and item.to_dict()["environment_profile_digest"] == environment_profile.digest
                        and item.to_dict()["mode"] == target_mode
                    ]
                    if not instances:
                        rejections.append(
                            ResolutionRejection(
                                "unavailable_instance",
                                "no local BindingInstance materializes the delivery",
                                definition.binding_definition_ref,
                            )
                        )
                        continue
                    for instance in instances:
                        selected_states: list[StateContract] = []
                        selected_spaces: list[StateSpace] = []
                        selected_relations: list[StateAccessRelation] = []
                        guarantees: list[Mapping[str, tuple[str, ...]]] = []
                        failed = False
                        for port in contract_value["state_ports"]:
                            eligible_states = [
                                item
                                for item in state_contracts
                                if item.state_contract_ref == port["contract_ref"]
                                and version_satisfies(item.version, port["contract_range"])
                            ]
                            attached = False
                            for state_contract in eligible_states:
                                spaces = [
                                    item
                                    for item in state_spaces
                                    if item.to_dict()["state_contract_digest"] == state_contract.digest
                                    and item.to_dict()["custodian_binding_instance_ref"] == instance.stable_ref
                                ]
                                for space in spaces:
                                    linked = [
                                        item
                                        for item in relations
                                        if item.to_dict()["port_id"] == port["port_id"]
                                        and item.to_dict()["binding_instance_revision_digest"] == instance.digest
                                        and item.to_dict()["state_space_revision_digest"] == space.digest
                                    ]
                                    for relation in linked:
                                        try:
                                            effective = validate_state_attachment(
                                                capability_contract=contract,
                                                port_id=port["port_id"],
                                                state_contract=state_contract,
                                                binding_definition=definition,
                                                binding_instance=instance,
                                                state_space=space,
                                                environment_profile=environment_profile,
                                                relation=relation,
                                            )
                                        except StateAttachmentError:
                                            continue
                                        selected_states.append(state_contract)
                                        selected_spaces.append(space)
                                        selected_relations.append(relation)
                                        guarantees.append(effective)
                                        attached = True
                                        break
                                    if attached:
                                        break
                                if attached:
                                    break
                            if not attached:
                                rejections.append(
                                    ResolutionRejection(
                                        "insufficient_guarantee",
                                        f"no compatible StateSpace satisfies port {port['port_id']}",
                                        definition.binding_definition_ref,
                                    )
                                )
                                failed = True
                                break
                        if failed:
                            continue
                        result.append(
                            SemanticCandidate(
                                capability_contract=contract,
                                state_contracts=tuple(selected_states),
                                binding_definition=definition,
                                delivery=delivery,
                                binding_instance=instance,
                                state_spaces=tuple(selected_spaces),
                                relations=tuple(selected_relations),
                                effective_guarantees=tuple(guarantees),
                            )
                        )
        return tuple(result)

    @staticmethod
    def _validate_package_plan(candidate: SemanticCandidate, plan: ReleasePlan) -> None:
        delivered = candidate.delivery.to_dict()["package"]
        matches = [
            item
            for item in plan.packages
            if item.digest == delivered["digest"]
            and item.kind == delivered["kind"]
            and item.artifact_id == delivered["id"]
            and item.version == delivered["version"]
        ]
        if len(matches) != 1:
            raise ValueError("exact package closure does not contain the BindingDelivery package")
        expected_release_digest = plan.release.release_digest or plan.release.computed_digest()
        if not expected_release_digest:
            raise ValueError("exact package resolver returned an unsealed ProjectRelease")

    @staticmethod
    def _admit_evidence(
        requirement: ApplicationRequirement,
        candidate: SemanticCandidate,
        profile: EnvironmentProfile,
        claims: tuple[EvidenceClaim, ...],
        assessments: tuple[EvidenceAssessment, ...],
    ) -> tuple[tuple[tuple[EvidenceClaim, EvidenceAssessment], ...], ResolutionRejection | None]:
        threshold = requirement.to_dict()["evidence_threshold"]
        required_kinds = set(threshold["required_claim_kinds"])
        allow_stale = bool(threshold["allow_stale"])
        assessment_by_claim = {
            item.to_dict()["claim_digest"]: item for item in assessments
        }
        admitted: list[tuple[EvidenceClaim, EvidenceAssessment]] = []
        found_kinds: set[str] = set()
        stale_kinds: set[str] = set()
        state_subject_digests = {item.digest for item in candidate.state_contracts}
        for claim in claims:
            value = claim.to_dict()
            kind = value["claim_kind"]
            if kind not in required_kinds or value["result"] != "verified":
                continue
            if value["environment"]["profile_digest"] != profile.digest:
                continue
            subject_digests = {item["digest"] for item in value["subjects"]}
            if kind == "capability_conformance" and not {
                candidate.capability_contract.digest,
                candidate.binding_definition.digest,
            }.issubset(subject_digests):
                continue
            if kind == "state_compatibility" and not (
                candidate.binding_definition.digest in subject_digests
                and state_subject_digests.issubset(subject_digests)
            ):
                continue
            assessment = assessment_by_claim.get(claim.digest)
            if assessment is None:
                continue
            status = assessment.to_dict()["status"]
            if status == "stale":
                stale_kinds.add(kind)
                if not allow_stale:
                    continue
            elif status != "admissible":
                continue
            found_kinds.add(kind)
            admitted.append((claim, assessment))
        missing = sorted(required_kinds - found_kinds)
        if missing:
            code = "stale_evidence" if set(missing).issubset(stale_kinds) else "missing_evidence"
            return (), ResolutionRejection(
                code,
                f"evidence admission did not satisfy: {', '.join(missing)}",
                candidate.binding_definition.binding_definition_ref,
            )
        return tuple(admitted), None

    def _resolution(
        self,
        requirement: ApplicationRequirement,
        *,
        semantic_revision_digest: str,
        target_mode: str,
        profile: EnvironmentProfile,
        candidate: SemanticCandidate,
        plan: ReleasePlan,
        evidence: tuple[tuple[EvidenceClaim, EvidenceAssessment], ...],
        rejections: list[ResolutionRejection],
    ) -> ApplicationResolution:
        now = self._now().astimezone(timezone.utc).replace(microsecond=0).isoformat()
        token_payload = {
            "semantic_revision_digest": semantic_revision_digest,
            "requirement_digest": requirement.digest,
            "profile_digest": profile.digest,
            "mode": target_mode,
            "binding_definition_digest": candidate.binding_definition.digest,
            "delivery_digest": candidate.delivery.digest,
            "created_at": now,
        }
        token = hashlib.sha256(
            str(sorted(token_payload.items())).encode("utf-8")
        ).hexdigest()[:24]
        delivery = candidate.delivery.to_dict()
        package_closure = [
            {
                "kind": item.kind,
                "id": item.artifact_id,
                "version": item.version,
                "digest": item.digest,
            }
            for item in sorted(plan.packages, key=lambda item: item.key)
        ]
        selected_contracts = [
            {
                "kind": "capability",
                "ref": candidate.capability_contract.capability_ref,
                "version": candidate.capability_contract.version,
                "digest": candidate.capability_contract.digest,
            },
            *(
                {
                    "kind": "state",
                    "ref": item.state_contract_ref,
                    "version": item.version,
                    "digest": item.digest,
                }
                for item in candidate.state_contracts
            ),
        ]
        policy_digest = canonical_payload_digest(
            {
                "policy_constraints": requirement.to_dict()["policy_constraints"],
                "evidence_threshold": requirement.to_dict()["evidence_threshold"],
            }
        )
        return ApplicationResolution.create(
            resolution_ref=f"application-resolution:{token_payload['semantic_revision_digest'].split(':', 1)[-1][:16]}/{target_mode}/{token}",
            semantic_revision_digest=semantic_revision_digest,
            requirement_ref=requirement.requirement_ref,
            requirement_digest=requirement.digest,
            environment_profile_ref=profile.profile_ref,
            environment_profile_digest=profile.digest,
            policy_digest=policy_digest,
            selected_contracts=selected_contracts,
            binding_definition={
                "ref": candidate.binding_definition.binding_definition_ref,
                "digest": candidate.binding_definition.digest,
            },
            project_release_digest=plan.release.release_digest or plan.release.computed_digest(),
            package_closure=package_closure,
            delivery={
                "delivery_digest": candidate.delivery.digest,
                "package_digest": delivery["package"]["digest"],
                "physical_member": delivery["physical_member"],
            },
            binding_instances=(
                {
                    "ref": candidate.binding_instance.stable_ref,
                    "revision_digest": candidate.binding_instance.digest,
                    "authority_epoch": candidate.binding_instance.authority_epoch,
                },
            ),
            provisioning_obligations=(),
            state_attachments=(
                {
                    "port_id": relation.to_dict()["port_id"],
                    "state_space_ref": space.stable_ref,
                    "revision_digest": space.digest,
                    "generation": space.generation,
                    "authority_epoch": space.authority_epoch,
                    "relation_digest": relation.digest,
                }
                for space, relation in zip(candidate.state_spaces, candidate.relations, strict=True)
            ),
            evidence=(
                {
                    "claim_ref": claim.claim_ref,
                    "claim_digest": claim.digest,
                    "assessment_digest": assessment.digest,
                    "status": assessment.to_dict()["status"],
                }
                for claim, assessment in evidence
            ),
            rejection_explanations=(item.to_dict() for item in rejections),
            created_at=now,
        )


__all__ = [
    "ExactPackageResolver",
    "ResolutionFailure",
    "ResolutionRejection",
    "SemanticCandidate",
    "SemanticResolver",
]
