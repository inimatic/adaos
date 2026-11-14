from types import SimpleNamespace
import json

import pytest

from adaos.services.builder import release_basis


@pytest.fixture
def installed(monkeypatch):
    app = SimpleNamespace(application_id="management", legacy_project_id="desktop")
    installation = SimpleNamespace(installed_release_digest="sha256:current", status="active", revision=4)
    channels = {"stable": "sha256:current"}
    store = SimpleNamespace(list_applications=lambda: [app], get_installation=lambda _id: installation,
        get_channels=lambda _id: {"channels": channels.copy()}, list_runtime_selections=lambda: [])
    monkeypatch.setattr(release_basis, "get_application_service", lambda _: SimpleNamespace(store=store))
    return installation, channels


def test_actual_application_basis_is_independent_of_older_workflow_delivery(tmp_path, installed):
    captured = release_basis.capture(tmp_path, "desktop")
    assert captured["basis"]["installation"]["release_digest"] == "sha256:current"
    change = {"delivery": {"release_digest": "sha256:old"}, "runs": [
        {"activity": "plan_change_set", "input_refs": [captured["ref"]]}]}
    release_basis.require_change_basis(tmp_path, change)


def test_installation_or_channel_change_invalidates_retained_basis(tmp_path, installed):
    installation, channels = installed
    before = release_basis.capture(tmp_path, "desktop")
    channels["stable"] = "sha256:successor"
    with pytest.raises(ValueError, match="rebase"):
        release_basis.require_current(tmp_path, before["ref"])
    channels["stable"] = "sha256:current"
    installation.revision += 1
    with pytest.raises(ValueError, match="rebase"):
        release_basis.require_current(tmp_path, before["ref"])


def test_unchanged_basis_reuse_and_tampered_receipt_rejection(tmp_path, installed):
    before = release_basis.capture(tmp_path, "desktop")
    assert release_basis.require_current(tmp_path, before["ref"])["digest"] == before["digest"]
    path = tmp_path / "builder/implementation-bases" / (before["digest"][7:] + ".json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["channels"]["stable"] = "sha256:forged"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        release_basis.require_current(tmp_path, before["ref"])


def test_legacy_workflow_does_not_acquire_an_invented_basis(tmp_path):
    before = set(tmp_path.iterdir())
    release_basis.require_change_basis(tmp_path, {"runs": [{"activity":"plan_change_set", "input_refs":[]}]})
    assert set(tmp_path.iterdir()) == before
