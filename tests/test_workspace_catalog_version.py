from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

from adaos.services.workspaces import index as workspace_index
from adaos.services.workspaces.relations import (
    BUILDER_PROJECT_PREVIEW,
    WebspaceRelationshipRegistry,
)


class _Sql:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


def test_workspace_catalog_version_changes_only_with_catalog_content(monkeypatch, tmp_path: Path) -> None:
    sql = _Sql(tmp_path / "catalog.db")
    monkeypatch.setattr(workspace_index, "get_ctx", lambda: SimpleNamespace(sql=sql))
    monkeypatch.setattr(
        workspace_index,
        "ystore_path_for_webspace",
        lambda webspace_id: tmp_path / f"{webspace_id}.yjs",
    )

    assert workspace_index.workspace_catalog_version() == 0
    created = workspace_index.ensure_workspace("dev1")
    assert workspace_index.workspace_catalog_version() == 1

    workspace_index.ensure_workspace("dev1")
    workspace_index.set_workspace_manifest(
        "dev1",
        display_name=created.display_name,
        kind=created.kind,
        home_scenario=created.home_scenario,
        source_mode=created.source_mode,
    )
    assert workspace_index.workspace_catalog_version() == 1

    workspace_index.set_workspace_manifest("dev1", home_scenario="builder")
    assert workspace_index.workspace_catalog_version() == 2
    workspace_index.delete_workspace("missing")
    assert workspace_index.workspace_catalog_version() == 2
    workspace_index.delete_workspace("dev1")
    assert workspace_index.workspace_catalog_version() == 3

    workspace_index.reset_webspaces([])
    assert workspace_index.workspace_catalog_version() == 4


def test_explicit_workspace_kind_is_not_overridden_by_legacy_suffix(monkeypatch, tmp_path: Path) -> None:
    sql = _Sql(tmp_path / "catalog.db")
    monkeypatch.setattr(workspace_index, "get_ctx", lambda: SimpleNamespace(sql=sql))
    monkeypatch.setattr(
        workspace_index,
        "ystore_path_for_webspace",
        lambda webspace_id: tmp_path / f"{webspace_id}.yjs",
    )

    legacy = workspace_index.ensure_workspace("named-dev")
    assert legacy.effective_kind == "dev"

    explicit = workspace_index.set_workspace_manifest(
        "named-dev",
        kind="workspace",
        source_mode="workspace",
    )
    assert explicit.effective_kind == "workspace"


def test_workspace_delete_removes_explicit_preview_relations(monkeypatch, tmp_path: Path) -> None:
    sql = _Sql(tmp_path / "catalog.db")
    monkeypatch.setattr(workspace_index, "get_ctx", lambda: SimpleNamespace(sql=sql))
    monkeypatch.setattr(
        workspace_index,
        "ystore_path_for_webspace",
        lambda webspace_id: tmp_path / f"{webspace_id}.yjs",
    )
    registry = WebspaceRelationshipRegistry(sql)
    relation, _created = registry.ensure(
        "builder-host",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="shopping",
    )
    workspace_index.ensure_workspace("builder-host")
    workspace_index.ensure_workspace(relation.target_webspace_id)

    workspace_index.delete_workspace("builder-host")

    assert registry.get_outgoing("builder-host") is None
    assert registry.get_incoming(relation.target_webspace_id) is None

    registry.ensure(
        "another-builder",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="shopping",
    )
    workspace_index.reset_webspaces([])
    assert registry.list() == []
