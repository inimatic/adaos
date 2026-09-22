from __future__ import annotations

import copy
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import application_cbs
from adaos.apps.api.auth import require_token
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import CapabilityContract
from adaos.services.agent_context import get_ctx
from adaos.services.applications.cbs import ApplicationCBSConflict, ApplicationCBSService
from adaos.services.builder.cbs import compile_prototype_cbs
from adaos.services.builder.workflow import BuilderWorkflowError


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
