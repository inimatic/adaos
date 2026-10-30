"""Frozen pre-submission origin; observations do not grant authoring authority.

Origin is retained in the exact generation admission, not inferred later from
the selected project or directory names. Legacy admissions remain unqualified.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest
from .generation_plan import GenerationInputRejected
from .task_spec import seal, verify_identity


def from_run_binding(task, plan, binding):
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    verify_identity(plan, id_key="generation_plan_id", prefix="generation-plan:")
    verify_identity(binding, id_key="binding_id", prefix="interpretation-binding:")
    if (
        binding["task_spec_ref"] != task["task_spec_id"]
        or binding["source_digest"] != task["source"]["digest"]
        or plan["task_spec_ref"] != task["task_spec_id"]
        or plan["source_digest"] != task["source"]["digest"]
    ):
        raise GenerationInputRejected("context.execution_origin_conflict", "Task, plan and origin differ")
    # Reuse the Core resolver's already verified Change/primary-component pins.
    # No interpretation Run is created by projecting this binding.
    fields = (
        "object_type", "object_id", "project_ref", "change_id", "origin_generation",
        "manifest_digest", "source_digest", "source_message_ref", "task_spec_ref",
        "execution_group_ref",
    )
    return seal(
        {
            "schema": "adaos.builder.generation_origin.v1",
            **{key: copy.deepcopy(binding[key]) for key in fields},
            "generation_plan_ref": plan["generation_plan_id"],
        },
        id_key="origin_id", prefix="generation-origin:",
    )


def verify_origin(origin, plan, *, task=None):
    verify_identity(origin, id_key="origin_id", prefix="generation-origin:")
    verify_identity(plan, id_key="generation_plan_id", prefix="generation-plan:")
    expected = plan.get("parent_generation_plan_ref") or plan["generation_plan_id"]
    if (
        origin.get("schema") != "adaos.builder.generation_origin.v1"
        or origin.get("generation_plan_ref") != expected
        or origin.get("source_digest") != plan.get("source_digest")
        or origin.get("task_spec_ref") != plan.get("task_spec_ref")
        or not origin.get("change_id")
        or not origin.get("project_ref")
        or origin.get("execution_group_ref") != "task-execution:" + digest({
            "project": origin.get("project_ref"), "source": origin.get("source_digest")})[7:31]
        or origin.get("object_type") not in {"scenario", "skill"}
        or not isinstance(origin.get("object_id"), str)
        or not origin["object_id"]
        or type(origin.get("origin_generation")) is not int
        or origin["origin_generation"] < 0
    ):
        raise GenerationInputRejected("context.execution_origin_conflict", "The frozen generation origin differs from its plan")
    if task is not None:
        verify_identity(task, id_key="task_spec_id", prefix="task:")
        if (
            origin["task_spec_ref"] != task["task_spec_id"]
            or origin["source_digest"] != task["source"]["digest"]
            or origin["project_ref"] != task["scope"]["project_ref"]
            or origin["source_message_ref"] != task["source"]["provenance"].get("ref")
        ):
            raise GenerationInputRejected("context.execution_origin_conflict", "Origin does not reproduce the admitted task source")
    return copy.deepcopy(origin)


def retain_generation_admission(path, record, *, observer=None):
    """Write once; never backfill an origin onto an older paid admission."""
    target = Path(path)
    with mutation_lock(target.with_suffix(".lock"), timeout_s=30):
        if target.exists():
            retained = json.loads(target.read_text(encoding="utf-8"))
            if {key: value for key, value in retained.items() if key != "execution_origin"} != record:
                raise GenerationInputRejected("context.generation_admission_conflict", "An immutable generation admission cannot be replaced")
            origin = retained.get("execution_origin")
            if origin:
                verify_origin(origin, record["generation_plan"], task=record["task_spec"])
            return retained
        value = copy.deepcopy(record)
        binder = getattr(observer, "bind_generation", None)
        if callable(binder):
            value["execution_origin"] = binder(record["task_spec"], record["generation_plan"])
            verify_origin(value["execution_origin"], record["generation_plan"], task=record["task_spec"])
        atomic_write_json(target, value)
        return value


def load_generation_origin(journal_dir, plan):
    """Resolve only the explicitly supplied, digest-verified plan's admission."""
    if not journal_dir or not plan:
        return None
    verify_identity(plan, id_key="generation_plan_id", prefix="generation-plan:")
    name = (
        "repair-plan-" + plan["generation_plan_id"].partition(":")[2]
        if plan.get("context_stage") == "repair"
        else plan["generation_plan_id"].replace(":", "-")
    )
    path = Path(journal_dir) / (name + ".json")
    if not path.exists():
        return None  # Explicitly unqualified/legacy, not reconstructed authority.
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("generation_plan") != plan:
        raise GenerationInputRejected("context.execution_plan_conflict", "The exact retained execution plan changed")
    origin = record.get("execution_origin")
    return verify_origin(origin, plan, task=record.get("task_spec")) if origin else None


def verify_wire_origin(journal_dir, wire):
    """Recover origin from an exact retained wire, never today's selection."""
    origin = wire.get("execution_origin")
    if not origin:
        return None
    admission = wire.get("context_admission") or {}
    ref = admission.get("generation_plan_ref")
    if not isinstance(ref, str) or not re.fullmatch(r"generation-plan:[a-f0-9]{24}", ref):
        raise GenerationInputRejected("context.execution_origin_conflict", "Qualified origin has no exact plan reference")
    name = (
        "repair-plan-" + ref.partition(":")[2]
        if admission.get("parent_generation_plan_ref") else ref.replace(":", "-")
    )
    path = Path(journal_dir) / (name + ".json")
    if not path.is_file():
        raise GenerationInputRejected("context.execution_origin_missing", "Recover the exact pre-submission admission before accepting its origin")
    plan = json.loads(path.read_text(encoding="utf-8"))["generation_plan"]
    if plan.get("generation_plan_id") != ref or plan.get("parent_generation_plan_ref") != admission.get("parent_generation_plan_ref"):
        raise GenerationInputRejected("context.execution_origin_conflict", "Retained wire lineage differs from its exact plan")
    expected = load_generation_origin(journal_dir, plan)
    if expected != origin:
        raise GenerationInputRejected("context.execution_origin_conflict", "Retained wire origin differs from its pre-submission admission")
    return expected
