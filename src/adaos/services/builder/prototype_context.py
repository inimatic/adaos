"""Compact stage-specific model context for Builder Prototype work."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .prototype_stage import PROTOTYPE_STAGE_CONTRACT


MODEL_CONTEXT_SCHEMA = "adaos.builder.prototype_model_context.v1"


def _known_value(value: Any) -> Any | None:
    if not isinstance(value, Mapping) or value.get("state") != "known":
        return None
    return copy.deepcopy(value.get("value"))


def _statements(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {
            "id": str(item.get("id") or ""),
            "statement": str(item.get("statement") or ""),
        }
        for item in value
        if isinstance(item, Mapping)
        and str(item.get("id") or "").strip()
        and str(item.get("statement") or "").strip()
    ]


def prototype_state_requirements(brief: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return stable model-facing refs for accepted representative states."""

    jobs = _statements(brief.get("principal_jobs"))
    jobs_by_statement = {item["statement"]: item["id"] for item in jobs}
    raw_states = _known_value(brief.get("representative_states"))
    if not isinstance(raw_states, list):
        raw_states = [] if raw_states is None else [raw_states]

    requirements: list[dict[str, str]] = []
    for raw in raw_states:
        statement = str(raw or "").strip()
        if not statement:
            continue
        job_ref = jobs_by_statement.get(statement)
        if job_ref:
            requirements.append({"job_ref": job_ref})
            continue
        requirements.append(
            {
                "id": f"representative_state:{len(requirements) + 1:02d}",
                "statement": statement,
            }
        )
    return requirements


def prototype_requirement_inventory(brief: Mapping[str, Any]) -> list[dict[str, str]]:
    """The same exact requirement inventory is used by the model and compiler."""
    inventory = [
        {**item, "kind": kind}
        for group, kind in (
            ("principal_jobs", "job"), ("residual_requirements", "residual"),
            ("operations", "operation"), ("information_requirements", "information"),
            ("collection_requirements", "collection"),
        )
        for item in _statements(brief.get(group))
    ]
    inventory.extend(
        {**item, "kind": "representative_state"}
        for item in prototype_state_requirements(brief) if item.get("id")
    )
    return inventory


def compile_prototype_model_context(brief: Mapping[str, Any], *, compact: bool = False) -> dict[str, Any]:
    """Remove persistence metadata and raw-statement duplication from a brief.

    The full content-addressed brief remains the source of truth. This slice is
    only the bounded generation-stage view supplied to a design model.
    """

    value = copy.deepcopy(dict(brief))
    if value.get("schema") != "adaos.builder.prototype_brief.v1":
        raise ValueError("Builder Prototype model context requires a Prototype Brief")

    jobs = _statements(value.get("principal_jobs"))
    state_requirements = prototype_state_requirements(value)

    facts = {
        name: fact
        for name in ("outcome", "actors", "entities")
        if (fact := _known_value(value.get(name))) is not None
    }
    boundaries = {
        str(name): fact
        for name, descriptor in dict(value.get("boundaries") or {}).items()
        if (fact := _known_value(descriptor)) is not None
    }
    constraints = {
        str(name): fact
        for name, descriptor in dict(value.get("constraints") or {}).items()
        if (fact := _known_value(descriptor)) is not None
    }
    operations = [
        {
            key: copy.deepcopy(item.get(key))
            for key in ("id", "kind", "statement", "source_clause", "target", "effect_scope", "authority")
        }
        for item in value.get("operations") or []
        if isinstance(item, Mapping)
    ]
    for operation in operations:
        operation["related_job_refs"] = [
            item["id"] for item in jobs if item["statement"] == operation["statement"]
        ]
    information_requirements = [
        {
            key: copy.deepcopy(item.get(key))
            for key in ("id", "kind", "interaction", "statement")
        }
        for item in value.get("information_requirements") or []
        if isinstance(item, Mapping)
    ]
    collection_requirements = [
        {
            key: copy.deepcopy(item.get(key))
            for key in ("id", "kind", "interaction", "statement")
        }
        for item in value.get("collection_requirements") or []
        if isinstance(item, Mapping)
    ]
    residual_requirements = _statements(value.get("residual_requirements"))
    interpretation = (
        value.get("interpretation")
        if isinstance(value.get("interpretation"), Mapping)
        else {}
    )
    context = {
        "schema": MODEL_CONTEXT_SCHEMA,
        "stage_contract": copy.deepcopy(PROTOTYPE_STAGE_CONTRACT),
        "brief_ref": str(value.get("brief_id") or ""),
        "brief_digest": str(value.get("digest") or ""),
        "required_references": prototype_requirement_inventory(value),
        "coverage_policy": "Every required_references id needs a binding or explicit gap. Related jobs and operations may share semantic refs; neither binding replaces the other.",
        "knowledge_policy": "facts are admitted knowledge; unknowns are not omissions in the request. Infer a suitable entity/view design from the original user request, but do not present design choices as user-confirmed facts or external authority.",
        "primary_jobs": jobs,
        "residual_requirements": residual_requirements,
        "operations": operations,
        "information_requirements": information_requirements,
        "collection_requirements": collection_requirements,
        "state_requirements": state_requirements,
        "facts": facts,
        "boundaries": boundaries,
        "constraints": constraints,
        "assumptions": _statements(value.get("assumptions")),
        "open_questions": _statements(value.get("open_questions")),
        "capability_gaps": _statements(value.get("capability_gaps")),
        "unknowns": sorted(
            {
                str(item)
                for item in interpretation.get("unresolved_fields") or []
                if str(item).strip()
            }
        ),
    }
    if not compact:
        return context
    # Statements appear once; typed annotations reference the same exact IDs.
    context["schema"] = "adaos.builder.prototype_model_context.v2"
    context["exclusions"] = _statements(value.get("exclusions"))
    context["exclusion_policy"] = "Explicit exclusions are not implementation requirements or Automation obligations. Do not add UI, gaps or deferred work for them."
    context.pop("stage_contract")
    context.pop("primary_jobs")
    context.pop("residual_requirements")
    for group in ("operations", "information_requirements", "collection_requirements"):
        for item in context[group]:
            item.pop("statement", None)
            item.pop("source_clause", None)
            if isinstance(item.get("target"), Mapping) and item["target"].get("state") == "unknown":
                item.pop("target")
            if item.get("authority") == "unknown":
                item.pop("authority")
            for key in list(item):
                if item[key] is None:
                    item.pop(key)
    context["coverage_policy"] = "Bind every required_references id or report an explicit gap. Shared semantic refs are valid; emit each requirement id once."
    return context


def prototype_output_locales(instruction: str, *, locale: str, existing: list[str] = ()) -> tuple[str, ...]:
    """Choose authoring languages without making a translation request implicit."""
    language = locale.lower().replace("_", "-").split("-")[0]
    if language not in {"en", "ru"}:
        language = "en"
    text = instruction.casefold()
    bilingual = bool(re.search(r"\b(?:en|english|английск\w*)\b", text)
                     and re.search(r"\b(?:ru|russian|русск\w*)\b", text))
    requested = {language, *(item for item in existing if item in {"en", "ru"})}
    if bilingual:
        requested.update(("en", "ru"))
    return tuple(item for item in ("en", "ru") if item in requested)


__all__ = [
    "MODEL_CONTEXT_SCHEMA",
    "compile_prototype_model_context",
    "prototype_state_requirements",
    "prototype_requirement_inventory",
    "prototype_output_locales",
]
