from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from adaos.services.scenario import scaffold


def test_scenario_scaffold_reuses_shared_workspace_git_root(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    scenarios = workspace / "scenarios"
    template = tmp_path / "template"
    (workspace / ".git").mkdir(parents=True)
    template.mkdir()
    (template / "scenario.yaml").write_text("id: template\nversion: '0.1.0'\n", encoding="utf-8")
    calls: list[tuple[str, str, str | None]] = []

    class _Git:
        def ensure_repo(self, root, url, branch=None):
            calls.append(("ensure_repo", str(root), branch))

        def sparse_add(self, root, path):
            calls.append(("sparse_add", str(root), str(path)))

    class _Registry:
        def __init__(self, _sql):
            return None

        def get(self, _name):
            return None

        def register(self, _name):
            return None

    paths = SimpleNamespace(
        workspace_dir=lambda: workspace,
        scenarios_dir=lambda: scenarios,
        scenarios_workspace_dir=lambda: scenarios,
        scenario_templates_dir=tmp_path / "templates",
    )
    ctx = SimpleNamespace(
        paths=paths,
        settings=SimpleNamespace(scenarios_monorepo_url="https://example.invalid/workspace.git", scenarios_monorepo_branch="main"),
        git=_Git(),
        sql=object(),
        bus=SimpleNamespace(publish=lambda _event: None),
    )
    monkeypatch.setattr(scaffold, "get_ctx", lambda: ctx)
    monkeypatch.setattr(scaffold, "SqliteScenarioRegistry", _Registry)
    monkeypatch.delenv("ADAOS_TESTING", raising=False)

    target = scaffold.create("smoke", template=str(template), register=True, push=False)

    assert target == scenarios / "smoke"
    assert (target / "scenario.yaml").exists()
    assert not (scenarios / "scenarios").exists()
    assert calls == [("sparse_add", str(workspace.resolve()), "scenarios/smoke")]
