from __future__ import annotations

import json
from pathlib import Path

from adaos.sdk.developer import conversational


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
