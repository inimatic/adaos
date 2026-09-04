from __future__ import annotations

import copy

import pytest

from adaos.services.builder.prototype_acceptance import (
    admit_prototype_acceptance,
    build_prototype_acceptance,
)
from adaos.services.builder.workflow import BuilderWorkflowError


def _webui() -> dict:
    return {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "kanban",
                        "layout": {
                            "type": "single",
                            "pattern": "stack",
                            "areas": [{"id": "main", "role": "main"}],
                        },
                        "widgets": [
                            {
                                "id": "tasks",
                                "type": "collection.board",
                                "area": "main",
                                "inputs": {
                                    "lanes": [
                                        {"id": "planned", "label": "Planned"},
                                        {"id": "doing", "label": "In progress"},
                                        {"id": "done", "label": "Done"},
                                    ],
                                    "laneKey": "status",
                                    "titleKey": "title",
                                    "dragDrop": True,
                                },
                                "dataSource": {
                                    "kind": "resourceQuery",
                                    "resourceType": "prototype.kanban_card",
                                    "queryId": "normal",
                                },
                                "actions": [
                                    {
                                        "on": "move",
                                        "type": "resourceOperation",
                                        "target": "prototype.kanban_card",
                                        "params": {"operation_id": "update"},
                                    },
                                    {
                                        "on": "create",
                                        "type": "resourceOperation",
                                        "target": "prototype.kanban_card",
                                        "params": {"operation_id": "create"},
                                    },
                                    {
                                        "on": "delete",
                                        "type": "resourceOperation",
                                        "target": "prototype.kanban_card",
                                        "params": {"operation_id": "delete"},
                                    },
                                ],
                            }
                        ],
                    }
                }
            }
        },
    }


def _behavior_checks() -> list[dict]:
    return [
        {"id": identifier, "status": "passed", "evidence_refs": [f"trace:{identifier}"]}
        for identifier in (
            "render.ready",
            "board.lanes",
            "board.select",
            "board.move",
            "board.move.alternative",
            "resource.query",
            "resource.filter",
            "resource.create",
            "resource.update",
            "resource.delete",
        )
    ]


def _visual_checks() -> list[dict]:
    return [
        {
            "breakpoint": "compact",
            "viewport": {"width": 390, "height": 844},
            "status": "passed",
            "evidence_ref": "screenshot:.tmp/kanban-compact.png",
        },
        {
            "breakpoint": "wide",
            "viewport": {"width": 1440, "height": 900},
            "status": "passed",
            "evidence_ref": "screenshot:.tmp/kanban-wide.png",
        },
    ]


def _acceptance() -> dict:
    return build_prototype_acceptance(
        acceptance_id="prototype-acceptance-1",
        project_ref="project:kanban",
        change_id="change-kanban",
        revision="003",
        webui=_webui(),
        request=(
            "Create a Kanban board with filters and drag and drop. "
            "Allow users to create, edit, and delete cards."
        ),
        reviewer={"id": "agent:codex", "kind": "agent", "delegated_by": "user:owner"},
        behavior_checks=_behavior_checks(),
        visual_checks=_visual_checks(),
        accepted_at="2026-09-04T00:00:00+00:00",
    )


def test_build_and_admit_prototype_acceptance() -> None:
    acceptance = _acceptance()

    admitted = admit_prototype_acceptance(
        acceptance,
        expected_project_ref="project:kanban",
        expected_change_id="change-kanban",
        expected_revision="003",
        expected_webui_digest=acceptance["webui_digest"],
    )

    assert admitted == acceptance
    assert admitted["deterministic_evaluation"]["ok"] is True


def test_acceptance_requires_non_drag_move_evidence() -> None:
    checks = [item for item in _behavior_checks() if item["id"] != "board.move.alternative"]

    with pytest.raises(BuilderWorkflowError, match="board.move.alternative"):
        build_prototype_acceptance(
            acceptance_id="prototype-acceptance-1",
            project_ref="project:kanban",
            change_id="change-kanban",
            revision="003",
            webui=_webui(),
            request=(
                "Create a Kanban board with filters and drag and drop. "
                "Allow users to create, edit, and delete cards."
            ),
            reviewer={"id": "agent:codex", "kind": "agent"},
            behavior_checks=checks,
            visual_checks=_visual_checks(),
        )


def test_acceptance_digest_and_revision_are_fail_closed() -> None:
    acceptance = _acceptance()
    tampered = copy.deepcopy(acceptance)
    tampered["visual_checks"][0]["viewport"]["width"] = 400

    with pytest.raises(BuilderWorkflowError, match="digest"):
        admit_prototype_acceptance(
            tampered,
            expected_project_ref="project:kanban",
            expected_change_id="change-kanban",
            expected_revision="003",
            expected_webui_digest=acceptance["webui_digest"],
        )
    with pytest.raises(BuilderWorkflowError, match="stale"):
        admit_prototype_acceptance(
            acceptance,
            expected_project_ref="project:kanban",
            expected_change_id="change-kanban",
            expected_revision="004",
            expected_webui_digest=acceptance["webui_digest"],
        )
