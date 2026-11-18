from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

import pytest
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
    assert '--check-release' in workflow
    assert 'Check Core release version before qualification' in workflow


def test_static_qualification_preserves_required_pr_status_without_push_duplicates() -> None:
    data = yaml.safe_load((ROOT / ".github/workflows/static.yml").read_text(encoding="utf-8"))
    assert set(data["on"]) == {"pull_request", "workflow_dispatch"}
    assert set(data["jobs"]) == {"static"}
    assert not data["on"]["pull_request"]  # Required status cannot use workflow path filters.
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


def test_release_preparation_bumps_before_qualification_and_cannot_deliver() -> None:
    data = yaml.safe_load((ROOT / '.github/workflows/prepare-core-release.yml').read_text())
    assert set(data['on']) == {'workflow_dispatch'}
    commands = '\n'.join(s.get('run', '') for s in data['jobs']['prepare']['steps'])
    assert commands.index('--base-ref origin/main') < commands.index('git push origin') < commands.index('gh workflow run ci.yml')
    assert 'gh workflow run static.yml' in commands
    assert 'gh pr create --base main' in commands
    assert 'root-delivery' not in commands


@pytest.mark.parametrize('outcome,dispatches,exit_code', [
    ('created', 2, 0), ('maintainer_required', 0, 0), ('unavailable', 0, 1),
])
def test_release_preparation_dispatches_only_for_a_bot_created_pr(tmp_path, outcome, dispatches, exit_code):
    """Execute the workflow control flow with local command substitutes."""
    bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
    if not bash or not Path(bash).is_file():
        pytest.skip('Bash is needed to exercise the release workflow')
    data = yaml.safe_load((ROOT/'.github/workflows/prepare-core-release.yml').read_text())
    commands = '\n'.join(step.get('run', '') for step in data['jobs']['prepare']['steps'])
    trace = tmp_path/'calls.txt'
    prefix = r'''
        git() { return 0; }
        python() { echo 0.1.9999; }
        gh() {
          printf '%s\n' "$*" >> "$CALL_TRACE"
          if [[ "$1 $2" == 'pr create' ]]; then
            case "$PR_OUTCOME" in
              created) echo 'https://example.test/pull/1'; return 0 ;;
              maintainer_required) echo 'GitHub Actions is not permitted to create or approve pull requests'; return 1 ;;
              *) echo 'service unavailable'; return 1 ;;
            esac
          fi
        }
    '''
    env = {**os.environ, 'CALL_TRACE': trace.as_posix(), 'PR_OUTCOME': outcome,
           'GITHUB_RUN_ID': 'fixture', 'GITHUB_REPOSITORY': 'example/core',
           'GITHUB_STEP_SUMMARY': (tmp_path/'summary.md').as_posix()}
    result = subprocess.run([bash, '--noprofile', '--norc', '-c', prefix+'\n'+commands],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == exit_code, result.stderr
    calls = trace.read_text().splitlines()
    assert calls[0].startswith('pr create ')
    assert sum(call.startswith('workflow run ') for call in calls) == dispatches
    if dispatches:
        assert calls[1:] == ['workflow run ci.yml --ref release/core-fixture',
                             'workflow run static.yml --ref release/core-fixture']


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
