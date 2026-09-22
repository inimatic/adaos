"""Frozen package-neutral contracts for the CBS7 ``booking.reserve`` proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingDefinition,
    CapabilityContract,
    EnvironmentProfile,
    StateContract,
)


BOOKING_CAPABILITY_REF = "capability:booking.reserve"
AVAILABILITY_CAPABILITY_REF = "capability:availability.check"
AVAILABILITY_STATE_REF = "state-contract:booking.availability"
BOOKING_STATE_REF = "state-contract:booking.records"
AUDIT_STATE_REF = "state-contract:booking.audit"
BOOKING_REQUIREMENT_REF = "requirement:booking/reserve"


def _schema_lock(name: str, schema: dict[str, Any]) -> dict[str, str]:
    return {
        "lock_id": f"inline:booking.{name}.v1",
        "digest": canonical_payload_digest(schema),
    }


def booking_record_schemas() -> dict[str, dict[str, Any]]:
    interval = {
        "type": "object",
        "required": ["starts_at", "ends_at"],
        "properties": {
            "starts_at": {"type": "string", "format": "date-time"},
            "ends_at": {"type": "string", "format": "date-time"},
        },
        "additionalProperties": False,
    }
    return {
        "availability": {
            "type": "object",
            "required": ["resource_ref", "intervals", "generation"],
            "properties": {
                "resource_ref": {"type": "string"},
                "intervals": {"type": "array", "items": interval},
                "generation": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        "booking": {
            "type": "object",
            "required": [
                "booking_ref",
                "resource_ref",
                "customer_ref",
                "starts_at",
                "ends_at",
                "effect_receipt",
                "revision",
            ],
            "properties": {
                "booking_ref": {"type": "string"},
                "resource_ref": {"type": "string"},
                "customer_ref": {"type": "string"},
                "starts_at": {"type": "string", "format": "date-time"},
                "ends_at": {"type": "string", "format": "date-time"},
                "effect_receipt": {"type": "object"},
                "revision": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        "audit": {
            "type": "object",
            "required": ["sequence", "event", "command_digest", "recorded_at"],
            "properties": {
                "sequence": {"type": "integer", "minimum": 1},
                "event": {"type": "string"},
                "command_digest": {"type": "string"},
                "recorded_at": {"type": "string", "format": "date-time"},
            },
            "additionalProperties": True,
        },
    }


def _state_contract(
    *,
    ref: str,
    name: str,
    schema: dict[str, Any],
    consistency: tuple[str, ...],
    isolation: tuple[str, ...],
    portability: str,
    destruction: str,
    invariants: tuple[str, ...],
) -> StateContract:
    return StateContract.create(
        state_contract_ref=ref,
        version="1.0.0",
        schema_locks=(_schema_lock(name, schema),),
        invariant_refs=invariants,
        guarantees={
            "consistency": list(consistency),
            "durability": ["persistent"],
            "isolation": list(isolation),
        },
        lifecycle={
            "retention": "booking-domain",
            "destruction": destruction,
            "backup_required": portability == "portable",
        },
        ownership_constraints={
            "allowed_owner_kinds": ["application", "service"],
            "custodian_may_differ": True,
            "mutation_authority_required": True,
        },
        portability_class=portability,
        migration_compatibility={
            "strategy": "explicit_edge" if portability == "portable" else "none",
            "from_versions": ["1.0.0"],
            "migration_lock_refs": [],
        },
    )


@dataclass(frozen=True, slots=True)
class BookingContracts:
    capability: CapabilityContract
    availability_capability: CapabilityContract
    availability_state: StateContract
    booking_state: StateContract
    audit_state: StateContract
    production_binding: BindingDefinition
    simulation_binding: BindingDefinition
    profile: EnvironmentProfile
    requirement: ApplicationRequirement
    semantic_application: dict[str, Any]


def booking_contracts() -> BookingContracts:
    schemas = booking_record_schemas()
    availability_state = _state_contract(
        ref=AVAILABILITY_STATE_REF,
        name="availability",
        schema=schemas["availability"],
        consistency=("snapshot",),
        isolation=("external_authority",),
        portability="external_authoritative",
        destruction="external",
        invariants=("availability intervals are half-open", "generation is monotonic"),
    )
    booking_state = _state_contract(
        ref=BOOKING_STATE_REF,
        name="records",
        schema=schemas["booking"],
        consistency=("serializable",),
        isolation=("single_writer",),
        portability="portable",
        destruction="explicit_authority",
        invariants=("confirmed intervals do not overlap per resource", "idempotency key is unique"),
    )
    audit_state = _state_contract(
        ref=AUDIT_STATE_REF,
        name="audit",
        schema=schemas["audit"],
        consistency=("serializable",),
        isolation=("append_only",),
        portability="portable",
        destruction="explicit_authority",
        invariants=("audit sequence is monotonic", "audit records are append-only"),
    )
    availability_capability = CapabilityContract.create(
        capability_ref=AVAILABILITY_CAPABILITY_REF,
        version="1.0.0",
        title="Check resource availability",
        operations=(
            {
                "operation_id": "check",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "errors": ["stale_read", "provider_unavailable"],
            },
        ),
        invariants=("availability result pins an observed generation",),
        effects=(),
        authority_requirements=("availability.read",),
        state_ports=(
            {
                "port_id": "availability",
                "contract_ref": availability_state.state_contract_ref,
                "contract_range": "^1.0.0",
                "access": "reads",
                "requirements": {
                    "consistency_at_least": "snapshot",
                    "durability": "persistent",
                    "isolation": "external_authority",
                    "mutation_authority": "external",
                },
            },
        ),
        conformance_refs=("scenario:availability.check.v1",),
    )
    command_schema = {
        "type": "object",
        "required": [
            "idempotency_key",
            "resource_ref",
            "customer_ref",
            "starts_at",
            "ends_at",
        ],
        "properties": {
            "idempotency_key": {"type": "string", "minLength": 1},
            "resource_ref": {"type": "string", "minLength": 1},
            "customer_ref": {"type": "string", "minLength": 1},
            "starts_at": {"type": "string", "format": "date-time"},
            "ends_at": {"type": "string", "format": "date-time"},
        },
        "additionalProperties": False,
    }
    capability = CapabilityContract.create(
        capability_ref=BOOKING_CAPABILITY_REF,
        version="1.0.0",
        title="Reserve a resource interval",
        operations=(
            {
                "operation_id": "reserve",
                "input_schema": command_schema,
                "output_schema": schemas["booking"],
                "errors": [
                    "invalid_interval",
                    "unavailable",
                    "conflict",
                    "stale_read",
                    "provider_timeout",
                    "reconciliation_required",
                    "idempotency_conflict",
                ],
            },
        ),
        invariants=(
            "reservation interval starts before it ends",
            "confirmed intervals do not overlap per resource",
            "one idempotency key denotes one command and one result",
        ),
        effects=(
            "creates an externally receipted reservation effect",
            "writes BookingState",
            "appends AuditState",
            "compensates or records reconciliation when commit cannot follow an external effect",
        ),
        authority_requirements=(
            "availability.read",
            "booking.write",
            "audit.append",
            "booking.effect.reserve",
        ),
        dependencies=(
            {
                "capability_ref": availability_capability.capability_ref,
                "contract_range": "^1.0.0",
                "optional": False,
            },
        ),
        state_ports=(
            {
                "port_id": "availability",
                "contract_ref": availability_state.state_contract_ref,
                "contract_range": "^1.0.0",
                "access": "reads",
                "requirements": {
                    "consistency_at_least": "snapshot",
                    "durability": "persistent",
                    "isolation": "external_authority",
                    "mutation_authority": "external",
                },
            },
            {
                "port_id": "bookings",
                "contract_ref": booking_state.state_contract_ref,
                "contract_range": "^1.0.0",
                "access": "writes",
                "requirements": {
                    "consistency_at_least": "serializable",
                    "durability": "persistent",
                    "isolation": "single_writer",
                    "mutation_authority": "workspace",
                },
            },
            {
                "port_id": "audit",
                "contract_ref": audit_state.state_contract_ref,
                "contract_range": "^1.0.0",
                "access": "appends",
                "requirements": {
                    "consistency_at_least": "serializable",
                    "durability": "persistent",
                    "isolation": "append_only",
                    "mutation_authority": "workspace",
                },
            },
        ),
        conformance_refs=("scenario:booking.reserve.v1",),
    )

    def binding(*, ref: str, entrypoint: str, mode: str, features: tuple[str, ...]) -> BindingDefinition:
        support = []
        for state, access, consistency, isolation in (
            (availability_state, "reads", "snapshot", "external_authority"),
            (booking_state, "writes", "serializable", "single_writer"),
            (audit_state, "appends", "serializable", "append_only"),
        ):
            support.append(
                {
                    "state_contract_ref": state.state_contract_ref,
                    "contract_range": "^1.0.0",
                    "access_modes": [access],
                    "guarantees": {
                        "consistency": [consistency],
                        "durability": ["persistent"],
                        "isolation": [isolation],
                    },
                }
            )
        return BindingDefinition.create(
            binding_definition_ref=ref,
            version="1.0.0",
            capability_ref=capability.capability_ref,
            capability_version=capability.version,
            entry_protocol="adaos.booking.provider.v1",
            implementation_entrypoint=entrypoint,
            state_support=support,
            modes=(mode,),
            environment_constraints={
                "profile_classes": ["local"],
                "provider_features": list(features),
            },
            authority_requirements=capability.to_dict()["authority_requirements"],
            conformance_obligations=("capability_conformance", "state_compatibility"),
        )

    production = binding(
        ref="binding-definition:booking.reserve.production",
        entrypoint="booking.reserve.production",
        mode="production",
        features=("external_reservation_receipts", "compensation", "serializable_commit"),
    )
    simulation = binding(
        ref="binding-definition:booking.reserve.simulation",
        entrypoint="booking.reserve.simulation",
        mode="simulation",
        features=("deterministic_provider", "compensation", "serializable_commit"),
    )
    profile = EnvironmentProfile.create(
        profile_ref="profile:local/booking-proof",
        profile_class="local",
        modes=("simulation", "production"),
        provider_features=(
            "external_reservation_receipts",
            "deterministic_provider",
            "compensation",
            "serializable_commit",
        ),
        guarantees={
            "consistency": ["snapshot", "serializable"],
            "durability": ["persistent"],
            "isolation": ["external_authority", "single_writer", "append_only"],
        },
        authorities=(
            "availability.read",
            "booking.write",
            "audit.append",
            "booking.effect.reserve",
            "external",
            "workspace",
            "workspace.activate",
        ),
    )
    requirement = ApplicationRequirement.create(
        requirement_ref=BOOKING_REQUIREMENT_REF,
        capability_ref=capability.capability_ref,
        contract_range="^1.0.0",
        environment_target={
            "profile_ref": profile.profile_ref,
            "allowed_modes": ["simulation", "production"],
        },
        policy_constraints={
            "locality": "remote_allowed",
            "privacy": "workspace",
            "required_authorities": list(capability.to_dict()["authority_requirements"]),
        },
        evidence_threshold={
            "required_claim_kinds": ["capability_conformance", "state_compatibility"],
            "allow_stale": False,
        },
    )
    semantic_application = {
        "schema": "adaos.semantic_application.fixture.v1",
        "application_ref": "application:booking-proof",
        "requirements": [requirement.to_dict()],
    }
    return BookingContracts(
        capability=capability,
        availability_capability=availability_capability,
        availability_state=availability_state,
        booking_state=booking_state,
        audit_state=audit_state,
        production_binding=production,
        simulation_binding=simulation,
        profile=profile,
        requirement=requirement,
        semantic_application=semantic_application,
    )


__all__ = [
    "AUDIT_STATE_REF",
    "AVAILABILITY_CAPABILITY_REF",
    "AVAILABILITY_STATE_REF",
    "BOOKING_CAPABILITY_REF",
    "BOOKING_REQUIREMENT_REF",
    "BOOKING_STATE_REF",
    "BookingContracts",
    "booking_contracts",
    "booking_record_schemas",
]
