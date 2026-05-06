from __future__ import annotations

import sys
from pathlib import Path

from adaos.skills import runtime_runner as runtime_runner_module


def _write_skill(root: Path, name: str, marker: str) -> Path:
    skill_dir = root / name
    (skill_dir / "handlers").mkdir(parents=True, exist_ok=True)
    (skill_dir / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "def get_snapshot(**kwargs):\n"
        f"    return {{'skill': '{name}', 'marker': '{marker}', 'kwargs': dict(kwargs)}}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_bare_tool_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    (skill_dir / "handlers").mkdir(parents=True, exist_ok=True)
    (skill_dir / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "from adaos.sdk.core.decorators import tool\n\n"
        "@tool\n"
        "def detach_link(node_id=None, target_node_id=None, webspace_id=None):\n"
        "    return {\n"
        "        'node_id': node_id,\n"
        "        'target_node_id': target_node_id,\n"
        "        'webspace_id': webspace_id,\n"
        "    }\n\n"
        "@tool\n"
        "def refresh_snapshot(webspace_id=None):\n"
        "    return {'webspace_id': webspace_id}\n\n"
        "@tool\n"
        "def ping():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_execute_tool_isolates_generic_handlers_main_between_skills(tmp_path: Path) -> None:
    alpha = _write_skill(tmp_path, "alpha_skill", "alpha")
    beta = _write_skill(tmp_path, "beta_skill", "beta")

    before = {key: sys.modules[key] for key in list(sys.modules.keys()) if key == "handlers" or key.startswith("handlers.")}
    try:
        first = runtime_runner_module.execute_tool(alpha, module="handlers.main", attr="get_snapshot", payload={"city": "Berlin"})
        second = runtime_runner_module.execute_tool(beta, module="handlers.main", attr="get_snapshot", payload={"city": "Moscow"})
    finally:
        for key in list(sys.modules.keys()):
            if key == "handlers" or key.startswith("handlers."):
                sys.modules.pop(key, None)
        sys.modules.update(before)

    assert first["skill"] == "alpha_skill"
    assert second["skill"] == "beta_skill"
    assert second["marker"] == "beta"


def test_execute_tool_supports_bare_tool_decorator(tmp_path: Path) -> None:
    skill_dir = _write_bare_tool_skill(tmp_path, "gamma_skill")

    before = {key: sys.modules[key] for key in list(sys.modules.keys()) if key == "handlers" or key.startswith("handlers.")}
    try:
        detach_result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="detach_link",
            payload={
                "node_id": "node-a",
                "target_node_id": "node-b",
                "webspace_id": "ws-1",
                "_meta": {
                    "webspace_id": "ws-1",
                    "target_node_id": "node-b",
                },
            },
        )
        refresh_result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="refresh_snapshot",
            payload={
                "webspace_id": "ws-2",
                "_meta": {
                    "webspace_id": "ws-2",
                },
            },
        )
        ping_result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="ping",
            payload={
                "_meta": {
                    "webspace_id": "ws-3",
                },
            },
        )
    finally:
        for key in list(sys.modules.keys()):
            if key == "handlers" or key.startswith("handlers."):
                sys.modules.pop(key, None)
        sys.modules.update(before)

    assert detach_result == {
        "node_id": "node-a",
        "target_node_id": "node-b",
        "webspace_id": "ws-1",
    }
    assert refresh_result == {
        "webspace_id": "ws-2",
    }
    assert ping_result == {
        "ok": True,
    }
