from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from adaos.services.project_install import ensure_workspace_project_materialized


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
