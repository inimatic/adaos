from __future__ import annotations

from pathlib import Path


def test_permission_manifest_cache_tracks_content_not_timestamps(tmp_path, monkeypatch):
    from adaos.services.builder import application_permissions as module

    path = tmp_path / "project.yaml"
    module._parse_manifest_bytes.cache_clear()
    original_load = module.yaml.safe_load
    reads = []

    def parse(value):
        reads.append(value)
        return original_load(value)

    monkeypatch.setattr(module.yaml, "safe_load", parse)
    path.write_text("permissions: [workspace.read]\n", encoding="utf-8")
    first, first_digest = module._manifest(path)
    first["permissions"].append("workspace.write")
    assert module._manifest(path)[0]["permissions"] == ["workspace.read"]
    assert len(reads) == 1
    path.write_text("permissions: [workspace.none]\n", encoding="utf-8")
    second, second_digest = module._manifest(path)
    assert second["permissions"] == ["workspace.none"]
    assert second_digest != first_digest
    assert len(reads) == 2
    path.unlink()
    assert module._manifest(path) == ({}, None)


def test_large_manifest_does_not_fill_parse_cache(tmp_path):
    from adaos.services.builder import application_permissions as module

    path = tmp_path / "project.yaml"
    path.write_text("description: " + "x" * 65536, encoding="utf-8")
    module._parse_manifest_bytes.cache_clear()
    assert module._manifest(path)[0]["description"] == "x" * 65536
    assert module._parse_manifest_bytes.cache_info().currsize == 0

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
    project_contract = context["authoring_contract"]["project_manifest_contract"]
    assert project_contract["top_level_fields"] == [
        "permission_profile",
        "application_roles",
    ]
    assert project_contract["forbidden_top_level_fields"] == [
        "application_permissions"
    ]
    assert (
        project_contract["permission_profile"]["required_and_optional_item_type"]
        == "object"
    )
    assert project_contract["minimal_example"]["permission_profile"]["required"][0][
        "id"
    ] == "workspace.read"
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


def test_permission_context_rejects_unknown_approval_policy(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "mail_reader"
    project.mkdir(parents=True)
    (project / "project.yaml").write_text(
        """
id: mail_reader
components:
  owned:
    - ref: scenario:mail_reader
permission_profile:
  schema: adaos.application.permission_profile.v1
  required:
    - id: workspace.read
      purpose: Read authorized messages.
      approval_policy: on_install
  optional: []
""".lstrip(),
        encoding="utf-8",
    )

    context = application_permissions_context(
        component_ref="scenario:mail_reader",
        requested_project_ref="project:mail_reader",
        dev_projects_root=projects,
        dev_skills_root=tmp_path / "skills",
    )

    assert context["declaration_status"] == "invalid"
    assert context["authority_status"] == "invalid"
    assert context["repair_required"] is True
    assert context["authoring_contract"]["semantics"]["approval_policies"][
        "allowed"
    ] == ["ask_in_context", "explicit", "grant_on_install", "owner_only"]
    assert "unsupported approval_policy values: on_install" in context["diagnostics"][0]
