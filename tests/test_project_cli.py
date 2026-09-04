from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from adaos.apps.cli.commands import project as project_cli


def _release() -> dict[str, object]:
    return {
        "project_id": "media_center",
        "version": "1.2.3",
        "release_digest": "sha256:" + "a" * 64,
        "packages": [],
    }


def test_project_push_publishes_release_by_default(monkeypatch) -> None:
    published: list[dict[str, object]] = []
    monkeypatch.setattr(project_cli, "_build_project_release", lambda *args, **kwargs: _release())
    monkeypatch.setattr(
        project_cli,
        "_publish_project_release",
        lambda payload, **kwargs: published.append(dict(payload)) or {"published": True},
    )

    result = CliRunner().invoke(
        project_cli.app,
        [
            "push",
            "media_center",
            "--revision",
            "abc123",
            "--repository",
            "registry",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert published and published[0]["project_id"] == "media_center"
    assert '"published": true' in result.output


def test_project_push_local_only_skips_remote_publication(monkeypatch) -> None:
    monkeypatch.setattr(project_cli, "_build_project_release", lambda *args, **kwargs: _release())

    def unexpected(*args, **kwargs):
        raise AssertionError("remote publication must be skipped")

    monkeypatch.setattr(project_cli, "_publish_project_release", unexpected)
    result = CliRunner().invoke(
        project_cli.app,
        [
            "push",
            "media_center",
            "--revision",
            "abc123",
            "--repository",
            "registry",
            "--local-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "media_center@1.2.3" in result.output


def test_project_release_rejects_dirty_owned_source(monkeypatch, tmp_path) -> None:
    project = tmp_path / "projects" / "media_center"
    project.mkdir(parents=True)
    (project / "project.yaml").write_text(
        """schema: adaos.project.v1
id: media_center
components:
  owned:
    - ref: scenario:media_center
""",
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(project_cli, "_git_text", lambda *_args: " M scenarios/media_center/webui.json")

    with pytest.raises(typer.BadParameter, match="uncommitted changes"):
        project_cli._assert_project_source_clean(tmp_path, "media_center")


def test_project_release_uses_active_workspace_lock(monkeypatch, tmp_path) -> None:
    lock = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(project_cli, "_roots", lambda _root: (tmp_path, tmp_path / "state"))
    monkeypatch.setattr(project_cli, "_assert_project_source_clean", lambda *_args: None)
    monkeypatch.setattr(project_cli, "load_active_workspace_lock", lambda _root: lock)

    def build(**kwargs):
        captured.update(kwargs)
        return type("Result", (), {"to_dict": lambda self: _release()})()

    monkeypatch.setattr(project_cli, "build_workspace_project_release", build)

    result = project_cli._build_project_release(
        "media_center",
        revision="a" * 40,
        repository="registry",
        forge="github",
        workspace_root=None,
        builder="test",
    )

    assert result["project_id"] == "media_center"
    assert captured["active_workspace_lock"] is lock
