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
    _write_project(projects, _project("beta", "skill:beta"))
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
