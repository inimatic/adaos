from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

if "nats" not in sys.modules:
    sys.modules["nats"] = types.ModuleType("nats")
if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.apps.api import tool_bridge as tool_bridge_module


def _fake_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        skills_repo=None,
        sql=None,
        git=None,
        paths=None,
        caps=None,
        settings=None,
        bus=None,
    )


def test_call_tool_offloads_local_execution_to_worker(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"{skill_name}:{tool_name}:{timeout}")
            return {"skill": skill_name, "tool": tool_name, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync")
        return func(*args, **kwargs)

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(tool="prompt_engineer_skill:prompt_list_project_objects", arguments={}),
            SimpleNamespace(headers={}),
            Response(),
            ctx=_fake_ctx(),
        )
    )

    assert calls[0] == "run_sync"
    assert calls[1] == "prompt_engineer_skill:prompt_list_project_objects:None"
    assert result["ok"] is True
    assert result["trace_id"] == "trace-123"


def test_call_tool_repairs_workspace_runtime_when_runtime_missing(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    (tmp_path / "workspace" / "skills" / "infrascope_skill").mkdir(parents=True, exist_ok=True)

    class _Paths:
        def skills_workspace_dir(self):
            return tmp_path / "workspace" / "skills"

        def repo_root(self):
            return tmp_path

    ctx = SimpleNamespace(
        skills_repo=None,
        sql=None,
        git=None,
        paths=_Paths(),
        caps=None,
        settings=None,
        bus=None,
    )

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            self.ready = False

        def runtime_status(self, _name: str) -> dict[str, object]:
            if not self.ready:
                raise RuntimeError("no versions installed")
            return {"ready": True}

        def runtime_update(self, name: str, *, space: str = "workspace") -> dict[str, object]:
            calls.append(f"update:{name}:{space}")
            return {"ok": False, "reason": "no_active_runtime"}

        def activate_for_space(
            self,
            name: str,
            *,
            space: str = "default",
            webspace_id: str | None = None,
            version: str | None = None,
            slot: str | None = None,
        ) -> str:
            calls.append(f"activate:{name}:{space}:{webspace_id}:{version}:{slot}")
            self.ready = True
            return "A"

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"run:{self.ready}:{skill_name}:{tool_name}:{timeout}")
            if not self.ready:
                raise RuntimeError("no versions installed")
            return {"skill": skill_name, "tool": tool_name, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)
    monkeypatch.setattr(tool_bridge_module, "default_webspace_id", lambda: "default")

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="infrascope_skill:get_overview_summary",
                arguments={"webspace_id": "ws-1"},
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert result["trace_id"] == "trace-123"
    assert calls == [
        "run:False:infrascope_skill:get_overview_summary:None",
        "update:infrascope_skill:workspace",
        "activate:infrascope_skill:default:ws-1:None:None",
        "run:True:infrascope_skill:get_overview_summary:None",
    ]


def test_call_tool_returns_gateway_timeout_when_worker_times_out(monkeypatch) -> None:
    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

    async def _fake_run_sync(_func, *args, **kwargs):
        raise TimeoutError("tool 'prompt_list_project_objects' timed out after 30 seconds")

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            tool_bridge_module.call_tool(
                tool_bridge_module.ToolCall(tool="prompt_engineer_skill:prompt_list_project_objects", arguments={}),
                SimpleNamespace(headers={}),
                Response(),
                ctx=_fake_ctx(),
            )
        )

    assert excinfo.value.status_code == 504
    assert "timed out" in str(excinfo.value.detail)


def test_call_tool_proxies_to_explicit_target_node_on_hub(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, *_args, **_kwargs):
            raise AssertionError("local tool execution should be bypassed for explicit target nodes")

    class _FakeDirectory:
        def get_node_base_url(self, node_id: str) -> str | None:
            calls.append(("base_url", node_id))
            return None

    class _FakeLinkManager:
        def is_connected(self, node_id: str) -> bool:
            calls.append(("is_connected", node_id))
            return True

        async def rpc_tools_call(self, node_id: str, *, tool: str, arguments: dict[str, object], timeout=None, dev=False):
            calls.append(("rpc", node_id))
            return {"node_id": node_id, "tool": tool, "arguments": arguments, "timeout": timeout, "dev": dev}

    ctx = SimpleNamespace(
        skills_repo=None,
        sql=None,
        git=None,
        paths=None,
        caps=None,
        settings=None,
        bus=None,
        config=SimpleNamespace(role="hub", node_id="hub-1", token="hub-token"),
    )

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module, "get_directory", lambda: _FakeDirectory())
    monkeypatch.setattr(tool_bridge_module, "get_hub_link_manager", lambda: _FakeLinkManager())

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="subnet_env:get_snapshot",
                arguments={"webspace_id": "desktop", "target_node_id": "member-1"},
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert result["result"]["node_id"] == "member-1"
    assert result["result"]["timeout"] == 8.0
    assert result["trace_id"] == "trace-123"
    assert ("rpc", "member-1") in calls


def test_call_tool_keeps_browsers_skill_local_on_hub(monkeypatch) -> None:
    calls: list[tuple[str, str] | tuple[str, str, dict[str, object]]] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(("run_tool", f"{skill_name}:{tool_name}", payload))
            return {"skill": skill_name, "tool": tool_name, "payload": payload, "timeout": timeout}

    class _FakeDirectory:
        def get_node_base_url(self, node_id: str) -> str | None:
            calls.append(("base_url", node_id))
            return "https://member.example"

    class _FakeLinkManager:
        def is_connected(self, node_id: str) -> bool:
            calls.append(("is_connected", node_id))
            return True

        async def rpc_tools_call(self, node_id: str, *, tool: str, arguments: dict[str, object], timeout=None, dev=False):
            calls.append(("rpc", node_id))
            raise AssertionError("browsers_skill should stay local on the hub")

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append(("run_sync", "local"))
        return func(*args, **kwargs)

    ctx = SimpleNamespace(
        skills_repo=None,
        sql=None,
        git=None,
        paths=None,
        caps=None,
        settings=None,
        bus=None,
        config=SimpleNamespace(role="hub", node_id="hub-1", token="hub-token"),
    )

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)
    monkeypatch.setattr(tool_bridge_module, "get_directory", lambda: _FakeDirectory())
    monkeypatch.setattr(tool_bridge_module, "get_hub_link_manager", lambda: _FakeLinkManager())

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="browsers_skill:rename_link",
                arguments={
                    "name": "Kitchen display",
                    "node_id": "member-1",
                    "target_node_id": "member-1",
                    "webspace_id": "desktop",
                },
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert result["trace_id"] == "trace-123"
    assert ("run_sync", "local") in calls
    assert ("base_url", "member-1") not in calls
    assert ("rpc", "member-1") not in calls


def test_call_tool_returns_degraded_snapshot_when_loopback_member_rpc_fails(monkeypatch) -> None:
    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, *_args, **_kwargs):
            raise AssertionError("local tool execution should be bypassed for explicit target nodes")

    class _FakeDirectory:
        def get_node_base_url(self, node_id: str) -> str | None:
            assert node_id == "member-1"
            return "http://127.0.0.1:8779"

    class _FakeLinkManager:
        def is_connected(self, node_id: str) -> bool:
            assert node_id == "member-1"
            return True

        async def rpc_tools_call(self, *_args, **_kwargs):
            raise RuntimeError("remote tool execution failed")

    ctx = SimpleNamespace(
        skills_repo=None,
        sql=None,
        git=None,
        paths=None,
        caps=None,
        settings=None,
        bus=None,
        config=SimpleNamespace(role="hub", node_id="hub-1", token="hub-token"),
    )

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module, "get_directory", lambda: _FakeDirectory())
    monkeypatch.setattr(tool_bridge_module, "get_hub_link_manager", lambda: _FakeLinkManager())
    tool_bridge_module._SNAPSHOT_UNAVAILABLE_CACHE.clear()

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="subnet_env:get_snapshot",
                arguments={"webspace_id": "desktop", "target_node_id": "member-1"},
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert result["degraded"] is True
    assert result["result"]["error"] == "target_member_unavailable"
    assert "member link rpc failed" in result["result"]["reason"]


def test_call_tool_uses_cached_snapshot_unavailable_before_connected_rpc(monkeypatch) -> None:
    rpc_calls = 0

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, *_args, **_kwargs):
            raise AssertionError("local tool execution should be bypassed for explicit target nodes")

    class _FakeDirectory:
        def get_node_base_url(self, _node_id: str) -> str | None:
            return "http://127.0.0.1:8779"

    class _FakeLinkManager:
        def is_connected(self, _node_id: str) -> bool:
            return True

        async def rpc_tools_call(self, *_args, **_kwargs):
            nonlocal rpc_calls
            rpc_calls += 1
            raise TimeoutError("slow member")

    ctx = SimpleNamespace(
        skills_repo=None,
        sql=None,
        git=None,
        paths=None,
        caps=None,
        settings=None,
        bus=None,
        config=SimpleNamespace(role="hub", node_id="hub-1", token="hub-token"),
    )

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module, "get_directory", lambda: _FakeDirectory())
    monkeypatch.setattr(tool_bridge_module, "get_hub_link_manager", lambda: _FakeLinkManager())
    tool_bridge_module._SNAPSHOT_UNAVAILABLE_CACHE.clear()

    body = tool_bridge_module.ToolCall(
        tool="subnet_env:get_snapshot",
        arguments={"webspace_id": "desktop", "target_node_id": "member-1"},
    )
    first = asyncio.run(tool_bridge_module.call_tool(body, SimpleNamespace(headers={}), Response(), ctx=ctx))
    second = asyncio.run(tool_bridge_module.call_tool(body, SimpleNamespace(headers={}), Response(), ctx=ctx))

    assert first["ok"] is True
    assert first["degraded"] is True
    assert second["ok"] is True
    assert second["result"]["cached"] is True
    assert rpc_calls == 1
