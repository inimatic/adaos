"""Replay exact provider results and admitted bounded repairs, without inference.

The original candidate is reconstructed from protected output, not a caller's
merged document. Each child rechecks its frozen diagnosis, scope and reducer.
This derives source; full candidate/task/preservation admission is still G7/G8.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from adaos.services.builder_context import digest

from .generation_outputs import GenerationOutputs
from .task_spec import seal, verify_identity


def reducer_digest():
    from . import semantic_repair

    return digest({
        "source_replay": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "bounded_reducers": hashlib.sha256(Path(semantic_repair.__file__).read_bytes()).hexdigest(),
    })


class GenerationCandidateSources:
    def __init__(self, state_dir):
        self.outputs = GenerationOutputs(state_dir)

    def resolve(self, *, output_ref=None, journal_dir, binding, origin, plan, wire, _seen=()):
        from .generation_candidates import GenerationCandidateRejected, _json_object

        key = wire["request_id"]
        if key in _seen or len(_seen) >= 8:
            raise GenerationCandidateRejected("candidate.repair_lineage_cycle_or_bound")
        context = {"journal_dir": journal_dir, "binding": binding, "origin": origin, "plan": plan, "wire": wire}
        reference = self.outputs.reference(**context)
        if output_ref is not None and reference != output_ref:
            # Preserve the existing protected-output admission outcome; this
            # is a wrong source reference, not a repairable candidate defect.
            from .interpretation_runs import InterpretationRunRejected
            raise InterpretationRunRejected()
        candidate = _json_object(self.outputs.load(reference, **context))
        stages = plan.get("stages") or []
        if not stages:
            raise GenerationCandidateRejected("candidate.output_contract_required")
        contract = stages[-1]["output_contract"]
        full_contract = "adaos.builder.semantic_prototype_candidate.v2"
        lineage = []
        if plan.get("parent_generation_plan_ref"):
            from . import semantic_repair
            from .generation_authoring import _result_context

            admission = self.outputs._read(Path(journal_dir) / (
                "repair-plan-" + plan["generation_plan_id"].partition(":")[2] + ".json"))
            schema = admission["provider_output_schema"]
            if (admission["generation_plan"] != plan or digest(schema) != plan["output_schema_digest"]
                    or plan.get("repair_reducer_digest") != reducer_digest()
                    or digest(admission.get("scope")) != plan["repair_scope_digest"]
                    or digest(admission.get("findings")) != plan["repair_findings_digest"]
                    or not re.fullmatch(r"repair-diagnosis:[a-f0-9]{24}", str(plan.get("repair_diagnosis_ref")))):
                raise GenerationCandidateRejected("candidate.retained_repair_conflict")
            diagnosis = self.outputs._read(Path(journal_dir) / (
                plan["repair_diagnosis_ref"].replace(":", "-") + ".json"))
            verify_identity(diagnosis, id_key="diagnosis_id", prefix="repair-diagnosis:")
            if (diagnosis["digest"] != plan["repair_diagnosis_digest"]
                    or diagnosis["findings"] != admission["findings"]
                    or diagnosis.get("paid_candidate_repair_allowed") is not True
                    or diagnosis["candidate_digest"] != plan["repair_candidate_digest"]):
                raise GenerationCandidateRejected("candidate.retained_repair_diagnosis_conflict")
            parent = _result_context(journal_dir, plan["parent_request_id"])
            if (parent["wire"]["job_id"] != plan["parent_provider_job_id"]
                    or parent["origin"] != origin):
                raise GenerationCandidateRejected("candidate.retained_repair_parent_conflict")
            source = self.resolve(**parent, _seen=(*_seen, key))
            if (admission.get("source_receipt") != source["source_receipt"]
                    or plan.get("repair_source_ref") != source["source_receipt"]["source_id"]
                    or digest(source["candidate"]) != plan["repair_candidate_digest"]):
                raise GenerationCandidateRejected("candidate.retained_repair_source_conflict")
            if not Draft202012Validator(schema).is_valid(candidate):
                raise GenerationCandidateRejected("candidate.provider_grammar_mismatch")
            kind = admission["repair_kind"]
            if kind == "semantic_candidate":
                if admission["scope"] is not None or contract != full_contract:
                    raise GenerationCandidateRejected("candidate.retained_repair_scope_conflict")
            else:
                family = kind.removeprefix("semantic_").removesuffix("_repair")
                if family not in {"record", "collection", "state", "binding", "reference"}:
                    raise GenerationCandidateRejected("candidate.repair_reducer_unknown")
                scope = getattr(semantic_repair, "prepare_" + family + "_repair")(
                    source["candidate"], admission["findings"])
                if (scope is None or scope["output_schema"] != schema
                        or {k: v for k, v in scope.items() if k != "output_schema"} != admission["scope"]):
                    raise GenerationCandidateRejected("candidate.retained_repair_scope_conflict")
                candidate = getattr(semantic_repair, "apply_" + family + "_repair")(
                    source["candidate"], candidate, admission["findings"])
            lineage = [*source["source_receipt"]["lineage"], {
                "parent_source_ref": source["source_receipt"]["source_id"],
                "output_ref": reference, "plan_ref": plan["generation_plan_id"],
                "diagnosis_ref": diagnosis["diagnosis_id"], "kind": kind,
                "candidate_digest": digest(candidate), "reducer_digest": reducer_digest(),
            }]
        elif contract != full_contract:
            raise GenerationCandidateRejected("candidate.scoped_repair_parent_required")
        if candidate.get("schema") != full_contract:
            raise GenerationCandidateRejected("candidate.full_v2_required")
        receipt = seal({
            "schema": "adaos.builder.generation_candidate_source.v1",
            "output_ref": reference, "binding_ref": binding["binding_id"],
            "origin_ref": origin["origin_id"], "plan_ref": plan["generation_plan_id"],
            "request_id": key, "job_id": wire["job_id"],
            "candidate_digest": digest(candidate), "lineage": lineage,
        }, id_key="source_id", prefix="candidate-source:")
        return {"candidate": candidate, "source_receipt": receipt}
