from __future__ import annotations

from fastapi.testclient import TestClient

from adaos.apps.supervisor_runtime import SupervisorRoute, create_supervisor_app


def test_supervisor_api_component_registers_public_and_protected_routes(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "test-token")

    async def startup() -> None:
        return None

    async def shutdown() -> None:
        return None

    async def public():
        return {"public": True}

    async def protected():
        return {"protected": True}

    app = create_supervisor_app(
        startup=startup,
        shutdown=shutdown,
        routes=(
            SupervisorRoute("/public", public, protected=False),
            SupervisorRoute("/protected", protected),
        ),
    )
    client = TestClient(app)

    assert client.get("/public").status_code == 200
    assert client.get("/protected").status_code in {401, 403}
    assert client.get("/protected", headers={"X-AdaOS-Token": "test-token"}).status_code == 200


def test_supervisor_api_component_handles_private_network_preflight() -> None:
    async def noop() -> None:
        return None

    app = create_supervisor_app(startup=noop, shutdown=noop, routes=())
    response = TestClient(app).options(
        "/api/ping",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-private-network"] == "true"
