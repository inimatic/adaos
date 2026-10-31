"""Negative canonical outcomes from retained evidence, never inferred success."""

from __future__ import annotations

import json
import re
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest
from .generation_observations import _verify_row, project_run_measurements
from .generation_runs import verify_run_binding
from .generation_submissions import GenerationSubmissions
from .interpretation_runs import InterpretationRunRejected
from .task_spec import seal, verify_identity


def complete_failed_generation(
    journal_dir, *, binding, origin, plan, outcome, cause_ref, workflow
):
    """Finish failed/cancelled work without UI revision or a new model call.

    Successful completion belongs to accepted revision joining. This helper is
    intentionally not exposed as a model tool or enabled by legacy Builder.
    """
    try:
        verify_run_binding(binding, origin, plan)
        if outcome not in {"failed", "cancelled"} or not isinstance(cause_ref, str):
            raise InterpretationRunRejected()
        directory = Path(journal_dir)
        prefix = "generation-observation-" + binding["run_id"].partition(":")[2] + "-"
        paths = sorted(directory.glob(prefix + "*.json"))
        if not 1 <= len(paths) <= 16:
            raise InterpretationRunRejected()
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        for row in rows:
            _verify_row(row, binding, origin)
        if cause_ref.startswith("llm_job:"):
            job = cause_ref.removeprefix("llm_job:")
            if not any(
                row["job_id"] == job and row["status"] == outcome for row in rows
            ):
                raise InterpretationRunRejected()
        elif outcome == "failed" and re.fullmatch(
            r"repair-diagnosis:[a-f0-9]{24}", cause_ref
        ):
            diagnosis = json.loads(
                (directory / (cause_ref.replace(":", "-") + ".json")).read_text(
                    encoding="utf-8"
                )
            )
            verify_identity(
                diagnosis, id_key="diagnosis_id", prefix="repair-diagnosis:"
            )
            if (
                diagnosis.get("schema") != "adaos.builder.repair_diagnosis.v1"
                or diagnosis.get("parent_generation_plan_ref")
                != origin["generation_plan_ref"]
                or diagnosis.get("task_spec_ref") != origin["task_spec_ref"]
                or not diagnosis.get("findings")
            ):
                raise InterpretationRunRejected()
        else:
            raise InterpretationRunRejected()
        closure = GenerationSubmissions(
            directory, binding=binding, origin=origin, plan=plan
        ).close()
        # The closure fences future submissions. Re-read the now closed set,
        # including any job completed while waiting for an active boundary.
        with mutation_lock(directory / (prefix + "run.lock"), timeout_s=30):
            rows = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(directory.glob(prefix + "*.json"))
            ]
            for row in rows:
                _verify_row(row, binding, origin)
            if (
                sorted(row["observation_id"] for row in rows)
                != closure["observation_refs"]
            ):
                raise InterpretationRunRejected()
            rows.sort(key=lambda row: row["job_id"])
            metrics = project_run_measurements(rows, origin=origin)
            receipt = seal(
                {
                    "schema": "adaos.builder.generation_failure_completion.v1",
                    "run_id": binding["run_id"],
                    "origin_ref": origin["origin_id"],
                    "closure_ref": closure["closure_id"],
                    "outcome": outcome,
                    "cause_ref": cause_ref,
                    "measurement_digest": digest(metrics),
                    "observation_refs": closure["observation_refs"],
                    "whole_turn_accounting_complete": False,
                    "inference_performed_by_completion": False,
                },
                id_key="completion_id",
                prefix="generation-completion:",
            )
            target = directory / (
                "generation-completion-" + binding["run_id"].partition(":")[2] + ".json"
            )
            if target.exists():
                if json.loads(target.read_text(encoding="utf-8")) != receipt:
                    raise InterpretationRunRejected()
            else:
                atomic_write_json(target, receipt)
            result = workflow.record_generation_observation(
                binding,
                origin=origin,
                plan=plan,
                execution_metrics=metrics,
                evidence_refs=[
                    closure["closure_id"],
                    cause_ref,
                    receipt["completion_id"],
                ],
                terminal_status=outcome,
                completion_ref=receipt["completion_id"],
            )
            if not result.get("ok") and not result.get("observation_recorded"):
                raise InterpretationRunRejected()
            return {**result, "completion": receipt, "closure": closure}
    except InterpretationRunRejected:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InterpretationRunRejected() from error
