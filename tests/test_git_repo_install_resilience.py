from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from adaos.adapters.scenarios import git_repo as scenario_repo_module
from adaos.adapters.skills import git_repo as skill_repo_module


class _Paths:
    def __init__(self, root: Path) -> None:
        self._root = root

    def workspace_dir(self) -> Path:
        return self._root

    def base_dir(self) -> Path:
        return self._root.parent

    def skills_dir(self) -> Path:
        return self._root / "skills"

    def scenarios_dir(self) -> Path:
        return self._root / "scenarios"


class _Git:
    def __init__(self, registry: dict) -> None:
        self.registry = registry
        self.pull_calls = 0

    def ensure_repo(self, *_args, **_kwargs) -> None:
        return None

    def fetch(self, *_args, **_kwargs) -> None:
        return None

    def show(self, _root, spec: str) -> str:
        assert spec == "origin/main:registry.json"
        return json.dumps(self.registry)

    def sparse_init(self, *_args, **_kwargs) -> None:
        return None

    def sparse_set(self, *_args, **_kwargs) -> None:
        return None

    def pull(self, *_args, **_kwargs) -> None:
        self.pull_calls += 1
        raise RuntimeError("local workspace has unrelated changes")


def _registry() -> dict:
    return {
        "version": 2,
        "updated_at": "2026-08-15T00:00:00+00:00",
        "skills": [
            {
                "kind": "skill",
                "id": "adaos_drive",
                "name": "adaos_drive",
                "version": "0.1.0",
                "install": {"kind": "skill", "name": "adaos_drive", "id": "adaos_drive"},
            }
        ],
        "scenarios": [
            {
                "kind": "scenario",
                "id": "adaos_drive",
                "name": "adaos_drive",
                "version": "0.1.0",
                "install": {"kind": "scenario", "name": "adaos_drive", "id": "adaos_drive"},
            }
        ],
    }


def test_scenario_install_materializes_missing_target_when_pull_is_blocked(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    git = _Git(_registry())
    materialized: list[str] = []

    def _materialize(**kwargs) -> None:
        materialized.append(str(kwargs["subpath"]))
        target = Path(kwargs["dest_root"]) / str(kwargs["subpath"])
        target.mkdir(parents=True, exist_ok=True)
        (target / "scenario.yaml").write_text(
            "id: adaos_drive\nname: AdaOS Drive\nversion: 0.1.0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(scenario_repo_module, "get_git_availability", lambda **_kwargs: SimpleNamespace(enabled=True, git_path="git"))
    monkeypatch.setattr(scenario_repo_module, "materialize_subpath_from_github_zip", _materialize)
    monkeypatch.setattr(scenario_repo_module, "publish_scenario_assets_from_content", lambda *_args, **_kwargs: None)

    repo = scenario_repo_module.GitScenarioRepository(
        paths=_Paths(workspace),
        git=git,
        url="https://github.com/inimatic/adaos-registry.git",
        branch="main",
    )
    result = repo.install("adaos_drive")

    assert result.id.value == "adaos_drive"
    assert git.pull_calls == 1
    assert materialized == ["scenarios/adaos_drive"]


def test_skill_install_materializes_missing_target_when_pull_is_blocked(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    git = _Git(_registry())
    materialized: list[str] = []

    def _materialize(**kwargs) -> None:
        materialized.append(str(kwargs["subpath"]))
        target = Path(kwargs["dest_root"]) / str(kwargs["subpath"])
        target.mkdir(parents=True, exist_ok=True)
        (target / "skill.yaml").write_text(
            "name: adaos_drive\nversion: 0.1.0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(skill_repo_module, "get_git_availability", lambda **_kwargs: SimpleNamespace(enabled=True, git_path="git"))
    monkeypatch.setattr(skill_repo_module, "materialize_subpath_from_github_zip", _materialize)
    monkeypatch.setattr(skill_repo_module, "publish_skill_assets_from_webui", lambda *_args, **_kwargs: None)

    repo = skill_repo_module.GitSkillRepository(
        paths=_Paths(workspace),
        git=git,
        monorepo_url="https://github.com/inimatic/adaos-registry.git",
        monorepo_branch="main",
    )
    result = repo.install("adaos_drive")

    assert result.id.value == "adaos_drive"
    assert git.pull_calls == 1
    assert materialized == ["skills/adaos_drive"]


def test_remote_registry_miss_falls_back_to_local_workspace_catalog(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    (workspace / "registry.json").write_text(
        json.dumps(
            {
                "version": 2,
                "skills": [
                    {
                        "kind": "skill",
                        "id": "local_experiment",
                        "name": "local_experiment",
                        "install": {"kind": "skill", "name": "local_experiment"},
                    }
                ],
                "scenarios": [
                    {
                        "kind": "scenario",
                        "id": "local_scene",
                        "name": "local_scene",
                        "install": {"kind": "scenario", "name": "local_scene"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    git = _Git(_registry())
    monkeypatch.setattr(skill_repo_module, "get_git_availability", lambda **_kwargs: SimpleNamespace(enabled=True, git_path="git"))
    monkeypatch.setattr(scenario_repo_module, "get_git_availability", lambda **_kwargs: SimpleNamespace(enabled=True, git_path="git"))

    skill_repo = skill_repo_module.GitSkillRepository(
        paths=_Paths(workspace),
        git=git,
        monorepo_url="https://github.com/inimatic/adaos-registry.git",
        monorepo_branch="main",
    )
    scenario_repo = scenario_repo_module.GitScenarioRepository(
        paths=_Paths(workspace),
        git=git,
        url="https://github.com/inimatic/adaos-registry.git",
        branch="main",
    )

    assert skill_repo.resolve_install_name("local_experiment") == "local_experiment"
    assert scenario_repo.resolve_install_name("local_scene") == "local_scene"
