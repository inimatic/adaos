"""Append-only, zero-inference review of a rejected first interpretation.

An old result is never relabelled in its original journal. Reuse requires exact
source/scope/authority equivalence and a fresh full Core admission. This bounded
path does not migrate accepted history or already materialized prototypes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from adaos.services.builder_context import digest
from .interpretation_delivery import decode_proposal
from .task_interpretation import admit_interpretation, TaskInterpretationRejected
from .task_spec import seal, verify_identity
from .generation_plan import context_receipt
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


def validator_digest():
    folder = Path(__file__).resolve().parent
    paths = [
        folder / name
        for name in (
            "task_interpretation.py",
            "source_literals.py",
            "source_operators.py",
            "source_relations.py",
            "source_obligations.py",
            "timestamp_labels.py",
            "task_spec.py",
            "task_assertions.py",
            "interpretation_delivery.py",
            "interpretation_revalidation.py",
        )
    ]
    paths += [
        folder.parent / "builder_intent.py",
        folder.parents[1] / "abi/builder.task_interpretation.v1.schema.json",
        folder.parents[1] / "abi/builder.prototype_brief.v1.schema.json",
        folder.parents[1] / "abi/builder.intent.v1.schema.json",
    ]
    return digest(
        {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    )


def recover_rejected_first_interpretation(
    intent, *, previous_brief, journal_dir, current_seed, current_semantic, exact_replay
):
    if previous_brief or current_semantic:
        return None
    verify_identity(intent, id_key="intent_id", prefix="intent:")
    directory = Path(journal_dir)
    matches = []
    for path in directory.glob("builder-interpretation-*.json"):
        original_bytes = path.read_bytes()
        record = json.loads(original_bytes)
        if (
            record.get("schema") != "adaos.builder.interpretation_attempt.v1"
            or (record.get("admission") or {}).get("status") != "rejected"
            or (record.get("result") or {}).get("status")
            not in {"succeeded", "completed"}
        ):
            continue
        old_task = record["task_spec"]
        verify_identity(old_task, id_key="task_spec_id", prefix="task:")
        if record.get("context") != context_receipt(
            record["messages"], generation_options=record["options"]
        ):
            raise TaskInterpretationRejected(
                [
                    {
                        "code": "task.revalidation_input_identity_invalid",
                        "gate": "G5",
                        "owner": "context_compilation",
                        "detail": "Retained interpretation input context cannot be verified",
                    }
                ]
            )
        evidence = [
            json.loads(row["content"]).get("interpretation_request")
            for row in record["messages"]
            if row["role"] == "user"
        ]
        if (
            len(evidence) != 1
            or not isinstance(evidence[0], dict)
            or evidence[0].get("prior_active_requirements") != []
            or evidence[0].get("prior_literal_assertions") != []
            or evidence[0].get("history_admission") is not None
        ):
            continue
        if (
            old_task["source"]["statement"] != intent["statement"]
            or old_task["scope"] != intent["scope"]
            or old_task["authority"] != intent["authority"]
        ):
            continue
        # Reconstruct only to VERIFY the original content identity. No guessed
        # locale/ref/authority is accepted when the frozen hash does not match.
        old_intent = copy.deepcopy(intent)
        old_intent["source"] = copy.deepcopy(old_task["source"]["provenance"])
        old_intent = seal(old_intent, id_key="intent_id", prefix="intent:")
        if old_intent["digest"] != old_task["source"]["digest"]:
            continue
        try:
            proposal = decode_proposal(record)
            task, brief = admit_interpretation(old_task, proposal, old_intent)
        except TaskInterpretationRejected as error:
            # A genuine semantic rejection remains rejected. Unknown transport
            # or broken context cannot authorize silently buying a replacement.
            if any(row.get("gate") != "G3" for row in error.findings):
                raise
            continue
        if current_seed.get("interface_context") != brief.get("interface_context"):
            continue
        validation = seal(
            {
                "schema": "adaos.builder.interpretation_validation.v1",
                "status": "admitted",
                "admission": {
                    "status": "admitted",
                    "basis": "current_core_revalidation_of_unchanged_retained_result",
                },
                "original_request_id": record["request_id"],
                "original_job_id": record["job_id"],
                "original_journal_sha256": hashlib.sha256(original_bytes).hexdigest(),
                "original_admission_digest": digest(record["admission"]),
                "validator_digest": validator_digest(),
                "proposal_digest": digest(proposal),
                "admitted_task": task,
                "brief": brief,
                "provider_calls": 0,
                "model_calls": 0,
                "historical_result_corrected": False,
            },
            id_key="validation_id",
            prefix="interpretation-validation:",
        )
        matches.append((record, validation))
    if not matches:
        return None
    if len(matches) != 1:
        raise TaskInterpretationRejected(
            [
                {
                    "code": "task.revalidation_ambiguous",
                    "gate": "G3",
                    "owner": "task_interpretation",
                    "detail": "Several currently admissible retained results require explicit selection",
                }
            ]
        )
    record, validation = matches[0]
    target = directory / (
        "interpretation-validation-" + validation["digest"][7:39] + ".json"
    )
    with mutation_lock(directory / ".interpretation-validation.lock", timeout_s=30):
        if target.exists():
            if json.loads(target.read_text(encoding="utf-8")) != validation:
                raise TaskInterpretationRejected(
                    [
                        {
                            "code": "task.revalidation_identity_conflict",
                            "gate": "G5",
                            "owner": "context_compilation",
                            "detail": "The immutable interpretation review cannot be verified",
                        }
                    ]
                )
        else:
            atomic_write_json(target, validation)
    replay = exact_replay(intent, validation["brief"], journal_dir, current_seed)
    if replay is None:
        return None
    task, brief, receipt = replay
    receipt.update(
        revalidation_ref=validation["validation_id"],
        revalidated_interpretation_job_id=record["job_id"],
        original_interpretation_request_id=record["request_id"],
        provider_calls=0,
        validator_digest=validation["validator_digest"],
        historical_result_corrected=False,
    )
    return task, brief, receipt
