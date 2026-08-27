from __future__ import annotations

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
