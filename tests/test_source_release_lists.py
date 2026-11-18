from dataclasses import replace

import pytest

from adaos.domain.artifact_release import ProjectRelease
from adaos.services.artifact_pipeline.channels import ChannelError, ChannelPointer, ReleaseRepository
from test_artifact_channels_subscriptions import _built, _plan


def test_same_version_in_different_source_lists_cannot_collide(tmp_path):
    main = _plan(_built(tmp_path, version="0.3.49", token="a"))
    development = replace(main, release=replace(main.release,
        source_branch="dev2027", release_digest=None).seal())
    assert main.release.release_digest != development.release.release_digest
    main_store = ReleaseRepository(tmp_path / "releases")
    dev_store = ReleaseRepository(main_store.root, source_branch="dev2027")
    main_store.put_release(main)
    dev_store.put_release(development)
    assert main_store.release_digests_by_version("recipes") == {"0.3.49": main.release.release_digest}
    assert dev_store.release_digests_by_version("recipes") == {"0.3.49": development.release.release_digest}
    a = main_store.set_channel("recipes", "stable", main.release.release_digest, expected_release_digest=None)
    b = dev_store.set_channel("recipes", "stable", development.release.release_digest, expected_release_digest=None)
    assert main_store.get_channel("recipes") == a
    assert dev_store.get_channel("recipes") == b
    assert ChannelPointer.from_mapping(b.to_dict()).source_branch == "dev2027"
    with pytest.raises(ChannelError, match="source branch"):
        main_store.set_channel("recipes", "stable", development.release.release_digest,
            expected_release_digest=a.release_digest)
    assert main_store.get_channel("recipes") == a


def test_main_legacy_serialization_and_digest_are_retained(tmp_path):
    main = _plan(_built(tmp_path, version="0.3.49", token="a"))
    encoded = main.release.to_dict()
    assert "source_branch" not in encoded
    assert ProjectRelease.from_mapping(encoded) == main.release
    explicit = {**encoded, "source_branch": "main"}
    assert ProjectRelease.from_mapping(explicit).release_digest == main.release.release_digest


def test_a_version_within_one_branch_stays_immutable(tmp_path):
    first = _plan(_built(tmp_path, version="0.3.49", token="a"))
    second = _plan(_built(tmp_path, version="0.3.49", token="b"))
    dev_store = ReleaseRepository(tmp_path / "releases", source_branch="dev2027")
    first = replace(first, release=replace(first.release, source_branch="dev2027", release_digest=None).seal())
    second = replace(second, release=replace(second.release, source_branch="dev2027", release_digest=None).seal())
    dev_store.put_release(first)
    with pytest.raises(ChannelError, match="already maps"):
        dev_store.put_release(second)
