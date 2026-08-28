from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from adaos.apps.api import project_deployment as project_deployment_api
from adaos.domain.project_deployment import NodeInventoryRecord
from adaos.services.project_deployment.authorization import DeploymentPrincipal
from adaos.services.project_deployment.authority import (
    AUTHORITY_REQUEST_SCHEMA,
    AUTHORITY_RESPONSE_SCHEMA,
    ProjectDeploymentAuthorityError,
    execute_authority_request,
    invoke_project_deployment_authority,
    register_project_deployment_authority,
)
from adaos.services.project_deployment.execution import ProjectDeploymentExecutionError


def _node() -> NodeInventoryRecord:
    return NodeInventoryRecord(
        node_id="node-a",
        subnet_id="home",
        trust_state="trusted",
        online=True,
        architecture="x86_64",
        runtime_version="0.1.930",
        capabilities=("project.activate",),
        protocols={"project_activation": "1"},
        labels={"site": "home"},
        capacity={"cpu_millicores": 4000},
        revision=1,
    )


def _principal() -> DeploymentPrincipal:
    return DeploymentPrincipal.create(
        actor_ref="skill:test",
        permissions=("project.deployment.inspect",),
    )


def _request(operation: str, arguments: dict) -> dict:
    return {
        "schema": AUTHORITY_REQUEST_SCHEMA,
        "operation": operation,
        "arguments": arguments,
        "principal": {
            "actor_ref": "skill:test",
            "permissions": ["project.deployment.inspect"],
            "approvals": [],
        },
    }


def test_authority_inventory_is_bounded_and_owned_by_registered_runtime(
    monkeypatch,
) -> None:
    class Inventory:
        def list_nodes(self, subnet_id: str):
            assert subnet_id == "home"
            return (_node(),)

    runtime = SimpleNamespace(inventory=Inventory(), local_node_id="node-a")
    register_project_deployment_authority(runtime)
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    try:
        response = execute_authority_request(
            _request("inventory", {"subnet_id": "home", "limit": 20})
        )
    finally:
        register_project_deployment_authority(None)

    assert response["schema"] == AUTHORITY_RESPONSE_SCHEMA
    assert response["authority"]["local_node_id"] == "node-a"
    assert response["result"]["total"] == 1
    assert response["result"]["nodes"][0]["node_id"] == "node-a"


def test_candidate_runtime_rejects_deployment_authority(monkeypatch) -> None:
    register_project_deployment_authority(SimpleNamespace(local_node_id="candidate"))
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    try:
        with pytest.raises(
            ProjectDeploymentAuthorityError,
            match="deployment_authority_candidate_passive",
        ):
            execute_authority_request(
                _request("inventory", {"subnet_id": "home", "limit": 20})
            )
    finally:
        register_project_deployment_authority(None)


def test_client_only_process_marshals_to_active_runtime(monkeypatch) -> None:
    from adaos.services.project_deployment import authority

    register_project_deployment_authority(None, client_only=True)
    monkeypatch.setattr(
        authority,
        "_invoke_http",
        lambda request: {
            "schema": AUTHORITY_RESPONSE_SCHEMA,
            "operation": request["operation"],
            "authority": {"runtime_instance_id": "rt-active"},
            "result": {
                "subnet_id": "home",
                "nodes": [],
                "total": 0,
                "truncated": False,
            },
        },
    )
    try:
        result = invoke_project_deployment_authority(
            "inventory",
            {"subnet_id": "home", "limit": 20},
            principal=_principal(),
        )
    finally:
        register_project_deployment_authority(None)

    assert result == {
        "subnet_id": "home",
        "nodes": [],
        "total": 0,
        "truncated": False,
    }


def test_authority_http_endpoint_is_token_protected_and_local(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "test-token")
    monkeypatch.setattr(
        project_deployment_api,
        "execute_authority_request",
        lambda payload: {
            "schema": AUTHORITY_RESPONSE_SCHEMA,
            "operation": payload["operation"],
            "authority": {"runtime_instance_id": "rt-active"},
            "result": {"ok": True},
        },
    )
    app = FastAPI()
    app.include_router(
        project_deployment_api.router,
        prefix="/api/node/project-deployment",
    )
    client = TestClient(app)
    path = "/api/node/project-deployment/authority"

    assert (
        client.post(path, json=_request("inventory", {"subnet_id": "home"})).status_code
        == 401
    )
    response = client.post(
        path,
        headers={"X-AdaOS-Token": "test-token"},
        json=_request("inventory", {"subnet_id": "home"}),
    )

    assert response.status_code == 200
    assert response.json()["result"] == {"ok": True}
    with pytest.raises(HTTPException) as error:
        project_deployment_api._require_loopback(
            SimpleNamespace(client=SimpleNamespace(host="192.0.2.10"))
        )
    assert error.value.status_code == 403


def test_authority_http_endpoint_preserves_inventory_conflict_code(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "test-token")

    def reject_stale_inventory(payload):
        raise ProjectDeploymentExecutionError(
            "node inventory changed after planning"
        )

    monkeypatch.setattr(
        project_deployment_api,
        "execute_authority_request",
        reject_stale_inventory,
    )
    app = FastAPI()
    app.include_router(
        project_deployment_api.router,
        prefix="/api/node/project-deployment",
    )

    response = TestClient(app).post(
        "/api/node/project-deployment/authority",
        headers={"X-AdaOS-Token": "test-token"},
        json=_request("submit", {"plan_digest": "sha256:stale"}),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "inventory_changed_after_planning"
    }


def test_authority_http_client_preserves_rejected_error_code(monkeypatch) -> None:
    from adaos.apps.cli import active_control
    from adaos.services.project_deployment import authority

    class RejectedResponse:
        def raise_for_status(self) -> None:
            raise requests.HTTPError(response=self)

        @staticmethod
        def json() -> dict:
            return {"detail": {"error": "inventory_changed_after_planning"}}

    class RejectedSession:
        trust_env = True

        @staticmethod
        def post(*args, **kwargs):
            return RejectedResponse()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(
        active_control,
        "resolve_control_base_url",
        lambda **kwargs: "http://127.0.0.1:8778",
    )
    monkeypatch.setattr(
        active_control,
        "resolve_control_token",
        lambda **kwargs: "test-token",
    )
    monkeypatch.setattr(authority.requests, "Session", RejectedSession)

    with pytest.raises(
        ProjectDeploymentAuthorityError,
        match="inventory_changed_after_planning",
    ):
        authority._invoke_http(_request("submit", {"plan_digest": "sha256:stale"}))
