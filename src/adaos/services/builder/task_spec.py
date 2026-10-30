"""Source-bound task state shared by discovery, generation and acceptance.

Extraction is a proposal. This module validates identities and transitions; it
does not turn an extractor's confidence into evidence of semantic correctness.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.builder_context import digest
from adaos.services.builder_intent import _validate

SCHEMA = "adaos.builder.task_spec.v1"
STATES = frozenset({"active", "amended", "superseded", "deferred", "rejected", "conflicting"})
FAMILIES = frozenset({"value", "transition", "preservation", "query", "layout", "locale", "policy", "runtime"})


def verify_identity(value: Mapping[str, Any], *, id_key: str, prefix: str) -> None:
    unsigned = {key: item for key, item in value.items() if key not in {id_key, "digest"}}
    expected = digest(unsigned)
    if value.get("digest") != expected or value.get(id_key) != prefix + expected[7:31]:
        raise ValueError(f"{id_key} content identity does not match its payload")


def seal(value: Mapping[str, Any], *, id_key: str = "task_spec_id", prefix: str = "task:") -> dict:
    unsigned = copy.deepcopy({key: item for key, item in value.items() if key not in {id_key, "digest"}})
    hashed = digest(unsigned)
    return {**unsigned, id_key: prefix + hashed[7:31], "digest": hashed}


def source_clauses(statement: str) -> list[dict]:
    """Preserve every source character, including punctuation and list markers.

    Line segmentation is deliberately syntactic. A later interpreter classifies
    clauses; punctuation in URLs, numbers or code cannot erase source evidence.
    """
    clauses, offset = [], 0
    for line in statement.splitlines(keepends=True):
        clauses.append({"id": f"clause:{len(clauses) + 1:03d}", "start": offset,
                        "end": offset + len(line), "text": line, "classification": "unextracted"})
        offset += len(line)
    if offset != len(statement) or "".join(row["text"] for row in clauses) != statement:
        raise ValueError("Source clause projection lost input")
    return clauses


def compile_task_spec(intent: Mapping[str, Any], brief: Mapping[str, Any], *,
                      previous: Mapping[str, Any] | None = None,
                      amendments: Sequence[Mapping[str, Any]] = ()) -> dict:
    from .prototype_context import prototype_model_requirement_inventory, prototype_process_constraints

    _validate("builder.intent.v1.schema.json", intent)
    _validate("builder.prototype_brief.v1.schema.json", brief)
    verify_identity(intent, id_key="intent_id", prefix="intent:")
    verify_identity(brief, id_key="brief_id", prefix="brief:")
    if brief["intent_digest"] != intent["digest"] or brief["intent_ref"] != intent["intent_id"]:
        raise ValueError("TaskSpec Intent and Brief disagree")
    ledger = copy.deepcopy(list((previous or {}).get("requirements") or []))
    if previous:
        verify_identity(previous, id_key="task_spec_id", prefix="task:")
        if previous.get("scope") != intent["scope"]:
            raise ValueError("TaskSpec history belongs to a different Project/Change")
    existing = {item["id"]: item for item in ledger}
    for item in prototype_model_requirement_inventory(brief):
        # Brief refs scope local extractor IDs; equality of IDs in different turns
        # is never evidence that one requirement amended another.
        identity = "requirement:" + digest({"brief": brief["digest"], "ref": item["id"]})[7:31]
        if identity not in existing:
            row = {"id": identity, "source_ref": brief["brief_id"] + "/" + item["id"],
                   "source_digest": intent["digest"], "statement": item["statement"],
                   "kind": item["kind"], "status": "active", "supersedes": []}
            ledger.append(row)
            existing[identity] = row
    touched = set()
    for change in amendments:
        identity, state = change.get("requirement_id"), change.get("status")
        if identity not in existing or state not in STATES or identity in touched:
            raise ValueError("Invalid or duplicate requirement lifecycle transition")
        if not change.get("source_ref"):
            raise ValueError("A requirement transition needs explicit source evidence")
        replacement = change.get("replacement_id")
        if state == "superseded" and (replacement not in existing or replacement == identity):
            raise ValueError("Supersession needs a distinct existing replacement")
        if replacement and existing[replacement]["status"] != "active":
            raise ValueError("A replacement must be active")
        existing[identity].update(status=state, transition_source=change["source_ref"])
        if replacement:
            existing[replacement]["supersedes"].append(identity)
        touched.add(identity)
    source = {"intent_ref": intent["intent_id"], "digest": intent["digest"],
              "statement": intent["statement"], "provenance": copy.deepcopy(intent["source"])}
    return seal({"schema": SCHEMA, "source": source, "scope": copy.deepcopy(intent["scope"]),
                 "brief_ref": brief["brief_id"], "brief_digest": brief["digest"],
                 "previous_ref": (previous or {}).get("task_spec_id"),
                 "clauses": source_clauses(intent["statement"]), "requirements": ledger,
                 "process_constraints": prototype_process_constraints(brief),
                 "authority": copy.deepcopy(intent["authority"]),
                 "knowledge": {key: {"state": "known", "value": copy.deepcopy(value["value"])}
                               if value.get("state") == "known" else {"state": "unextracted"}
                               for key in ("outcome", "actors", "entities")
                               for value in (brief[key],)},
                 "acceptance": [], "questions": [], "design_freedom": [],
                 "interpretation_status": "proposed"})


def active_requirements(task: Mapping[str, Any]) -> list[dict]:
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    if any(row["status"] == "conflicting" for row in task["requirements"]):
        raise ValueError("Conflicting requirements need TaskSpec admission")
    return copy.deepcopy([row for row in task["requirements"] if row["status"] in {"active", "amended"}])
