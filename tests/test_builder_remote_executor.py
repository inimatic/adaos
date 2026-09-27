from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.skill_factory_worker import SubprocessCodexExecutor


def _workspace(tmp_path: Path):
    return SimpleNamespace(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        dev_skills_root=tmp_path / "skills",
        dev_scenarios_root=tmp_path / "scenarios",
    )


def test_dev_builder_keeps_local_codex_executor(tmp_path, monkeypatch) -> None:
    workspace = _workspace(tmp_path)
    monkeypatch.setattr(
        "adaos.services.builder.automation.BuilderWorkspaceService.from_context",
        lambda: workspace,
    )
    monkeypatch.setattr(
        "adaos.services.agent_context.get_ctx",
        lambda: SimpleNamespace(settings=SimpleNamespace(env_type="dev")),
    )

    service = BuilderAutomationService.from_context(background=False)
    worker = service.worker_factory()

    assert isinstance(worker.executor, SubprocessCodexExecutor)


def test_prod_builder_selects_remote_executor(tmp_path, monkeypatch) -> None:
    workspace = _workspace(tmp_path)
    monkeypatch.setattr(
        "adaos.services.builder.automation.BuilderWorkspaceService.from_context",
        lambda: workspace,
    )
    monkeypatch.setattr(
        "adaos.services.agent_context.get_ctx",
        lambda: SimpleNamespace(settings=SimpleNamespace(env_type="prod")),
    )

    sentinel = object()
    package = ModuleType("adaos_automation")
    package.__path__ = []
    builder_module = ModuleType("adaos_automation.builder")

    class RemoteCodexExecutor:
        @classmethod
        def from_env(cls):
            return sentinel

    builder_module.RemoteCodexExecutor = RemoteCodexExecutor
    monkeypatch.setitem(sys.modules, "adaos_automation", package)
    monkeypatch.setitem(sys.modules, "adaos_automation.builder", builder_module)

    service = BuilderAutomationService.from_context(background=False)
    worker = service.worker_factory()

    assert worker.executor is sentinel
