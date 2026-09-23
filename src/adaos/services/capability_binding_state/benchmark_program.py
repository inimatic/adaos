"""Executable bounded CBS9 benchmark over matched legacy and CBS treatments.

The program deliberately measures architecture declaration cost, not model
quality.  Both arms receive the same semantic input and execute the same
acceptance workload.  Evaluation rubrics and recipes stay in the evaluator and
are represented in the authoring case only by digests.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic_ns
from typing import Any, Callable, Iterable, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.capability_binding_state.benchmark import (
    build_benchmark_report,
    create_benchmark_observation,
    freeze_benchmark_case,
    load_cbs_telemetry,
)
from adaos.services.capability_binding_state.booking import (
    BookingReservationService,
    InMemoryAuditState,
    InMemoryAvailabilityState,
    InMemoryBookingState,
    ProductionReservationProvider,
)
from adaos.services.capability_binding_state.reference_booking import booking_contracts
from adaos.services.capability_binding_state.reference_crud import (
    FLOWBOARD_RESOURCE_TYPE,
    flowboard_bundle,
    flowboard_contracts,
)
from adaos.services.resources.local import LocalCrudResourceService


_NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
_MODEL_ID = "none:deterministic-reference"
_TOOL_BUDGET = {"python_processes": 1, "external_calls": 0, "model_calls": 0}
_INVARIANTS = (
    "materialization_independence",
    "package_topology_independence",
    "state_continuity",
    "application_independence",
    "atomic_rebinding",
)


def _digest(value: Any) -> str:
    return canonical_payload_digest(value)


def _line_cost(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).splitlines())


def _count_physical_tokens(value: Any) -> int:
    physical = {"provider", "binding", "package", "storage", "endpoint", "credential"}
    count = 0

    def visit(item: Any) -> None:
        nonlocal count
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).lower() in physical:
                    count += 1
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return count


def _evaluation(
    authoring_input: Mapping[str, Any],
    *,
    title: str,
    sealed: bool,
) -> dict[str, Any]:
    rubric = {
        "title": title,
        "required": [
            "same semantic input",
            "same acceptance workload",
            "exact source and evidence digests",
            "no evaluator recipe in authoring input",
        ],
    }
    labels = {"capability_labels": sorted(authoring_input.get("capability_labels") or [])}
    recipe = {"evaluator": title, "revision": 1}
    return {
        "input_digest": _digest(authoring_input),
        "rubric_digest": _digest(rubric),
        "target_labels_digest": _digest(labels),
        "solution_recipe_digest": _digest(recipe),
        "authoring_visibility": "input_only" if sealed else "input_and_rubric",
        "sealed": sealed,
    }


def _run_crud(root: Path, _variant: str) -> dict[str, Any]:
    service = LocalCrudResourceService(root)
    service.materialize(flowboard_bundle())
    created = service.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "create",
        record_id="benchmark-record",
        payload={
            "id": "benchmark-record",
            "title": "CBS9 matched record",
            "status": "planned",
            "revision": 1,
        },
    )
    updated = service.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "update",
        record_id="benchmark-record",
        payload={"status": "done"},
        expected_revision=1,
    )
    deleted = service.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "delete",
        record_id="benchmark-record",
        payload={},
        expected_revision=2,
    )
    passed = (
        created["record"]["revision"] == 1
        and updated["record"]["revision"] == 2
        and updated["record"]["status"] == "done"
        and deleted["deleted"] is True
    )
    return {
        "passed": passed,
        "assertions": 4,
        "result_digest": _digest(
            {"created": created, "updated": updated, "deleted": deleted}
        ),
    }


def _run_booking(_root: Path, _variant: str) -> dict[str, Any]:
    intervals = {
        "room:blue": (
            ("2026-09-24T09:00:00+00:00", "2026-09-24T18:00:00+00:00"),
        )
    }
    provider = ProductionReservationProvider(provider_ref="external:calendar-benchmark")
    service = BookingReservationService(
        availability=InMemoryAvailabilityState(intervals),
        bookings=InMemoryBookingState(),
        audit=InMemoryAuditState(),
        provider=provider,
        now=lambda: _NOW,
    )
    command = {
        "idempotency_key": "cbs9-booking-one",
        "resource_ref": "room:blue",
        "customer_ref": "customer:benchmark",
        "starts_at": "2026-09-24T10:00:00+00:00",
        "ends_at": "2026-09-24T11:00:00+00:00",
    }
    first = service.reserve(command)
    duplicate = service.reserve(command)
    passed = (
        first["status"] == "confirmed"
        and first["duplicate"] is False
        and duplicate["duplicate"] is True
        and duplicate["booking_ref"] == first["booking_ref"]
        and provider.reserve_calls == 1
        and len(service.bookings.records()) == 1
    )
    return {
        "passed": passed,
        "assertions": 6,
        "result_digest": _digest(
            {
                "first": first,
                "duplicate": duplicate,
                "events": service.audit.events(),
            }
        ),
    }


def _run_drive_lifecycle(_root: Path, variant: str) -> dict[str, Any]:
    release_digest = _digest({"application": "adaos_drive", "release": "0.1.10"})
    candidate = {
        "candidate_id": "adaos_drive-0-1-10-frozen",
        "release_digest": release_digest,
        "webspace_id": "desktop",
        "application_id": "adaos_drive",
        "runtime_root_ref": "trial:adaos_drive-0-1-10-frozen",
    }
    selections = [
        {
            "webspace_id": "desktop",
            "application_id": "adaos_drive",
            "release_digest": release_digest,
            "runtime_root_ref": "trial:adaos_drive-0-1-10-frozen",
        },
        {
            "webspace_id": "desktop-dev",
            "application_id": "adaos_drive",
            "release_digest": release_digest,
            "runtime_root_ref": "trial:adaos_drive-0-1-10-frozen",
        },
    ]
    if variant == "legacy":
        selected = [
            item
            for item in selections
            if item["application_id"] == candidate["application_id"]
            and item["release_digest"] == candidate["release_digest"]
        ]
    else:
        selected = [
            item
            for item in selections
            if all(item[field] == candidate[field] for field in item)
        ]
    passed = len(selected) == 1
    return {
        "passed": passed,
        "assertions": 1,
        "manual_interventions": 0 if passed else 1,
        "reason": None if passed else "ambiguous_runtime_selection",
        "result_digest": _digest(
            {"variant": variant, "candidate": candidate, "selected": selected}
        ),
    }


def _flowboard_material() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    contracts = flowboard_contracts()
    semantic = {
        "schema": "adaos.application.semantic_fixture.v1",
        "application_ref": "application:flowboard",
        "requirements": [contracts.requirement.to_dict()],
    }
    cbs = {
        "semantic": semantic,
        "new_inventory": [
            contracts.capability.to_dict(),
            contracts.state.to_dict(),
            contracts.production_binding.to_dict(),
            contracts.simulation_binding.to_dict(),
            contracts.profile.to_dict(),
        ],
    }
    legacy = flowboard_bundle()
    identities = [
        contracts.capability.digest,
        contracts.state.digest,
        contracts.production_binding.digest,
        contracts.requirement.digest,
        _digest(semantic),
    ]
    return legacy, cbs, identities


def _booking_material() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    contracts = booking_contracts()
    legacy = {
        "schema": "adaos.legacy.application_requirement.v1",
        "application_ref": "application:booking",
        "operation": "booking.reserve",
        "state": ["availability:read", "bookings:write", "audit:append"],
        "semantics": [
            "future half-open interval",
            "idempotent command",
            "compensate partial external effect",
        ],
        "provider": "external calendar",
    }
    cbs = {
        "semantic": copy.deepcopy(contracts.semantic_application),
        "new_inventory": [
            contracts.capability.to_dict(),
            contracts.availability_capability.to_dict(),
            contracts.availability_state.to_dict(),
            contracts.booking_state.to_dict(),
            contracts.audit_state.to_dict(),
            contracts.production_binding.to_dict(),
            contracts.simulation_binding.to_dict(),
        ],
    }
    identities = [
        contracts.capability.digest,
        contracts.availability_capability.digest,
        contracts.availability_state.digest,
        contracts.booking_state.digest,
        contracts.audit_state.digest,
        contracts.production_binding.digest,
        contracts.requirement.digest,
        _digest(contracts.semantic_application),
    ]
    return legacy, cbs, identities


def _drive_lifecycle_material() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    release_digest = _digest({"application": "adaos_drive", "release": "0.1.10"})
    candidate_digest = _digest(
        {"candidate": "adaos_drive-0-1-10-frozen", "release": release_digest}
    )
    legacy = {
        "schema": "adaos.legacy.trial_acceptance.v1",
        "application_id": "adaos_drive",
        "candidate_digest": candidate_digest,
        "lookup": ["application_id", "release_digest"],
    }
    cbs = {
        "schema": "adaos.application.trial_acceptance.v1",
        "candidate_id": "adaos_drive-0-1-10-frozen",
        "candidate_digest": candidate_digest,
        "runtime_selection": {
            "webspace_id": "desktop",
            "application_id": "adaos_drive",
            "release_digest": release_digest,
            "runtime_root_ref": "trial:adaos_drive-0-1-10-frozen",
        },
    }
    return legacy, cbs, [release_digest, candidate_digest, _digest(cbs)]


def _observation(
    *,
    case: Mapping[str, Any],
    variant: str,
    material: Mapping[str, Any],
    identities: list[str],
    run: Mapping[str, Any],
    duration_ms: int,
    maturity: Mapping[str, int],
    selected_contracts: int,
    reused_contracts: int,
) -> dict[str, Any]:
    material_cost = _line_cost(material)
    semantic = material.get("semantic") if isinstance(material.get("semantic"), Mapping) else material
    source_digest = _digest(material)
    evidence_digest = str(run["result_digest"])
    independent_reuse = reused_contracts if variant == "cbs" else 0
    return create_benchmark_observation(
        case=case,
        variant=variant,
        run_ref=f"cbs9-{case['sequence_index']}-{variant}",
        controls=case["controls"],
        metrics={
            "e2e_success": 1 if run["passed"] else 0,
            "requirements_resolution_rate": 1 if run["passed"] else 0,
            "context_tokens_total": 0,
            "residual_diff_lines": material_cost,
            "manual_interventions": int(run.get("manual_interventions") or 0),
            "end_to_end_duration_ms": max(1, duration_ms),
            "package_reuse_rate": 0,
            "contract_reuse_rate": (
                reused_contracts / selected_contracts if selected_contracts else 0
            ),
            "independent_reuse_rate": (
                independent_reuse / selected_contracts if selected_contracts else 0
            ),
            "composition_complexity": max(1, selected_contracts),
            "semantic_overlap_rate": 0,
            "substitution_cost": _count_physical_tokens(semantic),
            "migration_count": 0,
            "regression_count": 0 if run["passed"] else 1,
            "marginal_cost": material_cost,
            "invariant_pass_rate": None,
        },
        source_digests=(source_digest,),
        treatment={
            "kind": variant,
            "inventory_maturity": dict(maturity),
            "cost_unit": "canonical_pretty_json_lines",
            "domain_runtime_excluded_from_both_arms": True,
        },
        identity_digests=identities if variant == "cbs" else (source_digest,),
        evidence_digests=(evidence_digest,),
    )


def _execute(
    root: Path,
    *,
    case_ref: str,
    title: str,
    cohort: str,
    sequence_index: int,
    inventory_size: int,
    authoring_input: Mapping[str, Any],
    environment_profile_digest: str,
    materials: Callable[[], tuple[dict[str, Any], dict[str, Any], list[str]]],
    acceptance: Callable[[Path, str], dict[str, Any]],
    maturity: Mapping[str, int],
    selected_contracts: int,
    reused_contracts: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    case = freeze_benchmark_case(
        case_ref=case_ref,
        title=title,
        cohort=cohort,
        workload_digest=_digest(authoring_input),
        environment_profile_digest=environment_profile_digest,
        model_id=_MODEL_ID,
        tool_budget=_TOOL_BUDGET,
        requirement_total=1,
        sequence_index=sequence_index,
        inventory_size=inventory_size,
        evaluation=_evaluation(authoring_input, title=title, sealed=cohort == "heldout"),
    )
    legacy, cbs, identities = materials()
    observations: list[dict[str, Any]] = []
    runs: dict[str, Any] = {}
    for variant, material in (("legacy", legacy), ("cbs", cbs)):
        started = monotonic_ns()
        result = acceptance(
            root / case_ref.replace(":", "_").replace("/", "_") / variant,
            variant,
        )
        duration_ms = (monotonic_ns() - started) // 1_000_000
        runs[variant] = result
        observations.append(
            _observation(
                case=case,
                variant=variant,
                material=material,
                identities=identities,
                run=result,
                duration_ms=duration_ms,
                maturity=maturity if variant == "cbs" else {},
                selected_contracts=selected_contracts if variant == "cbs" else 0,
                reused_contracts=reused_contracts if variant == "cbs" else 0,
            )
        )
    return case, observations, {"authoring_input": dict(authoring_input), "runs": runs}


def run_bounded_cbs9_benchmark(
    root: Path,
    *,
    historical_telemetry_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    """Run the frozen matched program and return a content-addressed bundle."""

    target = Path(root).resolve()
    target.mkdir(parents=True, exist_ok=True)
    flowboard = flowboard_contracts()
    booking = booking_contracts()
    cases: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    execution: dict[str, Any] = {}

    flow_case, flow_observations, flow_run = _execute(
        target,
        case_ref="benchmark-case:flowboard/crud",
        title="Flowboard CRUD declaration and acceptance",
        cohort="representative",
        sequence_index=1,
        inventory_size=0,
        authoring_input={
            "application_ref": "application:flowboard",
            "requirements": ["typed CRUD with persistent single-writer state"],
            "capability_labels": ["resource.records.manage"],
        },
        environment_profile_digest=flowboard.profile.digest,
        materials=_flowboard_material,
        acceptance=_run_crud,
        maturity={"application_local": 2},
        selected_contracts=2,
        reused_contracts=0,
    )
    cases.append(flow_case)
    observations.extend(flow_observations)
    execution[flow_case["case_ref"]] = flow_run

    drive_case, drive_observations, drive_run = _execute(
        target,
        case_ref="benchmark-case:adaos-drive/trial-acceptance",
        title="AdaOS Drive exact Trial acceptance",
        cohort="representative",
        sequence_index=2,
        inventory_size=2,
        authoring_input={
            "application_ref": "application:adaos_drive",
            "requirements": [
                "accept only the reviewed Candidate into desktop webspace"
            ],
            "capability_labels": ["application.trial.accept"],
        },
        environment_profile_digest=flowboard.profile.digest,
        materials=_drive_lifecycle_material,
        acceptance=_run_drive_lifecycle,
        maturity={},
        selected_contracts=0,
        reused_contracts=0,
    )
    cases.append(drive_case)
    observations.extend(drive_observations)
    execution[drive_case["case_ref"]] = drive_run

    booking_case, booking_observations, booking_run = _execute(
        target,
        case_ref="benchmark-case:booking/reserve",
        title="Booking reserve declaration and acceptance",
        cohort="heldout",
        sequence_index=3,
        inventory_size=2,
        authoring_input={
            "application_ref": "application:booking",
            "requirements": [
                "reserve future intervals idempotently",
                "compensate partial provider effects",
            ],
            "capability_labels": ["booking.reserve", "availability.check"],
        },
        environment_profile_digest=booking.profile.digest,
        materials=_booking_material,
        acceptance=_run_booking,
        maturity={"application_local": 5},
        selected_contracts=5,
        reused_contracts=0,
    )
    cases.append(booking_case)
    observations.extend(booking_observations)
    execution[booking_case["case_ref"]] = booking_run

    report = build_benchmark_report(cases, observations)
    historical_telemetry = [
        item.to_dict() for item in load_cbs_telemetry(historical_telemetry_paths)
    ]
    body = {
        "schema": "adaos.cbs.benchmark_bundle.v1",
        "methodology": {
            "scope": "bounded deterministic architecture-declaration benchmark",
            "model_calls": 0,
            "cost_unit": "canonical_pretty_json_lines",
            "domain_runtime": "shared and excluded from both declaration-cost arms",
            "historical_telemetry_rewritten": False,
            "invariant_evidence": [
                "tests/test_capability_binding_state_e2e.py",
                "tests/test_capability_binding_state_booking.py",
            ],
            "invariants": list(_INVARIANTS),
        },
        "cases": cases,
        "observations": observations,
        "historical_telemetry": historical_telemetry,
        "historical_context": {
            "observation_count": len(historical_telemetry),
            "telemetry_digests": [
                item["telemetry_digest"] for item in historical_telemetry
            ],
            "matched_legacy_arms": 0,
            "use": "unmatched context only",
        },
        "execution": execution,
        "report": report,
    }
    return {**body, "bundle_digest": _digest(body)}


__all__ = ["run_bounded_cbs9_benchmark"]
