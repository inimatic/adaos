from __future__ import annotations

import copy
import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from adaos.domain.artifact_release import (
    ArtifactSourceRef,
    canonical_json_bytes,
    canonical_payload_digest,
    sha256_digest,
)
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingDefinition,
    BindingInstance,
    CapabilityContract,
    EnvironmentProfile,
    EvidenceClaim,
)
from adaos.services.artifact_pipeline import (
    ContentAddressedPackageStore,
    PackageCatalog,
    ReleaseRepository,
    build_artifact_package,
    build_project_release,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.capability_binding_state.catalog import (
    PortableContractCatalog,
    portable_record_identity,
)
from adaos.services.capability_binding_state.registry_distribution import (
    ThinDistributionError,
    ThinSemanticDistributionResolver,
)
from adaos.services.capability_binding_state.registry_projection import (
    SemanticRegistryProjection,
)
from adaos.services.capability_binding_state.resolved_bundle import (
    ResolvedBundleError,
    ResolvedSemanticBundleAdmission,
    ResolvedSemanticBundleExporter,
)


NOW = datetime(2026, 9, 29, 13, 0, tzinfo=UTC)
REVISION = "1234567890abcdef1234567890abcdef12345678"


def _source(path: str) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="adaos-root",
        repository="inimatic/adaos-registry",
        revision=REVISION,
        path_scope=(path,),
    )


def _provider(root: Path):
    source = root / "provider"
    (source / "contracts").mkdir(parents=True)
    (source / "handlers").mkdir()
    (source / "handlers" / "main.py").write_text(
        "def invoke(payload):\n    return {'items': []}\n", encoding="utf-8"
    )
    (source / "skill.yaml").write_text(
        """\
name: thin_mail_provider
version: 1.0.0
capabilities: [providers.google.gmail]
tools:
  - name: list_messages
    input_schema:
      type: object
      additionalProperties: false
    output_schema:
      type: object
      required: [items]
      properties:
        items: {type: array}
      additionalProperties: false
""",
        encoding="utf-8",
    )
    (source / "contracts" / "provider.cbs.yaml").write_text(
        """\
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
      errors: [permission_denied]
  authority_requirements: [providers.google.gmail]
binding:
  ref: binding-definition:mail.messages.google-gmail
  version: 1.0.0
  entry_protocol: adaos.skill.tools.v1
  logical_entrypoint: mail.messages.google
  physical_member: handlers/main.py
  modes: [production]
  profile_classes: [local]
  provider_features: [oauth_pkce]
  conformance_obligations: [capability_conformance]
""",
        encoding="utf-8",
    )
    return build_artifact_package(
        source,
        kind="skill",
        source_ref=_source("skills/thin_mail_provider/"),
    )


def _archive_json(data: bytes, name: str) -> dict:
    with zipfile.ZipFile(BytesIO(data), "r") as archive:
        return json.loads(archive.read(name).decode("utf-8"))


def _write_fixture(root: Path):
    built = _provider(root / "source")
    capability = CapabilityContract.from_mapping(
        _archive_json(built.archive_bytes, "contracts/capability.contract.json")
    )
    binding = BindingDefinition.from_mapping(
        _archive_json(built.archive_bytes, "contracts/binding.definition.json")
    )
    delivery = built.binding_deliveries[0]
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
    evidence = EvidenceClaim.create(
        claim_ref="evidence-claim:thin-mail/conformance",
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
        dependencies=({"ref": "google:gmail-api", "observed_version": "v1"},),
        suite_digest="sha256:" + "1" * 64,
        evidence_digest="sha256:" + "2" * 64,
        provenance={"issuer": "subnet:publisher", "runner": "pytest"},
        issued_at=NOW.isoformat(),
        freshness={"max_age_seconds": 3600, "invalidated_by": ["dependency_change"]},
        result="verified",
        redaction={"portable": True, "omitted_fields": []},
        portability_scope="portable",
    )
    requirement = ApplicationRequirement.create(
        requirement_ref="requirement:thin-mail.messages",
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
    plan = build_project_release(
        project_id="thin_mail",
        version="1.0.0",
        source_ref=_source("projects/thin_mail/"),
        components=(built.ref,),
        catalog=PackageCatalog(),
        permissions=("providers.google.gmail",),
        validation_evidence=({"validator": "pytest", "status": "passed"},),
    )
    release_digest = str(plan.release.release_digest)
    records = (capability, binding, delivery, evidence)
    unrelated = CapabilityContract.create(
        capability_ref="capability:unrelated.noop",
        version="1.0.0",
        title="Unrelated",
        operations=(
            {
                "operation_id": "noop",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "errors": [],
            },
        ),
    )
    index = {
        "schema": "adaos.semantic_registry.index.v1",
        "identities": {},
        "records": {},
        "application_releases": {},
    }
    for record in (*records, unrelated):
        identity, version = portable_record_identity(record)
        token = record.digest.removeprefix("sha256:")
        relative = f"semantic/records/sha256/{token[:2]}/{token}.json"
        atomic_write_json(root / "registry" / relative, record.to_dict())
        index["identities"][f"{record.SCHEMA}|{identity}|{version}"] = record.digest
        index["records"][record.digest] = {
            "schema": record.SCHEMA,
            "identity": identity,
            "revision": version,
            "path": relative,
            "published_by_release_digests": [release_digest],
        }
    artifacts: dict[str, list[str]] = {}
    for record in records:
        artifacts.setdefault(record.SCHEMA, []).append(record.digest)
    semantic_release = {
        "schema": "adaos.semantic_registry.application_release.v1",
        "project_id": "thin_mail",
        "version": "1.0.0",
        "application_ref": "application:thin_mail",
        "project_release_digest": release_digest,
        "source_ref": plan.release.source_ref.to_dict(),
        "compilation_digest": "sha256:" + "3" * 64,
        "semantic_revision_digest": "sha256:" + "4" * 64,
        "requirements": [requirement.to_dict()],
        "portable_artifacts": {
            schema: sorted(digests) for schema, digests in sorted(artifacts.items())
        },
        "distribution": {
            "thin": {"requirements_only": True, "registry_required": True},
            "resolved": {
                "project_release_digest": release_digest,
                "package_closure": [item.to_dict() for item in plan.packages],
                "portable_artifact_digests": sorted(
                    record.digest for record in records
                ),
            },
        },
    }
    semantic_release["projection_digest"] = canonical_payload_digest(
        semantic_release
    )
    release_token = release_digest.removeprefix("sha256:")
    app_relative = f"semantic/applications/thin_mail/{release_token}.json"
    atomic_write_json(root / "registry" / app_relative, semantic_release)
    index["application_releases"][f"project:thin_mail@{release_digest}"] = {
        "project_id": "thin_mail",
        "version": "1.0.0",
        "application_ref": "application:thin_mail",
        "project_release_digest": release_digest,
        "semantic_revision_digest": semantic_release["semantic_revision_digest"],
        "path": app_relative,
        "projection_digest": semantic_release["projection_digest"],
    }
    index["index_digest"] = canonical_payload_digest(index)
    atomic_write_json(root / "registry" / "semantic" / "index.json", index)
    query = {
        "schema": "adaos.semantic_registry.query.v1",
        "query_ref": "registry-query:thin-mail/install-1",
        "snapshot": {
            "registry_revision": REVISION,
            "index_digest": index["index_digest"],
        },
        "requirements": [requirement.to_dict()],
        "environment_profile": profile.to_dict(),
        "target_mode": "production",
        "policy_inputs": {
            "allowed_publisher_refs": ["application:thin_mail"],
            "denied_publisher_refs": [],
            "required_authorities": ["providers.google.gmail"],
        },
        "evidence_inputs": {
            "as_of": NOW.isoformat(),
            "required_claim_kinds": ["capability_conformance"],
            "accepted_results": ["verified"],
            "dependency_observations": [
                {"ref": "google:gmail-api", "observed_version": "v1"}
            ],
        },
    }
    return built, plan, query, requirement, unrelated


class _Remote:
    def __init__(self, plan, archive: bytes) -> None:
        self.plan = plan
        self.archive = archive
        self.release_calls = 0
        self.package_calls = 0

    def get_release(self, project_id: str, release_digest: str):
        self.release_calls += 1
        return self.plan

    def fetch_package(self, package):
        self.package_calls += 1
        return self.archive


class _Provenance:
    def __init__(self, status: str = "verified") -> None:
        self.status = status
        self.calls = 0

    def verify_release_plan(self, plan):
        self.calls += 1
        return {
            "schema": "adaos.artifact.attestation_admission.v1",
            "status": self.status,
            "subjects": [{"digest": str(plan.release.release_digest)}],
        }


def _resolver(root: Path, remote: _Remote, provenance: _Provenance):
    return ThinSemanticDistributionResolver(
        projection=SemanticRegistryProjection(
            root / "registry", root / "state"
        ),
        package_store=ContentAddressedPackageStore(root / "packages"),
        remote=remote,
        provenance=provenance,
    )


def test_thin_distribution_resolves_from_cold_cache_without_local_authority(
    tmp_path: Path,
) -> None:
    built, plan, query, _requirement, unrelated = _write_fixture(tmp_path)
    remote = _Remote(plan, built.archive_bytes)
    provenance = _Provenance()

    result = _resolver(tmp_path, remote, provenance).resolve(
        project_id="thin_mail",
        project_release_digest=str(plan.release.release_digest),
        query=query,
        registry_revision=REVISION,
    )

    assert result["status"] == "resolved"
    assert result["activation_performed"] is False
    assert result["local_authority_created"] is False
    assert result["receipt_digest"] == canonical_payload_digest(
        {key: value for key, value in result.items() if key != "receipt_digest"}
    )
    assert remote.release_calls == remote.package_calls == provenance.calls == 1
    assert result["package_acquisition"][0]["source"] == "remote"
    resolution = result["resolutions"][0]
    assert resolution["binding_instances"] == []
    assert resolution["state_attachments"] == []
    assert resolution["provisioning_obligations"][0]["kind"] == "binding_instance"
    assert resolution["project_release_digest"] == plan.release.release_digest
    catalog = PortableContractCatalog(
        tmp_path / "state" / "capability-binding-state" / "portable"
    )
    with pytest.raises(KeyError):
        catalog.load_mapping(unrelated.digest)


def test_thin_distribution_rejects_provenance_before_cache_mutation(
    tmp_path: Path,
) -> None:
    built, plan, query, _requirement, _unrelated = _write_fixture(tmp_path)
    remote = _Remote(plan, built.archive_bytes)
    provenance = _Provenance("rejected")

    with pytest.raises(ThinDistributionError) as rejected:
        _resolver(tmp_path, remote, provenance).resolve(
            project_id="thin_mail",
            project_release_digest=str(plan.release.release_digest),
            query=query,
            registry_revision=REVISION,
        )

    assert rejected.value.code == "provenance_rejected"
    assert remote.package_calls == 0
    assert not (tmp_path / "packages").exists()
    assert not (tmp_path / "state").exists()


def test_thin_distribution_rejects_tampered_package_without_resolution(
    tmp_path: Path,
) -> None:
    built, plan, query, _requirement, _unrelated = _write_fixture(tmp_path)
    remote = _Remote(plan, built.archive_bytes + b"tampered")

    with pytest.raises(ThinDistributionError) as rejected:
        _resolver(tmp_path, remote, _Provenance()).resolve(
            project_id="thin_mail",
            project_release_digest=str(plan.release.release_digest),
            query=query,
            registry_revision=REVISION,
        )

    assert rejected.value.code == "package_fetch_failed"
    assert not (tmp_path / "state").exists()


def test_thin_distribution_requires_exact_application_requirements(
    tmp_path: Path,
) -> None:
    built, plan, query, requirement, _unrelated = _write_fixture(tmp_path)
    changed = ApplicationRequirement.create(
        requirement_ref="requirement:thin-mail.another-consumer",
        capability_ref=requirement.capability_ref,
        contract_range="^1.0.0",
        environment_target=requirement.to_dict()["environment_target"],
        policy_constraints=requirement.to_dict()["policy_constraints"],
        evidence_threshold=requirement.to_dict()["evidence_threshold"],
    )
    mismatched = copy.deepcopy(query)
    mismatched["requirements"] = [changed.to_dict()]
    mismatched["snapshot"] = dict(query["snapshot"])
    remote = _Remote(plan, built.archive_bytes)

    with pytest.raises(ThinDistributionError) as rejected:
        _resolver(tmp_path, remote, _Provenance()).resolve(
            project_id="thin_mail",
            project_release_digest=str(plan.release.release_digest),
            query=mismatched,
            registry_revision=REVISION,
        )

    assert rejected.value.code == "application_requirements_mismatch"
    assert remote.release_calls == 0


def _offline_admission(root: Path) -> ResolvedSemanticBundleAdmission:
    return ResolvedSemanticBundleAdmission(
        package_store=ContentAddressedPackageStore(root / "packages"),
        portable_catalog=PortableContractCatalog(root / "portable"),
        release_repository=ReleaseRepository(root / "releases"),
        receipt_root=root / "receipts",
    )


def test_resolved_bundle_offline_admission_matches_thin_selection_and_is_idempotent(
    tmp_path: Path,
) -> None:
    online = tmp_path / "online"
    built, plan, query, _requirement, unrelated = _write_fixture(online)
    exported = ResolvedSemanticBundleExporter(
        _resolver(online, _Remote(plan, built.archive_bytes), _Provenance())
    ).export(
        project_id="thin_mail",
        project_release_digest=str(plan.release.release_digest),
        query=query,
        registry_revision=REVISION,
    )

    offline = tmp_path / "offline"
    admission = _offline_admission(offline)
    first = admission.admit(
        exported.archive_bytes,
        expected_bundle_digest=exported.bundle_digest,
    )
    second = admission.admit(
        exported.archive_bytes,
        expected_bundle_digest=exported.bundle_digest,
    )

    assert first == second
    assert first["receipt_digest"] == canonical_payload_digest(
        {key: value for key, value in first.items() if key != "receipt_digest"}
    )
    assert first["resolution_digests"] == sorted(
        item["resolution_digest"] for item in exported.thin_receipt["resolutions"]
    )
    assert first["activation_performed"] is False
    assert first["local_authority_created"] is False
    assert admission.package_store.has(built.ref.digest)
    assert admission.release_repository.get_release(
        "thin_mail", str(plan.release.release_digest)
    ) == plan
    with pytest.raises(KeyError):
        admission.portable_catalog.load_mapping(unrelated.digest)


def test_resolved_bundle_rejects_member_tamper_before_cold_cache_mutation(
    tmp_path: Path,
) -> None:
    online = tmp_path / "online"
    built, plan, query, _requirement, _unrelated = _write_fixture(online)
    exported = ResolvedSemanticBundleExporter(
        _resolver(online, _Remote(plan, built.archive_bytes), _Provenance())
    ).export(
        project_id="thin_mail",
        project_release_digest=str(plan.release.release_digest),
        query=query,
        registry_revision=REVISION,
    )
    changed = BytesIO()
    with zipfile.ZipFile(BytesIO(exported.archive_bytes), "r") as source:
        with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == "application.json":
                    payload = b"{}"
                target.writestr(info.filename, payload)
    tampered = changed.getvalue()
    offline = tmp_path / "offline"

    with pytest.raises(ResolvedBundleError) as rejected:
        _offline_admission(offline).admit(
            tampered,
            expected_bundle_digest="sha256:" + __import__("hashlib").sha256(tampered).hexdigest(),
        )

    assert rejected.value.code == "member_digest_mismatch"
    assert not offline.exists()


def test_resolved_bundle_rejects_undeclared_credentials_member(
    tmp_path: Path,
) -> None:
    online = tmp_path / "online"
    built, plan, query, _requirement, _unrelated = _write_fixture(online)
    exported = ResolvedSemanticBundleExporter(
        _resolver(online, _Remote(plan, built.archive_bytes), _Provenance())
    ).export(
        project_id="thin_mail",
        project_release_digest=str(plan.release.release_digest),
        query=query,
        registry_revision=REVISION,
    )
    changed = BytesIO()
    with zipfile.ZipFile(BytesIO(exported.archive_bytes), "r") as source:
        with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                target.writestr(info.filename, source.read(info.filename))
            target.writestr("credentials/refresh-token.json", b'{"refresh_token":"secret"}')
    injected = changed.getvalue()

    with pytest.raises(ResolvedBundleError) as rejected:
        _offline_admission(tmp_path / "offline").admit(
            injected,
            expected_bundle_digest="sha256:" + __import__("hashlib").sha256(injected).hexdigest(),
        )

    assert rejected.value.code == "local_authority_forbidden"


def test_resolved_bundle_requires_out_of_band_sealed_digest(tmp_path: Path) -> None:
    online = tmp_path / "online"
    built, plan, query, _requirement, _unrelated = _write_fixture(online)
    exported = ResolvedSemanticBundleExporter(
        _resolver(online, _Remote(plan, built.archive_bytes), _Provenance())
    ).export(
        project_id="thin_mail",
        project_release_digest=str(plan.release.release_digest),
        query=query,
        registry_revision=REVISION,
    )

    with pytest.raises(ResolvedBundleError) as rejected:
        _offline_admission(tmp_path / "offline").admit(
            exported.archive_bytes + b"changed",
            expected_bundle_digest=exported.bundle_digest,
        )

    assert rejected.value.code == "bundle_digest_mismatch"


def test_resolved_bundle_rejects_resealed_cross_object_closure_drift(
    tmp_path: Path,
) -> None:
    online = tmp_path / "online"
    built, plan, query, _requirement, _unrelated = _write_fixture(online)
    exported = ResolvedSemanticBundleExporter(
        _resolver(online, _Remote(plan, built.archive_bytes), _Provenance())
    ).export(
        project_id="thin_mail",
        project_release_digest=str(plan.release.release_digest),
        query=query,
        registry_revision=REVISION,
    )
    with zipfile.ZipFile(BytesIO(exported.archive_bytes), "r") as source:
        members = {
            info.filename: source.read(info.filename) for info in source.infolist()
        }
    thin = json.loads(members["resolution-receipt.json"])
    thin["portable_record_digests"] = []
    thin.pop("receipt_digest")
    thin["receipt_digest"] = canonical_payload_digest(thin)
    members["resolution-receipt.json"] = canonical_json_bytes(thin)
    manifest = json.loads(members["manifest.json"])
    for entry in manifest["members"]:
        if entry["name"] == "resolution-receipt.json":
            entry["digest"] = sha256_digest(members["resolution-receipt.json"])
            entry["size"] = len(members["resolution-receipt.json"])
    manifest["thin_resolution_receipt_digest"] = thin["receipt_digest"]
    manifest.pop("manifest_digest")
    manifest["manifest_digest"] = canonical_payload_digest(manifest)
    members["manifest.json"] = canonical_json_bytes(manifest)
    changed = BytesIO()
    with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, payload in sorted(members.items()):
            target.writestr(name, payload)
    resealed = changed.getvalue()

    with pytest.raises(ResolvedBundleError) as rejected:
        _offline_admission(tmp_path / "offline").admit(
            resealed,
            expected_bundle_digest=sha256_digest(resealed),
        )

    assert rejected.value.code == "portable_closure_mismatch"
    assert not (tmp_path / "offline").exists()


def test_resolved_bundle_preserves_selection_but_not_local_account_identity(
    tmp_path: Path,
) -> None:
    online = tmp_path / "online"
    built, plan, query, _requirement, _unrelated = _write_fixture(online)
    exported = ResolvedSemanticBundleExporter(
        _resolver(online, _Remote(plan, built.archive_bytes), _Provenance())
    ).export(
        project_id="thin_mail",
        project_release_digest=str(plan.release.release_digest),
        query=query,
        registry_revision=REVISION,
    )
    admitted_a = _offline_admission(tmp_path / "subnet-a").admit(
        exported.archive_bytes, expected_bundle_digest=exported.bundle_digest
    )
    admitted_b = _offline_admission(tmp_path / "subnet-b").admit(
        exported.archive_bytes, expected_bundle_digest=exported.bundle_digest
    )
    assert admitted_a["resolution_digests"] == admitted_b["resolution_digests"]
    resolution = admitted_a["resolutions"][0]
    common = {
        "revision": 1,
        "predecessor_digest": None,
        "tenant_ref": None,
        "binding_definition_ref": resolution["binding_definition"]["ref"],
        "binding_definition_digest": resolution["binding_definition"]["digest"],
        "delivery_digest": resolution["delivery"]["delivery_digest"],
        "environment_profile_ref": resolution["environment_profile_ref"],
        "environment_profile_digest": resolution["environment_profile_digest"],
        "mode": "production",
        "authority_epoch": 1,
    }
    local_a = BindingInstance.create(
        binding_instance_ref="binding-instance:subnet-a/gmail-primary",
        workspace_ref="workspace:subnet-a/desktop",
        local_binding_ref="provider-account:subnet-a/gmail-primary",
        **common,
    )
    local_b = BindingInstance.create(
        binding_instance_ref="binding-instance:subnet-b/gmail-primary",
        workspace_ref="workspace:subnet-b/desktop",
        local_binding_ref="provider-account:subnet-b/gmail-primary",
        **common,
    )

    assert local_a.stable_ref != local_b.stable_ref
    assert local_a.digest != local_b.digest
    assert local_a.to_dict()["binding_definition_digest"] == local_b.to_dict()[
        "binding_definition_digest"
    ]
    assert b"provider-account:subnet-a" not in exported.archive_bytes
    assert b"provider-account:subnet-b" not in exported.archive_bytes
