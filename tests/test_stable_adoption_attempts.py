from dataclasses import replace
import json

import pytest

from adaos.services.applications.development import ApplicationDevelopmentCoordinator
from adaos.services.applications.publication_attempts import reviewed_publication, observed_recovered_adoption
from adaos.services.applications.runtime_channel import RuntimeChannelConflict
from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep
from test_application_data_lifecycle import coordinator, seed, sql


def review(tmp_path, operation_id, *, fresh=True, candidate_id="candidate1"):
    return reviewed_publication({"action": "promote_stable", "application_id": "sample",
        "operation_id": operation_id, "actor_ref": "user:owner", "created_at": "2026-11-02T12:00:00+00:00",
        "intent": {"candidate_id": candidate_id}, "recovered_adoption_digest": (
            observed_recovered_adoption(tmp_path / "state", "sample", candidate_id) if fresh else None)})


def preflight_recovered(tmp_path):
    _state, stable, channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    def fail(_key):
        raise ValueError("old read-only preflight failure")
    with pytest.raises(ValueError):
        lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: pytest.fail("No publication"), prepare_core_state=fail)
    lifecycle.abort_stable_adoption_before_effects(verify_source=lambda _: {"ok": True})
    return lifecycle, stable, channel


def test_new_finalize_after_verified_recovery_keeps_history_and_adopts_current_beta_writes(tmp_path):
    lifecycle, stable, channel = preflight_recovered(tmp_path)
    runner = ApplicationRuntimeTransition(channel)
    old = runner.get(lifecycle.stable_operation_id)
    old_intent = (lifecycle.recovery / "stable.intent.json").read_bytes()
    beta = lifecycle.components[0].beta_root / "records.sqlite"
    sql(beta, "INSERT INTO records(id,value) VALUES(2,'after recovery')")
    selected = channel.read()[0]
    channel.select(replace(selected, webspace_id="mobile", revision=1), expected_revision=0)
    with review(tmp_path, "new-finalize"):
        retry = coordinator(tmp_path, 1)
        retry.select_stable_attempt()
        accepted = retry.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True})
    assert accepted["completed"]
    assert accepted["operation_id"] != old["operation_id"]
    assert runner.current(old["operation_id"])["operation_id"] == accepted["operation_id"]
    assert runner.get(old["operation_id"]) == old
    assert (lifecycle.recovery / "stable.intent.json").read_bytes() == old_intent
    assert sql(stable / "records.sqlite", "SELECT id,value FROM records ORDER BY id") == [(1, "original"), (2, "after recovery")]
    assert len(channel.read()) == 2 and all(s.runtime_root_ref == "workspace" for s in channel.read())


def test_recovered_publication_after_data_adoption_uses_fresh_snapshots(tmp_path):
    _state, stable, channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    component = lifecycle.components[0]
    beta = component.beta_root / "records.sqlite"
    sql(beta, "INSERT INTO records(id,value) VALUES(2,'first attempt')")
    with pytest.raises(TimeoutError):
        lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: (_ for _ in ()).throw(TimeoutError()))
    old_snapshot = lifecycle._root(component, "accepted") / "records.sqlite"
    old_bytes = old_snapshot.read_bytes()
    baseline = lifecycle._root(component, "stable") / "records.sqlite"
    digest = json.loads(baseline.with_suffix(".sqlite.receipt.json").read_text())["digest"]
    lifecycle.data.install(baseline, stable / "records.sqlite", staged_digest=digest)
    runner = ApplicationRuntimeTransition(channel)
    old = runner.abort(lifecycle.stable_operation_id, contract_digest=lifecycle._contract(),
        steps=[TransitionStep("verify_restored_source", lambda _: {"ok": True})])
    sql(beta, "INSERT INTO records(id,value) VALUES(3,'new Beta work')")
    with review(tmp_path, "retry-after-publication-rollback"):
        retry = coordinator(tmp_path, 1)
        retry.select_stable_attempt()
        assert retry.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True})["completed"]
    assert old_snapshot.read_bytes() == old_bytes
    assert runner.get(old["operation_id"]) == old
    assert sql(stable / "records.sqlite", "SELECT value FROM records ORDER BY id") == [("original",), ("first attempt",), ("new Beta work",)]


def test_interrupted_new_attempt_resumes_original_effect_key_without_repeating_data_adoption(tmp_path):
    _lifecycle, _stable, channel = preflight_recovered(tmp_path)
    keys = []
    def fail(key):
        keys.append(key)
        raise TimeoutError("publication acknowledgement lost")
    with review(tmp_path, "retry"):
        retry = coordinator(tmp_path, 1)
        retry.select_stable_attempt()
        with pytest.raises(TimeoutError):
            retry.accept_beta(webspace_id="desktop", publish=fail)
    with review(tmp_path, "another-finalize"):
        other = coordinator(tmp_path, 1)
        with pytest.raises(RuntimeChannelConflict, match="Another reviewed publication"):
            other.select_stable_attempt()
    with review(tmp_path, "retry", fresh=False):
        resumed = coordinator(tmp_path, 1)
        resumed.select_stable_attempt()
        assert resumed.stable_operation_id == retry.stable_operation_id
        assert resumed.accept_beta(webspace_id="desktop", publish=lambda key: keys.append(key) or {"ok": True})["completed"]
    assert keys[0] == keys[1]
    assert all(s.runtime_root_ref == "workspace" for s in channel.read())


def test_old_cancelled_review_cannot_reopen_or_take_over_new_attempt(tmp_path):
    _state, _stable, channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    with review(tmp_path, "old-review"):
        lifecycle.select_stable_attempt()
        with pytest.raises(ValueError):
            lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True},
                prepare_core_state=lambda _: (_ for _ in ()).throw(ValueError()))
    lifecycle.abort_stable_adoption_before_effects(verify_source=lambda _: {"ok": True})
    with review(tmp_path, "new-review"):
        retry = coordinator(tmp_path, 1)
        retry.select_stable_attempt()
    pointer = (lifecycle.recovery / "stable-attempts/current.json").read_bytes()
    with review(tmp_path, "old-review", fresh=False):
        old = coordinator(tmp_path, 1)
        old.select_stable_attempt()
        with pytest.raises(RuntimeChannelConflict, match="cancelled"):
            old.accept_beta(webspace_id="desktop", publish=lambda _: pytest.fail("No old effects"))
    assert (lifecycle.recovery / "stable-attempts/current.json").read_bytes() == pointer
    assert channel.read()[0].runtime_root_ref == "trial:candidate1"


def test_pending_recovery_never_creates_another_transition(tmp_path):
    _state, _stable, channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    with pytest.raises(TimeoutError):
        lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: (_ for _ in ()).throw(TimeoutError()))
    runner = ApplicationRuntimeTransition(channel)
    with pytest.raises(TimeoutError):
        runner.abort(lifecycle.stable_operation_id, contract_digest=lifecycle._contract(),
            steps=[TransitionStep("restore_source", lambda _: (_ for _ in ()).throw(TimeoutError()))])
    before = runner.get(lifecycle.stable_operation_id)
    with review(tmp_path, "cannot-bypass-recovery"):
        with pytest.raises(RuntimeChannelConflict, match="Finish verification"):
            coordinator(tmp_path, 1).select_stable_attempt()
    assert runner.get(lifecycle.stable_operation_id) == before
    assert not (lifecycle.recovery / "stable-attempts/current.json").exists()


def test_cancelled_legacy_intent_cannot_restart_through_recovery_instead_of_new_confirmation(tmp_path):
    lifecycle, _stable, _channel = preflight_recovered(tmp_path)
    with review(tmp_path, "legacy-operation", fresh=False):
        with pytest.raises(RuntimeChannelConflict, match="new reviewed Finalize"):
            coordinator(tmp_path, 1).select_stable_attempt()
    assert not (lifecycle.recovery / "stable-attempts/current.json").exists()


def test_attempt_pointer_lost_ack_can_resume_without_forking_again(tmp_path, monkeypatch):
    lifecycle, _stable, _channel = preflight_recovered(tmp_path)
    from adaos.services.applications import publication_attempts
    write = publication_attempts.atomic_write_json
    def fail_index(path, payload):
        if path.parent.name == "reviews":
            raise OSError("crash before index acknowledgement")
        return write(path, payload)
    with monkeypatch.context() as patch, review(tmp_path, "retry"):
        patch.setattr(publication_attempts, "atomic_write_json", fail_index)
        with pytest.raises(OSError):
            coordinator(tmp_path, 1).select_stable_attempt()
    pointer = (lifecycle.recovery / "stable-attempts/current.json").read_bytes()
    with review(tmp_path, "retry", fresh=False):
        resumed = coordinator(tmp_path, 1)
        resumed.select_stable_attempt()
        assert resumed.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True})["completed"]
    assert (lifecycle.recovery / "stable-attempts/current.json").read_bytes() == pointer


def test_review_must_name_the_exact_candidate(tmp_path):
    lifecycle, _stable, _channel = preflight_recovered(tmp_path)
    with review(tmp_path, "wrong-candidate", candidate_id="other"):
        with pytest.raises(RuntimeChannelConflict, match="another Candidate"):
            coordinator(tmp_path, 1).select_stable_attempt()
    assert not (lifecycle.recovery / "stable-attempts/current.json").exists()


def test_core_development_coordinator_supplies_and_recovers_the_same_review(tmp_path):
    _lifecycle, _stable, channel = preflight_recovered(tmp_path)
    core = ApplicationDevelopmentCoordinator(tmp_path / "state")
    def fail():
        lifecycle = coordinator(tmp_path, 1)
        lifecycle.select_stable_attempt()
        return lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        core.execute("promote_stable", "sample", actor_ref="user:owner", subnet_ref="subnet:local",
            capability="applications.publish", expected_revision=1, idempotency_key="finalize",
            intent={"candidate_id": "candidate1"}, callback=fail)
    operation = core.list("sample")[0]
    def resume(_operation):
        lifecycle = coordinator(tmp_path, 1)
        lifecycle.select_stable_attempt()
        return lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True})
    result = core.recover(operation["operation_id"], actor_ref="user:owner", subnet_ref="subnet:local",
        capability="applications.recover", callback=resume)
    assert result["status"] == "succeeded" and channel.read()[0].runtime_root_ref == "workspace"


def test_core_review_survives_failure_before_source_preparation_or_attempt_selection(tmp_path):
    lifecycle, _stable, channel = preflight_recovered(tmp_path)
    core = ApplicationDevelopmentCoordinator(tmp_path / "state")
    with pytest.raises(TimeoutError):
        core.execute("promote_stable", "sample", actor_ref="user:owner", subnet_ref="subnet:local",
            capability="applications.publish", expected_revision=1, idempotency_key="before-source",
            intent={"candidate_id": "candidate1"}, callback=lambda: (_ for _ in ()).throw(TimeoutError()))
    operation = core.list("sample")[0]
    assert operation["recovered_adoption_digest"]
    assert not (lifecycle.recovery / "stable-attempts/current.json").exists()
    def resume(_operation):
        retry = coordinator(tmp_path, 1)
        retry.select_stable_attempt()
        return retry.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True})
    result = core.recover(operation["operation_id"], actor_ref="user:owner", subnet_ref="subnet:local",
        capability="applications.recover", callback=resume)
    assert result["status"] == "succeeded" and channel.read()[0].runtime_root_ref == "workspace"


def test_review_made_before_a_later_cancellation_cannot_start_another_attempt(tmp_path):
    _lifecycle, _stable, channel = preflight_recovered(tmp_path)
    core = ApplicationDevelopmentCoordinator(tmp_path / "state")
    with pytest.raises(TimeoutError):
        core.execute("promote_stable", "sample", actor_ref="user:owner", subnet_ref="subnet:local",
            capability="applications.publish", expected_revision=1, idempotency_key="earlier-review",
            intent={"candidate_id": "candidate1"}, callback=lambda: (_ for _ in ()).throw(TimeoutError()))
    operation = core.list("sample")[0]
    with review(tmp_path, "later-reviewed-attempt"):
        later = coordinator(tmp_path, 1)
        later.select_stable_attempt()
        with pytest.raises(ValueError):
            later.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True},
                prepare_core_state=lambda _: (_ for _ in ()).throw(ValueError()))
    later.abort_stable_adoption_before_effects(verify_source=lambda _: {"ok": True})
    pointer = (later.recovery / "stable-attempts/current.json").read_bytes()
    result = core.recover(operation["operation_id"], actor_ref="user:owner", subnet_ref="subnet:local",
        capability="applications.recover", callback=lambda _: coordinator(tmp_path, 1).select_stable_attempt())
    assert result["status"] == "failed"
    assert result["result"]["error"] == "cancelled_publication_requires_new_review"
    assert (later.recovery / "stable-attempts/current.json").read_bytes() == pointer
    assert channel.read()[0].runtime_root_ref == "trial:candidate1"


@pytest.mark.parametrize("fault", ["recovery_receipt", "binding_digest", "area", "parent_changed"])
def test_damaged_recovery_or_attempt_binding_never_adopts_data(tmp_path, fault):
    lifecycle, _stable, channel = preflight_recovered(tmp_path)
    runner = ApplicationRuntimeTransition(channel)
    if fault == "recovery_receipt":
        record = runner.get(lifecycle.stable_operation_id)
        record["recovery"]["receipts"].clear()
        runner._write(lifecycle.stable_operation_id, record)
    else:
        with review(tmp_path, "retry"):
            coordinator(tmp_path, 1).select_stable_attempt()
        pointer = lifecycle.recovery / "stable-attempts/current.json"
        binding = json.loads(pointer.read_text())
        if fault == "parent_changed":
            record = runner.get(lifecycle.stable_operation_id)
            record["recovery"]["completed_at"] = "changed"
            runner._write(lifecycle.stable_operation_id, record)
        else:
            binding[fault] = "../other" if fault == "area" else "sha256:" + "0" * 64
            pointer.write_text(json.dumps(binding), encoding="utf-8")
    with review(tmp_path, "retry"):
        with pytest.raises(RuntimeChannelConflict):
            coordinator(tmp_path, 1).select_stable_attempt()
    assert channel.read()[0].runtime_root_ref == "trial:candidate1"
