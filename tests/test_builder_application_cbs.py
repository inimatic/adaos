from __future__ import annotations

import copy
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import application_cbs
from adaos.apps.api.auth import require_token
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    BindingInstance,
    CapabilityContract,
    EvidenceAssessment,
    EvidenceClaim,
    LocalRevisionObservation,
    StateSpace,
)
from adaos.services.agent_context import get_ctx
from adaos.services.applications.cbs import ApplicationCBSConflict, ApplicationCBSService
from adaos.services.builder.cbs import compile_prototype_cbs
from adaos.services.builder.workflow import BuilderWorkflowError
from adaos.services.capability_binding_state import LocalIdentityStore


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _acceptance(*, application_ref: str = "scenario:applications", revision: str = "17") -> dict:
    value = {
        "schema": "adaos.builder.prototype_acceptance.v1",
        "acceptance_id": f"acceptance-{revision}",
        "project_ref": application_ref,
        "change_id": "change-beta",
        "revision": revision,
        "webui_digest": DIGEST_A,
        "request_digest": DIGEST_B,
        "reviewer": {"id": "agent:builder", "kind": "agent"},
        "decision": "accepted",
        "acceptance_stage": "prototype",
        "automation_requirements": [
            {
                "requirement_ref": "automation:refresh",
                "statement": "Refresh the application registry.",
                "acceptance": "The visible list reflects the registry revision.",
                "disclosure": {"en": "Refresh applications"},
                "prototype_refs": ["action:refresh"],
                "status": "pending_automation",
                "brief_ref": "brief:applications.refresh",
                "brief_digest": DIGEST_C,
            }
        ],
        "deterministic_evaluation": {"ok": True},
        "prototype_resources": [
            {
                "resource_type": "prototype.applications",
                "bundle_digest": DIGEST_A,
                "definition_digest": DIGEST_B,
                "generation": 3,
                "record_count": 7,
                "records_digest": DIGEST_C,
            }
        ],
        "behavior_checks": [
            {"id": "render.ready", "status": "passed", "evidence_refs": ["e2e:render"]}
        ],
        "visual_checks": [
            {
                "breakpoint": "compact",
                "viewport": {"width": 390, "height": 844},
                "status": "passed",
                "evidence_ref": "e2e:compact",
            },
            {
                "breakpoint": "wide",
                "viewport": {"width": 1440, "height": 900},
                "status": "passed",
                "evidence_ref": "e2e:wide",
            },
        ],
        "accepted_at": "2026-09-22T12:00:00+00:00",
    }
    value["digest"] = canonical_payload_digest(value)
    return value


def _contract(capability_ref: str) -> CapabilityContract:
    return CapabilityContract.create(
        capability_ref=capability_ref,
        version="1.0.0",
        title=capability_ref,
        operations=(
            {
                "operation_id": "execute",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "errors": [],
            },
        ),
    )


def test_compiler_preserves_semantic_identity_and_separates_simulation_state() -> None:
    acceptance = _acceptance()
    first = compile_prototype_cbs(acceptance)
    second = compile_prototype_cbs(copy.deepcopy(acceptance))

    assert first == second
    assert first["application_ref"] == "scenario:applications"
    assert first["semantic_revision_digest"].startswith("sha256:")
    assert {item["capability_ref"] for item in first["requirements"]} >= {
        "capability:application.ui.render",
        "capability:resource.records.manage",
    }
    assert first["simulation_attachments"] == [
        {
            "resource_type": "prototype.applications",
            "state_space_ref": "state-space:scenario.applications.simulation.applications",
            "portability_class": "reconstructible",
            "generation": 3,
            "definition_digest": DIGEST_B,
            "records_digest": DIGEST_C,
        }
    ]
    assert first["viability"]["production"] == "unresolved"
    serialized = str(first)
    assert "package" not in serialized
    assert "binding-definition:" not in serialized
    assert first["authoring_telemetry"] == {
        "human_authored_requirements": 0,
        "builder_inferred_requirements": 0,
        "compiler_generated_requirements": 2,
    }


def test_compiler_expands_compact_package_neutral_cbs_intent() -> None:
    acceptance = _acceptance(application_ref="scenario:gmail_mail_client")
    acceptance["cbs_intent"] = {
        "schema": "adaos.builder.cbs_intent.v1",
        "requirements": [
            {
                "id": "mail",
                "capability_ref": "capability:mail.messages.manage",
                "contract_range": "^1.0.0",
                "origin": "human_explicit",
                "required_authorities": ["mail.messages.read", "mail.messages.modify"],
            }
        ],
    }
    acceptance["digest"] = canonical_payload_digest(
        {key: value for key, value in acceptance.items() if key != "digest"}
    )

    compilation = compile_prototype_cbs(acceptance)

    mail = next(
        item
        for item in compilation["requirements"]
        if item["capability_ref"] == "capability:mail.messages.manage"
    )
    assert mail["requirement_ref"] == "requirement:scenario.gmail_mail_client.mail"
    assert mail["contract_range"] == "^1.0.0"
    assert mail["policy_constraints"]["required_authorities"] == [
        "mail.messages.read",
        "mail.messages.modify",
    ]
    assert compilation["authoring_telemetry"] == {
        "human_authored_requirements": 1,
        "builder_inferred_requirements": 0,
        "compiler_generated_requirements": 2,
    }
    assert "googleapis" not in str(mail).lower()
    assert "package" not in str(mail).lower()


def test_compiler_rejects_tampered_acceptance() -> None:
    acceptance = _acceptance()
    acceptance["revision"] = "18"
    with pytest.raises(BuilderWorkflowError, match="digest"):
        compile_prototype_cbs(acceptance)


def test_application_registry_is_content_addressed_and_optimistic(tmp_path) -> None:
    service = ApplicationCBSService(tmp_path)
    first = service.compile_and_register(
        application_ref="scenario:applications",
        acceptance=_acceptance(),
    )
    assert service.inspect("scenario:applications") == first
    assert service.compile_and_register(
        application_ref="scenario:applications",
        acceptance=_acceptance(),
    ) == first

    with pytest.raises(ApplicationCBSConflict, match="stale"):
        service.compile_and_register(
            application_ref="scenario:applications",
            acceptance=_acceptance(revision="18"),
        )

    second = service.compile_and_register(
        application_ref="scenario:applications",
        acceptance=_acceptance(revision="18"),
        expected_previous_digest=first["compilation_digest"],
    )
    assert service.inspect("scenario:applications") == second
    assert len(service.history("scenario:applications")) == 2


def test_application_cbs_lifecycle_projection_is_explicitly_non_authoritative(
    tmp_path,
) -> None:
    service = ApplicationCBSService(tmp_path)
    compilation = service.compile_and_register(
        application_ref="scenario:applications",
        acceptance=_acceptance(),
    )

    projection = service.lifecycle_projection(
        "scenario:applications",
        runtime_selection={
            "source": "local_trial",
            "release_digest": DIGEST_A,
            "revision": 4,
        },
        local_development={
            "trial": {"evidence_present": True},
            "publication": {"evidence_present": False},
        },
    )

    assert projection["authoritative"] is False
    assert projection["authority"] == "derived_read_only"
    assert projection["compilation_digest"] == compilation["compilation_digest"]
    assert [item["id"] for item in projection["stages"]] == [
        "requirement",
        "resolution",
        "plan",
        "activation",
        "lock",
    ]
    assert projection["requirement"]["status"] == "compiled"
    assert projection["resolution"]["status"] == "unresolved"
    assert projection["plan"]["status"] == "not_created"
    assert projection["activation"]["status"] == "trial_active"
    assert projection["lock"]["status"] == "unchanged"


def test_application_api_compiles_and_assesses_without_activation(tmp_path) -> None:
    app = FastAPI()
    app.include_router(application_cbs.router, prefix="/api")
    ctx = types.SimpleNamespace(paths=types.SimpleNamespace(state_dir=lambda: str(tmp_path)))
    app.dependency_overrides[get_ctx] = lambda: ctx
    app.dependency_overrides[require_token] = lambda: True
    client = TestClient(app)

    acceptance = _acceptance()
    acceptance["automation_requirements"] = []
    acceptance["digest"] = canonical_payload_digest(
        {key: value for key, value in acceptance.items() if key != "digest"}
    )
    response = client.post(
        "/api/v1/applications/scenario:applications/cbs/compilations",
        json={"acceptance": acceptance},
    )
    assert response.status_code == 200, response.text
    compilation = response.json()["compilation"]
    assert response.json()["activation_performed"] is False

    inspected = client.get("/api/v1/applications/scenario:applications/cbs")
    assert inspected.status_code == 200
    assert inspected.json()["compilation"]["compilation_digest"] == compilation["compilation_digest"]

    contracts = [
        _contract("capability:application.ui.render").to_dict(),
        _contract("capability:resource.records.manage").to_dict(),
    ]
    viability = client.post(
        "/api/v1/applications/scenario:applications/cbs/semantic-viability",
        json={"capability_contracts": contracts},
    )
    assert viability.status_code == 200, viability.text
    assert viability.json()["viable"] is True
    assert viability.json()["activation_performed"] is False
    assert viability.json()["evidence_obligations"]
    assert viability.json()["capability_gaps"] == []

    ui_contract = _contract("capability:application.ui.render")
    claim = EvidenceClaim.create(
        claim_ref="evidence-claim:applications/ui-render",
        claim_kind="capability_conformance",
        subjects=(
            {
                "kind": "capability_contract",
                "ref": ui_contract.capability_ref,
                "digest": ui_contract.digest,
            },
        ),
        environment={
            "profile_ref": "profile:local/default",
            "profile_digest": DIGEST_C,
        },
        dependencies=(),
        suite_digest=DIGEST_A,
        evidence_digest=DIGEST_B,
        provenance={"issuer": "adaos:test", "runner": "pytest"},
        issued_at="2026-09-22T12:00:00+00:00",
        freshness={
            "max_age_seconds": 86400,
            "invalidated_by": ["dependency_change"],
        },
        result="verified",
        redaction={"portable": True, "omitted_fields": []},
        portability_scope="portable",
    )
    assessment = EvidenceAssessment.create(
        assessment_ref="evidence-assessment:applications/ui-render",
        claim_ref=claim.claim_ref,
        claim_digest=claim.digest,
        evaluated_at="2026-09-23T12:00:00+00:00",
        policy_digest=DIGEST_A,
        status="stale",
        reasons=("external renderer dependency changed",),
    )
    explained = client.post(
        "/api/v1/applications/scenario:applications/cbs/semantic-viability",
        json={
            "capability_contracts": contracts,
            "evidence_claims": [claim.to_dict()],
            "evidence_assessments": [assessment.to_dict()],
        },
    )
    assert explained.status_code == 200, explained.text
    obligation = next(
        item
        for item in explained.json()["evidence_obligations"]
        if item.get("claim_ref") == claim.claim_ref
    )
    assert obligation["status"] == "stale"
    assert "historically verified but is stale" in obligation["explanation"]["message"]

    identities = LocalIdentityStore(
        tmp_path / "capability-binding-state" / "identities"
    )
    binding = BindingInstance.create(
        binding_instance_ref="binding-instance:applications/local",
        revision=1,
        predecessor_digest=None,
        workspace_ref="workspace:applications",
        tenant_ref="tenant:test",
        binding_definition_ref="binding-definition:resource.records.local-json",
        binding_definition_digest=DIGEST_A,
        delivery_digest=DIGEST_B,
        environment_profile_ref="profile:local/default",
        environment_profile_digest=DIGEST_C,
        mode="production",
        local_binding_ref="local-crud:applications",
        authority_epoch=1,
    )
    identities.append(binding)
    state_space = StateSpace.create(
        state_space_ref="state-space:applications/records",
        revision=1,
        predecessor_digest=None,
        state_contract_ref="state-contract:applications.records",
        state_contract_version="1.0.0",
        state_contract_digest=DIGEST_A,
        workspace_ref="workspace:applications",
        tenant_ref="tenant:test",
        logical_owner_ref="application:applications",
        lifecycle_authority_ref="application:applications",
        custodian_binding_instance_ref=binding.stable_ref,
        mutation_authority_ref="workspace:applications",
        locator_ref="local-resource-registry:applications",
        generation=4,
        authority_epoch=1,
        portability_class="portable",
        schema_locks=(),
    )
    identities.append(state_space)
    identities.put_fact(
        LocalRevisionObservation.create(
            observation_ref="observation:applications/backup",
            subject_kind="state_space",
            subject_ref=state_space.stable_ref,
            subject_revision_digest=state_space.digest,
            observation_kind="backup",
            status="ready",
            observed_at="2026-09-23T12:00:00+00:00",
            details={"backup_ref": "backup:applications/latest", "restore_tested": True},
        )
    )
    inspected_state = client.get(
        "/api/v1/cbs/state-spaces/inspect",
        params={"state_space_ref": state_space.stable_ref},
    )
    assert inspected_state.status_code == 200, inspected_state.text
    state_payload = inspected_state.json()["inspection"]
    assert state_payload["state_space_ref"] == state_space.stable_ref
    assert state_payload["observations"]["backup"]["details"]["restore_tested"] is True
    assert inspected_state.json()["activation_performed"] is False
