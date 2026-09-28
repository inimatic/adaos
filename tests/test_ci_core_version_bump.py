from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ci_embeds_patch_bump_after_fast_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "bump_patch_version:" in workflow
    assert "needs: [quick_tests, skills_tests]" in workflow
    assert "Fast SDK checks (Ubuntu)" in workflow
    assert "tests/sdk" in workflow
    assert "tests/smoke" in workflow
    assert "tests/test_realtime_sidecar.py" in workflow
    assert "tests/test_supervisor.py" in workflow
    assert "test_gateway_transport_snapshot_does_not_retain_live_ydoc_on_worker" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "github.ref == 'refs/heads/rev2026'" in workflow
    assert "chore: bump adaos version" in workflow
    assert "python tools/bump_adaos_patch_version.py" in workflow


def test_hosted_ci_keeps_only_the_short_mandatory_validation_path() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "Fast SDK checks (Ubuntu)" in workflow
    assert "Skills tests (Ubuntu)" in workflow
    assert "Full SDK tests" not in workflow
    assert "full_validation" not in workflow
    assert "include_windows" not in workflow
    assert "schedule:" not in workflow
    assert "windows_nightly:" not in workflow
    assert "adaos tests run --only-sdk" not in workflow


def test_standalone_version_bump_workflow_is_not_registered_separately() -> None:
    assert not (ROOT / ".github" / "workflows" / "adaos-version-bump.yml").exists()
