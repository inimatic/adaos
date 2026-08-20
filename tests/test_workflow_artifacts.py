from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder.governed import builder_change_definition
from adaos.services.scenario.validation import validate_scenario_path
from adaos.services.skill.validation import SkillValidationService
from adaos.services.agent_context import get_ctx
from adaos.services.workflow_artifacts import (
    WorkflowArtifactError,
    WorkflowArtifactLimits,
    canonical_workflow_digest,
    load_manifest_bound_workflow,
    validate_workflow_definition_report,
    workflow_graph_diff,
)


def _write_manifest(root: Path, name: str, *, bound: bool) -> None:
    identity = "name: demo_skill" if name == "skill.yaml" else "id: demo_scenario"
    workflow = "workflow:\n  manifest: workflow.json\n" if bound else ""
    (root / name).write_text(
        f"{identity}\nversion: 0.1.0\n{workflow}",
        encoding="utf-8",
    )


def _write_definition(root: Path) -> dict[str, object]:
    definition = builder_change_definition()
    (root / "workflow.json").write_text(
        json.dumps(definition, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return definition


def test_manifest_bound_workflow_loads_and_has_canonical_digest(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "skill.yaml", bound=True)
    definition = _write_definition(tmp_path)

    artifact = load_manifest_bound_workflow(
        tmp_path,
        manifest_name="skill.yaml",
        allow_legacy_inline=False,
    )

    assert artifact is not None
    assert artifact.compiled.workflow_type == "builder.change"
    assert artifact.definition_digest == canonical_workflow_digest(definition)
    assert artifact.raw_digest.startswith("sha256:")
    assert artifact.validation_report["valid"] is True
    assert artifact.validation_report["metrics"]["transitions"] == len(definition["transitions"])


def test_validation_report_is_structured_for_invalid_authoring_input() -> None:
    result = validate_workflow_definition_report(
        b'{"schema":"adaos.workflow.definition.v1","states":[]}'
    )

    assert result.compiled is None
    assert result.report["valid"] is False
    assert result.report["diagnostics"]
    assert all(item["path"].startswith("$") for item in result.report["diagnostics"])
    assert {item["severity"] for item in result.report["diagnostics"]} == {"error"}


def test_graph_diff_uses_semantic_identities_not_array_positions() -> None:
    before = builder_change_definition()
    after = json.loads(json.dumps(before))
    after["states"] = list(reversed(after["states"]))
    after["states"][0]["label"] = "Changed label"
    removed_command = after["commands"].pop()

    diff = workflow_graph_diff(after, before)

    assert diff["states"]["changed"] == [after["states"][0]["id"]]
    assert diff["commands"]["removed"] == [removed_command["id"]]
    assert diff["states"]["added"] == []


def test_unreferenced_workflow_is_rejected(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "skill.yaml", bound=False)
    _write_definition(tmp_path)

    with pytest.raises(WorkflowArtifactError, match="does not reference"):
        load_manifest_bound_workflow(tmp_path, manifest_name="skill.yaml")


def test_missing_and_duplicate_key_workflows_are_rejected(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "skill.yaml", bound=True)
    with pytest.raises(WorkflowArtifactError, match="references missing"):
        load_manifest_bound_workflow(tmp_path, manifest_name="skill.yaml")

    (tmp_path / "workflow.json").write_text(
        '{"schema":"adaos.workflow.definition.v1","schema":"duplicate"}',
        encoding="utf-8",
    )
    with pytest.raises(WorkflowArtifactError, match="duplicate key"):
        load_manifest_bound_workflow(tmp_path, manifest_name="skill.yaml")


def test_multiple_json_values_and_unsupported_schema_are_rejected(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "skill.yaml", bound=True)
    (tmp_path / "workflow.json").write_text("{}\n{}", encoding="utf-8")
    with pytest.raises(WorkflowArtifactError, match="invalid workflow.json"):
        load_manifest_bound_workflow(tmp_path, manifest_name="skill.yaml")

    definition = builder_change_definition()
    definition["schema"] = "adaos.workflow.definition.v999"
    (tmp_path / "workflow.json").write_text(json.dumps(definition), encoding="utf-8")
    with pytest.raises(WorkflowArtifactError, match="validation failed"):
        load_manifest_bound_workflow(tmp_path, manifest_name="skill.yaml")


def test_workflow_resource_limits_fail_before_compilation(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "skill.yaml", bound=True)
    _write_definition(tmp_path)

    with pytest.raises(WorkflowArtifactError, match="exceeds 128 bytes"):
        load_manifest_bound_workflow(
            tmp_path,
            manifest_name="skill.yaml",
            limits=WorkflowArtifactLimits(max_bytes=128),
        )
    with pytest.raises(WorkflowArtifactError, match="transitions exceeds limit 1"):
        load_manifest_bound_workflow(
            tmp_path,
            manifest_name="skill.yaml",
            limits=WorkflowArtifactLimits(max_transitions=1),
        )


def test_skill_validator_rejects_invalid_bound_workflow(tmp_path: Path) -> None:
    skill = tmp_path / "demo_skill"
    (skill / "handlers").mkdir(parents=True)
    _write_manifest(skill, "skill.yaml", bound=True)
    (skill / "handlers" / "main.py").write_text("def ping():\n    return {}\n", encoding="utf-8")

    report = SkillValidationService(get_ctx()).validate_path(skill)

    assert report.ok is False
    assert "workflow.invalid" in {issue.code for issue in report.issues}


def test_skill_validator_rejects_unregistered_workflow_adapter_before_packaging(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "demo_skill"
    (skill / "handlers").mkdir(parents=True)
    _write_manifest(skill, "skill.yaml", bound=True)
    definition = builder_change_definition()
    transition = next(
        item for item in definition["transitions"] if item["effect"].get("activity")
    )
    transition["effect"]["activity"] = "demo_skill.prepare_attempt"
    transition["effect"]["compensation"] = None
    (skill / "workflow.json").write_text(
        json.dumps(definition, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (skill / "handlers" / "main.py").write_text(
        "def prepare_attempt():\n    return {}\n",
        encoding="utf-8",
    )

    report = SkillValidationService(get_ctx()).validate_path(
        skill,
        strict=True,
        install_mode=True,
    )

    assert report.ok is False
    issue = next(
        item for item in report.issues if item.code == "workflow.adapter_binding.invalid"
    )
    assert "not registered" in issue.message


def test_scenario_validator_rejects_unbound_workflow(tmp_path: Path) -> None:
    scenario = tmp_path / "demo_scenario"
    scenario.mkdir()
    _write_manifest(scenario, "scenario.yaml", bound=False)
    _write_definition(scenario)

    report = validate_scenario_path(scenario)

    assert report.ok is False
    assert "scenario.workflow.invalid" in {issue.code for issue in report.issues}
