"""Synthetic lifecycle observations never touch live runtimes or accounts."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from adaos.services.applications.lifecycle import ApplicationLifecycleService


DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    from adaos.services.applications import lifecycle as module
    app = SimpleNamespace(application_id="sample", legacy_project_id="sample", protection={})
    selected = SimpleNamespace(application_id="sample", webspace_id="desktop", source="local_trial",
                               runtime_root_ref="trial:sample-trial", release_digest=DIGEST, revision=2)
    selected.to_dict = lambda: {k: v for k, v in vars(selected).items() if k != "to_dict"}
    candidate = SimpleNamespace(project_id="sample", release_digest=DIGEST, status="accepted",
                                digest=DIGEST, package_digest=DIGEST)
    activation = {"status": "completed", "expires_at": "2026-10-28T21:00:00+00:00",
                  "candidate_ref": {"candidate_id": "sample-trial", "release_digest": DIGEST}}
    store = SimpleNamespace(get_application=lambda _: app, list_runtime_selections=lambda: (selected,),
        get_installation=lambda _: (_ for _ in ()).throw(FileNotFoundError()),
        get_release=lambda *_: SimpleNamespace(project_release=SimpleNamespace(version="1.0.0")),
        list_operations=lambda _: ())
    owner = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    monkeypatch.setattr(module.CandidateStore, "load", lambda *_: candidate)
    monkeypatch.setattr(module.TrialActivationStore, "load", lambda *_: activation)
    return ApplicationLifecycleService(owner, store=store), activation, candidate, store


def test_beta_only_is_installed_and_observed_without_builder_workflow(lifecycle, tmp_path):
    service, _, _, _ = lifecycle
    before = set(tmp_path.rglob("*"))
    model = service.snapshot("sample", now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert model["installation"] == {"present": True, "beta_only": True,
        "stable_baseline_present": False, "stable_release_digest": None}
    assert model["candidate"]["accepted"] and model["actions"]["uninstall"]["eligible"]
    assert model["runtime"]["admission"] == "admitted"
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("day, admission, reason", [(27, "expiring", "TrialExpiring"),
                                                  (29, "expired", "TrialExpired")])
def test_acceptance_does_not_waive_trial_expiry(lifecycle, day, admission, reason):
    service, _, _, _ = lifecycle
    model = service.snapshot("sample", now=datetime(2026, 10, day, tzinfo=timezone.utc))
    assert model["candidate"]["accepted"]
    assert model["runtime"]["admission"] == admission
    assert model["runtime"]["reason"] == reason
    assert model["actions"]["open"]["eligible"] is (admission == "expiring")


def test_mismatched_candidate_is_not_claimed_as_selected_acceptance(lifecycle):
    service, _, candidate, _ = lifecycle
    candidate.project_id = "unrelated"
    model = service.snapshot("sample")
    assert model["candidate"]["reason"] == "CandidateUnavailable"
    assert not model["candidate"]["accepted"]
    assert not model["actions"]["retain_local_beta"]["eligible"]


def test_authorization_is_not_granted_by_action_eligibility(lifecycle):
    model = lifecycle[0].snapshot("sample", now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert all(a["requires_authorization"] for a in model["actions"].values())
    assert model["actions"]["uninstall"]["required_capability"] == "applications.apply"


def test_snapshot_matches_versioned_browser_contract(lifecycle):
    import json
    from pathlib import Path
    import jsonschema

    schema = json.loads((Path(__file__).parents[1] / "src/adaos/abi/application.lifecycle.v1.schema.json").read_text())
    jsonschema.validate(lifecycle[0].snapshot("sample"), schema)


def test_unknown_integrity_does_not_invent_verified_package_health(lifecycle):
    model = lifecycle[0].snapshot("sample", now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert model["runtime"]["integrity"] == "not_checked"
    model = lifecycle[0].snapshot("sample", runtime_revisions={"status": "drift"},
                                  now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert not model["actions"]["open"]["eligible"]
