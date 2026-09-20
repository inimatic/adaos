from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder.governed import builder_change_definition
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


@pytest.fixture
def service(tmp_path: Path) -> BuilderWorkflowService:
    skills = tmp_path / "skills"
    root = tmp_path / "scenarios" / "recipes"
    skills.mkdir()
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "recipes-page",
                        "layout": {"type": "stack", "responsive": {"compact": "single-column"}},
                        "widgets": [
                            {"id": "recipe-title", "type": "ui.text", "title": "Recipes"},
                            {"id": "recipe-list", "type": "ui.list", "title": "Recipe list"},
                        ],
                    }
                }
            }
        },
    }
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    revisions = root / "ui_revisions"
    revisions.mkdir()
    (revisions / "001.json").write_text("{}", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    return BuilderWorkflowService(skills, tmp_path / "scenarios", tmp_path / "state")


def _plan(service: BuilderWorkflowService, target_ref: str) -> None:
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-layout",
            "request": "Move the recipe title before the list.",
            "issues": [
                {
                    "issue_id": "layout",
                    "title": "Move the recipe title",
                    "lane": "prototype",
                    "semantic_refs": [target_ref],
                    "acceptance_criteria": ["The title is before the list."],
                }
            ],
        },
    )


def test_spatial_context_reports_structure_abi_constraints_data_and_authority(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "widget:recipe-title")
    required = [
        "target_structure",
        "abi",
        "ui_capabilities",
        "constraints",
        "data_policy",
        "execution_authority",
    ]
    packet = service.build_context_packet(
        "scenario",
        "recipes",
        required_facets=required,
        enforce_context_coverage=True,
    )

    assert packet["coverage"] == {
        "required": required,
        "present": required,
        "missing": [],
        "ambiguous": [],
        "ready": True,
    }
    target = packet["facets"]["target_structure"]["resolved"][0]
    assert target["target_ref"] == "widget:recipe-title"
    assert target["siblings"] == ["recipe-title", "recipe-list"]
    assert target["order"] == 0
    assert packet["facets"]["abi"]["definition_ref"] == "abi:webui.v1.schema.json"
    assert packet["facets"]["ui_capabilities"]["catalog_ref"] == "descriptor:ui_capability_catalog"
    assert packet["facets"]["ui_capabilities"]["status"] == "present"
    assert packet["facets"]["data_policy"]["selected_mode"] == "mock"


def test_requested_automation_context_precedes_transition_without_reusing_mock_mode(service):
    from adaos.services.skill_factory_worker import context_packet_prompt_projection

    _plan(service, "widget:recipe-title")
    prototype = service.build_context_packet("scenario", "recipes")
    automation = service.build_context_packet("scenario", "recipes", execution_phase="automation", persist=True)
    assert automation["facets"]["execution_authority"]["phase"] == "automation"
    assert automation["facets"]["execution_authority"]["observed_phase"] == "prototype"
    policy = automation["facets"]["data_policy"]
    assert policy["execution_mode"] == "implemented_resources"
    assert "selected_mode" not in policy
    assert policy["prototype_binding"]["selected_mode"] == "mock"
    assert policy["local_release_lifecycle"]["manifest_field"] == "skill.yaml:data_lifecycle"
    assert "local_release_lifecycle" not in prototype["facets"]["data_policy"]
    assert automation["digest"] != prototype["digest"]
    assert service.describe("scenario", "recipes")["active_phase"] == "prototype"
    projected = context_packet_prompt_projection(automation)
    assert projected["facets"]["data_policy"]["local_release_lifecycle"] == policy["local_release_lifecycle"]
    assert projected["facets"]["data_policy"]["execution_mode"] == "implemented_resources"
    assert projected["facets"]["execution_authority"]["observed_phase"] == "prototype"


def test_automation_context_exposes_project_permission_and_role_contract(
    service: BuilderWorkflowService,
) -> None:
    from adaos.services.skill_factory_worker import context_packet_prompt_projection

    project_root = service.dev_projects_root / "recipes_app"
    project_root.mkdir(parents=True)
    (project_root / "project.yaml").write_text(
        """schema: adaos.project.v1
kind: project
id: recipes_app
version: 0.1.0
components:
  owned:
    - ref: scenario:recipes
      role: primary
    - ref: skill:recipes_skill
      role: implementation
  dependencies: []
entrypoints:
  - id: main
    presentation: scenario:recipes
    default: true
permission_profile:
  schema: adaos.application.permission_profile.v1
  required:
    - id: storage.relational
      purpose: Store recipes owned by the application.
    - id: network.read
      purpose: Search a public recipe catalog.
  optional: []
application_roles:
  - id: viewer
    title: Viewer
    grants: [recipes.read]
    assignable_to: [owner, member, child, guest]
    default_for: {guest: viewer}
    requires_permissions: [network.read]
""",
        encoding="utf-8",
    )
    skill_root = service.dev_skills_root / "recipes_skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """name: recipes_skill
version: 0.1.0
capabilities:
  - storage.relational
  - network.read
""",
        encoding="utf-8",
    )
    _plan(service, "widget:recipe-title")

    packet = service.build_context_packet(
        "scenario",
        "recipes",
        execution_phase="automation",
        application_project_ref="project:recipes_app",
        required_facets=["application_permissions"],
        enforce_context_coverage=True,
    )

    permissions = packet["facets"]["application_permissions"]
    assert permissions["declaration_status"] == "present"
    assert permissions["statically_inferred"] == ["network.read", "storage.relational"]
    assert permissions["undeclared_inferred"] == []
    assert permissions["role_matrix"]["guest"] == ["viewer"]
    contract = permissions["authoring_contract"]
    assert contract["canonical_identifier"]["pattern"] == "^[a-z0-9][a-z0-9_.-]{0,127}$"
    assert "private phone and email" in contract["canonical_identifier"]["invalid_examples"]
    projected = context_packet_prompt_projection(packet)["facets"]["application_permissions"]
    assert projected["manifest_ref"] == "projects/recipes_app/project.yaml"
    assert projected["roles"][0]["id"] == "viewer"
    assert projected["authoring_contract"] == contract


def test_invalid_permission_profile_is_repairable_context_but_not_authority(
    service: BuilderWorkflowService,
) -> None:
    project_root = service.dev_projects_root / "recipes_app"
    project_root.mkdir(parents=True)
    (project_root / "project.yaml").write_text(
        """schema: adaos.project.v1
kind: project
id: recipes_app
version: 0.1.0
components:
  owned:
    - ref: scenario:recipes
      role: primary
    - ref: skill:recipes_skill
      role: implementation
  dependencies: []
entrypoints:
  - id: main
    presentation: scenario:recipes
    default: true
permission_profile:
  schema: adaos.application.permission_profile.v1
  required:
    - id: storage.relational
      purpose: Store recipes owned by the application.
  optional: []
  data_practices:
    collected: [recipe title and notes]
""",
        encoding="utf-8",
    )
    skill_root = service.dev_skills_root / "recipes_skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """name: recipes_skill
version: 0.1.0
capabilities: [storage.relational]
""",
        encoding="utf-8",
    )
    _plan(service, "widget:recipe-title")

    packet = service.build_context_packet(
        "scenario",
        "recipes",
        execution_phase="automation",
        application_project_ref="project:recipes_app",
        required_facets=["application_permissions"],
        enforce_context_coverage=True,
    )

    permissions = packet["facets"]["application_permissions"]
    assert packet["coverage"]["ready"] is True
    assert permissions["status"] == "present"
    assert permissions["declaration_status"] == "invalid"
    assert permissions["authority_status"] == "invalid"
    assert permissions["repair_required"] is True
    assert "canonical identifier" in permissions["diagnostics"][0]


def test_unknown_context_phase_is_rejected(service):
    _plan(service, "widget:recipe-title")
    with pytest.raises(BuilderWorkflowError, match="execution context phase"):
        service.build_context_packet("scenario", "recipes", execution_phase="publication")


def test_missing_semantic_target_fails_before_model_submission(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "widget:missing")
    with pytest.raises(BuilderWorkflowError, match="missing:target_structure"):
        service.build_context_packet(
            "scenario",
            "recipes",
            required_facets=["target_structure", "abi"],
            enforce_context_coverage=True,
        )

    report = service.build_context_packet(
        "scenario",
        "recipes",
        required_facets=["target_structure", "abi"],
        enforce_context_coverage=False,
    )
    assert report["coverage"]["ready"] is False
    assert report["coverage"]["missing"] == ["target_structure"]


def test_evidence_reference_does_not_require_spatial_target(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "dticket.01M2YYDDB0ZRFG5144FVBE54FF")

    packet = service.build_context_packet(
        "scenario",
        "recipes",
        required_facets=["abi"],
        enforce_context_coverage=True,
    )

    target = packet["facets"]["target_structure"]
    assert target["requested_refs"] == []
    assert target["status"] == "missing"
    assert packet["coverage"]["ready"] is True


def test_context_packet_digest_covers_purpose_facets_and_review_constraints(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "widget:recipe-title")
    iteration = service.build_context_packet(
        "scenario",
        "recipes",
        run_purpose="iteration",
        required_facets=["target_structure"],
    )
    experiment = service.build_context_packet(
        "scenario",
        "recipes",
        run_purpose="experiment",
        required_facets=["target_structure"],
    )
    assert iteration["run"]["purpose"] == "iteration"
    assert experiment["run"]["purpose"] == "experiment"
    assert iteration["digest"] != experiment["digest"]


def test_context_packet_carries_workflow_authoring_and_static_review(
    service: BuilderWorkflowService,
) -> None:
    root = service.dev_scenarios_root / "recipes"
    (root / "scenario.yaml").write_text(
        "id: recipes\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )
    (root / "workflow.json").write_text(
        json.dumps(builder_change_definition(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _plan(service, "widget:recipe-title")

    packet = service.build_context_packet(
        "scenario",
        "recipes",
        required_facets=["workflow_definition"],
        enforce_context_coverage=True,
    )

    workflow = packet["facets"]["workflow_definition"]
    assert workflow["status"] == "present"
    assert workflow["graph_diff"]["baseline_digest"] is None
    assert workflow["static_review"]["schema"] == "adaos.workflow.static_report.v1"
    assert workflow["static_review"]["conformance_case_count"] > 0
    assert workflow["authoring"]["context_schema"] == "adaos.workflow.authoring_context.v1"
    assert any(
        item["schema_id"] == "adaos.workflow.definition.v1"
        for item in workflow["authoring"]["abi_schemas"]
    )
    assert {
        item["contract"]["adapter_id"]
        for item in workflow["authoring"]["adapter_catalog"]
    } >= {"builder.codex.run", "builder.trial.activate", "builder.publication.publish"}
    assert workflow["authoring"]["role_policy"]["unknown_role_policy"] == "deny"
    assert workflow["authoring"]["publish_policy"]["role_policy_mismatch"] == "reject"
