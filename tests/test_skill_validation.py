from __future__ import annotations

from pathlib import Path

from adaos.services.agent_context import get_ctx
from adaos.services.skill.validation import SkillValidationService, validate_webui_file_contract


def _write_skill(
    root: Path,
    *,
    handler: str,
    extra_files: dict[str, str] | None = None,
    manifest_extra: list[str] | None = None,
) -> Path:
    skill_dir = root / "demo_skill"
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: demo_skill",
                "version: 0.1.0",
                "description: test skill",
                "tools:",
                "  - name: ping",
                "    entry: handlers.main:ping",
                "    input_schema: {}",
                "",
                *(manifest_extra or []),
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "handlers" / "main.py").write_text(handler, encoding="utf-8")
    for rel, text in (extra_files or {}).items():
        path = skill_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return skill_dir


def test_validation_accepts_flat_runtime_capabilities(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=["capabilities:", "  - storage.relational"],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert "schema.invalid" not in {issue.code for issue in report.issues}


def test_dynamic_validation_registers_handler_module_for_dataclasses(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from dataclasses import dataclass
from adaos.sdk.core.decorators import tool

@dataclass(frozen=True)
class Reply:
    ok: bool = True

@tool(summary="ping")
def ping():
    return {"ok": Reply().ok}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    assert "import.failed" not in {issue.code for issue in report.issues}


def test_validation_rejects_capability_mapping_not_consumed_by_admission(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=["capabilities:", "  requires:", "    - storage.relational"],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert report.ok is False
    assert "schema.invalid" in {issue.code for issue in report.issues}


def test_strict_validation_predicts_heavy_dependency_isolation_failure(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=["dependencies:", "  - torch>=2.2.0"],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    assert report.ok is False
    assert "runtime.dependencies.heavy_isolation" in {issue.code for issue in report.issues}


def test_strict_validation_accepts_explicit_heavy_dependency_boundary(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=[
            "runtime:",
            "  env:",
            "    mode: shared",
            "    allow_heavy_dependencies: true",
            "dependencies:",
            "  - torch>=2.2.0",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    assert "runtime.dependencies.heavy_isolation" not in {issue.code for issue in report.issues}


def test_strict_validation_rejects_undeclared_heavy_runtime_import(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
import torch
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": bool(torch.__version__)}
""",
        manifest_extra=[
            "runtime:",
            "  env:",
            "    mode: shared",
            "    allow_heavy_dependencies: true",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    assert report.ok is False
    assert "runtime.dependencies.heavy_undeclared" in {issue.code for issue in report.issues}


def test_strict_validation_reports_all_heavy_import_contract_failures(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
import torch
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": bool(torch.__version__)}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    codes = {issue.code for issue in report.issues}
    assert "runtime.dependencies.heavy_undeclared" in codes
    assert "runtime.dependencies.heavy_isolation" in codes


def test_validation_rejects_root_module_that_shadows_stdlib(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        extra_files={"operator.py": "DOMAIN_OPERATOR = True\n"},
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    issue = next(item for item in report.issues if item.code == "runtime.python.stdlib_shadowing")
    assert report.ok is False
    assert issue.where == "operator.py"


def test_validation_allows_nested_module_named_like_stdlib(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        extra_files={"domain/operator.py": "DOMAIN_OPERATOR = True\n"},
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert "runtime.python.stdlib_shadowing" not in {issue.code for issue in report.issues}


def test_validation_rejects_unexported_provider_contract_operation(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=[
            "provider_contracts:",
            "  - contract: example.runner.v1",
            "    capability: example.runner",
            "    operations: [ping, collect_attempt]",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)

    assert report.ok is False
    assert "provider_contracts.operations_unexported" in {issue.code for issue in report.issues}


def test_skill_validation_blocks_conversation_storage_antipatterns(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

history = []

def touch_doc(doc):
    return encode_state_as_update(doc)

@tool(summary="ping")
def ping():
    return {"ok": True, "source": "voice_chat.messages"}
""",
        extra_files={"chat_history.json": "[]"},
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)

    codes = {issue.code for issue in report.issues}
    assert report.ok is False
    assert {
        "conversation.unsafe_direct_yjs",
        "conversation.unbounded_process_memory",
        "conversation.transport_owned_memory",
        "conversation.raw_transcript_file",
    }.issubset(codes)
    assert all(issue.level == "error" for issue in report.issues if issue.code.startswith("conversation."))


def test_skill_validation_allows_bounded_conversation_sdk_usage(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from collections import deque

from adaos.sdk import conversation, memory
from adaos.sdk.core.decorators import tool

event_buffer = deque(maxlen=50)

@tool(summary="ping")
def ping():
    event_buffer.append("ping")
    return {"ok": True, "context_schema": conversation.context.__name__, "memory": memory.write_policy.__name__}
""",
        manifest_extra=[
            "conversation:",
            "  dialog_channel:",
            "    id: demo",
            "    owner: skill:demo_skill",
            "    default_tool: demo_skill.ping",
            "  memory:",
            "    scopes: [skill_user]",
            "data_routes:",
            "  - surface: demo memory",
            "    route: skill-local",
            "    owner: skill:demo_skill",
            "    path: skill_memory:demo_skill.memory",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)

    conversation_codes = {issue.code for issue in report.issues if issue.code.startswith("conversation.")}
    assert report.ok is True
    assert conversation_codes == set()


def test_skill_validation_admits_declared_conversational_package(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=[
            "conversational:",
            "  manifest: conversational/manifest.yaml",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert report.ok is False
    assert "conversational.manifest.missing" in {issue.code for issue in report.issues}


def test_skill_validation_enforces_opt_in_sdk_only_imports(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
from adaos.services.builder.workbench import BuilderWorkbenchService

@tool(summary="ping")
def ping():
    return {"ok": True, "service": BuilderWorkbenchService.__name__}
""",
        manifest_extra=[
            "runtime:",
            "  python: '3.11'",
            "  sdk_only: true",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    sdk_issues = [issue for issue in report.issues if issue.code == "runtime.sdk_only_import"]
    assert report.ok is False
    assert len(sdk_issues) == 1
    assert "adaos.services.builder.workbench" in sdk_issues[0].message
    assert sdk_issues[0].where == "handlers/main.py"


def test_skill_validation_allows_sdk_only_skill_to_use_sdk(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.builder import preview
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True, "operation": preview.get_binding.__name__}
""",
        manifest_extra=[
            "runtime:",
            "  python: '3.11'",
            "  sdk_only: true",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert report.ok is True
    assert not any(issue.code == "runtime.sdk_only_import" for issue in report.issues)


def test_skill_validation_warns_on_direct_write_capable_yjs_access(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
from adaos.services.yjs.doc import async_get_ydoc as open_doc

@tool(summary="ping")
async def ping():
    async with open_doc("desktop") as ydoc:
        ydoc.get_map("data")
    return {"ok": True}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    issues = [issue for issue in report.issues if issue.code == "projection.direct_yjs_write"]
    assert report.ok is True
    assert len(issues) == 1
    assert issues[0].level == "warning"
    assert "async_get_ydoc" in issues[0].message
    assert issues[0].where == "handlers/main.py:7"


def test_skill_validation_allows_projection_sdk_and_read_only_yjs_access(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool
from adaos.sdk.web.yjs import webspace_read_ydoc
from adaos.services.yjs.doc import async_read_ydoc

@tool(summary="ping")
async def ping():
    async with webspace_read_ydoc("desktop"):
        pass
    async with async_read_ydoc("desktop"):
        pass
    return {"ok": True}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert not any(issue.code == "projection.direct_yjs_write" for issue in report.issues)


def test_skill_validation_warns_on_blocking_call_in_async_subscription(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
import time as clock

from adaos.sdk.core.decorators import subscribe, tool

@tool(summary="ping")
def ping():
    return {"ok": True}

@subscribe("demo.changed")
async def on_demo_changed(evt):
    clock.sleep(2)
""",
        manifest_extra=[
            "events:",
            "  subscribe: [demo.changed]",
            "  publish: []",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)
    issues = [issue for issue in report.issues if issue.code == "runtime.async_subscription_blocking_call"]

    assert report.ok is True
    assert len(issues) == 1
    assert issues[0].level == "warning"
    assert "time.sleep" in issues[0].message


def test_strict_skill_validation_rejects_blocking_async_subscription(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from subprocess import run as run_process

from adaos.sdk.core.decorators import subscribe, tool

@tool(summary="ping")
def ping():
    return {"ok": True}

@subscribe("demo.changed")
async def on_demo_changed(evt):
    run_process(["demo"])
""",
        manifest_extra=[
            "events:",
            "  subscribe: [demo.changed]",
            "  publish: []",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    assert report.ok is False
    assert "runtime.async_subscription_blocking_call" in {issue.code for issue in report.issues}


def test_strict_skill_validation_rejects_blocking_detached_task_through_helper(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
import asyncio
from pathlib import Path

from adaos.sdk.core.decorators import tool
from adaos.sdk.data import skill_memory_get

def read_state():
    return skill_memory_get("state", {})

def read_payload():
    return Path("payload.json").read_text(encoding="utf-8")

async def background_refresh():
    read_state()
    read_payload()

@tool(summary="ping")
def ping():
    asyncio.get_running_loop().create_task(background_refresh())
    return {"ok": True}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)
    issues = [issue for issue in report.issues if issue.code == "runtime.async_task_blocking_call"]

    assert report.ok is False
    assert len(issues) == 1
    assert "adaos.sdk.data.skill_memory_get" in issues[0].message
    assert "read_text" in issues[0].message


def test_strict_skill_validation_allows_blocking_helper_via_to_thread(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
import asyncio
from pathlib import Path

from adaos.sdk.core.decorators import tool

def read_payload():
    return Path("payload.json").read_text(encoding="utf-8")

async def background_refresh():
    return await asyncio.to_thread(read_payload)

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    assert "runtime.async_task_blocking_call" not in {issue.code for issue in report.issues}


def test_strict_skill_validation_follows_async_class_method_helpers(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from pathlib import Path

from adaos.sdk.core.decorators import tool

class BackgroundWorker:
    def read_payload(self):
        return Path("payload.json").read_text(encoding="utf-8")

    async def run(self):
        return self.read_payload()

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)
    issues = [issue for issue in report.issues if issue.code == "runtime.async_task_blocking_call"]

    assert report.ok is False
    assert len(issues) == 1
    assert "BackgroundWorker.run" in issues[0].message
    assert "read_text" in issues[0].message


def test_skill_validation_allows_declared_stream_receiver_and_bounded_state(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

_STATE_BY_KEY = {}

@tool(summary="ping")
def ping():
    return {"ok": True, "receiver": "voice_chat.messages"}
""",
        extra_files={
            "webui.json": """
{
  "webio": {
    "receivers": {
      "voice_chat.messages": {
        "mode": "replace",
        "transport": "hub",
        "snapshotPolicy": "on_subscribe",
        "budget": {
          "maxPayloadBytes": 16384,
          "maxFanout": 3,
          "maxItems": 6
        },
        "initialState": { "messages": [] }
      }
    }
  }
}
"""
        },
        manifest_extra=[
            "data_routes:",
            "  - surface: demo chat",
            "    route: stream",
            "    receiver: voice_chat.messages",
            "    budget:",
            "      max_payload_bytes: 16384",
            "      max_fanout: 3",
            "      max_items: 6",
            "memory_budget:",
            "  caches:",
            "    - name: demo.state_by_key",
            "      max_items: 32",
            "      ttl_seconds: 3600",
            "      cleanup_hook: dispose",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)

    conversation_codes = {issue.code for issue in report.issues if issue.code.startswith("conversation.")}
    assert report.ok is True
    assert conversation_codes == set()


def test_skill_validation_warns_when_conversation_sdk_lacks_manifest_policy(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk import conversation, memory
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    packet = conversation.context("conv.demo", requester_owner="skill:demo_skill")
    policy = memory.write_policy("skill_preference", owner="skill:demo_skill")
    return {"ok": True, "packet": packet, "policy": policy}
""",
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)

    codes = {issue.code for issue in report.issues}
    assert report.ok is True
    assert {"conversation.manifest_missing", "conversation.memory_policy_missing"}.issubset(codes)
    assert all(issue.level == "warning" for issue in report.issues if issue.code in codes)


def test_skill_validation_rejects_broken_webui_modal_contract(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        extra_files={
            "webui.json": """
{
  "interface": {
    "schema": "adaos.ui.skill_interface.v1",
    "defaultView": "demo.note.edit",
    "views": {
      "demo.note.edit": {
        "surfaces": ["modal"],
        "params": {
          "note_id": { "type": "string", "required": true }
        }
      }
    }
  },
  "registry": {
    "modals": {
      "demo_modal": {
        "implements": ["demo.note.edit"],
        "schema": {
          "id": "demo_modal",
          "layout": {
            "type": "single",
            "areas": [{ "id": "main" }]
          },
          "interface": {
            "schema": "adaos.ui.modal.interface.v1",
            "defaultRoute": "note.edit",
            "routes": {
              "note.edit": {
                "view": "demo.note.edit",
                "params": {},
                "state": {
                  "selectedNoteId": "$params.note_id"
                }
              }
            }
          },
          "widgets": [
              {
                "id": "back",
                "type": "input.commandBar",
                "area": "main",
                "actions": [
                { "type": "navigateModal", "params": { "route": "missing.route" } }
              ]
            }
          ]
        }
      }
    }
  }
}
""",
        },
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)

    codes = {issue.code for issue in report.issues}
    assert report.ok is False
    assert "webui.modal.route_missing_view_param" in codes
    assert "webui.modal.state_unknown_param" in codes
    assert "webui.action.navigate_modal_unknown_route" in codes


def test_webui_push_contract_helper_accepts_valid_addressed_modal(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        extra_files={
            "webui.json": """
{
  "interface": {
    "schema": "adaos.ui.skill_interface.v1",
    "defaultView": "demo.notes.list",
    "views": {
      "demo.notes.list": { "surfaces": ["modal"], "params": {} },
      "demo.note.edit": {
        "surfaces": ["modal"],
        "params": {
          "note_id": { "type": "string", "required": true }
        }
      }
    },
    "transitions": [
      {
        "from": "demo.notes.list",
        "to": "demo.note.edit",
        "surface": "modal",
        "params": { "note_id": "$event.id" }
      }
    ]
  },
  "widgets": [
    {
      "id": "latest",
      "type": "ui.list",
      "area": "main",
      "actions": [
        {
          "type": "navigate",
          "params": {
            "to": "demo.note.edit",
            "surface": "modal",
            "params": { "note_id": "$event.id" }
          }
        }
      ]
    }
  ],
  "registry": {
    "modals": {
      "demo_modal": {
        "implements": ["demo.notes.list", "demo.note.edit"],
        "schema": {
          "id": "demo_modal",
          "layout": {
            "type": "single",
            "areas": [{ "id": "main" }]
          },
          "interface": {
            "schema": "adaos.ui.modal.interface.v1",
            "defaultRoute": "notes.list",
            "routes": {
              "notes.list": {
                "view": "demo.notes.list",
                "params": {},
                "state": { "viewMode": "list" }
              },
              "note.edit": {
                "view": "demo.note.edit",
                "params": {
                  "note_id": { "type": "string", "required": true }
                },
                "state": { "viewMode": "edit", "selectedNoteId": "$params.note_id" }
              }
            }
          },
          "widgets": [
              {
                "id": "notes",
                "type": "ui.list",
                "area": "main",
                "actions": [
                {
                  "type": "navigateModal",
                  "params": {
                    "route": "note.edit",
                    "params": { "note_id": "$event.id" }
                  }
                }
              ]
            }
          ]
        }
      }
    }
  }
}
""",
        },
    )

    issues = validate_webui_file_contract(skill_dir, skill_name="demo_skill")

    assert issues == []


def test_skill_validation_rejects_tool_route_without_exact_tool(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=[
            "data_routes:",
            "  - surface: widget:demo.status",
            "    route: tool/details",
            "    first_paint: cached status",
            "    recovery: explicit retry",
            "    budget:",
            "      max_payload_bytes: 4096",
            "    guard_visibility: unavailable status",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert report.ok is False
    assert "data_routes.tool_missing" in {issue.code for issue in report.issues}


def test_skill_validation_rejects_same_skill_webui_action_for_undeclared_tool(
    tmp_path: Path,
) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        extra_files={
            "webui.json": """
{
  "widgets": [
    {
      "id": "refresh",
      "type": "ui.list",
      "actions": [
        {
          "on": "click:refresh",
          "type": "callSkill",
          "target": "demo_skill.refresh_usage",
          "params": {}
        }
      ]
    }
  ]
}
"""
        },
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert report.ok is False
    assert "webui.action.skill_tool_unknown" in {
        issue.code for issue in report.issues
    }


def test_skill_validation_accepts_same_skill_webui_action_for_declared_tool(
    tmp_path: Path,
) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        extra_files={
            "webui.json": """
{
  "widgets": [
    {
      "id": "refresh",
      "type": "ui.list",
      "actions": [
        {
          "on": "click:refresh",
          "type": "callSkill",
          "target": "demo_skill.ping",
          "params": {}
        }
      ]
    }
  ]
}
"""
        },
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert "webui.action.skill_tool_unknown" not in {
        issue.code for issue in report.issues
    }


def test_skill_validation_accepts_bounded_causal_tool_read(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=[
            "data_routes:",
            "  - surface: widget:demo.status",
            "    route: tool/details",
            "    tool: ping",
            "    first_paint: last successful status",
            "    recovery: explicit retry preserves the last value",
            "    budget:",
            "      max_payload_bytes: 4096",
            "    read_policy:",
            "      mode: stale_while_revalidate",
            "      triggers: [mount, explicit_refresh, targeted_invalidation]",
            "      cache_ttl_ms: 60000",
            "      max_request_hz: 0.1",
            "      preserve_last_value: true",
            "      invalidation_tags: [demo.status]",
            "    guard_visibility: unavailable status",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, strict=True)

    assert report.ok is True
    assert not report.issues


def test_skill_validation_rejects_subscription_policy_for_tool_read(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        handler="""
from adaos.sdk.core.decorators import tool

@tool(summary="ping")
def ping():
    return {"ok": True}
""",
        manifest_extra=[
            "data_routes:",
            "  - surface: widget:demo.status",
            "    route: tool/details",
            "    tool: ping",
            "    first_paint: empty status",
            "    recovery: explicit retry",
            "    budget:",
            "      max_payload_bytes: 4096",
            "      snapshot_policy: on_subscribe",
            "    read_policy:",
            "      mode: explicit",
            "      triggers: [explicit_refresh]",
            "      max_request_hz: 1",
            "      preserve_last_value: true",
            "    guard_visibility: unavailable status",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir)

    assert report.ok is False
    assert "data_routes.tool_snapshot_policy" in {issue.code for issue in report.issues}


def test_public_builder_generated_conversation_skill_is_release_quality() -> None:
    skill_dir = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "builder-generated-conversation-skill"
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)
    webui_issues = validate_webui_file_contract(
        skill_dir,
        skill_name="builder_generated_preferences_skill",
    )

    assert report.ok is True, [(item.code, item.message) for item in report.issues]
    assert webui_issues == []
