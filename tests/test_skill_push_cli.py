from __future__ import annotations

import sys
import subprocess
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
        def push(
            self,
            skill_name: str,
            message: str,
            signoff: bool = False,
            bump: bool = True,
            publish_private_models: bool = False,
        ) -> str:
            assert skill_name == "demo_skill"
            assert message == "initial commit"
            assert signoff is False
            assert bump is True
            assert publish_private_models is False
            return "rev-1"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Mgr())
    monkeypatch.setattr(skill_cmd, "_warn_if_registry_tracking_refresh_failed", lambda: None)
    result = runner.invoke(skill_cmd.app, ["push", "demo_skill", "--message", "initial", "commit"])
    assert result.exit_code == 0, result.output
    assert "done" in result.output.lower() or "rev-1" in result.output


def test_skill_push_without_message_releases_changed_skills(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    pushed: list[tuple[str, str, bool]] = []

    class _Mgr:
        def push(
            self,
            skill_name: str,
            message: str,
            signoff: bool = False,
            bump: bool = True,
            publish_private_models: bool = False,
        ) -> str:
            assert publish_private_models is False
            pushed.append((skill_name, message, signoff, bump))
            return f"rev-{skill_name}"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Mgr())
    monkeypatch.setattr(
        skill_cmd,
        "_collect_skill_release_candidates",
        lambda **kwargs: {
            "base_ref": "origin/main",
            "ahead_count": 2,
            "behind_count": 0,
            "skills": [
                {"name": "browsers_skill", "reasons": ["git-ahead"]},
                {"name": "infrastate_skill", "reasons": ["registry-version"]},
            ],
        },
    )
    monkeypatch.setattr(skill_cmd, "_warn_if_registry_tracking_refresh_failed", lambda: None)

    result = runner.invoke(skill_cmd.app, ["push", "--signoff"])

    assert result.exit_code == 0, result.output
    assert pushed == [
        ("browsers_skill", "chore(browsers_skill): release workspace changes", True, True),
        ("infrastate_skill", "chore(infrastate_skill): release workspace changes", True, False),
    ]
    assert "released skill changes" in result.output
    assert "browsers_skill, infrastate_skill" in result.output


def test_skill_push_without_message_does_not_bump_registry_only_drift(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    pushed: list[tuple[str, bool]] = []

    class _Mgr:
        def push(
            self,
            skill_name: str,
            message: str,
            signoff: bool = False,
            bump: bool = True,
            publish_private_models: bool = False,
        ) -> str:
            assert publish_private_models is False
            pushed.append((skill_name, bump))
            return f"rev-{skill_name}"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Mgr())
    monkeypatch.setattr(
        skill_cmd,
        "_collect_skill_release_candidates",
        lambda **kwargs: {
            "base_ref": "origin/main",
            "ahead_count": 0,
            "behind_count": 0,
            "skills": [
                {"name": "weather_skill", "reasons": ["registry-version"]},
                {"name": "rasa_nlu_service_skill", "reasons": ["registry-missing"]},
            ],
        },
    )
    monkeypatch.setattr(skill_cmd, "_warn_if_registry_tracking_refresh_failed", lambda: None)

    result = runner.invoke(skill_cmd.app, ["push"])

    assert result.exit_code == 0, result.output
    assert pushed == [("weather_skill", False), ("rasa_nlu_service_skill", False)]


def test_skill_push_without_message_reports_when_named_skill_has_no_release_changes(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    pushed: list[str] = []

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: SimpleNamespace(push=lambda *args, **kwargs: pushed.append("push")))
    monkeypatch.setattr(
        skill_cmd,
        "_collect_skill_release_candidates",
        lambda **kwargs: {
            "base_ref": "origin/main",
            "ahead_count": 1,
            "behind_count": 0,
            "skills": [],
        },
    )

    result = runner.invoke(skill_cmd.app, ["push", "infrastate_skill"])

    assert result.exit_code == 0, result.output
    assert pushed == []
    assert "infrastate_skill has no release changes" in result.output


def test_skill_push_message_requires_skill_name(monkeypatch) -> None:
    result = CliRunner().invoke(skill_cmd.app, ["push", "--message", "publish"])

    assert result.exit_code == 2
    assert "skill name is required" in result.output


def test_skill_push_message_can_disable_version_bump(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    skill_dir = tmp_path / "demo_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    pushed: list[tuple[str, str, bool]] = []

    monkeypatch.setattr(skill_cmd, "_resolve_skill_path", lambda target: skill_dir)

    class _Mgr:
        def push(
            self,
            skill_name: str,
            message: str,
            signoff: bool = False,
            bump: bool = True,
            publish_private_models: bool = False,
        ) -> str:
            assert publish_private_models is False
            pushed.append((skill_name, message, bump))
            return "rev-1"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Mgr())
    monkeypatch.setattr(skill_cmd, "_warn_if_registry_tracking_refresh_failed", lambda: None)

    result = runner.invoke(skill_cmd.app, ["push", "demo_skill", "--message", "catch up registry", "--no-bump"])

    assert result.exit_code == 0, result.output
    assert pushed == [("demo_skill", "catch up registry", False)]


def test_skill_push_message_refreshes_registry_tracking_ref(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    skill_dir = tmp_path / "demo_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    refreshed: list[bool] = []

    monkeypatch.setattr(skill_cmd, "_resolve_skill_path", lambda target: skill_dir)

    class _Mgr:
        def push(
            self,
            skill_name: str,
            message: str,
            signoff: bool = False,
            bump: bool = True,
            publish_private_models: bool = False,
        ) -> str:
            return "rev-1"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Mgr())
    monkeypatch.setattr(skill_cmd, "_warn_if_registry_tracking_refresh_failed", lambda: refreshed.append(True))

    result = runner.invoke(skill_cmd.app, ["push", "demo_skill", "-m", "publish demo"])

    assert result.exit_code == 0, result.output
    assert refreshed == [True]


def test_skill_push_refreshes_registry_tracking_ref_after_auto_release(monkeypatch) -> None:
    runner = CliRunner()
    refreshed: list[bool] = []

    monkeypatch.setattr(
        skill_cmd,
        "_release_changed_skills",
        lambda **_kwargs: {
            "pushed": True,
            "base_ref": "registry/main",
            "ahead_count": 1,
            "behind_count": 0,
            "released": [{"name": "demo_skill", "revision": "rev-1", "reasons": ["git-ahead"]}],
        },
    )
    monkeypatch.setattr(skill_cmd, "_warn_if_registry_tracking_refresh_failed", lambda: refreshed.append(True))

    result = runner.invoke(skill_cmd.app, ["push"])

    assert result.exit_code == 0, result.output
    assert refreshed == [True]


def test_refresh_workspace_registry_tracking_ref_after_push_catches_up_stale_registry_ref(
    monkeypatch,
    tmp_path: Path,
) -> None:
    remote = tmp_path / "registry.git"
    workspace = tmp_path / "workspace"

    def _git(args: list[str], cwd: Path) -> str:
        proc = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stderr or proc.stdout
        return (proc.stdout or "").strip()

    workspace.mkdir()
    _git(["init", "--bare", str(remote)], tmp_path)
    _git(["init"], workspace)
    _git(["branch", "-M", "main"], workspace)
    _git(["config", "user.name", "AdaOS Test"], workspace)
    _git(["config", "user.email", "test@adaos.local"], workspace)
    _git(["remote", "add", "origin", str(remote)], workspace)
    _git(["remote", "add", "registry", str(remote)], workspace)

    (workspace / "registry.json").write_text("{}\n", encoding="utf-8")
    _git(["add", "registry.json"], workspace)
    _git(["commit", "-m", "initial"], workspace)
    _git(["push", "-u", "origin", "main"], workspace)
    _git(["fetch", "registry", "main"], workspace)
    old_registry_ref = _git(["rev-parse", "registry/main"], workspace)

    (workspace / "registry.json").write_text('{"changed": true}\n', encoding="utf-8")
    _git(["commit", "-am", "publish skill"], workspace)
    _git(["push", "origin", "main"], workspace)
    new_head = _git(["rev-parse", "HEAD"], workspace)
    assert old_registry_ref != new_head
    assert _git(["rev-parse", "registry/main"], workspace) == old_registry_ref

    monkeypatch.setenv("ADAOS_WORKSPACE_REGISTRY_REPO", str(remote))
    err = skill_cmd._refresh_workspace_registry_tracking_ref_after_push(workspace_root=workspace)

    assert err is None
    assert _git(["rev-parse", "registry/main"], workspace) == new_head
