from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from adaos.services.builder.workflow import BuilderWorkflowService


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_builder_workflow_describes_composition_project(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    projects = tmp_path / "projects"
    state = tmp_path / "state"
    for root in (skills, scenarios, projects, state):
        root.mkdir()

    _write_yaml(
        scenarios / "root_mgmnt_ops" / "scenario.yaml",
        {
            "id": "root_mgmnt_ops",
            "title": "Root Management Ops",
            "description": "Operator console",
            "version": "0.1.0",
        },
    )
    revision_dir = scenarios / "root_mgmnt_ops" / "ui_revisions"
    revision_dir.mkdir()
    (revision_dir / "current.txt").write_text("002", encoding="utf-8")
    (revision_dir / "002.json").write_text("{}", encoding="utf-8")
    _write_yaml(
        skills / "root_mgmnt" / "skill.yaml",
        {
            "name": "root_mgmnt",
            "description": "Private root-management backend",
            "version": "0.5.6",
        },
    )
    _write_yaml(
        projects / "root_mgmnt" / "project.yaml",
        {
            "schema": "adaos.project.v1",
            "kind": "project",
            "id": "root_mgmnt",
            "version": "0.1.0",
            "profiles": ["adaos.root_mgmnt.operator.v1"],
            "components": {
                "owned": [
                    {"ref": "scenario:root_mgmnt_ops", "role": "primary"},
                    {"ref": "skill:root_mgmnt", "role": "implementation"},
                ],
                "dependencies": [],
            },
            "entrypoints": [
                {
                    "id": "ops",
                    "presentation": "scenario:root_mgmnt_ops",
                    "default": True,
                    "bindings": {"operator_scope": "root"},
                }
            ],
            "catalog": {
                "title": "Root Management",
                "description": "Private operator project",
                "categories": ["ops"],
                "tags": ["root"],
            },
            "lifecycle": {
                "uninstall": {
                    "components": "retain",
                    "runtime_data": "retain",
                    "source_artifacts": "retain",
                }
            },
        },
    )
    service = BuilderWorkflowService(
        dev_skills_root=skills,
        dev_scenarios_root=scenarios,
        dev_projects_root=projects,
        state_dir=state,
    )

    projection = service.describe("project", "root_mgmnt")

    assert projection["object_type"] == "project"
    assert projection["project"]["project_ref"] == "project:root_mgmnt"
    assert projection["project"]["identity"]["title"] == "Root Management"
    assert projection["project"]["component_refs"] == [
        "project:root_mgmnt",
        "scenario:root_mgmnt_ops",
        "skill:root_mgmnt",
    ]
    assert projection["presentation"]["scenario_id"] == "root_mgmnt_ops"
    assert projection["prototype"]["head_revision"] == "002"
    assert projection["capabilities"]["can_preview_prototype"] is True
    assert projection["workflow_inspection"]["project"]["composition"]["declared"] is True
    assert {node["ref"] for node in projection["process"]["nodes"]} >= {
        "scenario:root_mgmnt_ops",
        "skill:root_mgmnt",
    }

    schema = json.loads(Path("src/adaos/abi/builder.project.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(projection["project"])
