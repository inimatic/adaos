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
