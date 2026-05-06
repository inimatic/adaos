# tests/test_decorators_registry.py
from __future__ import annotations
from adaos.sdk.core.decorators import tool, resolve_tool


def test_tool_registration_and_resolve():
    @tool("unit.echo")
    def echo(x: int) -> int:
        return x

    fn = resolve_tool(echo.__module__, "unit.echo")
    assert fn is echo


def test_tool_registration_supports_bare_decorator():
    @tool
    def bare_echo(x: int) -> int:
        return x

    fn = resolve_tool(bare_echo.__module__, "bare_echo")
    assert fn is bare_echo
    assert bare_echo(7) == 7
