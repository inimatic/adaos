"""Source-bound delivery must not confuse commit, scheduling and owner acceptance."""
import copy
from types import SimpleNamespace

import pytest
from test_builder_publication_preview_authority import committed as _committed
from test_builder_workflow import workflow_project as _workflow_project

from adaos.sdk.builder import preview, prototype
from adaos.services.resources.prototype import prototype_webui_digest
from adaos.services.scenario.webspace_components.builder_publication import (
    WebspaceBuilderPublicationService,
)

workflow_project = _workflow_project
committed = _committed


@pytest.fixture
def delivery(monkeypatch):
    publication = {
        "publication_ref": "prototype-publication:" + "a" * 24,
        "project_ref": "scenario:screen", "is_current": True,
        "prototype_stage": "alpha", "revision": "002",
        "source_scope": {"object_type": "scenario", "object_id": "screen",
                         "project_ref": "project:app", "manifest_digest": "frozen"},
    }
    binding = {"preview_webspace_id": "host-dev", "preview_target": {
        "object_type": "project", "object_id": "app", "scenario_id": "screen",
        "stage": "prototype", "revision": "001", "follow_active": True,
    }}
    calls = []
    response = {"ok": True, "accepted": True, "action": "builder_revision_apply",
                "webspace_id": "host-dev", "scenario_id": "screen", "request_id": "owner-1",
                "materialization_identity": {
                    "webspace_id": "host-dev", "scenario_id": "screen", "revision": "002",
                    "source_fingerprint": "prototype:sha256:" + "c" * 64}}

    def refresh(*args, **kwargs):
        calls.append(("refresh", args, kwargs))
        binding["preview_target"]["revision"] = kwargs["revision"]
        return {"ok": True, "target": {**binding["preview_target"], "label": "proto: app · UI 002"}}

    def materialize(**kwargs):
        calls.append(("owner", kwargs))
        return copy.deepcopy(response)

    monkeypatch.setattr(prototype, "generation_publication_projection", lambda **kwargs: copy.deepcopy(publication))
    monkeypatch.setattr(preview, "canonical_source_webspace_id", lambda source: source)
    monkeypatch.setattr(preview, "get_binding", lambda source: copy.deepcopy(binding))
    monkeypatch.setattr(preview, "refresh_follow_active_target", refresh)
    monkeypatch.setattr(preview, "materialize_revision_via_owner", materialize)
    monkeypatch.setattr(preview, "materialize_revision", lambda **kwargs: pytest.fail("Detached YDoc write"))
    return publication, binding, response, calls


def deliver(publication):
    return preview.deliver_generation_publication(
        publication_ref=publication["publication_ref"], source_webspace_id="host")


def test_exact_accepted_publication_is_delivered_as_prototype_without_duplicate_event(delivery):
    publication, binding, _, calls = delivery
    before = copy.deepcopy(publication)
    result = deliver(publication)
    assert result["delivered"] is True
    assert result["publication_ref"] == publication["publication_ref"]
    assert calls[0][2]["publish_event"] is False
    assert calls[1][1]["revision"] == "002"
    assert calls[1][1]["preview_stage"] == "prototype"
    assert binding["preview_target"]["object_type"] == "project"
    assert publication == before  # A projection is not another generation/head write.
    assert deliver(publication)["delivered"] is True
    assert [call[0] for call in calls] == ["refresh", "owner", "refresh", "owner"]


@pytest.mark.parametrize("fault", ["historical", "stage", "owner", "no_revision"])
def test_unaccepted_or_wrong_scope_never_reaches_preview(delivery, fault):
    publication, _, _, calls = delivery
    if fault == "historical":
        publication["is_current"] = False
    elif fault == "stage":
        publication["prototype_stage"] = "beta"
    elif fault == "owner":
        publication["project_ref"] = "scenario:other"
    else:
        publication["revision"] = ""
    with pytest.raises(ValueError, match="Current accepted"):
        deliver(publication)
    assert not calls


@pytest.mark.parametrize("fault", ["pin", "stage", "project", "scenario", "logical_owner", "no_relation"])
def test_pinned_or_unrelated_preview_is_not_changed(delivery, fault):
    publication, binding, _, calls = delivery
    target = binding["preview_target"]
    if fault == "pin":
        target["follow_active"] = False
    elif fault == "stage":
        target["stage"] = "automation"
    elif fault == "project":
        target["object_id"] = "other"
    elif fault == "scenario":
        target.update(object_type="scenario", object_id="other")
    elif fault == "logical_owner":
        publication["source_scope"]["project_ref"] = "project:other"
    else:
        binding.pop("preview_webspace_id")
    before = copy.deepcopy(binding)
    assert deliver(publication)["delivered"] is False
    assert not calls and binding == before


@pytest.mark.parametrize("fault", ["queued", "rejected", "no_acceptance", "revision", "webspace", "scenario", "stage", "identity"])
def test_scheduled_or_different_owner_receipt_cannot_claim_delivery(delivery, fault):
    publication, _, response, _ = delivery
    if fault == "queued":
        response["scheduled"] = True
    elif fault == "rejected":
        response["accepted"] = False
    elif fault == "no_acceptance":
        response.pop("accepted")
    elif fault == "revision":
        response["materialization_identity"]["revision"] = "001"
    elif fault in {"webspace", "scenario"}:
        response[fault + "_id"] = "other"
    elif fault == "stage":
        response["materialization_identity"]["source_fingerprint"] = "automation:sha256:any"
    else:
        response.pop("materialization_identity")
    result = deliver(publication)
    assert result["delivered"] is False
    assert result["error"] == "preview_owner_receipt_unconfirmed"


def test_owner_timeout_is_pending_and_an_explicit_retry_uses_the_same_publication(delivery, monkeypatch):
    publication, _, response, calls = delivery

    def timeout(**kwargs):
        calls.append(("timeout", kwargs))
        raise TimeoutError("Controlled ambiguous owner outcome")

    monkeypatch.setattr(preview, "materialize_revision_via_owner", timeout)
    result = deliver(publication)
    assert not result["delivered"] and result["error_type"] == "TimeoutError"
    monkeypatch.setattr(preview, "materialize_revision_via_owner", lambda **kwargs: response)
    retry = deliver(publication)
    assert retry["delivered"] is True
    assert retry["publication_ref"] == result["publication_ref"]


@pytest.mark.parametrize("when", ["before_owner", "after_owner"])
def test_target_or_head_change_prevents_false_delivery(delivery, monkeypatch, when):
    publication, binding, response, calls = delivery
    if when == "before_owner":
        def refresh(*args, **kwargs):
            binding["preview_target"].update(object_id="other", revision="002")
            return {"ok": True, "target": binding["preview_target"]}
        monkeypatch.setattr(preview, "refresh_follow_active_target", refresh)
    else:
        def owner(**kwargs):
            publication["is_current"] = False
            calls.append(("owner", kwargs))
            return response
        monkeypatch.setattr(preview, "materialize_revision_via_owner", owner)
    assert deliver(publication)["delivered"] is False
    if when == "before_owner":
        assert not calls


def test_real_committed_store_and_owner_content_ignore_conflicting_dev_revision(committed, monkeypatch):
    from test_builder_generation_authoring import source_bytes
    from test_builder_preview_sdk import _Workbench

    workflow, root, prepared, operations = committed
    workbench = _Workbench()
    monkeypatch.setattr(preview, "_service", lambda: workbench)
    monkeypatch.setattr("adaos.sdk.data.events.publish", lambda *args, **kwargs: pytest.fail("Duplicate async delivery"))
    source_before = source_bytes(root)
    workflow_before = copy.deepcopy(workflow.describe("scenario", "recipes"))
    rendered = []

    def owner(**kwargs):
        content, space = WebspaceBuilderPublicationService().preview_content_override(
            kwargs["scenario_id"], stage=kwargs["preview_stage"],
            revision=kwargs["revision"], label=kwargs["preview_label"], operations=operations)
        assert space == "dev"
        rendered.append(content)
        return {"ok": True, "accepted": True, "action": "builder_revision_apply",
                "webspace_id": kwargs["webspace_id"], "scenario_id": kwargs["scenario_id"],
                "materialization_identity": {
                    "webspace_id": kwargs["webspace_id"], "scenario_id": kwargs["scenario_id"],
                    "revision": kwargs["revision"],
                    "source_fingerprint": "prototype:" + prototype_webui_digest(content)}}

    monkeypatch.setattr(preview, "materialize_revision_via_owner", owner)
    for _ in range(2):
        result = preview.deliver_generation_publication(
            publication_ref=prepared["publication_ref"], source_webspace_id="host")
        assert result["delivered"] is True
    assert len(rendered) == 2
    for content in rendered:
        page = content["ui"]["application"]["desktop"]["pageSchema"]
        assert page["_adaos"]["releaseStage"] == "ALPHA"
        assert page["id"] != "forged"
        assert page["_adaos"]["materialization"]["revision"] == prepared["revision"]
    assert source_bytes(root) == source_before
    assert workflow.describe("scenario", "recipes") == workflow_before


def test_prepared_but_uncommitted_core_publication_cannot_reach_owner(workflow_project, monkeypatch, tmp_path):
    from test_builder_generation_authoring import ready_result

    from adaos.services.builder.interpretation_runs import InterpretationRunRejected

    workflow, _ = workflow_project
    monkeypatch.setattr("adaos.sdk.builder.workflow._service", lambda: workflow)
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: None)
    arguments = ready_result(workflow, tmp_path / "jobs")
    prepared = prototype.prepare_generation_publication(**arguments)
    monkeypatch.setattr(preview, "_service", lambda: SimpleNamespace(
        get_workspace_binding=lambda *args: pytest.fail("Orphan reached Preview")))
    with pytest.raises(InterpretationRunRejected):
        preview.deliver_generation_publication(
            publication_ref=prepared["publication_ref"], source_webspace_id="host")
