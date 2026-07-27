from __future__ import annotations

import json
from pathlib import Path

from adaos.e2e.stand import StandRunner, redact_value


def _config() -> dict[str, object]:
    return {
        "environment": "stand",
        "profile": "observe",
        "firebaseClientUrl": "https://client.example.test/",
        "rootApiUrl": "https://root.example.test",
        "hubApiUrl": "https://hub.example.test",
        "subnetId": "sn_test",
        "hubId": "hub_test",
        "webspaceId": "desktop",
        "browserDeviceId": "e2e-browser-01",
        "tokenEnv": "ADAOS_E2E_TOKEN",
    }


def _healthy_response(url: str, *, authenticated: bool) -> tuple[int, dict[str, object], dict[str, str]]:
    assert authenticated is ("/api/node/" in url)
    if "/api/browser/session/authorize" in url:
        payload: dict[str, object] = {"allowed": True, "reason": None}
    elif "/materialization" in url:
        payload = {"ok": True, "materialization": {"ready": True, "missing_branches": []}}
    else:
        payload = {"ok": True}
    return 200, payload, {"Date": "Thu, 23 Jul 2026 08:00:00 GMT"}


def test_redact_value_removes_headers_tokens_jwts_and_query_secrets() -> None:
    token = "secret-control-token"
    redacted = redact_value(
        {
            "Authorization": f"Bearer {token}",
            "token": token,
            "tokenEnv": "ADAOS_E2E_TOKEN",
            "sessionToken": token,
            "url": f"https://hub.test/api?token={token}&mode=thin",
            "message": f"credential={token}",
        },
        (token,),
    )

    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["token"] == "[REDACTED]"
    assert redacted["tokenEnv"] == "ADAOS_E2E_TOKEN"
    assert redacted["sessionToken"] == "[REDACTED]"
    assert "secret-control-token" not in json.dumps(redacted)
    assert "token=[REDACTED]" in redacted["url"]


def test_observe_run_writes_passed_correlated_evidence_bundle(tmp_path: Path) -> None:
    runner = StandRunner(
        _config(),
        output_root=tmp_path / "runs",
        run_id="run-test",
        environment={"ADAOS_E2E_TOKEN": "secret-control-token"},
        repo_root=tmp_path,
    )
    runner._open_json = _healthy_response  # type: ignore[method-assign]

    manifest = runner.run()

    assert manifest["result"] == "passed"
    assert manifest["run_id"] == "run-test"
    assert {item["result"] for item in manifest["checks"]} == {"passed", "skipped"}
    assert (runner.bundle_dir / "manifest.json").is_file()
    assert (runner.bundle_dir / "checks.json").is_file()
    snapshots = sorted((runner.bundle_dir / "snapshots").glob("*.json"))
    assert len(snapshots) == 8
    assert all(json.loads(path.read_text(encoding="utf-8"))["run_id"] == "run-test" for path in snapshots)
    assert "secret-control-token" not in (runner.bundle_dir / "config.redacted.json").read_text(encoding="utf-8")


def test_materialization_invariant_failure_is_failed_not_inconclusive(tmp_path: Path) -> None:
    def _response(url: str, *, authenticated: bool) -> tuple[int, dict[str, object], dict[str, str]]:
        status, payload, headers = _healthy_response(url, authenticated=authenticated)
        if "/materialization" in url:
            payload = {
                "ok": True,
                "materialization": {"ready": False, "missing_branches": ["data.desktop"]},
            }
        return status, payload, headers

    runner = StandRunner(
        _config(),
        output_root=tmp_path / "runs",
        environment={"ADAOS_E2E_TOKEN": "token"},
        repo_root=tmp_path,
    )
    runner._open_json = _response  # type: ignore[method-assign]

    manifest = runner.run()

    assert manifest["result"] == "failed"
    materialization = next(item for item in manifest["checks"] if item["id"] == "hub.materialization")
    assert materialization["category"] == "materialization_incomplete"
    assert "data.desktop" in materialization["detail"]


def test_missing_control_token_leaves_inconclusive_evidence(tmp_path: Path) -> None:
    runner = StandRunner(_config(), output_root=tmp_path / "runs", environment={}, repo_root=tmp_path)
    runner._open_json = _healthy_response  # type: ignore[method-assign]

    manifest = runner.run()

    assert manifest["result"] == "inconclusive"
    unavailable = [item for item in manifest["checks"] if item["result"] == "inconclusive"]
    assert unavailable
    assert {item["category"] for item in unavailable} == {"credentials_invalid"}
    assert all(item["evidence"] for item in unavailable)


def test_inline_secret_is_rejected_before_any_target_request(tmp_path: Path) -> None:
    config = _config()
    config["token"] = "must-not-be-in-config"
    runner = StandRunner(config, output_root=tmp_path / "runs", repo_root=tmp_path)

    manifest = runner.run()

    assert manifest["result"] == "inconclusive"
    assert manifest["checks"][0]["id"] == "runner.config"
    assert "inline secrets are forbidden" in manifest["checks"][0]["detail"]
    redacted = (runner.bundle_dir / "config.redacted.json").read_text(encoding="utf-8")
    assert "must-not-be-in-config" not in redacted
