"""Pure joins over explicitly supplied, retained generation evidence.

This projection grants no authoring authority and does not collect evidence or
invent an enclosing-turn manifest. Canonical Run aggregates are links, never
another charge added to their constituent provider observations.
"""

from __future__ import annotations

import copy
import math
import re

from jsonschema import ValidationError

from .generation_origin import verify_origin
from .generation_plan import context_receipt
from .run_observability import execution_metrics_from_llm
from .task_spec import seal, verify_identity


class GenerationTraceRejected(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise GenerationTraceRejected(code)


def _identity(value, key, prefix):
    try:
        verify_identity(value, id_key=key, prefix=prefix)
    except (ValueError, KeyError, TypeError) as error:
        raise GenerationTraceRejected("trace.invalid_identity") from error


def _exact_body_pin(wire):
    return (
        wire.get("body_digest_basis") == "serialized_utf8"
        and isinstance(wire.get("body_sha256"), str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", wire["body_sha256"]))
    )


def _observation(value):
    identity = value.get("job_id")
    _require(
        isinstance(identity, str) and 0 < len(identity) <= 200,
        "trace.job_identity_missing",
    )
    telemetry = value.get("telemetry") or {}
    _require(isinstance(telemetry, dict), "trace.observation_shape_invalid")
    for key in ("request_id", "status", "input_digest", "model"):
        _require(
            value.get(key) is None or isinstance(value[key], str),
            "trace.observation_shape_invalid",
        )
    _require(
        not any(
            telemetry.get(key) for key in ("repair", "history", "attempt_observations")
        ),
        "trace.aggregate_observation_not_disjoint",
    )
    safe = {
        key: copy.deepcopy(telemetry[key])
        for key in ("provider_calls", "usage", "cost", "timing")
        if key in telemetry
    }
    safe["root_job_id"] = identity
    if isinstance(telemetry.get("provider"), dict) and telemetry["provider"].get(
        "model"
    ):
        safe["provider"] = {"model": telemetry["provider"]["model"]}
    wire = telemetry.get("wire") or {}
    _require(isinstance(wire, dict), "trace.observation_shape_invalid")
    for key in ("model", "body_sha256", "body_digest_basis", "output_schema_digest"):
        _require(
            wire.get(key) is None or isinstance(wire[key], str),
            "trace.observation_shape_invalid",
        )
    if "tool_types" in wire:
        _require(
            isinstance(wire["tool_types"], list)
            and len(wire["tool_types"]) <= 64
            and all(isinstance(item, str) for item in wire["tool_types"]),
            "trace.observation_shape_invalid",
        )
    model = value.get("model") or wire.get("model")
    metrics = execution_metrics_from_llm(
        {"model": model, "usage": value.get("usage") or {}, "telemetry": safe}
    )
    return {
        "job_id": identity,
        "request_id": value.get("request_id"),
        "status": value.get("status"),
        "input_digest": value.get("input_digest"),
        "metrics": metrics,
        "wire": {
            key: copy.deepcopy(wire[key])
            for key in (
                "model",
                "body_sha256",
                "body_digest_basis",
                "output_schema_digest",
                "tool_types",
            )
            if key in wire
        },
    }


def project_generation_trace(
    *, admission, wires=(), observations=(), runs=(), repairs=(), interpretation=None
):
    """Project explicit retained evidence; never infer whole-turn completeness."""
    try:
        return _project_generation_trace(
            admission=admission,
            wires=wires,
            observations=observations,
            runs=runs,
            repairs=repairs,
            interpretation=interpretation,
        )
    except GenerationTraceRejected:
        raise
    except ValidationError as error:
        raise GenerationTraceRejected("trace.invalid_measurement") from error
    except (KeyError, TypeError, ValueError) as error:
        raise GenerationTraceRejected("trace.invalid_input") from error


def _project_generation_trace(
    *, admission, wires=(), observations=(), runs=(), repairs=(), interpretation=None
):
    """Join one plan's supplied evidence, with explicit incompleteness bounds.

    Observations are disjoint provider-job results, not Builder's aggregate
    generation result. Inputs remain unchanged; raw messages/output/provider
    prose/credentials are never copied into this diagnostic projection.
    """
    _require(
        len(wires) <= 16
        and len(observations) <= 32
        and len(repairs) <= 16
        and len(runs) <= 1000,
        "trace.capacity_exceeded",
    )
    plan, task, brief = (
        admission[key] for key in ("generation_plan", "task_spec", "brief")
    )
    _require(
        admission.get("schema") == "adaos.builder.generation_admission.v1",
        "trace.admission_scope_invalid",
    )
    for value, key, prefix in (
        (plan, "generation_plan_id", "generation-plan:"),
        (task, "task_spec_id", "task:"),
        (brief, "brief_id", "brief:"),
    ):
        _identity(value, key, prefix)
    _require(
        plan["task_spec_ref"] == task["task_spec_id"]
        and plan["task_spec_digest"] == task["digest"]
        and plan["brief_ref"] == brief["brief_id"]
        and plan["brief_digest"] == brief["digest"]
        and task["brief_ref"] == brief["brief_id"]
        and task["brief_digest"] == brief["digest"]
        and plan["source_digest"] == task["source"]["digest"],
        "trace.task_plan_conflict",
    )
    gaps = []

    def gap(code, ref=None):
        row = {"code": code, **({"ref": ref} if ref else {})}
        if row not in gaps:
            gaps.append(row)

    source = (brief.get("task_contract") or {}).get("source")
    if source is None:
        gap("trace.admitted_source_binding_missing")
    else:
        _identity(source, "intent_id", "intent:")
        _require(
            source["digest"] == plan["source_digest"]
            and source["statement"] == task["source"]["statement"]
            and source["source"] == task["source"]["provenance"]
            and source["scope"] == task["scope"]
            and source["authority"] == task["authority"],
            "trace.source_binding_conflict",
        )
    canonical = {}
    for run in runs:
        key = (run["change_id"], run["run_id"])
        _require(
            key not in canonical or canonical[key] == run,
            "trace.canonical_snapshot_conflict",
        )
        canonical[key] = run
    runs = list(canonical.values())

    origin = admission.get("execution_origin")
    if origin:
        try:
            verify_origin(origin, plan, task=task)
        except (ValueError, KeyError, TypeError) as error:
            raise GenerationTraceRejected("trace.origin_conflict") from error
    else:
        gap("trace.prospective_origin_missing")
    children = {}
    for record in repairs:
        child = record["generation_plan"]
        _identity(child, "generation_plan_id", "generation-plan:")
        _require(
            child.get("parent_generation_plan_ref") == plan["generation_plan_id"]
            and child["task_spec_ref"] == plan["task_spec_ref"]
            and child["brief_digest"] == plan["brief_digest"]
            and child["source_digest"] == plan["source_digest"],
            "trace.repair_lineage_conflict",
        )
        if origin:
            if record.get("execution_origin") is None:
                gap("trace.repair_origin_missing", child["generation_plan_id"])
            else:
                _require(
                    record["execution_origin"] == origin, "trace.repair_origin_conflict"
                )
        ref = child["generation_plan_id"]
        _require(
            ref not in children or children[ref] == child,
            "trace.repair_identity_conflict",
        )
        children[ref] = child

    expected = {}
    request_jobs = {}

    def expect(job, request, stage, wire=None):
        _require(
            isinstance(job, str)
            and bool(job)
            and isinstance(request, str)
            and bool(request),
            "trace.job_binding_missing",
        )
        row = {
            "job_id": job,
            "request_id": request,
            "stage": stage,
            "wire_admission": wire,
        }
        _require(
            job not in expected or expected[job] == row, "trace.job_binding_conflict"
        )
        _require(
            request not in request_jobs or request_jobs[request] == job,
            "trace.request_binding_conflict",
        )
        expected[job] = row
        request_jobs[request] = job

    excluded_wires = []
    for wire in wires:
        context = wire["context_admission"]
        ref = context.get("generation_plan_ref")
        if ref == plan["generation_plan_id"]:
            stage, actual = "generation", plan
        elif context.get("parent_generation_plan_ref") == plan["generation_plan_id"]:
            stage, actual = "repair", children.get(ref)
            if actual is None:
                gap("trace.repair_admission_missing", ref)
        else:
            excluded_wires.append(wire.get("job_id"))
            continue
        if actual:
            if context.get("transport") is None:
                gap("trace.transport_pin_missing", wire.get("job_id"))
            else:
                _require(
                    context["transport"] == actual["transport"],
                    "trace.transport_conflict",
                )
            _require(
                context.get("parent_generation_plan_ref")
                == actual.get("parent_generation_plan_ref"),
                "trace.wire_lineage_conflict",
            )
            if actual.get("parent_request_id"):
                if context.get("parent_request_id") is None:
                    gap("trace.repair_request_pin_missing", wire.get("job_id"))
                else:
                    _require(
                        context["parent_request_id"] == actual["parent_request_id"],
                        "trace.repair_request_conflict",
                    )
        if origin:
            if wire.get("execution_origin") is None:
                gap("trace.wire_origin_missing", wire.get("job_id"))
            else:
                _require(
                    wire["execution_origin"] == origin, "trace.wire_origin_conflict"
                )
        expect(wire.get("job_id"), wire.get("request_id"), stage, context)
    if not any(row["stage"] == "generation" for row in expected.values()):
        gap("trace.generation_wire_missing")
    for child in children.values():
        parent = expected.get(child.get("parent_provider_job_id"))
        _require(
            parent is not None
            and parent["stage"] == "generation"
            and parent["request_id"] == child["parent_request_id"],
            "trace.repair_parent_job_conflict",
        )

    receipt = admission.get("interpretation_receipt") or {}
    historical = []
    if plan["route"] == "two_stage":
        if receipt.get("job_id") and receipt.get("request_id"):
            expect(receipt["job_id"], receipt["request_id"], "interpretation")
        else:
            gap("trace.interpretation_binding_missing")
        if interpretation is None:
            gap("trace.interpretation_journal_missing")
        else:
            _identity(interpretation["task_spec"], "task_spec_id", "task:")
            _require(
                interpretation["job_id"] == receipt.get("job_id")
                and interpretation["request_id"] == receipt.get("request_id")
                and interpretation["task_spec"]["source"]["digest"]
                == plan["source_digest"],
                "trace.interpretation_source_conflict",
            )
            _require(
                interpretation["context"]
                == context_receipt(
                    interpretation["messages"],
                    generation_options=interpretation["options"],
                ),
                "trace.interpretation_context_conflict",
            )
            _require(
                (interpretation.get("admission") or {}).get("status") == "admitted",
                "trace.interpretation_not_admitted",
            )
            if not interpretation.get("admitted_task") or not interpretation.get(
                "brief"
            ):
                gap("trace.interpretation_admission_artifacts_missing")
            else:
                _identity(interpretation["admitted_task"], "task_spec_id", "task:")
                _identity(interpretation["brief"], "brief_id", "brief:")
                _require(
                    interpretation["admitted_task"]["digest"] == task["digest"]
                    and interpretation["brief"]["digest"] == brief["digest"],
                    "trace.interpretation_meaning_conflict",
                )
    else:
        job = receipt.get("revalidated_interpretation_job_id") or receipt.get(
            "recovered_interpretation_job_id"
        )
        if job:
            historical.append(job)

    unique = {}
    for raw in observations:
        row = _observation(raw)
        identity = row["job_id"]
        _require(
            identity not in unique or unique[identity] == row,
            "trace.provider_observation_conflict",
        )
        unique[identity] = row
    rows = []
    terminal = {"completed", "succeeded", "failed", "cancelled", "interrupted"}
    for identity, binding in expected.items():
        observed = unique.get(identity)
        if observed is None:
            gap("trace.provider_observation_missing", identity)
        else:
            _require(
                observed["request_id"] == binding["request_id"],
                "trace.provider_request_conflict",
            )
            context = binding["wire_admission"]
            if context:
                pin = context.get("provider_attempt_input_digest")
                if pin and observed["input_digest"]:
                    _require(
                        pin == observed["input_digest"],
                        "trace.post_adapter_input_conflict",
                    )
                else:
                    gap("trace.post_adapter_input_pin_missing", identity)
            if not _exact_body_pin(observed["wire"]):
                gap("trace.exact_wire_body_pin_missing", identity)
        linked = [
            run
            for run in runs
            if "llm_job:" + identity
            in (run.get("execution_metrics") or {}).get("source_refs", [])
        ]
        if not linked:
            gap("trace.canonical_run_missing", identity)
        if len(linked) > 1:
            gap("trace.canonical_job_duplicated", identity)
        if origin and any(
            run.get("change_id") != origin["change_id"] for run in linked
        ):
            raise GenerationTraceRejected("trace.canonical_change_conflict")
        rows.append(
            {
                "job_id": identity,
                "request_id": binding["request_id"],
                "stage": binding["stage"],
                "observation": observed,
                "canonical_run_refs": [run["run_id"] for run in linked],
                "canonical_run_links": [
                    {
                        key: run.get(key)
                        for key in ("run_id", "change_id", "status", "activity")
                    }
                    for run in linked
                ],
            }
        )
    supplied = [row["observation"] for row in rows if row["observation"]]
    counters_complete = (
        bool(rows)
        and len(supplied) == len(rows)
        and all(
            row["status"] in terminal
            and type(row["metrics"].get("provider_calls")) is int
            for row in supplied
        )
    )
    if "trace.generation_wire_missing" in {row["code"] for row in gaps}:
        counters_complete = False
    totals = {
        "known_llm_job_count": len(rows),
        "known_repair_job_count": sum(row["stage"] == "repair" for row in rows),
        "provider_counters_complete_for_supplied_jobs": counters_complete,
    }
    known = [
        row["metrics"]["provider_calls"]
        for row in supplied
        if type(row["metrics"].get("provider_calls")) is int
    ]
    if counters_complete:
        totals["provider_calls"] = sum(known)
        if totals["provider_calls"] > plan["provider_call_limit"]:
            gap("trace.known_provider_budget_exceeded")
    elif known:
        totals["known_provider_calls_subtotal"] = sum(known)
    for key in (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    ):
        if (
            supplied
            and len(supplied) == len(rows)
            and all(key in row["metrics"] for row in supplied)
        ):
            totals[key] = sum(row["metrics"][key] for row in supplied)
    if (
        supplied
        and len(supplied) == len(rows)
        and all("cost_usd" in row["metrics"] for row in supplied)
    ):
        bases = {row["metrics"]["cost_basis"] for row in supplied}
        if len(bases) == 1:
            total_cost = sum(row["metrics"]["cost_usd"] for row in supplied)
            _require(math.isfinite(total_cost), "trace.measurement_capacity_exceeded")
            totals.update(cost_usd=total_cost, cost_basis=next(iter(bases)))
    gap("trace.enclosing_turn_manifest_missing")
    return seal(
        {
            "schema": "adaos.builder.generation_trace.v1",
            "status": "partial",
            "scope": "supplied_generation_admission_and_explicit_disjoint_observations",
            "generation_plan_ref": plan["generation_plan_id"],
            "task_spec_ref": task["task_spec_id"],
            "brief_ref": brief["brief_id"],
            "source_digest": plan["source_digest"],
            "route": plan["route"],
            "transport": plan["transport"],
            "retrieval": plan["retrieval"],
            "context_strategy": plan["context_strategy"],
            "prospective_origin_ref": origin["origin_id"] if origin else None,
            "logical_model_stages": [row["name"] for row in plan["stages"]],
            "jobs": rows,
            "totals": totals,
            "historical_interpretation_job_refs": historical,
            "interpretation_basis_refs": {
                key: copy.deepcopy(receipt[key])
                for key in (
                    "schema",
                    "admission_basis",
                    "source_digest",
                    "prior_source_digest",
                    "prior_brief_ref",
                    "prior_task_ref",
                    "revalidation_ref",
                )
                if key in receipt
            },
            "out_of_scope_wire_job_refs": excluded_wires,
            "out_of_scope_observation_job_refs": [
                identity for identity in unique if identity not in expected
            ],
            "gaps": gaps,
            "whole_turn_accounting_complete": False,
            "product_acceptance": "not_implied",
            "inference_or_mutation_performed": False,
        },
        id_key="trace_id",
        prefix="generation-trace:",
    )
