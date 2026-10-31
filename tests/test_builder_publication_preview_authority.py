import copy
import json
from types import SimpleNamespace

import pytest
from test_builder_generation_authoring import ready_result
from test_builder_workflow import workflow_project as _workflow_project

from adaos.sdk.builder import prototype
from adaos.services.builder.prototype_publications import PrototypePublications
from adaos.services.scenario.webspace_components.builder_publication import (
    WebspaceBuilderPublicationService,
)

workflow_project = _workflow_project


@pytest.fixture
def committed(workflow_project, tmp_path, monkeypatch):
    workflow, root = workflow_project
    monkeypatch.setattr(
        "adaos.services.runtime_paths.current_state_dir", lambda: workflow.state_dir
    )
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: workflow)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    arguments = ready_result(workflow, tmp_path / "jobs")
    prepared = prototype.prepare_generation_publication(**arguments)
    prototype.commit_generation_publication(
        **arguments, publication_ref=prepared["publication_ref"]
    )
    legacy = root / "ui_revisions" / (prepared["revision"] + ".json")
    legacy.write_text(
        json.dumps(
            {
                "after_webui": {
                    "ui": {
                        "application": {
                            "desktop": {
                                "pageSchema": {
                                    "id": "forged",
                                    "title": "Unaccepted legacy override",
                                    "widgets": [
                                        {"id": "forged-only", "type": "ui.form"}
                                    ],
                                }
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    operations = SimpleNamespace(
        clone_json_like=copy.deepcopy,
        scenarios_loader=SimpleNamespace(
            scenario_root_for_space=lambda *args: root,
            read_content=lambda *args, **kwargs: pytest.fail(
                "Accepted Preview fell back to mutable source"
            ),
        ),
    )
    return workflow, root, prepared, operations


def preview(prepared, operations):
    return WebspaceBuilderPublicationService().preview_content_override(
        "recipes",
        stage="prototype",
        revision=prepared["revision"],
        label=None,
        operations=operations,
    )


def test_committed_preview_cannot_be_replaced_by_same_revision_legacy_file(committed):
    _, root, prepared, operations = committed
    expected = prototype.generation_publication_projection(
        publication_ref=prepared["publication_ref"]
    )["webui"]
    before = (root / "ui_revisions" / (prepared["revision"] + ".json")).read_bytes()
    rendered, space = preview(prepared, operations)
    page = rendered["ui"]["application"]["desktop"]["pageSchema"]
    assert (
        page["widgets"]
        == expected["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    )
    assert page["_adaos"]["releaseStage"] == "ALPHA" and space == "dev"
    assert (
        root / "ui_revisions" / (prepared["revision"] + ".json")
    ).read_bytes() == before


def test_missing_committed_snapshot_cannot_use_existing_legacy_preview(committed):
    workflow, _, prepared, operations = committed
    store = PrototypePublications(workflow.state_dir)
    store._path(prepared["publication_ref"]).unlink()
    with pytest.raises(ValueError):
        preview(prepared, operations)


def test_committed_preview_does_not_require_runtime_dev_source_lookup(committed):
    _, _, prepared, operations = committed

    def unavailable(*args):
        raise FileNotFoundError("controlled unavailable DEV source")

    operations.scenarios_loader.scenario_root_for_space = unavailable
    rendered, space = preview(prepared, operations)
    assert (
        rendered["ui"]["application"]["desktop"]["pageSchema"]["_adaos"]["releaseStage"]
        == "ALPHA"
    )
    assert space == "dev"


def test_unpinned_prototype_preview_resolves_the_committed_current_revision(committed):
    _, _, prepared, operations = committed
    rendered, space = WebspaceBuilderPublicationService().preview_content_override(
        "recipes", stage="prototype", revision=None, label=None, operations=operations
    )
    page = rendered["ui"]["application"]["desktop"]["pageSchema"]
    assert page["_adaos"]["materialization"]["revision"] == prepared["revision"]
    assert page["_adaos"]["releaseStage"] == "ALPHA" and space == "dev"


def test_inconsistent_committed_head_cannot_downgrade_to_unpinned_dev_source(committed):
    workflow, _, _, operations = committed
    state = workflow._read_state("scenario", "recipes")
    state["workflow"]["prototype"]["head_revision"] = "999"
    workflow._write_state("scenario", "recipes", state)
    with pytest.raises(ValueError):
        WebspaceBuilderPublicationService().preview_content_override(
            "recipes",
            stage="prototype",
            revision=None,
            label=None,
            operations=operations,
        )
