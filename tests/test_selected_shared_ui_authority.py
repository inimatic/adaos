from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.applications import runtime_selection
from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable
from adaos.services.scenario.webspace_components.resolution import _selected_shared_skill_decls


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
