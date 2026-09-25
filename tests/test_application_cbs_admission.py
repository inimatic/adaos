from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from adaos.domain.capability_binding_state import ApplicationRequirement
from adaos.services.applications.cbs import ApplicationCBSService
from adaos.services.applications.cbs_admission import (
    NativeApplicationCBSAdmissionService,
)
from adaos.services.applications.deployment_executor import (
    ApplicationDeploymentExecutor,
)
from adaos.services.capability_binding_state import (
    PortableContractCatalog,
)
from adaos.services.capability_binding_state.registry_projection import (
    SemanticRegistryProjection,
)
from adaos.services.artifact_pipeline import (
    ContentAddressedPackageStore,
    PackageCatalog,
    build_artifact_package,
    build_project_release,
)
from adaos.services.applications.store import ApplicationStore


FIXED_NOW = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)


def _source(scope: str) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="local",
        repository="inimatic/application-cbs-admission-test",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=(scope,),
    )


def _scenario(root: Path):
    source = root / "mail_client"
    source.mkdir(parents=True)
    (source / "scenario.yaml").write_text(
        "id: mail_client\nversion: 1.0.0\n", encoding="utf-8"
    )
    (source / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "title": "Mail"}),
        encoding="utf-8",
    )
    return build_artifact_package(
        source, kind="scenario", source_ref=_source("scenarios/mail_client/")
    )


def _provider(root: Path):
    source = root / "mail_provider"
    (source / "contracts").mkdir(parents=True)
    (source / "handlers").mkdir()
    (source / "handlers" / "main.py").write_text(
        "def invoke(payload):\n    return payload\n", encoding="utf-8"
    )
    (source / "skill.yaml").write_text(
        """\
name: mail_provider
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
        source, kind="skill", source_ref=_source("skills/mail_provider/")
    )


def _compilation() -> dict:
    target = {
        "profile_ref": "profile:local/default",
        "allowed_modes": ["simulation", "production"],
    }
    requirements = [
        ApplicationRequirement.create(
            requirement_ref="requirement:scenario.mail_client.ui",
            capability_ref="capability:application.ui.render",
            contract_range="^1.0.0",
            environment_target=target,
            policy_constraints={
                "locality": "local",
                "privacy": "application-declared",
                "required_authorities": [],
            },
            evidence_threshold={
                "required_claim_kinds": ["capability_conformance"],
                "allow_stale": False,
            },
        ).to_dict(),
        ApplicationRequirement.create(
            requirement_ref="requirement:scenario.mail_client.mail",
            capability_ref="capability:mail.messages.manage",
            contract_range="^1.0.0",
            environment_target=target,
            policy_constraints={
                "locality": "remote_allowed",
                "privacy": "application-declared",
                "required_authorities": [],
            },
            evidence_threshold={
                "required_claim_kinds": ["capability_conformance"],
                "allow_stale": False,
            },
        ).to_dict(),
    ]
    value = {
        "schema": "adaos.builder.cbs_compilation.v1",
        "compiler_version": "1.1.0",
        "application_ref": "scenario:mail_client",
        "source_acceptance_digest": canonical_payload_digest(
            {"acceptance": "mail-client"}
        ),
        "semantic_revision_digest": canonical_payload_digest(requirements),
        "environment_target": target,
        "requirements": requirements,
        "simulation_attachments": [],
        "automation_obligations": [],
        "authoring_telemetry": {
            "human_authored_requirements": 1,
            "builder_inferred_requirements": 0,
            "compiler_generated_requirements": 1,
        },
        "viability": {
            "semantic": "compiled",
            "simulation": "accepted",
            "production": "unresolved",
            "unresolved_requirement_refs": [
                item["requirement_ref"] for item in requirements
            ],
        },
    }
    value["compilation_digest"] = canonical_payload_digest(value)
    return value


def _release(tmp_path: Path, *, include_provider: bool = True):
    scenario = _scenario(tmp_path / "source")
    packages = [scenario]
    if include_provider:
        packages.append(_provider(tmp_path / "source"))
    store = ContentAddressedPackageStore(tmp_path / "packages")
    for package in packages:
        store.put(package.archive_bytes, expected_digest=package.ref.digest)
    plan = build_project_release(
        project_id="mail_client",
        version="1.0.0",
        source_ref=_source("projects/mail_client/"),
        components=tuple(item.ref for item in packages),
        catalog=PackageCatalog(),
        permissions=("providers.google.gmail",) if include_provider else (),
        validation_evidence=({"validator": "pytest", "status": "passed"},),
    )
    return plan, store


def test_exact_application_release_admits_every_requirement_and_plan(tmp_path: Path) -> None:
    plan, store = _release(tmp_path)
    compilation = _compilation()
    service = NativeApplicationCBSAdmissionService(
        tmp_path / "state", now=lambda: FIXED_NOW
    )
    ApplicationCBSService(tmp_path / "state").register(compilation)

    admitted = service.admit(
        application_ref="scenario:mail_client",
        compilation=compilation,
        release_plan=plan,
        package_store=store,
        workspace_ref="trial:candidate-mail",
        evidence_context={"candidate_id": "candidate-mail"},
    )

    assert admitted["status"] == "admitted"
    assert admitted["requirements_total"] == admitted["requirements_resolved"] == 2
    assert {item["requirement_ref"] for item in admitted["resolutions"]} == {
        "requirement:scenario.mail_client.ui",
        "requirement:scenario.mail_client.mail",
    }
    assert len(admitted["plans"]) == 2
    assert all(item["base_lock"] == {"revision": 0} for item in admitted["plans"])
    assert service.inspect("scenario:mail_client") == admitted

    catalog = json.loads(
        (tmp_path / "state" / "capability-binding-state" / "portable" / "index.json").read_text(
            encoding="utf-8"
        )
    )
    assert "capability:mail.messages.manage@1.0.0" in catalog["identities"]
    assert "binding-definition:mail.messages.google-gmail@1.0.0" in catalog["identities"]
    assert all(
        item["schema"] != "adaos.environment.profile.v1"
        for item in catalog["records"].values()
    )

    projection = ApplicationCBSService(tmp_path / "state").lifecycle_projection(
        "scenario:mail_client",
        runtime_selection={
            "source": "local_trial",
            "release_digest": plan.release.release_digest,
            "revision": 1,
        },
    )
    assert projection["resolution"]["status"] == "admitted"
    assert projection["resolution"]["requirements_resolved"] == 2
    assert projection["plan"]["status"] == "ready"


def test_exact_release_publishes_and_imports_shared_semantic_registry(
    tmp_path: Path,
) -> None:
    plan, store = _release(tmp_path)
    compilation = _compilation()
    state_dir = tmp_path / "publisher-state"
    ApplicationCBSService(state_dir).register(compilation)
    admission_service = NativeApplicationCBSAdmissionService(
        state_dir, now=lambda: FIXED_NOW
    )
    admitted = admission_service.admit(
        application_ref="scenario:mail_client",
        compilation=compilation,
        release_plan=plan,
        package_store=store,
        workspace_ref="trial:candidate-mail",
        evidence_context={"candidate_id": "candidate-mail"},
    )

    registry = tmp_path / "registry"
    projection = SemanticRegistryProjection(registry, state_dir)
    published = projection.prepare_release(plan, package_store=store)

    assert published["status"] == "prepared"
    assert published["record_count"] == 6
    assert published["portable_evidence_count"] == 0
    assert published["omitted_local_evidence_count"] == 2
    assert published == projection.prepare_release(plan, package_store=store)
    assert (
        admission_service.find_by_project_release(plan.release.release_digest)
        == admitted
    )
    assert (
        ApplicationCBSService(state_dir).inspect_digest(
            "scenario:mail_client", compilation["compilation_digest"]
        )
        == compilation
    )

    consumer_state = tmp_path / "consumer-state"
    imported = SemanticRegistryProjection(registry, consumer_state).import_to_local_catalog()
    assert imported["record_count"] == 6
    catalog = PortableContractCatalog(
        consumer_state / "capability-binding-state" / "portable"
    )
    contracts = catalog.matching_capabilities(
        "capability:mail.messages.manage", "^1.0.0"
    )
    assert len(contracts) == 1
    bindings = catalog.matching_bindings(
        contracts[0].capability_ref, contracts[0].version
    )
    assert len(bindings) == 1
    deliveries = catalog.deliveries_for_binding(bindings[0].digest)
    assert len(deliveries) == 1
    assert deliveries[0].to_dict()["package"]["id"] == "mail_provider"


def test_public_application_catalog_imports_installable_aggregate_without_installing(
    tmp_path: Path,
) -> None:
    plan, store = _release(tmp_path)
    compilation = _compilation()
    publisher_state = tmp_path / "publisher-state"
    ApplicationCBSService(publisher_state).register(compilation)
    NativeApplicationCBSAdmissionService(
        publisher_state, now=lambda: FIXED_NOW
    ).admit(
        application_ref="scenario:mail_client",
        compilation=compilation,
        release_plan=plan,
        package_store=store,
        workspace_ref="trial:candidate-mail",
        evidence_context={"candidate_id": "candidate-mail"},
    )
    registry = tmp_path / "registry"
    projection = SemanticRegistryProjection(registry, publisher_state)
    semantic = projection.prepare_release(plan, package_store=store)
    application = Application(
        application_id="mail_client",
        legacy_project_id="mail_client",
        publisher_ref="subnet:publisher",
        slug="mail-client",
        display={"title": "Mail Client", "summary": "Portable mail client"},
        visibility="public",
        entrypoints=(
            {
                "entrypoint_id": "main",
                "presentation_ref": "scenario:mail_client",
            },
        ),
        publisher={
            "publisher_ref": "subnet:publisher",
            "display_name": "Publisher",
            "subnet_short_ref": "publisher",
            "release_key_ref": "key:publisher/releases",
            "release_key_fingerprint": "sha256:" + "1" * 64,
            "home_zone": "global",
            "trust_relation": "trusted",
        },
        revision=2,
        created_at=FIXED_NOW.isoformat(),
        updated_at=FIXED_NOW.isoformat(),
    )
    release = ApplicationRelease(
        application_id="mail_client",
        publisher_ref="subnet:publisher",
        project_release=plan.release,
        accepted_candidate_id="candidate-mail",
        acceptance_evidence=({"status": "passed", "validator": "test"},),
        provenance_refs=(str(plan.release.release_digest),),
        lifecycle="stable",
        published_at=FIXED_NOW.isoformat(),
    )
    published = projection.prepare_public_application(application, release)

    assert published["status"] == "prepared"
    assert published["release_digest"] == plan.release.release_digest
    assert semantic["application_projection_digest"]

    consumer_state = tmp_path / "consumer-state"
    imported = SemanticRegistryProjection(
        registry, consumer_state
    ).import_to_local_catalog(local_publisher_ref="subnet:consumer")
    catalog_import = imported["application_catalog"]
    assert catalog_import["application_count"] == 1
    consumer_store = ApplicationStore(consumer_state)
    assert consumer_store.get_application("mail_client") == application
    assert (
        consumer_store.get_release("mail_client", release.release_digest)
        == release
    )
    assert consumer_store.get_channels("mail_client")["channels"] == {
        "stable": release.release_digest
    }
    assert consumer_store.list_installations() == ()
    assert consumer_store.list_runtime_selections() == ()
    imported_application = catalog_import["applications"][0]
    assert imported_application["local_admission"] == {"status": "not_installed"}
    assert imported_application["semantic_requirement_set"]["application_ref"] == (
        "scenario:mail_client"
    )

    consumer_cbs = ApplicationCBSService(consumer_state)
    requirement_source = consumer_cbs.inspect_requirement_source(
        "application:mail_client",
        project_release_digest=str(plan.release.release_digest),
    )
    assert requirement_source is not None
    assert requirement_source["schema"] == (
        "adaos.application.semantic_requirement_set.v1"
    )
    assert requirement_source["compilation_digest"] == (
        compilation["compilation_digest"]
    )
    before_admission = consumer_cbs.lifecycle_projection(
        "application:mail_client",
        runtime_selection={
            "source": "stable_installation",
            "release_digest": plan.release.release_digest,
            "revision": 1,
        },
    )
    assert before_admission["requirement"]["status"] == "compiled"
    assert before_admission["resolution"]["status"] == "unresolved"

    executor = ApplicationDeploymentExecutor(
        runtime=SimpleNamespace(
            releases=SimpleNamespace(
                get_release=lambda project_id, release_digest: plan,
                fetch_package=lambda package: store.read(package.digest),
            )
        ),
        state_dir=consumer_state,
    )
    consumer_admission = executor._native_cbs_admission(
        {
            "kind": "install",
            "application_id": "mail_client",
            "legacy_project_id": "mail_client",
            "release_digest": plan.release.release_digest,
            "subnet_ref": "subnet:consumer",
        }
    )
    assert consumer_admission is not None
    assert consumer_admission["status"] == "admitted"
    after_admission = consumer_cbs.lifecycle_projection(
        "application:mail_client",
        runtime_selection={
            "source": "stable_installation",
            "release_digest": plan.release.release_digest,
            "revision": 1,
        },
    )
    assert after_admission["resolution"]["status"] == "admitted"
    assert after_admission["plan"]["status"] == "ready"
    assert after_admission["activation"]["status"] == "active"
    assert after_admission["lock"]["status"] == "committed"


def test_reissued_evidence_for_a_recompiled_release_has_a_distinct_identity(
    tmp_path: Path,
) -> None:
    plan, store = _release(tmp_path)
    compilation = _compilation()
    clock = [FIXED_NOW]
    service = NativeApplicationCBSAdmissionService(
        tmp_path / "state", now=lambda: clock[0]
    )

    first = service.admit(
        application_ref="scenario:mail_client",
        compilation=compilation,
        release_plan=plan,
        package_store=store,
        workspace_ref="trial:candidate-mail",
        evidence_context={"verification": "first"},
    )
    recompiled = copy.deepcopy(compilation)
    recompiled["compiler_version"] = "1.2.0"
    recompiled["compilation_digest"] = canonical_payload_digest(
        {
            key: value
            for key, value in recompiled.items()
            if key != "compilation_digest"
        }
    )
    clock[0] += timedelta(seconds=1)

    second = service.admit(
        application_ref="scenario:mail_client",
        compilation=recompiled,
        release_plan=plan,
        package_store=store,
        workspace_ref="trial:candidate-mail",
        evidence_context={"verification": "second"},
    )

    assert first["status"] == second["status"] == "admitted"
    assert {item["claim_ref"] for item in first["evidence"]}.isdisjoint(
        item["claim_ref"] for item in second["evidence"]
    )


def test_exact_application_release_fails_closed_when_provider_is_missing(
    tmp_path: Path,
) -> None:
    plan, store = _release(tmp_path, include_provider=False)
    admitted = NativeApplicationCBSAdmissionService(
        tmp_path / "state", now=lambda: FIXED_NOW
    ).admit(
        application_ref="scenario:mail_client",
        compilation=_compilation(),
        release_plan=plan,
        package_store=store,
        workspace_ref="trial:candidate-mail",
    )

    assert admitted["status"] == "unresolved"
    assert admitted["requirements_total"] == 2
    assert admitted["requirements_resolved"] == 1
    assert admitted["unresolved"][0]["requirement_ref"] == (
        "requirement:scenario.mail_client.mail"
    )
