"""Evidence-owned compound generation/head completion, not native activation.

Lock order is submission closure, observation Run, workflow, publication and
resource. A prepared success receipt alone grants no authoring authority; only
the authoritative compound workflow write accepts its publication and Run.
"""

from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest

from .generation_candidates import GenerationCandidates
from .generation_observations import _verify_row, project_run_measurements
from .generation_outputs import GenerationOutputs
from .generation_submissions import GenerationSubmissions
from .interpretation_runs import InterpretationRunRejected
from .prototype_publications import PrototypePublications
from .task_spec import seal

_SUCCESS_SCOPE = ContextVar("builder_generation_success", default=None)


@contextmanager
def _issued(workflow, payload):
    capability = object()
    scope = {
        "workflow": workflow,
        "payload": copy.deepcopy(payload),
        "capability": capability,
        "thread": threading.get_ident(),
        "active": True,
    }
    token = _SUCCESS_SCOPE.set(scope)
    try:
        yield capability
    finally:
        scope["active"] = False
        _SUCCESS_SCOPE.reset(token)


def admitted_success(capability, workflow, publication_ref):
    """Called only while the issuer owns the observation lock; never relock it."""
    scope = _SUCCESS_SCOPE.get()
    if (
        not scope
        or not scope["active"]
        or scope["capability"] is not capability
        or scope["workflow"] is not workflow
        or scope["thread"] != threading.get_ident()
        or scope["payload"]["completion"]["publication_ref"] != publication_ref
    ):
        raise InterpretationRunRejected()
    from .generation_candidates import _compiler_identity

    if scope["payload"]["completion"]["compiler_digest"] != _compiler_identity():
        raise InterpretationRunRejected()
    return copy.deepcopy(scope["payload"])


def _row(workflow, payload, *, replay=False):
    origin, binding = payload["origin"], payload["binding"]
    change = workflow.get("change") or workflow.get("change_set") or {}
    if change.get("change_id") != origin["change_id"] and replay:
        record = (workflow.get("change_portfolio") or {}).get(origin["change_id"]) or {}
        change = record.get("change") or record.get("change_set") or {}
    row = next(
        (row for row in change.get("runs", []) if row["run_id"] == binding["run_id"]),
        None,
    )
    if (
        change.get("change_id") != origin["change_id"]
        or not row
        or origin["task_spec_ref"] not in row.get("input_refs", [])
        or origin["generation_plan_ref"] not in row.get("input_refs", [])
        or origin["origin_id"] not in row.get("evidence_refs", [])
        or binding["binding_id"] not in row.get("evidence_refs", [])
        or row.get("execution_metrics") != payload["metrics"]
    ):
        raise InterpretationRunRejected()
    return change, row


def record_success(workflow, payload, *, changed_at, publication_ref):
    """Mutate the reserved row in memory before the SAME head/state replacement."""
    from .workflow import _change_set_compatibility

    origin, completion = payload["origin"], payload["completion"]
    if (
        workflow.get("generation") != origin["origin_generation"] + 1
        or workflow.get("prototype", {}).get("publication_ref") != publication_ref
    ):
        raise InterpretationRunRejected()
    change, row = _row(workflow, payload)
    if row["status"] not in {"queued", "running"}:
        raise InterpretationRunRejected()
    evidence = list(
        dict.fromkeys(
            [
                *row.get("evidence_refs", []),
                completion["completion_id"],
                completion["candidate_ref"],
                completion["output_ref"],
                completion["closure_ref"],
                completion["preparation_ref"],
                completion["materialization_ref"],
                *completion["observation_refs"],
            ]
        )
    )
    outputs = list(
        dict.fromkeys(
            [
                *row.get("output_refs", []),
                publication_ref,
                "ui_revision:" + completion["revision"],
            ]
        )
    )
    if len(evidence) > 100 or len(outputs) > 100:
        raise InterpretationRunRejected()
    row.update(
        activity="prototype_generation_succeeded",
        status="succeeded",
        completed_at=changed_at,
        error=None,
        evidence_refs=evidence,
        output_refs=outputs,
    )
    workflow["change"] = change
    workflow["change_set"] = _change_set_compatibility(change)


def assert_replay(workflow, payload, publication_ref):
    _, row = _row(workflow, payload, replay=True)
    if (
        row["status"] != "succeeded"
        or payload["completion"]["completion_id"] not in row.get("evidence_refs", [])
        or publication_ref not in row.get("output_refs", [])
    ):
        raise InterpretationRunRejected()


def complete_generation_success(
    workflow, *, journal_dir, binding, origin, plan, wire, output_ref, publication_ref
):
    """Accept one fully compiled output and finish its Run without another call.

    Internal full-candidate protocol only. The supplied materialized publication
    must equal the source-derived candidate and its frozen logical owner. Failed
    precommit attempts leave the Run nonterminal and allow zero-model reprepare.
    """
    candidate = GenerationCandidates(workflow.state_dir).compile(
        output_ref=output_ref,
        journal_dir=journal_dir,
        binding=binding,
        origin=origin,
        plan=plan,
        wire=wire,
    )
    prepared = PrototypePublications(workflow.state_dir).ready(publication_ref)
    value, materialization = prepared["preparation"], prepared["materialization"]
    scope = {
        key: origin[key]
        for key in ("project_ref", "object_type", "object_id", "manifest_digest")
    }
    owner = origin["object_type"] + ":" + origin["object_id"]
    if (
        origin["object_type"] != "scenario"
        or value["seed"]["project_ref"] != owner
        or value["seed"]["change_id"] != origin["change_id"]
        or value["document"] != candidate["semantic_document"]
        or (value.get("brief") or {}).get("digest") != candidate["brief_digest"]
        or value["seed"].get("compiler_digest") != candidate["compiler_digest"]
        or (
            value["seed"].get("source_scope") is not None
            and value["seed"]["source_scope"] != scope
        )
        or (
            value["seed"].get("source_scope") is None and origin["project_ref"] != owner
        )
    ):
        raise InterpretationRunRejected()
    directory = Path(journal_dir)
    closure = GenerationSubmissions(
        directory, binding=binding, origin=origin, plan=plan
    ).close()
    prefix = "generation-observation-" + binding["run_id"].partition(":")[2] + "-"
    # Do not acquire this lock inside workflow/publication commit: observers
    # already take Run -> workflow, and the reverse order would deadlock.
    with mutation_lock(directory / (prefix + "run.lock"), timeout_s=30):
        rows = [
            GenerationOutputs._read(path)
            for path in sorted(directory.glob(prefix + "*.json"))
        ]
        for row in rows:
            _verify_row(row, binding, origin)
        if sorted(row["observation_id"] for row in rows) != closure["observation_refs"]:
            raise InterpretationRunRejected()
        metrics = project_run_measurements(
            sorted(rows, key=lambda row: row["job_id"]), origin=origin
        )
        completion = seal(
            {
                "schema": "adaos.builder.generation_success_completion.v1",
                "run_id": binding["run_id"],
                "origin_ref": origin["origin_id"],
                "publication_ref": publication_ref,
                "revision": value["seed"]["revision"],
                "candidate_ref": candidate["candidate_id"],
                "compiler_digest": candidate["compiler_digest"],
                "output_ref": output_ref,
                "closure_ref": closure["closure_id"],
                "preparation_ref": value["preparation_id"],
                "materialization_ref": materialization["materialization_id"],
                "measurement_digest": digest(metrics),
                "observation_refs": closure["observation_refs"],
                "whole_turn_accounting_complete": False,
                "runtime_accepted": False,
            },
            id_key="completion_id",
            prefix="generation-completion:",
        )
        # Per-publication attempt, not an irreversible one-candidate lock on a
        # paid Run. A changed base can be reforked locally after a failed commit.
        target = GenerationOutputs._safe(
            Path(workflow.state_dir)
            / "builder"
            / "generation_success"
            / (completion["completion_id"].partition(":")[2] + ".json")
        )
        with mutation_lock(
            GenerationOutputs._safe(target.with_suffix(".lock")), timeout_s=30
        ):
            GenerationOutputs._safe(target)
            if target.exists():
                if GenerationOutputs._read(target) != completion:
                    raise InterpretationRunRejected()
            else:
                atomic_write_json(target, completion)
        payload = {
            "origin": origin,
            "binding": binding,
            "metrics": metrics,
            "completion": completion,
        }
        with _issued(workflow, payload) as capability:
            result = workflow.commit_prepared_prototype(
                origin["object_type"],
                origin["object_id"],
                publication_ref,
                expected_generation=origin["origin_generation"],
                _generation_success=capability,
            )
    return {
        **result,
        "run_id": binding["run_id"],
        "completion_ref": completion["completion_id"],
        "candidate_ref": candidate["candidate_id"],
    }
