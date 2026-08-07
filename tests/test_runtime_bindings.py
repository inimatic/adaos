from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from adaos.domain.execution import ExecutionAttempt, ExecutionResourceRequest, ExecutionSpec
from adaos.domain.relational_storage import RelationalStorageBinding, RelationalStorageRequirements
from adaos.domain.runtime_bindings import (
    ContentRef,
    RuntimeBindingContractError,
    ServiceBinding,
)


def test_content_ref_is_generic_and_content_addressed() -> None:
    ref = ContentRef(
        uri="adaos-evidence:bundle/primary-analysis.json",
        digest="sha256:" + "a" * 64,
        size_bytes=123,
        media_type="application/json",
        owner_ref="skill:research_manager",
        kind="evidence",
        metadata={"protocol_digest": "sha256:" + "b" * 64},
    )
    assert ref.to_dict()["schema"] == "adaos.content.ref.v1"
    assert ref.to_dict()["kind"] == "evidence"


def test_service_binding_is_redacted_and_consumer_scoped() -> None:
    binding = ServiceBinding(
        binding_id="service-binding.mlflow.local",
        capability="tracker.experiment",
        provider_ref="service:mlflow-tracker",
        consumer_ref="skill:research-manager",
        endpoint="http://127.0.0.1:5050/api",
        protocol="mlflow-rest",
        protocol_version="2.0",
        health_endpoint="http://127.0.0.1:5050/health",
        ui_endpoint="http://127.0.0.1:5050/",
        secret_ref="skill-secret:mlflow-token",
    )
    payload = binding.to_dict()
    encoded = json.dumps(payload).lower()
    assert payload["schema"] == "adaos.service.binding.v1"
    assert payload["consumer_ref"] == "skill:research-manager"
    assert "password" not in encoded
    assert "token=" not in encoded


def test_service_binding_rejects_inline_credentials() -> None:
    with pytest.raises(RuntimeBindingContractError, match="credentials"):
        ServiceBinding(
            binding_id="bad",
            capability="tracker.experiment",
            provider_ref="service:mlflow-tracker",
            consumer_ref="skill:research-manager",
            endpoint="http://user:password@127.0.0.1:5050/api",
            protocol="mlflow-rest",
            protocol_version="2.0",
        )


def test_service_binding_rejects_inline_secret_values() -> None:
    with pytest.raises(RuntimeBindingContractError, match="opaque reference"):
        ServiceBinding(
            binding_id="bad-secret",
            capability="tracker.experiment",
            provider_ref="service:mlflow-tracker",
            consumer_ref="skill:research-manager",
            endpoint="http://127.0.0.1:5050/api",
            protocol="mlflow-rest",
            protocol_version="2.0",
            secret_ref="token=inline-secret",
        )


def test_relational_binding_rejects_inline_secret_values() -> None:
    with pytest.raises(ValueError, match="opaque reference"):
        RelationalStorageBinding(
            binding_id="relbind.bad-secret",
            provider_id="postgresql",
            owner_ref="skill:fixture",
            logical_name="main",
            isolation="database",
            locator="adaos-db:fixture",
            migration_owner="skill:fixture",
            secret_ref="password=inline-secret",
        )


def test_arf05_contract_payloads_validate_against_packaged_abi(tmp_path) -> None:
    abi_root = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi"
    names = (
        "content.ref.v1.schema.json",
        "service.binding.v1.schema.json",
        "storage.relational_requirement.v1.schema.json",
        "storage.relational_binding.v1.schema.json",
        "execution.spec.v1.schema.json",
        "execution.attempt.v1.schema.json",
    )
    schemas = {
        name: json.loads((abi_root / name).read_text(encoding="utf-8"))
        for name in names
    }
    registry = Registry()
    for name, schema in schemas.items():
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(name, resource)
        registry = registry.with_resource(str(schema["$id"]), resource)

    content = ContentRef(
        uri="adaos-content:fixture/input",
        digest="sha256:" + "c" * 64,
        size_bytes=4,
        media_type="text/plain",
        owner_ref="skill:fixture",
    )
    service = ServiceBinding(
        binding_id="binding.fixture",
        capability="executor.jobs",
        provider_ref="service:local-executor",
        consumer_ref="skill:fixture",
        endpoint="http://127.0.0.1:9000/api",
        protocol="adaos-executor",
        protocol_version="1",
    )
    requirement = RelationalStorageRequirements(migration_owner="skill:fixture")
    storage_binding = RelationalStorageBinding(
        binding_id="relbind.fixture",
        provider_id="sqlite",
        owner_ref="skill:fixture",
        logical_name="main",
        isolation="file",
        locator="skill-data:db/main.db",
        migration_owner="skill:fixture",
        capabilities={},
    )
    spec = ExecutionSpec(
        spec_id="fixture.v1",
        owner_ref="skill:fixture",
        command=("python", "-c", "print('ok')"),
        working_directory=str(tmp_path),
        resources=ExecutionResourceRequest(wall_time_s=1),
        inputs=(content,),
    )
    attempt = ExecutionAttempt(
        attempt_id="attempt.fixture",
        owner_ref="skill:fixture",
        spec_id=spec.spec_id,
        spec_digest=spec.digest,
        provider_id="local-process",
        provider_attempt_id="attempt.fixture",
        idempotency_key="fixture",
        status="accepted",
    )
    payloads = {
        "content.ref.v1.schema.json": content.to_dict(),
        "service.binding.v1.schema.json": service.to_dict(),
        "storage.relational_requirement.v1.schema.json": requirement.to_dict(),
        "storage.relational_binding.v1.schema.json": storage_binding.to_dict(),
        "execution.spec.v1.schema.json": spec.to_dict(),
        "execution.attempt.v1.schema.json": attempt.to_dict(),
    }
    for name, payload in payloads.items():
        Draft202012Validator(schemas[name], registry=registry).validate(payload)
