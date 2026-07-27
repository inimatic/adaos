from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.artifact_pipeline import (
    ArtifactTrustRuntimeError,
    ArtifactTrustStore,
    Ed25519ArtifactSigner,
    compose_artifact_trust_runtime,
)


class _Client:
    pass


def test_artifact_trust_runtime_is_explicit_and_off_by_default(tmp_path: Path) -> None:
    runtime = compose_artifact_trust_runtime(
        state_root=tmp_path,
        client=_Client(),
        environ={},
    )
    assert runtime.mode == "off"
    assert runtime.publisher is None
    assert runtime.admission is None

    with pytest.raises(ArtifactTrustRuntimeError, match="require.*MODE"):
        compose_artifact_trust_runtime(
            state_root=tmp_path,
            client=_Client(),
            environ={"ADAOS_ARTIFACT_SIGNING_ISSUER": "inimatic.release"},
        )


def test_publish_mode_loads_one_persistent_private_key_without_auto_trust(
    tmp_path: Path,
) -> None:
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    key_path = tmp_path / "publisher.key"
    key_path.write_bytes(signer.private_key_bytes())
    key_path.chmod(0o600)

    runtime = compose_artifact_trust_runtime(
        state_root=tmp_path / "state",
        client=_Client(),
        environ={
            "ADAOS_ARTIFACT_ATTESTATIONS_MODE": "publish",
            "ADAOS_ARTIFACT_SIGNING_KEY_FILE": str(key_path),
            "ADAOS_ARTIFACT_SIGNING_ISSUER": "inimatic.release",
        },
    )

    assert runtime.publisher is not None
    assert runtime.publisher.signer.private_key_bytes() == signer.private_key_bytes()
    assert runtime.admission is None
    assert not (tmp_path / "state" / "artifact-trust.json").exists()


def test_required_mode_fails_closed_until_trust_store_is_provisioned(
    tmp_path: Path,
) -> None:
    trust_path = tmp_path / "trust.json"
    with pytest.raises(ArtifactTrustRuntimeError, match="missing or empty"):
        compose_artifact_trust_runtime(
            state_root=tmp_path / "state",
            client=_Client(),
            environ={
                "ADAOS_ARTIFACT_ATTESTATIONS_MODE": "required",
                "ADAOS_ARTIFACT_TRUST_STORE": str(trust_path),
            },
        )

    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    trust = ArtifactTrustStore(trust_path)
    trust.add(signer.trusted_key())
    runtime = compose_artifact_trust_runtime(
        state_root=tmp_path / "state",
        client=_Client(),
        environ={
            "ADAOS_ARTIFACT_ATTESTATIONS_MODE": "required",
            "ADAOS_ARTIFACT_TRUST_STORE": str(trust_path),
            "ADAOS_ARTIFACT_ALLOWED_ISSUERS": "inimatic.release",
        },
    )

    assert runtime.admission is not None
    assert runtime.admission.policy.allowed_issuers == ("inimatic.release",)
    assert runtime.publisher is None


def test_signing_key_encoding_and_issuer_are_strict(tmp_path: Path) -> None:
    key_path = tmp_path / "publisher.key"
    key_path.write_text("not-a-key", encoding="ascii")
    key_path.chmod(0o600)
    with pytest.raises(ArtifactTrustRuntimeError, match="must be 32 raw bytes"):
        compose_artifact_trust_runtime(
            state_root=tmp_path / "state",
            client=_Client(),
            environ={
                "ADAOS_ARTIFACT_ATTESTATIONS_MODE": "publish",
                "ADAOS_ARTIFACT_SIGNING_KEY_FILE": str(key_path),
                "ADAOS_ARTIFACT_SIGNING_ISSUER": "inimatic.release",
            },
        )

    key_path.write_bytes(b"x" * 32)
    key_path.chmod(0o600)
    with pytest.raises(ArtifactTrustRuntimeError, match="ISSUER"):
        compose_artifact_trust_runtime(
            state_root=tmp_path / "state",
            client=_Client(),
            environ={
                "ADAOS_ARTIFACT_ATTESTATIONS_MODE": "publish",
                "ADAOS_ARTIFACT_SIGNING_KEY_FILE": str(key_path),
            },
        )
