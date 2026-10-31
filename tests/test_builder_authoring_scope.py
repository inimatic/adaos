import copy
import json

import pytest

from adaos.services.builder.authoring_scope import (
    AuthoringScopeRejected,
    resolve_authoring_scope,
    verify_frozen_authoring_scope,
)
from adaos.services.builder_context import digest
from test_builder_workflow import workflow_project as _workflow_project

workflow_project = _workflow_project


def state_bytes(service):
    path = service._state_path("scenario", "recipes")
    return path.read_bytes() if path.is_file() else None


@pytest.fixture
def scoped_project(workflow_project):
    service, _ = workflow_project
    root = service.dev_projects_root / "qualification"
    root.mkdir(parents=True)
    manifest = {
        "schema": "adaos.project.v1",
        "kind": "project",
        "id": "qualification",
        "version": "0.1.0",
        "profiles": [],
        "components": {
            "owned": [
                {
                    "ref": "scenario:recipes",
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
                "presentation": "scenario:recipes",
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
    return service, path, manifest


@pytest.mark.parametrize("kind", ["scenario", "skill"])
def test_direct_component_resolution_has_no_manifest_or_authoring_effects(
    workflow_project, kind
):
    service, _ = workflow_project
    before = state_bytes(service)
    assert resolve_authoring_scope(service, f"{kind}:recipes") == {
        "project_ref": f"{kind}:recipes",
        "object_type": kind,
        "object_id": "recipes",
        "manifest_digest": None,
    }
    assert state_bytes(service) == before


def test_project_scope_resolves_exact_primary_without_moving_workflow(scoped_project):
    service, _, manifest = scoped_project
    before = state_bytes(service)
    scope = resolve_authoring_scope(service, "project:qualification")
    assert scope == {
        "project_ref": "project:qualification",
        "object_type": "scenario",
        "object_id": "recipes",
        "manifest_digest": digest(manifest),
    }
    assert verify_frozen_authoring_scope(service, scope) == scope
    assert state_bytes(service) == before
    assert not (
        service.dev_projects_root / "qualification" / "prompt_state.json"
    ).exists()


@pytest.mark.parametrize("change", ["primary", "metadata", "missing", "corrupt"])
def test_frozen_authoring_scope_refuses_changed_manifest(scoped_project, change):
    service, path, manifest = scoped_project
    scope = resolve_authoring_scope(service, "project:qualification")
    if change == "primary":
        manifest["components"]["owned"][0]["ref"] = "scenario:other"
        manifest["entrypoints"][0]["presentation"] = "scenario:other"
    elif change == "metadata":
        manifest["catalog"]["title"] = "Changed after admission"
    elif change == "missing":
        path.unlink()
    else:
        path.write_text("[", encoding="utf-8")
    if change in {"primary", "metadata"}:
        path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AuthoringScopeRejected):
        verify_frozen_authoring_scope(service, scope)


@pytest.mark.parametrize("fault", ["duplicate", "absent", "wrong_id", "oversized"])
def test_scope_refuses_ambiguous_unowned_or_invalid_project(scoped_project, fault):
    service, path, manifest = scoped_project
    if fault == "duplicate":
        other = copy.deepcopy(manifest["components"]["owned"][0])
        other["ref"] = "scenario:other"
        manifest["components"]["owned"].append(other)
    elif fault == "absent":
        manifest["components"]["owned"][0]["role"] = "implementation"
    elif fault == "wrong_id":
        manifest["id"] = "another-project"
    if fault == "oversized":
        path.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    else:
        path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AuthoringScopeRejected):
        resolve_authoring_scope(service, "project:qualification")


@pytest.mark.parametrize(
    "reference",
    [None, "", "project", "unknown:recipes", "scenario:../recipes", "skill:"],
)
def test_scope_rejects_invalid_reference_without_source_lookup(
    workflow_project, reference
):
    service, _ = workflow_project
    with pytest.raises(AuthoringScopeRejected):
        resolve_authoring_scope(service, reference)
