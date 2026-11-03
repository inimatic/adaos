from types import SimpleNamespace

import pytest

from adaos.services.applications.lifecycle import observe_runtime_admission
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
from adaos.services.applications.dashboard import project_application_dashboard


@pytest.mark.parametrize("source", ["stable_installation", "local_trial"])
def test_inventory_admission_observes_the_same_transition_fence_as_detail(tmp_path, monkeypatch, source):
    """A retained Beta lease cannot turn an unfinished Finalize green in inventory."""
    from adaos.services.applications import lifecycle

    selection = SimpleNamespace(application_id="test", source=source)
    selected_fact = {"admission": "admitted", "reason": "InstalledLocalBeta",
                     "admission_kind": "installed_local_beta", "expires_at": None,
                     "trial_expires_at": "2026-10-28T00:00:00+00:00", "remaining_seconds": None}
    monkeypatch.setattr(lifecycle, "_observe_selected_runtime_admission", lambda *a, **k: dict(selected_fact))
    monkeypatch.setattr(ApplicationRuntimeChannel, "pending_transitions", lambda self: ({"running_step": "publish_verify"},))
    observed = observe_runtime_admission(tmp_path, selection)
    assert observed["admission"] == "recovery_required"
    assert observed["reason"] == "RuntimeTransitionPending"
    assert observed["admission_kind"] == selected_fact["admission_kind"]
    assert observed["trial_expires_at"] == selected_fact["trial_expires_at"]


def test_inventory_admission_returns_to_selected_admission_after_fence_is_removed(tmp_path, monkeypatch):
    from adaos.services.applications import lifecycle

    selected = SimpleNamespace(application_id="test")
    fact = {"admission": "expiring", "reason": "TrialExpiring"}
    monkeypatch.setattr(lifecycle, "_observe_selected_runtime_admission", lambda *a, **k: dict(fact))
    monkeypatch.setattr(ApplicationRuntimeChannel, "pending_transitions", lambda self: ())
    assert observe_runtime_admission(tmp_path, selected) == fact


def release(version, digest):
    return {"version": version, "release_digest": digest}


def dashboard_model():
    return {"application": {"application_id": "app", "publisher_ref": "subnet:owner", "visibility": "public"},
            "channels": {"stable": "stable", "prerelease": "public-beta"},
            "active_release": release("0.1.48", "local-beta"),
            "marketplace_release": release("0.1.44", "stable"),
            "prerelease_release": release("0.1.47", "public-beta"),
            "runtime_revisions": {"alpha": {"version": "0.1.48", "status": "mutable_source"}},
            "lifecycle": {"runtime": {"release_digest": "local-beta", "source": "local_trial", "version": "0.1.48"},
                          "candidate": {"candidate_id": "candidate-48"},
                          "actions": {"open": {"eligible": False}, "promote_stable": {"eligible": False}},
                          "transition": {"status": "recovery_required", "operation_id": "application-stable:app:candidate-48"}},
            "local_development": {"trial": {"version": "0.1.38", "evidence_present": True},
                                  "publication": {"version": "0.1.65", "status": "published"},
                                  "source_registry": {"status": "published", "release_digest": "stable",
                                                      "application_catalog_publication": {"release_digest": "stable"}}}}


def test_dashboard_distinguishes_five_positions_without_using_builder_history():
    value = project_application_dashboard(dashboard_model(), local_subnet_ref="subnet:owner")
    assert [p["id"] for p in value["positions"]] == ["private_alpha", "private_beta", "private_stable", "public_beta", "public_stable"]
    assert value["active_position_id"] == "private_beta"
    assert len([p for p in value["positions"] if p["active"]]) == 1
    assert value["positions"][1]["version"] == "0.1.48"
    assert not value["positions"][1]["actions"]["finalize"]["eligible"]
    assert value["operation"]["stable_version"] == "0.1.44"
    assert "0.1.65" not in str(value) and "0.1.38" not in str(value)


def test_recipient_without_dev_has_only_public_positions():
    model = dashboard_model()
    model["runtime_revisions"] = {"alpha": None}
    model["lifecycle"]["runtime"] = {"release_digest": "public-beta", "source": "stable_installation"}
    value = project_application_dashboard(model, local_subnet_ref="subnet:recipient")
    assert [p["id"] for p in value["positions"]] == ["public_beta", "public_stable"]
    assert value["active_position_id"] == "public_beta"


def test_finalize_replaces_the_accepted_private_beta_position():
    model = dashboard_model()
    model["channels"] = {"stable": "local-beta"}
    model["lifecycle"]["runtime"]["source"] = "stable_installation"
    model["lifecycle"].pop("transition")
    published = model["marketplace_release"]
    value = project_application_dashboard(model, local_subnet_ref="subnet:owner", public_stable=published)
    positions = {p["id"]: p for p in value["positions"]}
    assert "private_beta" not in positions and "public_beta" not in positions
    assert positions["private_stable"]["version"] == "0.1.48"
    assert positions["public_stable"]["version"] == "0.1.44"
    assert value["active_position_id"] == "private_stable"


def test_unpublished_private_stable_does_not_manufacture_publication():
    model = dashboard_model()
    model["channels"].pop("prerelease")
    model["local_development"]["source_registry"]["application_catalog_publication"]["release_digest"] = "another-release"
    value = project_application_dashboard(model, local_subnet_ref="subnet:owner")
    assert not any(p["scope"] == "public" for p in value["positions"])
