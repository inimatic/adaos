from __future__ import annotations

from pathlib import Path

from adaos.services.agent_context import get_ctx
from adaos.services.skill.validation import SkillValidationService


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
