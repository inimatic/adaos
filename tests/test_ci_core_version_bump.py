from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ci_embeds_patch_bump_after_matrix_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "bump_patch_version:" in workflow
    assert "needs: tests" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "github.ref == 'refs/heads/rev2026'" in workflow
    assert "chore: bump adaos version" in workflow
    assert "python tools/bump_adaos_patch_version.py" in workflow


def test_standalone_version_bump_workflow_is_not_registered_separately() -> None:
    assert not (ROOT / ".github" / "workflows" / "adaos-version-bump.yml").exists()
