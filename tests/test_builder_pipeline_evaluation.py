"""Evaluation instrumentation must preserve the native delivery contract."""
import ast
from functools import wraps
import inspect
from pathlib import Path
from typing import Any

import pytest


def test_native_mcp_protocol_failure_is_not_a_tool_execution_error():
    from adaos.e2e.prototype_mcp_eval import mcp_trace
    trace = mcp_trace({'response': {'output': [{'type': 'mcp_call', 'status': 'failed',
        'name': 'builder_read_prototype_guidance', 'arguments': '{}',
        'error': {'type': 'mcp_protocol_error', 'code': 32600, 'message': 'Session terminated'}}]}})
    assert trace['errors'] == trace['protocol_errors'] == 1
    assert trace['tool_execution_errors'] == 0


def test_native_mcp_expected_tool_error_remains_counted_once_and_classified():
    from adaos.e2e.prototype_mcp_eval import mcp_trace
    trace = mcp_trace({'response': {'output': [{'type': 'mcp_call', 'status': 'failed',
        'name': 'builder_read_prototype_guidance', 'arguments': '{}',
        'error': {'type': 'mcp_tool_execution_error', 'content': [{'type': 'text', 'text': '{"ok":false,"error":"prototype_guidance_fragment_not_found"}'}]}}]}})
    assert trace['errors'] == trace['tool_execution_errors'] == 1
    assert trace['protocol_errors'] == 0


def test_repair_recorders_preserve_sdk_signatures():
    repo = Path(__file__).parents[1]
    tree = ast.parse((repo / "e2e/stand/repair-prototype-pipeline-cohort.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for name, target in (("submit", "native_submit"), ("wait", "native_wait")):
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "wraps" and len(node.args) == 1
                   and isinstance(node.args[0], ast.Name) and node.args[0].id == target
                   for node in functions[name].decorator_list)


def test_recorded_repair_retains_native_provider_deadline():
    repo = Path(__file__).parents[1]
    handler = repo / ".adaos/workspace/skills/builder_skill/handlers/main.py"
    if not handler.exists():
        pytest.skip("Installed native Builder fixture absent")
    tree = ast.parse(handler.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_development_profile_kwargs")
    namespace = {"Any": Any, "inspect": inspect, "_builder_llm_job_timeout_s": lambda: 930,
                 "_prototype_mcp_sdk_call": lambda _: {}}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(handler), "exec"), namespace)

    def sdk_submit(messages, *, profile_scope=None, provider_timeout_s=None, tools=None):
        return {"provider_timeout_s": provider_timeout_s}

    @wraps(sdk_submit)
    def recorder(messages, **kwargs):
        return sdk_submit(messages, **kwargs)

    native = namespace["_development_profile_kwargs"](sdk_submit)
    recorded = namespace["_development_profile_kwargs"](recorder)
    assert native == recorded == {"profile_scope": "development", "provider_timeout_s": 900.0}
    assert recorder([], **recorded)["provider_timeout_s"] == 900.0
