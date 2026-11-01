from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_full_regression_qualifies_development_nightly_and_main_releases() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(workflow)
    assert set(data["on"]) == {"schedule", "workflow_dispatch", "push", "pull_request"}
    assert data["on"]["push"]["branches"] == ["main"]
    assert data["on"]["pull_request"]["branches"] == ["main"]
    assert len(data["on"]["schedule"]) == 1
    jobs = data["jobs"]
    assert "bump_patch_version" not in jobs
    for name in ("quick_tests", "skills_tests", "builder_contracts"):
        assert jobs[name]["needs"] == "changes"
        assert "needs.changes.outputs.run == 'true'" in jobs[name]["if"]
    assert "Fast SDK checks (Ubuntu)" in workflow
    assert "tests/sdk" in workflow
    assert "tests/smoke" in workflow
    assert "tests/test_tests_cli.py" in workflow
    assert "tests/test_realtime_sidecar.py" in workflow
    assert "tests/test_supervisor.py" in workflow
    assert "test_gateway_transport_snapshot_does_not_retain_live_ydoc_on_worker" in workflow
    assert "git push" not in workflow
    assert "python tools/bump_adaos_patch_version.py" not in workflow


def test_development_push_runs_one_bounded_static_job() -> None:
    data = yaml.safe_load((ROOT / ".github/workflows/static.yml").read_text(encoding="utf-8"))
    assert set(data["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert set(data["jobs"]) == {"static"}
    assert data["on"]["push"]["branches"] == ["main", "dev2027"]
    job = data["jobs"]["static"]
    assert job["timeout-minutes"] <= 5
    assert any("inimatic/.github/.github/actions/static-checks@" in step.get("uses", "") for step in job["steps"])
    assert data["permissions"] == {"contents": "read"}
    assert data["concurrency"]["cancel-in-progress"] is True


def test_node_delivery_waits_for_all_main_qualification_jobs() -> None:
    data = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = data["jobs"]["deliver-nodes"]
    assert set(job["needs"]) == {"quick_tests", "skills_tests", "builder_contracts"}
    assert "github.event_name == 'push'" in job["if"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    transport = next(step for step in job["steps"] if "root-delivery@" in step.get("uses", ""))
    assert transport["with"]["revision"] == "${{ github.sha }}"
    assert transport["with"]["kind"] == "core"
    assert "ROOT_CI_DELIVERY_TARGETS_JSON" in transport["with"]["targets"]
    assert "root-delivery@" in transport["uses"]
    assert data["concurrency"]["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"


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
