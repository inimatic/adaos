"""Internal native bridge from an exact paid result to prepared publication.

No caller-supplied semantic/UI artifacts, source writes or provider calls. A
prepared namespace remains invisible until compound canonical Run/head commit.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

from adaos.services.builder_context import digest

from .authoring_scope import verify_frozen_authoring_scope
from .generation_candidates import GenerationCandidates
from .generation_origin import verify_wire_origin
from .generation_outputs import GenerationOutputs
from .generation_runs import wire_run_binding
from .generation_success import complete_generation_success
from .interpretation_runs import InterpretationRunRejected
from .prototype_publications import PrototypePublications
from .task_spec import verify_identity


def _result_context(journal_dir, request_id, output_ref=None):
    try:
        if not isinstance(request_id, str) or not 0 < len(request_id) <= 4096:
            raise InterpretationRunRejected()
        directory = Path(journal_dir)
        wire = GenerationOutputs._read(
            directory / ("wire-" + digest(request_id)[7:39] + ".json")
        )
        if wire.get("request_id") != request_id:
            raise InterpretationRunRejected()
        origin = verify_wire_origin(directory, wire)
        binding = wire_run_binding(directory, wire, origin)
        if origin is None or binding is None:
            raise InterpretationRunRejected()
        admission = wire["context_admission"]
        ref = admission["generation_plan_ref"]
        if not isinstance(ref, str) or not re.fullmatch(
            r"generation-plan:[a-f0-9]{24}", ref
        ):
            raise InterpretationRunRejected()
        name = (
            "repair-plan-" + ref.partition(":")[2]
            if admission.get("parent_generation_plan_ref")
            else ref.replace(":", "-")
        )
        plan = GenerationOutputs._read(directory / (name + ".json"))["generation_plan"]
        return {
            "journal_dir": directory,
            "wire": wire,
            "origin": origin,
            "binding": binding,
            "plan": plan,
            "output_ref": output_ref,
        }
    except InterpretationRunRejected:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InterpretationRunRejected() from error


def _prepared_result(value, inputs, candidate_ref):
    origin = inputs["origin"]
    return {
        "schema": "adaos.builder.generation_publication_prepared.v1",
        "publication_ref": value["publication_ref"],
        "preparation_ref": value["preparation_id"],
        "candidate_ref": candidate_ref,
        "output_ref": inputs["output_ref"],
        "run_id": inputs["binding"]["run_id"],
        "revision": value["seed"]["revision"],
        "expected_generation": origin["origin_generation"],
        "source_scope": {
            key: origin[key]
            for key in ("project_ref", "object_type", "object_id", "manifest_digest")
        },
        "head_advanced": False,
    }


def _completed_preparation(workflow, inputs):
    """Recover only this already accepted current head, never reserve a new Run."""
    origin, binding = inputs["origin"], inputs["binding"]
    current = workflow.lifecycle_state(origin["object_type"], origin["object_id"])
    change = current.get("change") or {}
    if change.get("change_id") != origin["change_id"]:
        record = (current.get("change_portfolio") or {}).get(origin["change_id"]) or {}
        change = record.get("change") or {}
    row = next(
        (row for row in change.get("runs", []) if row["run_id"] == binding["run_id"]),
        None,
    )
    if row is None or row.get("status") != "succeeded":
        return None
    evidence = row.get("evidence_refs", [])
    references = [
        ref
        for ref in evidence
        if re.fullmatch(r"generation-completion:[a-f0-9]{24}", ref)
    ]
    if (
        len(references) != 1
        or inputs["output_ref"] not in evidence
        or binding["binding_id"] not in evidence
        or origin["origin_id"] not in evidence
        or origin["task_spec_ref"] not in row.get("input_refs", [])
        or origin["generation_plan_ref"] not in row.get("input_refs", [])
    ):
        raise InterpretationRunRejected()
    completion = GenerationOutputs._read(
        Path(workflow.state_dir)
        / "builder"
        / "generation_success"
        / (references[0].partition(":")[2] + ".json")
    )
    verify_identity(completion, id_key="completion_id", prefix="generation-completion:")
    publication_ref = completion.get("publication_ref")
    if (
        completion.get("schema") != "adaos.builder.generation_success_completion.v1"
        or completion["completion_id"] != references[0]
        or completion.get("run_id") != binding["run_id"]
        or completion.get("origin_ref") != origin["origin_id"]
        or completion.get("output_ref") != inputs["output_ref"]
        or completion.get("candidate_ref") not in evidence
        or completion.get("measurement_digest") != digest(row.get("execution_metrics"))
        or publication_ref not in row.get("output_refs", [])
        or current["prototype"].get("publication_ref") != publication_ref
    ):
        raise InterpretationRunRejected()
    GenerationOutputs(workflow.state_dir).load(**inputs)
    value = PrototypePublications(workflow.state_dir).load(publication_ref)
    if (
        value["seed"].get("compiler_digest") != completion.get("compiler_digest")
        or value["seed"]["project_ref"]
        != origin["object_type"] + ":" + origin["object_id"]
        or value["seed"]["change_id"] != origin["change_id"]
        or value["seed"]["revision"] != completion.get("revision")
        or value["preparation_id"] != completion.get("preparation_ref")
    ):
        raise InterpretationRunRejected()
    return _prepared_result(value, inputs, completion["candidate_ref"])


def prepare_generation_publication(workflow, *, journal_dir, request_id, output_ref):
    """Derive the next revision from Core, never advance mirrors/current.txt."""
    from .prototype_data_owners import assert_no_unmigrated_project_data
    from .workflow import _LOCK

    inputs = _result_context(journal_dir, request_id, output_ref)
    with _LOCK:
        completed = _completed_preparation(workflow, inputs)
        if completed is not None:
            return completed
    candidate = GenerationCandidates(workflow.state_dir).compile(**inputs)
    origin = inputs["origin"]
    scope = {
        key: origin[key]
        for key in ("project_ref", "object_type", "object_id", "manifest_digest")
    }
    with _LOCK:
        verify_frozen_authoring_scope(workflow, scope)
        current = workflow.lifecycle_state(origin["object_type"], origin["object_id"])
        if (
            origin["object_type"] != "scenario"
            or current["archived"]
            or current["generation"] != origin["origin_generation"]
            or (current.get("change") or {}).get("change_id") != origin["change_id"]
            or not current["capabilities"]["can_edit_prototype"]
        ):
            raise InterpretationRunRejected()
        reserved = workflow.record_generation_preparation(
            inputs["binding"], origin=origin, plan=inputs["plan"], existing_only=True
        )
        if not reserved.get("ok"):
            raise InterpretationRunRejected()
        head = current["prototype"]
        base = head.get("head_revision")
        if base is not None and (
            not isinstance(base, str) or not re.fullmatch(r"[0-9]{3,6}", base)
        ):
            raise InterpretationRunRejected()
        if (
            scope["project_ref"].startswith("project:")
            and head.get("publication_ref") is None
        ):
            assert_no_unmigrated_project_data(workflow.state_dir, scope["project_ref"])
        parent = GenerationOutputs._read(
            Path(journal_dir)
            / (origin["generation_plan_ref"].replace(":", "-") + ".json")
        )
        brief = parent["brief"]
        if brief.get("digest") != candidate["brief_digest"]:
            raise InterpretationRunRejected()
        store = PrototypePublications(workflow.state_dir)
        value = store.prepare(
            candidate["semantic_document"],
            project_ref=origin["object_type"] + ":" + origin["object_id"],
            change_id=origin["change_id"],
            revision=f"{int(base or '0') + 1:03d}",
            base_revision=base,
            previous_publication_ref=head.get("publication_ref"),
            brief=brief,
            source_scope=scope,
            compiler_digest=candidate["compiler_digest"],
        )
    store.materialize(value["publication_ref"])
    return _prepared_result(value, inputs, candidate["candidate_id"])


def commit_generation_publication(
    workflow, *, journal_dir, request_id, output_ref, publication_ref
):
    inputs = _result_context(journal_dir, request_id, output_ref)
    return complete_generation_success(
        workflow, **inputs, publication_ref=publication_ref
    )


def finish_generation_failure(
    workflow, *, journal_dir, request_id, outcome, cause_ref=None
):
    """Finish only a provable terminal failure; unknown outcomes remain pending."""
    from .generation_runs import GenerationRuns

    inputs = _result_context(journal_dir, request_id)
    return GenerationRuns(workflow).finish_failure(
        inputs["binding"],
        inputs["origin"],
        inputs["plan"],
        journal_dir=journal_dir,
        outcome=outcome,
        cause_ref=cause_ref or "llm_job:" + inputs["wire"]["job_id"],
    )


def generation_publication_projection(workflow, *, publication_ref):
    """Read exact committed artifacts, never promote an orphan preparation."""
    from .workflow import _LOCK

    store = PrototypePublications(workflow.state_dir)
    index = store.index(publication_ref)
    owner = index["seed"]["project_ref"]
    kind, _, identity = owner.partition(":")
    if kind != "scenario":
        raise InterpretationRunRejected()
    with _LOCK:
        current = workflow.lifecycle_state(kind, identity)["prototype"]
        head = current.get("publication_ref")
        if head is None or publication_ref not in {
            row["publication_ref"] for row in store.lineage(head, project_ref=owner)
        }:
            raise InterpretationRunRejected()
        value = store.load(publication_ref)
        return copy.deepcopy(
            {
                "schema": "adaos.builder.generation_publication_projection.v1",
                "publication_ref": publication_ref,
                "project_ref": owner,
                "source_scope": value["seed"].get("source_scope"),
                "revision": value["seed"]["revision"],
                "is_current": head == publication_ref,
                "prototype_stage": "alpha",
                "webui": value["compiled"]["webui"],
                "webui_digest": value["webui_digest"],
                "semantic_document": value["document"],
                "semantic_digest": value["compiled"]["semantic_digest"],
                "prototype_brief": value["brief"],
                "locale_dictionaries": value["compiled"].get("locale_dictionaries", {}),
            }
        )
