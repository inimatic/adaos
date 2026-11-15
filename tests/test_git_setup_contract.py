"""Repository preparation remains repeatable under interruption and concurrency."""

from concurrent.futures import ThreadPoolExecutor
import subprocess

import pytest

from adaos.adapters.git import cli_git


@pytest.mark.parametrize("actual_url", ["expected", "foreign"])
def test_origin_creation_race_is_accepted_only_for_the_requested_remote(
    monkeypatch, actual_url
):
    writes = []

    def run(args, cwd=None):
        if args == ["remote"]:
            return ""
        if args[:2] == ["remote", "add"]:
            writes.append(args)
            raise cli_git.GitError("origin already exists")
        if args == ["remote", "get-url", "origin"]:
            return actual_url
        pytest.fail(f"Unexpected remote mutation: {args}")

    monkeypatch.setattr(cli_git, "_run_git", run)
    if actual_url == "expected":
        cli_git._ensure_origin_remote("synthetic", "expected")
    else:
        with pytest.raises(cli_git.GitError, match="changed during"):
            cli_git._ensure_origin_remote("synthetic", "expected")
    assert len(writes) == 1


def test_remote_read_failure_is_not_remote_absence(monkeypatch):
    calls = []

    def run(args, cwd=None):
        calls.append(args)
        if args == ["remote"]:
            return "origin"
        raise cli_git.GitError("config is temporarily unavailable")

    monkeypatch.setattr(cli_git, "_run_git", run)
    with pytest.raises(cli_git.GitError, match="temporarily unavailable"):
        cli_git._ensure_origin_remote("synthetic", "expected")
    assert calls == [["remote"], ["remote", "get-url", "origin"]]


def test_concurrent_repository_setup_and_retry_preserve_existing_files(tmp_path):
    def git(*args, cwd=tmp_path):
        result = subprocess.run(
            ["git", *map(str, args)], cwd=cwd, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    remote, seed, dest = [tmp_path / name for name in ["remote.git", "seed", "dest"]]
    git("init", "--bare", remote)
    git("init", seed)
    git("config", "user.name", "Test", cwd=seed)
    git("config", "user.email", "test@example.local", cwd=seed)
    (seed / "registry.json").write_text('{"version":2}\n', encoding="utf-8")
    git("add", "registry.json", cwd=seed)
    git("commit", "-m", "seed", cwd=seed)
    git("branch", "-M", "main", cwd=seed)
    git("remote", "add", "origin", remote, cwd=seed)
    git("push", "-u", "origin", "main", cwd=seed)
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
    dest.mkdir()
    (dest / "retained.txt").write_text("retain across retries", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [
            pool.submit(
                cli_git.CliGitClient(depth=0).ensure_repo, dest, str(remote), "main"
            )
            for _ in range(2)
        ]
        for call in calls:
            call.result(timeout=60)
    cli_git.CliGitClient(depth=0).ensure_repo(dest, str(remote), "main")
    assert git("remote", "get-url", "origin", cwd=dest) == str(remote)
    assert git("rev-parse", "HEAD", cwd=dest) == git("rev-parse", "HEAD", cwd=seed)
    assert (dest / "retained.txt").read_text(
        encoding="utf-8"
    ) == "retain across retries"
