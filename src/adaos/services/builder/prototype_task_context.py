"""Task-grounded delivery of canonical Prototype guidance, not learned advice.

The router reads only the task, typed Brief and existing application. It never
routes by its own instructional prose and never changes facts or patch authority.
Exact fragments have one owner: semantic_generation_guidance. Unknown authority
and unavailable retrieval cause a byte-preserving full fallback in the caller.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.builder_context import canonical, digest

ORIENTATION_KERNEL = frozenset({"contract", "stage", "limits", "authoring_constraints", "records_per_resource"})
COMMON_FRAGMENTS = frozenset({"modeling", "coverage", "fixture_values", "view_roles", "layout_regions"})

# Query/state reachability is a platform constraint, not an optional UX hint.
# A task may ask for "no results" without using the platform's state vocabulary.
FRAGMENT_DEPENDENCIES = {"query_filters": frozenset({"state_rules", "state_proofs"})}

# Platform distinctions and lookup handles; no generic programming checklist.
PROTOTYPE_ACCENTS = (
    ("authoring", "Semantic authoring → Core-compiled UI/providers/i18n. IDs ≠ displayed labels ≠ ownership.",
     ("modeling", "fixture_values", "coverage")),
    ("interaction", "Selection ≠ details ≠ editor opening. Linked collections need relationship/selection semantics.",
     ("view_roles", "relationships", "selection_links", "command_ownership")),
    ("query_and_state", "Permanent collection scope ≠ user query ≠ representative-state evidence.",
     ("query_filters", "ux.query_scope", "state_rules", "state_proofs")),
    ("local_effects", "Local Prototype CRUD/validation is executable; labels/fixtures do not implement external Automation.",
     ("interactions", "field_validation", "command_guards", "deferred_computations")),
    ("surfaces", "Sections partition visibility; regions/editor surfaces determine wide/narrow reachability.",
     ("layout_regions", "ux.sections", "ux.layout", "ux.editor_surface")),
)

ROUTES = (
    ("query", r"search|filter|query|поиск|фильтр|дефицит|ниже|только|subset",
     ("query_filters", "ux.query_toolbar", "ux.query_scope")),
    ("relationships", r"relat|linked|foreign|supplier|customer|связан|связи|поставщик|клиент|справочник|состав|истори|комментар",
     ("relationships", "selection_links", "interactions", "ux.progressive_disclosure")),
    ("editing", r"edit|update|delete|transition|создавать|редактир|изменять|удалять|переводить|добавлять|добавить|сохран|команд",
     ("command_ownership", "field_validation", "command_guards", "form_guidance", "interactions", "ux.editor_inputs", "ux.editor_surface")),
    ("states", r"empty|state|proof|пуст|состояни|без результатов|ожидани",
     ("state_rules", "state_proofs")),
    ("computation", r"calculat|computed|forecast|deficit|вычисл|расч[её]т|дефицит|сравнен|прогноз",
     ("deferred_computations", "command_guards")),
    ("responsive", r"mobile|responsive|narrow|wide|workspace|панел|рядом|телефон|широк|адаптив|мобиль|колон",
     ("ux.layout", "ux.collection_presentations", "ux.editor_surface", "ux.text")),
    ("sections", r"tabs|section|вклад|раздел|экран|partition",
     ("ux.sections",)),
    ("media", r"image|video|audio|attach|upload|изображ|видео|аудио|вложени|загруз.*файл",
     ("media",)),
)


def task_evidence(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Extract admitted task carriers; do not scan system guidance or tool replies."""
    requests: list[dict[str, Any]] = []
    findings: list[Any] = []

    def visit(body: Any) -> None:
        if not isinstance(body, dict):
            return
        if isinstance(body.get("original_request"), dict):
            visit(body["original_request"])
        request = body.get("builder_request")
        if isinstance(request, dict) and isinstance(request.get("instruction"), str):
            requests.append({key: copy.deepcopy(request[key]) for key in
                ("instruction", "prototype_brief", "current_semantic") if key in request})
        repair = body.get("semantic_repair")
        if isinstance(repair, dict):
            if isinstance(repair.get("instruction"), str):
                requests.append({key: copy.deepcopy(repair[key]) for key in
                    ("instruction", "prototype_brief") if key in repair})
            findings.extend(copy.deepcopy(repair.get("validation_findings") or []))
    for message in messages:
        if message.get("role") not in {"user", None} or not isinstance(message.get("content"), str):
            continue
        try:
            visit(json.loads(message["content"]))
        except ValueError:
            pass
    return {"requests": requests, "findings": findings} if requests else None


def select_task_fragments(evidence: Mapping[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    def semantic_strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            if isinstance(value.get("state"), str) and value["state"] in {"known", "unknown", "missing"}:
                yield from semantic_strings(value.get("value"))
            else:
                for key, content in value.items():
                    if key not in {"schema", "id", "state", "required_references", "source_refs"}:
                        yield from semantic_strings(content)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from semantic_strings(item)
    text = "\n".join(semantic_strings(evidence)).casefold()
    selected = set(COMMON_FRAGMENTS)
    reasons: list[dict[str, Any]] = [{"dimension": "semantic_authoring", "fragment_ids": sorted(COMMON_FRAGMENTS),
                                    "signal": "all_semantic_prototypes"}]
    for dimension, pattern, fragments in ROUTES:
        typed_mutation = dimension == "editing" and any(operation.get("kind") in {"create", "update", "delete", "transition"}
            for request in evidence.get("requests", []) for operation in (request.get("prototype_brief") or {}).get("operations", [])
            if isinstance(operation, dict))
        if re.search(pattern, text) or typed_mutation:
            selected.update(fragments)
            reasons.append({"dimension": dimension, "fragment_ids": list(fragments), "signal": "task_or_typed_brief"})
    if any(request.get("current_semantic") is not None for request in evidence.get("requests", [])):
        selected.add("revision_scope")
        reasons.append({"dimension": "revision", "fragment_ids": ["revision_scope"], "signal": "current_semantic"})
    visited = set()
    while pending := sorted(selected - visited):
        for identity in pending:
            visited.add(identity)
            required = FRAGMENT_DEPENDENCIES.get(identity, frozenset())
            if required:
                selected.update(required)
                reasons.append({"dimension": "fragment_dependency", "fragment_ids": sorted(required),
                    "signal": "required_by:" + identity})
    return sorted(selected), reasons


def project_task_guidance(invariants: dict[str, Any], messages, *, strategy: str, oracle_fragment_ids=None):
    from adaos.services.builder.prototype_mcp import library_snapshot, retrieval_index
    evidence = task_evidence(messages)
    if evidence is None:
        raise ValueError("task_evidence_absent")
    snapshot = library_snapshot()
    known = {fragment["id"] for fragment in snapshot["fragments"]}
    selected: list[str] = []
    reasons: list[dict[str, Any]] = []
    if strategy == "task-aware":
        selected, reasons = select_task_fragments(evidence)
    elif strategy == "oracle":
        if (not isinstance(oracle_fragment_ids, (list, tuple)) or not oracle_fragment_ids
                or not all(isinstance(identity, str) for identity in oracle_fragment_ids)):
            raise ValueError("oracle_requires_explicit_fragment_ids")
        selected = sorted(set(oracle_fragment_ids))
        reasons = [{"dimension": "diagnostic_oracle", "fragment_ids": selected, "signal": "preregistered_before_generation"}]
    elif strategy != "core-indexed":
        raise ValueError("unknown_task_context_strategy")
    if not set(selected) <= known:
        raise ValueError("unknown_selected_fragment")
    projected = {key: copy.deepcopy(value) for key, value in invariants.items() if key in ORIENTATION_KERNEL}
    for identity in selected:
        if identity.startswith("ux."):
            child = identity[3:]
            projected.setdefault("ux_recommendations", {})[child] = copy.deepcopy(invariants["ux_recommendations"][child])
        else:
            projected[identity] = copy.deepcopy(invariants[identity])
    inline = (set(projected) - {"contract", "ux_recommendations"}) | {"ux." + key for key in projected.get("ux_recommendations", {})}
    index = retrieval_index(available=True)
    index["dimensions"] = [part for part in index["dimensions"] if part["id"] not in inline]
    # Inline ids/digests belong to the audit receipt, not the model's input.
    index["policy"] += (" Before authoring a platform feature not specified inline, read its indexed rules. "
        "Known ids can be read directly in batches of 1–8. Do not search for an id already listed. "
        "Do not reread inline fragments unless resolving an actual conflict.")
    accents = {"schema": "adaos.builder.prototype_platform_accents.v1", "authority": snapshot["authority"],
        "library_digest": snapshot["library_digest"],
        "dimensions": [{"id": identity, "accent": accent, "lookup": [part for part in lookup if part not in inline]}
            for identity, accent, lookup in PROTOTYPE_ACCENTS]}
    receipt = {"task_evidence_sha256": digest(evidence), "selected_fragment_ids": selected,
        "inline_fragment_ids": sorted(inline), "retrievable_fragment_ids": [part["id"] for part in index["dimensions"]],
        "selection_reasons": reasons, "library_digest": snapshot["library_digest"],
        "fragment_digests": {part["id"]: part["content_digest"] for part in snapshot["fragments"] if part["id"] in inline}}
    return projected, index, accents, receipt
