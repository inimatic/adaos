from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from adaos.services.agent_context import get_ctx
from adaos.services.distributed_runtime import (
    SkillToolTopologyAdapter,
    get_distributed_runtime,
    register_distributed_runtime,
    register_service_invocation_receiver,
    register_topology_phase_receiver,
)
from adaos.services.project_deployment import (
    get_project_deployment_runtime,
    register_local_deployment_receiver,
    register_project_deployment_runtime,
)
from adaos.services.project_deployment import default_runtime
from adaos.services.project_deployment.default_runtime import (
    AdaOSComponentLifecycleHooks,
    configure_default_distributed_runtimes,
    deployment_runtime_inventory_payload,
    local_node_inventory_record,
)


def test_async_bridge_runs_when_activation_is_called_from_an_event_loop() -> None:
    async def invoke() -> str:
        return default_runtime._run_async_from_sync(asyncio.sleep(0, result="ready"))

    assert asyncio.run(invoke()) == "ready"


def test_skill_component_activation_reloads_live_handlers(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    class Manager:
        def activate_runtime(self, component_id: str, *, version: str) -> str:
            events.append(("slot", f"{component_id}:{version}"))
            return "B"

    monkeypatch.setattr(AdaOSComponentLifecycleHooks, "_skill_manager", lambda _self: Manager())
    monkeypatch.setattr(default_runtime, "runtime_mutation_lease", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_reload_skill_handlers",
        lambda _self, component_id, **_kwargs: (
            events.append(("handlers", component_id))
            or {"ok": True, "handlers": ["handlers/main.py"]}
        ),
    )
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_publish_skill_activation",
        lambda _self, component_id, **_kwargs: (
            events.append(("event", component_id)) or {"emitted": True}
        ),
    )
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_wait_for_skill_service_ready",
        lambda _self, component_id, **_kwargs: (
            events.append(("service", component_id))
            or {"managed": True, "ready": True}
        ),
    )
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_service_activation_status",
        staticmethod(lambda _component_id: {"managed": False, "ready": True}),
    )

    receipt = AdaOSComponentLifecycleHooks(SimpleNamespace()).activate(
        kind="skill",
        component_id="media_center_skill",
        version="0.8.23",
    )

    assert events == [
        ("slot", "media_center_skill:0.8.23"),
        ("handlers", "media_center_skill"),
        ("event", "media_center_skill"),
        ("service", "media_center_skill"),
    ]
    assert receipt["slot"] == "B"
    assert receipt["handler_reload"]["ok"] is True
    assert receipt["activation_event"]["emitted"] is True
    assert receipt["service"]["ready"] is True


def test_skill_component_activation_fails_when_live_handlers_do_not_activate(monkeypatch) -> None:
    class Manager:
        def activate_runtime(self, _component_id: str, *, version: str) -> str:
            return "A"

    monkeypatch.setattr(AdaOSComponentLifecycleHooks, "_skill_manager", lambda _self: Manager())
    monkeypatch.setattr(default_runtime, "runtime_mutation_lease", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_reload_skill_handlers",
        lambda _self, _component_id, **_kwargs: {
            "ok": False,
            "reason": "runtime_safety_validation_failed",
        },
    )

    with pytest.raises(RuntimeError, match="runtime_safety_validation_failed"):
        AdaOSComponentLifecycleHooks(SimpleNamespace()).activate(
            kind="skill",
            component_id="media_center_skill",
            version="0.8.23",
        )


def test_skill_service_activation_waits_for_new_process_spec(monkeypatch) -> None:
    hooks = AdaOSComponentLifecycleHooks(SimpleNamespace())
    observations = iter(
        (
            {
                "managed": True,
                "ready": False,
                "running": True,
                "process_spec_matches": False,
                "pid": 10,
                "process_observed_at": 2.0,
            },
            {
                "managed": True,
                "ready": True,
                "running": True,
                "process_spec_matches": True,
                "health_ok": True,
                "pid": 11,
                "process_observed_at": 3.0,
            },
        )
    )
    monkeypatch.setenv("ADAOS_PROJECT_SERVICE_ACTIVATION_TIMEOUT_S", "5")
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_service_activation_status",
        staticmethod(lambda _component_id: next(observations)),
    )
    monkeypatch.setattr(default_runtime, "_sleep", lambda _seconds: None)

    receipt = hooks._wait_for_skill_service_ready(
        "media_library_agent",
        previous={
            "managed": True,
            "ready": True,
            "running": True,
            "pid": 10,
            "process_observed_at": 1.0,
        },
    )

    assert receipt["ready"] is True
    assert receipt["process_spec_matches"] is True
    assert receipt["restart_required"] is True
    assert receipt["restart_observed"] is True


def test_skill_service_activation_does_not_accept_old_ready_process(monkeypatch) -> None:
    hooks = AdaOSComponentLifecycleHooks(SimpleNamespace())
    observations = iter(
        (
            {
                "managed": True,
                "ready": True,
                "running": True,
                "process_spec_matches": True,
                "health_ok": True,
                "pid": 10,
                "process_observed_at": 1.0,
            },
            {
                "managed": True,
                "ready": True,
                "running": True,
                "process_spec_matches": True,
                "health_ok": True,
                "pid": 12,
                "process_observed_at": 2.0,
            },
        )
    )
    monkeypatch.setenv("ADAOS_PROJECT_SERVICE_ACTIVATION_TIMEOUT_S", "5")
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_service_activation_status",
        staticmethod(lambda _component_id: next(observations)),
    )
    monkeypatch.setattr(default_runtime, "_sleep", lambda _seconds: None)

    receipt = hooks._wait_for_skill_service_ready(
        "media_library_agent",
        previous={
            "managed": True,
            "ready": True,
            "running": True,
            "pid": 10,
            "process_observed_at": 1.0,
        },
    )

    assert receipt["pid"] == 12
    assert receipt["restart_observed"] is True


def test_skill_service_activation_fails_when_new_process_never_converges(monkeypatch) -> None:
    hooks = AdaOSComponentLifecycleHooks(SimpleNamespace())
    clock = iter((0.0, 0.0, 6.0))
    monkeypatch.setenv("ADAOS_PROJECT_SERVICE_ACTIVATION_TIMEOUT_S", "5")
    monkeypatch.setattr(
        AdaOSComponentLifecycleHooks,
        "_service_activation_status",
        staticmethod(
            lambda _component_id: {
                "managed": True,
                "ready": False,
                "running": True,
                "process_spec_matches": False,
            }
        ),
    )
    monkeypatch.setattr(default_runtime, "_monotonic", lambda: next(clock))
    monkeypatch.setattr(default_runtime, "_sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="did not converge"):
        hooks._wait_for_skill_service_ready("media_library_agent")


def test_default_runtimes_share_durable_store_and_publish_local_inventory(monkeypatch) -> None:
    ctx = get_ctx()
    object.__setattr__(
        ctx,
        "config",
        SimpleNamespace(
            node_id="node-local",
            subnet_id="subnet-home",
            role="hub",
            token="test-token",
        ),
    )
    monkeypatch.setenv("ADAOS_NODE_DEPLOYMENT_URL", "http://192.0.2.10:8778")

    configured = configure_default_distributed_runtimes(ctx)
    deployment = get_project_deployment_runtime()
    distributed = get_distributed_runtime()
    payload = deployment_runtime_inventory_payload(ctx)
    node = local_node_inventory_record(ctx)

    assert configured["configured"] is True
    assert deployment.store.root.parent == distributed.deployment_store.root.parent
    assert node.node_id == "node-local"
    assert node.trust_state == "trusted"
    assert "project.activate" in node.capabilities
    assert node.endpoints[0].role == "deployment"
    assert payload["protocols"]["distributed_topology"] == "1"
    assert isinstance(distributed.topology_adapter, SkillToolTopologyAdapter)
    assert distributed.service_invoker is not None

    register_project_deployment_runtime(None)
    register_distributed_runtime(None)
    register_local_deployment_receiver(None)
    register_topology_phase_receiver(None)
    register_service_invocation_receiver(None)


def test_inventory_capacity_uses_stable_physical_totals(monkeypatch) -> None:
    ctx = get_ctx()
    disk_samples = iter((
        SimpleNamespace(total=2_000_000_000, free=900_000_000),
        SimpleNamespace(total=2_000_000_000, free=700_000_000),
    ))
    memory_samples = iter((
        SimpleNamespace(total=8 * 1024 * 1024 * 1024, available=5 * 1024 * 1024 * 1024),
        SimpleNamespace(total=8 * 1024 * 1024 * 1024, available=3 * 1024 * 1024 * 1024),
    ))
    monkeypatch.setattr(default_runtime.shutil, "disk_usage", lambda _path: next(disk_samples))

    import psutil

    monkeypatch.setattr(psutil, "virtual_memory", lambda: next(memory_samples))

    first = deployment_runtime_inventory_payload(ctx)["capacity"]
    second = deployment_runtime_inventory_payload(ctx)["capacity"]

    assert first == second
    assert first["storage_bytes"] == 2_000_000_000
    assert first["memory_mb"] == 8 * 1024
