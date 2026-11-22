import subprocess

import pytest

from adaos.adapters.git.cli_git import CliGitClient, GitError


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True,
        encoding='utf-8', stderr=subprocess.STDOUT, timeout=15).strip()


@pytest.fixture
def repository(tmp_path):
    remote = tmp_path / 'remote.git'
    remote.mkdir()
    git(remote, 'init', '--bare', '--initial-branch=main')
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    git(checkout, 'init', '--initial-branch=main')
    git(checkout, 'config', 'user.name', 'Test')
    git(checkout, 'config', 'user.email', 'test@example.com')
    git(checkout, 'remote', 'add', 'origin', str(remote))
    (checkout / 'source.txt').write_text('qualified', encoding='utf-8')
    git(checkout, 'add', 'source.txt')
    git(checkout, 'commit', '-m', 'Qualified candidate')
    return CliGitClient(), checkout, remote, git(checkout, 'rev-parse', 'HEAD')


def test_publishes_only_the_qualified_commit_and_preserves_unrelated_local_work(repository):
    client, checkout, remote, qualified = repository
    (checkout / 'source.txt').write_text('later local changes', encoding='utf-8')
    git(checkout, 'commit', '-am', 'Unqualified local successor')
    local_head = git(checkout, 'rev-parse', 'HEAD')
    (checkout / 'source.txt').write_text('uncommitted work', encoding='utf-8')
    client.publish_commit(checkout, commit=qualified, ref='refs/heads/main')
    assert git(remote, 'rev-parse', 'refs/heads/main') == qualified
    assert git(checkout, 'rev-parse', 'HEAD') == local_head
    assert (checkout / 'source.txt').read_text(encoding='utf-8') == 'uncommitted work'
    client.publish_commit(checkout, commit=qualified, ref='refs/heads/main')


def test_rejected_publication_cannot_rebase_or_rewrite_the_receiving_branch(repository):
    client, checkout, remote, qualified = repository
    client.publish_commit(checkout, commit=qualified, ref='refs/heads/main')
    (checkout / 'source.txt').write_text('receiving successor', encoding='utf-8')
    git(checkout, 'commit', '-am', 'Receiving successor')
    receiving = git(checkout, 'rev-parse', 'HEAD')
    client.publish_commit(checkout, commit=receiving, ref='refs/heads/main')
    git(checkout, 'checkout', '-b', 'candidate', qualified)
    (checkout / 'source.txt').write_text('divergent candidate', encoding='utf-8')
    git(checkout, 'commit', '-am', 'Divergent candidate')
    candidate = git(checkout, 'rev-parse', 'HEAD')
    with pytest.raises(GitError):
        client.publish_commit(checkout, commit=candidate, ref='refs/heads/main')
    assert git(remote, 'rev-parse', 'refs/heads/main') == receiving
    assert git(checkout, 'rev-parse', 'HEAD') == candidate
    assert not (checkout / '.git/rebase-merge').exists()


def test_catalog_snapshot_is_retained_before_main_and_cannot_be_overwritten(repository):
    client, checkout, remote, qualified = repository
    ref = f'refs/tags/catalog-snapshots/{qualified}'
    client.publish_commit(checkout, commit=qualified, ref=ref)
    assert git(remote, 'rev-parse', ref) == qualified
    with pytest.raises(subprocess.CalledProcessError):
        git(remote, 'rev-parse', 'refs/heads/main')
    (checkout / 'source.txt').write_text('later', encoding='utf-8')
    git(checkout, 'commit', '-am', 'Later candidate')
    with pytest.raises(GitError):
        client.publish_commit(checkout, commit=git(checkout, 'rev-parse', 'HEAD'), ref=ref)
    assert git(remote, 'rev-parse', ref) == qualified


@pytest.mark.parametrize('commit,ref,remote', [
    ('main', 'refs/heads/main', 'origin'),
    ('a' * 40, '--force', 'origin'),
    ('a' * 40, 'refs/heads/main', '--mirror'),
    ('a' * 40, 'refs/heads/main:other', 'origin'),
])
def test_exact_publication_rejects_mutable_or_unsafe_inputs(repository, commit, ref, remote):
    client, checkout, _, _ = repository
    with pytest.raises(GitError):
        client.publish_commit(checkout, commit=commit, ref=ref, remote=remote)
