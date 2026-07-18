# tests/smoke/test_git_adapter.py
import os, tempfile
import subprocess
from adaos.adapters.git.cli_git import CliGitClient, GitError


def test_git_smoke(monkeypatch, tmp_path):
    # пропустить, если git недоступен в окружении
    try:
        import subprocess

        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except Exception:
        return
    # локальный пустой репо
    g = CliGitClient()
    d = tmp_path / "repo"
    # не клонируем (нет URL), просто убедимся, что команда формируется
    try:
        g.current_commit(str(tmp_path))  # даст ошибку (не git-репо) — это нормально
    except GitError:
        pass


def test_latest_commit_for_path_returns_full_message(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    artifact = repo / "scenarios" / "demo" / "scenario.yaml"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("id: demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AdaOS Test",
            "-c",
            "user.email=test@adaos.local",
            "commit",
            "-m",
            "Add demo\n\nAdaOS-Change-Id: builder_change_test",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    info = CliGitClient().latest_commit_for_path(str(repo), "scenarios/demo")

    assert len(info["commit"]) == 40
    assert info["message"].startswith("Add demo")
    assert "AdaOS-Change-Id: builder_change_test" in info["message"]
