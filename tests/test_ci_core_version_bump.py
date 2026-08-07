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


def test_full_sdk_validation_is_sequential_and_not_on_push() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "Full SDK tests (Ubuntu sequential)" in workflow
    assert "github.event_name == 'schedule'" in workflow
    assert "inputs.full_validation" in workflow
    assert "adaos tests run --only-sdk" in workflow
    assert "ci_sdk_shard.py" not in workflow


def test_standalone_version_bump_workflow_is_not_registered_separately() -> None:
    assert not (ROOT / ".github" / "workflows" / "adaos-version-bump.yml").exists()
