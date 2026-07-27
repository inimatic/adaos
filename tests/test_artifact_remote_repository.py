from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    PACKAGE_PROVENANCE_PREDICATE,
    RELEASE_PROVENANCE_PREDICATE,
    ArtifactAttestationRef,
    Ed25519ArtifactSigner,
    PackageCatalog,
    ReleaseAttestationSet,
    RemoteArtifactAttestationStore,
    RemoteReleaseRepository,
    build_artifact_package,
    build_project_release,
    package_provenance_digest,
    release_provenance_digest,
)
from adaos.services.root.client import RootHttpError


class _Client:
    def __init__(self) -> None:
        self.packages: dict[str, str] = {}
        self.releases: dict[tuple[str, str], dict[str, Any]] = {}
        self.channels: dict[tuple[str, str], dict[str, Any]] = {}
        self.attestations: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.attestation_sets: dict[tuple[str, str], dict[str, Any]] = {}

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

    def put_artifact_attestation(self, *, attestation: dict[str, Any], **kwargs: Any) -> dict:
        key = (attestation["subject_kind"], attestation["subject_digest"])
        values = self.attestations.setdefault(key, [])
        if attestation not in values:
            values.append(attestation)
        return {"ok": True, "attestation_digest": attestation["attestation_digest"]}

    def list_artifact_attestations(
        self,
        *,
        subject_kind: str,
        subject_digest: str,
        **kwargs: Any,
    ) -> dict:
        return {
            "ok": True,
            "attestations": self.attestations.get((subject_kind, subject_digest), []),
        }

    def put_release_attestation_set(
        self,
        *,
        project_id: str,
        release_digest: str,
        attestation_set: dict[str, Any],
        **kwargs: Any,
    ) -> dict:
        key = (project_id, release_digest)
        existing = self.attestation_sets.get(key)
        if existing is not None and existing != attestation_set:
            raise RuntimeError("immutable attestation set conflict")
        self.attestation_sets[key] = attestation_set
        return {"ok": True, "attestation_set": attestation_set}

    def get_release_attestation_set(
        self,
        *,
        project_id: str,
        release_digest: str,
        **kwargs: Any,
    ) -> dict:
        return {
            "ok": True,
            "attestation_set": self.attestation_sets[(project_id, release_digest)],
        }

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


def test_remote_attestations_are_content_addressed_and_bound_to_exact_release(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "recipes"
    scenario.mkdir()
    (scenario / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\n",
        encoding="utf-8",
    )
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
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    signed = (
        signer.sign(
            subject_kind="package",
            subject_digest=built.ref.digest,
            project_id="recipes",
            predicate_type=PACKAGE_PROVENANCE_PREDICATE,
            predicate_digest=package_provenance_digest(built.ref),
            issued_at="2026-07-27T00:00:00Z",
        ),
        signer.sign(
            subject_kind="release",
            subject_digest=str(plan.release.release_digest),
            project_id="recipes",
            predicate_type=RELEASE_PROVENANCE_PREDICATE,
            predicate_digest=release_provenance_digest(plan.release),
            issued_at="2026-07-27T00:00:00Z",
        ),
    )
    client = _Client()
    remote = RemoteReleaseRepository(client)
    store = RemoteArtifactAttestationStore(client)
    remote.put_release(plan, {built.ref.digest: built.archive_bytes})

    for attestation in signed:
        assert store.put(attestation) == attestation.attestation_digest
    exact_set = ReleaseAttestationSet.from_references(
        plan,
        (ArtifactAttestationRef.from_attestation(item) for item in signed),
    )
    assert remote.put_release_attestation_set(exact_set) == exact_set
    assert remote.get_release_attestation_set(
        "recipes",
        str(plan.release.release_digest),
    ) == exact_set
    assert store.list_for_subject("package", built.ref.digest) == (signed[0],)

    with pytest.raises(ValueError, match="does not cover every release subject"):
        ReleaseAttestationSet.from_references(
            plan,
            (ArtifactAttestationRef.from_attestation(signed[0]),),
        )
    wrong_release_ref = ArtifactAttestationRef(
        **{
            **ArtifactAttestationRef.from_attestation(signed[1]).to_dict(),
            "predicate_digest": "sha256:" + "0" * 64,
        }
    )
    with pytest.raises(ValueError, match="does not match exact release provenance"):
        ReleaseAttestationSet.from_references(
            plan,
            (ArtifactAttestationRef.from_attestation(signed[0]), wrong_release_ref),
        )
