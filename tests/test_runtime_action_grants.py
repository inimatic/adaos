from __future__ import annotations

from types import SimpleNamespace

import adaos.services.runtime_action_grants as action_grants
from adaos.services.runtime_action_grants import (
    find_runtime_action_grant,
    remember_runtime_action_grant,
    revoke_runtime_action_grant,
)


def _ctx(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))


def test_runtime_action_grant_is_resource_scoped_durable_and_revocable(tmp_path):
    ctx = _ctx(tmp_path)
    grant = remember_runtime_action_grant(
        ctx,
        subject="controller:phone-1",
        scope="media.playback.control",
        resource="target-tv",
        webspace_id="desktop",
        approval_id="pa.1",
        approved_by="user:owner",
        ttl_seconds=3600,
        now=1000,
    )

    restored = find_runtime_action_grant(
        ctx,
        subject="controller:phone-1",
        scope="media.playback.control",
        resource="target-tv",
        webspace_id="desktop",
        now=1001,
    )
    other_target = find_runtime_action_grant(
        ctx,
        subject="controller:phone-1",
        scope="media.playback.control",
        resource="target-bedroom",
        webspace_id="desktop",
        now=1001,
    )

    assert restored and restored["id"] == grant["id"]
    assert other_target is None
    assert revoke_runtime_action_grant(ctx, grant["id"]) is True
    assert find_runtime_action_grant(
        ctx,
        subject="controller:phone-1",
        scope="media.playback.control",
        resource="target-tv",
        webspace_id="desktop",
        now=1002,
    ) is None


def test_runtime_action_grant_expires(tmp_path):
    ctx = _ctx(tmp_path)
    remember_runtime_action_grant(
        ctx,
        subject="profile:default",
        scope="media.playback.control",
        resource="target-tv",
        webspace_id="desktop",
        approval_id="pa.2",
        approved_by="user:owner",
        ttl_seconds=300,
        now=1000,
    )

    assert find_runtime_action_grant(
        ctx,
        subject="profile:default",
        scope="media.playback.control",
        resource="target-tv",
        webspace_id="desktop",
        now=1301,
    ) is None


def test_runtime_action_grant_retries_transient_replace_denial(tmp_path, monkeypatch):
    real_replace = action_grants.os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return real_replace(source, target)

    monkeypatch.setattr(action_grants.os, "replace", flaky_replace)

    grant = remember_runtime_action_grant(
        _ctx(tmp_path),
        subject="controller:phone-1",
        scope="media.playback.control",
        resource="target-tv",
        webspace_id="desktop",
        approval_id="pa.retry",
        approved_by="user:owner",
        now=1000,
    )

    assert attempts == 3
    assert grant["status"] == "active"
