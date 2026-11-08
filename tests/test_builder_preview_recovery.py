from types import SimpleNamespace
import copy
import json
import logging

import pytest

from adaos.services.builder.workbench import BuilderWorkbenchService
from adaos.services.scenario.webspace_components.builder_publication import WebspaceBuilderPublicationService


@pytest.mark.parametrize("stage,revision", [("prototype", "071"), ("automation", "task.1")])
def test_preview_recovery_keeps_project_primary_scenario_and_revision(monkeypatch, stage, revision):
    monkeypatch.setattr("adaos.services.builder.dev_dependencies.selected_data_binding", lambda _scenario, **_kwargs: {})
    target = {"stage": stage, "object_type": "project", "object_id": "aggregate",
              "scenario_id": "primary", "revision": revision, "label": "Pinned result"}
    monkeypatch.setattr(BuilderWorkbenchService, "from_context", staticmethod(
        lambda: SimpleNamespace(existing_preview_target=lambda _webspace: target)))
    service = WebspaceBuilderPublicationService()
    calls = []
    content = {"ui": {"application": {"desktop": {"pageSchema": {"widgets": []}}}}}

    def read(scenario_id, **kwargs):
        calls.append((scenario_id, kwargs))
        return content, "dev"

    monkeypatch.setattr(service, "preview_content_override", read)
    result = service.selected_preview_inputs("desktop-dev", scenario_id="primary", operations=SimpleNamespace(
        canonical_materialization_identity=lambda **kwargs: kwargs))
    assert calls[0][0] == "primary"
    assert calls[0][1]["stage"] == stage
    assert calls[0][1]["revision"] == revision
    assert result["scenario_content_override"] == content
    assert result["materialization_identity"]["revision"] == revision
    assert result["materialization_identity"]["source_fingerprint"].startswith(stage + ":")
    assert service.selected_preview_inputs(
        "desktop-dev", scenario_id="another", operations=None
    ) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,revision", [("prototype", "071"), ("automation", "task.1")])
@pytest.mark.parametrize("is_dev,paired", [(True, True), (False, True), (True, False)])
async def test_revision_only_delivery_uses_only_current_paired_dev_target(
    monkeypatch, stage, revision, is_dev, paired,
):
    binding = {"dev_webspace_id": "preview" if paired else "foreign",
               "runtime_scenario_id": "primary",
               "preview_target": {"stage": stage, "scenario_id": "primary",
                                  "revision": revision, "label": "Pinned result"}}
    monkeypatch.setattr(BuilderWorkbenchService, "from_context", staticmethod(
        lambda: SimpleNamespace(get_workspace_binding=lambda _source: binding)))
    service = WebspaceBuilderPublicationService()
    captured = {}

    async def resolve(*args, **kwargs):
        return SimpleNamespace(is_dev=is_dev, source_mode="dev"), "primary", "explicit"

    def read(scenario, **kwargs):
        captured.update(scenario=scenario, **kwargs)
        # Stop before any manifest/runtime writes; the retained-source reader
        # has received the exact identity that the real compiler will consume.
        raise RuntimeError("selected source captured")

    monkeypatch.setattr(service, "preview_content_override", read)
    operations = SimpleNamespace(resolve_rebuild_scenario_target=resolve,
        preflight_validated_scenario=lambda scenario, **kwargs: (scenario, "explicit", {}),
        logger=logging.getLogger(__name__))
    with pytest.raises(RuntimeError, match="selected source captured"):
        await service.apply_revision_materialization("preview", scenario_id="primary", revision=revision,
            event_payload={"source_webspace_id": "host"}, operations=operations)
    assert captured["revision"] == revision
    assert captured["stage"] == (stage if is_dev and paired else "")
    assert captured["label"] == ("Pinned result" if is_dev and paired else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["070", "072", None])
async def test_revision_only_delivery_cannot_replace_selected_revision(monkeypatch, requested):
    binding = {"dev_webspace_id": "preview", "runtime_scenario_id": "primary",
               "preview_target": {"stage": "prototype", "scenario_id": "primary", "revision": "071"}}
    monkeypatch.setattr(BuilderWorkbenchService, "from_context", staticmethod(
        lambda: SimpleNamespace(get_workspace_binding=lambda _source: binding)))

    async def resolve(*args, **kwargs):
        return SimpleNamespace(is_dev=True), "primary", "explicit"

    operations = SimpleNamespace(resolve_rebuild_scenario_target=resolve)
    result = await WebspaceBuilderPublicationService().apply_revision_materialization(
        "preview", scenario_id="primary", revision=requested,
        event_payload={"source_webspace_id": "host"}, operations=operations)
    assert result["accepted"] is False
    assert result["skipped"] == "superseded_builder_revision"
    assert result["desired_revision"] == "071"


@pytest.mark.asyncio
async def test_revision_only_delivery_compiles_retained_alpha_not_current_source(monkeypatch, tmp_path):
    binding = {"dev_webspace_id": "preview", "runtime_scenario_id": "primary",
               "preview_target": {"stage": "prototype", "scenario_id": "primary", "revision": "071"}}
    monkeypatch.setattr(BuilderWorkbenchService, "from_context", staticmethod(
        lambda: SimpleNamespace(get_workspace_binding=lambda _source: binding)))
    revisions = tmp_path / "ui_revisions"
    revisions.mkdir()
    retained = {"ui": {"application": {"desktop": {"pageSchema": {
        "title": "Retained", "widgets": [{"id": "retained", "type": "ui.table"}]}}}}}
    def read_revision(kind, identity, revision):
        assert (kind, identity, revision) == ("scenario", "primary", "071")
        return {"scenario_id": identity, "revision": revision, "after_webui": retained}
    monkeypatch.setattr("adaos.sdk.builder.prototype.read_revision", read_revision)
    (revisions / "071.json").write_text(json.dumps({"after_webui": retained}), encoding="utf-8")
    state = SimpleNamespace(is_dev=True, source_mode="dev", kind="dev",
                            current_scenario="primary", effective_home_scenario="primary")
    row = SimpleNamespace(ui_overlay={}, home_scenario="primary", current_scenario_overlay="primary",
                          effective_home_scenario="primary")
    captured = {}

    async def resolve(*args, **kwargs):
        return state, "primary", "explicit"

    async def rebuild(*args, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "accepted": True}

    def generic_source(*args, **kwargs):
        pytest.fail("A revision-only delivery must not read mutable current DEV source")

    operations = SimpleNamespace(resolve_rebuild_scenario_target=resolve,
        preflight_validated_scenario=lambda scenario, **kwargs: (scenario, "explicit", {}),
        scenarios_loader=SimpleNamespace(scenario_root_for_space=lambda *args: tmp_path, read_content=generic_source),
        clone_json_like=copy.deepcopy, canonical_materialization_identity=lambda **kwargs: {"key_hash": "exact", **kwargs},
        workspace_index=SimpleNamespace(get_workspace=lambda _: row, set_workspace_manifest=lambda *args, **kwargs: row),
        elapsed_ms=lambda _: 0, logger=logging.getLogger(__name__), payload_command_trace=lambda _: {},
        rebuild_webspace_from_sources=rebuild)
    result = await WebspaceBuilderPublicationService().apply_revision_materialization(
        "preview", scenario_id="primary", revision="071", source_fingerprint="generic-stale-fingerprint",
        event_payload={"source_webspace_id": "host"}, operations=operations)
    assert result["accepted"] and result["preview_identity_recovered"]
    page = captured["scenario_content_override"]["ui"]["application"]["desktop"]["pageSchema"]
    assert page["widgets"] == retained["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    assert page["_adaos"]["releaseStage"] == "ALPHA"
    assert page["_adaos"]["materialization"] == {"stage": "prototype", "revision": "071", "sourceSpace": "dev"}
    assert captured["materialization_identity"]["source_fingerprint"].startswith("prototype:sha256:")
