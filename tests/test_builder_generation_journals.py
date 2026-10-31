import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_builder_authoring_scope import scoped_project as _scoped_project
from test_builder_workflow import workflow_project as _workflow_project

from adaos.sdk.builder import prototype
from adaos.services.builder.generation_journals import (
    GenerationJournalRejected,
    GenerationJournals,
)

workflow_project = _workflow_project
scoped_project = _scoped_project


def test_sdk_journal_is_core_owned_idempotent_and_does_not_write_dev(
    workflow_project, monkeypatch
):
    workflow, root = workflow_project
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: workflow)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    owner = prototype.prepare_generation_journal(project_ref="scenario:recipes")
    directory = prototype.generation_journal_dir(owner["journal_ref"])
    assert directory.is_relative_to(workflow.state_dir)
    assert not directory.is_relative_to(root)
    assert prototype.prepare_generation_journal(project_ref="scenario:recipes") == owner
    assert {
        path: path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before
    for function in (
        prototype.prepare_generation_journal,
        prototype.generation_journal_dir,
    ):
        assert function.__adaos_authoring_operation__
        assert function.__name__ in prototype.__all__


def test_frozen_journal_survives_manifest_loss_without_rebinding(scoped_project):
    workflow, manifest_path, _ = scoped_project
    store = GenerationJournals(workflow)
    owner = store.prepare("project:qualification")
    assert owner["source_scope"]["object_type"] == "scenario"
    directory = store.resolve(owner["journal_ref"])
    manifest_path.unlink()
    assert store.resolve(owner["journal_ref"]) == directory
    with pytest.raises(ValueError):
        store.prepare("project:qualification")


def test_core_journal_result_can_commit_after_legacy_state_and_webui_loss(
    workflow_project, monkeypatch
):
    from test_builder_generation_authoring import ready_result, source_bytes

    workflow, root = workflow_project
    store = GenerationJournals(workflow)
    owner = store.prepare("scenario:recipes")
    arguments = ready_result(workflow, store.resolve(owner["journal_ref"]))
    workflow._legacy_state_path("scenario", "recipes").unlink()
    (root / "webui.json").unlink()
    remaining = source_bytes(root)
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: workflow)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    prepared = prototype.prepare_generation_publication(**arguments)
    accepted = prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )
    assert accepted["committed"]
    assert source_bytes(root) == remaining
    assert prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )["duplicate"]


def test_changed_manifest_gets_a_distinct_location_but_old_receipt_stays_readable(
    scoped_project,
):
    workflow, manifest_path, manifest = scoped_project
    store = GenerationJournals(workflow)
    original = store.prepare("project:qualification")
    directory = store.resolve(original["journal_ref"])
    manifest["version"] = "0.2.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    changed = store.prepare("project:qualification")
    assert original["journal_ref"] != changed["journal_ref"]
    assert store.resolve(original["journal_ref"]) == directory


@pytest.mark.parametrize("fault", ["missing", "corrupt", "different", "oversized"])
def test_missing_or_conflicting_owner_never_recreates_or_adopts_journals(
    workflow_project, fault
):
    workflow, _ = workflow_project
    store = GenerationJournals(workflow)
    owner = store.prepare("scenario:recipes")
    directory = store.resolve(owner["journal_ref"])
    (directory / "wire-retained.json").write_text("{}", encoding="utf-8")
    path = directory / "owner.json"
    if fault == "missing":
        path.unlink()
    elif fault == "corrupt":
        path.write_text("{", encoding="utf-8")
    elif fault == "different":
        changed = copy.deepcopy(owner)
        changed["source_scope"]["object_id"] = "different"
        path.write_text(json.dumps(changed), encoding="utf-8")
    else:
        path.write_bytes(b" " * (16 * 1024 + 1))
    before = {file: file.read_bytes() for file in directory.iterdir()}
    with pytest.raises(GenerationJournalRejected):
        store.resolve(owner["journal_ref"])
    with pytest.raises(GenerationJournalRejected):
        store.prepare("scenario:recipes")
    assert {file: file.read_bytes() for file in directory.iterdir()} == before


@pytest.mark.parametrize(
    "ref", [None, "", "../escape", "generation-journal:" + "f" * 25]
)
def test_invalid_reference_does_not_create_a_location(workflow_project, ref):
    workflow, _ = workflow_project
    store = GenerationJournals(workflow)
    with pytest.raises(GenerationJournalRejected):
        store.resolve(ref)
    assert not store.root.exists()


def test_linked_ancestor_refuses_before_any_owner_write(workflow_project, monkeypatch):
    workflow, _ = workflow_project
    store = GenerationJournals(workflow)
    original = Path.lstat

    def linked(path, *args, **kwargs):
        if path == store.root:
            return SimpleNamespace(st_mode=0o120777)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", linked)
    with pytest.raises(ValueError, match="traverse links"):
        store.prepare("scenario:recipes")
    assert not store.root.exists()
