from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from adaos.apps.cli.commands import dev_project


def _source() -> dict[str, object]:
    return {
        "source_revision": "sha256:" + "a" * 64,
        "file_count": 3,
        "size_bytes": 42,
        "components": [{"ref": "skill:kanban_skill"}],
    }


def _release() -> dict[str, object]:
    return {
        "project_id": "kanban",
        "version": "0.1.0",
        "release_digest": "sha256:" + "b" * 64,
        "packages": [],
    }


def test_dev_project_list_uses_project_composition_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        dev_project,
        "_list_project_records",
        lambda service, profile=None, limit=500: [
            {"id": "kanban", "version": "0.1.0", "stage": "alpha"}
        ],
    )
    monkeypatch.setattr(dev_project, "_service", lambda: SimpleNamespace())

    result = CliRunner().invoke(dev_project.app, ["list", "--json"])

    assert result.exit_code == 0, result.output
    assert '"id": "kanban"' in result.output


def test_dev_project_create_can_adopt_existing_primary_component(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def create_for_existing_component(project_id, **kwargs):
        calls.append({"project_id": project_id, **kwargs})
        return {
            "ok": True,
            "project": {"id": project_id, "version": "0.1.0"},
            "created_component": False,
        }

    monkeypatch.setattr(
        dev_project.compositions,
        "create_for_existing_component",
        create_for_existing_component,
    )

    result = CliRunner().invoke(
        dev_project.app,
        [
            "create",
            "kanban",
            "--primary-kind",
            "scenario",
            "--primary-id",
            "kanban_ui",
            "--existing",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["project_id"] == "kanban"
    assert calls[0]["component_id"] == "kanban_ui"
    assert calls[0]["entrypoints"][0]["presentation"] == "scenario:kanban_ui"
    assert '"created_component": false' in result.output


def test_dev_project_push_uses_content_revision_and_can_stay_local(
    monkeypatch,
    tmp_path,
) -> None:
    service = SimpleNamespace()
    captured: dict[str, object] = {}
    monkeypatch.setattr(dev_project, "_service", lambda: service)
    monkeypatch.setattr(
        dev_project,
        "_project",
        lambda project_id: {"id": project_id, "stage": "alpha"},
    )
    monkeypatch.setattr(dev_project, "_snapshot", lambda *_: _source())
    monkeypatch.setattr(dev_project, "_dev_workspace_root", lambda *_: tmp_path)
    monkeypatch.setattr(
        dev_project,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                subnet_id="sn_test",
                node_id="node_test",
                node_settings=SimpleNamespace(id="node_test"),
            )
        ),
    )

    def build(project_id, **kwargs):
        captured.update({"project_id": project_id, **kwargs})
        return _release()

    monkeypatch.setattr(dev_project.project_cli, "_build_project_release", build)
    monkeypatch.setattr(
        dev_project.project_cli,
        "_publish_project_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local-only push must not publish")
        ),
    )

    result = CliRunner().invoke(
        dev_project.app,
        ["push", "kanban", "--bump", "none", "--local-only", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert captured["revision"] == _source()["source_revision"]
    assert captured["workspace_root"] == tmp_path
    assert captured["forge"] == "content-addressed-dev"
    assert '"publication_stage": "alpha"' in result.output
    assert '"publication"' not in result.output


def test_dev_project_push_publishes_release_by_default(monkeypatch, tmp_path) -> None:
    service = SimpleNamespace()
    published: list[dict[str, object]] = []
    monkeypatch.setattr(dev_project, "_service", lambda: service)
    monkeypatch.setattr(
        dev_project,
        "_project",
        lambda project_id: {"id": project_id, "stage": "beta"},
    )
    monkeypatch.setattr(dev_project, "_snapshot", lambda *_: _source())
    monkeypatch.setattr(dev_project, "_dev_workspace_root", lambda *_: tmp_path)
    monkeypatch.setattr(
        dev_project,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                subnet_id="sn_test",
                node_settings=SimpleNamespace(id="node_test"),
            )
        ),
    )
    monkeypatch.setattr(
        dev_project.project_cli,
        "_build_project_release",
        lambda *args, **kwargs: _release(),
    )
    monkeypatch.setattr(
        dev_project.project_cli,
        "_publish_project_release",
        lambda payload, **kwargs: published.append(dict(payload))
        or {"published": True},
    )

    result = CliRunner().invoke(
        dev_project.app,
        ["push", "kanban", "--bump", "none", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert published[0]["project_id"] == "kanban"
    assert '"published": true' in result.output


def test_dev_project_push_rolls_back_version_when_build_fails(
    monkeypatch,
    tmp_path,
) -> None:
    project_root = tmp_path / "projects" / "kanban"
    project_root.mkdir(parents=True)
    manifest = project_root / "project.yaml"
    manifest.write_text("version: 1.2.3\n", encoding="utf-8")
    original = manifest.read_bytes()
    project = {
        "id": "kanban",
        "version": "1.2.3",
        "manifest_digest": "sha256:" + "c" * 64,
        "source_path": str(project_root),
        "publication": {"stage": "alpha"},
    }
    monkeypatch.setattr(dev_project, "_service", lambda: SimpleNamespace())
    monkeypatch.setattr(dev_project, "_project", lambda *_: dict(project))

    def replace(_project_id, replacement, **_kwargs):
        assert replacement["version"] == "1.2.4"
        manifest.write_text("version: 1.2.4\n", encoding="utf-8")
        return {**project, **replacement}

    monkeypatch.setattr(dev_project.compositions, "replace", replace)
    monkeypatch.setattr(
        dev_project,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                subnet_id="sn_test",
                node_settings=SimpleNamespace(id="node_test"),
            )
        ),
    )
    monkeypatch.setattr(dev_project, "_dev_workspace_root", lambda *_: tmp_path)
    monkeypatch.setattr(
        dev_project,
        "_snapshot",
        lambda *_: (_ for _ in ()).throw(RuntimeError("broken source")),
    )

    result = CliRunner().invoke(dev_project.app, ["push", "kanban"])

    assert result.exit_code == 1
    assert manifest.read_bytes() == original
