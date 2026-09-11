"""Compact stage-specific model context for Builder Prototype work."""

from __future__ import annotations

import copy
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


def compile_prototype_model_context(brief: Mapping[str, Any]) -> dict[str, Any]:
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
            for key in ("id", "kind", "statement", "effect_scope", "authority")
        }
        for item in value.get("operations") or []
        if isinstance(item, Mapping)
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
    return {
        "schema": MODEL_CONTEXT_SCHEMA,
        "stage_contract": copy.deepcopy(PROTOTYPE_STAGE_CONTRACT),
        "brief_ref": str(value.get("brief_id") or ""),
        "brief_digest": str(value.get("digest") or ""),
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


__all__ = [
    "MODEL_CONTEXT_SCHEMA",
    "compile_prototype_model_context",
    "prototype_state_requirements",
]
