from types import SimpleNamespace

import pytest

from adaos.sdk.builder import automation, model_settings
from adaos.sdk.developer import prompt_context


def test_root_catalog_is_stage_scoped_and_selection_is_allowlisted(monkeypatch):
    saved = []
    monkeypatch.setattr(prompt_context, "get", lambda *_: {"builder_codex_profile": None})
    monkeypatch.setattr(prompt_context, "set_preferences", lambda *_, **kw: saved.append(kw) or kw)
    def catalog(**kwargs):
        assert kwargs["scope"] == "automation"
        return {"scope": "automation", "data": [
            {"id": "model-a", "provider": "openai-codex-cli", "scope": "automation", "available": True,
             "default_reasoning_effort": "low"},
            {"id": "model-b", "provider": "openai-codex-cli", "scope": "automation", "available": False},
            {"id": "prototype-model", "provider": "openai", "scope": "development", "available": True},
        ]}
    monkeypatch.setattr(model_settings, "list_llm_models", catalog)
    monkeypatch.setattr(model_settings, "local_codex_models", lambda **_: {"status": "ready", "models": [
        {"id": "model-a", "supported_reasoning_efforts": ["low"]},
        {"id": "model-b", "supported_reasoning_efforts": ["low"]},
    ]})
    assert len(model_settings.codex_options("project", "one")["options"]) == 2
    model_settings.set_codex_profile("project", "one", model="model-a")
    assert saved[0]["codex_profile"] == {"model": "model-a", "provider": "openai-codex-cli", "reasoning_effort": "low"}
    for model in ("model-b", "prototype-model", "undeclared"):
        with pytest.raises(ValueError):
            model_settings.set_codex_profile("project", "one", model=model)
    assert len(saved) == 1
    with pytest.raises(ValueError, match="reasoning effort"):
        model_settings.set_codex_profile("project", "one", model="model-a", reasoning_effort="high")
    monkeypatch.setattr(model_settings, "local_codex_models", lambda **_: {"status": "ready", "models": []})
    result = model_settings.codex_options("project", "one")
    assert result["status"] == "unavailable"
    assert result["options"][0]["root_available"] is True
    assert result["options"][0]["available"] is False
    assert result["options"][0]["availability_reason"] == "not_available_in_local_codex"


def test_root_failure_never_invents_selectable_codex_models(monkeypatch):
    current = {"model": "prior-model"}
    monkeypatch.setattr(prompt_context, "get", lambda *_: {"builder_codex_profile": current})
    def unavailable(**_):
        raise TimeoutError()
    monkeypatch.setattr(model_settings, "list_llm_models", unavailable)
    result = model_settings.codex_options("scenario", "one")
    assert result["options"] == [] and result["current"] == current
    assert result["ok"] is False
    monkeypatch.setattr(model_settings, "list_llm_models", lambda **_: {"data": [{"id": "wrong-scope"}]})
    assert model_settings.codex_options("scenario", "one")["reason"] == "root_codex_catalog_not_supported"


def test_automation_captures_project_codex_preference_and_honors_explicit_override(monkeypatch):
    calls = []
    saved = {"provider": "openai-codex-cli", "model": "saved-model", "reasoning_effort": "low"}
    monkeypatch.setattr(prompt_context, "get", lambda kind, id: {"builder_codex_profile": saved})
    checked = []
    monkeypatch.setattr(model_settings, "require_local_profile", lambda profile: checked.append(profile))
    monkeypatch.setattr(automation, "_service", lambda: SimpleNamespace(background=True,
        start_from_execute=lambda **kw: calls.append(kw) or {"ok": True}))
    automation.start(object_type="project", object_id="one", implementation_brief="Implement the accepted scope")
    explicit = {"provider": "openai-codex-cli", "model": "explicit-model"}
    automation.start(object_type="project", object_id="one", implementation_brief="Implement", agent_profile=explicit)
    assert calls[0]["agent_profile"] == saved
    assert calls[1]["agent_profile"] == explicit
    assert checked == [saved, explicit]


def test_explicit_unavailable_model_is_rejected_before_start(monkeypatch):
    monkeypatch.setattr(model_settings, "local_codex_models", lambda **_: {"status": "ready", "models": []})
    monkeypatch.setattr(automation, "_service", lambda: pytest.fail("must not start a task"))
    with pytest.raises(ValueError, match="codex_model_unavailable"):
        automation.start(object_type="scenario", object_id="one", implementation_brief="Implement",
                         agent_profile={"model": "retired-model"})
