from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import threading

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from adaos.domain.capability_binding_state import (
    BindingDelivery,
    BindingInstance,
    EvidenceAssessment,
    EvidenceClaim,
    StateAccessRelation,
    StateSpace,
)
from adaos.services.artifact_pipeline import (
    PackageCatalog,
    build_artifact_package,
    build_project_release,
)
from adaos.services.capability_binding_state import (
    BookingConflict,
    BookingReservationService,
    InMemoryAuditState,
    InMemoryAvailabilityState,
    InMemoryBookingState,
    IdempotencyConflict,
    InvalidBookingInterval,
    ProductionReservationProvider,
    ProviderTimeout,
    ReconciliationRequired,
    ResolutionFailure,
    ResolutionPlanner,
    SemanticResolver,
    SimulationReservationProvider,
    StaleAvailability,
)
from adaos.services.capability_binding_state.reference_booking import booking_contracts


NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
INTERVALS = {
    "room:blue": (
        ("2026-09-24T09:00:00+00:00", "2026-09-24T18:00:00+00:00"),
    )
}


def _command(
    key: str = "reserve-blue-1",
    *,
    starts_at: str = "2026-09-24T10:00:00+00:00",
    ends_at: str = "2026-09-24T11:00:00+00:00",
    customer: str = "customer:one",
) -> dict[str, str]:
    return {
        "idempotency_key": key,
        "resource_ref": "room:blue",
        "customer_ref": customer,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def _service(provider) -> BookingReservationService:
    return BookingReservationService(
        availability=InMemoryAvailabilityState(INTERVALS),
        bookings=InMemoryBookingState(),
        audit=InMemoryAuditState(),
        provider=provider,
        now=lambda: NOW,
    )


def test_booking_contracts_have_three_orthogonal_state_ports_and_composition() -> None:
    contracts = booking_contracts()
    ports = {
        item["port_id"]: item
        for item in contracts.capability.to_dict()["state_ports"]
    }
    assert {key: value["access"] for key, value in ports.items()} == {
        "availability": "reads",
        "bookings": "writes",
        "audit": "appends",
    }
    assert contracts.capability.to_dict()["dependencies"] == [
        {
            "capability_ref": contracts.availability_capability.capability_ref,
            "contract_range": "^1.0.0",
            "optional": False,
        }
    ]
    assert contracts.availability_state.to_dict()["portability_class"] == "external_authoritative"
    assert contracts.booking_state.to_dict()["portability_class"] == "portable"
    assert contracts.audit_state.to_dict()["guarantees"]["isolation"] == ["append_only"]
    serialized = str(contracts.semantic_application).lower()
    for forbidden in ("package", "binding-definition", "provider", "storage", "credential"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "provider_type,provider_ref",
    (
        (SimulationReservationProvider, "simulation:calendar"),
        (ProductionReservationProvider, "external:calendar"),
    ),
)
def test_simulation_and_production_bindings_share_idempotent_contract_scenario(
    provider_type,
    provider_ref: str,
) -> None:
    provider = provider_type(provider_ref=provider_ref)
    service = _service(provider)

    first = service.reserve(_command())
    duplicate = service.reserve(_command())

    assert first["status"] == "confirmed"
    assert first["duplicate"] is False
    assert duplicate["booking_ref"] == first["booking_ref"]
    assert duplicate["duplicate"] is True
    assert provider.reserve_calls == 1
    assert len(service.bookings.records()) == 1
    assert [item["event"] for item in service.audit.events()] == [
        "booking.reserve.started",
        "booking.reserve.confirmed",
    ]


def test_concurrent_overlapping_decisions_serialize_and_compensate_loser() -> None:
    provider = ProductionReservationProvider(
        provider_ref="external:calendar",
        outcomes=("success", "success"),
        barrier=threading.Barrier(2),
    )
    service = _service(provider)
    commands = (
        _command("concurrent-one", customer="customer:one"),
        _command("concurrent-two", customer="customer:two"),
    )

    def reserve(command):
        try:
            return service.reserve(command)
        except Exception as exc:  # the result intentionally captures the losing decision
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(reserve, commands))

    confirmed = [item for item in results if isinstance(item, dict)]
    rejected = [item for item in results if isinstance(item, BookingConflict)]
    assert len(confirmed) == 1
    assert len(rejected) == 1
    assert len(service.bookings.records()) == 1
    assert provider.reserve_calls == 2
    assert provider.compensation_calls == 1
    assert len(provider.active_effects) == 1
    assert "booking.reserve.compensated" in {item["event"] for item in service.audit.events()}


def test_provider_timeout_before_effect_is_retryable_without_partial_state() -> None:
    provider = ProductionReservationProvider(
        provider_ref="external:calendar",
        outcomes=("timeout_before_effect", "success"),
    )
    service = _service(provider)

    with pytest.raises(ProviderTimeout) as caught:
        service.reserve(_command())
    assert caught.value.effect_receipt is None
    assert service.bookings.records() == ()
    assert provider.active_effects == {}

    recovered = service.reserve(_command())
    assert recovered["status"] == "confirmed"
    assert provider.reserve_calls == 2


def test_temporal_bounds_and_idempotency_payload_are_invariants() -> None:
    service = _service(ProductionReservationProvider(provider_ref="external:calendar"))
    with pytest.raises(InvalidBookingInterval, match="starts_at < ends_at"):
        service.reserve(
            _command(
                "invalid-interval",
                starts_at="2026-09-24T10:00:00+00:00",
                ends_at="2026-09-24T10:00:00+00:00",
            )
        )

    service.reserve(_command("stable-idempotency"))
    with pytest.raises(IdempotencyConflict):
        service.reserve(
            _command(
                "stable-idempotency",
                starts_at="2026-09-24T12:00:00+00:00",
                ends_at="2026-09-24T13:00:00+00:00",
            )
        )


def test_timeout_after_effect_is_compensated() -> None:
    provider = ProductionReservationProvider(
        provider_ref="external:calendar",
        outcomes=("timeout_after_effect",),
    )
    service = _service(provider)

    with pytest.raises(ProviderTimeout) as caught:
        service.reserve(_command())

    assert caught.value.effect_receipt is not None
    assert service.bookings.records() == ()
    assert provider.active_effects == {}
    assert provider.compensation_calls == 1
    assert service.audit.events()[-1]["event"] == "booking.reserve.compensated"


def test_failed_compensation_records_reconciliation_and_fences_duplicate() -> None:
    provider = ProductionReservationProvider(
        provider_ref="external:calendar",
        outcomes=("timeout_after_effect",),
        compensation_fails=True,
    )
    service = _service(provider)

    with pytest.raises(ReconciliationRequired) as caught:
        service.reserve(_command())
    assert caught.value.effect_receipt is not None
    assert len(service.bookings.reconciliation_items()) == 1
    assert len(provider.active_effects) == 1
    with pytest.raises(ReconciliationRequired, match="previous attempt"):
        service.reserve(_command())
    assert provider.reserve_calls == 1


def test_stale_availability_after_external_effect_is_compensated() -> None:
    availability = InMemoryAvailabilityState(INTERVALS)
    provider = ProductionReservationProvider(
        provider_ref="external:calendar",
        on_effect=lambda _receipt: availability.replace(INTERVALS),
    )
    service = BookingReservationService(
        availability=availability,
        bookings=InMemoryBookingState(),
        audit=InMemoryAuditState(),
        provider=provider,
        now=lambda: NOW,
    )

    with pytest.raises(StaleAvailability):
        service.reserve(_command())

    assert service.bookings.records() == ()
    assert provider.active_effects == {}
    assert provider.compensation_calls == 1


def test_provider_rebinding_preserves_portable_booking_state_identity_and_records() -> None:
    availability = InMemoryAvailabilityState(INTERVALS)
    bookings = InMemoryBookingState(state_space_ref="state-space:tenant-x/booking-records")
    audit = InMemoryAuditState(state_space_ref="state-space:tenant-x/booking-audit")
    first = BookingReservationService(
        availability=availability,
        bookings=bookings,
        audit=audit,
        provider=ProductionReservationProvider(provider_ref="external:calendar-a"),
        now=lambda: NOW,
    )
    first.reserve(_command("provider-a"))
    identity_before = bookings.state_space_ref
    semantic_before = canonical_payload_digest(booking_contracts().semantic_application)

    second = BookingReservationService(
        availability=availability,
        bookings=bookings,
        audit=audit,
        provider=ProductionReservationProvider(provider_ref="external:calendar-b"),
        now=lambda: NOW,
    )
    second.reserve(
        _command(
            "provider-b",
            starts_at="2026-09-24T12:00:00+00:00",
            ends_at="2026-09-24T13:00:00+00:00",
        )
    )

    assert bookings.state_space_ref == identity_before
    assert len(bookings.records()) == 2
    assert canonical_payload_digest(booking_contracts().semantic_application) == semantic_before


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-cbs-booking-fixtures",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/",),
    )


def _resolution_fixture(tmp_path: Path):
    contracts = booking_contracts()
    package_root = tmp_path / "booking_provider"
    package_root.mkdir()
    (package_root / "skill.yaml").write_text(
        "name: booking_provider\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (package_root / "runtime.py").write_text(
        "def reserve(command):\n    return command\n",
        encoding="utf-8",
    )
    package = build_artifact_package(package_root, kind="skill", source_ref=_source())
    release = build_project_release(
        project_id="booking_provider",
        version="1.0.0",
        source_ref=_source(),
        components=(package.ref,),
        catalog=PackageCatalog(),
    )
    binding = contracts.production_binding
    delivery = BindingDelivery.create(
        binding_definition_ref=binding.binding_definition_ref,
        binding_definition_digest=binding.digest,
        logical_entrypoint=binding.to_dict()["implementation_entrypoint"],
        package={
            "kind": package.ref.kind,
            "id": package.ref.artifact_id,
            "version": package.ref.version,
            "digest": package.ref.digest,
        },
        physical_member="runtime.py",
    )
    instance = BindingInstance.create(
        binding_instance_ref="binding-instance:booking/production",
        revision=1,
        predecessor_digest=None,
        workspace_ref="workspace:booking-proof",
        tenant_ref="tenant:test",
        binding_definition_ref=binding.binding_definition_ref,
        binding_definition_digest=binding.digest,
        delivery_digest=delivery.digest,
        environment_profile_ref=contracts.profile.profile_ref,
        environment_profile_digest=contracts.profile.digest,
        mode="production",
        local_binding_ref="opaque:booking-provider",
        authority_epoch=1,
    )
    states = []
    relations = []
    for port_id, state, access, owner in (
        ("availability", contracts.availability_state, "reads", "external:calendar"),
        ("bookings", contracts.booking_state, "writes", "application:booking-proof"),
        ("audit", contracts.audit_state, "appends", "application:booking-proof"),
    ):
        space = StateSpace.create(
            state_space_ref=f"state-space:booking-proof/{port_id}",
            revision=1,
            predecessor_digest=None,
            state_contract_ref=state.state_contract_ref,
            state_contract_version=state.version,
            state_contract_digest=state.digest,
            workspace_ref="workspace:booking-proof",
            tenant_ref="tenant:test",
            logical_owner_ref=owner,
            lifecycle_authority_ref=owner,
            custodian_binding_instance_ref=instance.stable_ref,
            mutation_authority_ref=("external:calendar" if port_id == "availability" else "workspace:booking-proof"),
            locator_ref=f"opaque:booking/{port_id}",
            generation=1,
            authority_epoch=1,
            portability_class=state.to_dict()["portability_class"],
            schema_locks=state.to_dict()["schema_locks"],
        )
        relation = StateAccessRelation.create(
            relation_ref=f"state-access:booking-proof/{port_id}",
            binding_instance_ref=instance.stable_ref,
            binding_instance_revision_digest=instance.digest,
            state_space_ref=space.stable_ref,
            state_space_revision_digest=space.digest,
            port_id=port_id,
            access=access,
        )
        states.append(space)
        relations.append(relation)
    dependency = {"ref": "external-api:calendar", "observed_version": "1", "fingerprint": "calendar-v1"}
    subjects = (
        {
            "kind": "capability_contract",
            "ref": contracts.capability.capability_ref,
            "digest": contracts.capability.digest,
        },
        {
            "kind": "capability_contract",
            "ref": contracts.availability_capability.capability_ref,
            "digest": contracts.availability_capability.digest,
        },
        {
            "kind": "binding_definition",
            "ref": binding.binding_definition_ref,
            "digest": binding.digest,
        },
    )
    state_subjects = (
        subjects[-1],
        *(
            {
                "kind": "state_contract",
                "ref": state.state_contract_ref,
                "digest": state.digest,
            }
            for state in (
                contracts.availability_state,
                contracts.booking_state,
                contracts.audit_state,
            )
        ),
    )
    pairs = []
    for kind, selected in (
        ("capability_conformance", subjects),
        ("state_compatibility", state_subjects),
    ):
        claim = EvidenceClaim.create(
            claim_ref=f"evidence-claim:booking/{kind}",
            claim_kind=kind,
            subjects=selected,
            environment={
                "profile_ref": contracts.profile.profile_ref,
                "profile_digest": contracts.profile.digest,
            },
            dependencies=(dependency,),
            suite_digest=canonical_payload_digest({"suite": "booking.reserve.v1"}),
            evidence_digest=canonical_payload_digest({"kind": kind, "result": "passed"}),
            provenance={"issuer": "adaos:cbs", "runner": "pytest", "run_id": "booking-proof"},
            issued_at="2026-09-23T09:00:00+00:00",
            freshness={
                "max_age_seconds": 86400,
                "invalidated_by": ["dependency_change", "environment_change"],
            },
            result="verified",
            redaction={"portable": True, "omitted_fields": ["credentials"]},
            portability_scope="portable",
        )
        assessment = EvidenceAssessment.create(
            assessment_ref=f"evidence-assessment:booking/{kind}",
            claim_ref=claim.claim_ref,
            claim_digest=claim.digest,
            evaluated_at="2026-09-23T09:01:00+00:00",
            policy_digest=canonical_payload_digest({"policy": "booking-production"}),
            status="admissible",
        )
        pairs.append((claim, assessment))
    return contracts, release, delivery, instance, tuple(states), tuple(relations), tuple(pairs)


def test_resolver_explains_composition_and_all_state_attachments(tmp_path: Path) -> None:
    contracts, release, delivery, instance, states, relations, pairs = _resolution_fixture(tmp_path)
    resolver = SemanticResolver(now=lambda: NOW)
    resolution = resolver.resolve(
        contracts.requirement,
        semantic_revision_digest=canonical_payload_digest(contracts.semantic_application),
        target_mode="production",
        capability_contracts=(contracts.capability, contracts.availability_capability),
        state_contracts=(
            contracts.availability_state,
            contracts.booking_state,
            contracts.audit_state,
        ),
        binding_definitions=(contracts.production_binding,),
        deliveries=(delivery,),
        environment_profile=contracts.profile,
        binding_instances=(instance,),
        state_spaces=states,
        relations=relations,
        evidence_claims=tuple(item[0] for item in pairs),
        evidence_assessments=tuple(item[1] for item in pairs),
        package_resolver=lambda _candidate: release,
    )
    payload = resolution.to_dict()
    plan = ResolutionPlanner(now=lambda: NOW).build(resolution, current_lock=None)

    assert {item["ref"] for item in payload["selected_contracts"] if item["kind"] == "capability"} == {
        contracts.capability.capability_ref,
        contracts.availability_capability.capability_ref,
    }
    assert {item["port_id"] for item in payload["state_attachments"]} == {
        "availability",
        "bookings",
        "audit",
    }
    assert plan.to_dict()["application_resolution_digest"] == resolution.digest

    stale = tuple(
        EvidenceAssessment.create(
            assessment_ref=item[1].to_dict()["assessment_ref"],
            claim_ref=item[0].claim_ref,
            claim_digest=item[0].digest,
            evaluated_at="2026-09-23T09:05:00+00:00",
            policy_digest=item[1].to_dict()["policy_digest"],
            status="stale",
            reasons=("external calendar fingerprint changed",),
        )
        for item in pairs
    )
    with pytest.raises(ResolutionFailure, match="stale_evidence"):
        resolver.resolve(
            contracts.requirement,
            semantic_revision_digest=canonical_payload_digest(contracts.semantic_application),
            target_mode="production",
            capability_contracts=(contracts.capability, contracts.availability_capability),
            state_contracts=(contracts.availability_state, contracts.booking_state, contracts.audit_state),
            binding_definitions=(contracts.production_binding,),
            deliveries=(delivery,),
            environment_profile=contracts.profile,
            binding_instances=(instance,),
            state_spaces=states,
            relations=relations,
            evidence_claims=tuple(item[0] for item in pairs),
            evidence_assessments=stale,
            package_resolver=lambda _candidate: release,
        )

    incompatible = tuple(
        EvidenceAssessment.create(
            assessment_ref=item[1].to_dict()["assessment_ref"],
            claim_ref=item[0].claim_ref,
            claim_digest=item[0].digest,
            evaluated_at="2026-09-23T09:06:00+00:00",
            policy_digest=item[1].to_dict()["policy_digest"],
            status="incompatible",
            reasons=("calendar v2 failed reverification",),
        )
        for item in pairs
    )
    with pytest.raises(ResolutionFailure, match="missing_evidence"):
        resolver.resolve(
            contracts.requirement,
            semantic_revision_digest=canonical_payload_digest(
                contracts.semantic_application
            ),
            target_mode="production",
            capability_contracts=(
                contracts.capability,
                contracts.availability_capability,
            ),
            state_contracts=(
                contracts.availability_state,
                contracts.booking_state,
                contracts.audit_state,
            ),
            binding_definitions=(contracts.production_binding,),
            deliveries=(delivery,),
            environment_profile=contracts.profile,
            binding_instances=(instance,),
            state_spaces=states,
            relations=relations,
            evidence_claims=tuple(item[0] for item in pairs),
            evidence_assessments=incompatible,
            package_resolver=lambda _candidate: release,
        )


def test_missing_supporting_capability_is_a_typed_resolution_failure(tmp_path: Path) -> None:
    contracts, release, delivery, instance, states, relations, pairs = _resolution_fixture(tmp_path)
    with pytest.raises(ResolutionFailure) as caught:
        SemanticResolver(now=lambda: NOW).resolve(
            contracts.requirement,
            semantic_revision_digest=canonical_payload_digest(contracts.semantic_application),
            target_mode="production",
            capability_contracts=(contracts.capability,),
            state_contracts=(contracts.availability_state, contracts.booking_state, contracts.audit_state),
            binding_definitions=(contracts.production_binding,),
            deliveries=(delivery,),
            environment_profile=contracts.profile,
            binding_instances=(instance,),
            state_spaces=states,
            relations=relations,
            evidence_claims=tuple(item[0] for item in pairs),
            evidence_assessments=tuple(item[1] for item in pairs),
            package_resolver=lambda _candidate: release,
        )
    assert "missing_capability_dependency" in {item.code for item in caught.value.rejections}
