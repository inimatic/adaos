from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_ci_embeds_patch_bump_after_fast_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "bump_patch_version:" in workflow
    jobs = yaml.safe_load(workflow)["jobs"]
    assert set(jobs["bump_patch_version"]["needs"]) == {"quick_tests", "skills_tests", "builder_contracts"}
    assert "Fast SDK checks (Ubuntu)" in workflow
    assert "tests/sdk" in workflow
    assert "tests/smoke" in workflow
    assert "tests/test_tests_cli.py" in workflow
    assert "tests/test_realtime_sidecar.py" in workflow
    assert "tests/test_supervisor.py" in workflow
    assert "test_gateway_transport_snapshot_does_not_retain_live_ydoc_on_worker" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
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


def test_builder_contract_gate_has_no_paid_or_live_service_dependencies() -> None:
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    gate = jobs["builder_contracts"]
    commands = "\n".join(step.get("run", "") for step in gate["steps"])
    for name in ("test_builder_prototype_task_context", "test_builder_prototype_context_ablation",
                 "test_e2e_dollar_budget", "test_e2e_blind_ux", "test_builder_preview_sdk",
                 "test_materialization_runtime", "test_webspace_component_state"):
        assert name in commands
    assert "node --test e2e/stand/browser/prototype-usability.test.mjs" in commands
    assert "run-prototype-model-extension" not in commands
    assert "qualify-prototype-context-ablation" not in commands
    assert gate["timeout-minutes"] <= 15
