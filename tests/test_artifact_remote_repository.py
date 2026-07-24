from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    PackageCatalog,
    RemoteReleaseRepository,
    build_artifact_package,
    build_project_release,
)


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
        **kwargs: Any,
    ) -> dict:
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
    pointer = remote.set_channel(plan)
    assert remote.get_channel("recipes") == pointer
    assert remote.tree_revision(source) == "f" * 40
