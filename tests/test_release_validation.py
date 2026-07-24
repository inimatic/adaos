from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from adaos.services.release_validation import (
    OBSERVE_CHECKS,
    ReleaseValidationService,
    SshObserveRunner,
    TestNode as ValidationNode,
    TestSuite as ValidationSuite,
    ValidationCampaign,
)


TARGET_BUILD = "b91874b6854c07fb0a038b83d31b817353c5653d"


def _node(identity_file: Path) -> ValidationNode:
    return ValidationNode(
        node_id="linux-exp-01",
        display_name="Linux experimental node",
        host="192.168.0.30",
        identity_file=str(identity_file),
    )


def _suite() -> ValidationSuite:
    return ValidationSuite(
        suite_id="adaos-observe-smoke",
        version="1.0.0",
        display_name="AdaOS observe-only smoke",
    )


def _successful_executor(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    del timeout_s
    command = argv[-1]
    if command == "true":
        stdout = ""
    elif command == "systemctl is-active adaos.service":
        stdout = "active\n"
    elif "/api/ping" in command:
        stdout = json.dumps({"ok": True, "service": "adaos-runtime", "runtime": {"slot": "B"}})
    elif "/api/supervisor/public/update-status" in command:
        stdout = json.dumps(
            {
                "ok": True,
                "runtime": {
                    "active_slot": "B",
                    "runtime_state": "ready",
                    "listener_running": True,
                    "runtime_api_ready": True,
                },
            }
        )
    else:
        stdout = "B\n" + json.dumps(
            {
                "target_version": TARGET_BUILD,
                "build_version": "0.1.572+1.b91874b",
                "git_commit": TARGET_BUILD,
            }
        )
    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


def test_observe_contract_rejects_mutating_checks(tmp_path: Path) -> None:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported_observe_checks"):
        ValidationSuite(
            suite_id="unsafe",
            version="1",
            display_name="Unsafe",
            checks=("runtime_ping", "core_update"),
        )

    with pytest.raises(ValueError, match="only_observe_profile"):
        ValidationNode(
            node_id="unsafe-node",
            display_name="Unsafe",
            host="192.168.0.30",
            identity_file=str(identity),
            allowed_profiles=("observe", "update"),
        )


def test_campaign_runs_to_passed_and_persists_redacted_snapshot(tmp_path: Path) -> None:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")
    state_path = tmp_path / "release-validation.json"
    service = ReleaseValidationService(
        state_path=state_path,
        runner=SshObserveRunner(executor=_successful_executor),
    )
    service.register_node(_node(identity))
    service.register_suite(_suite())
    service.create_campaign(
        ValidationCampaign(
            campaign_id="manual-pass-01",
            suite_id="adaos-observe-smoke",
            target_build=TARGET_BUILD,
            node_ids=("linux-exp-01",),
        )
    )

    result = service.run_campaign("manual-pass-01")

    assert result["state"] == "passed"
    assert result["result"]["passed"] == 1
    assert result["assignments"][0]["state"] == "passed"
    assert [item["check_id"] for item in result["assignments"][0]["checks"]] == list(OBSERVE_CHECKS)
    snapshot = service.snapshot()
    assert snapshot["mode"] == "observe-only"
    assert snapshot["nodes"][0]["identity_file"] == "<configured>"
    assert str(identity) not in json.dumps(snapshot)

    restored = ReleaseValidationService(state_path=state_path)
    assert restored.campaign("manual-pass-01")["state"] == "passed"


def test_version_mismatch_marks_campaign_failed(tmp_path: Path) -> None:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")
    service = ReleaseValidationService(
        state_path=tmp_path / "state.json",
        runner=SshObserveRunner(executor=_successful_executor),
    )
    service.register_node(_node(identity))
    service.register_suite(_suite())
    service.create_campaign(
        ValidationCampaign(
            campaign_id="manual-mismatch-01",
            suite_id="adaos-observe-smoke",
            target_build="different-build",
            node_ids=("linux-exp-01",),
        )
    )

    result = service.run_campaign("manual-mismatch-01")

    assert result["state"] == "failed"
    assignment = result["assignments"][0]
    assert assignment["result"]["reason"] == "version_identity_failed"
    assert assignment["checks"][-1]["detail"] == "target_build_mismatch"


def test_short_git_commit_matches_observed_full_commit(tmp_path: Path) -> None:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")
    service = ReleaseValidationService(
        state_path=tmp_path / "state.json",
        runner=SshObserveRunner(executor=_successful_executor),
    )
    service.register_node(_node(identity))
    service.register_suite(_suite())
    service.create_campaign(
        ValidationCampaign(
            campaign_id="manual-short-commit-01",
            suite_id="adaos-observe-smoke",
            target_build=TARGET_BUILD[:7],
            node_ids=("linux-exp-01",),
        )
    )

    result = service.run_campaign("manual-short-commit-01")

    assert result["state"] == "passed"
    assert result["assignments"][0]["checks"][-1]["detail"] == "target_build_observed"


def test_ssh_transport_failure_is_inconclusive_not_defective(tmp_path: Path) -> None:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")

    def unavailable(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
        del timeout_s
        return subprocess.CompletedProcess(argv, 255, stdout="", stderr="connection refused")

    service = ReleaseValidationService(
        state_path=tmp_path / "state.json",
        runner=SshObserveRunner(executor=unavailable),
    )
    service.register_node(_node(identity))
    service.register_suite(_suite())
    service.create_campaign(
        ValidationCampaign(
            campaign_id="manual-offline-01",
            suite_id="adaos-observe-smoke",
            target_build=TARGET_BUILD,
            node_ids=("linux-exp-01",),
        )
    )

    result = service.run_campaign("manual-offline-01")

    assert result["state"] == "inconclusive"
    assert result["result"]["failed"] == 0
    assert result["assignments"][0]["result"]["reason"] == "ssh_transport_error"


def test_ssh_connect_timeout_is_inconclusive_with_bounded_budget(tmp_path: Path) -> None:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")
    observed: dict[str, object] = {"calls": 0}

    def timeout(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
        observed["calls"] = int(observed["calls"]) + 1
        observed["argv"] = argv
        observed["timeout_s"] = timeout_s
        raise subprocess.TimeoutExpired(argv, timeout_s)

    service = ReleaseValidationService(
        state_path=tmp_path / "state.json",
        runner=SshObserveRunner(executor=timeout),
    )
    service.register_node(_node(identity))
    service.register_suite(_suite())
    service.create_campaign(
        ValidationCampaign(
            campaign_id="manual-timeout-01",
            suite_id="adaos-observe-smoke",
            target_build=TARGET_BUILD,
            node_ids=("linux-exp-01",),
        )
    )

    result = service.run_campaign("manual-timeout-01")

    assert result["state"] == "inconclusive"
    assert result["result"]["failed"] == 0
    assert result["result"]["timed_out"] == 0
    assert result["assignments"][0]["result"]["reason"] == "ssh_connect_transport_timed_out"
    assert observed["calls"] == 2
    assert float(observed["timeout_s"]) >= 15.0
    assert "ConnectTimeout=10" in observed["argv"]
    assert "ConnectionAttempts=2" in observed["argv"]


def test_transient_ssh_timeout_is_retried_once(tmp_path: Path) -> None:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")
    calls = 0

    def transient(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(argv, timeout_s)
        return _successful_executor(argv, timeout_s)

    service = ReleaseValidationService(
        state_path=tmp_path / "state.json",
        runner=SshObserveRunner(executor=transient),
    )
    service.register_node(_node(identity))
    service.register_suite(_suite())
    service.create_campaign(
        ValidationCampaign(
            campaign_id="manual-transient-timeout-01",
            suite_id="adaos-observe-smoke",
            target_build=TARGET_BUILD,
            node_ids=("linux-exp-01",),
        )
    )

    result = service.run_campaign("manual-transient-timeout-01")

    assert result["state"] == "passed"
    assert result["assignments"][0]["checks"][0]["transport_attempts"] == 2
