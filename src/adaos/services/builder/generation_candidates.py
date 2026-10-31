"""Compile retained provider text against its original admitted TaskSpec.

This internal full-candidate rail does not publish or certify runtime behavior.
Scoped repair patches need their own replayable reducer and are not treated as
complete candidates. No model call or current-selection lookup occurs here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest

from .generation_origin import verify_origin
from .generation_outputs import GenerationOutputs
from .task_spec import seal, verify_identity


class GenerationCandidateRejected(ValueError):
    def __init__(self, code):
        self.findings = [
            {
                "code": code,
                "gate": "G7",
                "owner": "candidate_admission",
                "detail": "Recover the exact admitted input/output or inspect the candidate findings",
            }
        ]
        super().__init__(code)


def _compiler_identity():
    from adaos.sdk.builder import prototype
    from adaos.sdk.developer import prototypes

    from . import prototype_publications
    from .semantic_admission import compiler_digest

    return digest(
        {
            "semantic_compiler": compiler_digest(),
            "sdk_candidate_boundary": hashlib.sha256(
                Path(prototype.__file__).read_bytes()
            ).hexdigest(),
            "output_candidate_boundary": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "publication_lowering": hashlib.sha256(
                Path(prototype_publications.__file__).read_bytes()
            ).hexdigest(),
            "resource_derivation": hashlib.sha256(
                Path(prototypes.__file__).read_bytes()
            ).hexdigest(),
        }
    )


def _json_object(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise GenerationCandidateRejected("candidate.duplicate_json_key")
            result[key] = value
        return result

    def constant(value):
        raise GenerationCandidateRejected("candidate.non_json_number")

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise GenerationCandidateRejected("candidate.non_json_number")
        return result

    try:
        result = json.loads(
            text, object_pairs_hook=pairs, parse_constant=constant, parse_float=number
        )
    except (ValueError, RecursionError) as error:
        if isinstance(error, GenerationCandidateRejected):
            raise
        raise GenerationCandidateRejected("candidate.invalid_json") from error
    if not isinstance(result, dict):
        raise GenerationCandidateRejected("candidate.object_required")
    return result


class GenerationCandidates:
    def __init__(self, state_dir):
        self.outputs = GenerationOutputs(state_dir)
        self.root = self.outputs.root.parent / "generation_candidates"

    def compile(self, *, output_ref, journal_dir, binding, origin, plan, wire):
        """Prepare a source-bound semantic candidate, never an accepted head."""
        try:
            return self._compile(
                output_ref=output_ref,
                journal_dir=journal_dir,
                binding=binding,
                origin=origin,
                plan=plan,
                wire=wire,
            )
        except GenerationCandidateRejected:
            raise
        except (OSError, KeyError, TypeError, ValueError) as error:
            # Keep the established typed semantic/preservation findings for
            # diagnosis. Never include raw provider output in this envelope.
            if getattr(error, "findings", None):
                raise
            raise GenerationCandidateRejected(
                "candidate.retained_input_conflict"
            ) from error

    def _compile(self, *, output_ref, journal_dir, binding, origin, plan, wire):
        text = self.outputs.load(
            output_ref,
            journal_dir=journal_dir,
            binding=binding,
            origin=origin,
            plan=plan,
            wire=wire,
        )
        if (
            not plan.get("stages")
            or plan["stages"][-1]["output_contract"]
            != "adaos.builder.semantic_prototype_candidate.v2"
        ):
            raise GenerationCandidateRejected(
                "candidate.scoped_repair_reducer_required"
            )
        if not re.fullmatch(
            r"generation-plan:[a-f0-9]{24}", str(origin["generation_plan_ref"])
        ):
            raise GenerationCandidateRejected("candidate.retained_input_conflict")
        parent = GenerationOutputs._read(
            Path(journal_dir)
            / (origin["generation_plan_ref"].replace(":", "-") + ".json")
        )
        task, brief, primary_plan = (
            parent["task_spec"],
            parent["brief"],
            parent["generation_plan"],
        )
        verify_origin(origin, primary_plan, task=task)
        verify_origin(origin, plan, task=task)
        verify_identity(brief, id_key="brief_id", prefix="brief:")
        if (
            task.get("interpretation_status") != "admitted"
            or not brief.get("task_contract")
            or task["brief_ref"] != brief["brief_id"]
            or task["brief_digest"] != brief["digest"]
            or any(
                current["task_spec_digest"] != task["digest"]
                or current["brief_digest"] != brief["digest"]
                or current["brief_ref"] != brief["brief_id"]
                for current in (primary_plan, plan)
            )
        ):
            raise GenerationCandidateRejected("candidate.admitted_task_required")
        baseline = parent.get("baseline")
        if (digest(baseline) if baseline else None) != plan["baseline_digest"]:
            raise GenerationCandidateRejected("candidate.retained_baseline_conflict")
        admission = parent
        if plan.get("parent_generation_plan_ref"):
            admission = GenerationOutputs._read(
                Path(journal_dir)
                / (
                    "repair-plan-"
                    + plan["generation_plan_id"].partition(":")[2]
                    + ".json"
                )
            )
        schema = admission["provider_output_schema"]
        if (
            admission["generation_plan"] != plan
            or digest(schema) != plan["output_schema_digest"]
        ):
            raise GenerationCandidateRejected("candidate.retained_grammar_conflict")
        candidate = _json_object(text)
        if candidate.get("schema") != "adaos.builder.semantic_prototype_candidate.v2":
            raise GenerationCandidateRejected("candidate.full_v2_required")
        if not Draft202012Validator(schema).is_valid(candidate):
            raise GenerationCandidateRejected("candidate.provider_grammar_mismatch")
        if (
            isinstance(baseline, dict)
            and baseline.get("schema") == "adaos.builder.semantic_compiler_view.v1"
        ):
            baseline = baseline["document"]
        from adaos.sdk.builder.prototype import compile_semantic_candidate

        compiled = compile_semantic_candidate(
            candidate, brief=brief, project_ref=origin["project_ref"], previous=baseline
        )
        receipt = seal(
            {
                "schema": "adaos.builder.generation_candidate.v1",
                "output_ref": output_ref,
                "run_id": binding["run_id"],
                "origin_ref": origin["origin_id"],
                "binding_ref": binding["binding_id"],
                "generation_plan_ref": plan["generation_plan_id"],
                "task_spec_ref": task["task_spec_id"],
                "brief_digest": brief["digest"],
                "baseline_digest": plan["baseline_digest"],
                "compiler_digest": _compiler_identity(),
                "candidate_digest": digest(candidate),
                "semantic_digest": compiled["semantic_digest"],
                "semantic_document": compiled["semantic_document"],
                "normalizations": compiled.get("normalizations", []),
                "task_acceptance": compiled.get("task_acceptance"),
                "preservation_receipt": compiled.get("preservation_receipt"),
                "head_advanced": False,
                "runtime_accepted": False,
            },
            id_key="candidate_id",
            prefix="generation-candidate:",
        )
        payload = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        from .generation_outputs import _MAX_BYTES

        if len(payload) > _MAX_BYTES:
            raise GenerationCandidateRejected("candidate.storage_bound_exceeded")
        path = self.outputs._safe(
            self.root / (receipt["candidate_id"].partition(":")[2] + ".json")
        )
        with mutation_lock(self.outputs._safe(path.with_suffix(".lock")), timeout_s=30):
            self.outputs._safe(path)
            if path.exists():
                if self.outputs._read(path) != receipt:
                    raise GenerationCandidateRejected(
                        "candidate.retained_compilation_conflict"
                    )
            else:
                atomic_write_json(path, receipt)
        return receipt
