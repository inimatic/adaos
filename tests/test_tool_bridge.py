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


@pytest.fixture(autouse=True)
def _reset_tool_bridge_runtime_guards() -> None:
    if hasattr(tool_bridge_module, "_WORKSPACE_RUNTIME_LAST_SYNC_AT"):
        tool_bridge_module._WORKSPACE_RUNTIME_LAST_SYNC_AT.clear()
    if hasattr(tool_bridge_module, "_WORKSPACE_RUNTIME_LOCKS"):
        tool_bridge_module._WORKSPACE_RUNTIME_LOCKS.clear()
    yield
    if hasattr(tool_bridge_module, "_WORKSPACE_RUNTIME_LAST_SYNC_AT"):
        tool_bridge_module._WORKSPACE_RUNTIME_LAST_SYNC_AT.clear()
    if hasattr(tool_bridge_module, "_WORKSPACE_RUNTIME_LOCKS"):
        tool_bridge_module._WORKSPACE_RUNTIME_LOCKS.clear()


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


def _patch_runtime_approval_pending_actions(monkeypatch) -> list[dict[str, object]]:
    published: list[dict[str, object]] = []

    def _list_pending_actions(*, webspace_id: str | None = None, include_terminal: bool = True) -> dict[str, object]:
        return {"by_id": {}, "active_items": [], "active": []}

    def _publish_pending_action(**kwargs) -> dict[str, object]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:  # pragma: no cover - assertion path for sync-in-event-loop regressions
            raise AssertionError("runtime pending action publish must be offloaded from the event loop")
        published.append(dict(kwargs))
        return {
            "id": kwargs.get("action_id") or "pa.runtime.test",
            "kind": kwargs.get("kind"),
            "status": "pending",
            "webspace_id": kwargs.get("webspace_id"),
            "domain_ref": kwargs.get("domain_ref"),
        }

    monkeypatch.setattr(tool_bridge_module, "list_pending_actions", _list_pending_actions)
    monkeypatch.setattr(tool_bridge_module, "publish_pending_action", _publish_pending_action)
    return published


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


def test_builder_chat_routes_to_active_local_automation(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _Automation:
        @classmethod
        def from_context(cls):
            return cls()

        def find_active_session(self, *, webspace_id):
            assert webspace_id == "prompt-dev"
            return {"object_type": "scenario", "object_id": "recipes"}

        def submit_turn(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "handled": True, "status": "automation_queued"}

    import adaos.services.builder.automation as automation_module

    monkeypatch.setattr(automation_module, "BuilderAutomationService", _Automation)

    result = asyncio.run(
        tool_bridge_module._route_builder_automation_chat(
            tool_name="builder_skill:chat",
            payload={"text": "Add a favorites filter"},
            webspace_id="prompt-dev",
        )
    )

    assert result["status"] == "automation_queued"
    assert calls == [
        {
            "text": "Add a favorites filter",
            "object_type": "scenario",
            "object_id": "recipes",
            "webspace_id": "prompt-dev",
        }
    ]


def test_builder_chat_delegates_active_automation_to_runtime_skill(monkeypatch) -> None:
    class _Automation:
        @classmethod
        def from_context(cls):
            return cls()

        def find_active_session(self, *, webspace_id):
            assert webspace_id == "prompt-dev"
            return {"object_type": "scenario", "object_id": "recipes"}

    class _Manager:
        def run_tool(self, skill, tool, payload):
            assert (skill, tool) == ("builder_automation_skill", "chat")
            assert payload["object_type"] == "scenario"
            assert payload["object_id"] == "recipes"
            assert payload["webspace_id"] == "prompt-dev"
            return {
                "ok": True,
                "handled": True,
                "status": "automation_queued",
                "message": "Iteration queued.",
            }

    import adaos.services.builder.automation as automation_module

    monkeypatch.setattr(automation_module, "BuilderAutomationService", _Automation)

    result = asyncio.run(
        tool_bridge_module._route_builder_automation_chat(
            tool_name="builder_skill:chat",
            payload={"text": "Add a favorites filter"},
            webspace_id="prompt-dev",
            manager=_Manager(),
        )
    )

    assert result["status"] == "automation_queued"
    assert result["message"] == "Iteration queued."


def test_call_tool_blocks_high_risk_runtime_action_without_approval(monkeypatch) -> None:
    published = _patch_runtime_approval_pending_actions(monkeypatch)

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, *_args, **_kwargs):
            raise AssertionError("high-risk tool must be blocked before execution")

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            tool_bridge_module.call_tool(
                tool_bridge_module.ToolCall(
                    tool="files_skill:delete_file",
                    arguments={"path": "C:/private/report.txt", "side_effect_class": "filesystem"},
                ),
                SimpleNamespace(headers={}),
                Response(),
                ctx=_fake_ctx(),
            )
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error"] == "action_approval_required"
    assert excinfo.value.detail["action_risk"]["risk_class"] == "filesystem"
    assert excinfo.value.detail["pending_action_id"] == published[0]["action_id"]
    assert published[0]["kind"] == "runtime.action_approval"
    assert published[0]["allowed_actions"] == ["approve", "refuse", "postpone"]
    assert published[0]["domain_ref"]["tool"] == "files_skill:delete_file"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("slideshow_skill:control_redevice_slideshow", {"action": "start"}),
        ("browsers_skill:identify_device", {"device_ref": "browser:dev-phone"}),
    ],
)
def test_call_tool_allows_operator_ui_device_control_without_pending_action(
    monkeypatch,
    tool: str,
    arguments: dict[str, object],
) -> None:
    calls: list[str] = []
    skill_name, public_tool = tool.split(":", 1)

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"{skill_name}:{tool_name}")
            return {"ok": True, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync")
        return func(*args, **kwargs)

    def _publish_pending_action(**_kwargs):
        raise AssertionError("operator UI device controls should not create a pending action")

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)
    monkeypatch.setattr(tool_bridge_module, "publish_pending_action", _publish_pending_action)

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool=tool,
                arguments={
                    **arguments,
                    "_meta": {
                        "action_source": "operator_ui",
                        "action_context": {
                            "widgetId": "slideshow-main-actions",
                            "widgetType": "input.commandBar",
                            "eventId": "play",
                        },
                    },
                },
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=_fake_ctx(),
        )
    )

    assert result["ok"] is True
    assert result["trace_id"] == "trace-123"
    assert calls == ["run_sync", f"{skill_name}:{public_tool}"]


def test_runtime_action_risk_ignores_local_write_freeform_content() -> None:
    body = tool_bridge_module.ToolCall(
        tool="notebook_skill:save_note",
        arguments={
            "note_id": "note-1",
            "content": "Title\nBody mentioning subnet should stay user text.",
            "source": "editor_change",
            "side_effect_class": "local_write",
            "webspace_id": "desktop-dev",
        },
    )

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name="notebook_skill",
        public_tool="save_note",
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "local_write"
    assert risk["approval_required"] is False


def test_runtime_action_risk_allows_builder_email_field_request() -> None:
    body = tool_bridge_module.ToolCall(
        tool="builder_skill:update_current_scenario",
        arguments={
            "instruction": "Добавь поле email для связи.",
            "webspace_id": "desktop",
            "auto_apply": True,
        },
    )

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name="builder_skill",
        public_tool="update_current_scenario",
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "local_write"
    assert risk["approval_required"] is False


def test_runtime_action_risk_allows_notebook_upload_attachment_paths() -> None:
    body = tool_bridge_module.ToolCall(
        tool="notebook_skill:attach_note_upload",
        arguments={
            "note_id": "note-1",
            "kind": "photo",
            "side_effect_class": "local_write",
            "artifact_ref": {
                "id": "skill_file:notebook_skill:photos:abcdef0123456789",
                "artifact_id": "skill_file:notebook_skill:photos:abcdef0123456789",
                "kind": "skill_file",
                "skill": "notebook_skill",
                "purpose": "photos",
                "name": "photo.jpg",
                "relative_path": "uploads/photos/photo.jpg",
                "path": r"D:\git\inimatic\adaos\.adaos\workspace\skills\.runtime\notebook_skill\v0.1\data\files\uploads\photos\photo.jpg",
                "local_path": r"D:\git\inimatic\adaos\.adaos\workspace\skills\.runtime\notebook_skill\v0.1\data\files\uploads\photos\photo.jpg",
                "stored_path": r"D:\git\inimatic\adaos\.adaos\workspace\skills\.runtime\notebook_skill\v0.1\data\files\uploads\photos\photo.jpg",
                "uri": "file:///D:/git/inimatic/adaos/.adaos/workspace/skills/.runtime/notebook_skill/v0.1/data/files/uploads/photos/photo.jpg",
                "size_bytes": 5606,
                "sha256": "abcdef0123456789",
                "mime": "image/jpeg",
            },
            "upload": {
                "name": "photo.jpg",
                "mime": "image/jpeg",
                "size_bytes": 5606,
                "sha256": "abcdef0123456789",
                "purpose": "photos",
            },
        },
    )

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name="notebook_skill",
        public_tool="attach_note_upload",
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "local_write"
    assert risk["approval_required"] is False


def test_call_tool_allows_notebook_upload_attachment_without_approval(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"{skill_name}:{tool_name}")
            return {"ok": True, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync")
        return func(*args, **kwargs)

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)
    monkeypatch.setattr(tool_bridge_module, "publish_pending_action", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("notebook uploads must not require approval")))

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="notebook_skill:attach_note_upload",
                arguments={
                    "note_id": "note-1",
                    "kind": "photo",
                    "side_effect_class": "local_write",
                    "artifact_ref": {
                        "artifact_id": "skill_file:notebook_skill:photos:abcdef0123456789",
                        "relative_path": "uploads/photos/photo.jpg",
                        "path": r"D:\git\inimatic\adaos\.adaos\workspace\skills\.runtime\notebook_skill\v0.1\data\files\uploads\photos\photo.jpg",
                    },
                    "upload": {
                        "name": "photo.jpg",
                        "mime": "image/jpeg",
                        "size_bytes": 5606,
                        "sha256": "abcdef0123456789",
                        "purpose": "photos",
                    },
                },
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=_fake_ctx(),
        )
    )

    assert result["ok"] is True
    assert calls == ["run_sync", "notebook_skill:attach_note_upload"]


def test_runtime_action_risk_allows_prompt_project_file_save_with_markdown_memory() -> None:
    body = tool_bridge_module.ToolCall(
        tool="prompt_engineer_skill:prompt_save_project_file",
        arguments={
            "object_type": "scenario",
            "object_id": "todo_list_5b9319fa",
            "path": "tz/base_tz.md",
            "text": (
                "# To-Do List\n\n"
                "No external network, device-control, or credential access is requested.\n"
                "Validation and human review are still required before activation.\n"
            ),
        },
    )

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name="prompt_engineer_skill",
        public_tool="prompt_save_project_file",
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "local_write"
    assert risk["approval_required"] is False


def test_runtime_action_risk_allows_cv_descriptor_capture_payload() -> None:
    body = tool_bridge_module.ToolCall(
        tool="cv_descriptor:cv_descriptor_save_descriptor",
        arguments={
            "vector": [0.1, 0.2, 0.3],
            "thumbnail": "data:image/jpeg;base64,/9j/test",
            "title": "Object 1",
            "description": "Captured from the phone camera",
            "model_signature": "tfjs-mobilenet-v2",
            "metadata": {
                "session_id": "cv_descriptor.setup",
                "mode": "setup",
                "camera": "phone",
            },
        },
    )

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name="cv_descriptor",
        public_tool="cv_descriptor_save_descriptor",
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "local_write"
    assert risk["approval_required"] is False


def test_runtime_action_risk_allows_slideshow_redevice_refresh_tick() -> None:
    body = tool_bridge_module.ToolCall(
        tool="slideshow_skill:refresh_redevice_slideshow_state",
        arguments={"code": "", "webspace_id": "desktop"},
    )

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name="slideshow_skill",
        public_tool="refresh_redevice_slideshow_state",
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "local_write"
    assert risk["approval_required"] is False


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("slideshow_skill:select_redevice_endpoint", {"code": "TV-1"}),
        ("redevice_settings:refresh_redevice_settings_state", {"webspace_id": "desktop"}),
        ("browsers_skill:select_browser", {"browser_device_id": "dev_phone"}),
        ("browsers_skill:rename_selected_browser", {"name": "Chrome"}),
        ("browsers_skill:rename_device", {"device_id": "dev_phone", "name": "Chrome"}),
        ("browsers_skill:rename_browser_device_name", {"browser_device_id": "dev_phone", "name": "My phone"}),
    ],
)
def test_runtime_action_risk_allows_local_ui_state_tools(tool: str, arguments: dict[str, object]) -> None:
    public_tool = tool.split(":", 1)[1]
    body = tool_bridge_module.ToolCall(tool=tool, arguments=arguments)

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name=tool.split(":", 1)[0],
        public_tool=public_tool,
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "local_write"
    assert risk["approval_required"] is False


def test_runtime_action_risk_treats_prompt_read_tools_as_readonly() -> None:
    body = tool_bridge_module.ToolCall(
        tool="prompt_engineer_skill:prompt_read_project_file",
        arguments={
            "object_type": "scenario",
            "object_id": "todo_list_5b9319fa",
            "path": "scenario.json",
        },
    )

    risk = tool_bridge_module._runtime_action_risk(
        body=body,
        skill_name="prompt_engineer_skill",
        public_tool="prompt_read_project_file",
        payload=dict(body.arguments or {}),
        local_node_id="hub-1",
    )

    assert risk["risk_class"] == "safe"
    assert risk["approval_required"] is False


def test_call_tool_allows_high_risk_runtime_action_with_approval(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"{skill_name}:{tool_name}")
            return {"ok": True, "payload": payload}

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
            tool_bridge_module.ToolCall(
                tool="files_skill:write_file",
                arguments={
                    "path": "C:/private/report.txt",
                    "text": "approved",
                    "_meta": {
                        "action_approval": {
                            "status": "approve",
                            "pending_action_id": "pa.runtime.fs",
                            "approved_by": "user:owner",
                            "risk_class": "filesystem",
                        }
                    },
                },
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=_fake_ctx(),
        )
    )

    assert result["ok"] is True
    assert calls == ["run_sync", "files_skill:write_file"]


def test_call_tool_allows_approved_runtime_pending_action_retry(monkeypatch) -> None:
    calls: list[str] = []
    pending_by_id: dict[str, dict[str, object]] = {}

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"{skill_name}:{tool_name}")
            return {"ok": True, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync")
        return func(*args, **kwargs)

    def _list_pending_actions(*, webspace_id: str | None = None, include_terminal: bool = True) -> dict[str, object]:
        return {"by_id": pending_by_id, "active_items": [], "active": []}

    def _publish_pending_action(**kwargs) -> dict[str, object]:
        action = {
            "id": kwargs.get("action_id") or "pa.runtime.test",
            "kind": kwargs.get("kind"),
            "status": "pending",
            "webspace_id": kwargs.get("webspace_id"),
            "domain_ref": kwargs.get("domain_ref"),
        }
        pending_by_id[str(action["id"])] = action
        return action

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)
    monkeypatch.setattr(tool_bridge_module, "list_pending_actions", _list_pending_actions)
    monkeypatch.setattr(tool_bridge_module, "publish_pending_action", _publish_pending_action)

    body = tool_bridge_module.ToolCall(
        tool="files_skill:write_file",
        arguments={"path": "C:/private/report.txt", "text": "approved", "side_effect_class": "filesystem"},
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(tool_bridge_module.call_tool(body, SimpleNamespace(headers={}), Response(), ctx=_fake_ctx()))

    pending_id = excinfo.value.detail["pending_action_id"]
    pending_by_id[pending_id]["status"] = "responded"
    pending_by_id[pending_id]["response"] = {
        "response_action_id": "approve",
        "responder": {"type": "user", "user_id": "owner"},
    }

    result = asyncio.run(tool_bridge_module.call_tool(body, SimpleNamespace(headers={}), Response(), ctx=_fake_ctx()))

    assert result["ok"] is True
    assert calls[-1] == "files_skill:write_file"
    assert calls.count("files_skill:write_file") == 1


def test_call_tool_keeps_prompt_project_selection_local_and_approval_free(monkeypatch) -> None:
    calls: list[str] = []
    payloads: list[dict[str, object]] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"{skill_name}:{tool_name}")
            payloads.append(payload)
            return {"ok": True, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync")
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
    monkeypatch.setattr(tool_bridge_module, "publish_pending_action", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("selection must not require approval")))

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="prompt_engineer_skill:prompt_select_project",
                arguments={
                    "object_type": "scenario",
                    "object_id": "shopping_list_222d3f0c",
                    "node_id": "member-1",
                    "target_node_id": "member-1",
                },
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert calls == ["run_sync", "prompt_engineer_skill:prompt_select_project"]
    assert payloads[0]["_meta"]["action_source"] == "api_tool_call"
    assert payloads[0]["_meta"]["origin_label"] == "API"
    assert payloads[0]["_meta"]["tool"] == "prompt_engineer_skill:prompt_select_project"


def test_call_tool_allows_prompt_project_file_save_without_approval(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"{skill_name}:{tool_name}")
            return {"ok": True, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync")
        return func(*args, **kwargs)

    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)
    monkeypatch.setattr(tool_bridge_module, "publish_pending_action", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("file save must not require approval")))

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="prompt_engineer_skill:prompt_save_project_file",
                arguments={
                    "object_type": "scenario",
                    "object_id": "todo_list_5b9319fa",
                    "path": "tz/base_tz.md",
                    "text": "# To-Do List\nNo external network, device-control, or credential access is requested.",
                },
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=_fake_ctx(),
        )
    )

    assert result["ok"] is True
    assert calls == ["run_sync", "prompt_engineer_skill:prompt_save_project_file"]


def test_call_tool_blocks_cross_node_mutation_without_approval(monkeypatch) -> None:
    published = _patch_runtime_approval_pending_actions(monkeypatch)

    class _FakeSkillManager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, *_args, **_kwargs):
            raise AssertionError("explicit target node mutation must not run locally")

    class _FakeDirectory:
        def get_node_base_url(self, _node_id: str) -> str | None:
            raise AssertionError("cross-node mutation must be blocked before proxy lookup")

    class _FakeLinkManager:
        def is_connected(self, _node_id: str) -> bool:
            raise AssertionError("cross-node mutation must be blocked before RPC")

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

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            tool_bridge_module.call_tool(
                tool_bridge_module.ToolCall(
                    tool="member_control:restart_service",
                    arguments={"target_node_id": "member-1", "service": "camera"},
                ),
                SimpleNamespace(headers={}),
                Response(),
                ctx=ctx,
            )
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["action_risk"]["risk_class"] == "cross_node"
    assert excinfo.value.detail["pending_action_id"] == published[0]["action_id"]
    assert published[0]["domain_ref"]["tool"] == "member_control:restart_service"


def test_call_tool_skips_workspace_autosync_for_readonly_snapshots(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    (tmp_path / "workspace" / "skills" / "infrastate_skill").mkdir(parents=True, exist_ok=True)

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
            return None

        def runtime_status(self, _name: str) -> dict[str, object]:
            return {"ready": True}

        def runtime_update(self, name: str, *, space: str = "workspace") -> dict[str, object]:
            calls.append(f"update:{name}:{space}")
            return {"ok": True}

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"run:{skill_name}:{tool_name}")
            return {"skill": skill_name, "tool": tool_name, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync")
        return func(*args, **kwargs)

    monkeypatch.setenv("ADAOS_LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(tool="infrastate_skill:get_snapshot", arguments={"webspace_id": "desktop"}),
            SimpleNamespace(headers={}),
            Response(),
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert calls == ["run_sync", "run:infrastate_skill:get_snapshot"]


def test_call_tool_runs_workspace_autosync_inside_worker(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    (tmp_path / "workspace" / "skills" / "prompt_engineer_skill").mkdir(parents=True, exist_ok=True)

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
            manifest = tmp_path / "runtime" / "slots" / "A" / "resolved.manifest.json"
            runtime_root = manifest.parent / "src" / "skills" / "prompt_engineer_skill"
            runtime_root.mkdir(parents=True, exist_ok=True)
            (runtime_root / "__init__.py").write_text("", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            self.manifest = manifest
            return None

        def runtime_status(self, _name: str) -> dict[str, object]:
            calls.append("runtime_status")
            return {"ready": True, "resolved_manifest": str(self.manifest)}

        def runtime_update(self, name: str, *, space: str = "workspace") -> dict[str, object]:
            calls.append(f"update:{name}:{space}")
            return {"ok": True}

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"run:{skill_name}:{tool_name}")
            return {"skill": skill_name, "tool": tool_name, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        calls.append("run_sync:start")
        result = func(*args, **kwargs)
        calls.append("run_sync:end")
        return result

    monkeypatch.setenv("ADAOS_LOG_LEVEL", "DEBUG")
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
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert calls == [
        "run_sync:start",
        "runtime_status",
        "update:prompt_engineer_skill:workspace",
        "run:prompt_engineer_skill:prompt_list_project_objects",
        "run_sync:end",
    ]


def test_call_tool_throttles_workspace_autosync_for_repeated_skill_calls(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    (tmp_path / "workspace" / "skills" / "prompt_engineer_skill").mkdir(parents=True, exist_ok=True)

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
            manifest = tmp_path / "runtime" / "slots" / "A" / "resolved.manifest.json"
            runtime_root = manifest.parent / "src" / "skills" / "prompt_engineer_skill"
            runtime_root.mkdir(parents=True, exist_ok=True)
            (runtime_root / "__init__.py").write_text("", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            self.manifest = manifest

        def runtime_status(self, _name: str) -> dict[str, object]:
            calls.append("runtime_status")
            return {"ready": True, "resolved_manifest": str(self.manifest)}

        def runtime_update(self, name: str, *, space: str = "workspace") -> dict[str, object]:
            calls.append(f"update:{name}:{space}")
            return {"ok": True}

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"run:{skill_name}:{tool_name}")
            return {"skill": skill_name, "tool": tool_name, "payload": payload}

    async def _fake_run_sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setenv("ADAOS_LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)

    body = tool_bridge_module.ToolCall(tool="prompt_engineer_skill:prompt_list_project_files", arguments={})
    first = asyncio.run(tool_bridge_module.call_tool(body, SimpleNamespace(headers={}), Response(), ctx=ctx))
    second = asyncio.run(tool_bridge_module.call_tool(body, SimpleNamespace(headers={}), Response(), ctx=ctx))

    assert first["ok"] is True
    assert second["ok"] is True
    assert calls.count("update:prompt_engineer_skill:workspace") == 1
    assert calls.count("run:prompt_engineer_skill:prompt_list_project_files") == 2


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


def test_call_tool_repairs_broken_ready_workspace_runtime_to_pending_slot(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    (tmp_path / "workspace" / "skills" / "prompt_engineer_skill").mkdir(parents=True, exist_ok=True)
    broken_manifest = tmp_path / "runtime" / "slots" / "B" / "resolved.manifest.json"
    broken_manifest.parent.mkdir(parents=True)
    broken_manifest.write_text("{}", encoding="utf-8")

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
            if self.ready:
                ready_manifest = tmp_path / "runtime" / "slots" / "A" / "resolved.manifest.json"
                (ready_manifest.parent / "src" / "skills" / "prompt_engineer_skill").mkdir(parents=True)
                ready_manifest.write_text("{}", encoding="utf-8")
                return {"ready": True, "resolved_manifest": str(ready_manifest)}
            return {
                "ready": True,
                "resolved_manifest": str(broken_manifest),
                "pending_version": "0.6.3",
                "pending_slot": "A",
            }

        def runtime_update(self, name: str, *, space: str = "workspace") -> dict[str, object]:
            calls.append(f"update:{name}:{space}")
            return {"ok": False, "reason": "runtime_src_missing", "path": str(broken_manifest.parent / "src" / "skills" / name)}

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
            return str(slot)

        def run_tool(self, skill_name: str, tool_name: str, payload: dict[str, object], timeout: float | None = None) -> dict[str, object]:
            calls.append(f"run:{self.ready}:{skill_name}:{tool_name}")
            if not self.ready:
                raise RuntimeError("tool unavailable")
            return {"ok": True}

    async def _fake_run_sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setenv("ADAOS_LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(tool_bridge_module, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(tool_bridge_module, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(tool_bridge_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_bridge_module, "attach_http_trace_headers", lambda _req, _resp: "trace-123")
    monkeypatch.setattr(tool_bridge_module.anyio.to_thread, "run_sync", _fake_run_sync)
    monkeypatch.setattr(tool_bridge_module, "default_webspace_id", lambda: "default")

    result = asyncio.run(
        tool_bridge_module.call_tool(
            tool_bridge_module.ToolCall(
                tool="prompt_engineer_skill:prompt_list_project_objects",
                arguments={"webspace_id": "desktop"},
            ),
            SimpleNamespace(headers={}),
            Response(),
            ctx=ctx,
        )
    )

    assert result["ok"] is True
    assert calls == [
        "run:False:prompt_engineer_skill:prompt_list_project_objects",
        "update:prompt_engineer_skill:workspace",
        "activate:prompt_engineer_skill:default:desktop:0.6.3:A",
        "run:True:prompt_engineer_skill:prompt_list_project_objects",
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


@pytest.mark.parametrize(
    "tool_name",
    [
        "browsers_skill:rename_link",
        "infra_access_skill:get_snapshot",
        "infrastate_skill:get_snapshot",
    ],
)
def test_call_tool_keeps_hub_projection_tools_local_on_hub(monkeypatch, tool_name: str) -> None:
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
            raise AssertionError("hub projection tools should stay local on the hub")

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
                tool=tool_name,
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
