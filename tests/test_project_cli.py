from __future__ import annotations

import json

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


def test_project_push_without_revision_checkpoints_and_builds_that_commit(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(project_cli, "_roots", lambda _root: (tmp_path, tmp_path / "state"))
    monkeypatch.setattr(
        project_cli,
        "_checkpoint_project_source",
        lambda *_args, **_kwargs: {
            "previous_version": "1.2.3",
            "version": "1.2.4",
            "revision": "release-commit",
            "skipped_occupied_versions": [],
            "pushed": False,
        },
    )

    def build(*_args, **kwargs):
        captured.update(kwargs)
        return _release()

    monkeypatch.setattr(project_cli, "_build_project_release", build)
    result = CliRunner().invoke(
        project_cli.app,
        ["push", "media_center", "--local-only", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert captured["revision"] == "release-commit"
    assert '"version": "1.2.4"' in result.output


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


def test_distribution_export_requires_snapshot_and_writes_exact_request(
    monkeypatch, tmp_path
) -> None:
    query = tmp_path / "query.json"
    query.write_text(json.dumps({"schema": "query"}), encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        project_cli,
        "_roots",
        lambda _root: (tmp_path / "workspace", tmp_path / "state"),
    )

    def export(**kwargs):
        captured.update(kwargs)
        return {
            "bundle_digest": "sha256:" + "b" * 64,
            "path": str(tmp_path / "bundle.zip"),
            "package_digests": ["sha256:" + "c" * 64],
            "portable_record_digests": ["sha256:" + "d" * 64],
        }

    monkeypatch.setattr(project_cli, "_export_resolved_distribution", export)
    result = CliRunner().invoke(
        project_cli.app,
        [
            "distribution-export",
            "gmail_cbs_cleanroom",
            "--release-digest",
            "sha256:" + "a" * 64,
            "--query",
            str(query),
            "--output",
            str(tmp_path / "bundle.zip"),
            "--registry-revision",
            "registry-commit",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["registry_revision"] == "registry-commit"
    assert captured["query"] == {"schema": "query"}
    assert "packages=1 records=1" in result.output


def test_distribution_admit_requires_out_of_band_digest(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"bundle")
    expected = "sha256:" + "a" * 64
    captured: dict[str, object] = {}

    def admit(**kwargs):
        captured.update(kwargs)
        return {
            "bundle_digest": expected,
            "receipt_digest": "sha256:" + "b" * 64,
            "package_digests": [],
            "portable_record_digests": [],
        }

    monkeypatch.setattr(project_cli, "_admit_resolved_distribution", admit)
    result = CliRunner().invoke(
        project_cli.app,
        [
            "distribution-admit",
            str(bundle),
            "--expected-digest",
            expected,
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"bundle": bundle, "expected_digest": expected}
    assert "packages=0 records=0" in result.output


def test_distribution_admit_reports_typed_bundle_failure(monkeypatch, tmp_path) -> None:
    from adaos.services.capability_binding_state.resolved_bundle import (
        ResolvedBundleError,
    )

    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"bundle")

    class Admission:
        def __init__(self, **_kwargs) -> None:
            pass

        def admit(self, _archive, *, expected_bundle_digest):
            assert expected_bundle_digest == "sha256:" + "0" * 64
            raise ResolvedBundleError(
                "bundle_digest_mismatch",
                "resolved bundle digest does not match its sealed identity",
            )

    monkeypatch.setattr(
        "adaos.services.capability_binding_state.resolved_bundle.ResolvedSemanticBundleAdmission",
        Admission,
    )
    result = CliRunner().invoke(
        project_cli.app,
        [
            "distribution-admit",
            str(bundle),
            "--expected-digest",
            "sha256:" + "0" * 64,
        ],
    )

    assert result.exit_code == 2
    assert "bundle_digest_mismatch" in result.output
    assert "Traceback" not in result.output
