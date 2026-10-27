"""The isolated acceptance host must retain the real shared-provider channels."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


def _qualification_module():
    source = Path(__file__).resolve().parents[1] / "e2e/stand/qualify-projection-convergence.py"
    spec = spec_from_file_location("projection_qualification", source)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_mirrors_shared_provider_not_only_navigated_apps(monkeypatch):
    module = _qualification_module()
    host = "desktop-codex-fixture"
    chat = SimpleNamespace(webspace_id="desktop", application_id="context_chat",
                           source="local_beta", release_digest="chat-beta", runtime_root_ref="trial:chat")
    provider = SimpleNamespace(webspace_id="desktop", application_id="subscription_status",
                               source="local_beta", release_digest="provider-beta", runtime_root_ref="trial:provider")
    unrelated = SimpleNamespace(webspace_id="other-host", application_id="other")
    existing = {
        "context_chat": SimpleNamespace(**{**vars(chat), "webspace_id": host, "revision": 3}),
        "subscription_status": SimpleNamespace(webspace_id=host, application_id="subscription_status",
            source="workspace", release_digest="provider-stable", runtime_root_ref="workspace", revision=2),
    }
    store = SimpleNamespace(list_runtime_selections=lambda: [chat, provider, unrelated],
                            get_runtime_selection=lambda webspace, app: existing[app])
    calls = []
    monkeypatch.setattr(module.applications, "select_runtime", lambda **kwargs: calls.append(kwargs))
    mirrored = module.mirror_runtime_selections(store, webspace_id=host, subnet_id="test")
    assert {item["application_id"] for item in mirrored} == {"context_chat", "subscription_status"}
    assert len(calls) == 1
    assert calls[0]["webspace_id"] == host
    assert calls[0]["application_id"] == "subscription_status"
    assert calls[0]["release_digest"] == "provider-beta"
    assert calls[0]["runtime_root_ref"] == "trial:provider"
    assert calls[0]["source"] == "local_beta"
    assert calls[0]["expected_revision"] == 2


def test_acceptance_cannot_rewrite_real_desktop_selections():
    module = _qualification_module()
    with pytest.raises(ValueError, match="isolated"):
        module.mirror_runtime_selections(object(), webspace_id="desktop", subnet_id="test")
