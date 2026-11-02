from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from adaos.services.applications import runtime_selection
from adaos.services.applications.runtime_channel import RuntimeChannelConflict
from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable
from adaos.services.scenario.webspace_components.resolution import (
    WebspaceResolutionService, _selected_shared_skill_decls,
)


def scoped_provider(monkeypatch, resolve):
    monkeypatch.setattr(runtime_selection, "selected_application", lambda ctx, host, kind, name, **kwargs:
        SimpleNamespace(application_id=name, release_digest="selected-release", runtime_root_ref="trial:selected"))
    monkeypatch.setattr(runtime_selection, "selected_trial_exact", lambda ctx, host, app, digest, kind, name:
        resolve(ctx, host, kind, name))
    monkeypatch.setattr(runtime_selection, "selected_trial", lambda *_:
        pytest.fail("UI cannot use an unqualified/global Trial"))


@pytest.mark.parametrize("authority", [None, SimpleNamespace(runtime_root_ref="workspace")])
def test_workspace_shared_ui_cannot_inherit_a_beta_from_another_host(monkeypatch, authority):
    from adaos.services.scenario.webspace_components.resolution import _selected_ui_trial
    monkeypatch.setattr(runtime_selection, "selected_application", lambda *args, **kwargs: authority)
    monkeypatch.setattr(runtime_selection, "selected_trial", lambda *_:
        pytest.fail("Foreign Beta cannot override selected Workspace"))
    monkeypatch.setattr(runtime_selection, "selected_trial_exact", lambda *_:
        pytest.fail("Workspace must not admit a Trial"))
    assert _selected_ui_trial(object(), "stable-host", "skill", "provider") is None


def test_shared_ui_uses_selected_provider_without_preparing_executor(monkeypatch):
    calls = []
    package = object()
    source = Path("selected/skills/provider")
    provider = SimpleNamespace(component=lambda kind, skill: package,
        verified_source=lambda item: source,
        identity=lambda skill: {"release_digest": "beta", "package_digest": "exact"})
    scoped_provider(monkeypatch, lambda *args: provider)
    def load(skill, space, **kwargs):
        calls.append((skill, space, kwargs))
        return {"skill": skill, "node_id": "local", "ui_owner": "shared", "version": "2.0.0"}
    runtime = SimpleNamespace(ctx=object(), _load_webui=load)
    operations = SimpleNamespace(local_node_id=lambda: "local")
    own = {"skill": "own", "node_id": "local", "ui_owner": "node"}
    remote = {"skill": "provider", "node_id": "remote", "ui_owner": "shared"}
    shared = {"skill": "provider", "node_id": "local", "ui_owner": "shared", "version": "1.0.0"}
    result = _selected_shared_skill_decls(runtime, operations, "desktop", "workspace", [own, shared, remote])
    assert result[0] == own and result[2] == remote
    assert result[1]["version"] == "2.0.0"
    assert result[1]["application_runtime"]["release_digest"] == "beta"
    assert calls == [("provider", "trial", {"root_override": source.parent,
                      "source_authority_override": "selected_shared_application"})]


def test_dev_ui_never_uses_production_shared_selection(monkeypatch):
    monkeypatch.setattr(runtime_selection, "selected_trial", lambda *args: pytest.fail("DEV cannot use production authority"))
    declarations = [{"skill": "provider", "ui_owner": "shared"}]
    assert _selected_shared_skill_decls(object(), object(), "desktop-dev", "dev", declarations) is declarations


def test_scenario_navigation_preserves_admission_failure_without_workspace_fallback(monkeypatch):
    from adaos.services.scenario import webspace_runtime

    def expired(*args):
        raise TrialRuntimeUnavailable("Trial has expired")
    monkeypatch.setattr(webspace_runtime, "get_ctx", lambda: object())
    monkeypatch.setattr(runtime_selection, "selected_application", lambda *args: None)
    monkeypatch.setattr(runtime_selection, "selected_trial", expired)
    monkeypatch.setattr(webspace_runtime, "_scenario_exists_for_switch", lambda *args, **kwargs:
        pytest.fail("Expired selected Trial cannot use Workspace"))
    assert webspace_runtime._scenario_exists_in_webspace("chat", space="workspace", webspace_id="desktop") is False
    with pytest.raises(TrialRuntimeUnavailable, match="Trial has expired"):
        webspace_runtime._scenario_switch_operations("desktop").scenario_exists_for_switch("chat", space="workspace")


def test_invalid_selected_shared_ui_does_not_fall_back_to_workspace(monkeypatch):
    def unavailable(*args):
        raise TrialRuntimeUnavailable("selected provider expired")
    scoped_provider(monkeypatch, unavailable)
    with pytest.raises(TrialRuntimeUnavailable, match="expired"):
        _selected_shared_skill_decls(SimpleNamespace(ctx=object()), SimpleNamespace(local_node_id=lambda: "local"),
            "desktop", "workspace", [{"skill": "provider", "ui_owner": "shared"}])


@pytest.mark.parametrize("error_type", [TrialRuntimeUnavailable, RuntimeChannelConflict])
def test_unavailable_ambient_shared_ui_does_not_break_an_unrelated_trial(monkeypatch, error_type):
    def unavailable(*args):
        raise error_type("selected provider unavailable")

    scoped_provider(monkeypatch, unavailable)
    runtime = SimpleNamespace(ctx=object(), _load_webui=lambda *_args, **_kwargs:
        pytest.fail("Unavailable selected UI must not fall back to Workspace"))
    declarations = [{"skill": "own", "ui_owner": "node"},
                    {"skill": "expired_other_app", "ui_owner": "shared"}]
    assert _selected_shared_skill_decls(runtime, SimpleNamespace(local_node_id=lambda: "local"),
        "desktop", "workspace", declarations, required_skills={"own"}) == [declarations[0]]


@pytest.mark.parametrize("error_type", [TrialRuntimeUnavailable, RuntimeChannelConflict])
def test_unavailable_required_shared_ui_still_blocks_its_application(monkeypatch, error_type):
    def unavailable(*args):
        raise error_type("required provider unavailable")

    scoped_provider(monkeypatch, unavailable)
    with pytest.raises(error_type, match="required provider unavailable"):
        _selected_shared_skill_decls(SimpleNamespace(ctx=object()), SimpleNamespace(local_node_id=lambda: "local"),
            "desktop", "workspace", [{"skill": "provider", "ui_owner": "shared"}], required_skills={"provider"})


@pytest.mark.parametrize("ambient_available", [True, False])
def test_prepared_trial_snapshot_keeps_available_ambient_shared_providers(monkeypatch, tmp_path, ambient_available):
    scene_source = tmp_path / "scene"
    scene_source.mkdir()
    (scene_source / "webui.json").write_text("{}", encoding="utf-8")
    scene = SimpleNamespace(root=tmp_path, candidate_id="scene-candidate", project_id="chat",
        release_digest="chat-beta", component=lambda *_: object(), verified_source=lambda _: scene_source,
        packages=(SimpleNamespace(kind="skill", artifact_id="chat"),))
    provider = SimpleNamespace(component=lambda *_: object(),
        verified_source=lambda _: tmp_path / "selected/skills/provider",
        identity=lambda _: {"release_digest": "provider-beta"})
    def resolve(ctx, host, kind, name):
        if kind == "scenario":
            return scene
        if not ambient_available:
            raise TrialRuntimeUnavailable("ambient provider expired")
        return provider
    scoped_provider(monkeypatch, resolve)
    monkeypatch.setattr(runtime_selection, "trial_launcher_entries", lambda *_: [])
    monkeypatch.setattr(runtime_selection, "trial_launcher_icon_resources", lambda *_: {})
    owned = [{"skill": "chat", "node_id": "local", "ui_owner": "node"}]
    ambient = [{"skill": "provider", "node_id": "local", "ui_owner": "shared"}]
    runtime = SimpleNamespace(ctx=object(), _collect_skill_decls_from_root=lambda _: owned,
        _collect_skill_decls=lambda **_: ambient,
        _load_webui=lambda *args, **kwargs: {**ambient[0], "version": "2.0.0"})
    operations = SimpleNamespace(local_node_id=lambda: "local", fingerprint_json_like=json.dumps,
                                  scenario_materialization_contract=lambda *args, **kwargs: {})
    declarations, _, _ = WebspaceResolutionService().prepare_external_inputs(
        runtime, operations, "desktop", "chat", source_mode="workspace",
        skill_decls=owned, skill_decls_fingerprint="trial-only")
    assert [item["skill"] for item in declarations] == (["chat", "provider"] if ambient_available else ["chat"])
    if ambient_available:
        assert declarations[1]["version"] == "2.0.0"


@pytest.mark.parametrize("provider_required", [False, True])
def test_workspace_application_uses_exact_release_to_scope_shared_ui(monkeypatch, tmp_path, provider_required):
    def expired(*args):
        raise TrialRuntimeUnavailable("shared provider expired")

    scoped_provider(monkeypatch, expired)
    selected = runtime_selection.selected_application
    monkeypatch.setattr(runtime_selection, "selected_application", lambda ctx, host, kind, name, **kwargs:
        SimpleNamespace(application_id="chat", release_digest="chat-release", runtime_root_ref="workspace")
        if kind == "scenario" else selected(ctx, host, kind, name, **kwargs))
    component_reads = []
    def component_keys(app, release):
        component_reads.append((app, release))
        return (("scenario", "chat"), ("skill", "provider" if provider_required else "chat_service"))
    monkeypatch.setattr("adaos.services.applications.store.ApplicationStore", lambda state:
        SimpleNamespace(release_component_keys=component_keys))
    monkeypatch.setattr(runtime_selection, "trial_launcher_entries", lambda *_: [])
    monkeypatch.setattr(runtime_selection, "trial_launcher_icon_resources", lambda *_: {})
    runtime = SimpleNamespace(ctx=SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path)),
        _load_webui=lambda *_args, **_kwargs: pytest.fail("Expired selected provider must not use Workspace"))
    operations = SimpleNamespace(local_node_id=lambda: "local", fingerprint_json_like=json.dumps,
        scenario_materialization_contract=lambda *args, **kwargs: {})
    owned = {"skill": "chat_service", "ui_owner": "node"}
    def prepare():
        return WebspaceResolutionService().prepare_external_inputs(runtime, operations, "desktop", "chat",
            source_mode="workspace", skill_decls=[owned, {"skill": "provider", "ui_owner": "shared"}],
            skill_decls_fingerprint="workspace")
    if provider_required:
        with pytest.raises(TrialRuntimeUnavailable, match="shared provider expired"):
            prepare()
    else:
        declarations, _, external = prepare()
        assert declarations == [owned]
        assert external["trial_active"] is False
    assert component_reads == [("chat", "chat-release")]


def test_verified_shared_ui_is_cached_but_selection_is_rechecked(monkeypatch, tmp_path):
    checks, reads = [], []
    package = SimpleNamespace(digest="package")
    provider = SimpleNamespace(root=tmp_path, release_digest="release",
        component=lambda *_: package, verified_source=lambda _: tmp_path / "skills/provider",
        identity=lambda _: {"release_digest": "release"})
    def selected(*args):
        checks.append(True)
        return provider
    def load(*args, **kwargs):
        reads.append(True)
        return {"skill": "provider", "registry": {"widgets": {"trusted": {}}}}
    scoped_provider(monkeypatch, selected)
    annotation = {'stage': 'beta'}
    monkeypatch.setattr("adaos.services.component_updates.ComponentUpdateService",
                        lambda: SimpleNamespace(current_component_metadata=lambda *_, **__: dict(annotation)))
    runtime = SimpleNamespace(ctx=object(), _load_webui=load)
    operations = SimpleNamespace(local_node_id=lambda: "local")
    declarations = [{"skill": "provider", "ui_owner": "shared"}]
    first = _selected_shared_skill_decls(runtime, operations, "desktop", "workspace", declarations)
    assert first[0]['component_update']['stage'] == 'beta'
    first[0]["registry"]["widgets"]["untrusted"] = {}
    annotation['stage'] = 'stable'
    second = _selected_shared_skill_decls(runtime, operations, "desktop", "workspace", declarations)
    assert second[0]['component_update']['stage'] == 'stable'
    assert "untrusted" not in second[0]["registry"]["widgets"]
    assert len(checks) == 2 and len(reads) == 1
    def expired(*args):
        raise TrialRuntimeUnavailable("expired after cached observation")
    scoped_provider(monkeypatch, expired)
    with pytest.raises(TrialRuntimeUnavailable, match="expired after"):
        _selected_shared_skill_decls(runtime, operations, "desktop", "workspace", declarations)
