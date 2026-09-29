from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingDefinition,
    BindingDelivery,
    CapabilityContract,
    EnvironmentProfile,
    EvidenceClaim,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.capability_binding_state.catalog import portable_record_identity
from adaos.services.capability_binding_state.registry_projection import (
    SemanticRegistryProjection,
)
from adaos.services.capability_binding_state.registry_query import (
    SemanticRegistryQueryError,
)


NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
REVISION = "0123456789abcdef0123456789abcdef01234567"


def _fixture_records():
    profile = EnvironmentProfile.create(
        profile_ref="profile:local/default",
        profile_class="local",
        modes=("production",),
        provider_features=("oauth_pkce",),
        guarantees={
            "consistency": ["snapshot"],
            "durability": ["persistent"],
            "isolation": ["read_committed"],
        },
        authorities=("providers.google.gmail",),
    )
    capability = CapabilityContract.create(
        capability_ref="capability:mail.messages.manage",
        version="1.0.0",
        title="Manage mail messages",
        operations=(
            {
                "operation_id": "list_messages",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "errors": ["permission_denied"],
            },
        ),
        authority_requirements=("providers.google.gmail",),
    )
    binding = BindingDefinition.create(
        binding_definition_ref="binding-definition:mail.messages.google-gmail",
        version="1.0.0",
        capability_ref=capability.capability_ref,
        capability_version=capability.version,
        entry_protocol="adaos.skill.tools.v1",
        implementation_entrypoint="mail.messages.google",
        state_support=(),
        modes=("production",),
        environment_constraints={
            "profile_classes": ["local"],
            "provider_features": ["oauth_pkce"],
        },
        authority_requirements=("providers.google.gmail",),
        conformance_obligations=("capability_conformance",),
    )
    package_digest = "sha256:" + "a" * 64
    delivery = BindingDelivery.create(
        binding_definition_ref=binding.binding_definition_ref,
        binding_definition_digest=binding.digest,
        logical_entrypoint="mail.messages.google",
        package={
            "kind": "skill",
            "id": "gmail_provider",
            "version": "1.0.0",
            "digest": package_digest,
        },
        physical_member="handlers/main.py",
    )
    evidence = EvidenceClaim.create(
        claim_ref="evidence-claim:gmail/provider/conformance",
        claim_kind="capability_conformance",
        subjects=(
            {
                "kind": "capability_contract",
                "ref": capability.capability_ref,
                "digest": capability.digest,
            },
            {
                "kind": "binding_definition",
                "ref": binding.binding_definition_ref,
                "digest": binding.digest,
            },
        ),
        environment={
            "profile_ref": profile.profile_ref,
            "profile_digest": profile.digest,
        },
        dependencies=(
            {"ref": "google:gmail-api", "observed_version": "v1"},
        ),
        suite_digest="sha256:" + "b" * 64,
        evidence_digest="sha256:" + "c" * 64,
        provenance={"issuer": "subnet:publisher", "runner": "pytest"},
        issued_at=NOW.isoformat(),
        freshness={
            "max_age_seconds": 3600,
            "invalidated_by": ["dependency_change"],
        },
        result="verified",
        redaction={"portable": True, "omitted_fields": []},
        portability_scope="portable",
    )
    requirement = ApplicationRequirement.create(
        requirement_ref="requirement:gmail.consumer.mail",
        capability_ref=capability.capability_ref,
        contract_range="^1.0.0",
        environment_target={
            "profile_ref": profile.profile_ref,
            "allowed_modes": ["production"],
        },
        policy_constraints={
            "locality": "remote_allowed",
            "privacy": "application-declared",
            "required_authorities": ["providers.google.gmail"],
        },
        evidence_threshold={
            "required_claim_kinds": ["capability_conformance"],
            "allow_stale": False,
        },
    )
    return profile, capability, binding, delivery, evidence, requirement


def _write_registry(root: Path, records) -> dict:
    semantic = root / "semantic"
    release_digest = "sha256:" + "d" * 64
    index = {
        "schema": "adaos.semantic_registry.index.v1",
        "identities": {},
        "records": {},
        "application_releases": {
            f"project:gmail_provider@{release_digest}": {
                "project_id": "gmail_provider",
                "version": "1.0.0",
                "application_ref": "application:gmail_provider",
                "project_release_digest": release_digest,
                "semantic_revision_digest": "sha256:" + "e" * 64,
                "path": f"semantic/applications/gmail_provider/{'d' * 64}.json",
                "projection_digest": "sha256:" + "f" * 64,
            }
        },
    }
    for record in records:
        identity, revision = portable_record_identity(record)
        token = record.digest.removeprefix("sha256:")
        relative = f"semantic/records/sha256/{token[:2]}/{token}.json"
        atomic_write_json(root / relative, record.to_dict())
        index["identities"][f"{record.SCHEMA}|{identity}|{revision}"] = record.digest
        index["records"][record.digest] = {
            "schema": record.SCHEMA,
            "identity": identity,
            "revision": revision,
            "path": relative,
            "published_by_release_digests": [release_digest],
        }
    index["index_digest"] = canonical_payload_digest(index)
    atomic_write_json(semantic / "index.json", index)
    return index


def _query(index: dict, profile: EnvironmentProfile, requirement: ApplicationRequirement):
    return {
        "schema": "adaos.semantic_registry.query.v1",
        "query_ref": "registry-query:gmail-consumer/install-1",
        "snapshot": {
            "registry_revision": REVISION,
            "index_digest": index["index_digest"],
        },
        "requirements": [requirement.to_dict()],
        "environment_profile": profile.to_dict(),
        "target_mode": "production",
        "policy_inputs": {
            "allowed_publisher_refs": ["application:gmail_provider"],
            "denied_publisher_refs": [],
            "required_authorities": ["providers.google.gmail"],
        },
        "evidence_inputs": {
            "as_of": (NOW + timedelta(minutes=30)).isoformat(),
            "required_claim_kinds": ["capability_conformance"],
            "accepted_results": ["verified"],
            "dependency_observations": [
                {"ref": "google:gmail-api", "observed_version": "v1"}
            ],
        },
    }


def test_snapshot_query_returns_exact_read_only_candidate(tmp_path: Path) -> None:
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    registry = tmp_path / "registry"
    state = tmp_path / "state"
    index = _write_registry(registry, (capability, binding, delivery, evidence))
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    result = SemanticRegistryProjection(registry, state).query(
        _query(index, profile, requirement),
        registry_revision=REVISION,
    )

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert after == before
    assert result["status"] == "matched"
    assert result["activation_performed"] is False
    assert result["local_authority_created"] is False
    assert result["result_digest"] == canonical_payload_digest(
        {key: value for key, value in result.items() if key != "result_digest"}
    )
    candidate = result["requirements"][0]["candidates"][0]
    assert candidate["capability_contract"]["digest"] == capability.digest
    assert candidate["binding_definition"]["digest"] == binding.digest
    assert candidate["delivery"]["digest"] == delivery.digest
    assert candidate["delivery"]["package"]["digest"] == "sha256:" + "a" * 64
    assert candidate["evidence"] == [
        {
            "claim_ref": evidence.claim_ref,
            "claim_kind": "capability_conformance",
            "claim_digest": evidence.digest,
            "status": "admissible",
        }
    ]
    assert candidate["publisher_release_digests"] == ["sha256:" + "d" * 64]
    assert set(result["record_digests"]) == {
        capability.digest,
        binding.digest,
        delivery.digest,
        evidence.digest,
    }


def test_snapshot_query_fails_closed_on_drift_and_unknown_fields(tmp_path: Path) -> None:
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    registry = tmp_path / "registry"
    index = _write_registry(registry, (capability, binding, delivery, evidence))
    projection = SemanticRegistryProjection(registry, tmp_path / "state")
    request = _query(index, profile, requirement)

    with pytest.raises(SemanticRegistryQueryError) as drift:
        projection.query(request, registry_revision="different")
    assert drift.value.code == "snapshot_drift"

    malformed = copy.deepcopy(request)
    malformed["credential_ref"] = "secret:gmail"
    with pytest.raises(SemanticRegistryQueryError) as invalid:
        projection.query(malformed, registry_revision=REVISION)
    assert invalid.value.code == "query_invalid"


def test_snapshot_query_explains_stale_external_evidence(tmp_path: Path) -> None:
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    registry = tmp_path / "registry"
    index = _write_registry(registry, (capability, binding, delivery, evidence))
    request = _query(index, profile, requirement)
    request["evidence_inputs"]["as_of"] = (NOW + timedelta(hours=2)).isoformat()

    result = SemanticRegistryProjection(registry, tmp_path / "state").query(
        request,
        registry_revision=REVISION,
    )

    assert result["status"] == "unmatched"
    assert result["requirements"][0]["candidates"] == []
    assert any(
        item["code"] == "stale_evidence"
        for item in result["requirements"][0]["rejections"]
    )


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    (
        (
            lambda request: request["policy_inputs"]["denied_publisher_refs"].append(
                "application:gmail_provider"
            ),
            "publisher_denied",
        ),
        (
            lambda request: request["evidence_inputs"]["dependency_observations"][
                0
            ].update(observed_version="v2"),
            "evidence_dependency_mismatch",
        ),
    ),
)
def test_snapshot_query_explains_policy_and_dependency_rejections(
    tmp_path: Path,
    mutate,
    expected_code: str,
) -> None:
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    registry = tmp_path / "registry"
    index = _write_registry(registry, (capability, binding, delivery, evidence))
    request = _query(index, profile, requirement)
    if expected_code == "publisher_denied":
        request["policy_inputs"]["allowed_publisher_refs"] = []
    mutate(request)

    result = SemanticRegistryProjection(registry, tmp_path / "state").query(
        request,
        registry_revision=REVISION,
    )

    assert result["status"] == "unmatched"
    assert any(
        item["code"] == expected_code
        for item in result["requirements"][0]["rejections"]
    )


def test_snapshot_query_rejects_tampered_portable_record(tmp_path: Path) -> None:
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    registry = tmp_path / "registry"
    index = _write_registry(registry, (capability, binding, delivery, evidence))
    entry = index["records"][capability.digest]
    path = registry / entry["path"]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["title"] = "Tampered"
    atomic_write_json(path, value)

    with pytest.raises(SemanticRegistryQueryError) as invalid:
        SemanticRegistryProjection(registry, tmp_path / "state").query(
            _query(index, profile, requirement),
            registry_revision=REVISION,
        )
    assert invalid.value.code == "registry_invalid"
