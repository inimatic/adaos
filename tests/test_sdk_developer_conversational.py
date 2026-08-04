from __future__ import annotations

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
    assert "conversational.manifest.missing" in {
        item["code"] for item in result["validation_report"]["diagnostics"]
    }
