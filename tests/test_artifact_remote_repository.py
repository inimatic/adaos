from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    PackageCatalog,
    RemoteReleaseRepository,
    build_artifact_package,
    build_project_release,
)
from adaos.services.root.client import RootHttpError


class _Client:
    def __init__(self) -> None:
        self.packages: dict[str, str] = {}
        self.releases: dict[tuple[str, str], dict[str, Any]] = {}
        self.channels: dict[tuple[str, str], dict[str, Any]] = {}

    def put_artifact_package(self, *, digest: str, archive_b64: str, **kwargs: Any) -> dict:
        self.packages[digest] = archive_b64
        return {"ok": True}

    def get_artifact_package(self, *, digest: str, **kwargs: Any) -> dict:
        return {"ok": True, "archive_b64": self.packages[digest]}

    def put_project_release(
        self,
        *,
        project_id: str,
        release_digest: str,
        release_plan: dict[str, Any],
        **kwargs: Any,
    ) -> dict:
        self.releases[(project_id, release_digest)] = release_plan
        return {"ok": True}

    def get_project_release(self, *, project_id: str, release_digest: str, **kwargs: Any) -> dict:
        return {"ok": True, "release_plan": self.releases[(project_id, release_digest)]}

    def set_artifact_channel(
        self,
        *,
        project_id: str,
        channel: str,
        release_digest: str,
        expected_release_digest: str | None,
        **kwargs: Any,
    ) -> dict:
        previous = self.channels.get((project_id, channel))
        observed = previous["release_digest"] if previous is not None else None
        if observed == release_digest:
            return {"ok": True, "pointer": previous}
        if observed != expected_release_digest:
            raise RuntimeError("channel conflict")
        plan = self.releases[(project_id, release_digest)]
        pointer = {
            "project_id": project_id,
            "channel": channel,
            "release": f"{project_id}@{plan['release']['version']}",
            "release_digest": release_digest,
            "source_revision": plan["release"]["source_ref"]["revision"],
            "updated_at": "2026-07-24T00:00:00Z",
        }
        self.channels[(project_id, channel)] = pointer
        return {"ok": True, "pointer": pointer}

    def get_artifact_channel(self, *, project_id: str, channel: str, **kwargs: Any) -> dict:
        return {"ok": True, "pointer": self.channels[(project_id, channel)]}

    def get_draft_source_tree(
        self,
        *,
        kind: str,
        name: str,
        revision: str,
        node_id: str,
        **kwargs: Any,
    ) -> dict:
        assert (kind, name, node_id) == ("scenarios", "recipes", "node")
        return {
            "ok": True,
            "stored_path": "subnets/dev/nodes/node/scenarios/recipes",
            "commit": revision,
            "tree_sha": "f" * 40,
        }


class _BinaryClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.binary_packages: dict[str, bytes] = {}
        self.binary_calls: list[tuple[str, str]] = []

    def put_artifact_package_bytes(
        self,
        *,
        digest: str,
        archive: bytes,
        **kwargs: Any,
    ) -> dict:
        self.binary_calls.append(("put", digest))
        self.binary_packages[digest] = archive
        return {"ok": True}

    def get_artifact_package_bytes(self, *, digest: str, **kwargs: Any) -> bytes:
        self.binary_calls.append(("get", digest))
        return self.binary_packages[digest]


class _LegacyClient(_Client):
    def put_artifact_package_bytes(self, **kwargs: Any) -> dict:
        raise RootHttpError("not found", status_code=404, error_code="not_found")


class _UncertainBinaryClient(_Client):
    def put_artifact_package_bytes(self, **kwargs: Any) -> dict:
        raise RootHttpError("response lost", status_code=0, error_code=None)

    def get_artifact_package_bytes(self, **kwargs: Any) -> bytes:
        raise RootHttpError("not found", status_code=404, error_code="not_found")


def test_remote_repository_upload_fetch_release_and_channel(tmp_path: Path) -> None:
    scenario = tmp_path / "recipes"
    scenario.mkdir()
    (scenario / "scenario.yaml").write_text("id: recipes\nversion: 1.0.0\n", encoding="utf-8")
    source = ArtifactSourceRef(
        forge="adaos-root",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("subnets/dev/nodes/node/scenarios/recipes/",),
    )
    built = build_artifact_package(scenario, kind="scenario", source_ref=source)
    plan = build_project_release(
        project_id="recipes",
        version="1.0.0",
        source_ref=source,
        components=(built.ref,),
        catalog=PackageCatalog(),
    )
    client = _Client()
    remote = RemoteReleaseRepository(client, verify="ca", cert=("cert", "key"))

    remote.put_release(plan, {built.ref.digest: built.archive_bytes})
    assert base64.b64decode(client.packages[built.ref.digest]) == built.archive_bytes
    assert remote.fetch_package(built.ref) == built.archive_bytes
    assert remote.get_release("recipes", plan.release.release_digest) == plan
    pointer = remote.set_channel(plan, expected_release_digest=None)
    assert remote.get_channel("recipes") == pointer
    assert remote.tree_revision(source) == "f" * 40

    binary_client = _BinaryClient()
    binary_remote = RemoteReleaseRepository(
        binary_client,
        verify="ca",
        cert=("cert", "key"),
    )
    binary_remote.put_release(plan, {built.ref.digest: built.archive_bytes})
    assert binary_client.packages == {}
    assert binary_remote.fetch_package(built.ref) == built.archive_bytes
    assert binary_client.binary_calls == [
        ("put", built.ref.digest),
        ("get", built.ref.digest),
    ]

    legacy_client = _LegacyClient()
    legacy_remote = RemoteReleaseRepository(legacy_client)
    legacy_remote.put_release(plan, {built.ref.digest: built.archive_bytes})
    assert base64.b64decode(legacy_client.packages[built.ref.digest]) == built.archive_bytes
    assert legacy_remote.fetch_package(built.ref) == built.archive_bytes

    uncertain_client = _UncertainBinaryClient()
    with pytest.raises(RootHttpError, match="response lost"):
        RemoteReleaseRepository(uncertain_client).put_release(
            plan,
            {built.ref.digest: built.archive_bytes},
        )
    assert uncertain_client.packages == {}
