from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.skill.dependency_requirements import (
    MissingLocalDependencyError,
    resolve_skill_dependency_args,
)


def test_local_non_python_module_dependency_is_checked_without_pip_install(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    backend = repo_root / "src" / "adaos" / "integrations" / "adaos-backend"
    skill_dir.mkdir(parents=True)
    backend.mkdir(parents=True)
    (backend / "package.json").write_text('{"name":"adaos-backend"}\n', encoding="utf-8")

    args = resolve_skill_dependency_args(
        ["requests", "src/adaos/integrations/adaos-backend"],
        skill_dir=skill_dir,
        repo_root=repo_root,
    )

    assert args == ["requests"]


def test_editable_marker_is_dropped_for_non_python_module_dependency(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    backend = repo_root / "src" / "adaos" / "integrations" / "adaos-backend"
    skill_dir.mkdir(parents=True)
    backend.mkdir(parents=True)
    (backend / "package.json").write_text('{"name":"adaos-backend"}\n', encoding="utf-8")

    args = resolve_skill_dependency_args(
        ["-e", "src/adaos/integrations/adaos-backend"],
        skill_dir=skill_dir,
        repo_root=repo_root,
    )

    assert args == []


def test_local_python_dependency_is_resolved_from_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    package = repo_root / "src" / "adaos" / "integrations" / "python-module"
    skill_dir.mkdir(parents=True)
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text("[project]\nname = 'python-module'\nversion = '0.1.0'\n", encoding="utf-8")

    args = resolve_skill_dependency_args(
        ["src/adaos/integrations/python-module"],
        skill_dir=skill_dir,
        repo_root=repo_root,
    )

    assert args == [str(package.resolve())]


def test_missing_submodule_dependency_reports_init_command(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (repo_root / ".gitmodules").write_text(
        "\n".join(
            [
                '[submodule "src/adaos/integrations/adaos-backend"]',
                "\tpath = src/adaos/integrations/adaos-backend",
                "\turl = git@example.invalid:adaos-backend.git",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(MissingLocalDependencyError) as excinfo:
        resolve_skill_dependency_args(
            ["src/adaos/integrations/adaos-backend"],
            skill_dir=skill_dir,
            repo_root=repo_root,
        )

    message = str(excinfo.value)
    assert "src/adaos/integrations/adaos-backend" in message
    assert "git submodule update --init --recursive src/adaos/integrations/adaos-backend" in message
