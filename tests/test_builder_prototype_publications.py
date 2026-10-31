from __future__ import annotations

import copy
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.builder.prototype_publications import (
    PrototypePublicationRejected,
    PrototypePublications,
)
from adaos.services.resources.prototype import PrototypeResourceService


@pytest.fixture
def document():
    path = (
        Path(__file__).resolve().parents[1]
        / "e2e/builder/development/contract-conformance/inventory-fixture.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def prepare(store, document, **kwargs):
    return store.prepare(
        document,
        project_ref="project:publication-test",
        change_id="change:test",
        revision=kwargs.pop("revision", "001"),
        **kwargs,
    )


def stock(receipt):
    return next(
        row["resource_type"]
        for row in receipt["resources"]
        if row["resource_type"].endswith(".stock")
    )


def edit_stock(service, receipt, note):
    service.operate(
        stock(receipt),
        "update",
        record_id="stock-1",
        command_id="stock-save",
        payload={"patch": {"reserve_note": note}},
    )


def test_preparation_is_exact_immutable_and_not_a_publication(tmp_path, document):
    store = PrototypePublications(tmp_path / "state")
    before = copy.deepcopy(document)
    first = prepare(store, document)
    ref = first["publication_ref"]
    assert store.load(ref) == first == prepare(store, document)
    assert first["head_advanced"] is False
    assert first["authoring_admitted"] is False
    assert not (store.state_dir / "resources").exists()
    assert not list(store.root.glob("*.materialized.json"))
    assert not list(tmp_path.rglob("current.txt"))
    assert not list(tmp_path.rglob("prompt_state.json"))
    assert document == before
    first["compiled"]["webui"]["generated_by"] = "caller-change"
    document["title"]["en"] = "caller-change"
    assert store.load(ref)["document"] == before
    assert store.load(ref)["compiled"]["webui"] != first["compiled"]["webui"]


def test_distinct_preparations_do_not_replace_previous_runtime_edits(
    tmp_path, document
):
    store = PrototypePublications(tmp_path)
    previous = prepare(store, document)
    previous_ready = store.materialize(previous["publication_ref"])
    runtime = PrototypeResourceService(state_dir=tmp_path)
    edit_stock(runtime, previous_ready, "Retained operator edit")
    previous_states = {
        row["resource_type"]: runtime.snapshot(row["resource_type"])
        for row in previous_ready["resources"]
    }
    successor = prepare(
        store,
        document,
        revision="002",
        previous_publication_ref=previous["publication_ref"],
    )
    ready = store.materialize(successor["publication_ref"])
    assert {row["resource_type"] for row in ready["resources"]}.isdisjoint(
        previous_states
    )
    assert {key: runtime.snapshot(key) for key in previous_states} == previous_states
    assert runtime.snapshot(stock(ready))["records"][0]["reserve_note"] == ""
    # Preparation does not silently claim to have carried the old edits forward.
    assert ready["head_advanced"] is False
    assert "preservation_receipt" not in ready


def test_partial_materialization_recovers_without_old_view_or_journal_rollback(
    tmp_path, document, monkeypatch
):
    store = PrototypePublications(tmp_path)
    previous = prepare(store, document)
    previous_ready = store.materialize(previous["publication_ref"])
    runtime = PrototypeResourceService(state_dir=tmp_path)
    edit_stock(runtime, previous_ready, "Keep this edit")
    previous_state = runtime.snapshot(stock(previous_ready))
    journal = tmp_path / "builder" / "paid-observation.json"
    journal.write_text('{"paid":true}', encoding="utf-8")
    successor = prepare(
        store,
        document,
        revision="002",
        previous_publication_ref=previous["publication_ref"],
    )
    ref = successor["publication_ref"]
    source_bytes = store._path(ref).read_bytes()
    original = PrototypeResourceService.materialize

    def fail_second(self, bundle):
        if bundle["resource_definition"]["resource_type"].endswith(".warehouses"):
            raise OSError("controlled second-resource failure")
        return original(self, bundle)

    with monkeypatch.context() as scoped:
        scoped.setattr(PrototypeResourceService, "materialize", fail_second)
        with pytest.raises(OSError, match="second-resource failure"):
            store.materialize(ref)
    assert not store._path(ref, ".materialized.json").exists()
    assert store._path(ref).read_bytes() == source_bytes
    assert runtime.snapshot(stock(previous_ready)) == previous_state
    assert journal.read_text(encoding="utf-8") == '{"paid":true}'
    # A reconstructed service resumes the immutable plan, without a model call
    # or writing source mirrors / a head pointer.
    restarted = PrototypePublications(tmp_path)
    ready = restarted.materialize(ref)
    receipt_bytes = store._path(ref, ".materialized.json").read_bytes()
    edit_stock(runtime, ready, "New view edit survives exact replay")
    pending_state = runtime.snapshot(stock(ready))
    assert restarted.materialize(ref) == ready
    assert runtime.snapshot(stock(ready)) == pending_state
    assert runtime.snapshot(stock(previous_ready)) == previous_state
    assert store._path(ref, ".materialized.json").read_bytes() == receipt_bytes
    assert journal.read_text(encoding="utf-8") == '{"paid":true}'


def test_concurrent_exact_preparations_retain_one_identity(tmp_path, document):
    store = PrototypePublications(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: prepare(store, document), range(4)))
    assert all(item == results[0] for item in results)
    assert len(list(store.root.glob("*.json"))) == 1
    assert store.load(results[0]["publication_ref"]) == results[0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project_ref": "project:../other"},
        {"project_ref": "project:"},
        {"project_ref": False},
        {"change_id": ""},
        {"change_id": False},
        {"revision": "../001"},
        {"revision": 1},
        {"previous_publication_ref": "../other"},
        {"previous_publication_ref": False},
    ],
)
def test_invalid_publication_identity_creates_nothing(tmp_path, document, kwargs):
    values = {
        "project_ref": "project:test",
        "change_id": "change:test",
        "revision": "001",
        **kwargs,
    }
    with pytest.raises(
        PrototypePublicationRejected, match="Invalid publication identity"
    ):
        PrototypePublications(tmp_path / "state").prepare(document, **values)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "ref", [False, "../outside", "prototype-publication:" + "A" * 24]
)
def test_invalid_load_reference_is_not_a_path(tmp_path, ref):
    with pytest.raises(
        PrototypePublicationRejected, match="Invalid publication reference"
    ):
        PrototypePublications(tmp_path).load(ref)


def test_tampered_preparation_is_never_materialized(tmp_path, document):
    store = PrototypePublications(tmp_path)
    value = prepare(store, document)
    ref = value["publication_ref"]
    value["seed"]["project_ref"] = "project:other"
    store._path(ref).write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PrototypePublicationRejected):
        store.materialize(ref)
    assert not (tmp_path / "resources").exists()
    with pytest.raises(PrototypePublicationRejected, match="conflicting content"):
        prepare(store, document)


@pytest.mark.parametrize("reparse", [False, True])
def test_every_ancestor_is_checked_for_links_and_windows_junctions(
    tmp_path, monkeypatch, reparse
):
    store = PrototypePublications(tmp_path / "state")
    original = Path.lstat
    blocked = store.state_dir / "builder"

    def controlled_metadata(path):
        if path == blocked:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR if reparse else stat.S_IFLNK,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if reparse else 0,
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", controlled_metadata)
    with pytest.raises(PrototypePublicationRejected, match="must not traverse links"):
        store._path("prototype-publication:" + "a" * 24)
    assert not store.state_dir.exists()


def test_oversized_source_refuses_before_compilation_or_storage(
    tmp_path, document, monkeypatch
):
    import adaos.services.builder.prototype_publications as publications

    monkeypatch.setattr(publications, "_MAX_BYTES", 64)
    monkeypatch.setattr(
        publications,
        "compile_semantic_prototype",
        lambda *a, **k: pytest.fail("compiled oversized source"),
    )
    with pytest.raises(PrototypePublicationRejected, match="bounded storage size"):
        prepare(PrototypePublications(tmp_path / "state"), document)
    assert not (tmp_path / "state").exists()
