from __future__ import annotations

import asyncio
import threading

from adaos.services.skill import service_supervisor_runtime as runtime_module


class _FakeSupervisor:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []
        self.discovery_forces: list[bool] = []

    def ensure_discovered(self, *, force: bool = False) -> None:
        self.discovery_forces.append(force)
        return None

    def list(self) -> list[str]:
        return ["service_skill", "voice_service"]

    async def restart(self, name: str) -> None:
        self.events.append(("restart", name))

    async def stop(self, name: str) -> None:
        self.events.append(("stop", name))

    async def shutdown(self) -> None:
        self.events.append(("shutdown", None))


class _FailingRestartSupervisor(_FakeSupervisor):
    async def restart(self, name: str) -> None:
        self.events.append(("restart", name))
        raise RuntimeError("restart failed")


class _FailingShutdownSupervisor(_FakeSupervisor):
    async def shutdown(self) -> None:
        self.events.append(("shutdown", None))
        raise RuntimeError("shutdown failed")


def test_service_supervisor_runtime_stops_service_on_skill_deactivated(monkeypatch) -> None:
    fake = _FakeSupervisor()
    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)

    asyncio.run(runtime_module._on_skill_deactivated({"name": "service_skill"}))

    assert fake.events == [("stop", "service_skill")]


def test_service_supervisor_runtime_forces_discovery_on_skill_activation(monkeypatch) -> None:
    fake = _FakeSupervisor()
    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)

    async def _run() -> None:
        await runtime_module._on_skill_activated({"skill_name": "service_skill"})
        await runtime_module._wait_for_activation_restarts()

    asyncio.run(_run())

    assert fake.discovery_forces == [True]
    assert fake.events == [("restart", "service_skill")]


def test_service_activation_restart_does_not_block_event_handler(monkeypatch) -> None:
    fake = _FakeSupervisor()
    restart_started = asyncio.Event()
    release_restart = asyncio.Event()

    async def _restart(name: str) -> None:
        restart_started.set()
        await release_restart.wait()
        fake.events.append(("restart", name))

    fake.restart = _restart  # type: ignore[method-assign]
    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)

    async def _run() -> None:
        await runtime_module._on_skill_activated({"skill_name": "service_skill"})
        await restart_started.wait()
        assert fake.events == []
        release_restart.set()
        await runtime_module._wait_for_activation_restarts()

    asyncio.run(_run())

    assert fake.events == [("restart", "service_skill")]


def test_service_supervisor_runtime_discovers_services_off_event_loop(monkeypatch) -> None:
    fake = _FakeSupervisor()
    discovery_threads: list[int] = []

    def _ensure_discovered() -> None:
        discovery_threads.append(threading.get_ident())

    fake.ensure_discovered = _ensure_discovered  # type: ignore[method-assign]
    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)

    async def _run() -> int:
        event_loop_thread = threading.get_ident()
        assert await runtime_module._restart_if_service("service_skill", reason="skills.activated") is True
        return event_loop_thread

    event_loop_thread = asyncio.run(_run())

    assert discovery_threads
    assert discovery_threads[0] != event_loop_thread
    assert fake.events == [("restart", "service_skill")]


def test_service_supervisor_runtime_stops_all_services_on_subnet_stopping(monkeypatch) -> None:
    fake = _FakeSupervisor()
    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)

    asyncio.run(runtime_module._on_subnet_stopping({"reason": "admin_shutdown"}))

    assert fake.events == [("shutdown", None)]


def test_service_supervisor_runtime_publishes_pending_action_on_restart_failure(monkeypatch) -> None:
    fake = _FailingRestartSupervisor()
    published: list[dict] = []

    async def _list_pending_actions_async(**kwargs):  # noqa: ANN001
        return {"active_items": []}

    async def _publish_pending_action_async(**kwargs):  # noqa: ANN001
        published.append(dict(kwargs))
        return {"id": "pa.runtime.recovery.1"}

    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)
    monkeypatch.setattr(runtime_module, "list_pending_actions_async", _list_pending_actions_async)
    monkeypatch.setattr(runtime_module, "publish_pending_action_async", _publish_pending_action_async)

    result = asyncio.run(runtime_module._restart_if_service("service_skill", reason="skills.activated"))

    assert result is False
    assert fake.events == [("restart", "service_skill")]
    assert len(published) == 1
    action = published[0]
    assert action["kind"] == "runtime.recovery.service_supervisor_failure"
    assert action["producer"]["system_id"] == "runtime_recovery"
    assert action["domain_ref"] == {
        "operation": "restart_service",
        "skill_name": "service_skill",
        "reason": "skills.activated",
    }
    assert action["response_route"]["topic"] == "runtime.recovery.service_supervisor.response"
    allowed = {item["id"]: item for item in action["allowed_actions"]}
    assert allowed["retry"]["terminal"] is True
    assert allowed["open_diagnostics"]["terminal"] is False


def test_service_supervisor_runtime_deduplicates_active_recovery_action(monkeypatch) -> None:
    fake = _FailingRestartSupervisor()

    async def _list_pending_actions_async(**kwargs):  # noqa: ANN001
        return {
            "active_items": [
                {
                    "id": "pa.existing",
                    "kind": "runtime.recovery.service_supervisor_failure",
                    "domain_ref": {
                        "operation": "restart_service",
                        "skill_name": "service_skill",
                        "reason": "skills.activated",
                    },
                }
            ]
        }

    async def _publish_pending_action_async(**kwargs):  # noqa: ANN001
        raise AssertionError("active recovery action should be reused")

    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)
    monkeypatch.setattr(runtime_module, "list_pending_actions_async", _list_pending_actions_async)
    monkeypatch.setattr(runtime_module, "publish_pending_action_async", _publish_pending_action_async)

    result = asyncio.run(runtime_module._restart_if_service("service_skill", reason="skills.activated"))

    assert result is False
    assert fake.events == [("restart", "service_skill")]


def test_service_supervisor_runtime_does_not_publish_when_shutdown_fallback_succeeds(monkeypatch) -> None:
    fake = _FailingShutdownSupervisor()

    async def _publish_pending_action_async(**kwargs):  # noqa: ANN001
        raise AssertionError("fallback stop succeeded, no human action is needed")

    monkeypatch.setattr(runtime_module, "get_service_supervisor", lambda: fake)
    monkeypatch.setattr(runtime_module, "publish_pending_action_async", _publish_pending_action_async)

    result = asyncio.run(runtime_module._stop_all_services(reason="subnet.stopping"))

    assert result is True
    assert fake.events == [("shutdown", None), ("stop", "service_skill"), ("stop", "voice_service")]


def test_service_supervisor_runtime_retries_from_pending_action_response(monkeypatch) -> None:
    calls: list[tuple[str | None, str]] = []
    events: list[tuple[str, dict]] = []

    async def _restart_if_service(skill_name: str | None, *, reason: str) -> bool:
        calls.append((skill_name, reason))
        return True

    monkeypatch.setattr(runtime_module, "_restart_if_service", _restart_if_service)
    monkeypatch.setattr(runtime_module, "_emit_runtime_recovery", lambda topic, payload: events.append((topic, dict(payload))))

    asyncio.run(
        runtime_module._on_runtime_recovery_response(
            {
                "pending_action_id": "pa.retry",
                "response": {"response_action_id": "retry"},
                "domain_ref": {"operation": "restart_service", "skill_name": "service_skill"},
            }
        )
    )

    assert calls == [("service_skill", "pending_action.retry:pa.retry")]
    assert events[0] == (
        "runtime.recovery.retry.started",
        {"pending_action_id": "pa.retry", "operation": "restart_service", "skill_name": "service_skill"},
    )
    assert events[-1] == (
        "runtime.recovery.retry.completed",
        {"pending_action_id": "pa.retry", "operation": "restart_service", "skill_name": "service_skill", "ok": True},
    )
