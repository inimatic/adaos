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


def test_managed_clone_keeps_long_catalog_paths_readable(tmp_path):
    origin=tmp_path/'origin'
    origin.mkdir()
    subprocess.run(['git','init','-b','main'],cwd=origin,check=True,capture_output=True)
    subprocess.run(['git','config','core.longpaths','true'],cwd=origin,check=True,capture_output=True)
    relative='semantic/catalog/releases/'+('long_application_'*4)+'/many-digests/'+('a'*64)+'.json'
    path=origin/relative
    path.parent.mkdir(parents=True)
    path.write_text('{}',encoding='utf-8')
    subprocess.run(['git','add','.'],cwd=origin,check=True,capture_output=True)
    subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.com','commit','-m','Catalog'],
        cwd=origin,check=True,capture_output=True)
    target=tmp_path/'registry-source-checkouts'/('b'*32)/'repo'
    client=CliGitClient()
    client.ensure_repo(target,str(origin),branch='main')
    client.sparse_add(target,'semantic')
    assert (target/relative).read_text(encoding='utf-8')=='{}'
    configured=subprocess.run(['git','config','--get','core.longpaths'],cwd=target,check=True,capture_output=True,text=True)
    assert configured.stdout.strip()=='true'


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


def test_remote_url_resolves_remote_alias(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    expected = "https://github.com/inimatic/adaos-registry.git"
    subprocess.run(
        ["git", "remote", "add", "registry", expected],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    assert CliGitClient().remote_url(str(repo), "registry") == expected
