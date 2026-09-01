from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import agent_context_control as context_api
from adaos.services.context_control import ContextControlService


def _client(service: ContextControlService) -> TestClient:
    app = FastAPI()
    app.include_router(context_api.router, prefix="/api/context")
    app.dependency_overrides[context_api._get_service] = lambda: service
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-AdaOS-Token": "dev-local-token"}


def test_context_api_resolve_plan_compile_and_inspect(tmp_path: Path) -> None:
    client = _client(ContextControlService(tmp_path))
    capsule_response = client.post(
        "/api/context/capsules",
        headers=_headers(),
        json={
            "kind": "project",
            "subject_refs": ["project:demo"],
            "authority_ref": "project:demo",
            "trust_class": "accepted",
            "summary": "Demo project",
            "bind": True,
        },
    )
    assert capsule_response.status_code == 200

    resolution_response = client.post(
        "/api/context/resolve",
        headers=_headers(),
        json={"scope_ref": "demo", "purpose": "builder.repair", "audience": "builder"},
    )
    assert resolution_response.status_code == 200
    resolution = resolution_response.json()["resolution"]
    plan_response = client.post(
        "/api/context/plans",
        headers=_headers(),
        json={"resolution": resolution, "token_budget": 1000},
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()["plan"]
    compiled = client.post(
        "/api/context/compile",
        headers=_headers(),
        json={"plan_id": plan["plan_id"], "output_format": "min_json"},
    )
    assert compiled.status_code == 200

    receipt_response = client.post(
        "/api/context/receipts",
        headers=_headers(),
        json={
            "run_ref": "run:demo",
            "plan_ref": plan["plan_ref"],
            "usage": {"provider_input_tokens": 12, "cached_input_tokens": 8, "output_tokens": 2},
        },
    )
    assert receipt_response.status_code == 200
    inspection = client.get("/api/context/inspect/run:demo", headers=_headers())
    assert inspection.status_code == 200
    assert inspection.json()["inspection"]["usage"]["fresh_plus_output"] == 6


def test_context_api_reports_binding_conflict(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    client = _client(service)
    capsule = service.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo"],
            "authority_ref": "project:demo",
            "trust_class": "accepted",
            "summary": "Demo project",
        }
    )
    first = client.post(
        "/api/context/bindings",
        headers=_headers(),
        json={"subject_ref": "project:demo", "capsule_id": capsule["capsule_id"], "expected_revision": 0},
    )
    conflict = client.post(
        "/api/context/bindings",
        headers=_headers(),
        json={"subject_ref": "project:demo", "capsule_id": capsule["capsule_id"], "expected_revision": 0},
    )
    assert first.status_code == 200
    assert conflict.status_code == 409


def test_context_api_lists_invalidations_and_merges_branch(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    client = _client(service)
    base = service.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo"],
            "authority_ref": "project:demo",
            "trust_class": "accepted",
            "summary": "base",
        }
    )
    changed = service.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo"],
            "authority_ref": "project:demo",
            "trust_class": "accepted",
            "summary": "changed",
        }
    )
    service.bind_subject(subject_ref="project:demo", capsule_id=base["capsule_id"])
    service.bind_subject(
        subject_ref="project:demo",
        capsule_id=changed["capsule_id"],
        branch="repair",
    )

    merged = client.post(
        "/api/context/bindings/merge",
        headers=_headers(),
        json={
            "subject_ref": "project:demo",
            "source_branch": "repair",
            "base_capsule_id": base["capsule_id"],
            "expected_target_revision": 1,
        },
    )
    invalidated = client.post(
        "/api/context/invalidate",
        headers=_headers(),
        json={
            "subject_ref": "project:demo",
            "reason": "project.release.changed",
            "event_ref": "event:release-1",
        },
    )
    listed = client.get(
        "/api/context/invalidations?subject_ref=project%3Ademo",
        headers=_headers(),
    )

    assert merged.status_code == 200
    assert merged.json()["merge"]["target"]["capsule_id"] == changed["capsule_id"]
    assert invalidated.status_code == 200
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
