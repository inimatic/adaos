from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(
        YDoc=type("YDoc", (), {}),
        encode_state_vector=lambda *args, **kwargs: b"",
        encode_state_as_update=lambda *args, **kwargs: b"",
        apply_update=lambda *args, **kwargs: None,
    )
if "ypy_websocket.ystore" not in sys.modules:
    ystore_module = types.ModuleType("ypy_websocket.ystore")
    ystore_module.BaseYStore = type("BaseYStore", (), {})
    ystore_module.YDocNotFound = type("YDocNotFound", (Exception,), {})
    sys.modules["ypy_websocket.ystore"] = ystore_module
if "ypy_websocket" not in sys.modules:
    pkg = types.ModuleType("ypy_websocket")
    pkg.ystore = sys.modules["ypy_websocket.ystore"]
    sys.modules["ypy_websocket"] = pkg

from adaos.apps.api import server as api_server
from adaos.apps.api import node_api
from adaos.services.system_model import service as system_model_service


def test_current_node_object_does_not_enter_diagnostic_io(monkeypatch) -> None:
    monkeypatch.setattr(
        system_model_service,
        "load_config",
        lambda: types.SimpleNamespace(
            node_id="hub-identity",
            subnet_id="subnet-identity",
            role="hub",
            node_names=["homepoint"],
            primary_node_name="homepoint",
            owner_id="owner-identity",
        ),
    )
    monkeypatch.setattr(system_model_service, "route_info", lambda _role: ("hub", None))
    monkeypatch.setattr(
        system_model_service,
        "runtime_lifecycle_snapshot",
        lambda: {"node_state": "ready", "draining": False},
    )
    monkeypatch.setattr(system_model_service, "is_ready", lambda: True)

    def _unexpected_diagnostic_io(*_args, **_kwargs):
        raise AssertionError("canonical node identity must not load runtime diagnostics")

    monkeypatch.setattr(system_model_service, "runtime_environment_payload", _unexpected_diagnostic_io)
    monkeypatch.setattr(system_model_service, "current_base_dir", _unexpected_diagnostic_io)
    monkeypatch.setattr(system_model_service, "_node_status_supervisor_runtime", _unexpected_diagnostic_io)
    monkeypatch.setattr(system_model_service, "_node_status_sidecar_runtime", _unexpected_diagnostic_io)

    node = system_model_service.current_node_object()

    assert node.id == "hub:hub-identity"
    assert node.status == "online"


def test_ping_exposes_runtime_identity_for_candidate(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_INSTANCE_ID", "rt-b-c-12345678")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8778")
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "starting", "ready": False, "started_at": 1.0, "completed_at": None},
        raising=False,
    )

    payload = asyncio.run(api_server.ping())

    assert payload["ok"] is True
    assert payload["service"] == "adaos-runtime"
    assert payload["runtime"]["transition_role"] == "candidate"
    assert payload["runtime"]["runtime_instance_id"] == "rt-b-c-12345678"
    assert payload["runtime"]["slot"] == "B"
    assert payload["runtime"]["runtime_port"] == 8778
    assert payload["runtime"]["admin_mutation_allowed"] is False
    assert payload["readiness"]["ready"] is False


def test_boot_sequence_readiness_changes_only_after_boot_completes(monkeypatch) -> None:
    release = asyncio.Event()

    async def _boot(_app) -> None:
        await release.wait()

    monkeypatch.setattr(api_server, "run_boot_sequence", _boot)

    async def _run() -> tuple[dict, dict]:
        task = asyncio.create_task(api_server._run_boot_sequence_logged(api_server.app))
        await asyncio.sleep(0)
        starting = api_server._runtime_boot_readiness_payload()
        release.set()
        await task
        return starting, api_server._runtime_boot_readiness_payload()

    starting, ready = asyncio.run(_run())

    assert starting["state"] == "starting"
    assert starting["ready"] is False
    assert ready["state"] == "ready"
    assert ready["ready"] is True
    assert ready["completed_at"] >= ready["started_at"]


def test_candidate_defers_post_boot_migration_until_promotion(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setattr(api_server.app.state, "runtime_boot_task", None, raising=False)
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "ready", "ready": True, "started_at": 1.0, "completed_at": 2.0},
        raising=False,
    )
    monkeypatch.setattr(api_server.app.state, "skill_runtime_migration_started", False, raising=False)
    monkeypatch.setattr(
        api_server.app.state,
        "skill_runtime_migration_deferred_for_promotion",
        False,
        raising=False,
    )

    payload = asyncio.run(
        api_server._start_post_boot_skill_runtime_migration(
            api_server.app,
            reason="test.post_boot",
        )
    )

    assert payload == {"ok": True, "started": False, "reason": "candidate_deferred"}
    assert api_server.app.state.skill_runtime_migration_started is False
    assert api_server.app.state.skill_runtime_migration_deferred_for_promotion is True


def test_post_boot_migration_does_not_mark_rejected_worker_as_started(monkeypatch) -> None:
    import adaos.services.core_slots as core_slots
    import adaos.services.skill.runtime_migration_worker as migration_worker

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    monkeypatch.setenv("ADAOS_TESTING", "1")
    monkeypatch.setattr(api_server.app.state, "runtime_boot_task", None, raising=False)
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "ready", "ready": True, "started_at": 1.0, "completed_at": 2.0},
        raising=False,
    )
    monkeypatch.setattr(api_server.app.state, "skill_runtime_migration_started", False, raising=False)
    monkeypatch.setattr(api_server.app.state, "skill_runtime_migration_starting", False, raising=False)
    monkeypatch.setattr(
        core_slots,
        "active_slot_manifest",
        lambda: {"skill_runtime_migration": {"deferred": True, "background_required": True}},
    )

    async def _rejected(*_args, **_kwargs):
        return {"ok": True, "accepted": False, "retryable": True, "reason": "global_migration_running"}

    monkeypatch.setattr(migration_worker, "start_background_migration", _rejected)

    payload = asyncio.run(
        api_server._start_post_boot_skill_runtime_migration(
            api_server.app,
            reason="test.post_boot",
        )
    )

    assert payload["started"] is False
    assert payload["reason"] == "global_migration_running"
    assert api_server.app.state.skill_runtime_migration_started is False
    assert api_server.app.state.skill_runtime_migration_starting is False


@pytest.mark.parametrize(
    ("workspace_lock_present", "expected_sync_workspace"),
    [(False, True), (True, False)],
)
def test_post_boot_migration_syncs_legacy_sparse_workspace(
    monkeypatch,
    tmp_path,
    workspace_lock_present: bool,
    expected_sync_workspace: bool,
) -> None:
    import adaos.services.core_slots as core_slots
    import adaos.services.skill.runtime_migration_worker as migration_worker

    if workspace_lock_present:
        lock_path = tmp_path / ".adaos" / "workspace.lock.json"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    monkeypatch.setenv("ADAOS_TESTING", "1")
    monkeypatch.setattr(api_server.app.state, "runtime_boot_task", None, raising=False)
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "ready", "ready": True, "started_at": 1.0, "completed_at": 2.0},
        raising=False,
    )
    monkeypatch.setattr(api_server.app.state, "skill_runtime_migration_started", False, raising=False)
    monkeypatch.setattr(api_server.app.state, "skill_runtime_migration_starting", False, raising=False)
    monkeypatch.setattr(
        core_slots,
        "active_slot_manifest",
        lambda: {"skill_runtime_migration": {"deferred": True, "background_required": True}},
    )
    monkeypatch.setattr(
        api_server,
        "get_ctx",
        lambda: types.SimpleNamespace(paths=types.SimpleNamespace(workspace_dir=lambda: tmp_path)),
    )
    calls: list[dict] = []

    async def _accepted(*_args, **kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "accepted": True, "status": {"state": "scheduled"}}

    monkeypatch.setattr(migration_worker, "start_background_migration", _accepted)

    payload = asyncio.run(
        api_server._start_post_boot_skill_runtime_migration(
            api_server.app,
            reason="test.post_boot",
        )
    )

    assert payload["started"] is True
    assert calls[0]["sync_workspace"] is expected_sync_workspace


def test_background_boot_defaults_to_supervisor_or_autostart(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_RUNTIME_BACKGROUND_BOOT", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.delenv("ADAOS_AUTOSTART_MODE", raising=False)

    assert api_server._background_boot_enabled() is False

    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    assert api_server._background_boot_enabled() is True

    monkeypatch.delenv("ADAOS_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.setenv("ADAOS_AUTOSTART_MODE", "true")
    assert api_server._background_boot_enabled() is True


def test_background_boot_explicit_env_overrides_managed_mode(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_AUTOSTART_MODE", "1")

    monkeypatch.setenv("ADAOS_RUNTIME_BACKGROUND_BOOT", "0")
    assert api_server._background_boot_enabled() is False

    monkeypatch.setenv("ADAOS_RUNTIME_BACKGROUND_BOOT", "yes")
    assert api_server._background_boot_enabled() is True


def test_runtime_retire_shutdown_skips_subnet_lifecycle(monkeypatch) -> None:
    emitted: list[str] = []

    async def _emit(event_type: str, _payload: dict, *, drain_timeout: float) -> bool:
        emitted.append(event_type)
        return True

    monkeypatch.setattr(api_server, "request_drain", lambda **_kwargs: None)
    monkeypatch.setattr(api_server, "_emit_shutdown_event", _emit)
    monkeypatch.setattr(api_server, "_write_runtime_profile_shutdown_debug", lambda _payload: None)
    monkeypatch.setattr(
        api_server,
        "get_ctx",
        lambda: types.SimpleNamespace(config=types.SimpleNamespace(subnet_id="sn_test")),
    )
    monkeypatch.setattr(api_server.app.state, "shutdown_requested", False, raising=False)
    monkeypatch.setattr(api_server.app.state, "shutdown_reason", "signal", raising=False)
    monkeypatch.setattr(api_server.app.state, "shutdown_drain_timeout", 5.0, raising=False)
    monkeypatch.setattr(api_server.app.state, "shutdown_lifecycle_scope", "subnet", raising=False)
    monkeypatch.setattr(api_server.app.state, "shutdown_stopping_emitted", False, raising=False)
    background = BackgroundTasks()

    response = asyncio.run(
        api_server.admin_shutdown(
            api_server.ShutdownRequest(
                reason="supervisor.fast_cutover.old_active_stop",
                drain_timeout_sec=5.0,
                signal_delay_sec=0.25,
                lifecycle_scope="runtime_retire",
            ),
            background,
        )
    )

    assert response.accepted is True
    assert emitted == []
    assert api_server.app.state.shutdown_lifecycle_scope == "runtime_retire"
    assert api_server.app.state.shutdown_stopping_emitted is True
    assert len(background.tasks) == 1


def test_shutdown_request_defaults_to_subnet_lifecycle() -> None:
    assert api_server.ShutdownRequest().lifecycle_scope == "subnet"


def test_admin_lifecycle_exposes_delayed_verification_worker(monkeypatch) -> None:
    monkeypatch.setattr(api_server, "runtime_lifecycle_snapshot", lambda: {"node_state": "ready"})
    monkeypatch.setattr(
        api_server,
        "_runtime_identity_public_payload",
        lambda: {"runtime_instance_id": "rt-test"},
    )

    async def _probe() -> dict:
        task = asyncio.create_task(asyncio.sleep(60))
        monkeypatch.setattr(
            api_server.app.state,
            "artifact_delayed_verification_task",
            task,
            raising=False,
        )
        try:
            return await api_server.admin_lifecycle()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    payload = asyncio.run(_probe())

    assert payload["artifact_delayed_verification"] == {
        "status": "running",
        "poll_seconds": api_server._artifact_observation_poll_seconds(),
    }


def test_node_status_exposes_runtime_environment(monkeypatch) -> None:
    monkeypatch.setenv("ENV_TYPE", "dev")

    payload = node_api._node_status_payload()

    assert payload["environment"]["envType"] == "dev"
    assert payload["environment"]["debug"] is True
    assert payload["runtime"]["environment"]["envType"] == "dev"


def test_node_status_probe_profile_avoids_diagnostic_payload(monkeypatch) -> None:
    probe_payload = {
        "node_id": "hub-1",
        "subnet_id": "sn-1",
        "role": "hub",
        "ready": True,
        "status_profile": "probe",
        "runtime": {"runtime_url": "http://127.0.0.1:8777"},
        "environment": {"envType": "prod"},
    }

    def _unexpected_diagnostic_payload():
        raise AssertionError("probe status must not build diagnostic status")

    monkeypatch.setattr(node_api, "_node_status_payload", _unexpected_diagnostic_payload)
    monkeypatch.setattr(node_api, "current_node_probe_status_payload", lambda: probe_payload)

    payload = asyncio.run(node_api.node_status(diagnostics=False, profile="probe")).model_dump()

    assert payload["status_profile"] == "probe"
    assert payload["node_id"] == "hub-1"
    assert payload["runtime"] == {"runtime_url": "http://127.0.0.1:8777"}
    assert len(json.dumps(payload).encode("utf-8")) < 2 * 1024


def test_current_node_probe_status_uses_live_state_without_supervisor_or_sidecar_io(monkeypatch) -> None:
    monkeypatch.setattr(
        system_model_service,
        "current_node_identity_status_payload",
        lambda: {
            "node_id": "hub-1",
            "subnet_id": "sn-1",
            "role": "hub",
            "ready": True,
            "node_state": "ready",
        },
    )
    monkeypatch.setattr(system_model_service, "runtime_port_http_base_from_env", lambda: "http://127.0.0.1:8778")
    monkeypatch.setattr(system_model_service, "runtime_environment_payload", lambda: {"envType": "prod"})

    def _unexpected_diagnostic_io(*_args, **_kwargs):
        raise AssertionError("probe status must not read supervisor or sidecar diagnostics")

    monkeypatch.setattr(system_model_service, "_node_status_supervisor_runtime", _unexpected_diagnostic_io)
    monkeypatch.setattr(system_model_service, "_node_status_sidecar_runtime", _unexpected_diagnostic_io)

    payload = system_model_service.current_node_probe_status_payload()

    assert payload["status_profile"] == "probe"
    assert payload["runtime"]["runtime_url"] == "http://127.0.0.1:8778"
    assert payload["runtime"]["runtime_state"] == "ready"


def test_node_status_push_excludes_recursive_diagnostics(monkeypatch) -> None:
    full_payload = {
        "node_id": "hub-1",
        "subnet_id": "sn-1",
        "role": "hub",
        "ready": True,
        "environment": {"envType": "dev"},
        "runtime": {
            "environment": {"envType": "dev"},
            "supervisor_available": True,
            "supervisor_runtime": {
                "available": True,
                "status": {
                    "state": "preparing",
                    "phase": "prewarm",
                    "action": "update",
                    "planned_reason": "minimum_update_period",
                    "candidate_prewarm_state": "ready",
                    "manifest": {"history": "x" * (300 * 1024)},
                },
                "attempt": {
                    "state": "recovering",
                    "target_version": "0.1.817",
                    "planned_reason": "minimum_update_period",
                    "last_status": {"incident_history": "x" * (300 * 1024)},
                },
                "runtime": {
                    "runtime_state": "ready",
                    "managed_alive": True,
                    "monitor": {"history": "x" * (300 * 1024)},
                },
            },
            "sidecar_runtime": {
                "status": "ready",
                "session_state": "remote_active",
                "last_diag": {"history": "x" * (300 * 1024)},
            },
            "core_update_status": {
                "state": "preparing",
                "phase": "prewarm",
                "candidate_prewarm_state": "ready",
                "manifest": {"history": "x" * (300 * 1024)},
            },
        },
    }
    monkeypatch.setattr(system_model_service, "current_node_status_payload", lambda: full_payload)

    payload = system_model_service.current_node_status_push_payload(updated_at=1.0)

    assert payload["runtime"]["supervisor_runtime"]["attempt"] == {
        "state": "recovering",
        "target_version": "0.1.817",
        "planned_reason": "minimum_update_period",
    }
    assert payload["runtime"]["supervisor_runtime"]["status"] == {
        "state": "preparing",
        "phase": "prewarm",
        "action": "update",
        "planned_reason": "minimum_update_period",
        "candidate_prewarm_state": "ready",
    }
    assert payload["runtime"]["supervisor_runtime"]["runtime"] == {
        "managed_alive": True,
        "runtime_state": "ready",
    }
    assert payload["runtime"]["sidecar_runtime"] == {
        "status": "ready",
        "session_state": "remote_active",
    }
    assert payload["runtime"]["core_update_status"] == {
        "state": "preparing",
        "phase": "prewarm",
        "candidate_prewarm_state": "ready",
    }
    assert payload["_meta"] == {
        "projection": "adaos.node_status.transport.v1",
        "diagnostics_truncated": True,
    }
    assert len(json.dumps(payload).encode("utf-8")) < 32 * 1024


def test_node_status_overlays_fresh_sidecar_runtime(monkeypatch) -> None:
    fresh_sidecar = {
        "enabled": True,
        "status": "ready",
        "route_tunnel_contract": {
            "ws": {"current_owner": "sidecar", "handoff_ready": True},
            "yws": {"current_owner": "sidecar", "handoff_ready": True},
        },
    }

    monkeypatch.setattr(
        system_model_service,
        "_node_status_supervisor_runtime",
        lambda _base_dir: {
            "available": True,
            "runtime": {
                "runtime_state": "ready",
                "sidecar": {"enabled": False, "status": "stale"},
            },
            "status": {},
        },
    )
    monkeypatch.setattr(
        system_model_service,
        "sidecar_runtime_snapshot",
        lambda **_kwargs: fresh_sidecar,
    )

    payload = node_api._node_status_payload()

    assert payload["runtime"]["sidecar_runtime"] == fresh_sidecar
    supervisor_runtime = payload["runtime"]["supervisor_runtime"]
    assert supervisor_runtime["runtime"]["sidecar"] == fresh_sidecar
    assert supervisor_runtime["runtime"]["sidecar_source"] == "reliability.sidecar_runtime_snapshot"


def test_node_status_endpoint_defaults_to_bounded_projection(monkeypatch) -> None:
    full_payload = {
        "node_id": "hub-1",
        "subnet_id": "sn-1",
        "role": "hub",
        "ready": True,
        "runtime": {
            "supervisor_available": True,
            "supervisor_runtime": {
                "available": True,
                "status": {
                    "state": "preparing",
                    "phase": "prewarm",
                    "manifest": {"history": "x" * (300 * 1024)},
                },
                "attempt": {"state": "active", "last_status": {"history": "x" * (300 * 1024)}},
                "runtime": {"runtime_url": "http://127.0.0.1:8778", "runtime_state": "ready"},
            },
            "core_update_status": {
                "state": "preparing",
                "manifest": {"history": "x" * (300 * 1024)},
            },
        },
        "environment": {"envType": "dev"},
    }
    monkeypatch.setattr(node_api, "_node_status_payload", lambda: full_payload)

    compact = asyncio.run(node_api.node_status(diagnostics=False)).model_dump()
    full = asyncio.run(node_api.node_status(diagnostics=True)).model_dump()

    assert compact["runtime"]["supervisor_runtime"]["status"] == {
        "state": "preparing",
        "phase": "prewarm",
    }
    assert compact["runtime"]["supervisor_runtime"]["attempt"] == {"state": "active"}
    assert len(json.dumps(compact).encode("utf-8")) < 32 * 1024
    assert full["runtime"]["supervisor_runtime"]["status"]["manifest"]["history"]


def test_subnet_identity_is_distinct_from_hub_node_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        api_server,
        "get_ctx",
        lambda: types.SimpleNamespace(
            config=types.SimpleNamespace(
                subnet_id="sn_92ffc943",
                node_names=["homepoint"],
                primary_node_name="homepoint",
            )
        ),
    )
    monkeypatch.setattr(
        api_server,
        "load_subnet_alias",
        lambda *, subnet_id=None: "ruhub" if subnet_id == "sn_92ffc943" else None,
    )

    payload = asyncio.run(api_server.get_alias())

    assert payload == {
        "ok": True,
        "schema": "adaos.subnet.identity.v1",
        "subnet_id": "sn_92ffc943",
        "subnet_names": ["ruhub"],
        "primary_subnet_name": "ruhub",
        "alias": "ruhub",
    }
    assert "node_names" not in payload
    assert "primary_node_name" not in payload


def test_set_subnet_alias_acknowledges_durable_identity_before_projection(monkeypatch) -> None:
    saved: list[tuple[str, str]] = []
    bus = types.SimpleNamespace(publish=lambda _event: None)
    config = types.SimpleNamespace(subnet_id="sn_92ffc943")
    monkeypatch.setattr(
        api_server,
        "get_ctx",
        lambda: types.SimpleNamespace(config=config, bus=bus),
    )
    monkeypatch.setattr(
        api_server,
        "save_subnet_alias",
        lambda alias, *, subnet_id=None: saved.append((alias, subnet_id)),
    )
    monkeypatch.setattr(
        api_server,
        "load_subnet_alias",
        lambda *, subnet_id=None: "ruhub" if subnet_id == "sn_92ffc943" else None,
    )
    background = BackgroundTasks()

    payload = asyncio.run(
        api_server.set_alias(
            api_server.SetAliasRequest(alias="ruhub"),
            background,
        )
    )

    assert saved == [("ruhub", "sn_92ffc943")]
    assert payload["primary_subnet_name"] == "ruhub"
    assert payload["projection_refreshed"] is False
    assert payload["projection_refresh_scheduled"] is True
    assert len(background.tasks) == 1
    assert background.tasks[0].args[1] is bus


def test_subnet_alias_background_refresh_publishes_timestamped_event(monkeypatch) -> None:
    from adaos.services import named_entity_projection

    published: list[object] = []

    async def _request_projection(**_kwargs):
        return {"pending": False}

    monkeypatch.setattr(named_entity_projection, "default_webspace_id", lambda: "default")
    monkeypatch.setattr(named_entity_projection, "request_named_entity_projection", _request_projection)
    bus = types.SimpleNamespace(publish=published.append)
    event_payload = {
        "alias": "ruhub",
        "subnet_id": "sn_92ffc943",
        "webspace_id": None,
    }

    asyncio.run(api_server._refresh_subnet_alias_dependents(event_payload, bus))

    assert len(published) == 1
    event = published[0]
    assert event.type == "subnet.alias.changed"
    assert event.payload == event_payload
    assert event.source == "api"
    assert isinstance(event.ts, float)


@pytest.mark.parametrize("origin", ["https://inimatic.web.app", "https://inimatic.com"])
def test_private_network_access_middleware_allows_cross_origin_loopback_probe(origin: str) -> None:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "OPTIONS",
        "scheme": "http",
        "path": "/api/ping",
        "raw_path": b"/api/ping",
        "query_string": b"",
        "headers": [
            (b"origin", origin.encode("ascii")),
            (b"access-control-request-method", b"GET"),
            (b"access-control-request-private-network", b"true"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8777),
    }

    async def _call_next(_request):
        return Response(status_code=599)

    response = asyncio.run(
        api_server.private_network_access_middleware(Request(scope), _call_next)
    )

    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers["Access-Control-Allow-Methods"] == "GET"
    assert response.headers["Access-Control-Allow-Private-Network"] == "true"
    assert response.headers["Vary"] == "Origin"


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/node/status", "GET"),
        ("/api/tools/call", "POST"),
        ("/api/node/yjs/webspaces/desktop/toggle-install", "POST"),
    ],
)
def test_runtime_cors_preflight_allows_local_browser_headers(path: str, method: str) -> None:
    client = TestClient(api_server.app)

    response = client.options(
        path,
        headers={
            "Origin": "http://localhost:4200",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": (
                "content-type,x-adaos-token,x-adaos-trace-id,"
                "x-adaos-device-id,authorization"
            ),
        },
    )

    assert response.status_code in {200, 204}
    assert response.headers["Access-Control-Allow-Origin"] in {"*", "http://localhost:4200"}
    allowed_headers = response.headers.get("Access-Control-Allow-Headers", "").lower()
    assert "x-adaos-trace-id" in allowed_headers
    assert "x-adaos-device-id" in allowed_headers


def test_runtime_cors_preflight_allows_private_network_tool_call() -> None:
    client = TestClient(api_server.app)

    response = client.options(
        "/api/tools/call",
        headers={
            "Origin": "http://localhost:4200",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-adaos-token,x-adaos-trace-id",
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:4200"
    assert response.headers["Access-Control-Allow-Private-Network"] == "true"
    assert "x-adaos-trace-id" in response.headers.get("Access-Control-Allow-Headers", "").lower()


def test_admin_root_mcp_call_allows_live_nlu_probe(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _Resp:
        ok = True

        def to_dict(self) -> dict[str, object]:
            return {
                "ok": True,
                "status": "ok",
                "result": {"check": {"intent": "desktop.open_modal"}},
            }

    monkeypatch.setattr(
        api_server,
        "get_ctx",
        lambda: types.SimpleNamespace(config=types.SimpleNamespace(subnet_id="sn-test")),
    )

    def _invoke(tool_id, **kwargs):
        calls.append({"tool_id": tool_id, **kwargs})
        return _Resp()

    monkeypatch.setattr(api_server, "invoke_root_mcp_tool", _invoke)

    payload = asyncio.run(
        api_server.admin_root_mcp_call(
            api_server.AdminRootMcpCallRequest(
                tool_id="nlu_authoring.check_phrase",
                arguments={"text": "Покажи медиа сервер"},
                request_id="req-1",
                trace_id="trace-1",
            )
        )
    )

    assert payload["ok"] is True
    assert payload["scope"]["subnet_id"] == "sn-test"
    assert payload["scope"]["target_id"] == "hub:sn-test"
    assert calls[0]["tool_id"] == "nlu_authoring.check_phrase"
    assert calls[0]["actor"] == "root:route_proxy"
    assert calls[0]["auth_method"] == "root_token"
    assert calls[0]["scope"]["target_id"] == "hub:sn-test"


def test_admin_root_mcp_call_blocks_non_allowlisted_tool() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_server.admin_root_mcp_call(
                api_server.AdminRootMcpCallRequest(tool_id="nlu_authoring.apply_template_patch")
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "tool_not_allowed"


@pytest.mark.parametrize(
    ("callable_name", "body"),
    [
        ("admin_update_start", lambda: api_server.CoreUpdateStartRequest(reason="test.update")),
        ("admin_update_cancel", lambda: api_server.CoreUpdateCancelRequest(reason="test.cancel")),
        ("admin_update_rollback", lambda: api_server.CoreUpdateRollbackRequest(reason="test.rollback")),
    ],
)
def test_candidate_runtime_rejects_mutating_update_calls(monkeypatch, callable_name, body) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_INSTANCE_ID", "rt-b-c-12345678")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8778")

    fn = getattr(api_server, callable_name)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(fn(body()))

    detail = exc_info.value.detail
    assert exc_info.value.status_code == 409
    assert detail["error"] == "candidate_runtime_is_passive"
    assert detail["runtime"]["transition_role"] == "candidate"
    assert detail["runtime"]["runtime_instance_id"] == "rt-b-c-12345678"
    assert detail["runtime"]["admin_mutation_allowed"] is False


def test_admin_update_start_forwards_to_supervisor_when_autostart_managed(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_AUTOSTART_MANAGED", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HOST", "127.0.0.1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_PORT", "8776")
    monkeypatch.setenv("ADAOS_TOKEN", "dev-token")
    monkeypatch.setenv("ADAOS_DEV_ALLOW_CORE_UPDATE", "1")
    calls: list[tuple[str, str, str]] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True, "accepted": True, "_served_by": "supervisor"}

    def _post(url: str, headers=None, json=None, timeout=None):
        calls.append((url, headers["X-AdaOS-Token"], json["target_version"]))
        return _Resp()

    monkeypatch.setattr("requests.post", _post)

    payload = asyncio.run(
        api_server.admin_update_start(
            api_server.CoreUpdateStartRequest(
                target_rev="rev2026",
                target_version="abc123",
                reason="test.supervisor",
            )
        )
    )

    assert payload["_served_by"] == "supervisor"
    assert calls == [("http://127.0.0.1:8776/api/supervisor/update/start", "dev-token", "abc123")]


def test_admin_update_start_does_not_block_runtime_event_loop(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_AUTOSTART_MANAGED", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HOST", "127.0.0.1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_PORT", "8776")
    monkeypatch.setenv("ADAOS_TOKEN", "dev-token")
    monkeypatch.setenv("ADAOS_DEV_ALLOW_CORE_UPDATE", "1")

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True, "accepted": True, "_served_by": "supervisor"}

    def _slow_post(*_args, **_kwargs):
        time.sleep(0.2)
        return _Resp()

    monkeypatch.setattr("requests.post", _slow_post)

    async def _exercise() -> dict[str, object]:
        task = asyncio.create_task(
            api_server.admin_update_start(
                api_server.CoreUpdateStartRequest(reason="test.supervisor.nonblocking")
            )
        )
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(0.03)
        assert asyncio.get_running_loop().time() - started < 0.12
        assert not task.done()
        return await task

    payload = asyncio.run(_exercise())

    assert payload["_served_by"] == "supervisor"


def test_admin_update_start_refuses_runtime_fallback_when_supervisor_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_AUTOSTART_MANAGED", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HOST", "127.0.0.1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_PORT", "8776")
    monkeypatch.setenv("ADAOS_TOKEN", "dev-token")
    monkeypatch.setenv("ADAOS_DEV_ALLOW_CORE_UPDATE", "1")
    calls: list[str] = []

    def _post(url: str, headers=None, json=None, timeout=None):
        calls.append(url)
        raise TimeoutError("supervisor unavailable")

    async def _write_status(_payload):
        raise AssertionError("runtime fallback must not write local update status")

    monkeypatch.setattr("requests.post", _post)
    monkeypatch.setattr(api_server, "write_core_update_status_async", _write_status)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_server.admin_update_start(
                api_server.CoreUpdateStartRequest(
                    target_rev="rev2026",
                    target_version="abc123",
                    reason="test.supervisor.unavailable",
                )
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "supervisor_update_route_unavailable"
    assert exc_info.value.detail["action"] == "update.start"
    assert calls == ["http://127.0.0.1:8776/api/supervisor/update/start"]


def test_admin_update_start_refuses_detached_runtime_without_restart_authority(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_DEV_ALLOW_CORE_UPDATE", "1")
    monkeypatch.setattr(api_server, "_try_forward_update_start_to_supervisor", lambda _body: None)

    async def _write_status(_payload):
        raise AssertionError("detached runtime must not write an update status or shutdown plan")

    monkeypatch.setattr(api_server, "write_core_update_status_async", _write_status)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_server.admin_update_start(
                api_server.CoreUpdateStartRequest(
                    target_rev="rev2026",
                    target_version="abc123",
                    reason="test.detached",
                )
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "update_restart_authority_unavailable"
    assert exc_info.value.detail["action"] == "update.start"


def test_admin_update_rollback_forwards_to_supervisor_when_autostart_managed(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_AUTOSTART_MANAGED", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HOST", "127.0.0.1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_PORT", "8776")
    monkeypatch.setenv("ADAOS_TOKEN", "dev-token")
    monkeypatch.setenv("ADAOS_DEV_ALLOW_CORE_UPDATE", "1")
    calls: list[tuple[str, str]] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True, "accepted": True, "_served_by": "supervisor"}

    def _post(url: str, headers=None, json=None, timeout=None):
        calls.append((url, json["reason"]))
        return _Resp()

    monkeypatch.setattr("requests.post", _post)

    payload = asyncio.run(
        api_server.admin_update_rollback(
            api_server.CoreUpdateRollbackRequest(reason="test.rollback.supervisor")
        )
    )

    assert payload["_served_by"] == "supervisor"
    assert calls == [("http://127.0.0.1:8776/api/supervisor/update/rollback", "test.rollback.supervisor")]


def test_admin_update_rollback_refuses_detached_runtime_without_restart_authority(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_DEV_ALLOW_CORE_UPDATE", "1")
    monkeypatch.setattr(api_server, "_try_forward_update_rollback_to_supervisor", lambda _body: None)

    async def _write_status(_payload):
        raise AssertionError("detached runtime must not write a rollback status or shutdown plan")

    monkeypatch.setattr(api_server, "write_core_update_status_async", _write_status)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_server.admin_update_rollback(
                api_server.CoreUpdateRollbackRequest(reason="test.rollback.detached")
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "update_restart_authority_unavailable"
    assert exc_info.value.detail["action"] == "update.rollback"


def test_admin_update_status_includes_runtime_identity(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_INSTANCE_ID", "rt-b-c-abcdef12")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8778")
    owner_thread = threading.get_ident()
    read_threads: list[int] = []

    def _read(value):
        def _inner():
            read_threads.append(threading.get_ident())
            return value

        return _inner

    monkeypatch.setattr(api_server, "read_core_update_status", _read({"state": "idle"}))
    monkeypatch.setattr(api_server, "read_core_update_last_result", _read({"state": "succeeded"}))
    monkeypatch.setattr(api_server, "read_core_update_plan", _read(None))
    monkeypatch.setattr(api_server, "core_slot_status", _read({"active_slot": "B"}))
    monkeypatch.setattr(api_server, "active_slot_manifest", _read({"slot": "B"}))

    payload = asyncio.run(api_server.admin_update_status())

    assert payload["status"]["state"] == "idle"
    assert payload["runtime"]["transition_role"] == "candidate"
    assert payload["runtime"]["slot"] == "B"
    assert payload["runtime"]["runtime_port"] == 8778
    assert payload["runtime"]["admin_mutation_allowed"] is False
    assert len(read_threads) == 5
    assert all(thread_id != owner_thread for thread_id in read_threads)


def test_supervisor_manages_sidecar_helper(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    assert api_server._supervisor_manages_sidecar() is True

    monkeypatch.delenv("ADAOS_SUPERVISOR_ENABLED", raising=False)
    assert api_server._supervisor_manages_sidecar() is False


def test_candidate_runtime_can_be_promoted_to_active(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_INSTANCE_ID", "rt-b-c-abcdef12")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8778")
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "ready", "ready": True, "started_at": 1.0, "completed_at": 2.0},
        raising=False,
    )

    reconnect_calls: list[tuple[str | None, str | None, bool]] = []
    service_start_reasons: list[str] = []
    call_order: list[str] = []
    from adaos.services import autostart

    class _ServiceSupervisor:
        async def start_all(self) -> None:
            call_order.append("services")

    async def _reconnect(
        *,
        transport: str | None = None,
        url_override: str | None = None,
        wait_for_authority: bool = False,
    ):
        call_order.append("reconnect")
        reconnect_calls.append((transport, url_override, wait_for_authority))
        return {"ok": True, "accepted": True, "authority": {"required": True, "ready": True}}

    monkeypatch.setattr(api_server, "get_service_supervisor", lambda: _ServiceSupervisor())
    monkeypatch.setattr(
        autostart,
        "ensure_linux_process_handoff_unit",
        lambda: {"ok": True, "changed": False, "kill_mode": "process"},
    )
    monkeypatch.setattr(api_server, "request_hub_root_reconnect", _reconnect)
    monkeypatch.setattr(
        api_server,
        "_schedule_promoted_runtime_service_start",
        lambda reason: service_start_reasons.append(reason) or {"background": True, "scheduled": True},
    )

    payload = asyncio.run(
        api_server.admin_runtime_promote_active(
            api_server.RuntimePromoteActiveRequest(reason="test.cutover", reconnect_hub_root=True)
        )
    )

    assert payload["ok"] is True
    assert payload["accepted"] is True
    assert payload["runtime"]["transition_role"] == "active"
    assert payload["runtime"]["runtime_instance_id"] == "rt-b-c-abcdef12"
    assert payload["runtime"]["admin_mutation_allowed"] is True
    assert payload["reconnect"]["ok"] is True
    assert payload["service_start"]["background"] is True
    assert payload["service_start"]["scheduled"] is True
    assert payload["supervisor_handoff_unit"]["kill_mode"] == "process"
    assert reconnect_calls == [(None, None, True)]
    assert service_start_reasons == ["test.cutover"]
    assert call_order == ["reconnect"]


def test_candidate_promotion_runs_deferred_sys_ready_after_service_start(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_PROMOTION_READY_EVENT_DELAY_S", "0")
    call_order: list[object] = []

    class _ServiceSupervisor:
        async def start_all(self) -> None:
            call_order.append("services")

    async def _emit(event_type, payload, **kwargs) -> None:
        call_order.append((event_type, payload, kwargs))

    monkeypatch.setattr(api_server, "get_service_supervisor", lambda: _ServiceSupervisor())
    monkeypatch.setattr(api_server, "_read_core_update_status_async", lambda: asyncio.sleep(0, result={"state": "idle"}))
    monkeypatch.setattr(api_server.sdk_data_bus, "emit", _emit)
    monkeypatch.setattr(
        api_server,
        "get_ctx",
        lambda: (_ for _ in ()).throw(AssertionError("raw AgentContext bus must not be used as the SDK bus")),
    )

    asyncio.run(api_server._start_service_skills_after_promotion("test.cutover"))

    assert call_order[0] == "services"
    event_type, payload, kwargs = call_order[1]
    assert event_type == "sys.ready"
    assert payload["promoted"] is True
    assert payload["reason"] == "test.cutover"
    assert kwargs == {"source": "lifecycle.promotion", "actor": "system"}


def test_candidate_promotion_defers_service_start_until_core_update_finishes(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_PROMOTION_READY_EVENT_DELAY_S", "0")
    statuses = iter(
        [
            {"state": "restarting", "phase": "launch"},
            {"state": "applying", "phase": "root_promotion"},
            {"state": "failed", "phase": "root_promotion"},
        ]
    )
    calls: list[str] = []

    class _ServiceSupervisor:
        async def start_all(self) -> None:
            calls.append("services")

    async def _read_status() -> dict[str, str]:
        value = next(statuses)
        calls.append(f"status:{value['state']}:{value['phase']}")
        return value

    async def _sleep(_delay: float) -> None:
        calls.append("wait")

    async def _emit(*_args, **_kwargs) -> None:
        calls.append("ready")

    monkeypatch.setattr(api_server, "get_service_supervisor", lambda: _ServiceSupervisor())
    monkeypatch.setattr(api_server, "_read_core_update_status_async", _read_status)
    monkeypatch.setattr(api_server.asyncio, "sleep", _sleep)
    monkeypatch.setattr(api_server.sdk_data_bus, "emit", _emit)

    asyncio.run(api_server._start_service_skills_after_promotion("test.cutover"))

    assert calls == [
        "status:restarting:launch",
        "wait",
        "status:applying:root_promotion",
        "wait",
        "status:failed:root_promotion",
        "services",
        "ready",
    ]
    assert api_server._promoted_service_start_status_payload()["state"] == "ready"
    assert api_server._promoted_service_start_status_payload()["update_state"] == "failed"


def test_promote_active_is_idempotent_for_active_runtime(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    monkeypatch.setenv("ADAOS_RUNTIME_INSTANCE_ID", "rt-a-a-abcdef12")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "A")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")

    payload = asyncio.run(
        api_server.admin_runtime_promote_active(
            api_server.RuntimePromoteActiveRequest(reason="test.cutover", reconnect_hub_root=True)
        )
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["runtime"]["transition_role"] == "active"
    assert payload["runtime"]["admin_mutation_allowed"] is True


def test_candidate_runtime_promotion_rejects_missing_hub_root_authority(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_INSTANCE_ID", "rt-b-c-abcdef12")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8778")
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "ready", "ready": True, "started_at": 1.0, "completed_at": 2.0},
        raising=False,
    )
    from adaos.services import autostart

    async def _reconnect(**_kwargs):
        return {
            "ok": False,
            "authority": {
                "required": True,
                "ready": False,
                "error": "hub_root_authority_timeout",
            },
        }

    monkeypatch.setattr(
        autostart,
        "ensure_linux_process_handoff_unit",
        lambda: {"ok": True, "changed": False, "kill_mode": "process"},
    )
    monkeypatch.setattr(api_server, "request_hub_root_reconnect", _reconnect)

    with pytest.raises(api_server.HTTPException) as exc_info:
        asyncio.run(
            api_server.admin_runtime_promote_active(
                api_server.RuntimePromoteActiveRequest(reason="test.cutover", reconnect_hub_root=True)
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "hub_root_authority_not_ready"
    assert api_server.runtime_transition_role() == "candidate"


def test_candidate_runtime_promotion_rejects_incomplete_boot(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "starting", "ready": False, "started_at": 1.0, "completed_at": None},
        raising=False,
    )

    with pytest.raises(api_server.HTTPException) as exc_info:
        asyncio.run(
            api_server.admin_runtime_promote_active(
                api_server.RuntimePromoteActiveRequest(reason="test.cutover", reconnect_hub_root=True)
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "runtime_boot_not_ready"
    assert api_server.runtime_transition_role() == "candidate"


def test_member_candidate_promotion_does_not_claim_hub_root_authority(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_INSTANCE_ID", "rt-b-c-abcdef12")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8778")
    monkeypatch.setattr(
        api_server.app.state,
        "runtime_boot_readiness",
        {"state": "ready", "ready": True, "started_at": 1.0, "completed_at": 2.0},
        raising=False,
    )
    from adaos.services import autostart

    wait_values: list[bool] = []
    member_reconnect_forces: list[bool] = []

    async def _reconnect(*, wait_for_authority: bool, **_kwargs):
        wait_values.append(wait_for_authority)
        return {"ok": True, "authority": {"required": False, "ready": None}}

    monkeypatch.setattr(api_server, "get_ctx", lambda: types.SimpleNamespace(config=types.SimpleNamespace(role="member")))
    monkeypatch.setattr(
        autostart,
        "ensure_linux_process_handoff_unit",
        lambda: {"ok": True, "changed": False, "kill_mode": "process"},
    )
    monkeypatch.setattr(api_server, "request_hub_root_reconnect", _reconnect)

    async def _member_reconnect(*, force: bool = False):
        member_reconnect_forces.append(force)
        return {"ok": True, "accepted": True, "role": "member"}

    monkeypatch.setattr(api_server, "request_member_hub_reconnect", _member_reconnect)
    monkeypatch.setattr(
        api_server,
        "_schedule_promoted_runtime_service_start",
        lambda _reason: {"background": True, "scheduled": True},
    )

    payload = asyncio.run(
        api_server.admin_runtime_promote_active(
            api_server.RuntimePromoteActiveRequest(reason="test.member.cutover", reconnect_hub_root=True)
        )
    )

    assert payload["ok"] is True
    assert payload["reconnect"]["role"] == "member"
    assert payload["reconnect"]["authority"] == {
        "kind": "member_hub",
        "required": False,
        "ready": None,
    }
    assert member_reconnect_forces == [True]
    assert wait_values == []


def test_admin_root_mcp_logs_returns_local_logs_by_default(monkeypatch) -> None:
    monkeypatch.setattr(api_server, "normalize_log_category", lambda category: "adaos")
    monkeypatch.setattr(api_server, "get_ctx", lambda: types.SimpleNamespace(config=types.SimpleNamespace(subnet_id="sn_local")))
    monkeypatch.setattr(
        api_server,
        "list_local_logs",
        lambda **kwargs: {
            "category": kwargs["category"],
            "source_mode": kwargs["source_mode"],
            "items": [{"name": "adaos.log"}],
        },
    )

    payload = asyncio.run(api_server.admin_root_mcp_logs("adaos", limit=2, lines=50))

    assert payload["ok"] is True
    assert payload["logs"]["category"] == "adaos"
    assert payload["logs"]["source_mode"] == "node_local_logs_dir"
    assert payload["logs"]["items"][0]["name"] == "adaos.log"


def test_admin_root_mcp_logs_aggregates_active_subnet_logs(monkeypatch) -> None:
    monkeypatch.setattr(api_server, "normalize_log_category", lambda category: "yjs")
    monkeypatch.setattr(
        api_server,
        "get_ctx",
        lambda: types.SimpleNamespace(config=types.SimpleNamespace(subnet_id="sn_92ffc943")),
    )

    async def _aggregate_subnet_logs(**kwargs):
        assert kwargs["category"] == "yjs"
        assert kwargs["subnet_id"] == "sn_92ffc943"
        assert kwargs["limit"] == 4
        assert kwargs["lines"] == 120
        assert kwargs["contains"] == "desktop"
        assert kwargs["include_hub"] is False
        return {
            "category": "yjs",
            "scope": "subnet_active",
            "nodes": [{"node_id": "member:alpha", "ok": True}],
        }

    monkeypatch.setattr(api_server, "aggregate_subnet_logs", _aggregate_subnet_logs)

    payload = asyncio.run(
        api_server.admin_root_mcp_logs(
            "yjs",
            limit=4,
            lines=120,
            contains="desktop",
            scope="subnet_active",
            include_hub=False,
        )
    )

    assert payload["ok"] is True
    assert payload["logs"]["category"] == "yjs"
    assert payload["logs"]["scope"] == "subnet_active"
    assert payload["logs"]["nodes"][0]["node_id"] == "member:alpha"
