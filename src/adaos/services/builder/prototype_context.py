"""Compact stage-specific model context for Builder Prototype work."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .prototype_stage import PROTOTYPE_STAGE_CONTRACT
from adaos.services.builder_intent import process_constraint_kind


MODEL_CONTEXT_SCHEMA = "adaos.builder.prototype_model_context.v1"
SEMANTIC_COMPILER_VIEW_SCHEMA = "adaos.builder.semantic_compiler_view.v1"


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


def _requirement_inventory(brief: Mapping[str, Any]) -> list[dict[str, str]]:
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


def prototype_process_constraints(brief: Mapping[str, Any]) -> list[dict[str, str]]:
    """Retain non-widget obligations, including old content-addressed Briefs."""
    return [{**item, "verification_owner": kind}
            for item in _requirement_inventory(brief)
            if (kind := process_constraint_kind(item["statement"]))]


def prototype_requirement_inventory(brief: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return every accepted reference required by the canonical compiler."""
    excluded = {item["id"] for item in prototype_process_constraints(brief)}
    return [
        item for item in _requirement_inventory(brief) if item["id"] not in excluded
    ]


def prototype_model_requirement_inventory(
    brief: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return the minimal model-facing set of independent UI obligations."""
    inventory = prototype_requirement_inventory(brief)
    # Operation extraction intentionally retains a principal job with the same
    # evidence. Requiring both IDs made every action consume two model bindings
    # without adding proof. Prefer the typed operation; similarly prefer any
    # already-retained requirement over an identical residual clause.
    operation_statements = {
        " ".join(item["statement"].casefold().split())
        for item in inventory
        if item["kind"] == "operation"
    }
    seen_statements: set[str] = set()
    result: list[dict[str, str]] = []
    for item in inventory:
        statement_key = " ".join(item["statement"].casefold().split())
        if item["kind"] == "job" and statement_key in operation_statements:
            continue
        if item["kind"] == "residual" and statement_key in seen_statements:
            continue
        result.append(item)
        seen_statements.add(statement_key)
    return result


def prototype_requirement_aliases(
    brief: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Map compact model refs to equivalent canonical requirement refs.

    The aliases are derived only from byte-independent normalized statement
    equality. Core can therefore restore complete canonical coverage without a
    model call or a semantic guess.
    """

    canonical = prototype_requirement_inventory(brief)
    model = prototype_model_requirement_inventory(brief)
    model_ids = {item["id"] for item in model}
    model_by_statement: dict[str, str] = {}
    for item in model:
        key = " ".join(item["statement"].casefold().split())
        model_by_statement.setdefault(key, item["id"])
    aliases: dict[str, list[str]] = {}
    for item in canonical:
        if item["id"] in model_ids:
            continue
        key = " ".join(item["statement"].casefold().split())
        source_ref = model_by_statement.get(key)
        if source_ref:
            aliases.setdefault(source_ref, []).append(item["id"])
    return aliases


def expand_prototype_requirement_aliases(
    candidate: Mapping[str, Any], brief: Mapping[str, Any]
) -> dict[str, Any]:
    """Restore equivalent canonical bindings omitted from the compact view."""

    result = copy.deepcopy(dict(candidate))
    aliases = prototype_requirement_aliases(brief)
    if not aliases:
        return result
    covered = {
        str(item.get("requirement_ref") or "")
        for group in ("requirement_bindings", "capability_gaps")
        for item in result.get(group) or []
        if isinstance(item, Mapping)
    }
    for group in ("requirement_bindings", "capability_gaps"):
        values = result.get(group)
        if not isinstance(values, list):
            continue
        additions: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, Mapping):
                continue
            source_ref = str(item.get("requirement_ref") or "")
            for alias_ref in aliases.get(source_ref, []):
                if alias_ref in covered:
                    continue
                alias = copy.deepcopy(dict(item))
                alias["requirement_ref"] = alias_ref
                additions.append(alias)
                covered.add(alias_ref)
        values.extend(additions)
    return result


def compile_semantic_revision_model_context(
    document: Mapping[str, Any],
    brief: Mapping[str, Any],
    *,
    source_ref: str,
    revision: Any,
) -> dict[str, Any]:
    """Build a digest-addressed revision view without duplicate proof aliases."""

    canonical = copy.deepcopy(dict(document))
    source_bytes = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    source_digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    model_refs = [
        item["id"] for item in prototype_model_requirement_inventory(brief)
    ]
    aliases = prototype_requirement_aliases(brief)
    alias_to_source = {
        alias: source for source, values in aliases.items() for alias in values
    }

    for group in ("requirement_bindings", "capability_gaps"):
        compact_values: list[dict[str, Any]] = []
        values = canonical.get(group)
        if not isinstance(values, list):
            continue
        by_ref = {
            str(item.get("requirement_ref") or ""): item
            for item in values
            if isinstance(item, Mapping)
        }
        for requirement_ref in model_refs:
            item = by_ref.get(requirement_ref)
            if item is None:
                item = next(
                    (
                        by_ref[alias]
                        for alias, source in alias_to_source.items()
                        if source == requirement_ref and alias in by_ref
                    ),
                    None,
                )
            if item is None:
                continue
            value = copy.deepcopy(dict(item))
            value["requirement_ref"] = requirement_ref
            compact_values.append(value)
        canonical[group] = compact_values

    view_bytes = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    view_digest = "sha256:" + hashlib.sha256(view_bytes).hexdigest()
    return {
        "schema": SEMANTIC_COMPILER_VIEW_SCHEMA,
        "source_ref": source_ref,
        "digest": source_digest,
        "view_digest": view_digest,
        "registry_ref": f"{source_ref}#compiler-view@{view_digest}",
        "revision": revision,
        "document": canonical,
        "coverage": {
            "canonical_count": len(prototype_requirement_inventory(brief)),
            "model_count": len(model_refs),
            "omitted_alias_count": sum(len(values) for values in aliases.values()),
        },
        "policy": (
            "This digest-addressed compiler view omits only exact-statement proof "
            "aliases. Return one complete updated candidate using every Brief "
            "required_references id; Core restores canonical aliases before "
            "validation. Preserve identities, layout and unrelated behavior."
        ),
    }


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
    process_constraints = prototype_process_constraints(value)
    process_ids = {item["id"] for item in process_constraints}
    jobs = [item for item in jobs if item["id"] not in process_ids]
    state_requirements = [item for item in state_requirements
                          if item.get("id", item.get("job_ref")) not in process_ids]

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
        if isinstance(item, Mapping) and item.get("id") not in process_ids
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
    residual_requirements = [item for item in _statements(value.get("residual_requirements"))
                             if item["id"] not in process_ids]
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
        "required_references": prototype_model_requirement_inventory(value),
        "process_constraints": process_constraints,
        "process_constraint_policy": "These are retained authoring, privacy, preservation or stage obligations, not requests for widgets. Obey them; do not bind them to UI or invent capability gaps. They are not automatically passed: independent source/process review owns verification. automation_scope must remain in the later Automation task, not become executable Prototype logic.",
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


def prototype_output_locales(instruction: str, *, locale: str, existing: list[str] = (),
                            brief: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Choose authoring languages without making a translation request implicit."""
    admitted = _known_value((brief or {}).get("constraints", {}).get("locale"))
    language = str(admitted or locale).lower().replace("_", "-").split("-")[0]
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
    "SEMANTIC_COMPILER_VIEW_SCHEMA",
    "compile_prototype_model_context",
    "prototype_state_requirements",
    "prototype_requirement_inventory",
    "prototype_model_requirement_inventory",
    "prototype_requirement_aliases",
    "expand_prototype_requirement_aliases",
    "compile_semantic_revision_model_context",
    "prototype_process_constraints",
    "prototype_output_locales",
]
