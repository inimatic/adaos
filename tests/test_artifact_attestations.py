from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.artifact_release import (
    ArtifactSourceRef,
    ProjectRelease,
    canonical_payload_digest,
)
from adaos.services.artifact_pipeline import (
    PACKAGE_PROVENANCE_PREDICATE,
    RELEASE_PROVENANCE_PREDICATE,
    ArtifactAttestation,
    ArtifactAttestationAdmission,
    ArtifactAttestationError,
    ArtifactAttestationPolicy,
    ArtifactAttestationVerificationError,
    ArtifactTrustStore,
    ContentAddressedAttestationStore,
    ContentAddressedPackageStore,
    Ed25519ArtifactSigner,
    ExternalImmutableAttestationStore,
    WorkspaceActivationManager,
    build_artifact_package,
    package_provenance_digest,
    release_provenance_digest,
    verify_artifact_attestation,
)
from adaos.services.artifact_pipeline.activation import ActivationError
from adaos.services.artifact_pipeline.releases import ReleasePlan


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )


def _scenario(root: Path) -> Path:
    scenario = root / "recipes"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.2.3\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text('{"ui": {}}\n', encoding="utf-8")
    return scenario


def _release_plan(root: Path) -> tuple[ReleasePlan, bytes]:
    built = build_artifact_package(
        _scenario(root),
        kind="scenario",
        source_ref=_source(),
    )
    release = ProjectRelease(
        project_id="recipes",
        version="1.2.3",
        source_ref=_source(),
        components=(built.ref,),
        validation_evidence=({"suite": "scenario", "status": "passed"},),
    ).seal()
    return (
        ReleasePlan(
            release=release,
            packages=(built.ref,),
            bindings=(),
            reverse_consumers={},
        ),
        built.archive_bytes,
    )


def _attested_admission(
    root: Path,
    plan: ReleasePlan,
    *,
    issuer: str = "inimatic.release",
) -> tuple[
    ArtifactAttestationAdmission,
    Ed25519ArtifactSigner,
    ArtifactTrustStore,
    ContentAddressedAttestationStore,
]:
    signer = Ed25519ArtifactSigner.generate(issuer=issuer)
    trust = ArtifactTrustStore(root / "trust.json")
    trust.add(signer.trusted_key())
    attestations = ContentAddressedAttestationStore(root / "attestations")
    for package in plan.packages:
        attestations.put(
            signer.sign(
                subject_kind="package",
                subject_digest=package.digest,
                project_id=plan.release.project_id,
                predicate_type=PACKAGE_PROVENANCE_PREDICATE,
                predicate_digest=package_provenance_digest(package),
                issued_at="2026-07-27T00:00:00Z",
            )
        )
    attestations.put(
        signer.sign(
            subject_kind="release",
            subject_digest=str(plan.release.release_digest),
            project_id=plan.release.project_id,
            predicate_type=RELEASE_PROVENANCE_PREDICATE,
            predicate_digest=release_provenance_digest(plan.release),
            issued_at="2026-07-27T00:00:00Z",
        )
    )
    admission = ArtifactAttestationAdmission(
        store=attestations,
        trust_store=trust,
        policy=ArtifactAttestationPolicy(allowed_issuers=(issuer,)),
    )
    return admission, signer, trust, attestations


def test_ed25519_attestation_round_trip_detects_signed_field_tampering(tmp_path: Path) -> None:
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    trust = ArtifactTrustStore(tmp_path / "trust.json")
    key = trust.add(signer.trusted_key())
    attestation = signer.sign(
        subject_kind="package",
        subject_digest="sha256:" + "a" * 64,
        project_id="recipes",
        predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        predicate_digest="sha256:" + "b" * 64,
        issued_at="2026-07-27T00:00:00+00:00",
    )

    loaded = ArtifactAttestation.from_mapping(attestation.to_dict())
    receipt = verify_artifact_attestation(
        loaded,
        trust_store=trust,
        expected_subject_kind="package",
        expected_subject_digest=loaded.subject_digest,
        expected_project_id="recipes",
        expected_predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        allowed_issuers=("inimatic.release",),
    )

    assert receipt["key_id"] == key.key_id
    assert loaded.issued_at == "2026-07-27T00:00:00Z"
    assert loaded.attestation_digest == loaded.computed_digest()

    tampered = loaded.to_dict()
    tampered["predicate_digest"] = "sha256:" + "c" * 64
    tampered["attestation_digest"] = canonical_payload_digest(
        {key: value for key, value in tampered.items() if key != "attestation_digest"}
    )
    tampered_attestation = ArtifactAttestation.from_mapping(tampered)
    with pytest.raises(ArtifactAttestationVerificationError, match="signature is invalid"):
        verify_artifact_attestation(
            tampered_attestation,
            trust_store=trust,
            expected_subject_kind="package",
            expected_subject_digest=loaded.subject_digest,
            expected_project_id="recipes",
            expected_predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        )


def test_valid_signature_over_wrong_provenance_is_not_admitted(tmp_path: Path) -> None:
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    trust = ArtifactTrustStore(tmp_path / "trust.json")
    trust.add(signer.trusted_key())
    attestation = signer.sign(
        subject_kind="package",
        subject_digest="sha256:" + "a" * 64,
        project_id="recipes",
        predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        predicate_digest="sha256:" + "b" * 64,
        issued_at="2026-07-27T00:00:00Z",
    )

    with pytest.raises(
        ArtifactAttestationVerificationError,
        match="does not match requested artifact provenance",
    ):
        verify_artifact_attestation(
            attestation,
            trust_store=trust,
            expected_subject_kind="package",
            expected_subject_digest=attestation.subject_digest,
            expected_project_id="recipes",
            expected_predicate_type=PACKAGE_PROVENANCE_PREDICATE,
            expected_predicate_digest="sha256:" + "c" * 64,
        )


def test_trust_store_rotation_and_revocation_are_fail_closed(tmp_path: Path) -> None:
    first = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    second = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    trust = ArtifactTrustStore(tmp_path / "trust.json")
    first_key = trust.add(first.trusted_key(purposes=("package",)))
    second_key = trust.add(second.trusted_key())

    assert [key.key_id for key in trust.load()] == sorted(
        [first_key.key_id, second_key.key_id]
    )
    attestation = first.sign(
        subject_kind="package",
        subject_digest="sha256:" + "a" * 64,
        project_id="recipes",
        predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        predicate_digest="sha256:" + "b" * 64,
        issued_at="2026-07-27T00:00:00Z",
    )
    trust.revoke(
        str(first_key.key_id),
        reason="publisher key compromised",
        revoked_at="2026-07-27T01:00:00Z",
    )

    with pytest.raises(ArtifactAttestationVerificationError, match="revoked"):
        verify_artifact_attestation(
            attestation,
            trust_store=trust,
            expected_subject_kind="package",
            expected_subject_digest=attestation.subject_digest,
            expected_project_id="recipes",
            expected_predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        )

    corrupt = json.loads((tmp_path / "trust.json").read_text(encoding="utf-8"))
    corrupt["future_policy"] = True
    (tmp_path / "trust.json").write_text(json.dumps(corrupt), encoding="utf-8")
    with pytest.raises(ArtifactAttestationError, match="unsupported fields"):
        trust.load()


def test_trusted_key_purpose_window_and_issuer_policy_are_enforced(tmp_path: Path) -> None:
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    attestation = signer.sign(
        subject_kind="package",
        subject_digest="sha256:" + "a" * 64,
        project_id="recipes",
        predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        predicate_digest="sha256:" + "b" * 64,
        issued_at="2026-07-27T00:00:00Z",
    )

    release_only = ArtifactTrustStore(tmp_path / "release-only.json")
    release_only.add(signer.trusted_key(purposes=("release",)))
    with pytest.raises(ArtifactAttestationVerificationError, match="subject kind"):
        verify_artifact_attestation(
            attestation,
            trust_store=release_only,
            expected_subject_kind="package",
            expected_subject_digest=attestation.subject_digest,
            expected_project_id="recipes",
            expected_predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        )

    future_key = ArtifactTrustStore(tmp_path / "future-key.json")
    future_key.add(
        signer.trusted_key(
            purposes=("package",),
            not_before="2026-07-28T00:00:00Z",
        )
    )
    with pytest.raises(ArtifactAttestationVerificationError, match="predates"):
        verify_artifact_attestation(
            attestation,
            trust_store=future_key,
            expected_subject_kind="package",
            expected_subject_digest=attestation.subject_digest,
            expected_project_id="recipes",
            expected_predicate_type=PACKAGE_PROVENANCE_PREDICATE,
        )

    admitted = ArtifactTrustStore(tmp_path / "admitted.json")
    admitted.add(signer.trusted_key(purposes=("package",)))
    with pytest.raises(ArtifactAttestationVerificationError, match="not allowed"):
        verify_artifact_attestation(
            attestation,
            trust_store=admitted,
            expected_subject_kind="package",
            expected_subject_digest=attestation.subject_digest,
            expected_project_id="recipes",
            expected_predicate_type=PACKAGE_PROVENANCE_PREDICATE,
            allowed_issuers=("another.publisher",),
        )


def test_content_addressed_attestation_store_is_idempotent_and_subject_scoped(
    tmp_path: Path,
) -> None:
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    store = ContentAddressedAttestationStore(tmp_path / "attestations")
    attestation = signer.sign(
        subject_kind="release",
        subject_digest="sha256:" + "d" * 64,
        project_id="recipes",
        predicate_type=RELEASE_PROVENANCE_PREDICATE,
        predicate_digest="sha256:" + "e" * 64,
        issued_at="2026-07-27T00:00:00Z",
    )

    assert store.put(attestation) == attestation.attestation_digest
    assert store.put(attestation) == attestation.attestation_digest
    assert store.list_for_subject("release", attestation.subject_digest) == (attestation,)
    assert store.list_for_subject("package", attestation.subject_digest) == ()


def test_attestation_policy_blocks_activation_before_workspace_mutation(tmp_path: Path) -> None:
    plan, archive = _release_plan(tmp_path / "source")
    package_store = ContentAddressedPackageStore(tmp_path / "packages")
    package_store.put(archive, expected_digest=plan.packages[0].digest)
    empty_trust = ArtifactTrustStore(tmp_path / "empty-trust.json")
    admission = ArtifactAttestationAdmission(
        store=ContentAddressedAttestationStore(tmp_path / "empty-attestations"),
        trust_store=empty_trust,
    )
    workspace = tmp_path / "workspace"
    manager = WorkspaceActivationManager(
        workspace_root=workspace,
        package_store=package_store,
        state_root=tmp_path / "state",
        delayed_verification_seconds=0,
        attestation_admission=admission,
    )

    with pytest.raises(ActivationError, match="attestation admission failed"):
        manager.activate(
            plan,
            idempotency_key="unsigned-release",
            reload_runtime=lambda _lock: {"ok": True},
            health_check=lambda _lock: {"ok": True},
        )

    assert not (workspace / ".adaos" / "workspace.lock.json").exists()
    assert not (workspace / "scenarios" / "recipes").exists()
    assert not manager.operation_path(manager.operation_id("unsigned-release")).exists()


def test_attested_activation_records_exact_verification_receipt(tmp_path: Path) -> None:
    plan, archive = _release_plan(tmp_path / "source")
    package_store = ContentAddressedPackageStore(tmp_path / "packages")
    package_store.put(archive, expected_digest=plan.packages[0].digest)
    admission, _, _, _ = _attested_admission(tmp_path / "authority", plan)
    workspace = tmp_path / "workspace"
    manager = WorkspaceActivationManager(
        workspace_root=workspace,
        package_store=package_store,
        state_root=tmp_path / "state",
        delayed_verification_seconds=0,
        attestation_admission=admission,
    )

    review = manager.plan_activation(plan)
    result = manager.activate(
        plan,
        idempotency_key="attested-release",
        reload_runtime=lambda _lock: {"status": "completed", "runtime": "test"},
        health_check=lambda _lock: {"status": "healthy", "health": "ready"},
    )
    operation = json.loads(manager.operation_path(result.operation_id).read_text(encoding="utf-8"))

    assert review["attestations"]["required"] is True
    assert result.status == "completed"
    assert operation["attestation_verification"]["status"] == "verified"
    assert len(operation["attestation_verification"]["subjects"]) == 2
    assert (workspace / "scenarios" / "recipes" / "scenario.yaml").is_file()


def test_activation_rechecks_revocation_after_remote_fetch(tmp_path: Path) -> None:
    plan, archive = _release_plan(tmp_path / "source")
    package_store = ContentAddressedPackageStore(tmp_path / "packages")
    admission, signer, trust, _ = _attested_admission(tmp_path / "authority", plan)
    workspace = tmp_path / "workspace"
    manager = WorkspaceActivationManager(
        workspace_root=workspace,
        package_store=package_store,
        state_root=tmp_path / "state",
        delayed_verification_seconds=0,
        attestation_admission=admission,
    )
    fetched = 0

    def _fetch(_package):
        nonlocal fetched
        fetched += 1
        trust.revoke(
            str(signer.trusted_key().key_id),
            reason="revoked during fetch",
            revoked_at="2026-07-27T01:00:00Z",
        )
        return archive

    with pytest.raises(ActivationError, match="signing key is revoked"):
        manager.activate(
            plan,
            idempotency_key="revoked-after-fetch",
            fetch_package=_fetch,
            reload_runtime=lambda _lock: {"status": "completed"},
            health_check=lambda _lock: {"status": "healthy"},
        )

    operation_id = manager.operation_id("revoked-after-fetch")
    operation = json.loads(manager.operation_path(operation_id).read_text(encoding="utf-8"))
    assert fetched == 1
    assert package_store.has(plan.packages[0].digest)
    assert operation["status"] == "failed"
    assert operation["rolled_back"] is True
    assert not (workspace / ".adaos" / "workspace.lock.json").exists()
    assert not (workspace / "scenarios" / "recipes").exists()


class _ImmutableAssetClient:
    def __init__(self, *, fail_put: bool = False) -> None:
        self.assets: dict[str, bytes] = {}
        self.put_calls = 0
        self.fail_put = fail_put

    def put_immutable_asset(self, *, name, data, media_type, digest):
        self.put_calls += 1
        if self.fail_put:
            raise TimeoutError("unknown external write outcome")
        assert media_type == "application/vnd.adaos.artifact-attestation+json"
        existing = self.assets.get(name)
        if existing is not None and existing != data:
            raise RuntimeError("immutable conflict")
        self.assets[name] = data
        return {"digest": digest, "created": existing is None}

    def get_immutable_asset(self, *, name):
        return self.assets[name]

    def list_immutable_assets(self, *, prefix):
        return [name for name in self.assets if name.startswith(prefix)]


def test_external_immutable_asset_adapter_round_trips_without_unknown_outcome_retry() -> None:
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    attestation = signer.sign(
        subject_kind="release",
        subject_digest="sha256:" + "f" * 64,
        project_id="recipes",
        predicate_type=RELEASE_PROVENANCE_PREDICATE,
        predicate_digest="sha256:" + "1" * 64,
        issued_at="2026-07-27T00:00:00Z",
    )
    client = _ImmutableAssetClient()
    store = ExternalImmutableAttestationStore(client)

    assert store.put(attestation) == attestation.attestation_digest
    assert store.list_for_subject("release", attestation.subject_digest) == (attestation,)

    failing_client = _ImmutableAssetClient(fail_put=True)
    failing = ExternalImmutableAttestationStore(failing_client)
    with pytest.raises(TimeoutError, match="unknown external write outcome"):
        failing.put(attestation)
    assert failing_client.put_calls == 1


def test_attestation_and_trust_store_payloads_match_abi_schemas(tmp_path: Path) -> None:
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    trust = ArtifactTrustStore(tmp_path / "trust.json")
    trust.add(signer.trusted_key())
    attestation = signer.sign(
        subject_kind="release",
        subject_digest="sha256:" + "a" * 64,
        project_id="recipes",
        predicate_type=RELEASE_PROVENANCE_PREDICATE,
        predicate_digest="sha256:" + "b" * 64,
        issued_at="2026-07-27T00:00:00Z",
    )
    abi = Path(__file__).parents[1] / "src" / "adaos" / "abi"
    attestation_schema = json.loads(
        (abi / "artifact.attestation.v1.schema.json").read_text(encoding="utf-8")
    )
    trust_schema = json.loads(
        (abi / "artifact.trust-store.v1.schema.json").read_text(encoding="utf-8")
    )

    jsonschema.Draft202012Validator(attestation_schema).validate(attestation.to_dict())
    jsonschema.Draft202012Validator(trust_schema).validate(
        json.loads((tmp_path / "trust.json").read_text(encoding="utf-8"))
    )
