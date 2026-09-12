from types import SimpleNamespace

import pytest

from adaos.services.builder.workbench import BuilderWorkbenchService
from adaos.services.scenario.webspace_components.builder_publication import WebspaceBuilderPublicationService
from adaos.services.scenario import webspace_runtime as runtime_module


def test_selected_trial_recovery_binds_candidate_ui_tools_and_identity(monkeypatch, tmp_path):
    target = {"stage": "trial", "object_id": "example", "revision": "candidate-1"}
    monkeypatch.setattr(BuilderWorkbenchService, "from_context", lambda: SimpleNamespace(
        existing_preview_target=lambda webspace: target if webspace == "preview" else None,
    ))
    service = WebspaceBuilderPublicationService()
    calls = []

    def find(scenario, **kwargs):
        calls.append((scenario, kwargs["revision"]))
        return {"candidate_ref": {"release_digest": "sha256:exact"}}, tmp_path

    def collect(root):
        assert root == tmp_path / "skills"
        return [{"name": "candidate_skill"}]

    monkeypatch.setattr(service, "trial_workspace_for_preview", find)
    monkeypatch.setattr(service, "preview_content_override", lambda *args, **kwargs: ({"candidate": True}, "trial"))
    operations = SimpleNamespace(
        scenario_runtime_type=lambda: SimpleNamespace(
            _collect_skill_decls_from_root=collect, _last_skill_decls_fingerprint="candidate-tools",
        ),
        canonical_materialization_identity=lambda **kwargs: kwargs,
    )
    result = service.selected_trial_inputs("preview", scenario_id="example", operations=operations)
    assert result["scenario_content_override"] == {"candidate": True}
    assert result["skill_decls_snapshot"] == [{"name": "candidate_skill"}]
    assert result["materialization_identity"]["source_fingerprint"] == "trial:sha256:exact"
    assert result["materialization_identity"]["revision"] == "candidate-1"
    assert calls == [("example", "candidate-1")]
    assert service.selected_trial_inputs("ordinary", scenario_id="example", operations=operations) == {}
    with pytest.raises(ValueError, match="identity"):
        service.selected_trial_inputs("preview", scenario_id="other", operations=operations)
    target["revision"] = ""
    with pytest.raises(ValueError, match="identity"):
        service.selected_trial_inputs("preview", scenario_id="example", operations=operations)


@pytest.mark.asyncio
@pytest.mark.parametrize("from_doc", [False, True])
async def test_room_resolution_recovers_selected_trial_before_generic_source_loading(monkeypatch, from_doc):
    selected = {
        "scenario_id": "example", "scenario_content_override": {"candidate": True},
        "skill_decls_snapshot": [{"name": "candidate_skill"}], "skill_decls_fingerprint": "exact-tools",
        "materialization_identity": {"revision": "candidate-1"},
    }
    monkeypatch.setattr(runtime_module, "_selected_trial_preview_inputs", lambda *args: selected)
    runtime = runtime_module.WebspaceScenarioRuntime()
    captured = {}
    expected = object()
    if from_doc:
        def collect(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace()
        monkeypatch.setattr(runtime, "_collect_resolver_inputs_in_doc", collect)
        monkeypatch.setattr(runtime, "_resolve_materialized_payload_from_inputs_sync", lambda inputs: (
            SimpleNamespace(to_registry_entry=lambda: expected), {}, {},
        ))
        result = await runtime.resolve_materialized_payload_from_doc_async(object(), "preview", scenario_id="example")
        assert captured["skill_decls_override"] == selected["skill_decls_snapshot"]
    else:
        async def resolve(*args, **kwargs):
            captured.update(kwargs)
            return expected
        monkeypatch.setattr(runtime_module._RUNTIME.materialization, "resolve_payload", resolve)
        result = await runtime.resolve_materialized_payload_async("preview", scenario_id="example")
        assert captured["skill_decls_snapshot"] == selected["skill_decls_snapshot"]
    assert result is expected
    assert captured["scenario_content_override"] == selected["scenario_content_override"]
    assert captured["materialization_identity"] == selected["materialization_identity"]


@pytest.mark.asyncio
async def test_explicit_materialization_is_not_overridden_by_previous_trial(monkeypatch):
    def unexpected(*args):
        pytest.fail("explicit source selection must not read the old preview target")
    monkeypatch.setattr(runtime_module, "_selected_trial_preview_inputs", unexpected)
    captured = {}
    async def resolve(*args, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(runtime_module._RUNTIME.materialization, "resolve_payload", resolve)
    await runtime_module.WebspaceScenarioRuntime().resolve_materialized_payload_async(
        "preview", scenario_id="new-prototype", materialization_identity={"revision": "002"},
    )
    assert captured["scenario_id"] == "new-prototype"
    assert captured["materialization_identity"] == {"revision": "002"}
