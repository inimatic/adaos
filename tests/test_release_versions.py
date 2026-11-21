from concurrent.futures import ThreadPoolExecutor

import pytest

from adaos.services.release_versions import ReleaseVersionAllocator


def reserve(allocator, operation="change-1", branch="main", **extra):
    return allocator.reserve(component="project:example", source_branch=branch,
        operation_id=operation, basis_digest="sha256:" + "a" * 64,
        baseline="0.3.48", **extra)


def test_reservation_survives_restart_and_does_not_bump_on_retry(tmp_path):
    first = reserve(ReleaseVersionAllocator(tmp_path))
    assert first["version"] == "0.3.49"
    assert reserve(ReleaseVersionAllocator(tmp_path)) == first


def test_parallel_lists_have_independent_counters(tmp_path):
    allocator = ReleaseVersionAllocator(tmp_path)
    assert reserve(allocator)["version"] == "0.3.49"
    assert reserve(allocator, "change-2")["version"] == "0.3.50"
    assert reserve(allocator, branch="dev2027")["version"] == "0.3.49"


def test_concurrent_core_reservations_are_unique_and_sequential(tmp_path):
    allocator = ReleaseVersionAllocator(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as executor:
        values = list(executor.map(lambda index: reserve(allocator, str(index))["version"], range(12)))
    assert sorted(int(value.split(".")[-1]) for value in values) == list(range(49, 61))


def test_manual_major_resets_lower_counters(tmp_path):
    allocator = ReleaseVersionAllocator(tmp_path)
    assert reserve(allocator, major=1)["version"] == "1.0.0"
    assert reserve(allocator, "next")["version"] == "1.0.1"


def test_retry_cannot_change_source_basis(tmp_path):
    allocator = ReleaseVersionAllocator(tmp_path)
    reserve(allocator)
    with pytest.raises(ValueError, match="another source basis"):
        allocator.reserve(component="project:example", source_branch="main", operation_id="change-1",
            basis_digest="sha256:" + "b" * 64, baseline="0.3.48")


def test_sealed_identity_cannot_change_digest(tmp_path):
    allocator = ReleaseVersionAllocator(tmp_path)
    reserve(allocator)
    args = dict(component="project:example", source_branch="main", operation_id="change-1")
    sealed = allocator.seal(**args, release_digest="sha256:" + "c" * 64)
    assert allocator.seal(**args, release_digest="sha256:" + "c" * 64) == sealed
    with pytest.raises(ValueError, match="different immutable content"):
        allocator.seal(**args, release_digest="sha256:" + "d" * 64)


@pytest.mark.parametrize('merged_baseline', ['0.3.1', '0.3.999', '9.0.0'])
def test_source_merge_cannot_overwrite_receiving_list_counter(tmp_path, merged_baseline):
    allocator = ReleaseVersionAllocator(tmp_path)
    assert reserve(allocator)['version'] == '0.3.49'
    next_release = allocator.reserve(component='project:example', source_branch='main',
        operation_id='promote-development', basis_digest='sha256:' + 'b' * 64,
        baseline=merged_baseline)
    assert next_release['version'] == '0.3.50'
    assert reserve(allocator, branch='dev2027')['version'] == '0.3.49'


def test_unaccounted_published_version_requires_reconciliation_without_allocating(tmp_path):
    allocator = ReleaseVersionAllocator(tmp_path)
    reserve(allocator)
    path = allocator.root / 'state.json'
    before = path.read_bytes()
    with pytest.raises(ValueError, match='reconcile the receiving release list'):
        reserve(allocator, 'unaccounted-release', known_versions=['0.3.51'])
    assert path.read_bytes() == before
    assert reserve(allocator, 'after-rejection')['version'] == '0.3.50'


def test_exact_legacy_release_bootstraps_receiving_counter_instead_of_merged_source(tmp_path):
    from test_artifact_channels_subscriptions import _built, _plan
    release = _plan(_built(tmp_path, version='0.3.49', token='a')).release
    allocator = ReleaseVersionAllocator(tmp_path / 'state')
    observed = allocator.reconcile_project_releases(project_id='recipes', source_branch='main', releases=[release])
    assert observed['version'] == '0.3.49'
    assert allocator.reserve(component='project:recipes', source_branch='main', operation_id='promote',
        basis_digest='sha256:'+'b'*64, baseline='9.9.999')['version'] == '0.3.50'


def test_legacy_observation_cannot_change_identity_or_cross_source_lists(tmp_path):
    from test_artifact_channels_subscriptions import _built, _plan
    allocator = ReleaseVersionAllocator(tmp_path / 'state')
    release = _plan(_built(tmp_path, version='0.3.49', token='a')).release
    conflicting = _plan(_built(tmp_path, version='0.3.49', token='b')).release
    allocator.reconcile_project_releases(project_id='recipes', source_branch='main', releases=[release])
    before = (allocator.root / 'state.json').read_bytes()
    with pytest.raises(ValueError, match='different immutable content'):
        allocator.reconcile_project_releases(project_id='recipes', source_branch='main', releases=[conflicting])
    with pytest.raises(ValueError, match='source-list identities'):
        allocator.reconcile_project_releases(project_id='recipes', source_branch='dev2027', releases=[release])
    assert (allocator.root / 'state.json').read_bytes() == before


def test_root_reservation_reconciles_sealed_receiving_records_before_allocation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from adaos.services.root.service import RootDeveloperService
    from adaos.services.artifact_pipeline.channels import ReleaseRepository
    from test_artifact_channels_subscriptions import _built, _plan
    repository = ReleaseRepository(tmp_path / 'release-cache')
    plan = _plan(_built(tmp_path, version='0.3.49', token='a'))
    repository.put_release(plan)
    owner = SimpleNamespace(config=SimpleNamespace(), paths=SimpleNamespace(state_dir=lambda:tmp_path / 'state'))
    worker = SimpleNamespace(config=SimpleNamespace(), authority_context=owner,
        authority_state_dir=tmp_path / 'state', paths=SimpleNamespace(state_dir=lambda:tmp_path / 'isolated-worker'))
    service = RootDeveloperService(ctx=worker, config_loader=lambda:None)
    monkeypatch.setattr(service, '_artifact_publication_service', lambda _:SimpleNamespace(release_cache=repository))
    monkeypatch.setattr('adaos.services.source_policy.source_policy', lambda _: {'branch':'main'})
    first = service.reserve_project_release_version('recipes', operation_id='promote',
        basis_digest='sha256:'+'b'*64, baseline='9.9.999')
    assert first['version'] == '0.3.50'
    assert (tmp_path / 'state/release_versions/state.json').is_file()
    assert not (tmp_path / 'isolated-worker/release_versions/state.json').exists()
    assert service.reserve_project_release_version('recipes', operation_id='promote',
        basis_digest='sha256:'+'b'*64, baseline='9.9.999') == first
