"""Stage-scoped specification deltas inside the canonical Builder workflow."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

STAGES = ("prototype", "automation")


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value: Any, name: str, limit: int, *, optional: bool = False) -> str:
    if not isinstance(value, str) or (not optional and not value.strip()) or len(value) > limit:
        raise ValueError(f"{name} must be {'optional' if optional else 'nonempty'} text of at most {limit} characters")
    return value


def empty_specification() -> dict[str, Any]:
    return {"schema": "adaos.builder.application_specification.v1",
            **{stage: {"generation": 0, "requirements": {}, "digest": digest({})} for stage in STAGES}}


def normalize_specification(value: Any) -> dict[str, Any]:
    if value is None:
        return empty_specification()
    if not isinstance(value, Mapping) or value.get("schema") != "adaos.builder.application_specification.v1":
        raise ValueError("Invalid application specification schema")
    result = copy.deepcopy(dict(value))
    for stage in STAGES:
        layer = result.get(stage)
        if not isinstance(layer, Mapping) or not isinstance(layer.get("requirements"), Mapping):
            raise ValueError(f"Missing {stage} specification layer")
        if len(layer["requirements"]) > 500:
            raise ValueError("A specification supports at most 500 requirements per stage")
        if layer.get("digest") != digest(layer["requirements"]):
            raise ValueError(f"{stage} specification digest mismatch")
        if not isinstance(layer.get("generation"), int) or layer["generation"] < 0:
            raise ValueError("Invalid specification generation")
    return result


def prepare_delta(value: Mapping[str, Any], *, change: Mapping[str, Any], specification: Mapping[str, Any]) -> dict[str, Any]:
    """Admit explicit requirement edits, not an inferred checklist from prose."""
    if not isinstance(value, Mapping):
        raise ValueError("Specification delta must be an object")
    spec = normalize_specification(specification)
    operations = value.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise ValueError("A specification delta needs 1..100 operations")
    issues = {str(row.get("issue_id")) for row in change.get("issues", []) if isinstance(row, Mapping)}
    known_sources = set(change.get("source_message_ids") or [])
    for issue in change.get("issues", []):
        if isinstance(issue, Mapping):
            known_sources.update(issue.get("source_message_ids") or [])
    seen = set()
    admitted = []
    for row in operations:
        if not isinstance(row, Mapping):
            raise ValueError("Specification operations must be objects")
        stage, op = row.get("stage"), row.get("operation")
        if stage not in STAGES or op not in {"add", "modify", "remove"}:
            raise ValueError("Invalid specification stage/operation")
        identifier = _text(row.get("requirement_id"), "requirement_id", 160)
        if (stage, identifier) in seen:
            raise ValueError("Duplicate requirement operation")
        seen.add((stage, identifier))
        refs = row.get("issue_ids")
        sources = row.get("source_message_ids", [])
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref in issues for ref in refs):
            raise ValueError("Requirement operations must reference Issues in their Change")
        if not isinstance(sources, list) or not all(isinstance(ref, str) and ref in known_sources for ref in sources):
            raise ValueError("Unknown requirement source message")
        requirements = spec[stage]["requirements"]
        if (op == "add") == (identifier in requirements):
            raise ValueError(f"{op} conflicts with the current requirement: {identifier}")
        entry = {"stage": stage, "operation": op, "requirement_id": identifier,
                 "issue_ids": list(dict.fromkeys(refs)), "source_message_ids": list(dict.fromkeys(sources))}
        if op != "remove":
            entry["text"] = _text(row.get("text"), "requirement text", 8000)
            criteria = row.get("acceptance_criteria")
            if not isinstance(criteria, list) or not 1 <= len(criteria) <= 30:
                raise ValueError("A requirement needs 1..30 acceptance criteria")
            entry["acceptance_criteria"] = [_text(c, "criterion", 2000) for c in criteria]
        else:
            entry["reason"] = _text(row.get("reason"), "removal reason", 2000)
        admitted.append(entry)
    result = {"schema": "adaos.builder.specification_delta.v1",
              "change_id": change.get("change_id") or change.get("change_set_id"),
              "base_digests": {stage: spec[stage]["digest"] for stage in STAGES},
              "operations": admitted,
              "design": _text(value.get("design", ""), "technical design", 16000, optional=True)}
    if not result["change_id"]:
        raise ValueError("Specification delta requires a Change")
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 131072:
        raise ValueError("Specification delta exceeds 128 KiB")
    result["digest"] = digest(result)
    result["applied"] = {}
    return result


def apply_delta(specification: Mapping[str, Any], delta: Mapping[str, Any], *, stage: str,
                evidence_ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge only the accepted stage; caller owns the workflow decision transaction."""
    if stage not in STAGES or not evidence_ref:
        raise ValueError("Specification merge requires a stage and acceptance evidence")
    spec = normalize_specification(specification)
    result = copy.deepcopy(dict(delta))
    if result.get("digest") != digest({k: v for k, v in result.items() if k not in {"digest", "applied"}}):
        raise ValueError("Specification delta digest mismatch")
    if stage in result.get("applied", {}):
        return spec, result
    operations = [op for op in result["operations"] if op["stage"] == stage]
    if not operations:
        return spec, result
    layer = spec[stage]
    if result["base_digests"][stage] != layer["digest"]:
        raise ValueError("Specification base changed; rebase and review the delta")
    for op in operations:
        identifier = op["requirement_id"]
        if op["operation"] == "remove":
            del layer["requirements"][identifier]
        else:
            layer["requirements"][identifier] = {
                "requirement_id": identifier, "text": op["text"],
                "acceptance_criteria": op["acceptance_criteria"], "issue_ids": op["issue_ids"],
                "source_message_ids": op["source_message_ids"], "change_id": result["change_id"],
                "evidence_ref": evidence_ref}
    layer["generation"] += 1
    layer["digest"] = digest(layer["requirements"])
    result.setdefault("applied", {})[stage] = {
        "generation": layer["generation"], "digest": layer["digest"], "evidence_ref": evidence_ref}
    return normalize_specification(spec), result
