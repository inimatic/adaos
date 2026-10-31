import copy
import json

import pytest

from adaos.services.builder.authoring_scope import (
    AuthoringScopeRejected,
    resolve_authoring_scope,
)
from adaos.services.builder.prototype_history import PrototypeHistoryService
from adaos.services.builder.prototype_publications import (
    PrototypePublications,
    PrototypePublicationRejected,
)
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.builder.workflow_authority import WorkflowAuthority
from adaos.services.builder.workflow import resolve_prototype_resource_owner
from adaos.services.resources.prototype import (
    PrototypeResourceService,
    PrototypeResourceConflict,
)
from test_builder_prototype_publications import (
    workflow_target,
    document,
    stock,
    edit_stock,
)


@pytest.fixture
def scoped(workflow_target, document):
    workflow, _, planned = workflow_target
    root = workflow.dev_projects_root / "publication-app"
    root.mkdir(parents=True)
    manifest = {
        "schema": "adaos.project.v1",
        "kind": "project",
        "id": "publication-app",
        "version": "0.1.0",
        "profiles": [],
        "components": {
            "owned": [
                {
                    "ref": "scenario:publication-test",
                    "role": "primary",
                    "exposure": "application",
                    "lifecycle": "bound",
                    "relations": ["uses"],
                }
            ],
            "dependencies": [],
        },
        "entrypoints": [
            {
                "id": "main",
                "presentation": "scenario:publication-test",
                "default": True,
                "bindings": {},
            }
        ],
        "catalog": {
            "title": "Fixture",
            "description": "",
            "categories": [],
            "tags": [],
        },
        "lifecycle": {
            "uninstall": {
                "components": "remove_if_unreferenced",
                "runtime_data": "retain",
                "source_artifacts": "retain",
            }
        },
    }
    path = root / "project.yaml"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    scope = resolve_authoring_scope(workflow, "project:publication-app")
    store = PrototypePublications(workflow.state_dir)
    value = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="002",
        base_revision="001",
        source_scope=scope,
    )
    return workflow, planned, store, value, scope, path


def accept(scoped):
    workflow, planned, store, value, *_ = scoped
    store.materialize(value["publication_ref"])
    return workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        value["publication_ref"],
        expected_generation=planned["generation"],
    )


def test_project_runtime_scope_and_scenario_head_share_one_qualified_view(
    scoped, document
):
    workflow, _, store, value, scope, _ = scoped
    expected = compile_semantic_prototype(
        document,
        project_ref="project:publication-app",
        runtime_scope_ref=value["publication_ref"],
    )
    assert value["compiled"] == expected
    assert value["seed"]["project_ref"] == "scenario:publication-test"
    assert value["seed"]["source_scope"] == scope
    accept(scoped)
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    ready = store.ready(value["publication_ref"])["materialization"]
    state = runtime.snapshot(stock(ready))
    assert state["project_ref"] == "project:publication-app"
    assert state["workflow_ref"] == "scenario:publication-test"
    assert (
        state["definition"]["metadata"]["prototype_workflow_ref"]
        == "scenario:publication-test"
    )
    edit_stock(runtime, ready, "Owned by the app, admitted by its Scenario")
    assert runtime.snapshot(stock(ready))["records"][0]["reserve_note"].startswith(
        "Owned by"
    )
    assert (
        PrototypeHistoryService(workflow).read("project", "publication-app", "002")[
            "publication_ref"
        ]
        == value["publication_ref"]
    )
    assert (
        WorkflowAuthority(workflow.state_dir).resolve("project:publication-app") is None
    )
    assert not (
        workflow.dev_projects_root / "publication-app" / "prompt_state.json"
    ).exists()
    resource_types = workflow._prototype_resource_types(value["compiled"]["webui"])
    owner = resolve_prototype_resource_owner(
        runtime,
        resource_types,
        component_ref="scenario:publication-test",
        dev_projects_root=workflow.dev_projects_root,
    )
    assert owner == "project:publication-app"
    assert runtime.acceptance_snapshots(
        project_ref=owner,
        change_id="CH-publication",
        revision="002",
        webui_digest=value["webui_digest"],
        resource_types=resource_types,
    )


def test_scoped_successor_preserves_data_and_fences_old_view(scoped, document):
    workflow, _, store, value, scope, _ = scoped
    first = accept(scoped)
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    previous = store.ready(value["publication_ref"])["materialization"]
    edit_stock(runtime, previous, "Preserve logical Project data")
    before = runtime.snapshot(stock(previous))
    successor = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="003",
        base_revision="002",
        previous_publication_ref=value["publication_ref"],
        source_scope=scope,
    )
    current = store.materialize(successor["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        successor["publication_ref"],
        expected_generation=first["workflow"]["generation"],
    )
    assert runtime.snapshot(stock(current))["records"] == before["records"]
    assert runtime.snapshot(stock(current))["project_ref"] == before["project_ref"]
    with pytest.raises(PrototypeResourceConflict, match="stale|committed"):
        edit_stock(runtime, previous, "Old Project view")


@pytest.mark.parametrize("drift", ["metadata", "primary", "missing"])
def test_scope_manifest_recheck_refuses_before_authoring(scoped, drift):
    workflow, _, store, value, _, path = scoped
    store.materialize(value["publication_ref"])
    state_path = workflow._state_path("scenario", "publication-test")
    before = state_path.read_bytes()
    if drift == "missing":
        path.unlink()
    else:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if drift == "primary":
            manifest["components"]["owned"][0]["ref"] = "scenario:another"
            manifest["entrypoints"][0]["presentation"] = "scenario:another"
        else:
            manifest["catalog"]["title"] = "Changed after snapshot"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AuthoringScopeRejected):
        accept(scoped)
    assert state_path.read_bytes() == before
    assert (
        WorkflowAuthority(workflow.state_dir).resolve("scenario:publication-test")
        is None
    )


@pytest.mark.parametrize("corruption", ["strip_metadata", "owner", "strip_state_pin"])
def test_scoped_mutation_rejects_corrupt_owner_without_legacy_downgrade(
    scoped, corruption
):
    workflow, _, store, value, *_ = scoped
    accept(scoped)
    runtime = PrototypeResourceService(state_dir=workflow.state_dir)
    ready = store.ready(value["publication_ref"])["materialization"]
    state = runtime.snapshot(stock(ready))
    if corruption == "strip_metadata":
        state["definition"]["metadata"] = {}
    elif corruption == "owner":
        state["definition"]["metadata"]["prototype_workflow_ref"] = "scenario:another"
    else:
        state.pop("workflow_ref")
    runtime._store().put(stock(ready), state)
    before = runtime.snapshot(stock(ready))
    with pytest.raises(PrototypeResourceConflict):
        edit_stock(runtime, ready, "Do not fall back to unadopted Project")
    assert runtime.snapshot(stock(ready)) == before
    with pytest.raises(PrototypePublicationRejected):
        store.ready(value["publication_ref"])


def test_source_scope_cannot_silently_move_existing_runtime_namespace(
    workflow_target, document, scoped
):
    # Use the scoped fixture's same workflow/owner but establish an unscoped
    # accepted base first. Adopting Project runtime ownership is a migration.
    workflow, planned, store, _, scope, _ = scoped
    previous = store.prepare(
        document,
        project_ref="scenario:publication-test",
        change_id="CH-publication",
        revision="002",
        base_revision="001",
    )
    store.materialize(previous["publication_ref"])
    workflow.commit_prepared_prototype(
        "scenario",
        "publication-test",
        previous["publication_ref"],
        expected_generation=planned["generation"],
    )
    with pytest.raises(
        PrototypePublicationRejected, match="ownership namespace migration"
    ):
        store.prepare(
            document,
            project_ref="scenario:publication-test",
            change_id="CH-publication",
            revision="003",
            base_revision="002",
            previous_publication_ref=previous["publication_ref"],
            source_scope=scope,
        )


@pytest.mark.parametrize("fault", ["object_id", "manifest_digest", "unknown"])
def test_preparation_refuses_malformed_source_scope(scoped, document, fault):
    _, _, store, _, scope, _ = scoped
    bad = copy.deepcopy(scope)
    bad[fault] = "invalid"
    with pytest.raises(PrototypePublicationRejected, match="source-scope"):
        store.prepare(
            document,
            project_ref="scenario:publication-test",
            change_id="CH-publication",
            revision="002",
            base_revision="001",
            source_scope=bad,
        )
