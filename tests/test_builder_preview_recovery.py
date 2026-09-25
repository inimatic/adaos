from types import SimpleNamespace

import pytest

from adaos.services.builder.workbench import BuilderWorkbenchService
from adaos.services.scenario.webspace_components.builder_publication import WebspaceBuilderPublicationService


@pytest.mark.parametrize("stage,revision", [("prototype", "071"), ("automation", "task.1")])
def test_preview_recovery_keeps_project_primary_scenario_and_revision(monkeypatch, stage, revision):
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
