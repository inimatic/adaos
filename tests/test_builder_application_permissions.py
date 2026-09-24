from __future__ import annotations

from pathlib import Path

from adaos.services.builder.application_permissions import (
    application_permissions_context,
)


def test_permission_context_declares_exact_trial_evidence_artifact(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "access_app"
    project.mkdir(parents=True)
    (project / "project.yaml").write_text(
        """
id: access_app
components:
  owned:
    - ref: scenario:access_app
permission_profile:
  schema: adaos.application.permission_profile.v1
  required:
    - id: workspace.read
      purpose: Read application records.
  optional: []
""".lstrip(),
        encoding="utf-8",
    )

    context = application_permissions_context(
        component_ref="scenario:access_app",
        requested_project_ref="project:access_app",
        dev_projects_root=projects,
        dev_skills_root=tmp_path / "skills",
    )

    evidence = context["authoring_contract"]["trial_evidence_contract"]
    assert evidence["artifact_path"] == "tests/test_application_contract.py"
    assert evidence["access_artifact_path"] == "tests/test_application_contract.py"
    assert evidence["behavior_artifact_path"] == "tests/test_behavior_contract.py"
    assert "both exact package-relative" in evidence["admission"]
    assert any(
        "tests/test_application_contract.py" in requirement
        for requirement in context["authoring_requirements"]
    )
    assert any(
        "tests/test_behavior_contract.py" in requirement
        for requirement in context["authoring_requirements"]
    )


def test_requested_project_resolves_owner_without_ambiguous_global_scan(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    skills = tmp_path / "skills"
    for project_id in ("mail_client", "mail_manager"):
        project = projects / project_id
        project.mkdir(parents=True)
        (project / "project.yaml").write_text(
            f"""
id: {project_id}
components:
  owned:
    - ref: skill:gmail_provider
permission_profile:
  schema: adaos.application.permission_profile.v1
  required:
    - id: providers.google.gmail
      purpose: Use Gmail.
  optional: []
""".lstrip(),
            encoding="utf-8",
        )
    skill = skills / "gmail_provider"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: gmail_provider\ncapabilities: [providers.google.gmail]\n",
        encoding="utf-8",
    )

    context = application_permissions_context(
        component_ref="skill:gmail_provider",
        requested_project_ref="project:mail_client",
        dev_projects_root=projects,
        dev_skills_root=skills,
    )

    assert context["authority_status"] == "valid"
    assert context["project_ref"] == "project:mail_client"


def test_requested_project_authorizes_declared_shared_dependency(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    skills = tmp_path / "skills"
    for project_id in ("mail_client", "mail_manager"):
        project = projects / project_id
        project.mkdir(parents=True)
        (project / "project.yaml").write_text(
            f"""
id: {project_id}
components:
  owned:
    - ref: scenario:{project_id}
  dependencies:
    - ref: skill:gmail_provider
      version: ==1.0.0
      lifecycle: shared
      relations: [realizes, uses]
permission_profile:
  schema: adaos.application.permission_profile.v1
  required:
    - id: providers.google.gmail
      purpose: Use an explicitly attached Gmail account.
  optional: []
""".lstrip(),
            encoding="utf-8",
        )
    skill = skills / "gmail_provider"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: gmail_provider\ncapabilities: [providers.google.gmail]\n",
        encoding="utf-8",
    )

    context = application_permissions_context(
        component_ref="skill:gmail_provider",
        requested_project_ref="project:mail_manager",
        dev_projects_root=projects,
        dev_skills_root=skills,
    )

    assert context["authority_status"] == "valid"
    assert context["project_ref"] == "project:mail_manager"
    assert context["declared"] == ["providers.google.gmail"]
