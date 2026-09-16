from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
import yaml

from adaos.sdk.developer import compositions
from adaos.services.application_registry_projection import ApplicationRegistryProjection


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


class _StoreRecord:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


def _permission_profile() -> dict:
    return {
        "schema": "adaos.application.permission_profile.v1",
        "required": [
            {"id": "workspace.read", "purpose": "Read application data"},
            {"id": "workspace.write", "purpose": "Persist application data"},
        ],
        "optional": [
            {
                "id": "notifications.send",
                "purpose": "Notify assigned users",
                "approval_policy": "ask_in_context",
            }
        ],
        "data_practices": {
            "collected": ["user_content"],
            "sent_off_device": ["project_metadata"],
        },
        "notifications": [{"id": "assignment_updates"}],
    }


def _application_roles() -> list[dict]:
    return [
        {
            "id": "viewer",
            "title": "Viewer",
            "grants": ["app.view"],
            "assignable_to": ["owner", "member", "child", "guest"],
            "default_for": {"guest": "viewer"},
            "requires_permissions": ["workspace.read"],
        },
        {
            "id": "editor",
            "title": "Editor",
            "grants": ["app.view", "app.write"],
            "assignable_to": ["owner", "member"],
            "default_for": {},
            "requires_permissions": ["workspace.write"],
        },
    ]


class _FakeApplicationStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_applications(self) -> tuple[_StoreRecord, ...]:
        return (
            _StoreRecord(
                {
                    "schema": "adaos.application.v1",
                    "application_id": "app_demo",
                    "legacy_project_id": "demo",
                    "publisher_ref": "subnet:local",
                    "slug": "app_demo",
                    "display": {"title": "Demo App", "summary": "Installed demo"},
                    "visibility": "public",
                    "entrypoints": [
                        {"entrypoint_id": "main", "presentation_ref": "skill:demo"}
                    ],
                    "publisher": {"publisher_ref": "subnet:local"},
                    "protection": {},
                    "lifecycle": "active",
                    "revision": 1,
                }
            ),
        )

    def list_installations(self) -> tuple[_StoreRecord, ...]:
        return (
            _StoreRecord(
                {
                    "schema": "adaos.application.installation.v1",
                    "installation_id": "installation:demo",
                    "application_id": "app_demo",
                    "installed_release_digest": _DIGEST_A,
                    "component_refs": [
                        {
                            "component_ref": "skill:demo",
                            "package_digest": _DIGEST_C,
                            "lifecycle": "bound",
                        }
                    ],
                    "data_policy": "retain",
                    "status": "active",
                    "revision": 1,
                }
            ),
        )

    def list_runtime_selections(self) -> tuple[_StoreRecord, ...]:
        return (
            _StoreRecord(
                {
                    "schema": "adaos.application.runtime_selection.v1",
                    "webspace_id": "workspace",
                    "application_id": "app_demo",
                    "source": "local_trial",
                    "release_digest": _DIGEST_B,
                    "runtime_root_ref": "trial:demo",
                    "revision": 1,
                }
            ),
        )

    def get_channels(self, application_id: str) -> dict:
        assert application_id == "app_demo"
        return {
            "schema": "adaos.application.channel_set.v1",
            "application_id": application_id,
            "revision": 1,
            "channels": {"stable": _DIGEST_A, "prerelease": _DIGEST_B},
        }

    def list_releases(self, application_id: str) -> tuple[_StoreRecord, ...]:
        assert application_id == "app_demo"
        return (
            _StoreRecord(
                {
                    "schema": "adaos.application.release.v1",
                    "application_id": application_id,
                    "publisher_ref": "subnet:local",
                    "legacy_project_id": "demo",
                    "version": "1.0.0",
                    "release_digest": _DIGEST_A,
                    "project_release": {
                        "schema": "adaos.artifact.project_release.v1",
                        "project_id": "demo",
                        "version": "1.0.0",
                        "permissions": ["workspace.read", "workspace.write"],
                    },
                    "permission_profile": _permission_profile(),
                    "application_roles": _application_roles(),
                    "lifecycle": "stable",
                }
            ),
        )


def _project(project_id: str, component_ref: str = "scenario:demo") -> dict:
    return {
        "schema": "adaos.project.v1",
        "kind": "project",
        "id": project_id,
        "version": "0.1.0",
        "profiles": ["adaos.demo.v1"],
        "components": {
            "owned": [{"ref": component_ref, "role": "primary"}],
            "dependencies": [],
        },
        "entrypoints": [
            {
                "id": "main",
                "presentation": component_ref,
                "default": True,
                "bindings": {},
            }
        ],
        "catalog": {
            "title": f"{project_id} title",
            "description": "Projection smoke",
            "categories": ["demo"],
            "tags": ["registry"],
        },
        "publication": {"stage": "alpha", "visibility": "unlisted", "channel": "stable"},
        "install": {"default": False, "features": []},
        "lifecycle": {
            "uninstall": {
                "components": "retain",
                "runtime_data": "retain",
                "source_artifacts": "retain",
            }
        },
    }


def _write_project(root: Path, value: dict) -> None:
    project_root = root / value["id"]
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "project.yaml").write_text(
        yaml.safe_dump(value, sort_keys=False),
        encoding="utf-8",
    )


def _rebuild(service: ApplicationRegistryProjection, projects_root: Path) -> dict:
    return service.rebuild_development_projects(
        projects_root,
        parser=compositions._parse_project,
        schema_bytes=compositions._schema_path().read_bytes(),
    )


def test_registry_projection_rebuilds_dev_projects_and_queries_without_manifest_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "projects"
    _write_project(projects, _project("alpha", "scenario:alpha"))
    _write_project(projects, _project("beta", "scenario:beta"))
    service = ApplicationRegistryProjection(tmp_path / "state")

    result = _rebuild(service, projects)

    assert result["status"] == "completed"
    assert result["scanned"] == 2
    assert result["indexed"] == 2
    assert result["source_watermark"].startswith("sha256:")
    assert service.db_path == tmp_path / "state" / "applications" / "registry.sqlite3"

    def explode(*_args, **_kwargs):
        raise AssertionError("hot query reparsed project.yaml")

    monkeypatch.setattr(compositions, "_parse_project", explode)

    listed = service.list_development_projects(query="beta title")
    owners = service.project_for_component("scenario:alpha")

    assert [item["id"] for item in listed] == ["beta"]
    assert owners[0]["id"] == "alpha"
    assert owners[0]["primary_ref"] == "scenario:alpha"


def test_registry_projection_indexes_development_permission_profiles_and_roles(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    project = _project("alpha", "scenario:alpha")
    project["permission_profile"] = _permission_profile()
    project["application_roles"] = _application_roles()
    _write_project(projects, project)
    service = ApplicationRegistryProjection(tmp_path / "state")

    _rebuild(service, projects)

    profiles = service.application_permission_profiles("alpha", source_kind="dev_project")
    roles = service.application_roles("alpha", source_kind="dev_project")

    assert profiles[0]["flat_permissions"] == [
        "notifications.send",
        "workspace.read",
        "workspace.write",
    ]
    assert profiles[0]["permission_profile_digest"].startswith("sha256:")
    assert profiles[0]["declaration_summary"]["privacy_labels"]["sent_off_device"] is True
    assert [item["role_id"] for item in roles] == ["editor", "viewer"]
    assert roles[0]["capabilities"] == ["app.view", "app.write"]


def test_registry_projection_isolates_invalid_permission_profile_from_catalog_rebuild(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    valid = _project("valid", "scenario:valid")
    valid["permission_profile"] = _permission_profile()
    broken = _project("broken_profile", "scenario:broken_profile")
    broken["permission_profile"] = _permission_profile()
    broken["permission_profile"]["data_practices"]["collected"] = [
        "private phone and email"
    ]
    broken["application_roles"] = _application_roles()
    _write_project(projects, valid)
    _write_project(projects, broken)
    service = ApplicationRegistryProjection(tmp_path / "state")

    result = _rebuild(service, projects)

    assert result["status"] == "completed"
    assert {item["id"] for item in service.list_development_projects()} == {
        "broken_profile",
        "valid",
    }
    profile = service.application_permission_profiles(
        "broken_profile", source_kind="dev_project"
    )[0]
    assert profile["validation_status"] == "invalid"
    assert profile["permission_profile_digest"] is None
    assert profile["declaration_summary"]["repair_required"] is True
    assert "canonical identifier" in profile["declaration_summary"]["error"]
    assert service.application_roles("broken_profile", source_kind="dev_project") == []


def test_registry_projection_rebuild_reuses_unchanged_sources_without_manifest_parse(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    _write_project(projects, _project("cached", "scenario:cached"))
    service = ApplicationRegistryProjection(tmp_path / "state")
    _rebuild(service, projects)

    def explode(*_args, **_kwargs):
        raise AssertionError("unchanged project.yaml should not be parsed")

    result = service.rebuild_development_projects(
        projects,
        parser=explode,
        schema_bytes=compositions._schema_path().read_bytes(),
    )

    assert result["status"] == "completed"
    assert result["scanned"] == 1
    assert result["indexed"] == 1
    assert result["reused"] == 1
    assert result["changed"] == 0
    assert service.project_for_component("scenario:cached")[0]["id"] == "cached"


def test_registry_projection_records_invalid_sources_without_indexing_them(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    _write_project(projects, _project("valid", "scenario:valid"))
    broken = projects / "broken"
    broken.mkdir(parents=True)
    (broken / "project.yaml").write_text("schema: adaos.project.v1\nid: broken\n", encoding="utf-8")
    service = ApplicationRegistryProjection(tmp_path / "state")

    result = _rebuild(service, projects)

    assert result["status"] == "completed"
    assert result["indexed"] == 1
    assert result["invalid"] == 1
    assert [item["id"] for item in service.list_development_projects()] == ["valid"]
    assert service.validation_counts() == {"invalid": 1, "valid": 1}

    con = sqlite3.connect(service.db_path)
    try:
        statuses = dict(
            con.execute(
                "SELECT source_ref, validation_status FROM projection_source ORDER BY source_ref"
            ).fetchall()
        )
    finally:
        con.close()
    assert statuses == {"project:broken": "invalid", "project:valid": "valid"}


def test_registry_snapshot_trust_requires_closed_graceful_epoch(tmp_path: Path) -> None:
    service = ApplicationRegistryProjection(tmp_path / "state")
    assert service.snapshot_trust_state()["reason"] == "registry_projection_absent"

    epoch = service.start_epoch(runtime_instance_id="runtime.1")
    open_state = service.snapshot_trust_state()
    assert not open_state["trusted_snapshot"]
    assert open_state["reason"] == "epoch_open"

    sealed = service.seal_epoch(
        epoch["epoch_id"],
        shutdown_request_id="shutdown.1",
        shutdown_reason="test",
        shutdown_scope="runtime_retire",
    )
    trusted = service.snapshot_trust_state()

    assert sealed["seal_status"] == "complete"
    assert trusted["trusted_snapshot"] is True
    assert trusted["shutdown_request_id"] == "shutdown.1"


def test_registry_runtime_start_trust_allows_current_open_epoch_to_use_projection(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    _write_project(projects, _project("trusted", "scenario:trusted"))
    service = ApplicationRegistryProjection(tmp_path / "state")
    _rebuild(service, projects)
    previous_epoch = service.start_epoch(runtime_instance_id="runtime.previous")
    service.seal_epoch(previous_epoch["epoch_id"], shutdown_request_id="shutdown.previous")
    previous_trust = service.snapshot_trust_state()

    service.start_epoch(
        runtime_instance_id="runtime.current",
        previous_snapshot_trust=previous_trust,
    )

    assert service.snapshot_trust_state()["reason"] == "epoch_open"
    startup_trust = service.runtime_start_snapshot_trust_state()
    assert startup_trust["trusted_snapshot"] is True
    assert startup_trust["shutdown_request_id"] == "shutdown.previous"
    assert service.development_projects_ready(projects, require_trusted_runtime_start=True)


def test_registry_runtime_start_requires_full_rebuild_after_ungraceful_snapshot(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    _write_project(projects, _project("recovered", "scenario:recovered"))
    service = ApplicationRegistryProjection(tmp_path / "state")
    _rebuild(service, projects)
    service.start_epoch(runtime_instance_id="runtime.crashed")
    previous_trust = service.snapshot_trust_state()

    assert previous_trust["reason"] == "epoch_open"
    service.start_epoch(
        runtime_instance_id="runtime.restarted",
        previous_snapshot_trust=previous_trust,
    )
    assert not service.development_projects_ready(projects, require_trusted_runtime_start=True)

    def explode(*_args, **_kwargs):
        raise AssertionError("incremental reuse should not satisfy untrusted runtime recovery")

    reused = service.rebuild_development_projects(
        projects,
        parser=explode,
        schema_bytes=compositions._schema_path().read_bytes(),
    )

    assert reused["reused"] == 1
    assert reused["allow_reuse"] is True
    assert not service.development_projects_ready(projects, require_trusted_runtime_start=True)

    rebuilt = service.rebuild_development_projects(
        projects,
        parser=compositions._parse_project,
        schema_bytes=compositions._schema_path().read_bytes(),
        allow_reuse=False,
    )

    assert rebuilt["reused"] == 0
    assert rebuilt["changed"] == 1
    assert rebuilt["allow_reuse"] is False
    assert service.development_projects_ready(projects, require_trusted_runtime_start=True)


def test_registry_projection_indexes_application_store_inventory(tmp_path: Path) -> None:
    service = ApplicationRegistryProjection(tmp_path / "state")
    store = _FakeApplicationStore(tmp_path / "state" / "applications")

    result = service.rebuild_application_store(store, operation_id="store.test")

    assert result["status"] == "completed"
    assert result["indexed"] == 1
    summaries = service.installed_summaries()
    assert len(summaries) == 1
    assert summaries[0]["application_id"] == "app_demo"
    assert summaries[0]["title"] == "Demo App"
    assert summaries[0]["installed"] is True
    assert summaries[0]["local_beta_active"] is True
    assert summaries[0]["release_digest"] == _DIGEST_A

    channels = service.release_channel_pointers("app_demo")
    assert channels is not None
    assert channels["release_digest"] == _DIGEST_A
    assert channels["channels"] == {"stable": _DIGEST_A, "prerelease": _DIGEST_B}

    owners = service.applications_for_component("skill:demo")
    assert [item["application_id"] for item in owners] == ["app_demo"]

    profiles = service.application_permission_profiles(
        "app_demo",
        release_digest=_DIGEST_A,
        source_kind="application_store",
    )
    roles = service.application_roles(
        "app_demo",
        release_digest=_DIGEST_A,
        source_kind="application_store",
    )
    assert profiles[0]["release_digest"] == _DIGEST_A
    assert profiles[0]["required_permissions"] == ["workspace.read", "workspace.write"]
    assert [item["role_id"] for item in roles] == ["editor", "viewer"]
