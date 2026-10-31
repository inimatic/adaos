"""Core-owned disjoint provider measurements for a prospectively reserved Run.

Only exact retained wires can contribute. This ledger is an as-of observation,
not a closed job set, a successful candidate or authoring authority. Responses,
prompts, arbitrary telemetry and provider exception prose are never retained.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest
from .generation_trace import _observation
from .generation_runs import verify_run_binding
from .interpretation_runs import InterpretationRunRejected
from .run_observability import validate_execution_metrics
from .task_spec import seal, verify_identity

_TERMINAL = {"succeeded", "completed", "failed", "cancelled"}
_STATES = _TERMINAL | {"queued", "running", "interrupted", "unknown"}
_MEASUREMENTS = (
    "model",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "provider_calls",
    "cost_usd",
    "cost_basis",
    "root_execution_ms",
    "root_queue_ms",
)


def _verify_row(row, binding, origin):
    verify_identity(row, id_key="observation_id", prefix="generation-observation:")
    verify_identity(row["binding"], id_key="binding_id", prefix="generation-binding:")
    if (
        row["schema"] != "adaos.builder.generation_observation.v1"
        or row["run_id"] != binding["run_id"]
        or row["origin_ref"] != origin["origin_id"]
        or row["binding"]["run_id"] != binding["run_id"]
        or row["binding"]["origin_ref"] != origin["origin_id"]
        or row["binding"]["invocation_scope"] != binding["invocation_scope"]
        or row["status"] not in _STATES
    ):
        raise InterpretationRunRejected()
    validate_execution_metrics(row["metrics"])


def project_run_measurements(rows, *, origin):
    """Aggregate each registered job once, never aggregate plus constituents."""
    if not rows or len(rows) > 16 or len({row["job_id"] for row in rows}) != len(rows):
        raise InterpretationRunRejected()
    metrics = {
        "source": "builder.llm_result",
        "pipeline_stage": "generation",
        "execution_group_ref": origin["execution_group_ref"],
        "source_refs": ["llm_job:" + row["job_id"] for row in rows],
        "llm_calls": len(rows),
        "repair_attempts": sum(row["stage"] == "repair" for row in rows),
    }
    terminal = all(row["status"] in _TERMINAL for row in rows)
    metrics["usage_complete"] = terminal and all(
        {"input_tokens", "output_tokens"} <= row["metrics"].keys() for row in rows
    )
    metrics["provider_calls_complete"] = terminal and all(
        "provider_calls" in row["metrics"] for row in rows
    )
    metrics["attempt_observations"] = [
        {
            "job_id": row["job_id"],
            "status": "observed",
            **{
                key: copy.deepcopy(row["metrics"][key])
                for key in _MEASUREMENTS
                if key in row["metrics"]
            },
        }
        for row in rows
    ]
    if metrics["provider_calls_complete"]:
        metrics["provider_calls"] = sum(
            row["metrics"]["provider_calls"] for row in rows
        )
    for key in (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "root_execution_ms",
        "root_queue_ms",
    ):
        if all(key in row["metrics"] for row in rows):
            metrics[key] = sum(row["metrics"][key] for row in rows)
    if (
        all("cost_usd" in row["metrics"] for row in rows)
        and len({row["metrics"]["cost_basis"] for row in rows}) == 1
    ):
        metrics.update(
            cost_usd=sum(row["metrics"]["cost_usd"] for row in rows),
            cost_basis=rows[0]["metrics"]["cost_basis"],
        )
    models = {row["metrics"].get("model") for row in rows}
    if len(models) == 1 and None not in models:
        metrics["model"] = next(iter(models))
    return validate_execution_metrics(metrics)


def observe_provider_response(
    journal_dir, *, binding, origin, plan, wire, response, workflow
):
    """Retain measurements before attempting canonical projection/recovery."""
    try:
        verify_run_binding(binding, origin, plan)
        directory = Path(journal_dir)
        wire_path = directory / (
            "wire-" + digest(str(wire["request_id"]))[7:39] + ".json"
        )
        retained = json.loads(wire_path.read_text(encoding="utf-8"))
        if (
            retained != wire
            or wire.get("execution_origin") != origin
            or wire.get("execution_run_binding") != binding
            or wire.get("context_admission", {}).get("generation_plan_ref")
            != plan["generation_plan_id"]
            or response.get("job_id") != wire.get("job_id")
            or not wire.get("job_id")
            or response.get("status") not in _STATES | {"canceled"}
            or response.get("request_id") not in (None, wire["request_id"])
            or (
                response.get("input_digest") is not None
                and wire.get("context_admission", {}).get(
                    "provider_attempt_input_digest"
                )
                is not None
                and response["input_digest"]
                != wire["context_admission"]["provider_attempt_input_digest"]
            )
        ):
            raise InterpretationRunRejected()
        from .generation_origin import verify_wire_origin
        from .generation_runs import wire_run_binding

        if (
            verify_wire_origin(directory, wire) != origin
            or wire_run_binding(directory, wire, origin) != binding
        ):
            raise InterpretationRunRejected()
        observed = _observation(response)
        observed["status"] = {"completed": "succeeded", "canceled": "cancelled"}.get(
            observed["status"], observed["status"]
        )
        row = seal(
            {
                "schema": "adaos.builder.generation_observation.v1",
                "run_id": binding["run_id"],
                "origin_ref": origin["origin_id"],
                "binding": binding,
                "generation_plan_ref": plan["generation_plan_id"],
                "wire_digest": digest(wire),
                "job_id": wire["job_id"],
                "stage": "repair"
                if plan.get("context_stage") == "repair"
                else "generation",
                "status": observed["status"],
                "metrics": observed["metrics"],
            },
            id_key="observation_id",
            prefix="generation-observation:",
        )
        prefix = "generation-observation-" + binding["run_id"].partition(":")[2] + "-"
        target = directory / (prefix + digest(wire["job_id"])[7:31] + ".json")
        with mutation_lock(directory / (prefix + "run.lock"), timeout_s=30):
            paths = sorted(directory.glob(prefix + "*.json"))
            if len(paths) > 16 or (len(paths) == 16 and target not in paths):
                raise InterpretationRunRejected()
            if target.exists():
                before = json.loads(target.read_text(encoding="utf-8"))
                _verify_row(before, binding, origin)
                immutable = (
                    "wire_digest",
                    "binding",
                    "generation_plan_ref",
                    "job_id",
                    "stage",
                )
                if any(before[key] != row[key] for key in immutable):
                    raise InterpretationRunRejected()
                # Never replace terminal measurements with a stale queued reply,
                # nor silently overwrite contradictory final provider evidence.
                if before["status"] in _TERMINAL:
                    if row["status"] not in _TERMINAL:
                        row = before
                    elif before != row:
                        raise InterpretationRunRejected()
                elif before["status"] == "running" and row["status"] == "queued":
                    row = before
            rows = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in paths
                if path != target
            ]
            rows.append(row)
            for item in rows:
                _verify_row(item, binding, origin)
            rows.sort(key=lambda item: item["job_id"])
            metrics = project_run_measurements(rows, origin=origin)
            atomic_write_json(target, row)
            projected = workflow.record_generation_observation(
                binding,
                origin=origin,
                plan=plan,
                execution_metrics=metrics,
                evidence_refs=[item["observation_id"] for item in rows],
            )
        if not projected.get("ok") and not projected.get("observation_recorded"):
            raise InterpretationRunRejected()
        return projected
    except InterpretationRunRejected:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InterpretationRunRejected() from error
