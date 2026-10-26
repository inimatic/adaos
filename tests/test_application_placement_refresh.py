from types import SimpleNamespace

import pytest
import requests

from adaos.sdk.builder import applications
from adaos.services.component_updates import ComponentUpdateService


@pytest.mark.parametrize("failure", (requests.ConnectionError, requests.ReadTimeout))
def test_transport_failure_keeps_other_rooms_observable_and_redacts_details(monkeypatch, tmp_path, failure):
    selections = [SimpleNamespace(application_id="sample", webspace_id=room) for room in ("a", "b")]
    service = SimpleNamespace(store=SimpleNamespace(list_runtime_selections=lambda: selections))
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_state_dir", lambda: tmp_path)
    reconciled = []
    monkeypatch.setattr(ComponentUpdateService, "reconcile_local_trials",
                        lambda self, room, **_: reconciled.append(room))
    def refresh(room):
        if room == "a":
            raise failure("sensitive request headers")
        return {"ok": True}
    monkeypatch.setattr(applications, "refresh_placement", refresh)
    result = applications._refresh_application_placements("sample")
    assert reconciled == ["a", "b"]
    assert result["pending"] and not result["ok"]
    assert result["webspaces"]["a"]["error_type"] == failure.__name__
    assert result["webspaces"]["b"] == {"ok": True}
    assert "sensitive request headers" not in str(result)


def test_authorization_failure_is_not_reported_as_transient_transport(monkeypatch, tmp_path):
    service = SimpleNamespace(store=SimpleNamespace(list_runtime_selections=lambda: [
        SimpleNamespace(application_id="sample", webspace_id="a")]))
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(ComponentUpdateService, "reconcile_local_trials", lambda *_, **__: None)
    def denied(_):
        raise requests.HTTPError("403")
    monkeypatch.setattr(applications, "refresh_placement", denied)
    with pytest.raises(requests.HTTPError):
        applications._refresh_application_placements("sample")
