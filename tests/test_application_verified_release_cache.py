import json
import os
from types import SimpleNamespace

import pytest
from adaos.services.applications import store as module


@pytest.fixture
def cached_release(tmp_path, monkeypatch):
    store = module.ApplicationStore(tmp_path)
    digest = "sha256:" + "a" * 64
    path = store._release_path("application", digest)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "application_id": "application",
                "release_digest": digest,
                "nested": {"title": "original"},
                "components": [{"kind": "skill", "artifact_id": "worker"}],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def parse(payload):
        calls.append(payload)
        if payload.get("invalid"):
            raise ValueError("invalid canonical release")
        components = [SimpleNamespace(**item) for item in payload.get("components", [])]
        return SimpleNamespace(**payload, project_release=SimpleNamespace(components=components))

    monkeypatch.setattr(module.ApplicationRelease, "from_mapping", parse)
    module._VERIFIED_RELEASE_CACHE.clear()
    return store, digest, path, calls


def test_only_validated_parsing_is_reused_and_nested_mutation_is_isolated(cached_release):
    store, digest, _, calls = cached_release
    first = store.get_release("application", digest)
    first.nested["title"] = "caller mutation"
    assert store.get_release("application", digest).nested["title"] == "original"
    assert len(calls) == 1


def test_cache_does_not_bypass_path_identity(cached_release):
    store, digest, _, _ = cached_release
    store.get_release("application", digest)
    with pytest.raises(module.ApplicationStoreError, match="identity mismatch"):
        store.get_release("application", "md5:" + digest.split(":")[1])


def test_same_timestamp_content_change_is_revalidated(cached_release):
    store, digest, path, calls = cached_release
    store.get_release("application", digest)
    stamp = path.stat()
    payload = json.loads(path.read_text())
    payload["invalid"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="invalid canonical"):
        store.get_release("application", digest)
    assert len(calls) == 2


def test_missing_or_nonobject_content_cannot_reuse_verified_release(cached_release):
    store, digest, path, _ = cached_release
    store.get_release("application", digest)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(module.ApplicationStoreError, match="not an object"):
        store.get_release("application", digest)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        store.get_release("application", digest)


def test_component_membership_reuses_verified_release_without_exposing_cached_object(
    cached_release,
):
    store, digest, _, calls = cached_release
    assert store.release_has_component("application", digest, kind="skill", artifact_id="worker") is True
    assert store.release_has_component("application", digest, kind="skill", artifact_id="missing") is False
    release = store.get_release("application", digest)
    release.project_release.components.clear()
    assert store.release_has_component("application", digest, kind="skill", artifact_id="worker") is True
    assert len(calls) == 1
