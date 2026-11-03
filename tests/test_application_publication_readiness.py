from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.sdk.builder import applications
from adaos.services.applications.development import ApplicationDevelopmentCoordinator
from adaos.services.applications.publication_readiness import candidate_publication_blocker
from test_application_distribution import _service, _accepted_candidate
from test_application_lifecycle_operations import operations, verified_operations, plan
from test_native_trial_runtime import activation_record, native_paths
from test_application_service import service, _release


def local_candidate():
    return SimpleNamespace(candidate_id="local-candidate", release_digest="sha256:" + "a" * 64,
        version="0.3.94", source_tree="1" * 40,
        source_ref=ArtifactSourceRef(forge="content-addressed-dev", repository="adaos-dev:home",
            revision="sha256:" + "b" * 64, path_scope=("projects/desktop/",)),
        verification_source_ref=ArtifactSourceRef(forge="content-addressed-dev", repository="adaos-dev:home",
            revision="sha256:" + "c" * 64, path_scope=("scenarios/desktop/",)))


def test_missing_reviewed_source_is_blocked_without_modifying_candidate_or_starting_publication(tmp_path, monkeypatch):
    candidate = local_candidate()
    candidate.source_tree = None
    before = vars(candidate).copy()
    monkeypatch.setattr(applications, "_application", lambda *_args: SimpleNamespace(publisher_ref="subnet:home"))
    monkeypatch.setattr(applications, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(applications, "_distribution_service", lambda: SimpleNamespace(
        candidates=SimpleNamespace(load=lambda _: candidate)))
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    result = coordinator.execute("promote_stable", "desktop", actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.publish", expected_revision=1,
        idempotency_key="finalize", intent={"candidate_id": candidate.candidate_id},
        callback=lambda: applications._promote_stable_effect("desktop", candidate.candidate_id,
            expected_stable_digest=None, actor_ref="user:owner", subnet_ref="subnet:home", expected_revision=1))
    assert result["status"] == "failed"
    assert result["result"]["error"] == "candidate_source_unavailable"
    assert result["result"]["recovery"]["action"] == "restore_reviewed_source_snapshot"
    assert vars(candidate) == before
    assert not (tmp_path / "artifact_pipeline/promotions").exists()


def test_aggregate_local_source_is_publishable_with_published_component_checkpoint(tmp_path):
    candidate = local_candidate()
    candidate.verification_source_ref = replace(candidate.verification_source_ref, forge="adaos-root")
    assert candidate_publication_blocker(candidate, tmp_path) is None


@pytest.mark.parametrize("tree", ["1" * 40, "2" * 64, None, "mutable"])
def test_finalize_prepares_a_local_checkpoint_only_for_an_immutable_source_identity(tmp_path, tree):
    candidate = local_candidate()
    candidate.source_tree = tree
    assert (candidate_publication_blocker(candidate, tmp_path) is None) == bool(tree and len(tree) in {40, 64})


def test_local_only_beta_offers_finalize_and_renewal_without_a_replacement_beta(operations):
    ops, _subnet, _activations, candidate = operations
    candidate = replace(candidate, source_tree="1" * 40, verification_source_ref=local_candidate().verification_source_ref)
    from adaos.services.artifact_pipeline.candidates import CandidateStore
    CandidateStore(ops.state / "artifact_pipeline/candidates").save(candidate)
    snapshot = ops.observer.snapshot("example")
    assert snapshot["actions"]["renew_beta"]["eligible"]
    assert snapshot["actions"]["promote_stable"]["eligible"]
    assert snapshot["candidate"]["publication_blocker"] is None


@pytest.mark.parametrize("visibility", ["private", "link"])
def test_private_stable_updates_do_not_require_public_prerelease(tmp_path, visibility):
    distribution, candidates, releases, packages, remote, admission = _service(tmp_path)
    app = distribution.applications.store.get_application("app_recipes")
    distribution.applications.register(replace(app, visibility=visibility, revision=app.revision + 1), expected_revision=app.revision)
    base = None
    for version in ("1.0.0", "1.1.0"):
        candidate, base = _accepted_candidate(tmp_path, version=version, base=base,
            candidates=candidates, releases=releases, packages=packages, admission=admission)
        previous = distribution.applications.store.get_channels("app_recipes").get("channels", {}).get("stable")
        distribution.publish_trial("app_recipes", candidate.candidate_id, publisher_ref="subnet:publisher", mode="link_only")
        promoted = distribution.promote_stable("app_recipes", candidate.candidate_id,
            publisher_ref="subnet:publisher", expected_stable_digest=previous)
        assert promoted["channel"]["pointer"]["release_digest"] == candidate.release_digest
    assert distribution.applications.store.get_application("app_recipes").visibility == visibility
    assert "prerelease" not in distribution.applications.store.get_channels("app_recipes")["channels"]
    assert remote.channel_writes == 2


def test_publish_rejects_nonstable_release_before_visibility_or_entrypoint_changes(monkeypatch):
    application = SimpleNamespace(publisher_ref="subnet:home", visibility="private", revision=1)
    store = SimpleNamespace(get_application=lambda _: application,
        get_channels=lambda _: {"channels": {"stable": "sha256:" + "a" * 64}})
    monkeypatch.setattr(applications, "_application_service", lambda: SimpleNamespace(store=store))
    with pytest.raises(ValueError, match="exact current stable"):
        applications._publish_to_registry_effect("desktop", "sha256:" + "b" * 64,
            release_notes="", subnet_ref="subnet:home", expected_revision=1)
    assert application.visibility == "private" and application.revision == 1


@pytest.mark.parametrize("fault", [None, "missing", "other_release", "other_project", "tampered", "reused"])
def test_installability_requires_exact_sealed_bundle_receipt(fault):
    from adaos.domain.artifact_release import canonical_payload_digest
    from adaos.services.applications.source_projection import has_installable_distribution
    digest = "sha256:" + "a" * 64
    descriptor = {"schema": "adaos.semantic_registry.default_distribution.v1",
        "project_id": "desktop", "project_release_digest": digest, "bundle_digest": "sha256:" + "b" * 64}
    if fault == "other_release": descriptor["project_release_digest"] = "sha256:" + "c" * 64
    if fault == "other_project": descriptor["project_id"] = "other"
    descriptor["descriptor_digest"] = canonical_payload_digest(descriptor)
    if fault == "tampered": descriptor["bundle_digest"] = "sha256:" + "d" * 64
    if fault == "reused": descriptor["reused"] = True
    receipt = {"semantic_publication": {"status": "prepared"}, "application_catalog_publication": {
        "status": "prepared", "application_id": "app_desktop", "release_digest": digest}}
    if fault != "missing": receipt["default_distribution"] = descriptor
    assert has_installable_distribution(receipt, application_id="app_desktop", project_id="desktop",
        release_digest=digest) == (fault in {None, "reused"})


@pytest.mark.parametrize("fault", [None, "actor", "digest"])
def test_finalize_can_be_recovered_through_core_using_only_original_intent(operations, monkeypatch, fault):
    from adaos.services.applications.operation_recovery import recover_core_operation
    ops, subnet, _activations, _candidate = operations
    candidate = local_candidate()
    candidate.source_tree = None
    coordinator = ApplicationDevelopmentCoordinator(ops.state)
    with pytest.raises(TimeoutError):
        coordinator.execute("promote_stable", "example", actor_ref="user:owner", subnet_ref=subnet,
            capability="applications.publish", expected_revision=1, idempotency_key="unknown-finalize",
            intent={"candidate_id": candidate.candidate_id},
            callback=lambda: (_ for _ in ()).throw(TimeoutError("ack lost")))
    op = coordinator.list()[0]
    assert any(record["operation_id"] == op["operation_id"] and record["recoverable"]
        for record in ops.observer.operations("example")["operations"])
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(applications, "_admit_builder_mutation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(applications, "_state_dir", lambda: ops.state)
    monkeypatch.setattr(applications, "_application", lambda *_: SimpleNamespace(publisher_ref=subnet))
    def missing_operation(_candidate_id):
        raise FileNotFoundError
    monkeypatch.setattr(applications, "_distribution_service", lambda: SimpleNamespace(reconcile=missing_operation,
        candidates=SimpleNamespace(load=lambda _: candidate)))
    args = {"plan_digest": op["intent_digest"], "actor_ref": "user:owner",
        "subnet_ref": subnet, "capability": "applications.recover"}
    if fault == "actor": args["actor_ref"] = "user:other"
    if fault == "digest": args["plan_digest"] = "sha256:" + "e" * 64
    if fault:
        with pytest.raises(Exception, match="original actor|retained development intent"):
            recover_core_operation(ops.owner, op["operation_id"], **args)
        assert coordinator.get(op["operation_id"])["status"] == "unknown"
    else:
        recovered = recover_core_operation(ops.owner, op["operation_id"], **args)
        assert recovered["status"] == "failed"
        assert recovered["result"]["error"] == "candidate_source_unavailable"
        assert recovered["intent"] == op["intent"]


def test_local_stable_acceptance_synchronizes_the_exact_application_channel(tmp_path, monkeypatch):
    distribution, candidates, releases, packages, _remote, admission = _service(tmp_path)
    service = distribution.applications
    app = service.store.get_application("app_recipes")
    service.register(replace(app, visibility="private", revision=app.revision + 1), expected_revision=app.revision)
    candidate, _plan = _accepted_candidate(tmp_path, version="1.0.0", base=None,
        candidates=candidates, releases=releases, packages=packages, admission=admission)
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    def finalize(application_id, candidate_id, **kwargs):
        assert (application_id, candidate_id) == ("app_recipes", candidate.candidate_id)
        assert kwargs["actor_ref"] == "user:owner"
        distribution.publish_trial(application_id, candidate_id, publisher_ref=app.publisher_ref, mode="link_only")
        return distribution.promote_stable(application_id, candidate_id, publisher_ref=app.publisher_ref,
            expected_stable_digest=kwargs["expected_stable_digest"])
    monkeypatch.setattr(applications, "_promote_stable_effect", finalize)
    applications._finalize_local_application_channel("app_recipes", candidate.candidate_id,
        release_digest=candidate.release_digest, actor_ref="user:owner")
    assert service.store.get_channels("app_recipes")["channels"]["stable"] == candidate.release_digest
    assert service.store.get_application("app_recipes").visibility == "private"


def test_publish_backfills_legacy_source_receipt_with_exact_install_distribution(service):
    from adaos.domain.artifact_release import canonical_payload_digest
    from adaos.services.applications.source_projection import (
        GitStableSourcePublisher, StableSourceProjectionService, has_installable_distribution,
    )
    release = service.register_release(_release())
    service.move_channel("app_recipes", "stable", release.release_digest,
        publisher_ref=release.publisher_ref, expected_release_digest=None)
    calls = []
    descriptor = {"schema": "adaos.semantic_registry.default_distribution.v1",
        "project_id": release.project_release.project_id, "project_release_digest": release.release_digest,
        "bundle_digest": "sha256:" + "b" * 64}
    descriptor["descriptor_digest"] = canonical_payload_digest(descriptor)
    def publish_candidate(candidate_id, **_kwargs):
        calls.append(candidate_id)
        result = {"candidate_id": candidate_id, "release_digest": release.release_digest,
            "publication": {"branch": "main", "commit": "1" * 40},
            "semantic_publication": {"status": "prepared"}, "application_catalog_publication": {
                "status": "prepared", "application_id": release.application_id, "release_digest": release.release_digest}}
        if len(calls) > 1: result["default_distribution"] = descriptor
        return result
    projection = StableSourceProjectionService(service, publisher=GitStableSourcePublisher(
        publish_candidate, repository="registry", remote="origin", branch="main"))
    legacy = projection.publish("app_recipes", release.release_digest, publisher_ref=release.publisher_ref, release_notes="")
    assert not has_installable_distribution(legacy, application_id=release.application_id,
        project_id=release.project_release.project_id, release_digest=release.release_digest)
    complete = projection.publish("app_recipes", release.release_digest, publisher_ref=release.publisher_ref,
        release_notes="", require_application_catalog=True)
    assert complete["default_distribution"] == descriptor
    assert projection.publish("app_recipes", release.release_digest, publisher_ref=release.publisher_ref,
        release_notes="", require_application_catalog=True) == complete
    assert len(calls) == 2


def test_common_recovery_resumes_reviewed_beta_publication_but_cannot_start_one(verified_operations, monkeypatch):
    from adaos.services.applications.operation_recovery import recover_core_operation
    ops, subnet, _activations, candidate = verified_operations
    planned = plan(ops, subnet, "promote_stable")
    arguments = dict(plan_digest=planned["plan_digest"], actor_ref="user:owner",
        subnet_ref=subnet, capability="applications.recover")
    with pytest.raises(RuntimeError, match="applications.publish"):
        recover_core_operation(ops.owner, planned["operation_id"], **arguments)
    def interrupted(*_args, **_kwargs):
        raise TimeoutError("ack lost")
    monkeypatch.setattr(applications, "accept_local_trial", interrupted)
    with pytest.raises(TimeoutError):
        ops.apply(planned["operation_id"], plan_digest=planned["plan_digest"], actor_ref="user:owner",
            subnet_ref=subnet, capability="applications.publish")
    def accepted(app, **_kwargs):
        selected = ops.store.get_runtime_selection("desktop", app)
        ops.store.save_runtime_selection(replace(selected, source="stable_installation", runtime_root_ref="workspace",
            revision=selected.revision + 1), expected_revision=selected.revision)
        return {"ok": True, "release_digest": candidate.release_digest}
    monkeypatch.setattr(applications, "accept_local_trial", accepted)
    assert recover_core_operation(ops.owner, planned["operation_id"], **arguments)["status"] == "succeeded"


def test_private_finalize_recovers_after_project_commit_before_runtime_selection(tmp_path, monkeypatch):
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
    candidate = SimpleNamespace(release_digest="sha256:" + "c" * 64, package_digest="sha256:" + "d" * 64)
    application = SimpleNamespace(publisher_ref="subnet:home", visibility="private")
    selections = []
    store = SimpleNamespace(get_channels=lambda _: {"channels": {"stable": "sha256:" + "a" * 64}},
        list_runtime_selections=lambda: selections)
    def publish_trial(application_id, _candidate_id, **kwargs):
        assert kwargs["mode"] == "link_only"
        selections.append(SimpleNamespace(application_id=application_id, release_digest=candidate.release_digest,
            source="stable_installation", webspace_id="desktop"))
        return {"mode": kwargs["mode"]}
    distribution = SimpleNamespace(applications=SimpleNamespace(store=store),
        candidates=SimpleNamespace(load=lambda _: candidate), project_release_is_current=lambda _: True,
        candidate_release_projection=lambda *_args, **_kwargs: object(), publish_trial=publish_trial,
        promote_stable=lambda *_args, **_kwargs: {"release_digest": candidate.release_digest})
    monkeypatch.setattr(applications, "_application", lambda *_: application)
    monkeypatch.setattr(applications, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(applications, "_distribution_service", lambda: distribution)
    monkeypatch.setattr(applications, "_reconcile_promoted_project_runtime", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(applications, "_promote_local_trial_final_verification", lambda *_args, **_kwargs: {"publication_allowed": True})
    monkeypatch.setattr(applications, "ApplicationAccessManagementService", lambda _: SimpleNamespace(
        list_verification_reports=lambda _: [{"release_digest": candidate.release_digest, "overall": "passed", "release_scope": "trial"}]))
    monkeypatch.setattr(TrialActivationStore, "load", lambda *_: {"target": {"webspace_id": "desktop"}})
    result = applications._promote_stable_effect("desktop", "candidate", expected_stable_digest="sha256:" + "a" * 64,
        actor_ref="user:owner", subnet_ref="subnet:home", expected_revision=1)
    assert result["trial_publication"]["mode"] == "link_only"
    assert result["visibility_transition"] is None and application.visibility == "private"
