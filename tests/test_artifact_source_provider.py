from __future__ import annotations

import subprocess
from pathlib import Path

from adaos.services.artifact_pipeline import LocalGitSourceProvider


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_local_git_source_provider_materializes_exact_sparse_commit(tmp_path: Path) -> None:
    repository = tmp_path / "registry"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "AdaOS Test")
    _git(repository, "config", "user.email", "adaos-test@example.invalid")
    scenario = repository / "scenarios" / "recipes"
    unrelated = repository / "skills" / "unrelated"
    scenario.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text("id: recipes\nversion: 1.0.0\n", encoding="utf-8")
    (unrelated / "skill.yaml").write_text("id: unrelated\nversion: 1.0.0\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")
    commit = _git(repository, "rev-parse", "HEAD")

    provider = LocalGitSourceProvider({"registry": repository})
    source = provider.resolve(
        "registry",
        "HEAD",
        path_scope=("scenarios/recipes/",),
    )
    context = provider.materialize(source, tmp_path / "dev-context")

    assert source.revision == commit
    assert _git(context.path, "rev-parse", "HEAD") == commit
    assert (context.path / "scenarios" / "recipes" / "scenario.yaml").is_file()
    assert not (context.path / "skills" / "unrelated" / "skill.yaml").exists()
    assert context.tree_revision == _git(repository, "rev-parse", f"{commit}^{{tree}}")

    provider.remove(source, context.path)
    assert not context.path.exists()
