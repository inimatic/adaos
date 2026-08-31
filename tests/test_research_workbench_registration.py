from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.research.register_conceptual_case import register_case
from scripts.research.run_conceptual_phase_a import _build_request


def _case(tmp_path: Path) -> Path:
    package = tmp_path / "case"
    package.mkdir()
    (package / "brief.md").write_text("Concept brief", encoding="utf-8")
    (package / "review.json").write_text("{}", encoding="utf-8")
    (package / "workbench.json").write_text(
        json.dumps(
            {
                "schema": "adaos.research.workbench_case.v1",
                "direction_id": "fixture",
                "title": "Fixture direction",
                "description": "Conceptual fixture.",
                "tags": ["conceptual-research"],
                "task": {
                    "task_id": "fixture.phase_a",
                    "title": "Phase A",
                    "research_question": "What is the bounded concept?",
                },
                "artifacts": [
                    {
                        "path": "brief.md",
                        "group_id": "inputs",
                        "role": "author_intent",
                        "visibility_profile": "formulation_only",
                    },
                    {
                        "path": "review.json",
                        "group_id": "candidate",
                        "role": "independent_review",
                        "visibility_profile": "evaluation_only",
                    },
                ],
                "lifecycle": {
                    "status": "awaiting_human_synthesis_decision",
                    "review_verdict": "revise",
                    "phase_b_authorized": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return package


def test_register_case_creates_direction_task_and_visibility_bound_artifacts(tmp_path: Path) -> None:
    calls: list[tuple[str, dict]] = []
    created = False
    task_created = False

    def invoke(tool: str, payload: dict) -> dict:
        nonlocal created, task_created
        calls.append((tool, payload))
        if tool == "list_directions":
            return {
                "items": [
                    {
                        "direction_id": "fixture",
                        "title": "Fixture direction",
                        "status": "formulation",
                    }
                ]
                if created
                else []
            }
        if tool == "create_direction":
            created = True
            return {"ok": True}
        if tool == "get_direction":
            return {
                "agenda": {
                    "active_task_id": "fixture.task-001",
                    "tasks": [{"task_id": "fixture.task-001"}],
                }
            }
        if tool == "create_task":
            task_created = True
            return {"ok": True}
        if tool == "list_artifacts":
            return {"items": []}
        if tool == "attach_source":
            return {"artifact": {"digest": "sha256:" + "1" * 64}}
        if tool == "sync_source_bundle":
            return {"source_bundle": {"digest": "sha256:" + "2" * 64}}
        raise AssertionError(tool)

    receipt = register_case(_case(tmp_path), invoke, actor="user:test")

    assert receipt["created_direction"] is True
    assert receipt["created_task"] is True
    assert task_created is True
    assert receipt["task_ref"] == "research-task:fixture.phase_a"
    assert receipt["conceptual_lifecycle"]["review_verdict"] == "revise"
    assert receipt["builder_invoked"] is False
    attachments = [payload for tool, payload in calls if tool == "attach_source"]
    assert [item["visibility_profile"] for item in attachments] == [
        "formulation_only",
        "evaluation_only",
    ]
    assert all(Path(item["path"]).is_file() for item in attachments)
    assert not any(tool.startswith("open_builder") for tool, _payload in calls)


def test_register_case_is_idempotent_for_existing_direction_and_task(tmp_path: Path) -> None:
    calls: list[str] = []
    package = _case(tmp_path)

    def digest(name: str) -> str:
        return "sha256:" + hashlib.sha256((package / name).read_bytes()).hexdigest()

    def invoke(tool: str, payload: dict) -> dict:
        calls.append(tool)
        if tool == "list_directions":
            return {
                "items": [
                    {
                        "direction_id": "fixture",
                        "title": "Fixture direction",
                        "status": "formulation",
                    }
                ]
            }
        if tool == "get_direction":
            return {
                "agenda": {
                    "active_task_id": "fixture.phase_a",
                    "tasks": [{"task_id": "fixture.phase_a"}],
                }
            }
        if tool == "list_artifacts":
            return {
                "items": [
                    {
                        "group_id": "inputs",
                        "path": "brief.md",
                        "digest": digest("brief.md"),
                        "role": "author_intent",
                        "visibility_profile": "formulation_only",
                    },
                    {
                        "group_id": "candidate",
                        "path": "review.json",
                        "digest": digest("review.json"),
                        "role": "independent_review",
                        "visibility_profile": "evaluation_only",
                    },
                ]
            }
        if tool == "attach_source":
            return {"artifact": {"digest": "sha256:" + "1" * 64}}
        if tool == "sync_source_bundle":
            return {"source_bundle": {"digest": "sha256:" + "2" * 64}}
        raise AssertionError(tool)

    receipt = register_case(package, invoke, actor="user:test")

    assert receipt["created_direction"] is False
    assert receipt["created_task"] is False
    assert "create_direction" not in calls
    assert "create_task" not in calls
    assert "select_active_task" not in calls
    assert "attach_source" not in calls
    assert all(item["reused"] for item in receipt["artifacts"])


def test_authoring_request_uses_workbench_direction_and_task_identity(tmp_path: Path) -> None:
    package = _case(tmp_path)
    (package / "author-brief.md").write_text("Author intent", encoding="utf-8")
    (package / "scope.json").write_text("{}", encoding="utf-8")
    (package / "sources.json").write_text("[]", encoding="utf-8")

    request = _build_request(
        package,
        run_id="fixture.run.1",
        model="research-test-llm",
        now="2026-08-29T09:00:00+03:00",
    )

    assert request["direction_ref"] == "research-direction:fixture"
    assert request["task_ref"] == "research-task:fixture.phase_a"
    assert request["synthesis_id"] == "fixture.phase_a"
    assert request["materials"][0]["ref"] == "artifact:fixture.author-brief"
