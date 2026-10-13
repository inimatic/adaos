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

# Omitted provider assertions MUST stay inline: strict provider schemas do not
# carry all platform bounds. Compact delivery is whole-fragment selection, not
# truncation or a second paraphrased contract. Duplicated limits are retrievable.
COMPACT_KERNEL = frozenset({"contract", "stage", "authoring_constraints"})
COMPACT_COMMON = frozenset({"modeling", "coverage", "fixture_values", "view_roles"})
COMPACT_INLINE_CHAR_BUDGET = 10_500
COMPACT_PRIORITY = (
    "revision_scope", "query_filters", "relationships", "selection_links",
    "command_ownership", "field_validation", "interactions", "command_guards",
    "deferred_computations", "form_guidance", "state_rules", "state_proofs",
    "layout_regions",
)
COMPACT_LOOKUP_GROUPS = (
    ("authoring", "Before exceeding counts/bounds or changing an existing application.", "schema limits revision constraints контракт",
     ("authoring_constraints", "limits", "records_per_resource", "revision_scope", "modeling", "coverage", "fixture_values")),
    ("linked_data", "Before linking parent/child collections, foreign keys or selection-driven details.", "relationships selection foreign key связи история",
     ("relationships", "selection_links", "view_roles", "ux.progressive_disclosure")),
    ("query_state", "Before authoring search, resettable filters, permanent subsets or reachable state evidence.", "query search filters states proof поиск фильтры",
     ("query_filters", "state_rules", "state_proofs", "ux.query_scope", "ux.query_toolbar")),
    ("local_effects", "Before authoring CRUD, writable fields, validation, guards or simulated computations.", "commands editor validation CRUD computation команды форма",
     ("command_ownership", "field_validation", "command_guards", "interactions", "deferred_computations", "form_guidance", "ux.editor_inputs")),
    ("surfaces", "Before defining regions, tabs, collection presentations or wide/narrow editor surfaces.", "layout sections modal responsive mobile панели вкладки",
     ("layout_regions", "ux.sections", "ux.layout", "ux.editor_surface", "ux.collection_presentations", "ux.text")),
    ("media", "Before rendering or uploading images, audio, video or attachments.", "media attachments upload изображения вложения",
     ("media",)),
    ("stage", "Before deferring behavior to Automation or claiming external effects are enforced.", "prototype automation local effects прототип автоматизация",
     ("stage",)),
)


def _project_fragments(invariants, kernel, selected):
    projected = {key: copy.deepcopy(value) for key, value in invariants.items() if key in kernel}
    for identity in selected:
        if identity.startswith("ux."):
            projected.setdefault("ux_recommendations", {})[identity[3:]] = copy.deepcopy(invariants["ux_recommendations"][identity[3:]])
        else:
            projected[identity] = copy.deepcopy(invariants[identity])
    return projected


def select_compact_fragments(invariants, requested):
    selected = set(COMPACT_COMMON)
    if len(canonical(_project_fragments(invariants, COMPACT_KERNEL, selected))) > COMPACT_INLINE_CHAR_BUDGET:
        raise ValueError("compact_mandatory_kernel_exceeds_budget")
    priority = list(COMPACT_PRIORITY) + sorted(set(requested) - set(COMPACT_PRIORITY))
    deferred = []
    for identity in priority:
        if identity not in requested or identity in selected:
            continue
        closure = {identity} | set(FRAGMENT_DEPENDENCIES.get(identity, ()))
        proposed = selected | closure
        if len(canonical(_project_fragments(invariants, COMPACT_KERNEL, proposed))) <= COMPACT_INLINE_CHAR_BUDGET:
            selected = proposed
        else:
            deferred.append(identity)
    return sorted(selected), deferred

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
    if strategy in {"task-aware", "task-aware-compact"}:
        selected, reasons = select_task_fragments(evidence)
        if strategy == "task-aware-compact":
            selected, deferred = select_compact_fragments(invariants, selected)
            reasons.append({"dimension": "bounded_whole_fragment_delivery", "fragment_ids": selected,
                "signal": "inline_char_budget:" + str(COMPACT_INLINE_CHAR_BUDGET), "deferred_task_fragment_ids": deferred})
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
    projected = _project_fragments(invariants, COMPACT_KERNEL if strategy == "task-aware-compact" else ORIENTATION_KERNEL, selected)
    inline = (set(projected) - {"contract", "ux_recommendations"}) | {"ux." + key for key in projected.get("ux_recommendations", {})}
    index = retrieval_index(available=True)
    index["dimensions"] = [part for part in index["dimensions"] if part["id"] not in inline]
    # Inline ids/digests belong to the audit receipt, not the model's input.
    accents = {"schema": "adaos.builder.prototype_platform_accents.v1", "authority": snapshot["authority"],
        "library_digest": snapshot["library_digest"],
        "dimensions": [{"id": identity, "accent": accent, "lookup": [part for part in lookup if part not in inline]}
            for identity, accent, lookup in PROTOTYPE_ACCENTS]}
    retrievable = [part["id"] for part in index["dimensions"]]
    if strategy == "task-aware-compact":
        # One decision-oriented index, not repeated per-fragment prose plus a
        # duplicate accent directory. Every omitted canonical id stays findable.
        index["schema"] = "adaos.builder.prototype_retrieval_index.v2"
        index["dimensions"] = [{"ids": [identity for identity in identities if identity not in inline],
            "read_when": when, "keywords": keywords} for _, when, keywords, identities in COMPACT_LOOKUP_GROUPS
            if any(identity not in inline for identity in identities)]
        indexed = [identity for part in index["dimensions"] for identity in part["ids"]]
        if set(indexed) != set(retrievable) or len(indexed) != len(set(indexed)):
            raise ValueError("compact_index_does_not_cover_library")
        accents = {"authoring": "Semantic candidate -> Core-compiled UI; ids, labels and ownership differ.",
            "interaction": "Selection, details and editor opening are separate; local CRUD is executable now.",
            "query_state": "Permanent scope, resettable query and state evidence are different.",
            "surfaces": "Sections partition visibility; regions and editor surfaces govern reachability."}
    receipt = {"task_evidence_sha256": digest(evidence), "selected_fragment_ids": selected,
        "inline_fragment_ids": sorted(inline), "retrievable_fragment_ids": retrievable,
        "selection_reasons": reasons, "library_digest": snapshot["library_digest"],
        "fragment_digests": {part["id"]: part["content_digest"] for part in snapshot["fragments"] if part["id"] in inline}}
    if strategy == "task-aware-compact":
        receipt.update(inline_char_budget=COMPACT_INLINE_CHAR_BUDGET, inline_chars=len(canonical(projected)),
            compact_delivery_chars=len(canonical(projected)) + len(canonical(index)) + len(canonical(accents)))
    return projected, index, accents, receipt
