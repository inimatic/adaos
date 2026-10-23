from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

import pytest

from adaos.services.applications.data_lifecycle import declared_databases
from adaos.services.applications.native_data_adoption import adopted_native_data
from adaos.services.applications.sqlite_data_transition import initialize_sqlite_schema
from adaos.services.skill.manager import SkillManager
from test_local_release_transition import setup, rows  # noqa: F401


def declaration():
    return {'data_lifecycle': {'schema': 'adaos.skill.data_lifecycle.v1', 'execution': 'native_tools',
        'databases': [{'path': 'store.sqlite', 'migrations': [{'version': 1, 'name': 'initial',
            'statements': ['CREATE TABLE entries(id INTEGER PRIMARY KEY, label TEXT)']}]}]}}


def bucket_prepare(tmp_path, manifest, *, resolve=False):
    target = tmp_path / 'new'
    target.mkdir(exist_ok=True)
    manager = SimpleNamespace(_data_migration_config=lambda _: {})
    environment = SimpleNamespace(runtime_bucket=lambda version: version[:3],
        resolve_active_version=lambda: '0.1.0', ensure_data_dirs=lambda _: None)
    slot = SimpleNamespace(version='0.2.0', data_root=target, internal_data_dir=target / 'internal')
    if resolve:
        from adaos.services.skill.manager import PolicyDefaults
        from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
        environment_real = SkillRuntimeEnvironment(skills_root=tmp_path / 'skills', skill_name='sample')
        slot_real = environment_real.build_slot_paths('0.2.0', 'B')
        manager_real = SkillManager(git=SimpleNamespace(), paths=SimpleNamespace(), caps=SimpleNamespace())
        manifest = manager_real._enrich_manifest(
            manifest={**manifest, 'name': 'sample', 'version': '0.2.0'}, slot=slot_real,
            interpreter=tmp_path / 'python', python_paths=[],
            defaults=PolicyDefaults(timeout_seconds=30, retry_count=0, telemetry_enabled=False),
            policy_overrides={}, skill_dir=tmp_path)
    return lambda: SkillManager._prepare_bucket_data(manager, env=environment, slot=slot,
        resolved_manifest={**manifest, 'version': '0.2.0'}, skill_dir=tmp_path, python_paths=[])


@pytest.mark.parametrize('resolve', [False, True])
def test_native_bucket_upgrade_never_copies_legacy_data_over_adopted_data(tmp_path, resolve):
    manifest = declaration()
    prepare = bucket_prepare(tmp_path, manifest, resolve=resolve)
    target = tmp_path / 'new'
    initialize_sqlite_schema(target / 'store.sqlite', declared_databases(manifest)['store.sqlite'])
    with closing(sqlite3.connect(target / 'store.sqlite')) as db:
        db.execute("INSERT INTO entries VALUES(1,'accepted beta')")
        db.commit()
    before = (target / 'store.sqlite').read_bytes()
    with pytest.raises(RuntimeError, match='fenced data lifecycle'):
        prepare()
    component = SimpleNamespace(target_root=target, target_manifest=manifest)
    with adopted_native_data((component,)):
        assert prepare()['mode'] == 'application_data_lifecycle'
    assert (target / 'store.sqlite').read_bytes() == before
    with pytest.raises(RuntimeError, match='fenced data lifecycle'):
        prepare()  # the Core-only scope cannot leak to another invocation


def test_adoption_scope_does_not_initialize_missing_or_drifted_data(tmp_path):
    manifest = declaration()
    prepare = bucket_prepare(tmp_path, manifest)
    target = tmp_path / 'new'
    with adopted_native_data((SimpleNamespace(target_root=target, target_manifest=manifest),)):
        with pytest.raises(ValueError, match='missing'):
            prepare()
    assert not (target / 'store.sqlite').exists()
    with closing(sqlite3.connect(target / 'store.sqlite')) as db:
        db.execute('CREATE TABLE unexpected_data(value TEXT)')
        db.commit()
    with adopted_native_data((SimpleNamespace(target_root=target, target_manifest=manifest),)):
        with pytest.raises(ValueError, match='no migration ledger'):
            prepare()


def test_missing_adopted_database_recovers_from_exact_accepted_snapshot(setup):
    from adaos.services.applications.local_release_transition import bind_local_data_lifecycle, promote_with_local_data
    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    owner, service, _, new, activations, lock = setup
    from adaos.domain.application import ApplicationRelease
    service.register_release(ApplicationRelease(application_id='sample', publisher_ref='subnet:home',
        project_release=new, accepted_candidate_id='candidate-sample',
        acceptance_evidence=({'status': 'passed'},), provenance_refs=(new.release_digest,), lifecycle='trial'))
    runtime = NativeTrialRuntime._resolve_immutable(owner, 'candidate-sample', new.release_digest)
    lifecycle = bind_local_data_lifecycle(owner, runtime, new)
    lifecycle.prepare_beta(webspace_id='desktop', activate=lambda _: {'ok': True})
    activations.update('candidate-sample', status='completed')
    target = lifecycle.components[0].target_root / 'entries.sqlite'
    def interrupted():
        assert target.exists()
        target.unlink()  # synthetic rollback reproduces the former legacy bucket-copy defect
        raise RuntimeError('activation rolled back')
    with pytest.raises(RuntimeError, match='activation rolled back'):
        promote_with_local_data(owner, 'candidate-sample', interrupted)
    assert not target.exists()
    def publish():
        assert rows(target) == [(1, 'private stable')]
        atomic_write_json(owner.paths.workspace_dir() / '.adaos/workspace.lock.json', lock(new).to_dict())
        return {'ok': True, 'status': 'published', 'release_digest': new.release_digest}
    result = promote_with_local_data(owner, 'candidate-sample', publish)
    assert result['data_transition']['completed']
    assert rows(target) == [(1, 'private stable')]
