from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import typer
from typer.testing import CliRunner

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.apps.cli.commands import skill as skill_cmd


def test_skill_push_rejoins_split_message(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    skill_dir = tmp_path / "demo_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(skill_cmd, "_resolve_skill_path", lambda target: skill_dir)

    class _Mgr:
        def push(self, skill_name: str, message: str, signoff: bool = False) -> str:
            assert skill_name == "demo_skill"
            assert message == "initial commit"
            assert signoff is False
            return "rev-1"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Mgr())
    result = runner.invoke(skill_cmd.app, ["push", "demo_skill", "--message", "initial", "commit"])
    assert result.exit_code == 0, result.output
    assert "done" in result.output.lower() or "rev-1" in result.output


def test_skill_push_without_message_pushes_committed_ahead_skills(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    pushed: list[tuple[str, str, str | None]] = []
    required: list[tuple[str, ...]] = []

    class _Caps:
        def require(self, *args: str) -> None:
            required.append(tuple(args))

    class _Git:
        def push(self, root: str, remote: str = "origin", branch: str | None = None) -> None:
            pushed.append((root, remote, branch))

    monkeypatch.setattr(
        skill_cmd,
        "get_ctx",
        lambda: SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: workspace), git=_Git()),
    )
    monkeypatch.setattr(skill_cmd, "_mgr", lambda: SimpleNamespace(caps=_Caps()))
    monkeypatch.setattr(skill_cmd, "resolve_base_ref", lambda *args, **kwargs: "origin/main")
    monkeypatch.setattr(skill_cmd, "ref_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(skill_cmd, "read_path_divergence", lambda *args, **kwargs: (2, 0))
    monkeypatch.setattr(
        skill_cmd,
        "list_changed_paths",
        lambda *args, **kwargs: [
            "skills/browsers_skill/skill.yaml",
            "skills/infrastate_skill/handlers/main.py",
        ],
    )
    monkeypatch.setattr(skill_cmd, "current_branch", lambda *args, **kwargs: "main")
    monkeypatch.setattr(skill_cmd, "compute_path_status", lambda **kwargs: SimpleNamespace(dirty=False))

    result = runner.invoke(skill_cmd.app, ["push"])

    assert result.exit_code == 0, result.output
    assert pushed == [(str(workspace), "origin", "main")]
    assert required == [("core", "skills.manage", "git.write", "net.git")]
    assert "browsers_skill, infrastate_skill" in result.output


def test_skill_push_without_message_reports_when_named_skill_is_not_ahead(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    skill_dir = workspace / "skills" / "infrastate_skill"
    skill_dir.mkdir(parents=True)
    pushed: list[str] = []

    monkeypatch.setattr(skill_cmd, "_resolve_skill_path", lambda target: skill_dir)
    monkeypatch.setattr(
        skill_cmd,
        "get_ctx",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(workspace_dir=lambda: workspace),
            git=SimpleNamespace(push=lambda *args, **kwargs: pushed.append("push")),
        ),
    )
    monkeypatch.setattr(skill_cmd, "_mgr", lambda: SimpleNamespace(caps=SimpleNamespace(require=lambda *args: None)))
    monkeypatch.setattr(skill_cmd, "resolve_base_ref", lambda *args, **kwargs: "origin/main")
    monkeypatch.setattr(skill_cmd, "ref_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(skill_cmd, "read_path_divergence", lambda *args, **kwargs: (1, 0))
    monkeypatch.setattr(skill_cmd, "list_changed_paths", lambda *args, **kwargs: ["skills/browsers_skill/skill.yaml"])

    result = runner.invoke(skill_cmd.app, ["push", "infrastate_skill"])

    assert result.exit_code == 0, result.output
    assert pushed == []
    assert "infrastate_skill has no committed ahead changes" in result.output


def test_skill_push_message_requires_skill_name(monkeypatch) -> None:
    result = CliRunner().invoke(skill_cmd.app, ["push", "--message", "publish"])

    assert result.exit_code == 2
    assert "skill name is required" in result.output
