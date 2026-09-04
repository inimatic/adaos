from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from adaos.services.resources import (
    LocalCrudResourceService,
    ResourceConflict,
    ResourceWorkbenchService,
)
from adaos.services.resources.local import declaration_paths
from adaos.services.skill.validation import SkillValidationService
from adaos.services.agent_context import get_ctx


def _bundle(*, title: str = "Work items") -> dict:
    record_schema = {
        "type": "object",
        "required": ["id", "title", "status", "revision"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "status": {"enum": ["planned", "in_progress", "done"]},
            "revision": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    }
    return {
        "schema": "adaos.resource.local_crud.v1",
        "owner_ref": "skill:flowboard_skill",
        "seed_policy": "if_missing",
        "resource_definition": {
            "schema": "adaos.resource.definition.v1",
            "resource_type": "skill.flowboard_skill.work_items",
            "version": "1.0.0",
            "title": title,
            "description": "Durable work items owned by the Flowboard skill.",
            "scope": {"owner": "skill:flowboard_skill"},
            "authority": {
                "provider": "local_crud",
                "binding": "flowboard_skill",
                "writes": "optimistic",
                "source_of_truth": "local_skill_state",
            },
            "record_schema_ref": "inline:skill.flowboard_skill.work_items",
            "record_schema": record_schema,
            "query": {
                "default": "all",
                "filters": ["id", "status", "search"],
                "sort": ["title"],
                "cursor": False,
                "include": [],
            },
            "operations": [
                {"id": "list", "kind": "list", "risk": "read"},
                {"id": "show", "kind": "show", "risk": "read"},
                {"id": "create", "kind": "create", "risk": "low"},
                {"id": "update", "kind": "update", "risk": "low"},
                {"id": "delete", "kind": "delete", "risk": "medium"},
            ],
            "views": [
                {"id": "board", "kind": "board", "title": "Board"},
                {"id": "form", "kind": "form", "title": "Form"},
            ],
            "events": {
                "emits": [
                    "resource.record.created",
                    "resource.record.updated",
                    "resource.record.deleted",
                ]
            },
            "i18n": {"default_locale": "en", "locales": ["en", "ru"]},
            "access": {
                "role_fixtures": {
                    "owner": {"create": "allowed", "update": "allowed", "delete": "allowed"},
                    "member": {"create": "allowed", "update": "allowed", "delete": "allowed"},
                    "guest": {"create": "denied", "update": "denied", "delete": "denied"},
                }
            },
            "privacy": {
                "sensitivity": "workspace",
                "retention": "skill_owned",
                "external_export": "denied",
            },
            "readiness": {"states": ["ready", "empty", "validation_error"]},
        },
        "seed": [
            {"id": "one", "title": "Plan release", "status": "planned", "revision": 1},
            {"id": "two", "title": "Ship release", "status": "done", "revision": 1},
        ],
    }


def _operate(service: ResourceWorkbenchService, operation_id: str, **values) -> dict:
    return service.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "skill.flowboard_skill.work_items",
            "operation_id": operation_id,
            "actor": {"id": "owner:test", "role": "owner"},
            **values,
        }
    )


def test_resolved_manifest_without_resource_declarations_is_a_noop() -> None:
    assert declaration_paths({"resource_runtime": {}}) == []
    with pytest.raises(ValueError, match="must be an array"):
        declaration_paths({"resource_runtime": {"declarations": None}})


def test_local_resource_survives_declaration_refresh_and_supports_crud(tmp_path: Path) -> None:
    local = LocalCrudResourceService(state_dir=tmp_path)
    local.materialize(_bundle())
    workbench = ResourceWorkbenchService(state_dir=tmp_path)

    created = _operate(
        workbench,
        "create",
        payload={"title": "Review result", "status": "in_progress"},
    )["result"]["record"]
    updated = _operate(
        workbench,
        "update",
        record_id=created["id"],
        expected_revision=created["revision"],
        payload={"status": "done"},
    )["result"]["record"]

    refreshed = local.materialize(_bundle(title="Work items v2"))
    queried = workbench.query(
        {
            "schema": "adaos.resource.query.v1",
            "resource_type": "skill.flowboard_skill.work_items",
            "filters": {"status": "done"},
            "search": "review",
            "actor": {"id": "owner:test", "role": "owner"},
        }
    )

    assert refreshed["duplicate"] is False
    assert queried["definition"]["title"] == "Work items v2"
    assert queried["items"] == [updated]
    assert _operate(
        workbench,
        "delete",
        record_id=created["id"],
        expected_revision=updated["revision"],
    )["result"]["deleted"] is True


def test_local_resource_rejects_foreign_namespace_conflict_and_schema_break(tmp_path: Path) -> None:
    local = LocalCrudResourceService(state_dir=tmp_path)
    local.materialize(_bundle())

    foreign = copy.deepcopy(_bundle())
    foreign["owner_ref"] = "skill:other_skill"
    foreign["resource_definition"]["authority"]["binding"] = "other_skill"
    with pytest.raises(ValueError, match="must start"):
        local.materialize(foreign)

    incompatible = copy.deepcopy(_bundle())
    incompatible["resource_definition"]["record_schema"]["required"].append("assignee")
    incompatible["resource_definition"]["record_schema"]["properties"]["assignee"] = {
        "type": "string",
        "minLength": 1,
    }
    for item in incompatible["seed"]:
        item["assignee"] = "seed"
    with pytest.raises(ValueError, match="requires migration"):
        local.materialize(incompatible)


def test_local_resource_conflict_and_manifest_activation_contract(tmp_path: Path) -> None:
    skill_dir = tmp_path / "flowboard_skill"
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "resources").mkdir()
    (skill_dir / "handlers" / "main.py").write_text("def ping():\n    return {'ok': True}\n", encoding="utf-8")
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: flowboard_skill",
                "version: 0.1.0",
                "resource_runtime:",
                "  declarations:",
                "    - resources/work_items.resource.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    declaration = skill_dir / "resources" / "work_items.resource.json"
    declaration.write_text(json.dumps(_bundle(), ensure_ascii=False), encoding="utf-8")

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)
    assert report.ok is True, [(item.code, item.message) for item in report.issues]

    local = LocalCrudResourceService(state_dir=tmp_path / "state")
    loaded = local.load_manifest(
        "flowboard_skill",
        {"resource_runtime": {"declarations": ["resources/work_items.resource.json"]}},
        artifact_root=skill_dir,
    )
    assert loaded["resource_types"] == ["skill.flowboard_skill.work_items"]

    workbench = ResourceWorkbenchService(state_dir=tmp_path / "state")
    with pytest.raises(ResourceConflict, match="revision conflict"):
        _operate(
            workbench,
            "update",
            record_id="one",
            expected_revision=0,
            payload={"status": "done"},
        )

    declaration.unlink()
    invalid = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)
    assert "resource_runtime.declaration.missing" in {item.code for item in invalid.issues}
