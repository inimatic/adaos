from __future__ import annotations

import copy
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.builder.prototype_publications import (
    PrototypePublicationRejected,
    PrototypePublications,
)
from adaos.services.resources.prototype import PrototypeResourceService
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


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


@pytest.fixture
def workflow_target(tmp_path):
    root = tmp_path / "scenarios" / "publication-test"
    (root / "ui_revisions").mkdir(parents=True)
    (root / "scenario.yaml").write_text(
        "id: publication-test\nversion: 0.1.0\n", encoding="utf-8"
    )
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {"application": {"desktop": {"pageSchema": {"id": "previous"}}}},
    }
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    (root / "ui_revisions" / "001.json").write_text("{}", encoding="utf-8")
    (root / "ui_revisions" / "current.txt").write_text("001\n", encoding="utf-8")
    workflow = BuilderWorkflowService(
        dev_skills_root=tmp_path / "skills",
        dev_scenarios_root=tmp_path / "scenarios",
        state_dir=tmp_path / "state",
    )
    planned = workflow.transition(
        "scenario",
        "publication-test",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-publication",
            "request": "Update the inventory interface.",
            "issues": [
                {
                    "issue_id": "inventory",
                    "title": "Inventory interface",
                    "lane": "prototype",
                    "acceptance_criteria": [
                        "The approved inventory interface is visible."
                    ],
                }
            ],
        },
    )["workflow"]
    return workflow, root, planned


def prepare_for_workflow(workflow, document, revision="002", **kwargs):
    store = PrototypePublications(workflow.state_dir)
    value = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision=revision,
        base_revision="001",
        **kwargs,
    )
    store.materialize(value["publication_ref"])
    return store, value


def test_committed_head_and_snapshot_ignore_mutable_source_pointers(
    workflow_target, document
):
    workflow, root, planned = workflow_target
    store, value = prepare_for_workflow(workflow, document)
    source_before = {
        path: path.read_bytes()
        for path in (root / "webui.json", root / "ui_revisions/current.txt")
    }
    result = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert result["committed"] is True
    assert workflow.current_prototype_revision("scenario", "publication-test") == "002"
    assert workflow._prototype_webui_snapshot("scenario", "publication-test") == (
        value["compiled"]["webui"],
        "002",
        value["webui_digest"],
    )
    assert {path: path.read_bytes() for path in source_before} == source_before
    (root / "ui_revisions/current.txt").write_text("999\n", encoding="utf-8")
    (root / "webui.json").write_text('{"mutable":"unaccepted"}', encoding="utf-8")
    assert (
        workflow.describe("scenario", "publication-test")["prototype"]["head_revision"]
        == "002"
    )
    assert (
        workflow._prototype_webui_snapshot("scenario", "publication-test")[0]
        == value["compiled"]["webui"]
    )
    before = (root / "prompt_state.json").read_bytes()
    replay = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert replay["duplicate"] is True
    assert (root / "prompt_state.json").read_bytes() == before
    assert store.ready(value["publication_ref"])["preparation"] == value


def test_failed_head_replace_preserves_old_revision_and_paid_evidence(
    workflow_target, document, monkeypatch
):
    import adaos.services.builder.workflow as workflow_module

    workflow, root, planned = workflow_target
    store, value = prepare_for_workflow(workflow, document)
    before = (root / "prompt_state.json").read_bytes()
    snapshot = workflow._prototype_webui_snapshot("scenario", "publication-test")
    journal = Path(workflow.state_dir) / "paid-evidence.json"
    journal.write_text('{"paid":true}', encoding="utf-8")
    original = workflow_module._replace_path

    def fail_head(source, target):
        if Path(target) == root / "prompt_state.json":
            raise OSError("controlled head replace failure")
        return original(source, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(workflow_module, "_replace_path", fail_head)
        with pytest.raises(OSError, match="head replace failure"):
            workflow.commit_prepared_prototype(
                "scenario",
                "publication-test",
                value["publication_ref"],
                expected_generation=planned["generation"],
            )
    assert (root / "prompt_state.json").read_bytes() == before
    assert workflow.current_prototype_revision("scenario", "publication-test") == "001"
    assert (
        workflow._prototype_webui_snapshot("scenario", "publication-test") == snapshot
    )
    visible = workflow.describe("scenario", "publication-test")
    assert not any(
        value["publication_ref"] in row.get("output_refs", [])
        for row in visible["change"]["runs"]
    )
    assert store.ready(value["publication_ref"])["preparation"] == value
    assert journal.read_text(encoding="utf-8") == '{"paid":true}'
    result = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert result["committed"] is True


def test_post_commit_event_failure_is_recoverable_projection_not_failed_publication(
    workflow_target, document
):
    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)

    def event_failure(_):
        raise OSError("controlled event delivery failure")

    workflow.event_sink = event_failure
    result = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert result["committed"] is True and result["projection_pending"] is True
    assert workflow.current_prototype_revision("scenario", "publication-test") == "002"
    before = (root / "prompt_state.json").read_bytes()
    assert (
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )["duplicate"]
        is True
    )
    assert (root / "prompt_state.json").read_bytes() == before


def test_competing_prepared_writers_cannot_both_advance_same_base(
    workflow_target, document
):
    workflow, _, planned = workflow_target
    _, first = prepare_for_workflow(workflow, document)
    _, second = prepare_for_workflow(workflow, document, revision="003")

    def commit(value):
        try:
            return workflow.commit_prepared_prototype(
                "scenario",
                "publication-test",
                value["publication_ref"],
                expected_generation=planned["generation"],
            )
        except BuilderWorkflowError:
            return {"committed": False}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, (first, second)))
    assert sum(row["committed"] for row in results) == 1
    winner = (first, second)[
        next(index for index, row in enumerate(results) if row["committed"])
    ]
    assert (
        workflow.current_prototype_revision("scenario", "publication-test")
        == winner["seed"]["revision"]
    )


def test_committed_publication_refuses_compatibility_revision_downgrade(
    workflow_target, document
):
    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    before = (root / "prompt_state.json").read_bytes()
    with pytest.raises(BuilderWorkflowError, match="cannot use compatibility"):
        workflow.transition(
            "scenario",
            "publication-test",
            "prototype_revision_recorded",
            metadata={"revision": "003"},
        )
    assert (root / "prompt_state.json").read_bytes() == before


@pytest.mark.parametrize("changed", ["base_revision", "change_id", "project_ref"])
def test_prepared_commit_refuses_wrong_target_or_stale_base(
    workflow_target, document, changed
):
    workflow, root, planned = workflow_target
    store = PrototypePublications(workflow.state_dir)
    values = dict(
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="002",
        base_revision="001",
    )
    values[changed] = {
        "base_revision": "000",
        "change_id": "CH-other",
        "project_ref": "scenario:other",
    }[changed]
    value = store.prepare(document, **values)
    store.materialize(value["publication_ref"])
    before = (root / "prompt_state.json").read_bytes()
    with pytest.raises((BuilderWorkflowError, PrototypePublicationRejected)):
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )
    assert (root / "prompt_state.json").read_bytes() == before


def test_ready_requires_all_pinned_bundles_not_just_a_receipt(tmp_path, document):
    store = PrototypePublications(tmp_path)
    value = prepare(store, document)
    ref = value["publication_ref"]
    with pytest.raises(
        PrototypePublicationRejected, match="materialization unavailable"
    ):
        store.ready(ref)
    ready = store.materialize(ref)
    runtime = PrototypeResourceService(state_dir=tmp_path)
    edit_stock(runtime, ready, "Mutable edits are allowed")
    assert store.ready(ref)["materialization"] == ready
    state = runtime.snapshot(stock(ready))
    state["definition"]["metadata"]["revision"] = "999"
    runtime._store().put(stock(ready), state)
    with pytest.raises(PrototypePublicationRejected, match="bundle changed or missing"):
        store.ready(ref)


def test_post_replace_catalog_failure_does_not_report_a_failed_commit(
    workflow_target, document, monkeypatch
):
    import adaos.services.builder.catalog_file_metadata as catalog

    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)

    def catalog_failure(*_):
        raise OSError("controlled post-replace catalog failure")

    monkeypatch.setattr(catalog, "record_catalog_file_metadata", catalog_failure)
    result = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert result["ok"] is True and result["projection_pending"] is True
    assert workflow.current_prototype_revision("scenario", "publication-test") == "002"
    before = (root / "prompt_state.json").read_bytes()
    assert (
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )["duplicate"]
        is True
    )
    assert (root / "prompt_state.json").read_bytes() == before


def test_missing_materialization_is_not_repaired_implicitly_on_commit(
    workflow_target, document
):
    workflow, root, planned = workflow_target
    store = PrototypePublications(workflow.state_dir)
    value = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="002",
        base_revision="001",
    )
    before = (root / "prompt_state.json").read_bytes()
    with pytest.raises(
        PrototypePublicationRejected, match="materialization unavailable"
    ):
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )
    assert (root / "prompt_state.json").read_bytes() == before
    assert not store._path(value["publication_ref"], ".materialized.json").exists()


def test_successor_commit_uses_exact_publication_base_and_preserves_prior_data(
    workflow_target, document
):
    workflow, _, planned = workflow_target
    store, first = prepare_for_workflow(workflow, document)
    committed = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    previous_ready = store.ready(first["publication_ref"])["materialization"]
    edit_stock(runtime, previous_ready, "Retain accepted base state")
    previous_state = runtime.snapshot(stock(previous_ready))
    successor = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=first["publication_ref"],
    )
    store.materialize(successor["publication_ref"])
    result = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        successor["publication_ref"],
        expected_generation=committed["workflow"]["generation"],
    )
    assert result["committed"] is True
    assert workflow.current_prototype_revision("scenario", "publication-test") == "003"
    assert runtime.snapshot(stock(previous_ready)) == previous_state
    run = next(
        row
        for row in result["workflow"]["change"]["runs"]
        if successor["publication_ref"] in row["output_refs"]
    )
    assert "ui_revision:003" in run["output_refs"]
    assert successor["preparation_id"] in run["evidence_refs"]


@pytest.mark.parametrize("wrong_generation", [None, True, -1, "1"])
def test_commit_requires_exact_integer_workflow_generation(
    workflow_target, document, wrong_generation
):
    workflow, root, _ = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    before = (root / "prompt_state.json").read_bytes()
    with pytest.raises(BuilderWorkflowError, match="exact generation"):
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=wrong_generation,
        )
    assert (root / "prompt_state.json").read_bytes() == before


def test_private_transition_hook_cannot_bypass_managed_commit_guard(
    workflow_target, document
):
    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    before = (root / "prompt_state.json").read_bytes()
    with pytest.raises(
        PrototypePublicationRejected, match="requires its managed guard"
    ):
        workflow.transition(
            "scenario",
            "publication-test",
            "prototype_revision_recorded",
            metadata={"revision": "002"},
            expected_generation=planned["generation"],
            _prototype_publication_ref=value["publication_ref"],
        )
    assert (root / "prompt_state.json").read_bytes() == before


def test_copied_context_cannot_reuse_guard_after_exit_or_on_another_thread(tmp_path):
    store = PrototypePublications(tmp_path)
    project = "scenario:publication-test"
    with store.commit_guard(project):
        context = copy_context()
        context.run(store.assert_commit_guard, project)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(
                PrototypePublicationRejected, match="requires its managed guard"
            ):
                pool.submit(context.run, store.assert_commit_guard, project).result()
    with pytest.raises(
        PrototypePublicationRejected, match="requires its managed guard"
    ):
        context.run(store.assert_commit_guard, project)
