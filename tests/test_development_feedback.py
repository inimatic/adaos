from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import development_feedback as feedback_api
from adaos.services.development_feedback import DevelopmentFeedbackService


def _capture(service: DevelopmentFeedbackService) -> dict:
    return service.capture(
        source="codex",
        category="ambiguous_contract",
        summary="The SDK refresh contract does not define stale-data behavior.",
        impact=["comprehension", "reliability"],
        target_refs=["sdk:resources.query", "skill:demo_metrics_skill"],
        details="The implementation has two equally plausible retry semantics.",
        recommendation="Specify whether explicit Retry keeps the last valid value.",
        evidence_refs=[{"type": "file", "ref": "skills/demo_metrics_skill/webui.json"}],
        relation_refs=[{"type": "run", "id": "run.demo"}],
        actor="codex:test",
    )["feedback"]


def test_development_feedback_lifecycle_query_comment_and_project_promotion(tmp_path: Path) -> None:
    service = DevelopmentFeedbackService(state_dir=tmp_path)
    feedback = _capture(service)

    duplicate = _capture(service)
    assert duplicate["feedback_id"] == feedback["feedback_id"]
    assert duplicate["occurrence_count"] == 2

    listed = service.list(category="ambiguous_contract", search="stale-data")
    assert [item["feedback_id"] for item in listed] == [feedback["feedback_id"]]

    triaged = service.transition(
        feedback["feedback_id"],
        status="triaged",
        actor="human:test",
        classification={"owner_route": "sdk_understanding"},
        expected_revision=duplicate["revision"],
    )
    commented = service.comment(
        feedback["feedback_id"],
        body="Confirm against the public query contract before changing code.",
        actor="human:test",
        expected_revision=triaged["revision"],
    )
    accepted = service.transition(
        feedback["feedback_id"],
        status="accepted",
        actor="human:test",
        expected_revision=commented["revision"],
    )
    promoted = service.promote(
        feedback["feedback_id"],
        route="project",
        actor="human:test",
        expected_revision=accepted["revision"],
    )

    assert promoted["feedback"]["status"] == "promoted"
    assert promoted["ticket"]["kind"] == "review_debt"
    assert promoted["ticket"]["component_ref"] == "skill:demo_metrics_skill"
    assert promoted["ticket"]["metadata"]["development_feedback_id"] == feedback["feedback_id"]
    assert promoted["feedback"]["ticket_refs"] == [promoted["ticket"]["ticket_id"]]


def test_development_feedback_requires_acceptance_and_optimistic_revision(tmp_path: Path) -> None:
    service = DevelopmentFeedbackService(state_dir=tmp_path)
    feedback = _capture(service)

    with pytest.raises(ValueError, match="accepted"):
        service.promote(feedback["feedback_id"], route="project", actor="test")
    with pytest.raises(ValueError, match="revision"):
        service.transition(
            feedback["feedback_id"],
            status="triaged",
            actor="test",
            expected_revision=99,
        )


def test_idempotent_feedback_replay_aggregates_distinct_task_observations(
    tmp_path: Path,
) -> None:
    service = DevelopmentFeedbackService(state_dir=tmp_path)
    common = {
        "source": "validator",
        "category": "validation_gap",
        "summary": "The public tool contract remains unsatisfied.",
        "target_refs": ["skill:demo", "sdk:skill.webui_tool_contract"],
        "dedup_key": "validator:demo:webui",
        "actor": "validator:test",
        "idempotent_replay": True,
    }

    first = service.capture(
        **common,
        evidence_refs=[{"type": "test", "ref": "task.one:test_report"}],
        relation_refs=[{"type": "skill_factory_task", "id": "task.one"}],
    )["feedback"]
    replay = service.capture(
        **common,
        evidence_refs=[{"type": "test", "ref": "task.one:test_report"}],
        relation_refs=[{"type": "skill_factory_task", "id": "task.one"}],
    )["feedback"]
    second_task = service.capture(
        **common,
        evidence_refs=[{"type": "test", "ref": "task.two:test_report"}],
        relation_refs=[{"type": "skill_factory_task", "id": "task.two"}],
    )["feedback"]

    assert replay["feedback_id"] == first["feedback_id"]
    assert replay["occurrence_count"] == 1
    assert second_task["feedback_id"] == first["feedback_id"]
    assert second_task["occurrence_count"] == 2
    assert {item["id"] for item in second_task["relation_refs"]} == {
        "task.one",
        "task.two",
    }
    assert {item["ref"] for item in second_task["evidence_refs"]} == {
        "task.one:test_report",
        "task.two:test_report",
    }


def test_legacy_builder_feedback_import_is_idempotent(tmp_path: Path) -> None:
    feedback_dir = tmp_path / "builder" / "development_sessions" / "session.demo" / "feedback"
    feedback_dir.mkdir(parents=True)
    (feedback_dir / "feedback_123.json").write_text(
        json.dumps(
            {
                "schema": "adaos.builder.development_feedback.v1",
                "feedback_id": "feedback_123",
                "session_id": "session.demo",
                "kind": "capability_gap",
                "severity": "error",
                "blocking": True,
                "summary": "The public SDK lacks a bounded operation required by this task.",
                "affected_refs": ["skill:demo_metrics_skill"],
                "constraints": ["Core source is outside the admitted scope."],
                "evidence": [{"kind": "contract", "ref": "sdk:resources", "digest": None}],
                "proposed_action": "request_capability",
                "protocol_digest": None,
                "status": "open",
                "created_at": "2026-09-03T10:00:00Z",
                "created_by": "codex",
                "digest": "sha256:" + "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    service = DevelopmentFeedbackService(state_dir=tmp_path)

    first = service.list()
    second = service.list()

    assert len(first) == len(second) == 1
    assert second[0]["occurrence_count"] == 1
    assert second[0]["source"] == "legacy_builder_session"
    assert second[0]["category"] == "missing_capability"


def test_development_feedback_api_exposes_filter_and_lifecycle(tmp_path: Path) -> None:
    service = DevelopmentFeedbackService(state_dir=tmp_path)
    app = FastAPI()
    app.include_router(feedback_api.router, prefix="/api/development-feedback")
    app.dependency_overrides[feedback_api._get_service] = lambda: service
    client = TestClient(app)
    headers = {"X-AdaOS-Token": "dev-local-token"}

    created = client.post(
        "/api/development-feedback",
        headers=headers,
        json={
            "source": "pre_codex_llm",
            "category": "insufficient_context",
            "summary": "The requested component cannot be identified from the admitted context.",
            "confidence": 0.88,
            "target_refs": ["project:demo"],
        },
    )
    assert created.status_code == 200
    feedback = created.json()["feedback"]

    listed = client.get(
        "/api/development-feedback",
        headers=headers,
        params={"source": "pre_codex_llm", "search": "admitted context"},
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    accepted = client.post(
        f"/api/development-feedback/{feedback['feedback_id']}/transition",
        headers=headers,
        json={"status": "accepted", "actor": "human:test", "expected_revision": feedback["revision"]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["feedback"]["status"] == "accepted"
