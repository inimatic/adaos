from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import pytest

from adaos.domain.artifact_release import (
    ArtifactSourceRef,
    canonical_json_bytes,
    sha256_digest,
)
from adaos.domain.capability_binding_state import (
    BindingDefinition,
    CapabilityContract,
)
from adaos.services.artifact_pipeline import (
    PackageBuildError,
    PackageVerificationError,
    build_artifact_package,
    verify_artifact_package,
)
from adaos.services.artifact_pipeline.cbs_authoring import (
    BINDING_OUTPUT_PATH,
    CAPABILITY_OUTPUT_PATH,
    CBS_PROVIDER_AUTHORING_PATH,
)
from adaos.services.artifact_pipeline.packages import artifact_source_snapshot


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="local",
        repository="inimatic/cbs-authoring-test",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/",),
    )


def _descriptor(*, physical_member: str) -> str:
    return f"""\
schema: adaos.cbs.provider_authoring.v1
authorship:
  origin: builder_inferred
capability:
  ref: capability:mail.messages.manage
  version: 1.0.0
  title: Manage mail messages
  operations:
    - operation_id: list_messages
      tool: list_messages
      errors: [permission_denied, provider_unavailable]
    - operation_id: send_message
      tool: send_message
      errors: [permission_denied, send_unconfirmed]
  invariants:
    - credentials never cross the provider boundary
  effects:
    - reads and sends mail through an authorized account
  authority_requirements: [providers.google.gmail]
  conformance_refs: [scenario:mail.messages.manage.v1]
binding:
  ref: binding-definition:mail.messages.google-gmail
  version: 1.0.0
  entry_protocol: adaos.skill.tools.v1
  logical_entrypoint: mail.messages.google
  physical_member: {physical_member}
  modes: [production]
  profile_classes: [local]
  provider_features: [oauth_pkce, send_idempotency]
  conformance_obligations: [capability_conformance]
"""


def _skill(root: Path, *, name: str, physical_member: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        f"""\
name: {name}
version: 1.0.0
capabilities: [providers.google.gmail]
tools:
  - name: list_messages
    input_schema:
      type: object
      properties:
        query: {{type: string}}
      additionalProperties: false
    output_schema:
      type: object
      required: [items]
      properties:
        items: {{type: array}}
      additionalProperties: false
  - name: send_message
    input_schema:
      type: object
      required: [command_id]
      properties:
        command_id: {{type: string}}
      additionalProperties: false
    output_schema:
      type: object
      required: [status]
      properties:
        status: {{enum: [confirmed, unknown]}}
      additionalProperties: false
""",
        encoding="utf-8",
    )
    member = skill / physical_member
    member.parent.mkdir(parents=True, exist_ok=True)
    member.write_text("def invoke(payload):\n    return payload\n", encoding="utf-8")
    contracts = skill / "contracts"
    contracts.mkdir()
    (contracts / "provider.cbs.yaml").write_text(
        _descriptor(physical_member=physical_member), encoding="utf-8"
    )
    return skill


def _archive_with_replaced_file(
    archive_bytes: bytes, *, path: str, replacement: bytes
) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as source:
        files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files[".adaos/package-manifest.json"])
    files[path] = replacement
    for record in manifest["files"]:
        if record["path"] == path:
            record["size"] = len(replacement)
            record["digest"] = sha256_digest(replacement)
    files[".adaos/package-manifest.json"] = canonical_json_bytes(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in files.items():
            target.writestr(name, data)
    return output.getvalue()


def test_compact_provider_compiles_canonical_contracts_and_exact_delivery(
    tmp_path: Path,
) -> None:
    skill = _skill(
        tmp_path, name="gmail_local_provider", physical_member="handlers/main.py"
    )

    built = build_artifact_package(skill, kind="skill", source_ref=_source())
    verified = verify_artifact_package(
        built.archive_bytes, expected_digest=built.ref.digest
    )

    assert built.binding_deliveries == verified.binding_deliveries
    assert len(built.binding_deliveries) == 1
    delivery = built.binding_deliveries[0]
    assert delivery.package_digest == built.ref.digest
    assert delivery.to_dict()["physical_member"] == "handlers/main.py"
    assert built.package_manifest["cbs"]["authorship_counts"] == {
        "human_authored": 0,
        "builder_inferred": 1,
        "compiler_generated": 2,
    }
    with zipfile.ZipFile(io.BytesIO(built.archive_bytes), "r") as archive:
        capability = CapabilityContract.from_mapping(
            json.loads(archive.read(CAPABILITY_OUTPUT_PATH))
        )
        binding = BindingDefinition.from_mapping(
            json.loads(archive.read(BINDING_OUTPUT_PATH))
        )
    assert [item["operation_id"] for item in capability.to_dict()["operations"]] == [
        "list_messages",
        "send_message",
    ]
    assert binding.digest == delivery.binding_definition_digest


def test_compilation_is_deterministic_and_package_relocation_preserves_definition(
    tmp_path: Path,
) -> None:
    first_root = _skill(
        tmp_path, name="gmail_provider_a", physical_member="handlers/main.py"
    )
    second_root = _skill(
        tmp_path, name="gmail_provider_b", physical_member="runtime/gmail.py"
    )

    first = build_artifact_package(first_root, kind="skill", source_ref=_source())
    repeated = build_artifact_package(first_root, kind="skill", source_ref=_source())
    second = build_artifact_package(second_root, kind="skill", source_ref=_source())

    assert first.ref.digest == repeated.ref.digest
    assert first.binding_deliveries == repeated.binding_deliveries
    assert first.binding_deliveries[0].binding_definition_digest == (
        second.binding_deliveries[0].binding_definition_digest
    )
    assert first.ref.digest != second.ref.digest
    assert first.binding_deliveries[0].digest != second.binding_deliveries[0].digest
    snapshot = artifact_source_snapshot(first_root)
    assert {item["path"] for item in snapshot["files"]}.issuperset(
        {CBS_PROVIDER_AUTHORING_PATH, CAPABILITY_OUTPUT_PATH, BINDING_OUTPUT_PATH}
    )


def test_verifier_recompiles_generated_contracts_instead_of_trusting_package(
    tmp_path: Path,
) -> None:
    skill = _skill(
        tmp_path, name="gmail_tamper_provider", physical_member="handlers/main.py"
    )
    built = build_artifact_package(skill, kind="skill", source_ref=_source())
    tampered = _archive_with_replaced_file(
        built.archive_bytes,
        path=CAPABILITY_OUTPUT_PATH,
        replacement=b'{"schema":"adaos.capability.contract.v1"}',
    )

    with pytest.raises(
        PackageVerificationError, match="generated CBS contract does not match"
    ):
        verify_artifact_package(tampered)


def test_compiler_rejects_owned_outputs_and_undeclared_authority(
    tmp_path: Path,
) -> None:
    skill = _skill(
        tmp_path, name="gmail_invalid_provider", physical_member="handlers/main.py"
    )
    (skill / CAPABILITY_OUTPUT_PATH).write_text("{}", encoding="utf-8")
    with pytest.raises(PackageBuildError, match="must not be authored directly"):
        build_artifact_package(skill, kind="skill", source_ref=_source())

    (skill / CAPABILITY_OUTPUT_PATH).unlink()
    manifest = (skill / "skill.yaml").read_text(encoding="utf-8")
    (skill / "skill.yaml").write_text(
        manifest.replace(
            "capabilities: [providers.google.gmail]", "capabilities: [workspace.read]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(PackageBuildError, match="absent from skill.yaml capabilities"):
        build_artifact_package(skill, kind="skill", source_ref=_source())
