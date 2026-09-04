from __future__ import annotations

from pathlib import Path

import yaml

from adaos.domain.artifact_release import ArtifactSourceRef, WorkspaceLock
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore,
    build_artifact_package,
)
from adaos.services.artifact_pipeline.project_build import (
    ProjectReleaseBuildError,
    build_workspace_project_release,
    project_source_snapshot,
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
        workspace / "scenarios" / "viewer_ui" / "scenario.yaml",
        {
            "id": "viewer",
            "name": "viewer",
            "version": "2.0.0",
            "title": "Viewer",
            "type": "desktop",
            "depends": ["worker", "web_desktop_skill"],
            "runtime": {
                "skills": {
                    "required": ["worker", "web_desktop_skill"],
                    "optional": [],
                }
            },
        },
    )
    (workspace / "scenarios" / "viewer_ui" / "webui.json").write_text(
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
    external_root = tmp_path / "installed" / "skills" / "web_desktop_skill"
    _write_yaml(
        external_root / "skill.yaml",
        {"name": "web_desktop_skill", "version": "0.2.4"},
    )
    external = build_artifact_package(
        external_root,
        kind="skill",
        source_ref=ArtifactSourceRef(
            forge="workspace-migration",
            repository="installed-workspace",
            revision="b" * 40,
            path_scope=("skills/web_desktop_skill/",),
        ),
    )
    package_store.put(external.archive_bytes, expected_digest=external.ref.digest)
    active_lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-09-02T00:00:00+00:00",
        components=(external.ref,),
    )

    first = build_workspace_project_release(
        project_dir=project_dir,
        workspace_root=workspace,
        source_ref=source,
        package_store=package_store,
        release_repository=releases,
        active_workspace_lock=active_lock,
    )
    second = build_workspace_project_release(
        project_dir=project_dir,
        workspace_root=workspace,
        source_ref=source,
        package_store=package_store,
        release_repository=releases,
        active_workspace_lock=active_lock,
    )

    assert first.plan.release.release_digest == second.plan.release.release_digest
    assert {item.key for item in first.plan.release.components} == {
        "scenario:viewer",
        "skill:worker",
    }
    assert {item.key for item in first.plan.packages} == {
        "scenario:viewer",
        "skill:worker",
        "skill:web_desktop_skill",
    }
    assert next(
        item for item in first.plan.packages if item.key == "skill:web_desktop_skill"
    ).digest == external.ref.digest
    assert all(path.is_file() for path in first.package_paths)
    stored = releases.get_release(
        "viewer", str(first.plan.release.release_digest)
    )
    assert stored == first.plan
    assert first.plan.release.composition_lock is not None


def test_workspace_project_build_rejects_empty_builder_draft(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project_dir = workspace / "projects" / "draft"
    _write_yaml(
        project_dir / "project.yaml",
        {
            "schema": "adaos.project.v1",
            "kind": "project",
            "id": "draft",
            "version": "0.1.0",
            "profiles": [],
            "components": {"owned": [], "dependencies": []},
            "entrypoints": [],
            "catalog": {
                "title": "Draft",
                "description": "",
                "categories": [],
                "tags": [],
            },
            "lifecycle": {
                "uninstall": {
                    "components": "retain",
                    "runtime_data": "retain",
                    "source_artifacts": "retain",
                }
            },
        },
    )

    try:
        build_workspace_project_release(
            project_dir=project_dir,
            workspace_root=workspace,
            source_ref=ArtifactSourceRef(
                forge="github",
                repository="example/projects",
                revision="a" * 40,
                path_scope=("projects/draft/",),
            ),
            package_store=ContentAddressedPackageStore(tmp_path / "packages"),
            release_repository=ReleaseRepository(tmp_path / "releases"),
        )
    except ProjectReleaseBuildError as exc:
        assert "primary owned component" in str(exc)
    else:
        raise AssertionError("empty Builder draft must not produce a release")


def test_project_source_snapshot_is_scoped_and_content_addressed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    skill_root = workspace / "skills" / "kanban_skill"
    _write_yaml(
        skill_root / "skill.yaml",
        {"name": "kanban_skill", "version": "0.1.0", "tools": []},
    )
    handler = skill_root / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("def list_cards():\n    return []\n", encoding="utf-8")
    unrelated = workspace / "skills" / "unrelated" / "skill.yaml"
    _write_yaml(
        unrelated,
        {"name": "unrelated", "version": "9.9.9", "tools": []},
    )
    project_root = workspace / "projects" / "kanban"
    _write_yaml(
        project_root / "project.yaml",
        {
            "schema": "adaos.project.v1",
            "kind": "project",
            "id": "kanban",
            "version": "0.1.0",
            "profiles": [],
            "components": {
                "owned": [{"ref": "skill:kanban_skill", "role": "primary"}],
                "dependencies": [],
            },
            "entrypoints": [],
            "catalog": {
                "title": "Kanban",
                "description": "",
                "categories": [],
                "tags": [],
            },
            "lifecycle": {
                "uninstall": {
                    "components": "retain",
                    "runtime_data": "retain",
                    "source_artifacts": "retain",
                }
            },
        },
    )

    first = project_source_snapshot(
        project_dir=project_root,
        workspace_root=workspace,
    )
    unrelated.write_text(unrelated.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    unchanged = project_source_snapshot(
        project_dir=project_root,
        workspace_root=workspace,
    )
    handler.write_text("def list_cards():\n    return ['changed']\n", encoding="utf-8")
    changed = project_source_snapshot(
        project_dir=project_root,
        workspace_root=workspace,
    )

    assert first["source_revision"].startswith("sha256:")
    assert first["source_revision"] == unchanged["source_revision"]
    assert changed["source_revision"] != first["source_revision"]
    assert [item["ref"] for item in first["components"]] == [
        "skill:kanban_skill"
    ]


def test_project_source_snapshot_excludes_shared_dependency_source(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    owned_root = workspace / "skills" / "kanban_skill"
    shared_root = workspace / "skills" / "shared_runtime"
    _write_yaml(
        owned_root / "skill.yaml",
        {"name": "kanban_skill", "version": "0.1.0", "tools": []},
    )
    _write_yaml(
        shared_root / "skill.yaml",
        {"name": "shared_runtime", "version": "1.0.0", "tools": []},
    )
    shared_handler = shared_root / "handlers" / "main.py"
    shared_handler.parent.mkdir(parents=True)
    shared_handler.write_text("VALUE = 1\n", encoding="utf-8")
    project_root = workspace / "projects" / "kanban"
    _write_yaml(
        project_root / "project.yaml",
        {
            "schema": "adaos.project.v1",
            "kind": "project",
            "id": "kanban",
            "version": "0.1.0",
            "profiles": [],
            "components": {
                "owned": [{"ref": "skill:kanban_skill", "role": "primary"}],
                "dependencies": [{"ref": "skill:shared_runtime"}],
            },
            "entrypoints": [],
            "catalog": {
                "title": "Kanban",
                "description": "",
                "categories": [],
                "tags": [],
            },
            "lifecycle": {
                "uninstall": {
                    "components": "retain",
                    "runtime_data": "retain",
                    "source_artifacts": "retain",
                }
            },
        },
    )

    first = project_source_snapshot(project_dir=project_root, workspace_root=workspace)
    shared_handler.write_text("VALUE = 2\n", encoding="utf-8")
    unchanged = project_source_snapshot(project_dir=project_root, workspace_root=workspace)

    assert first["source_revision"] == unchanged["source_revision"]
    assert [item["ref"] for item in first["components"]] == ["skill:kanban_skill"]


def test_workspace_project_build_locks_local_project_dependencies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_yaml(
        workspace / "skills" / "platform_skill" / "skill.yaml",
        {
            "name": "platform_skill",
            "version": "1.2.0",
            "entry": "handlers.main:run",
        },
    )
    (workspace / "skills" / "platform_skill" / "handlers").mkdir(parents=True)
    (workspace / "skills" / "platform_skill" / "handlers" / "main.py").write_text(
        "def run(**_):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    _write_yaml(
        workspace / "skills" / "dependent_skill" / "skill.yaml",
        {
            "name": "dependent_skill",
            "version": "0.1.0",
            "entry": "handlers.main:run",
        },
    )
    (workspace / "skills" / "dependent_skill" / "handlers").mkdir(parents=True)
    (workspace / "skills" / "dependent_skill" / "handlers" / "main.py").write_text(
        "def run(**_):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    base_project = {
        "schema": "adaos.project.v1",
        "kind": "project",
        "version": "0.1.0",
        "profiles": ["test.v1"],
        "entrypoints": [],
        "catalog": {
            "title": "Test",
            "description": "",
            "categories": [],
            "tags": [],
        },
        "lifecycle": {
            "uninstall": {
                "components": "retain",
                "runtime_data": "retain",
                "source_artifacts": "retain",
            }
        },
    }
    _write_yaml(
        workspace / "projects" / "platform" / "project.yaml",
        {
            **base_project,
            "id": "platform",
            "version": "1.2.0",
            "components": {
                "owned": [{"ref": "skill:platform_skill", "role": "primary"}],
                "dependencies": [],
            },
        },
    )
    _write_yaml(
        workspace / "projects" / "dependent" / "project.yaml",
        {
            **base_project,
            "id": "dependent",
            "components": {
                "owned": [{"ref": "skill:dependent_skill", "role": "primary"}],
                "dependencies": [{"ref": "project:platform", "version": "^1.0"}],
            },
        },
    )
    package_store = ContentAddressedPackageStore(tmp_path / "packages")
    releases = ReleaseRepository(tmp_path / "releases")
    source = ArtifactSourceRef(
        forge="github",
        repository="example/projects",
        revision="a" * 40,
        path_scope=("projects/test/",),
    )

    platform = build_workspace_project_release(
        project_dir=workspace / "projects" / "platform",
        workspace_root=workspace,
        source_ref=source,
        package_store=package_store,
        release_repository=releases,
    )
    dependent = build_workspace_project_release(
        project_dir=workspace / "projects" / "dependent",
        workspace_root=workspace,
        source_ref=source,
        package_store=package_store,
        release_repository=releases,
    )

    locks = dependent.plan.release.composition_lock.project_dependencies
    assert len(locks) == 1
    assert locks[0].project_ref == "project:platform"
    assert locks[0].version_spec == "^1.0"
    assert locks[0].release_digest == platform.plan.release.release_digest
