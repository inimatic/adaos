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
from adaos.services.resources.prototype import (
    PrototypeResourceConflict,
    PrototypeResourceService,
)
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService
from adaos.services.builder.prototype_history import PrototypeHistoryService


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
    workflow_target, document
):
    workflow, _, planned = workflow_target
    store, previous = prepare_for_workflow(workflow, document)
    previous_ready = store.materialize(previous["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        previous["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    edit_stock(runtime, previous_ready, "Retained operator edit")
    previous_states = {
        row["resource_type"]: runtime.snapshot(row["resource_type"])
        for row in previous_ready["resources"]
    }
    successor = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=previous["publication_ref"],
    )
    ready = store.materialize(successor["publication_ref"])
    assert {row["resource_type"] for row in ready["resources"]}.isdisjoint(
        previous_states
    )
    assert {key: runtime.snapshot(key) for key in previous_states} == previous_states
    assert (
        runtime.snapshot(stock(ready))["records"]
        == previous_states[stock(previous_ready)]["records"]
    )
    # Preparation carries a pinned local fork, but grants no head authority.
    assert ready["head_advanced"] is False
    assert successor["runtime_fork"]["fork_id"] == successor["seed"]["runtime_fork_ref"]


def test_relationship_display_labels_can_change_without_migrating_or_losing_live_records(workflow_target, document):
    workflow, _, planned = workflow_target
    store, previous = prepare_for_workflow(workflow, document)
    previous_ready = store.materialize(previous['publication_ref'])
    workflow.commit_prepared_prototype('scenario', 'publication-test', previous['publication_ref'],
        expected_generation=planned['generation'])
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    edit_stock(runtime, previous_ready, 'Keep this operator edit while changing a caption')
    records = runtime.snapshot(stock(previous_ready))['records']
    changed = copy.deepcopy(document)
    assert changed['relationships']
    changed['relationships'][0]['label_field_refs'] = []
    successor = store.prepare(changed, project_ref='scenario:publication-test', change_id='CH-publication',
        revision='003', base_revision='002', previous_publication_ref=previous['publication_ref'])
    ready = store.materialize(successor['publication_ref'])
    assert runtime.snapshot(stock(ready))['records'] == records
    assert runtime.snapshot(stock(previous_ready))['records'] == records


def test_partial_materialization_recovers_without_old_view_or_journal_rollback(
    workflow_target, document, monkeypatch
):
    workflow, _, planned = workflow_target
    store, previous = prepare_for_workflow(workflow, document)
    previous_ready = store.materialize(previous["publication_ref"])
    accepted = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        previous["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    edit_stock(runtime, previous_ready, "Keep this edit")
    previous_state = runtime.snapshot(stock(previous_ready))
    journal = Path(workflow.state_dir) / "builder" / "paid-observation.json"
    journal.write_text('{"paid":true}', encoding="utf-8")
    successor = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=previous["publication_ref"],
    )
    ref = successor["publication_ref"]
    source_bytes = store._path(ref).read_bytes()
    original = PrototypeResourceService.materialize

    def fail_second(self, bundle, **kwargs):
        if bundle["resource_definition"]["resource_type"].endswith(".warehouses"):
            raise OSError("controlled second-resource failure")
        return original(self, bundle, **kwargs)

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
    restarted = PrototypePublications(workflow.state_dir)
    ready = restarted.materialize(ref)
    receipt_bytes = store._path(ref, ".materialized.json").read_bytes()
    with pytest.raises(PrototypeResourceConflict, match="committed"):
        edit_stock(runtime, ready, "Uncommitted view cannot write")
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        ref,
        expected_generation=accepted["workflow"]["generation"],
    )
    edit_stock(runtime, ready, "Accepted view edit survives exact replay")
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
    assert len(list(store.root.glob("*.index.json"))) == 1
    assert len(list(store.root.glob("*.json"))) == 2
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
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    replay = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert replay["duplicate"] is True
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before
    assert store.ready(value["publication_ref"])["preparation"] == value


def test_automation_seals_committed_prototype_and_locales_without_rewriting_dev(
    workflow_target, document, tmp_path
):
    from adaos.services.builder.automation_sources import capture_automation_sources
    from adaos.services.skill_factory_sources import materialize_source_snapshot
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker

    workflow, root, planned = workflow_target
    _store, value = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype("scenario", "publication-test", value["publication_ref"],
                                       expected_generation=planned["generation"])
    dev_before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    acceptance = {"decision": "accepted", "revision": "002", "webui_digest": value["webui_digest"]}
    snapshot = capture_automation_sources(workflow=workflow, object_type="scenario",
        object_id="publication-test", acceptance=acceptance, state_dir=workflow.state_dir,
        artifacts=[("scenario", "publication-test", root)], attachments=[], created_at="test")
    workspace = tmp_path / "worker"
    materialize_source_snapshot(state_dir=workflow.state_dir, reference=snapshot, workspace=workspace)
    staged = workspace / "scenarios/publication-test"
    assert json.loads((staged / "webui.json").read_text(encoding="utf-8")) == value["compiled"]["webui"]
    assert json.loads((staged / "semantic.webui.json").read_text(encoding="utf-8")) == value["document"]
    dictionaries, locale_snapshot = workflow._prototype_locale_snapshot("scenario", "publication-test",
        value["compiled"]["webui"], "002")
    assert dictionaries == value["compiled"]["locale_dictionaries"]
    assert locale_snapshot
    for definition in value["compiled"]["webui"]["ui"]["application"].get("resources", {}).values():
        if definition.get("role") == "i18n":
            assert json.loads((staged / definition["path"]).read_text(encoding="utf-8")) == dictionaries[definition["locale"]]
    for locale, dictionary in dictionaries.items():
        assert json.loads((staged / f"assets/i18n/{locale}.json").read_text(encoding="utf-8")) == dictionary
    identity = LocalSkillFactoryWorker._accepted_prototype_identity(
        {"realize_request": {"artifacts": {"prototype_acceptance": acceptance}}},
        workspace, target_id="publication-test")
    assert identity["matches_acceptance"] is True
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == dev_before
    assert list((Path(workflow.state_dir) / "builder/source_staging").iterdir()) == []


@pytest.mark.parametrize("key,value", [("revision", "999"), ("webui_digest", "sha256:wrong")])
def test_automation_rejects_a_different_accepted_core_revision(workflow_target, document, key, value):
    from adaos.services.builder.automation_sources import capture_automation_sources
    workflow, root, planned = workflow_target
    _store, publication = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype("scenario", "publication-test", publication["publication_ref"],
                                       expected_generation=planned["generation"])
    acceptance = {"decision": "accepted", "revision": "002", "webui_digest": publication["webui_digest"], key: value}
    with pytest.raises(ValueError, match="differs from the accepted revision"):
        capture_automation_sources(workflow=workflow, object_type="scenario", object_id="publication-test",
            acceptance=acceptance, state_dir=workflow.state_dir, artifacts=[("scenario", "publication-test", root)],
            attachments=[], created_at="test")


def test_failed_head_replace_preserves_old_revision_and_paid_evidence(
    workflow_target, document, monkeypatch
):
    import adaos.services.artifact_pipeline.storage as storage
    from adaos.services.builder.workflow_authority import WorkflowAuthority

    workflow, root, planned = workflow_target
    store, value = prepare_for_workflow(workflow, document)
    authority = WorkflowAuthority(workflow.state_dir)
    authority.adopt("scenario:publication-test", root / "prompt_state.json")
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    snapshot = workflow._prototype_webui_snapshot("scenario", "publication-test")
    journal = Path(workflow.state_dir) / "paid-evidence.json"
    journal.write_text('{"paid":true}', encoding="utf-8")
    original = storage.replace_with_retry

    def fail_head(source, target):
        if Path(target) == authority.head_path("scenario:publication-test"):
            raise OSError("controlled head replace failure")
        return original(source, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(storage, "replace_with_retry", fail_head)
        with pytest.raises(OSError, match="head replace failure"):
            workflow.commit_prepared_prototype(
                "scenario",
                "publication-test",
                value["publication_ref"],
                expected_generation=planned["generation"],
            )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before
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
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    assert (
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )["duplicate"]
        is True
    )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before


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
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    with pytest.raises(BuilderWorkflowError, match="cannot use compatibility"):
        workflow.transition(
            "scenario",
            "publication-test",
            "prototype_revision_recorded",
            metadata={"revision": "003"},
        )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before


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
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    with pytest.raises((BuilderWorkflowError, PrototypePublicationRejected)):
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before


def test_ready_requires_all_pinned_bundles_not_just_a_receipt(
    workflow_target, document
):
    workflow, _, planned = workflow_target
    store = PrototypePublications(workflow.state_dir)
    value = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="002",
        base_revision="001",
    )
    ref = value["publication_ref"]
    with pytest.raises(
        PrototypePublicationRejected, match="materialization unavailable"
    ):
        store.ready(ref)
    ready = store.materialize(ref)
    workflow.commit_prepared_prototype(
        "scenario", "publication-test", ref, expected_generation=planned["generation"]
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    edit_stock(runtime, ready, "Mutable edits are allowed")
    assert store.ready(ref)["materialization"] == ready
    state = runtime.snapshot(stock(ready))
    state["definition"]["metadata"]["revision"] = "999"
    runtime._store().put(stock(ready), state)
    with pytest.raises(PrototypePublicationRejected, match="bundle changed or missing"):
        store.ready(ref)
    with pytest.raises(PrototypeResourceConflict, match="bundle differs"):
        edit_stock(runtime, ready, "Corrupt definition must not execute")


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
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    assert (
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )["duplicate"]
        is True
    )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before


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
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    with pytest.raises(
        PrototypePublicationRejected, match="materialization unavailable"
    ):
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before
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
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    with pytest.raises(BuilderWorkflowError, match="exact generation"):
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=wrong_generation,
        )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before


def test_private_transition_hook_cannot_bypass_managed_commit_guard(
    workflow_target, document
):
    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    before = workflow._state_path("scenario", "publication-test").read_bytes()
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
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before


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


def test_history_ignores_orphan_preparations_even_when_fully_materialized(
    workflow_target, document
):
    workflow, _, _ = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    history = PrototypeHistoryService(workflow)
    listed = history.list("scenario", "publication-test")
    assert listed["current_revision"] == "001"
    assert not any(
        row.get("publication_ref") == value["publication_ref"]
        for row in listed["items"]
    )
    with pytest.raises(FileNotFoundError):
        history.read("scenario", "publication-test", "002")


def test_history_reads_only_committed_lineage_not_orphan_or_mutable_mirrors(
    workflow_target, document, monkeypatch
):
    workflow, root, planned = workflow_target
    store, first = prepare_for_workflow(workflow, document)
    accepted = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    successor = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=first["publication_ref"],
    )
    store.materialize(successor["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        successor["publication_ref"],
        expected_generation=accepted["workflow"]["generation"],
    )
    orphan = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="004",
        base_revision="003",
        previous_publication_ref=successor["publication_ref"],
    )
    store.materialize(orphan["publication_ref"])
    (root / "webui.json").write_text('{"mutable":"unaccepted"}', encoding="utf-8")
    for revision in ("002", "003"):
        (root / "ui_revisions" / (revision + ".json")).write_text(
            '{"mutable":"unaccepted"}', encoding="utf-8"
        )
    (root / "ui_revisions/004.json").write_text(
        '{"orphan":"not accepted"}', encoding="utf-8"
    )
    history = PrototypeHistoryService(workflow)
    state_bytes = workflow._state_path("scenario", "publication-test").read_bytes()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            PrototypePublications,
            "load",
            lambda *a: pytest.fail("list loaded full snapshot"),
        )
        listed = history.list("scenario", "publication-test")
    assert [row["revision"] for row in listed["items"]] == ["003", "002", "001"]
    assert listed["current"]["authority"] == "committed_publication"
    assert listed["current"]["created_at"]
    assert not listed["missing_revisions"]
    for revision, value in (("002", first), ("003", successor)):
        snapshot = history.read("scenario", "publication-test", revision)
        assert snapshot["after_webui"] == value["compiled"]["webui"]
        assert snapshot["publication_ref"] == value["publication_ref"]
        assert "brief" not in snapshot and "document" not in snapshot
    assert (
        workflow._state_path("scenario", "publication-test").read_bytes() == state_bytes
    )
    with pytest.raises(FileNotFoundError):
        history.read("scenario", "publication-test", "004")


def test_missing_committed_snapshot_is_reported_missing_not_replaced_by_source(
    workflow_target, document
):
    workflow, _, planned = workflow_target
    store, value = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    store._path(value["publication_ref"]).unlink()
    history = PrototypeHistoryService(workflow)
    listed = history.list("scenario", "publication-test")
    assert listed["current_revision"] == "002"
    assert listed["current"]["availability"] == "missing"
    assert "002" in listed["missing_revisions"]
    with pytest.raises(PrototypePublicationRejected, match="preparation unavailable"):
        history.read("scenario", "publication-test", "002")


def test_tampered_lineage_index_is_not_history_authority(workflow_target, document):
    workflow, _, planned = workflow_target
    store, value = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    path = store._path(value["publication_ref"], ".index.json")
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["seed"]["revision"] = "999"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(PrototypePublicationRejected, match="index unavailable"):
        PrototypeHistoryService(workflow).list("scenario", "publication-test")


def test_adopted_publication_head_and_history_survive_full_dev_source_loss(
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
    head = workflow._state_path("scenario", "publication-test")
    assert head.is_relative_to(Path(workflow.state_dir))
    before = head.read_bytes()
    root.rename(root.parent / "publication-test-source-away")
    assert not root.exists()
    assert workflow.current_prototype_revision("scenario", "publication-test") == "002"
    assert (
        workflow._prototype_webui_snapshot("scenario", "publication-test")[0]
        == value["compiled"]["webui"]
    )
    history = PrototypeHistoryService(workflow)
    assert history.list("scenario", "publication-test")["current_revision"] == "002"
    assert (
        history.read("scenario", "publication-test", "002")["after_webui"]
        == value["compiled"]["webui"]
    )
    replay = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert replay["duplicate"] is True
    assert head.read_bytes() == before
    assert not root.exists()  # No reader/commit replay restores disposable sources.


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_damaged_adopted_authority_never_uses_existing_legacy_source(
    workflow_target, document, damage
):
    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert (
        root / "prompt_state.json"
    ).exists()  # Deliberately leave the old mirror intact.
    head = workflow._state_path("scenario", "publication-test")
    if damage == "missing":
        head.unlink()
    else:
        head.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        workflow.current_prototype_revision("scenario", "publication-test")
    with pytest.raises(ValueError):
        PrototypeHistoryService(workflow).list("scenario", "publication-test")


def test_failed_initial_adoption_keeps_prior_authority_and_can_retry(
    workflow_target, document, monkeypatch
):
    import adaos.services.builder.workflow_authority as module

    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    original = (root / "prompt_state.json").read_bytes()

    def fail_copy(*_):
        raise OSError("controlled initial authority copy failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(module, "atomic_write_json", fail_copy)
        with pytest.raises(OSError, match="authority copy failure"):
            workflow.commit_prepared_prototype(
                "scenario",
                "publication-test",
                value["publication_ref"],
                expected_generation=planned["generation"],
            )
    assert (
        workflow._state_path("scenario", "publication-test")
        == root / "prompt_state.json"
    )
    assert (root / "prompt_state.json").read_bytes() == original
    assert workflow.current_prototype_revision("scenario", "publication-test") == "001"
    assert (
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            value["publication_ref"],
            expected_generation=planned["generation"],
        )["committed"]
        is True
    )


def test_interrupted_adoption_retry_preserves_late_observation_in_authoritative_workflow(
    workflow_target, document, monkeypatch
):
    import adaos.services.builder.workflow_authority as module

    workflow, root, planned = workflow_target
    _, value = prepare_for_workflow(workflow, document)
    write = module.atomic_write_json

    def fail_receipt(path, payload):
        if path.name == "adoption.json":
            raise OSError("controlled adoption fence failure")
        return write(path, payload)

    with monkeypatch.context() as scoped:
        scoped.setattr(module, "atomic_write_json", fail_receipt)
        with pytest.raises(OSError, match="adoption fence failure"):
            workflow.commit_prepared_prototype(
                "scenario",
                "publication-test",
                value["publication_ref"],
                expected_generation=planned["generation"],
            )
    assert workflow._state_path("scenario", "publication-test").is_relative_to(
        Path(workflow.state_dir)
    )
    late = workflow._read_state("scenario", "publication-test")
    late["late_observation"] = {"original_run": "controlled-run", "calls": 1}
    workflow._write_state("scenario", "publication-test", late)
    (root / "prompt_state.json").write_text('{"stale":"DEV mirror"}', encoding="utf-8")
    result = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    assert result["committed"] is True
    assert (
        workflow._read_state("scenario", "publication-test")["late_observation"]
        == late["late_observation"]
    )


def test_stale_published_view_cannot_mutate_after_successor_commit(
    workflow_target, document
):
    workflow, _, planned = workflow_target
    store, first = prepare_for_workflow(workflow, document)
    accepted = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    previous = store.ready(first["publication_ref"])["materialization"]
    edit_stock(runtime, previous, "Accepted current view write")
    second = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=first["publication_ref"],
    )
    current = store.materialize(second["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        second["publication_ref"],
        expected_generation=accepted["workflow"]["generation"],
    )
    before = runtime.snapshot(stock(previous))
    active_before = runtime.snapshot(stock(current))
    with pytest.raises(PrototypeResourceConflict, match="stale|current|committed"):
        edit_stock(runtime, previous, "Write through stale browser")
    assert runtime.snapshot(stock(previous)) == before
    assert runtime.snapshot(stock(current)) == active_before
    assert runtime.query(stock(previous), filters={}, search="", sort=[], limit=100)


def test_prepared_uncommitted_view_cannot_mutate_runtime(tmp_path, document):
    store = PrototypePublications(tmp_path)
    value = prepare(store, document)
    ready = store.materialize(value["publication_ref"])
    runtime = PrototypeResourceService(state_dir=tmp_path)
    before = runtime.snapshot(stock(ready))
    with pytest.raises(PrototypeResourceConflict, match="current|committed"):
        edit_stock(runtime, ready, "Uncommitted view write")
    assert runtime.snapshot(stock(ready)) == before


def test_ui_only_successor_carries_current_runtime_records(workflow_target, document):
    workflow, _, planned = workflow_target
    store, first = prepare_for_workflow(workflow, document)
    accepted = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    previous = store.ready(first["publication_ref"])["materialization"]
    edit_stock(runtime, previous, "Operator data must survive UI changes")
    before = runtime.snapshot(stock(previous))
    changed = copy.deepcopy(document)
    changed["title"]["en"] = "Inventory overview"
    second = store.prepare(
        changed,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=first["publication_ref"],
    )
    successor = store.materialize(second["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        second["publication_ref"],
        expected_generation=accepted["workflow"]["generation"],
    )
    assert runtime.snapshot(stock(successor))["records"] == before["records"]
    assert runtime.snapshot(stock(previous)) == before
    # Runtime values are local initial state, never a rewrite of semantic seeds.
    assert second["document"] == changed
    assert (
        second["compiled"]["semantic_document"]["resources"][0]["records"]
        == document["resources"][0]["records"]
    )
    assert (
        runtime.snapshot(stock(successor))["data_definition"]["seed"][0]["reserve_note"]
        == ""
    )


def test_changed_runtime_base_refuses_commit_and_refreshes_without_inference(
    workflow_target,
    document,
):
    workflow, _, planned = workflow_target
    store, first = prepare_for_workflow(workflow, document)
    accepted = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    previous = store.ready(first["publication_ref"])["materialization"]
    kwargs = dict(
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=first["publication_ref"],
    )
    pending = store.prepare(document, **kwargs)
    old_pending = store.materialize(pending["publication_ref"])
    edit_stock(runtime, previous, "Write after fork was captured")
    before = workflow._state_path("scenario", "publication-test").read_bytes()
    with pytest.raises(PrototypePublicationRejected, match="base changed"):
        workflow.commit_prepared_prototype(
            "scenario",
            "publication-test",
            pending["publication_ref"],
            expected_generation=accepted["workflow"]["generation"],
        )
    assert workflow._state_path("scenario", "publication-test").read_bytes() == before
    refreshed = store.prepare(document, **kwargs)
    assert refreshed["publication_ref"] != pending["publication_ref"]
    assert store.prepare(document, **kwargs) == refreshed
    active = store.materialize(refreshed["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        refreshed["publication_ref"],
        expected_generation=accepted["workflow"]["generation"],
    )
    assert (
        runtime.snapshot(stock(active))["records"]
        == runtime.snapshot(stock(previous))["records"]
    )
    with pytest.raises(PrototypeResourceConflict, match="committed"):
        edit_stock(runtime, old_pending, "Superseded pending view")


def test_single_resource_v1_fork_preserves_added_records(workflow_target, document):
    single = copy.deepcopy(document)
    single["schema"] = "adaos.webui.semantic.v1"
    single["resource"] = single.pop("resources")[0]
    single.pop("relationships")
    single["views"] = [
        view for view in single["views"] if view.pop("resource_ref") == "stock"
    ]
    for view in single["views"]:
        view.pop("scope_filters", None)
        view.pop("surface", None)
    for command in single["commands"]:
        command.pop("exposure", None)
    workflow, _, planned = workflow_target
    store, first = prepare_for_workflow(workflow, single)
    accepted = workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    previous = store.ready(first["publication_ref"])["materialization"]
    runtime.operate(
        stock(previous),
        "create",
        record_id=None,
        command_id="stock-create",
        payload={
            "record": {
                "id": "operator-created",
                "sku": "SKU-C",
                "status": "available",
                "warehouse_id": "warehouse-1",
                "quantity": 3,
                "reserve_note": "Created locally",
            }
        },
    )
    before = runtime.snapshot(stock(previous))
    second = store.prepare(
        single,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=first["publication_ref"],
    )
    ready = store.materialize(second["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        second["publication_ref"],
        expected_generation=accepted["workflow"]["generation"],
    )
    assert runtime.snapshot(stock(ready))["records"] == before["records"]
    assert len(runtime.snapshot(stock(ready))["records"]) == 3
    assert len(runtime.snapshot(stock(ready))["data_definition"]["seed"]) == 2


@pytest.mark.parametrize("initial", [None, [], [{"id": "forged"}]])
def test_fork_materialization_requires_exact_initial_records(
    workflow_target,
    document,
    initial,
):
    workflow, _, planned = workflow_target
    store, first = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    successor = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=first["publication_ref"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    spec = successor["resource_specs"][0]
    with pytest.raises(PrototypeResourceConflict, match="initial records differ"):
        runtime.materialize(
            {
                "schema": "adaos.builder.prototype_resource.v1",
                "project_ref": "scenario:publication-test",
                "change_id": "CH-publication",
                "revision": "003",
                "webui_digest": successor["webui_digest"],
                **spec,
            },
            _initial_records=initial,
        )
    assert runtime.snapshot(spec["resource_definition"]["resource_type"]) is None


@pytest.mark.parametrize(
    "incompatibility", ["schema", "identity", "fixtures", "resources", "relationships"]
)
def test_runtime_fork_refuses_implicit_data_migration(
    workflow_target,
    document,
    incompatibility,
):
    workflow, _, planned = workflow_target
    store, first = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        first["publication_ref"],
        expected_generation=planned["generation"],
    )
    changed = copy.deepcopy(document)
    if incompatibility == "schema":
        changed["resources"][0]["fields"].append(
            {
                "id": "extra",
                "label": {"key": "extra", "en": "Extra", "ru": "Extra"},
                "value_type": "short_text",
                "required": False,
                "editable": True,
            }
        )
    elif incompatibility == "identity":
        changed["resources"][0]["identity_field_refs"] = ["sku"]
    elif incompatibility == "fixtures":
        changed["resources"][0]["records"][0]["reserve_note"] = "New source fixture"
    elif incompatibility == "resources":
        changed["resources"].pop()
        changed["relationships"] = []
        changed["views"] = [
            view for view in changed["views"] if view["resource_ref"] != "warehouses"
        ]
    else:
        changed["relationships"][0]["cardinality"] = "one_to_one"
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    before = runtime._store().states()
    files_before = set(store.root.glob("*.json"))
    with pytest.raises(
        PrototypePublicationRejected, match="explicit .*migration|explicit fixture"
    ):
        store.prepare(
            changed,
            project_ref="scenario:publication-test",
            change_id="CH-publication",
            revision="003",
            base_revision="002",
            previous_publication_ref=first["publication_ref"],
        )
    assert runtime._store().states() == before
    assert set(store.root.glob("*.json")) == files_before


@pytest.mark.parametrize("strip_scope", [False, True])
def test_published_bundle_cannot_be_redefined_or_downgraded(
    workflow_target, document, strip_scope
):
    workflow, _, planned = workflow_target
    store, value = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    ready = store.ready(value["publication_ref"])["materialization"]
    before = runtime.snapshot(stock(ready))
    spec = copy.deepcopy(value["resource_specs"][0])
    if strip_scope:
        spec["resource_definition"]["metadata"].pop("prototype_publication_ref")
    with pytest.raises(PrototypeResourceConflict, match="immutable"):
        runtime.materialize(
            {
                "schema": "adaos.builder.prototype_resource.v1",
                "project_ref": "scenario:publication-test",
                "change_id": "CH-publication",
                "revision": "999",
                "webui_digest": value["webui_digest"],
                **spec,
            }
        )
    assert runtime.snapshot(stock(ready)) == before


def test_legacy_dataset_cannot_write_after_opt_in_publication(
    workflow_target, document
):
    from adaos.services.builder.semantic_prototype import compile_semantic_prototype
    from adaos.sdk.developer.prototypes import derive_resource_specs
    from adaos.services.resources.prototype import prototype_webui_digest

    workflow, _, planned = workflow_target
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    legacy = compile_semantic_prototype(
        document, project_ref="scenario:publication-test"
    )
    specs = derive_resource_specs(
        legacy["webui"], resources=legacy["prototype_resources"]
    )
    identity = {
        "schema": "adaos.builder.prototype_resource.v1",
        "project_ref": "scenario:publication-test",
        "change_id": "legacy",
        "revision": "001",
        "webui_digest": prototype_webui_digest(legacy["webui"]),
    }
    rows = [runtime.materialize({**identity, **spec})["state"] for spec in specs]
    legacy_view = {
        "resources": [{"resource_type": row["resource_type"]} for row in rows]
    }
    edit_stock(runtime, legacy_view, "Legacy current view works before adoption")
    _, value = prepare_for_workflow(workflow, document)
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )
    before = runtime.snapshot(stock(legacy_view))
    with pytest.raises(PrototypeResourceConflict, match="stale"):
        edit_stock(runtime, legacy_view, "Legacy view after adoption")
    with pytest.raises(PrototypeResourceConflict, match="stale"):
        runtime.materialize({**identity, **specs[0], "revision": "999"})
    assert runtime.snapshot(stock(legacy_view)) == before
    assert runtime.query(stock(legacy_view), filters={}, search="", sort=[], limit=100)
