from __future__ import annotations

import json

import pytest

from adaos.sdk.research import (
    ResearchSynthesisError,
    accept_inquiry_projection,
    apply_projection_patch,
    build_discussion_event,
    build_projection_patch,
    build_projection_patch_messages,
    build_source_discovery_messages,
    build_source_discovery_receipt,
    canonicalize_projection_patch_payload,
    new_inquiry_projection,
)


NOW = "2026-08-30T10:00:00+00:00"
SOURCE = "source:paper-1"


def _initial(inquiry_id: str = "inquiry.test") -> dict:
    return new_inquiry_projection(
        inquiry_id=inquiry_id,
        direction_ref="research-direction:test",
        task_ref="research-task:test.task-001",
        created_at=NOW,
    )


def _event(
    projection: dict,
    *,
    event_id: str = "evt-1",
    text: str = "Discuss the problem",
    source_refs: list[str] | None = None,
    ordinal: int | None = None,
) -> dict:
    return build_discussion_event(
        event_id=event_id,
        inquiry_id=projection["inquiry_id"],
        direction_ref=projection["direction_ref"],
        task_ref=projection.get("task_ref"),
        ordinal=ordinal or projection["revision"] + 1,
        actor_kind="human",
        actor_id="user:test",
        text=text,
        source_refs=list(source_refs or []),
        prior_projection_digest=projection["digest"],
        created_at=NOW,
    )


def _record(
    target_type: str,
    record_id: str,
    statement: str,
    event_ref: str,
    *,
    attrs: dict | None = None,
    derivation: str = "model_proposed",
    basis_refs: list[str] | None = None,
) -> dict:
    refs = list(basis_refs or [event_ref])
    if event_ref not in refs:
        refs.append(event_ref)
    return {
        "id": record_id,
        "target_type": target_type,
        "statement": statement,
        "status": "proposed",
        "derivation": derivation,
        "basis_refs": refs,
        "confidence": "medium",
        "uncertainty": "Requires review.",
        "attributes": dict(attrs or {}),
    }


def _patch(projection: dict, event: dict, records: list[dict], *, patch_id: str = "patch-1") -> dict:
    event_ref = f"discussion-event:{event['event_id']}"
    return build_projection_patch(
        {
            "operations": [
                {
                    "action": "upsert",
                    "target_type": item["target_type"],
                    "target_id": item["id"],
                    "record": item,
                    "basis_refs": list(item["basis_refs"]),
                }
                for item in records
            ],
            "rationale": "Project the scientific semantics of this turn.",
        },
        patch_id=patch_id,
        inquiry_id=projection["inquiry_id"],
        base_projection_digest=projection["digest"],
        trigger_event_ref=event_ref,
        actor_kind="llm",
        actor_id="llm:researcher",
        model="gpt-test",
        created_at=NOW,
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        },
    )


def _frame(event_ref: str, frame_id: str = "PF1") -> dict:
    return _record(
        "problem_frame",
        frame_id,
        "Determine whether the proposed mechanism expresses a scientific gap.",
        event_ref,
        attrs={
            "scope": "bounded conceptual inquiry",
            "system": "candidate adaptive system",
            "phenomenon": "claimed mechanism",
            "desired_explanation": "what is known and what remains open",
            "exclusions": ["implementation before classification"],
        },
        derivation="inferred_from_discussion",
    )


def _disposition(
    event_ref: str,
    disposition: str,
    *,
    assessment_status: str = "provisional",
    basis_refs: list[str] | None = None,
) -> dict:
    return _record(
        "problem_disposition",
        "PD1",
        f"The current frame is classified as {disposition}.",
        event_ref,
        attrs={
            "problem_frame_ref": "problem-frame:PF1",
            "disposition": disposition,
            "assessment_status": assessment_status,
            "rationale": "Classification is explicit and revisable.",
            "reconsideration_conditions": ["new nearest-neighbor evidence"],
            "permitted_next_steps": ["search", "continue_discussion"],
        },
        basis_refs=basis_refs,
    )


def _task(event_ref: str, task_kind: str) -> dict:
    return _record(
        "task_candidate",
        "TC1",
        f"Candidate {task_kind} task.",
        event_ref,
        attrs={
            "task_kind": task_kind,
            "derives_from_refs": ["problem-frame:PF1", "problem-disposition:PD1"],
            "objective": "Resolve the bounded uncertainty.",
            "exit_condition": "A typed result is accepted or the branch is stopped.",
        },
    )


def test_empty_projection_requires_a_problem_frame() -> None:
    projection = _initial()

    assert projection["revision"] == 0
    assert projection["readiness"]["decision"] == "continue_discussion"
    assert projection["measures"]["active_record_count"] == 0


def test_provisional_sota_classification_requires_search_before_task_formulation() -> None:
    projection = _initial("inquiry.evolnomics")
    event = _event(projection, text="Could provenance-aware allocation be a research gap?")
    event_ref = f"discussion-event:{event['event_id']}"
    patch = _patch(
        projection,
        event,
        [_frame(event_ref), _disposition(event_ref, "active_open_problem"), _task(event_ref, "research")],
    )

    result = apply_projection_patch(projection, patch, event, created_at=NOW)

    assert result["projection"]["readiness"]["decision"] == "search"
    assert "provisional" in result["projection"]["readiness"]["blockers"][0]
    assert result["semantic_diff"]["churn"] == {
        "added": 3,
        "changed": 0,
        "rejected_or_superseded": 0,
    }


def test_source_checked_research_gap_can_yield_bounded_research_task() -> None:
    projection = _initial("inquiry.evolnomics")
    event = _event(projection, source_refs=[SOURCE])
    event_ref = f"discussion-event:{event['event_id']}"
    patch = _patch(
        projection,
        event,
        [
            _frame(event_ref),
            _disposition(
                event_ref,
                "researchable_gap",
                assessment_status="source_checked",
                basis_refs=[event_ref, SOURCE],
            ),
            _task(event_ref, "research"),
        ],
    )

    result = apply_projection_patch(projection, patch, event, created_at=NOW)
    accepted = accept_inquiry_projection(
        result["projection"],
        acceptance_id="accept-1",
        decision="accept_for_research_task",
        accepted_by="user:test",
        accepted_at=NOW,
        rationale="The exact projection is sufficiently bounded for task formulation.",
    )

    assert result["projection"]["readiness"]["decision"] == "ready_for_research_task"
    assert accepted["projection_digest"] == result["projection"]["digest"]


def test_neurocompiler_like_metaphor_engineering_mix_must_be_split() -> None:
    projection = _initial("inquiry.neurocompiler-regression")
    event = _event(
        projection,
        text="Evolution is a neurocompiler; let us implement a slot-based learner.",
        source_refs=[SOURCE],
    )
    event_ref = f"discussion-event:{event['event_id']}"
    records = [
        _frame(event_ref),
        _disposition(
            event_ref,
            "mixed",
            assessment_status="source_checked",
            basis_refs=[event_ref, SOURCE],
        ),
        _task(event_ref, "engineering"),
    ]
    records[1]["attributes"]["permitted_next_steps"] = ["split_problem"]
    patch = _patch(projection, event, records)

    result = apply_projection_patch(projection, patch, event, created_at=NOW)

    assert result["projection"]["readiness"]["decision"] == "split_problem"
    assert result["projection"]["readiness"]["admitted_transitions"] == ["split_problem"]


def test_llm_cannot_silently_materialize_a_human_decision() -> None:
    projection = _initial()
    event = _event(projection)
    event_ref = f"discussion-event:{event['event_id']}"
    record = _frame(event_ref)
    record["derivation"] = "human_decision"

    with pytest.raises(ResearchSynthesisError, match="human_decision"):
        _patch(projection, event, [record])


def test_incompatible_acceptance_fails_closed() -> None:
    projection = _initial()

    with pytest.raises(ResearchSynthesisError, match="incompatible"):
        accept_inquiry_projection(
            projection,
            acceptance_id="accept-invalid",
            decision="accept_for_engineering_task",
            accepted_by="user:test",
            accepted_at=NOW,
            rationale="This must not pass.",
        )


def test_source_discovery_is_typed_but_not_admitted_as_evidence() -> None:
    projection = _initial("inquiry.discovery")
    event = _event(projection)
    event_ref = f"discussion-event:{event['event_id']}"
    search = _record(
        "search_request",
        "SR1",
        "Find primary work on provenance-aware contribution allocation.",
        event_ref,
        attrs={
            "gap_ref": "problem-frame:PF1",
            "query": "software contribution provenance value allocation primary research",
            "stop_rule": "At least two primary nearest neighbors and one contrary result.",
        },
    )
    patch = _patch(projection, event, [_frame(event_ref), _disposition(event_ref, "unresolved"), search])
    projection = apply_projection_patch(projection, patch, event, created_at=NOW)["projection"]

    messages = build_source_discovery_messages(projection)
    receipt = build_source_discovery_receipt(
        {
            "candidates": [
                {
                    "title": "A primary paper",
                    "url": "https://example.org/paper",
                    "source_type": "paper",
                    "authors": ["A. Researcher"],
                    "year": 2025,
                    "identifiers": {"doi": "10.1000/example", "arxiv": None, "openalex": None},
                    "open_access": {"status": "claimed", "url": "https://example.org/paper.pdf", "license": "CC-BY-4.0"},
                    "relevance": "Directly studies the allocation mechanism.",
                    "supports_refs": ["search-request:SR1"],
                    "warnings": ["Only search metadata was checked."],
                }
            ],
            "search_notes": "Coverage is intentionally narrow.",
        },
        discovery_id="discovery-1",
        projection=projection,
        model="gpt-test",
        provider_job_id="job-1",
        usage={"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
        created_at=NOW,
    )

    assert "Use web search" in messages[0]["content"]
    assert receipt["candidates"][0]["discovery_status"] == "candidate_not_admitted"
    assert receipt["usage"]["accounting_scope"] == "researcher_llm"
    assert receipt["search_request_refs"] == ["search-request:SR1"]


def test_projection_patch_rejects_noncompact_json_paper() -> None:
    projection = _initial("inquiry.compactness")
    event = _event(projection)
    event_ref = f"discussion-event:{event['event_id']}"
    records = [
        _record("concept", f"C{index}", f"Concept {index}.", event_ref)
        for index in range(17)
    ]

    with pytest.raises(ResearchSynthesisError, match="compactness limit"):
        _patch(projection, event, records)


def test_wire_shape_canonicalizer_only_moves_known_record_fields() -> None:
    payload, normalizations = canonicalize_projection_patch_payload(
        {
            "operations": [
                {
                    "action": "upsert",
                    "target_type": "concept",
                    "target_id": "C1",
                    "basis_refs": ["discussion-event:evt-1"],
                    "statement": "A candidate concept.",
                    "status": "proposed",
                    "derivation": "model_proposed",
                    "confidence": "low",
                    "uncertainty": "Unreviewed.",
                    "attributes": {},
                }
            ],
            "rationale": "Preserve semantics while repairing the wire shape.",
        }
    )

    record = payload["operations"][0]["record"]
    assert record["id"] == "C1"
    assert record["target_type"] == "concept"
    assert record["basis_refs"] == ["discussion-event:evt-1"]
    assert {item["field"] for item in normalizations} == {
        "attributes",
        "basis_refs",
        "confidence",
        "derivation",
        "id",
        "statement",
        "status",
        "target_type",
        "uncertainty",
    }


def test_failed_turn_events_are_replayed_and_recorded_in_projection_provenance() -> None:
    projection = _initial("inquiry.replay")
    failed_event = _event(
        projection,
        event_id="evt-failed",
        text="The previous LLM attempt failed after this input was stored.",
        ordinal=1,
    )
    current_event = _event(
        projection,
        event_id="evt-current",
        text="Continue the discussion without losing the prior input.",
        ordinal=2,
    )
    failed_ref = "discussion-event:evt-failed"
    current_ref = "discussion-event:evt-current"

    messages = build_projection_patch_messages(
        projection,
        current_event,
        unprojected_events=[failed_event],
    )
    prompt = json.loads(messages[1]["content"])
    assert prompt["unprojected_prior_events"] == [failed_event]

    record = _record(
        "concept",
        "C-replayed",
        "Both durable discussion events inform this projection.",
        current_ref,
        basis_refs=[failed_ref, current_ref],
    )
    patch = _patch(projection, current_event, [record])
    result = apply_projection_patch(
        projection,
        patch,
        current_event,
        context_events=[failed_event],
        created_at=NOW,
    )

    assert result["projection"]["provenance"]["event_refs"] == [
        failed_ref,
        current_ref,
    ]


def test_replay_prompt_compacts_duplicate_content_without_losing_event_refs() -> None:
    projection = _initial("inquiry.replay-duplicates")
    first = _event(projection, event_id="evt-first", text="Repeated input", ordinal=1)
    duplicate = _event(
        projection,
        event_id="evt-duplicate",
        text="Repeated input",
        ordinal=2,
    )
    current = _event(projection, event_id="evt-current", text="Continue", ordinal=3)

    messages = build_projection_patch_messages(
        projection,
        current,
        unprojected_events=[first, duplicate],
    )
    prompt = json.loads(messages[1]["content"])

    assert [item["event_id"] for item in prompt["unprojected_prior_events"]] == [
        "evt-first"
    ]
    assert prompt["unprojected_event_equivalence"] == [
        {
            "representative_ref": "discussion-event:evt-first",
            "event_refs": [
                "discussion-event:evt-first",
                "discussion-event:evt-duplicate",
            ],
        }
    ]
