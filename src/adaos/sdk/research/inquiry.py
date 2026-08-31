"""Typed, revisioned scientific inquiry projection for Research Workbench."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .synthesis import (
    ResearchSynthesisError,
    _as_mapping,
    _require_matching_digest,
    _validate_schema,
    digest_payload,
    stamp_digest,
)


InquiryLlmCall = Callable[..., Mapping[str, Any]]

_RECORD_COLLECTIONS = {
    "concept": "concepts",
    "problem_frame": "problem_frames",
    "knowledge_claim": "knowledge_claims",
    "research_question": "research_questions",
    "hypothesis": "hypotheses",
    "problem_disposition": "problem_dispositions",
    "evidence_need": "evidence_needs",
    "search_request": "search_requests",
    "contradiction": "contradictions",
    "task_candidate": "task_candidates",
}
_ACTIVE_STATUSES = {"proposed", "contested"}
_SOURCE_PREFIXES = (
    "artifact:",
    "artifact://",
    "doi:",
    "paper:",
    "source:",
    "source-fragment:",
    "url:",
)
_DISPOSITIONS = {
    "established_solution",
    "active_open_problem",
    "engineering_problem",
    "researchable_gap",
    "underspecified",
    "currently_intractable",
    "category_error",
    "mixed",
    "unresolved",
}
_PERMITTED_NEXT_STEPS = {
    "continue_discussion",
    "clarify",
    "search",
    "split_problem",
    "reformulate",
    "reuse_known_solution",
    "formulate_research_task",
    "formulate_engineering_task",
    "defer",
    "stop",
}
_TASK_KINDS = {
    "research",
    "engineering",
    "clarification",
    "literature_search",
    "reuse_known_solution",
    "defer",
    "stop",
}
_MAX_PATCH_OPERATIONS = 16
_MAX_SOURCE_CANDIDATES = 6
_RECORD_FIELDS = {
    "id",
    "target_type",
    "statement",
    "status",
    "derivation",
    "basis_refs",
    "confidence",
    "uncertainty",
    "attributes",
}


def build_discussion_event(
    *,
    event_id: str,
    inquiry_id: str,
    direction_ref: str,
    ordinal: int,
    actor_kind: str,
    actor_id: str,
    text: str,
    created_at: str,
    task_ref: str | None = None,
    content_kind: str = "message",
    source_refs: Sequence[str] = (),
    prior_projection_digest: str | None = None,
    actor_label: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Create one immutable raw discussion event without interpreting its content."""

    content: dict[str, Any] = {
        "kind": content_kind,
        "text": str(text or "").strip(),
        "text_digest": digest_payload(str(text or "").strip()),
    }
    if language:
        content["language"] = language
    actor = {"kind": actor_kind, "actor_id": actor_id}
    if actor_label:
        actor["label"] = actor_label
    event = stamp_digest(
        {
            "schema": "adaos.research.discussion_event.v1",
            "schema_version": "1.0.0",
            "event_id": event_id,
            "inquiry_id": inquiry_id,
            "direction_ref": direction_ref,
            **({"task_ref": task_ref} if task_ref else {}),
            "ordinal": int(ordinal),
            "actor": actor,
            "content": content,
            "prior_projection_digest": prior_projection_digest,
            "source_refs": list(dict.fromkeys(str(item) for item in source_refs)),
            "created_at": created_at,
        }
    )
    return validate_discussion_event(event)


def validate_discussion_event(value: Mapping[str, Any]) -> dict[str, Any]:
    event = _as_mapping(value, "discussion_event")
    _validate_schema(event, "research.discussion_event.v1.schema.json")
    _require_matching_digest(event, "discussion_event")
    content = _as_mapping(event["content"], "discussion_event.content")
    if content["text_digest"] != digest_payload(str(content["text"])):
        raise ResearchSynthesisError("discussion_event content text_digest mismatch")
    return dict(event)


def new_inquiry_projection(
    *,
    inquiry_id: str,
    direction_ref: str,
    created_at: str,
    task_ref: str | None = None,
) -> dict[str, Any]:
    """Create an empty revision zero that can receive typed patches."""

    records = {collection: [] for collection in _RECORD_COLLECTIONS.values()}
    measures, readiness = _measure(records, churn=_empty_churn())
    projection = stamp_digest(
        {
            "schema": "adaos.research.inquiry_projection.v1",
            "schema_version": "1.0.0",
            "inquiry_id": inquiry_id,
            "direction_ref": direction_ref,
            **({"task_ref": task_ref} if task_ref else {}),
            "revision": 0,
            "parent_digest": None,
            "applied_patch_digest": None,
            "state": "draft",
            "records": records,
            "measures": measures,
            "readiness": readiness,
            "provenance": {
                "event_refs": [],
                "patch_digests": [],
                "created_by": "research_fabric_deterministic_patch_application",
            },
            "created_at": created_at,
        }
    )
    return validate_inquiry_projection(projection)


def build_projection_patch(
    payload: Mapping[str, Any],
    *,
    patch_id: str,
    inquiry_id: str,
    base_projection_digest: str,
    trigger_event_ref: str,
    actor_kind: str,
    actor_id: str,
    created_at: str,
    model: str | None = None,
    provider_job_id: str | None = None,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind model- or human-proposed operations to Fabric-owned control fields."""

    actor: dict[str, Any] = {"kind": actor_kind, "actor_id": actor_id}
    if model:
        actor["model"] = model
    if provider_job_id:
        actor["provider_job_id"] = provider_job_id
    patch = {
        "schema": "adaos.research.projection_patch.v1",
        "schema_version": "1.0.0",
        "patch_id": patch_id,
        "inquiry_id": inquiry_id,
        "base_projection_digest": base_projection_digest,
        "trigger_event_ref": trigger_event_ref,
        "actor": actor,
        "operations": copy.deepcopy(list(payload.get("operations") or [])),
        "rationale": str(payload.get("rationale") or "").strip(),
        "created_at": created_at,
    }
    if usage is not None:
        patch["usage"] = _normalize_usage(usage)
    return validate_projection_patch(stamp_digest(patch))


def canonicalize_projection_patch_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repair only redundant wire-shape errors without inventing semantics."""

    operations: list[dict[str, Any]] = []
    normalizations: list[dict[str, Any]] = []
    for index, raw in enumerate(list(payload.get("operations") or [])):
        operation = dict(_as_mapping(raw, f"operations[{index}]"))
        action = str(operation.get("action") or "")
        allowed = {"action", "target_type", "target_id", "basis_refs", "record"}
        lifted = set(operation) & _RECORD_FIELDS
        unknown = set(operation) - allowed - _RECORD_FIELDS
        if unknown:
            raise ResearchSynthesisError(
                f"operations[{index}] has unknown fields: {', '.join(sorted(unknown))}"
            )
        cleaned = {key: copy.deepcopy(operation[key]) for key in allowed if key in operation}
        if action == "upsert":
            record = (
                dict(_as_mapping(operation["record"], f"operations[{index}].record"))
                if operation.get("record") is not None
                else {}
            )
            for key in sorted(lifted):
                if key not in record:
                    record[key] = copy.deepcopy(operation[key])
                    normalizations.append(
                        {"operation_index": index, "field": key, "action": "nested_record_field"}
                    )
            for field, source in (
                ("id", "target_id"),
                ("target_type", "target_type"),
                ("basis_refs", "basis_refs"),
            ):
                if field not in record and source in cleaned:
                    record[field] = copy.deepcopy(cleaned[source])
                    normalizations.append(
                        {"operation_index": index, "field": field, "action": "copied_redundant_control"}
                    )
            cleaned["record"] = record
        elif lifted:
            raise ResearchSynthesisError(
                f"operations[{index}] cannot carry record fields for action {action}"
            )
        operations.append(cleaned)
    return {
        "operations": operations,
        "rationale": str(payload.get("rationale") or "").strip(),
    }, normalizations


def validate_projection_patch(value: Mapping[str, Any]) -> dict[str, Any]:
    patch = _as_mapping(value, "projection_patch")
    _validate_schema(patch, "research.projection_patch.v1.schema.json")
    _require_matching_digest(patch, "projection_patch")
    actor = _as_mapping(patch["actor"], "projection_patch.actor")
    if len(patch["operations"]) > _MAX_PATCH_OPERATIONS:
        raise ResearchSynthesisError(
            f"projection patch exceeds compactness limit of {_MAX_PATCH_OPERATIONS} operations"
        )
    trigger = str(patch["trigger_event_ref"])
    for index, raw in enumerate(patch["operations"]):
        operation = _as_mapping(raw, f"projection_patch.operations[{index}]")
        if trigger not in operation["basis_refs"]:
            raise ResearchSynthesisError(
                f"projection patch operation {index} must cite its trigger_event_ref"
            )
        if operation["action"] != "upsert":
            continue
        record = _as_mapping(operation["record"], f"projection_patch.operations[{index}].record")
        if record["id"] != operation["target_id"]:
            raise ResearchSynthesisError(f"projection patch operation {index} target_id mismatch")
        if record["target_type"] != operation["target_type"]:
            raise ResearchSynthesisError(f"projection patch operation {index} target_type mismatch")
        if trigger not in record["basis_refs"]:
            raise ResearchSynthesisError(f"projection record {record['id']} must cite its trigger event")
        if actor["kind"] == "llm" and record["derivation"] == "human_decision":
            raise ResearchSynthesisError("LLM patches cannot create human_decision records")
        if record["derivation"] == "source_supported" and not _has_source_ref(record["basis_refs"]):
            raise ResearchSynthesisError(
                f"source_supported record {record['id']} needs an admitted source ref"
            )
        _validate_record_semantics(record, actor_kind=str(actor["kind"]))
    return dict(patch)


def apply_projection_patch(
    projection: Mapping[str, Any],
    patch: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    context_events: Sequence[Mapping[str, Any]] = (),
    created_at: str | None = None,
) -> dict[str, Any]:
    """Apply a typed patch and return a new immutable revision plus semantic diff."""

    base = validate_inquiry_projection(projection)
    checked_patch = validate_projection_patch(patch)
    checked_event = validate_discussion_event(event)
    if base["state"] != "draft":
        raise ResearchSynthesisError("closed inquiry projections cannot be revised")
    if checked_patch["inquiry_id"] != base["inquiry_id"]:
        raise ResearchSynthesisError("projection patch inquiry_id mismatch")
    if checked_event["inquiry_id"] != base["inquiry_id"]:
        raise ResearchSynthesisError("discussion event inquiry_id mismatch")
    if checked_patch["base_projection_digest"] != base["digest"]:
        raise ResearchSynthesisError("projection patch base digest mismatch")
    event_ref = f"discussion-event:{checked_event['event_id']}"
    if checked_patch["trigger_event_ref"] != event_ref:
        raise ResearchSynthesisError("projection patch trigger event mismatch")
    if checked_event.get("prior_projection_digest") != base["digest"]:
        raise ResearchSynthesisError("discussion event prior projection digest mismatch")
    context_event_refs: list[str] = []
    for raw_context_event in context_events:
        context_event = validate_discussion_event(raw_context_event)
        context_ref = f"discussion-event:{context_event['event_id']}"
        if context_ref == event_ref or context_ref in context_event_refs:
            continue
        if context_event["inquiry_id"] != base["inquiry_id"]:
            raise ResearchSynthesisError("context discussion event inquiry_id mismatch")
        if context_event.get("prior_projection_digest") != base["digest"]:
            raise ResearchSynthesisError(
                "context discussion event prior projection digest mismatch"
            )
        context_event_refs.append(context_ref)
    admitted_discussion_refs = {
        *base["provenance"]["event_refs"],
        *context_event_refs,
        event_ref,
    }
    admitted_source_refs = set(str(item) for item in checked_event["source_refs"])
    for operation in checked_patch["operations"]:
        operation_discussion_refs = set(_discussion_refs(operation["basis_refs"]))
        if not operation_discussion_refs <= admitted_discussion_refs:
            raise ResearchSynthesisError(
                "projection patch cites discussion events outside the admitted context"
            )
        if operation["action"] != "upsert":
            continue
        record = operation["record"]
        record_discussion_refs = set(_discussion_refs(record["basis_refs"]))
        if not record_discussion_refs <= admitted_discussion_refs:
            raise ResearchSynthesisError(
                f"record {record['id']} cites discussion events outside the admitted context"
            )
        source_refs = set(_source_refs(record["basis_refs"]))
        source_assertion = (
            record["derivation"] == "source_supported"
            or record["attributes"].get("epistemic_status") == "source_supported"
            or record["attributes"].get("assessment_status") == "source_checked"
        )
        if source_assertion and not source_refs <= admitted_source_refs:
            raise ResearchSynthesisError(
                f"record {record['id']} cites sources not admitted by its discussion event"
            )

    records = copy.deepcopy(base["records"])
    indexes = _record_indexes(records)
    changes: list[dict[str, Any]] = []
    for operation in checked_patch["operations"]:
        target_type = str(operation["target_type"])
        collection = _RECORD_COLLECTIONS[target_type]
        target_id = str(operation["target_id"])
        previous = copy.deepcopy(indexes.get(target_id))
        if previous and previous[0] != collection:
            raise ResearchSynthesisError(f"record id {target_id} already belongs to another target_type")
        if operation["action"] == "upsert":
            after = copy.deepcopy(dict(operation["record"]))
            if previous:
                records[collection][previous[1]] = after
            else:
                records[collection].append(after)
            changes.append(
                {
                    "action": "added" if previous is None else "changed",
                    "target_type": target_type,
                    "target_id": target_id,
                    "before": previous[2] if previous else None,
                    "after": after,
                }
            )
        else:
            if previous is None:
                raise ResearchSynthesisError(
                    f"cannot {operation['action']} unknown projection record {target_id}"
                )
            after = copy.deepcopy(previous[2])
            after["status"] = "rejected" if operation["action"] == "reject" else "superseded"
            after["basis_refs"] = list(
                dict.fromkeys([*after["basis_refs"], *operation["basis_refs"]])
            )
            records[collection][previous[1]] = after
            changes.append(
                {
                    "action": str(operation["action"]),
                    "target_type": target_type,
                    "target_id": target_id,
                    "before": previous[2],
                    "after": after,
                }
            )
        indexes = _record_indexes(records)

    for collection in records.values():
        collection.sort(key=lambda item: str(item["id"]))
    churn = {
        "added": sum(item["action"] == "added" for item in changes),
        "changed": sum(item["action"] == "changed" for item in changes),
        "rejected_or_superseded": sum(
            item["action"] in {"reject", "supersede"} for item in changes
        ),
    }
    measures, readiness = _measure(records, churn=churn)
    projection_value = stamp_digest(
        {
            "schema": "adaos.research.inquiry_projection.v1",
            "schema_version": "1.0.0",
            "inquiry_id": base["inquiry_id"],
            "direction_ref": base["direction_ref"],
            **({"task_ref": base["task_ref"]} if base.get("task_ref") else {}),
            "revision": int(base["revision"]) + 1,
            "parent_digest": base["digest"],
            "applied_patch_digest": checked_patch["digest"],
            "state": "draft",
            "records": records,
            "measures": measures,
            "readiness": readiness,
            "provenance": {
                "event_refs": list(
                    dict.fromkeys(
                        [
                            *base["provenance"]["event_refs"],
                            *context_event_refs,
                            event_ref,
                        ]
                    )
                ),
                "patch_digests": [
                    *base["provenance"]["patch_digests"],
                    checked_patch["digest"],
                ],
                "created_by": "research_fabric_deterministic_patch_application",
            },
            "created_at": created_at or _utc_now(),
        }
    )
    checked_projection = validate_inquiry_projection(projection_value)
    return {
        "projection": checked_projection,
        "semantic_diff": {
            "schema": "adaos.research.projection_semantic_diff.v1",
            "base_projection_digest": base["digest"],
            "result_projection_digest": checked_projection["digest"],
            "patch_digest": checked_patch["digest"],
            "changes": changes,
            "churn": churn,
        },
    }


def validate_inquiry_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    projection = _as_mapping(value, "inquiry_projection")
    _validate_schema(projection, "research.inquiry_projection.v1.schema.json")
    _require_matching_digest(projection, "inquiry_projection")
    records = _as_mapping(projection["records"], "inquiry_projection.records")
    indexes = _record_indexes(records)
    for record_id, (_, _, record) in indexes.items():
        if record["id"] != record_id:
            raise ResearchSynthesisError("projection record index drift")
        _validate_record_semantics(record, actor_kind=None)
    measured, readiness = _measure(records, churn=projection["measures"]["semantic_churn"])
    if measured != projection["measures"]:
        raise ResearchSynthesisError("inquiry projection measures do not match its records")
    if readiness != projection["readiness"]:
        raise ResearchSynthesisError("inquiry projection readiness does not match its records")
    return dict(projection)


def accept_inquiry_projection(
    projection: Mapping[str, Any],
    *,
    acceptance_id: str,
    decision: str,
    accepted_by: str,
    accepted_at: str,
    rationale: str,
) -> dict[str, Any]:
    """Record a human decision over one exact projection digest."""

    checked = validate_inquiry_projection(projection)
    allowed = {
        "accept_for_research_task": "ready_for_research_task",
        "accept_for_engineering_task": "ready_for_engineering_task",
        "reuse_known_solution": "reuse_known_solution",
        "defer_intractable": "defer_or_stop",
        "stop": "defer_or_stop",
    }
    expected = allowed.get(decision)
    if decision != "request_revision" and checked["readiness"]["decision"] != expected:
        raise ResearchSynthesisError(
            f"inquiry decision {decision} is incompatible with readiness "
            f"{checked['readiness']['decision']}"
        )
    acceptance = stamp_digest(
        {
            "schema": "adaos.research.inquiry_acceptance.v1",
            "schema_version": "1.0.0",
            "acceptance_id": acceptance_id,
            "inquiry_id": checked["inquiry_id"],
            "projection_digest": checked["digest"],
            "projection_revision": checked["revision"],
            "decision": decision,
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
            "rationale": rationale,
        }
    )
    _validate_schema(acceptance, "research.inquiry_acceptance.v1.schema.json")
    _require_matching_digest(acceptance, "inquiry_acceptance")
    return acceptance


def build_projection_patch_messages(
    projection: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    admitted_sources: Sequence[Mapping[str, Any]] = (),
    unprojected_events: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, str]]:
    """Build the domain-neutral Researcher prompt for one projection update."""

    checked_projection = validate_inquiry_projection(projection)
    checked_event = validate_discussion_event(event)
    current_event_ref = f"discussion-event:{checked_event['event_id']}"
    represented_event_refs = set(checked_projection["provenance"]["event_refs"])
    pending_events: list[dict[str, Any]] = []
    pending_refs: set[str] = set()
    pending_content_indexes: dict[str, int] = {}
    pending_equivalence: list[dict[str, Any]] = []
    for raw_pending_event in unprojected_events:
        pending_event = validate_discussion_event(raw_pending_event)
        pending_ref = f"discussion-event:{pending_event['event_id']}"
        if pending_ref == current_event_ref or pending_ref in represented_event_refs:
            continue
        if pending_event["inquiry_id"] != checked_projection["inquiry_id"]:
            raise ResearchSynthesisError("unprojected discussion event inquiry_id mismatch")
        if pending_event.get("prior_projection_digest") != checked_projection["digest"]:
            raise ResearchSynthesisError(
                "unprojected discussion event prior projection digest mismatch"
            )
        if pending_ref in pending_refs:
            continue
        pending_refs.add(pending_ref)
        content_key = str(
            pending_event.get("content", {}).get("text_digest")
            or pending_event.get("digest")
            or pending_ref
        )
        existing_index = pending_content_indexes.get(content_key)
        if existing_index is not None:
            pending_equivalence[existing_index]["event_refs"].append(pending_ref)
            continue
        pending_content_indexes[content_key] = len(pending_events)
        pending_events.append(pending_event)
        pending_equivalence.append(
            {
                "representative_ref": pending_ref,
                "event_refs": [pending_ref],
            }
        )
    source_packet = [
        {
            "ref": item.get("ref"),
            "digest": item.get("digest"),
            "title": item.get("title") or item.get("name"),
            "authority": item.get("authority"),
            "reading_status": item.get("actual_reading_status"),
            "content": item.get("content"),
        }
        for item in admitted_sources
    ]
    system = (
        "You are the Researcher LLM inside AdaOS Research Fabric. Update a compact, typed "
        "scientific inquiry projection; do not write a paper or jump to implementation. Separate "
        "the author's explicit position, your inference, sourced knowledge, open scientific "
        "questions, and engineering work. Return a json object only. Every operation must cite "
        "the current event_ref. Unprojected prior events are durable discussion inputs that were "
        "not represented because an earlier LLM attempt failed; incorporate their relevant "
        "semantics and add their discussion-event refs as additional basis_refs. "
        "Classify each active problem frame with a provisional ProblemDisposition. A claim that a "
        "problem is solved, open, or currently intractable requires checked source support before "
        "it can authorize stopping or task formulation. When support is absent, use disposition "
        "unresolved with a search_request or evidence_need. Detect category errors, metaphor-to-"
        "mechanism leaps, unfalsifiable hypotheses, and premature engineering schemes. Hard "
        "compactness contract: return at most 16 operations, at most 3 problem frames, exactly "
        "one disposition per frame, at most 3 search requests, and at most 2 contradictions. In "
        "a source-free first turn, prioritize concepts, frames, dispositions, contradictions, and "
        "searches; omit hypotheses and task candidates unless essential. Keep every statement and "
        "attribute compact. Return JSON only with operations and rationale. Never mark a human decision."
    )
    record_shape = {
            "id": "stable compact id",
            "target_type": "one target type",
            "statement": "compact scientific statement",
            "status": "proposed | contested",
            "derivation": (
                "explicit_user | inferred_from_discussion | model_proposed | source_supported"
            ),
            "basis_refs": [f"discussion-event:{checked_event['event_id']}"],
            "confidence": "low | medium | high | not_assessed",
            "uncertainty": "what remains uncertain",
            "attributes": {},
    }
    contract = {
        "actions": ["upsert", "reject", "supersede"],
        "target_types": list(_RECORD_COLLECTIONS),
        "operation_shapes": {
            "upsert": {
                "action": "upsert",
                "target_type": "one target type",
                "target_id": "same value as record.id",
                "record": record_shape,
                "basis_refs": [f"discussion-event:{checked_event['event_id']}"],
            },
            "reject_or_supersede": {
                "action": "reject | supersede",
                "target_type": "one target type",
                "target_id": "existing record id",
                "basis_refs": [f"discussion-event:{checked_event['event_id']}"],
            },
        },
        "required_attributes": {
            "problem_frame": ["scope", "system", "phenomenon", "desired_explanation", "exclusions"],
            "knowledge_claim": ["epistemic_status"],
            "hypothesis": ["falsification_condition"],
            "problem_disposition": [
                "problem_frame_ref",
                "disposition",
                "assessment_status",
                "rationale",
                "reconsideration_conditions",
                "permitted_next_steps",
            ],
            "search_request": ["gap_ref", "query", "stop_rule"],
            "task_candidate": ["task_kind", "derives_from_refs", "objective", "exit_condition"],
        },
        "dispositions": sorted(_DISPOSITIONS),
        "task_kinds": sorted(_TASK_KINDS),
        "assessment_statuses": ["provisional", "source_checked", "human_confirmed"],
        "permitted_next_steps": sorted(_PERMITTED_NEXT_STEPS),
    }
    user = {
        "event_ref": current_event_ref,
        "event": checked_event,
        "unprojected_prior_events": pending_events,
        "unprojected_event_equivalence": pending_equivalence,
        "current_projection": checked_projection,
        "admitted_sources": source_packet,
        "output_contract": {
            "operations": "array of objects matching exactly one operation_shape; record is nested only for upsert",
            "operation_contract": contract,
            "rationale": "non-empty string",
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)},
    ]


def build_source_discovery_messages(
    projection: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Build a search-only prompt from active typed SearchRequests."""

    checked = validate_inquiry_projection(projection)
    requests = [
        item
        for item in checked["records"]["search_requests"]
        if item["status"] in _ACTIVE_STATUSES
    ]
    if not requests:
        raise ResearchSynthesisError("source discovery requires an active search_request")
    system = (
        "You are the source-discovery Researcher inside AdaOS Research Fabric. Use web search "
        "to find primary scientific publications, authoritative standards, datasets, or software "
        "that directly address the typed SearchRequests. Prefer DOI, publisher, proceedings, "
        "repository, or official project pages over summaries. Do not decide whether the problem "
        "is solved, novel, or tractable. Do not claim that you read a full paper unless the search "
        "tool exposed its full content. Open-access and license fields are discovery claims only, "
        "not admission decisions. Hard compactness contract: return at most 6 candidates total, "
        "normally the 2 closest primary or authoritative sources per SearchRequest. Keep relevance "
        "to two sentences, warnings compact, and search_notes to four sentences. Return a json object only."
    )
    user = {
        "projection_digest": checked["digest"],
        "search_requests": requests,
        "output_contract": {
            "candidates_max_items": _MAX_SOURCE_CANDIDATES,
            "candidates": [
                {
                    "title": "string",
                    "url": "https URL",
                    "source_type": "paper | dataset | standard | software | other",
                    "authors": ["string"],
                    "year": "integer or null",
                    "identifiers": {"doi": None, "arxiv": None, "openalex": None},
                    "open_access": {
                        "status": "claimed | unknown",
                        "url": None,
                        "license": None,
                    },
                    "relevance": "specific relation to one or more requests",
                    "supports_refs": ["search-request:<id>"],
                    "warnings": ["metadata or content limitations"],
                }
            ],
            "search_notes": "coverage and stopping limitations",
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)},
    ]


def build_source_discovery_receipt(
    payload: Mapping[str, Any],
    *,
    discovery_id: str,
    projection: Mapping[str, Any],
    model: str,
    provider_job_id: str,
    usage: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Bind untrusted search output to an immutable, non-admitted receipt."""

    checked = validate_inquiry_projection(projection)
    active_requests = {
        str(item["id"])
        for item in checked["records"]["search_requests"]
        if item["status"] in _ACTIVE_STATUSES
    }
    if not active_requests:
        raise ResearchSynthesisError("source discovery requires an active search_request")
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    raw_candidates = list(payload.get("candidates") or [])
    if len(raw_candidates) > _MAX_SOURCE_CANDIDATES:
        raise ResearchSynthesisError(
            f"source discovery exceeds compactness limit of {_MAX_SOURCE_CANDIDATES} candidates"
        )
    for index, raw in enumerate(raw_candidates):
        candidate = _as_mapping(raw, f"source_discovery.candidates[{index}]")
        url = str(candidate.get("url") or "").strip()
        if url in seen_urls:
            continue
        seen_urls.add(url)
        supports = [
            str(item).removeprefix("search-request:")
            for item in list(candidate.get("supports_refs") or [])
        ]
        if not supports or not set(supports) <= active_requests:
            raise ResearchSynthesisError(
                f"source candidate {index} references an inactive search_request"
            )
        open_access = _as_mapping(
            candidate.get("open_access") or {},
            f"source_discovery.candidates[{index}].open_access",
        )
        identifiers = _as_mapping(
            candidate.get("identifiers") or {},
            f"source_discovery.candidates[{index}].identifiers",
        )
        candidates.append(
            {
                "candidate_id": "candidate."
                + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20],
                "title": str(candidate.get("title") or "").strip(),
                "url": url,
                "source_type": str(candidate.get("source_type") or "other"),
                "authors": [str(item).strip() for item in candidate.get("authors") or [] if str(item).strip()],
                "year": candidate.get("year"),
                "identifiers": {
                    "doi": identifiers.get("doi"),
                    "arxiv": identifiers.get("arxiv"),
                    "openalex": identifiers.get("openalex"),
                },
                "open_access": {
                    "status": str(open_access.get("status") or "unknown"),
                    "url": open_access.get("url"),
                    "license": open_access.get("license"),
                },
                "relevance": str(candidate.get("relevance") or "").strip(),
                "supports_refs": [f"search-request:{item}" for item in supports],
                "discovery_status": "candidate_not_admitted",
                "warnings": [str(item) for item in candidate.get("warnings") or [] if str(item)],
            }
        )
    receipt = stamp_digest(
        {
            "schema": "adaos.research.source_discovery.v1",
            "schema_version": "1.0.0",
            "discovery_id": discovery_id,
            "inquiry_id": checked["inquiry_id"],
            "projection_digest": checked["digest"],
            "search_request_refs": [
                f"search-request:{item}" for item in sorted(active_requests)
            ],
            "candidates": candidates,
            "search_notes": str(payload.get("search_notes") or "").strip(),
            "producer": {
                "kind": "researcher_llm",
                "model": model,
                "provider_job_id": provider_job_id,
                "tool": "web_search",
            },
            "usage": _normalize_usage(usage),
            "created_at": created_at,
        }
    )
    _validate_schema(receipt, "research.source_discovery.v1.schema.json")
    _require_matching_digest(receipt, "source_discovery")
    return receipt


def _validate_record_semantics(record: Mapping[str, Any], *, actor_kind: str | None) -> None:
    target_type = str(record["target_type"])
    if target_type not in _RECORD_COLLECTIONS:
        raise ResearchSynthesisError(f"unknown inquiry record target_type {target_type}")
    attrs = _as_mapping(record["attributes"], f"{record['id']}.attributes")
    if actor_kind == "llm" and record["derivation"] == "human_decision":
        raise ResearchSynthesisError("LLM cannot materialize a human decision")
    if target_type == "problem_frame":
        _require_attributes(record, attrs, "scope", "system", "phenomenon", "desired_explanation", "exclusions")
    elif target_type == "knowledge_claim":
        _require_attributes(record, attrs, "epistemic_status")
        if attrs["epistemic_status"] == "source_supported" and not _has_source_ref(record["basis_refs"]):
            raise ResearchSynthesisError(f"knowledge claim {record['id']} lacks source support")
    elif target_type == "hypothesis":
        _require_attributes(record, attrs, "falsification_condition")
    elif target_type == "problem_disposition":
        _require_attributes(
            record,
            attrs,
            "problem_frame_ref",
            "disposition",
            "assessment_status",
            "rationale",
            "reconsideration_conditions",
            "permitted_next_steps",
        )
        if attrs["disposition"] not in _DISPOSITIONS:
            raise ResearchSynthesisError(f"problem disposition {record['id']} has invalid class")
        if attrs["assessment_status"] not in {"provisional", "source_checked", "human_confirmed"}:
            raise ResearchSynthesisError(f"problem disposition {record['id']} has invalid assessment_status")
        if attrs["assessment_status"] == "source_checked" and not _has_source_ref(record["basis_refs"]):
            raise ResearchSynthesisError(f"source_checked disposition {record['id']} lacks source support")
        if attrs["assessment_status"] == "human_confirmed" and actor_kind == "llm":
            raise ResearchSynthesisError("LLM cannot mark a disposition human_confirmed")
        if not isinstance(attrs["permitted_next_steps"], list) or not set(attrs["permitted_next_steps"]) <= _PERMITTED_NEXT_STEPS:
            raise ResearchSynthesisError(f"problem disposition {record['id']} has invalid next steps")
    elif target_type == "search_request":
        _require_attributes(record, attrs, "gap_ref", "query", "stop_rule")
    elif target_type == "task_candidate":
        _require_attributes(record, attrs, "task_kind", "derives_from_refs", "objective", "exit_condition")
        if attrs["task_kind"] not in _TASK_KINDS:
            raise ResearchSynthesisError(f"task candidate {record['id']} has invalid task_kind")


def _measure(
    records: Mapping[str, Any], *, churn: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    active = [
        item
        for collection in records.values()
        for item in collection
        if item["status"] in _ACTIVE_STATUSES
    ]
    frames = {item["id"]: item for item in records["problem_frames"] if item["status"] in _ACTIVE_STATUSES}
    dispositions: dict[str, list[Mapping[str, Any]]] = {key: [] for key in frames}
    orphan_dispositions: list[str] = []
    for item in records["problem_dispositions"]:
        if item["status"] not in _ACTIVE_STATUSES:
            continue
        frame_ref = str(item["attributes"].get("problem_frame_ref") or "").removeprefix("problem-frame:")
        if frame_ref not in frames:
            orphan_dispositions.append(str(item["id"]))
        else:
            dispositions[frame_ref].append(item)

    unsupported_claims = [
        item["id"]
        for item in records["knowledge_claims"]
        if item["status"] in _ACTIVE_STATUSES
        and item["attributes"].get("epistemic_status") == "source_supported"
        and not _has_source_ref(item["basis_refs"])
    ]
    untraceable = [item["id"] for item in active if not item.get("basis_refs")]
    disposition_classes = {
        str(items[0]["attributes"]["disposition"])
        for items in dispositions.values()
        if len(items) == 1
    }
    compatible_task_ids, incompatible_task_ids = _compatible_tasks(
        records["task_candidates"], disposition_classes
    )
    measures = {
        "active_record_count": len(active),
        "unclassified_problem_count": sum(len(items) != 1 for items in dispositions.values()),
        "untraceable_record_count": len(untraceable),
        "unsupported_source_claim_count": len(unsupported_claims),
        "incompatible_task_count": len(incompatible_task_ids),
        "semantic_churn": {
            "added": int(churn.get("added") or 0),
            "changed": int(churn.get("changed") or 0),
            "rejected_or_superseded": int(churn.get("rejected_or_superseded") or 0),
        },
    }

    blockers: list[str] = []
    if not frames:
        blockers.append("No explicit problem frame has been separated from the discussion.")
        return measures, _readiness("continue_discussion", ["continue_discussion"], blockers)
    if orphan_dispositions:
        blockers.append("Problem dispositions reference unknown frames: " + ", ".join(orphan_dispositions))
    for frame_id, items in dispositions.items():
        if not items:
            blockers.append(f"Problem frame {frame_id} has no ProblemDisposition.")
        elif len(items) > 1:
            blockers.append(f"Problem frame {frame_id} has competing active ProblemDispositions.")
    if blockers:
        return measures, _readiness("clarify", ["clarify", "search"], blockers)

    selected = [items[0] for items in dispositions.values()]
    provisional_stop_classes = {
        str(item["attributes"]["disposition"])
        for item in selected
        if item["attributes"]["disposition"]
        in {"established_solution", "active_open_problem", "currently_intractable"}
        and item["attributes"]["assessment_status"] == "provisional"
    }
    if provisional_stop_classes:
        blockers.append(
            "SOTA or tractability classification is provisional and needs source checking: "
            + ", ".join(sorted(provisional_stop_classes))
        )
        return measures, _readiness("search", ["search", "continue_discussion"], blockers)
    if "unresolved" in disposition_classes:
        blockers.append("At least one problem disposition remains unresolved.")
        return measures, _readiness("search", ["search", "continue_discussion"], blockers)
    if "underspecified" in disposition_classes:
        blockers.append("At least one problem frame is underspecified.")
        return measures, _readiness("clarify", ["clarify"], blockers)
    if "mixed" in disposition_classes or len(
        disposition_classes
        & {"engineering_problem", "researchable_gap", "active_open_problem", "established_solution"}
    ) > 1:
        blockers.append("The projection mixes problem classes that require separate task branches.")
        return measures, _readiness("split_problem", ["split_problem"], blockers)
    if "category_error" in disposition_classes:
        blockers.append("The current problem statement contains a category error.")
        return measures, _readiness("reformulate", ["reformulate", "stop"], blockers)
    if "currently_intractable" in disposition_classes:
        return measures, _readiness("defer_or_stop", ["defer", "stop"], [])
    if disposition_classes == {"established_solution"}:
        return measures, _readiness(
            "reuse_known_solution", ["reuse_known_solution", "stop"], []
        )
    if incompatible_task_ids:
        blockers.append("Task candidates conflict with ProblemDisposition: " + ", ".join(incompatible_task_ids))
        return measures, _readiness("clarify", ["clarify"], blockers)
    if disposition_classes <= {"researchable_gap", "active_open_problem"}:
        if not compatible_task_ids:
            blockers.append("No bounded research task candidate has been derived from the problem frame.")
            return measures, _readiness("continue_discussion", ["continue_discussion"], blockers)
        return measures, _readiness(
            "ready_for_research_task", ["formulate_research_task"], []
        )
    if disposition_classes == {"engineering_problem"}:
        if not compatible_task_ids:
            blockers.append("No bounded engineering task candidate has been derived from the problem frame.")
            return measures, _readiness("continue_discussion", ["continue_discussion"], blockers)
        return measures, _readiness(
            "ready_for_engineering_task", ["formulate_engineering_task"], []
        )
    blockers.append("The disposition combination does not admit a task transition.")
    return measures, _readiness("clarify", ["clarify"], blockers)


def _compatible_tasks(
    task_candidates: Sequence[Mapping[str, Any]], disposition_classes: set[str]
) -> tuple[list[str], list[str]]:
    active = [item for item in task_candidates if item["status"] in _ACTIVE_STATUSES]
    if disposition_classes <= {"researchable_gap", "active_open_problem"}:
        expected = {"research", "literature_search"}
    elif disposition_classes == {"engineering_problem"}:
        expected = {"engineering"}
    elif disposition_classes == {"established_solution"}:
        expected = {"reuse_known_solution", "stop"}
    elif disposition_classes == {"currently_intractable"}:
        expected = {"defer", "stop"}
    else:
        expected = set()
    compatible = [str(item["id"]) for item in active if item["attributes"].get("task_kind") in expected]
    incompatible = [str(item["id"]) for item in active if item["attributes"].get("task_kind") not in expected]
    return compatible, incompatible


def _record_indexes(records: Mapping[str, Any]) -> dict[str, tuple[str, int, dict[str, Any]]]:
    indexes: dict[str, tuple[str, int, dict[str, Any]]] = {}
    for collection_name, raw_collection in records.items():
        if collection_name not in _RECORD_COLLECTIONS.values():
            raise ResearchSynthesisError(f"unknown inquiry record collection {collection_name}")
        if not isinstance(raw_collection, list):
            raise ResearchSynthesisError(f"inquiry record collection {collection_name} must be an array")
        for index, raw in enumerate(raw_collection):
            record = dict(_as_mapping(raw, f"{collection_name}[{index}]"))
            record_id = str(record.get("id") or "")
            if record_id in indexes:
                raise ResearchSynthesisError(f"duplicate inquiry record id {record_id}")
            expected_collection = _RECORD_COLLECTIONS.get(str(record.get("target_type") or ""))
            if expected_collection != collection_name:
                raise ResearchSynthesisError(f"record {record_id} is stored in the wrong collection")
            indexes[record_id] = (collection_name, index, record)
    return indexes


def _require_attributes(record: Mapping[str, Any], attrs: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if key not in attrs or attrs[key] in (None, "", [])]
    if missing:
        raise ResearchSynthesisError(
            f"{record['target_type']} record {record['id']} lacks attributes: {', '.join(missing)}"
        )


def _has_source_ref(refs: Sequence[str]) -> bool:
    return any(str(ref).lower().startswith(_SOURCE_PREFIXES) for ref in refs)


def _source_refs(refs: Sequence[str]) -> list[str]:
    return [str(ref) for ref in refs if str(ref).lower().startswith(_SOURCE_PREFIXES)]


def _discussion_refs(refs: Sequence[str]) -> list[str]:
    return [str(ref) for ref in refs if str(ref).startswith("discussion-event:")]


def _readiness(decision: str, transitions: Sequence[str], blockers: Sequence[str]) -> dict[str, Any]:
    return {
        "decision": decision,
        "admitted_transitions": list(dict.fromkeys(transitions)),
        "blockers": list(dict.fromkeys(str(item) for item in blockers if str(item))),
    }


def _empty_churn() -> dict[str, int]:
    return {"added": 0, "changed": 0, "rejected_or_superseded": 0}


def _normalize_usage(value: Mapping[str, Any]) -> dict[str, Any]:
    usage = value.get("usage") if isinstance(value.get("usage"), Mapping) else value
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cached = int(
        usage.get("cached_input_tokens")
        or (usage.get("input_tokens_details") or {}).get("cached_tokens")
        or 0
    )
    reasoning = int(
        usage.get("reasoning_tokens")
        or (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        or 0
    )
    total = int(usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "accounting_scope": "researcher_llm",
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
        "accuracy": "provider_reported" if usage else "unavailable",
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "accept_inquiry_projection",
    "apply_projection_patch",
    "build_discussion_event",
    "build_projection_patch",
    "build_projection_patch_messages",
    "build_source_discovery_messages",
    "build_source_discovery_receipt",
    "canonicalize_projection_patch_payload",
    "new_inquiry_projection",
    "validate_discussion_event",
    "validate_inquiry_projection",
    "validate_projection_patch",
]
