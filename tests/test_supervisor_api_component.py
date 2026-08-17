from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from adaos.apps.supervisor_runtime import (
    SupervisorApiAdapter,
    SupervisorRoute,
    create_supervisor_app,
    create_supervisor_routes,
)


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


def test_supervisor_route_groups_preserve_public_and_post_routes() -> None:
    async def handler():
        return {"ok": True}

    handlers = {
        "ping": handler,
        "supervisor_status": handler,
        "supervisor_memory_status": handler,
        "supervisor_memory_telemetry": handler,
        "supervisor_public_memory_status": handler,
        "supervisor_memory_sessions": handler,
        "supervisor_memory_incidents": handler,
        "supervisor_memory_session": handler,
        "supervisor_memory_session_artifact": handler,
        "supervisor_memory_profile_start": handler,
        "supervisor_memory_profile_stop": handler,
        "supervisor_memory_profile_retry": handler,
        "supervisor_memory_publish": handler,
        "supervisor_sidecar_status": handler,
        "supervisor_service_restart": handler,
        "supervisor_runtime_restart": handler,
        "supervisor_runtime_candidate_start": handler,
        "supervisor_runtime_candidate_stop": handler,
        "supervisor_sidecar_restart": handler,
        "supervisor_update_status": handler,
        "supervisor_public_update_status": handler,
        "supervisor_update_start": handler,
        "supervisor_update_cancel": handler,
        "supervisor_update_defer": handler,
        "supervisor_update_rollback": handler,
        "supervisor_update_promote_root": handler,
        "supervisor_update_complete": handler,
    }

    routes = create_supervisor_routes(handlers)
    by_path = {route.path: route for route in routes}

    assert len(routes) == 27
    assert by_path["/api/ping"].protected is False
    assert by_path["/api/supervisor/public/update-status"].protected is False
    assert by_path["/api/supervisor/update/start"].method == "POST"
    assert by_path["/api/supervisor/runtime/candidate/stop"].method == "POST"
    assert by_path["/api/supervisor/service/restart"].method == "POST"


def test_supervisor_api_adapter_delegates_and_validates_payloads() -> None:
    class _Manager:
        async def start_update(self, **kwargs):
            return kwargs

    adapter = SupervisorApiAdapter(lambda: _Manager())

    result = asyncio.run(adapter.supervisor_update_start({"target_rev": "rev-a"}))

    assert result["action"] == "update"
    assert result["target_rev"] == "rev-a"
    assert result["countdown_sec"] == 60.0


def test_supervisor_api_adapter_requests_managed_service_restart() -> None:
    class _Manager:
        def restart_service(self, *, reason: str):
            return {"ok": True, "accepted": True, "reason": reason}

    adapter = SupervisorApiAdapter(lambda: _Manager())

    result = asyncio.run(adapter.supervisor_service_restart({"reason": "test.operator"}))

    assert result == {"ok": True, "accepted": True, "reason": "test.operator"}


def test_supervisor_api_status_does_not_block_event_loop() -> None:
    class _Manager:
        def status(self):
            time.sleep(0.2)
            return {"runtime_state": "ready"}

    adapter = SupervisorApiAdapter(lambda: _Manager())

    async def _exercise() -> dict[str, str]:
        task = asyncio.create_task(adapter.supervisor_status())
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(0.03)
        assert asyncio.get_running_loop().time() - started < 0.12
        assert not task.done()
        return await task

    assert asyncio.run(_exercise()) == {"runtime_state": "ready"}
