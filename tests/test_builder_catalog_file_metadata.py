import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.builder.catalog_file_metadata import CatalogFileMetadata, file_key, record_catalog_file_metadata
from adaos.services.builder.project_catalog import BuilderProjectCatalogService


def catalog(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("projects", "scenarios", "skills")]
    for root in roots:
        root.mkdir()
    project, component = roots[0] / "example", roots[1] / "main"
    project.mkdir(); component.mkdir()
    (component / "prompt_state.json").write_text('{"updated_at":"2026-10-27T10:00:00Z"}', encoding="utf-8")
    row = {"id": "example", "version": "1.0.0", "components": {"owned": [{"ref": "scenario:main"}]}}
    monkeypatch.setattr(BuilderProjectCatalogService, "_project_registry", lambda *_: [(project, project / "project.yaml", row)])
    service = BuilderProjectCatalogService(roots[2], roots[1], tmp_path / "state", roots[0], True)
    return service, project, component


def test_warmed_sql_catalog_does_not_stat_or_read_each_project_file(tmp_path, monkeypatch):
    import adaos.services.builder.project_catalog as module
    service, _, _ = catalog(tmp_path, monkeypatch)
    service.refresh_file_metadata()
    monkeypatch.setattr(module, "_prompt_summary", lambda *_: pytest.fail("interactive metadata filesystem scan"))
    original_stat = Path.stat
    def checked_stat(path, *args, **kwargs):
        assert path.name != "prompt_state.json", "interactive per-file metadata stat"
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", checked_stat)
    row = service.list_projects(kind="project")[0]
    assert row["updated"] == "2026-10-27T10:00:00Z"
    assert row["metadata_source"] == "core_sqlite"
    assert row["metadata_status"] == "ready"
    assert CatalogFileMetadata(service.state_dir).diagnostics()["scopes"][0]["file_total"] == 2


def test_sdk_write_updates_owned_activity_and_archive_without_full_revalidation(tmp_path, monkeypatch):
    service, project, component = catalog(tmp_path, monkeypatch)
    service.refresh_file_metadata()
    payload = {"updated_at": "2026-10-27T12:00:00Z", "archived": True}
    path = component / "prompt_state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    record_catalog_file_metadata(service.state_dir, path)
    assert service.list_projects(kind="project")[0]["updated"] == payload["updated_at"]
    path = project / "prompt_state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    record_catalog_file_metadata(service.state_dir, path)
    assert service.list_projects(kind="project") == []
    assert service.list_projects(kind="project", include_archived=True)[0]["archived"] is True


def test_prepared_old_snapshot_cannot_overwrite_a_concurrent_sdk_write(tmp_path, monkeypatch):
    service, _, component = catalog(tmp_path, monkeypatch)
    service.refresh_file_metadata()
    started = time.time() - 10
    path = component / "prompt_state.json"
    store = CatalogFileMetadata(service.state_dir)
    path.write_text('{"updated_at":"new","archived":true}', encoding="utf-8")
    store.update_file(path)
    store.replace_snapshot(service._metadata_scope(), service._metadata_roots(),
        [(file_key(path), {"updated_at": "old", "archived": False}, None)], started_at=started)
    records, _ = store.read(service._metadata_scope())
    assert records[file_key(path)]["updated_at"] == "new"
    assert records[file_key(path)]["archived"] is True


def test_startup_revalidation_does_not_trust_a_previous_process_snapshot_forever(tmp_path, monkeypatch):
    import adaos.services.builder.project_catalog as module
    service, _, component = catalog(tmp_path, monkeypatch)
    service.refresh_file_metadata()
    (component / "prompt_state.json").write_text('{"updated_at":"2026-10-27T13:00:00Z"}', encoding="utf-8")
    module._METADATA_READY.pop(str(service.state_dir) + ":" + service._metadata_scope())
    assert service.list_projects(kind="project")[0]["updated"] == "2026-10-27T13:00:00Z"


def test_external_reconciliation_is_single_flight_and_explicitly_observable(tmp_path, monkeypatch):
    import adaos.services.builder.project_catalog as module
    service, _, component = catalog(tmp_path, monkeypatch)
    service.refresh_file_metadata()
    store = CatalogFileMetadata(service.state_dir)
    records, _ = store.read(service._metadata_scope())
    store.replace_snapshot(service._metadata_scope(), service._metadata_roots(),
        [(key, value, None) for key, value in records.items()], started_at=time.time()-60)
    tasks = []
    monkeypatch.setattr(module, "_METADATA_EXECUTOR", SimpleNamespace(submit=lambda task: tasks.append(task)))
    try:
        assert service.list_projects(kind="project")[0]["metadata_refresh_pending"] is True
        assert service.list_projects(kind="project")[0]["metadata_status"] == "refreshing"
        assert len(tasks) == 1
        (component / "prompt_state.json").write_text('{"updated_at":"2026-10-27T14:00:00Z"}', encoding="utf-8")
        tasks[0]()
        assert service.list_projects(kind="project")[0]["updated"] == "2026-10-27T14:00:00Z"
    finally:
        module._METADATA_REFRESHING.discard(str(service.state_dir) + ":" + service._metadata_scope())


def test_metadata_update_never_indexes_unregistered_source_roots(tmp_path, monkeypatch):
    service, _, _ = catalog(tmp_path, monkeypatch)
    service.refresh_file_metadata()
    path = tmp_path / "private" / "unrelated" / "prompt_state.json"
    path.parent.mkdir(parents=True)
    path.write_text('{}', encoding="utf-8")
    store = CatalogFileMetadata(service.state_dir)
    assert store.update_file(path) == 0
    assert file_key(path) not in store.read(service._metadata_scope())[0]
