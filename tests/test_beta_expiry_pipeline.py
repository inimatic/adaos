from contextlib import closing
import sqlite3

from adaos.domain.application import ApplicationRelease
from adaos.services.applications.lifecycle_operations import ApplicationLifecycleOperations
from adaos.services.applications.local_release_transition import bind_local_data_lifecycle
from adaos.services.applications.trial_runtime import NativeTrialRuntime
from test_local_release_transition import setup, rows


def test_reviewed_expiry_extension_preserves_real_beta_snapshot_and_verified_packages(setup):
    owner, service, old, new, activations, _lock = setup
    runtime = NativeTrialRuntime.resolve(owner, "candidate-sample", new.release_digest)
    data = bind_local_data_lifecycle(owner, runtime, new)
    data.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    service.register_release(ApplicationRelease(application_id="sample", publisher_ref="subnet:home",
        project_release=new, accepted_candidate_id="candidate-sample",
        acceptance_evidence=({"status": "passed"},), provenance_refs=(new.release_digest,), lifecycle="trial"))
    with closing(sqlite3.connect(data.components[0].beta_root / "entries.sqlite")) as db:
        db.execute("INSERT INTO entries VALUES(2, 'retained beta write')")
        db.commit()
    activations.update("candidate-sample", expires_at="2000-01-01T00:00:00+00:00", health_evidence={"status": "passed"})
    ops = ApplicationLifecycleOperations(owner)
    snapshot = ops.observer.snapshot("sample")
    assert snapshot["runtime"]["reason"] == "TrialExpired"
    plan = ops.plan("sample", "renew_beta", actor_ref="user:owner", subnet_ref="subnet:home",
        capability="applications.plan", idempotency_key="real-package-renewal", expected_revision=snapshot["revision"])
    assert ops.observer.snapshot("sample")["runtime"]["reason"] == "TrialExpired"
    result = ops.apply(plan["operation_id"], plan_digest=plan["plan_digest"], actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.apply")
    assert result["status"] == "succeeded"
    assert NativeTrialRuntime.resolve(owner, "candidate-sample", new.release_digest).verified_source(new.components[0])
    assert rows(data.components[0].beta_root / "entries.sqlite") == [(1, "private stable"), (2, "retained beta write")]
    assert rows(data.components[0].stable_root / "entries.sqlite") == [(1, "private stable")]
    assert service.store.get_runtime_selection("desktop", "sample").runtime_root_ref == "trial:candidate-sample"
