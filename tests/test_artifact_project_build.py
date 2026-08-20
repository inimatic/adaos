from __future__ import annotations

from pathlib import Path

import yaml

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.project_build import (
    build_workspace_project_release,
)


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_workspace_project_build_persists_exact_dependency_closure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _write_yaml(
        workspace / "skills" / "worker" / "skill.yaml",
        {
            "name": "worker",
            "version": "1.2.3",
            "entry": "handlers.main:run",
            "dependencies": [],
            "tools": [
                {
                    "name": "run",
                    "entry": "handlers.main:run",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                }
            ],
        },
    )
    handler = workspace / "skills" / "worker" / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("def run(**_):\n    return {'ok': True}\n", encoding="utf-8")
    _write_yaml(
        workspace / "scenarios" / "viewer" / "scenario.yaml",
        {
            "id": "viewer",
            "name": "viewer",
            "version": "2.0.0",
            "title": "Viewer",
            "type": "desktop",
            "depends": ["worker"],
            "runtime": {"skills": {"required": ["worker"], "optional": []}},
        },
    )
    (workspace / "scenarios" / "viewer" / "webui.json").write_text(
        '{"schema":"adaos.webui.v1","pages":[]}', encoding="utf-8"
    )
    project_dir = workspace / "projects" / "viewer"
    _write_yaml(
        project_dir / "project.yaml",
        {
            "schema": "adaos.project.v1",
            "kind": "project",
            "id": "viewer",
            "version": "2.0.0",
            "profiles": ["viewer.v1"],
            "components": {
                "owned": [
                    {
                        "ref": "scenario:viewer",
                        "role": "primary",
                        "exposure": "application",
                        "lifecycle": "bound",
                        "relations": ["presents"],
                    },
                    {
                        "ref": "skill:worker",
                        "role": "implementation",
                        "exposure": "project_only",
                        "lifecycle": "bound",
                        "relations": ["realizes"],
                    },
                ],
                "dependencies": [],
            },
            "entrypoints": [
                {
                    "id": "main",
                    "presentation": "scenario:viewer",
                    "default": True,
                    "bindings": {},
                }
            ],
            "catalog": {
                "title": "Viewer",
                "description": "Test viewer",
                "categories": ["test"],
                "tags": ["viewer"],
            },
            "compatibility": {
                "required_entrypoints": ["main"],
                "required_contracts": [],
                "validation_profiles": [],
            },
            "lifecycle": {
                "uninstall": {
                    "components": "remove_if_unreferenced",
                    "runtime_data": "retain",
                    "source_artifacts": "retain",
                }
            },
        },
    )
    package_store = ContentAddressedPackageStore(tmp_path / "packages")
    releases = ReleaseRepository(tmp_path / "releases")
    source = ArtifactSourceRef(
        forge="github",
        repository="example/projects",
        revision="a" * 40,
        path_scope=("projects/viewer/",),
    )

    first = build_workspace_project_release(
        project_dir=project_dir,
        workspace_root=workspace,
        source_ref=source,
        package_store=package_store,
        release_repository=releases,
    )
    second = build_workspace_project_release(
        project_dir=project_dir,
        workspace_root=workspace,
        source_ref=source,
        package_store=package_store,
        release_repository=releases,
    )

    assert first.plan.release.release_digest == second.plan.release.release_digest
    assert {item.key for item in first.plan.release.components} == {
        "scenario:viewer",
        "skill:worker",
    }
    assert {item.key for item in first.plan.packages} == {
        "scenario:viewer",
        "skill:worker",
    }
    assert all(path.is_file() for path in first.package_paths)
    stored = releases.get_release(
        "viewer", str(first.plan.release.release_digest)
    )
    assert stored == first.plan
    assert first.plan.release.composition_lock is not None
