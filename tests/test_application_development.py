from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.applications import (
    ApplicationDevelopmentCoordinator,
    ApplicationDevelopmentError,
    ApplicationDevelopmentOutcomeUnknown,
)


@pytest.mark.parametrize('recover', [False, True])
def test_portable_qualification_rejection_is_terminal_not_an_unknown_outcome(tmp_path, recover):
    from adaos.services.capability_binding_state.default_distribution import DefaultDistributionPreflightError
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    def rejected():
        raise DefaultDistributionPreflightError([{'requirement_ref': 'requirement:mail.manage',
                                                  'reasons': ['evidence_missing']}])
    args = dict(actor_ref='user:owner', subnet_ref='subnet:home', capability='applications.publish',
                expected_revision=1, idempotency_key='public-1', intent={'release_digest': 'exact-release'})
    if recover:
        with pytest.raises(TimeoutError):
            coordinator.execute('publish_to_registry', 'mail', callback=lambda: (_ for _ in ()).throw(TimeoutError()), **args)
        operation = coordinator.list('mail')[0]
        result = coordinator.recover(operation['operation_id'], actor_ref='user:owner', subnet_ref='subnet:home',
                                     capability='applications.recover', callback=lambda _: rejected())
    else:
        result = coordinator.execute('publish_to_registry', 'mail', callback=rejected, **args)
    assert result['status'] == 'failed'
    assert result['result']['error']['code'] == 'PortableReleaseQualificationRequired'
    assert result['result']['recovery']['requires_user_review']
    # A rejected, exact intent is a stable receipt, not an unknown outcome.
    repeated = coordinator.execute('publish_to_registry', 'mail',
        callback=lambda: pytest.fail('Terminal rejection was replayed'), **args)
    assert repeated == result


def test_development_summary_cache_drops_heavy_results_and_tracks_authority_changes(tmp_path, monkeypatch):
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    operation = coordinator.execute("create_trial", "app_test", actor_ref="user:owner", subnet_ref="subnet:home",
        capability="applications.develop", expected_revision=1, idempotency_key="trial-1",
        intent={"source_webspace_id": "dev-test"}, callback=lambda: {"evidence": ["large"] * 10000})
    first = coordinator.summaries()
    assert first[0]["intent"] == {"source_webspace_id": "dev-test"}
    assert "result" not in first[0]
    original = coordinator._read
    monkeypatch.setattr(coordinator, "_read", lambda p: pytest.fail("warm summary must not parse retained results"))
    assert coordinator.summaries() == first
    operation.update(status="unknown", updated_at="2026-11-06T00:00:00Z")
    atomic_write_json(coordinator._path(operation["operation_id"]), operation)
    monkeypatch.setattr(coordinator, "_read", original)
    assert coordinator.summaries()[0]["status"] == "unknown"
    assert coordinator.get(operation["operation_id"])["result"]["evidence"] == ["large"] * 10000


def test_development_coordinator_is_idempotent_and_durable(tmp_path: Path) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    calls = []

    def execute():
        calls.append("called")
        return {"ok": True, "candidate_id": "candidate.1"}

    first = coordinator.execute(
        "create_trial", "app_test", actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.develop",
        expected_revision=1, idempotency_key="trial-1",
        intent={"revision": "change.1"}, callback=execute,
    )
    repeated = coordinator.execute(
        "create_trial", "app_test", actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.develop",
        expected_revision=1, idempotency_key="trial-1",
        intent={"revision": "change.1"}, callback=execute,
    )

    assert first == repeated
    assert first["status"] == "succeeded"
    assert calls == ["called"]
    assert coordinator.get(first["operation_id"])["result"]["candidate_id"] == "candidate.1"


def test_development_progress_survives_restart_and_recovery_without_copying_private_payloads(tmp_path):
    import json
    from adaos.services.applications.store import ApplicationStore
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    args = dict(actor_ref='user:owner', subnet_ref='subnet:home', capability='applications.publish',
        expected_revision=1, idempotency_key='private-publication',
        intent={'candidate_id': 'candidate.exact', 'release_digest': 'sha256:exact',
                'source_webspace_id': 'dev-owner', 'prompt': 'private research prompt',
                'release_notes': 'private unfinished draft'})
    with pytest.raises(TimeoutError):
        coordinator.execute('publish_trial', 'app_test', callback=lambda: (_ for _ in ()).throw(
            TimeoutError('upstream-secret-body /private/path')), **args)
    events, cursor = ApplicationStore(tmp_path).list_operation_events(application_id='app_test')
    assert [e['status'] for e in events] == ['planned', 'applying', 'unknown']
    assert events[-1]['operation']['recovery_code'] == 'outcome_unknown:TimeoutError'
    assert 'private research prompt' not in json.dumps(events)
    assert 'upstream-secret-body' not in json.dumps(events)
    assert 'unfinished draft' not in json.dumps(events)
    resumed = ApplicationDevelopmentCoordinator(tmp_path)
    operation = resumed.list('app_test')[0]
    result = resumed.recover(operation['operation_id'], actor_ref='user:owner', subnet_ref='subnet:home',
        capability='applications.recover', callback=lambda _: {'ok': True, 'candidate_id': 'candidate.exact',
                                                               'provider_response': 'private output'})
    remaining, checkpoint = ApplicationStore(tmp_path).list_operation_events(application_id='app_test', cursor=cursor)
    assert [e['status'] for e in remaining] == ['applying', 'succeeded']
    assert [e['operation_revision'] for e in (*events, *remaining)] == [1, 2, 3, 4, 5]
    assert remaining[-1]['operation']['result'] == {'candidate_id': 'candidate.exact'}
    assert 'private output' not in json.dumps(remaining)
    assert ApplicationStore(tmp_path).list_operation_events(application_id='other')[0] == ()
    # An unchanged journal replay is idempotent, including a fresh process owner.
    store = ApplicationStore(tmp_path)
    store.record_development_progress(result)
    assert resumed.execute('publish_trial', 'app_test', callback=lambda: pytest.fail('duplicate publication'), **args) == result
    assert store.list_operation_events(application_id='app_test', cursor=checkpoint)[0] == ()


def test_development_journal_rejects_rewriting_an_existing_revision(tmp_path):
    from adaos.services.applications.store import ApplicationStore, ApplicationStoreError
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    operation = coordinator.execute('preview', 'app_test', actor_ref='user:owner', subnet_ref='subnet:home',
        capability='applications.develop', expected_revision=1, idempotency_key='one', intent={}, callback=lambda: {'ok': True})
    operation['result'] = {'ok': True, 'candidate_id': 'another-candidate'}
    with pytest.raises(ApplicationStoreError, match='different progress'):
        ApplicationStore(tmp_path).record_development_progress(operation)


def test_development_journal_keeps_nested_release_and_source_identity_without_payloads(tmp_path):
    import json
    from adaos.services.applications.store import ApplicationStore
    ApplicationDevelopmentCoordinator(tmp_path).execute('create_trial', 'app_test',
        actor_ref='user:owner', subnet_ref='subnet:home', capability='applications.develop',
        expected_revision=1, idempotency_key='nested-source', intent={}, callback=lambda: {
            'ok': True, 'candidate': {'candidate_id': 'candidate.exact', 'prompt': 'private prompt'},
            'release': {'project_id': 'project.exact', 'release_digest': 'sha256:release', 'private': 'private body'},
            'source_checkpoint': {'commit': 'a' * 40, 'snapshot_digest': 'sha256:source',
                                  'operation_id': 'source.exact', 'archive_b64': 'private archive'}})
    events, _ = ApplicationStore(tmp_path).list_operation_events(application_id='app_test')
    refs = events[-1]['operation']['result']
    assert refs['candidate'] == {'candidate_id': 'candidate.exact'}
    assert refs['release'] == {'project_id': 'project.exact', 'release_digest': 'sha256:release'}
    assert refs['source_checkpoint'] == {'operation_id': 'source.exact',
        'source_commit': 'a' * 40, 'source_snapshot_digest': 'sha256:source'}
    assert 'private' not in json.dumps(events)


def test_recent_progress_starts_near_head_and_resumes_the_same_cursor_without_older_history(tmp_path):
    from adaos.services.applications.store import ApplicationStore, ApplicationStoreError
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    def execute(app, key):
        return coordinator.execute('preview', app, actor_ref='user:owner', subnet_ref='subnet:home',
            capability='applications.develop', expected_revision=1, idempotency_key=key, intent={}, callback=lambda: {'ok': True})
    execute('other', 'one')
    execute('app_test', 'two')
    execute('other', 'three')
    store = ApplicationStore(tmp_path)
    recent, cursor = store.list_operation_events(application_id='app_test', limit=2, tail=True)
    assert [e['status'] for e in recent] == ['applying', 'succeeded']
    assert [e['sequence'] for e in recent] == [5, 6]
    assert store.list_operation_events(application_id='app_test', cursor=cursor)[0] == ()
    execute('app_test', 'four')
    after, _ = store.list_operation_events(application_id='app_test', cursor=cursor)
    assert [e['status'] for e in after] == ['planned', 'applying', 'succeeded']
    assert len({e['event_id'] for e in (*recent, *after)}) == 5
    with pytest.raises(ApplicationStoreError, match='without a cursor'):
        store.list_operation_events(cursor=cursor, tail=True)


def test_development_coordinator_rejects_cross_application_idempotency_reuse(
    tmp_path: Path,
) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    coordinator.execute(
        "create_trial", "app_first", actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.develop",
        expected_revision=1, idempotency_key="shared-key",
        intent={"revision": "change.1"}, callback=lambda: {"ok": True},
    )

    with pytest.raises(ApplicationDevelopmentError, match="authority or intent"):
        coordinator.execute(
            "create_trial", "app_second", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.develop",
            expected_revision=1, idempotency_key="shared-key",
            intent={"revision": "change.1"}, callback=lambda: {"ok": True},
        )


def test_development_coordinator_blocks_blind_retry_after_unknown_outcome(tmp_path: Path) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)

    with pytest.raises(TimeoutError):
        coordinator.execute(
            "publish_trial", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.publish",
            expected_revision=1, idempotency_key="publish-1",
            intent={"candidate_id": "candidate.1"},
            callback=lambda: (_ for _ in ()).throw(TimeoutError("ack lost")),
        )
    with pytest.raises(ApplicationDevelopmentOutcomeUnknown):
        coordinator.execute(
            "publish_trial", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.publish",
            expected_revision=1, idempotency_key="publish-1",
            intent={"candidate_id": "candidate.1"}, callback=lambda: {"ok": True},
        )

    operation = coordinator.list("app_test")[0]
    assert operation["status"] == "unknown"
    assert operation["recovery_reason"].startswith("outcome_unknown:TimeoutError")


def test_development_coordinator_requires_action_capability(tmp_path: Path) -> None:
    with pytest.raises(ApplicationDevelopmentError, match="applications.publish"):
        ApplicationDevelopmentCoordinator(tmp_path).execute(
            "promote_stable", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.develop",
            expected_revision=1, idempotency_key="stable-1", intent={},
            callback=lambda: {"ok": True},
        )


def test_development_coordinator_recovers_only_for_original_owner(tmp_path: Path) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    with pytest.raises(TimeoutError):
        coordinator.execute(
            "preview", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.develop",
            expected_revision=1, idempotency_key="preview-1",
            intent={"source_webspace_id": "desktop"},
            callback=lambda: (_ for _ in ()).throw(TimeoutError("response lost")),
        )

    with pytest.raises(ApplicationDevelopmentError, match="original actor"):
        coordinator.recover(
            coordinator.list()[0]["operation_id"],
            actor_ref="user:other",
            subnet_ref="subnet:home",
            capability="applications.recover",
            callback=lambda _operation: {"ok": True},
        )

    recovered = coordinator.recover(
        coordinator.list()[0]["operation_id"],
        actor_ref="user:owner",
        subnet_ref="subnet:home",
        capability="applications.recover",
        callback=lambda operation: {
            "ok": True,
            "replayed_intent": dict(operation["intent"]),
        },
    )

    assert recovered["status"] == "succeeded"
    assert recovered["recovery_attempt"] == 1
    assert recovered["result"]["replayed_intent"] == {
        "source_webspace_id": "desktop"
    }
