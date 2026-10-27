from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from adaos.services.applications import runtime_selection
from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable
from adaos.services.scenario.webspace_components.resolution import (
    WebspaceResolutionService, _selected_shared_skill_decls,
)


def test_shared_ui_uses_selected_provider_without_preparing_executor(monkeypatch):
    calls = []
    package = object()
    source = Path("selected/skills/provider")
    provider = SimpleNamespace(component=lambda kind, skill: package,
        verified_source=lambda item: source,
        identity=lambda skill: {"release_digest": "beta", "package_digest": "exact"})
    monkeypatch.setattr(runtime_selection, "selected_trial", lambda *args: provider)
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


def test_invalid_selected_shared_ui_does_not_fall_back_to_workspace(monkeypatch):
    def unavailable(*args):
        raise TrialRuntimeUnavailable("selected provider expired")
    monkeypatch.setattr(runtime_selection, "selected_trial", unavailable)
    with pytest.raises(TrialRuntimeUnavailable, match="expired"):
        _selected_shared_skill_decls(SimpleNamespace(ctx=object()), SimpleNamespace(local_node_id=lambda: "local"),
            "desktop", "workspace", [{"skill": "provider", "ui_owner": "shared"}])


def test_prepared_trial_snapshot_keeps_ambient_shared_providers(monkeypatch, tmp_path):
    scene_source = tmp_path / "scene"
    scene_source.mkdir()
    (scene_source / "webui.json").write_text("{}", encoding="utf-8")
    scene = SimpleNamespace(root=tmp_path, candidate_id="scene-candidate", project_id="chat",
        release_digest="chat-beta", component=lambda *_: object(), verified_source=lambda _: scene_source)
    provider = SimpleNamespace(component=lambda *_: object(),
        verified_source=lambda _: tmp_path / "selected/skills/provider",
        identity=lambda _: {"release_digest": "provider-beta"})
    monkeypatch.setattr(runtime_selection, "selected_trial",
        lambda ctx, host, kind, name: scene if kind == "scenario" else provider)
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
    assert [item["skill"] for item in declarations] == ["chat", "provider"]
    assert declarations[1]["version"] == "2.0.0"


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
    monkeypatch.setattr(runtime_selection, "selected_trial", selected)
    monkeypatch.setattr("adaos.services.component_updates.ComponentUpdateService",
                        lambda: SimpleNamespace(state_path=tmp_path / "annotations.json"))
    runtime = SimpleNamespace(ctx=object(), _load_webui=load)
    operations = SimpleNamespace(local_node_id=lambda: "local")
    declarations = [{"skill": "provider", "ui_owner": "shared"}]
    first = _selected_shared_skill_decls(runtime, operations, "desktop", "workspace", declarations)
    first[0]["registry"]["widgets"]["untrusted"] = {}
    second = _selected_shared_skill_decls(runtime, operations, "desktop", "workspace", declarations)
    assert "untrusted" not in second[0]["registry"]["widgets"]
    assert len(checks) == 2 and len(reads) == 1
    def expired(*args):
        raise TrialRuntimeUnavailable("expired after cached observation")
    monkeypatch.setattr(runtime_selection, "selected_trial", expired)
    with pytest.raises(TrialRuntimeUnavailable, match="expired after"):
        _selected_shared_skill_decls(runtime, operations, "desktop", "workspace", declarations)
