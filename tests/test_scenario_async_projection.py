from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import threading
import time
from types import SimpleNamespace

import pytest

from adaos.services.scenario import manager as scenario_manager
from adaos.services.scenario import webspace_runtime


def test_async_projection_prepares_plain_payload_off_loop_but_applies_on_owner(monkeypatch):
    owner = threading.get_ident()
    threads = {}
    document = object()
    manager = object.__new__(scenario_manager.ScenarioManager)
    manager.caps = SimpleNamespace(require=lambda *_args: None)

    def prepare(scenario_id, *, space):
        assert (scenario_id, space) == ("example", "workspace")
        threads["prepare"] = threading.get_ident()
        time.sleep(0.12)
        return ({"desktop": {}}, {}, {}, {})

    def apply(doc, scenario_id, *_payload):
        assert doc is document and scenario_id == "example"
        threads["apply"] = threading.get_ident()

    @asynccontextmanager
    async def metadata():
        threads["metadata"] = threading.get_ident()
        yield

    @asynccontextmanager
    async def open_document(webspace_id):
        assert webspace_id == "isolated-projection"
        threads["document"] = threading.get_ident()
        yield document

    monkeypatch.setattr(manager, "_ensure_yjs_payload", prepare)
    monkeypatch.setattr(manager, "_project_to_doc", apply)
    monkeypatch.setattr(scenario_manager, "_scenario_manager_async_write_meta", metadata)
    monkeypatch.setattr(scenario_manager, "async_get_ydoc", open_document)

    async def exercise():
        task = asyncio.create_task(manager.sync_to_yjs_async("example", "isolated-projection", emit_event=False))
        beats = 0
        while not task.done():
            await asyncio.sleep(0.01)
            beats += 1
        await task
        assert beats >= 5

    asyncio.run(exercise())
    assert threads["prepare"] != owner
    assert threads["apply"] == threads["metadata"] == threads["document"] == owner


def test_failed_payload_preparation_never_opens_or_mutates_document(monkeypatch):
    manager = object.__new__(scenario_manager.ScenarioManager)
    manager.caps = SimpleNamespace(require=lambda *_args: None)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("missing scenario")

    def unexpected(*_args, **_kwargs):
        pytest.fail("failed preparation reached a YDoc operation")

    monkeypatch.setattr(manager, "_ensure_yjs_payload", missing)
    monkeypatch.setattr(manager, "_project_to_doc", unexpected)
    monkeypatch.setattr(scenario_manager, "async_get_ydoc", unexpected)
    with pytest.raises(FileNotFoundError, match="missing scenario"):
        asyncio.run(manager.sync_to_yjs_async("missing", "isolated-projection", emit_event=False))


@pytest.mark.parametrize("operation", ["reload", "home"])
def test_operator_preflight_does_not_scan_packages_on_owner(monkeypatch, operation):
    owner = threading.get_ident()
    state = SimpleNamespace(source_mode="workspace", kind="workspace", effective_home_scenario="missing", current_scenario="missing")
    observed = []

    async def resolve(*_args, **_kwargs):
        return state, "missing", "explicit"

    async def describe(*_args, **_kwargs):
        return state

    def preflight(*_args, **_kwargs):
        observed.append(threading.get_ident())
        time.sleep(0.12)
        return "", "explicit", {"reason": "scenario_missing"}

    monkeypatch.setattr(webspace_runtime, "_resolve_reload_scenario_target", resolve)
    monkeypatch.setattr(webspace_runtime, "describe_webspace_operational_state", describe)
    monkeypatch.setattr(webspace_runtime, "_preflight_validated_scenario", preflight)

    async def exercise():
        call = webspace_runtime.reload_webspace_from_scenario if operation == "reload" else webspace_runtime.go_home_webspace
        task = asyncio.create_task(call("isolated-projection"))
        beats = 0
        while not task.done():
            await asyncio.sleep(0.01)
            beats += 1
        assert (await task)["error"] == "scenario_not_found"
        assert beats >= 5

    asyncio.run(exercise())
    assert observed and observed[0] != owner
