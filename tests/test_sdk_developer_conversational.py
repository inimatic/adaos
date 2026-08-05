from __future__ import annotations

import json
from pathlib import Path

import yaml

from adaos.sdk.developer import conversational, projects


def test_developer_sdk_scaffolds_non_destructive_valid_package(tmp_path: Path) -> None:
    (tmp_path / "skill.yaml").write_text(
        "name: demo\nversion: 0.1.0\n",
        encoding="utf-8",
    )

    result = conversational.scaffold_package(
        tmp_path,
        kind="skill",
        locales=("en", "de"),
    )

    assert result["valid"] is True
    assert result["validation_report"]["story_reports"] == []
    assert (tmp_path / "conversational" / "tests" / "stories").is_dir()
    assert (tmp_path / "conversational" / "matchers.yaml").is_file()
    assert (tmp_path / "conversational" / "locale.de.yaml").is_file()
    manifest = yaml.safe_load((tmp_path / "conversational" / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["default_locale"] == "en"
    component = (tmp_path / "skill.yaml").read_text(encoding="utf-8")
    assert "manifest: conversational/manifest.yaml" in component

    original = (tmp_path / "conversational" / "input.yaml").read_bytes()
    try:
        conversational.scaffold_package(tmp_path, kind="skill")
    except FileExistsError:
        pass
    else:
        raise AssertionError("scaffold must not replace an existing package")
    assert (tmp_path / "conversational" / "input.yaml").read_bytes() == original


def test_developer_sdk_scaffold_binds_existing_workflow(tmp_path: Path) -> None:
    package_root = Path(__file__).resolve().parents[1] / "examples" / "conversational-workflow-skill"
    (tmp_path / "skill.yaml").write_bytes((package_root / "skill.yaml").read_bytes())
    (tmp_path / "workflow.json").write_bytes((package_root / "workflow.json").read_bytes())
    (tmp_path / "skill.yaml").write_text(
        "name: demo_workflow\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )

    result = conversational.scaffold_package(tmp_path, kind="skill")

    assert result["valid"] is True
    report = result["static_report"]
    assert report is not None
    assert report["workflow_type"] == "example.release"


def test_developer_sdk_scaffolds_and_compiles_dev_project_by_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    skills.mkdir()
    scenarios.mkdir()
    monkeypatch.setattr(projects, "_roots", lambda: (skills.resolve(), scenarios.resolve()))
    project_root = skills / "identity_demo"
    project_root.mkdir(parents=True)
    (project_root / "skill.yaml").write_text(
        "name: identity_demo\nversion: 0.1.0\n",
        encoding="utf-8",
    )

    scaffolded = conversational.scaffold_project(
        "skill",
        "identity_demo",
        locales=("en", "ru"),
    )
    compiled = conversational.compile_project("skill", "identity_demo")

    assert scaffolded["valid"] is True
    assert compiled["valid"] is True
    assert compiled["validation_report"]["metrics"]["locales"] == 2
    assert compiled["runtime_bundle"]["schema"] == "adaos.conversational.runtime_bundle.v1"
    assert compiled["runtime_bundle"]["default_locale"] == "en"
    assert compiled["runtime_bundle"]["supported_locales"] == ["en", "ru"]
    assert compiled["runtime_bundle"]["source_digest"] == compiled["validation_report"]["package_digest"]
    assert compiled["runtime_bundle"]["catalog_digest"].startswith("sha256:")
    assert (project_root / "conversational" / "locale.ru.yaml").is_file()


def test_developer_sdk_derives_scenario_dependency_operation_catalog(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    skills.mkdir()
    scenarios.mkdir()
    monkeypatch.setattr(projects, "_roots", lambda: (skills.resolve(), scenarios.resolve()))
    dependency = skills / "demo_control"
    dependency.mkdir()
    (dependency / "skill.yaml").write_text(
        "name: demo_control\nversion: 0.1.0\ntools:\n- name: ping\n  entry: handlers.main:ping\n",
        encoding="utf-8",
    )
    project_root = scenarios / "identity_scenario"
    project_root.mkdir()
    (project_root / "scenario.yaml").write_text(
        "id: identity_scenario\nversion: 0.1.0\ndepends: [demo_control]\n",
        encoding="utf-8",
    )
    conversational.scaffold_project("scenario", "identity_scenario")
    package_root = project_root / "conversational"
    input_source = yaml.safe_load((package_root / "input.yaml").read_text(encoding="utf-8"))
    input_source["intents"] = [
        {
            "id": "ping",
            "description": "Read dependency status.",
            "kind": "query",
            "affordance_id": "ping",
            "workflow": None,
            "skill_invocation": {"skill_id": "demo_control", "operation_id": "ping"},
            "example_ids": [],
            "slots": [],
        }
    ]
    affordances = yaml.safe_load(
        (package_root / "affordances.yaml").read_text(encoding="utf-8")
    )
    affordances["affordances"] = [
        {
            "id": "ping",
            "kind": "query",
            "label": "Read status",
            "description": "Read status through the declared dependency.",
            "workflow": None,
            "skill_invocation": {"skill_id": "demo_control", "operation_id": "ping"},
            "action_policy": {
                "schema": "adaos.conversation.action_policy.v1",
                "risk_class": "read",
                "side_effect": "none",
                "confirmation": "none",
            },
            "required_capabilities": [],
            "output_refs": [],
        }
    ]
    (package_root / "input.yaml").write_text(
        yaml.safe_dump(input_source, sort_keys=False),
        encoding="utf-8",
    )
    (package_root / "affordances.yaml").write_text(
        yaml.safe_dump(affordances, sort_keys=False),
        encoding="utf-8",
    )

    compiled = conversational.compile_project("scenario", "identity_scenario")

    assert compiled["valid"] is True
    assert compiled["validation_report"]["diagnostics"] == []


def test_developer_sdk_runs_canonical_conversational_pipeline(tmp_path: Path) -> None:
    (tmp_path / "skill.yaml").write_text(
        "name: demo\nversion: 0.1.0\nconversational:\n  manifest: conversational/manifest.yaml\n",
        encoding="utf-8",
    )

    result = conversational.compile_package(tmp_path, kind="skill")

    assert result["valid"] is False
    assert result["static_report"] is None
    assert result["static_markdown"] is None
    assert "conversational.manifest.missing" in {
        item["code"] for item in result["validation_report"]["diagnostics"]
    }


def test_developer_sdk_exports_human_readable_static_evidence(tmp_path: Path) -> None:
    from adaos.sdk.developer.conversational import export_package

    package_root = Path(__file__).resolve().parents[1] / "examples" / "conversational-workflow-skill"
    output_dir = tmp_path / "evidence"

    result = export_package(package_root, kind="skill", output_dir=output_dir)

    assert result["valid"] is True
    coverage = result["static_report"]["coverage"]
    assert coverage["output_kinds_covered_by_stories"] == ["repair", "result"]
    assert coverage["repair_policies_covered_by_stories"] == ["no_match"]
    assert coverage["repair_policies_missing_story_coverage"] == []
    assert coverage["risk_classes_covered_by_stories"] == ["isolated_write"]
    assert coverage["locales_covered_by_stories"] == ["en"]
    assert coverage["channels_covered_by_stories"] == ["text", "web"]
    assert set(result["artifacts"]) == {
        "conversational-validation.json",
        "workflow-static-report.json",
        "workflow-static-report.md",
    }
    validation = json.loads((output_dir / "conversational-validation.json").read_text(encoding="utf-8"))
    assert validation["valid"] is True
    markdown = (output_dir / "workflow-static-report.md").read_text(encoding="utf-8")
    assert "```mermaid" in markdown
    assert "release.submit.en.happy_path [PASS]" in markdown

    invalid_root = tmp_path / "invalid"
    invalid_root.mkdir()
    (invalid_root / "skill.yaml").write_text(
        "name: invalid\nversion: 0.1.0\nconversational:\n  manifest: conversational/manifest.yaml\n",
        encoding="utf-8",
    )
    invalid = export_package(invalid_root, kind="skill", output_dir=output_dir)
    assert invalid["valid"] is False
    assert set(invalid["artifacts"]) == {"conversational-validation.json"}
    assert not (output_dir / "workflow-static-report.json").exists()
    assert not (output_dir / "workflow-static-report.md").exists()
