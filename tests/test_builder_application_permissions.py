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
    assert "exact package-relative" in evidence["admission"]
    assert any(
        "tests/test_application_contract.py" in requirement
        for requirement in context["authoring_requirements"]
    )
