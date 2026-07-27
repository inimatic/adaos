from __future__ import annotations

import base64
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from adaos.services.artifact_pipeline.attestation_publication import (
    ArtifactAttestationPublisher,
)
from adaos.services.artifact_pipeline.attestations import (
    ArtifactAttestationAdmission,
    ArtifactAttestationPolicy,
    ArtifactTrustStore,
    Ed25519ArtifactSigner,
)
from adaos.services.artifact_pipeline.remote import (
    ArtifactRegistryClient,
    RemoteArtifactAttestationStore,
)


_HEX_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactTrustRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactTrustRuntime:
    mode: str
    publisher: ArtifactAttestationPublisher | None
    admission: ArtifactAttestationAdmission | None


def _private_key_bytes(path: Path) -> bytes:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ArtifactTrustRuntimeError(f"artifact signing key is not a file: {source}")
    try:
        mode = source.stat().st_mode
        if os.name != "nt" and mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ArtifactTrustRuntimeError(
                "artifact signing key must not be accessible by group or others"
            )
        payload = source.read_bytes()
    except ArtifactTrustRuntimeError:
        raise
    except OSError as exc:
        raise ArtifactTrustRuntimeError(f"cannot read artifact signing key: {exc}") from exc
    if len(payload) == 32:
        return payload
    if len(payload) > 4096:
        raise ArtifactTrustRuntimeError("artifact signing key file exceeds 4096 bytes")
    try:
        token = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ArtifactTrustRuntimeError(
            "artifact signing key must be 32 raw bytes, base64:<value>, or hex:<value>"
        ) from exc
    if token.startswith("hex:") and _HEX_KEY_RE.fullmatch(token[4:]):
        return bytes.fromhex(token[4:])
    if token.startswith("base64:"):
        encoded = token[len("base64:") :]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ArtifactTrustRuntimeError("artifact signing key base64 is invalid") from exc
        if len(decoded) == 32 and base64.b64encode(decoded).decode("ascii") == encoded:
            return decoded
    raise ArtifactTrustRuntimeError(
        "artifact signing key must be 32 raw bytes, canonical base64:<value>, or lowercase hex:<value>"
    )


def compose_artifact_trust_runtime(
    *,
    state_root: Path,
    client: ArtifactRegistryClient,
    verify: Any = None,
    cert: tuple[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ArtifactTrustRuntime:
    values = environ if environ is not None else os.environ
    mode = str(values.get("ADAOS_ARTIFACT_ATTESTATIONS_MODE") or "off").strip().lower()
    if mode not in {"off", "publish", "required"}:
        raise ArtifactTrustRuntimeError(
            "ADAOS_ARTIFACT_ATTESTATIONS_MODE must be off, publish, or required"
        )
    key_file = str(values.get("ADAOS_ARTIFACT_SIGNING_KEY_FILE") or "").strip()
    issuer = str(values.get("ADAOS_ARTIFACT_SIGNING_ISSUER") or "").strip()
    trust_file = str(values.get("ADAOS_ARTIFACT_TRUST_STORE") or "").strip()
    allowed_issuers = tuple(
        sorted(
            {
                item.strip()
                for item in str(
                    values.get("ADAOS_ARTIFACT_ALLOWED_ISSUERS") or ""
                ).split(",")
                if item.strip()
            }
        )
    )
    configured = bool(key_file or issuer or trust_file or allowed_issuers)
    if mode == "off":
        if configured:
            raise ArtifactTrustRuntimeError(
                "artifact trust settings require ADAOS_ARTIFACT_ATTESTATIONS_MODE"
            )
        return ArtifactTrustRuntime(mode=mode, publisher=None, admission=None)

    remote_store = RemoteArtifactAttestationStore(client, verify=verify, cert=cert)
    publisher: ArtifactAttestationPublisher | None = None
    if key_file:
        if not issuer:
            raise ArtifactTrustRuntimeError(
                "ADAOS_ARTIFACT_SIGNING_ISSUER is required with a signing key"
            )
        signer = Ed25519ArtifactSigner.from_private_key_bytes(
            issuer=issuer,
            private_key=_private_key_bytes(Path(key_file)),
        )
        publisher = ArtifactAttestationPublisher(
            state_root=Path(state_root),
            store=remote_store,
            signer=signer,
        )
    elif mode == "publish":
        raise ArtifactTrustRuntimeError(
            "publish mode requires ADAOS_ARTIFACT_SIGNING_KEY_FILE"
        )

    admission: ArtifactAttestationAdmission | None = None
    if mode == "required":
        trust_path = (
            Path(trust_file).expanduser().resolve()
            if trust_file
            else Path(state_root).expanduser().resolve() / "artifact-trust.json"
        )
        trust_store = ArtifactTrustStore(trust_path)
        if not trust_store.load():
            raise ArtifactTrustRuntimeError(
                f"required artifact trust store is missing or empty: {trust_path}"
            )
        admission = ArtifactAttestationAdmission(
            store=remote_store,
            trust_store=trust_store,
            policy=ArtifactAttestationPolicy(allowed_issuers=allowed_issuers),
        )
    return ArtifactTrustRuntime(mode=mode, publisher=publisher, admission=admission)


__all__ = [
    "ArtifactTrustRuntime",
    "ArtifactTrustRuntimeError",
    "compose_artifact_trust_runtime",
]
