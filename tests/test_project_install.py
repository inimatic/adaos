from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from adaos.services.project_install import (
    ensure_workspace_project_materialized,
    list_workspace_projects,
    load_installed_projects,
    record_project_install,
)


def test_project_install_materializes_project_manifest_from_sparse_checkout(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[tuple[str, str]] = []

    class _Git:
        def sparse_add(self, root: str, path: str) -> None:
            calls.append((root, path))
            project = workspace / "projects" / "web_desktop"
            project.mkdir(parents=True)
            (project / "project.yaml").write_text(
                "schema: adaos.project.v1\n"
                "kind: project\n"
                "id: web_desktop\n"
                "version: 0.1.0\n"
                "profiles: []\n"
                "components:\n"
                "  owned: []\n"
                "  dependencies: []\n"
                "entrypoints: []\n"
                "catalog:\n"
                "  title: Web Desktop\n"
                "  description: ''\n"
                "  categories: []\n"
                "  tags: []\n"
                "lifecycle:\n"
                "  uninstall:\n"
                "    components: retain\n"
                "    runtime_data: retain\n"
                "    source_artifacts: retain\n",
                encoding="utf-8",
            )

    ctx = SimpleNamespace(
        paths=SimpleNamespace(workspace_dir=lambda: workspace),
        git=_Git(),
    )

    ensure_workspace_project_materialized(ctx, "web_desktop")

    assert calls == [(str(workspace.resolve()), "projects/web_desktop")]
    assert (workspace / "projects" / "web_desktop" / "project.yaml").is_file()


def test_project_install_record_preserves_project_i18n(tmp_path: Path) -> None:
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path / "state"))
    definition = {
        "id": "web_desktop",
        "version": "0.1.0",
        "catalog": {
            "title": "Web Desktop",
            "title_i18n": {
                "en": "Web Desktop",
                "ru": "\u0412\u0435\u0431-\u0440\u0430\u0431\u043e\u0447\u0438\u0439 \u0441\u0442\u043e\u043b",
            },
            "description": "Default shell.",
            "description_i18n": {
                "en": "Default shell.",
                "ru": "\u0421\u0442\u0430\u043d\u0434\u0430\u0440\u0442\u043d\u0430\u044f \u043e\u0431\u043e\u043b\u043e\u0447\u043a\u0430.",
            },
            "categories": ["system", "desktop"],
            "tags": ["default"],
        },
        "publication": {"stage": "alpha"},
        "install": {"default": True},
    }

    record = record_project_install(
        ctx,
        definition,
        component_refs=["scenario:web_desktop"],
        webspace_id="desktop",
    )
    installed = load_installed_projects(ctx)

    assert (
        record["title_i18n"]["ru"]
        == "\u0412\u0435\u0431-\u0440\u0430\u0431\u043e\u0447\u0438\u0439 \u0441\u0442\u043e\u043b"
    )
    assert (
        record["description_i18n"]["ru"]
        == "\u0421\u0442\u0430\u043d\u0434\u0430\u0440\u0442\u043d\u0430\u044f \u043e\u0431\u043e\u043b\u043e\u0447\u043a\u0430."
    )
    assert installed[0]["title_i18n"] == record["title_i18n"]
    assert installed[0]["description_i18n"] == record["description_i18n"]


def _project_manifest(
    project_id: str,
    component_ref: str,
    *,
    visibility: str = "listed",
) -> dict:
    return {
        "schema": "adaos.project.v1",
        "kind": "project",
        "id": project_id,
        "version": "0.1.0",
        "profiles": [],
        "components": {
            "owned": [{"ref": component_ref, "role": "primary"}],
            "dependencies": [],
        },
        "entrypoints": [],
        "catalog": {
            "title": project_id.replace("_", " ").title(),
            "description": "",
            "categories": [],
            "tags": [],
        },
        "publication": {"visibility": visibility},
        "install": {"default": False, "features": []},
        "lifecycle": {
            "uninstall": {
                "components": "retain",
                "runtime_data": "retain",
                "source_artifacts": "retain",
            }
        },
    }


def _write_workspace_project(workspace: Path, manifest: dict) -> None:
    project = workspace / "projects" / manifest["id"]
    project.mkdir(parents=True, exist_ok=True)
    import yaml

    (project / "project.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def test_list_workspace_projects_uses_registry_projection_without_reparse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    _write_workspace_project(workspace, _project_manifest("alpha", "scenario:alpha"))
    _write_workspace_project(
        workspace,
        _project_manifest("hidden", "scenario:hidden", visibility="hidden"),
    )

    first = list_workspace_projects(workspace)

    from adaos.services import project_install

    def explode(*_args, **_kwargs):
        raise AssertionError("hot workspace project list reparsed project.yaml")

    monkeypatch.setattr(project_install, "_parse_workspace_project", explode)

    second = list_workspace_projects(workspace)
    including_hidden = list_workspace_projects(workspace, include_hidden=True)

    assert [item["id"] for item in first] == ["alpha"]
    assert [item["id"] for item in second] == ["alpha"]
    assert [item["id"] for item in including_hidden] == ["alpha", "hidden"]
    assert all("source_kind" not in item for item in second)
