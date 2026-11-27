from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location(
    "publish_client_hosting", Path(__file__).resolve().parents[1] / "tools/publish_client_hosting.py")
hosting = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosting)


@pytest.fixture
def client(tmp_path):
    remote = tmp_path / "remote.git"
    source = tmp_path / "client"
    source.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--initial-branch=dev2027", str(source)], check=True, capture_output=True)
    policy = {
        "schema": "adaos.client.hosting_sources.v1", "project": "test-project",
        "sources": {
            "main": {"management": "management-main", "site-runtime": "runtime-main"},
            "dev2027": {"management": "management-dev", "site-runtime": "runtime-dev"},
        },
    }
    (source / "hosting-sources.json").write_text(json.dumps(policy), encoding="utf-8")
    hosting.git(source, "add", ".")
    hosting.git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "-m", "initial source")
    hosting.git(source, "remote", "add", "origin", str(remote))
    hosting.git(source, "push", "origin", "dev2027")
    return source


def test_publication_admits_only_the_actual_clean_remote_source_list(client):
    admitted = hosting.inspect_source(client, "dev2027")
    assert admitted["problems"] == []
    assert admitted["source_commit"] == admitted["remote_commit"]
    assert admitted["sites"]["management"] == "management-dev"
    (client / "uncommitted.txt").write_text("keep this draft", encoding="utf-8")
    assert any("uncommitted" in problem for problem in hosting.inspect_source(client, "dev2027")["problems"])
    assert (client / "uncommitted.txt").read_text(encoding="utf-8") == "keep this draft"


def test_a_relabelled_branch_cannot_publish_as_the_requested_source_list(client):
    hosting.git(client, "checkout", "-b", "feature")
    assert any("expected dev2027" in item for item in hosting.inspect_source(client, "dev2027")["problems"])


def test_unpushed_commits_cannot_be_delivered_as_the_qualified_remote_head(client):
    hosting.git(client, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "--allow-empty", "-m", "unpublished")
    assert any("source-list head" in item for item in hosting.inspect_source(client, "dev2027")["problems"])


def test_development_build_keeps_local_credentials_out_of_public_configuration(client, tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_BASE", "https://inimatic.com")
    monkeypatch.setenv("ADAOS_TOKEN", "private-local-token")
    monkeypatch.setenv("ADAOS_BASE", "http://private-local-node")
    monkeypatch.delenv("PUBLIC_ADAOS_TOKEN", raising=False)
    monkeypatch.delenv("PUBLIC_ADAOS_BASE", raising=False)
    env = hosting.build_environment(hosting.inspect_source(client, "dev2027"), tmp_path)
    assert env["PUBLIC_APP_BASE"] == "https://management-dev.web.app"
    assert env["ADAOS_SITE_CLIENT_ORIGIN"] == "https://runtime-dev.web.app"
    assert env["PUBLIC_ADAOS_TOKEN"] == env["PUBLIC_ADAOS_BASE"] == ""
    assert "ADAOS_TOKEN" not in env and "ADAOS_BASE" not in env
    assert Path(env["ADAOS_HOSTING_STATE_DIR"]).is_relative_to(tmp_path)


def test_overlapping_source_destinations_are_rejected_before_any_publication(client):
    policy_path = client / "hosting-sources.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["sources"]["dev2027"] = policy["sources"]["main"]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="overlap"):
        hosting.inspect_source(client, "dev2027")


def test_failed_preflight_cannot_start_a_build_or_delivery(client, monkeypatch):
    (client / "draft.txt").write_text("unfinished", encoding="utf-8")
    monkeypatch.setattr(hosting, "browser_path", lambda: __file__)
    monkeypatch.setattr(hosting, "check_firebase", lambda *_: None)
    monkeypatch.setattr(hosting, "run", lambda *_: pytest.fail("preflight started a build"))
    result = hosting.publish(client, "dev2027", check_only=False, deliver=True, reserve_gib=3)
    assert result["ok"] is False
    assert result["mode"] == "check"


def test_read_only_preflight_reports_storage_rejection_before_allocation(client, monkeypatch):
    monkeypatch.setattr(hosting, "browser_path", lambda: __file__)
    monkeypatch.setattr(hosting, "check_firebase", lambda *_: None)
    admission = {"ok": False, "reasons": ["workspace_free_space"], "disk_free_bytes": 10 * 1024**3,
                 "reserved_bytes": 3 * 1024**3, "scratch_disks": [],
                 "policy": {"min_free_bytes": 8 * 1024**3}}
    monkeypatch.setattr(hosting, "storage_command", lambda _: [
        sys.executable, "-c", "print(" + repr(json.dumps(admission)) + "); raise SystemExit(1)"])
    before = set(client.iterdir())
    result = hosting.publish(client, "dev2027", check_only=True, deliver=False, reserve_gib=3)
    assert result["ok"] is False
    assert result["workspace_admission"]["reasons"] == ["workspace_free_space"]
    assert set(client.iterdir()) == before


def test_build_without_delivery_needs_no_firebase_login_and_still_obeys_storage_admission(client, monkeypatch):
    monkeypatch.setattr(hosting, "browser_path", lambda: __file__)
    monkeypatch.setattr(hosting, "check_firebase", lambda *_: pytest.fail("build required Firebase login"))
    monkeypatch.setattr(hosting, "storage_command", lambda _: [sys.executable, "-c", "raise SystemExit(9)"])
    with pytest.raises(subprocess.CalledProcessError) as error:
        hosting.publish(client, "dev2027", check_only=False, deliver=False, reserve_gib=3)
    assert error.value.returncode == 9
