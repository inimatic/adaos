from __future__ import annotations

import ast
from pathlib import Path

import nats
import nats.errors
import nats.protocol.parser
import y_py
import ypy_websocket
import ypy_websocket.ystore
import ypy_websocket.websocket


def _is_sys_modules(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def test_nats_is_loaded_from_the_installed_package() -> None:
    assert hasattr(nats, "__path__")
    assert nats.errors.Error
    assert nats.protocol.parser.Parser


def test_yjs_dependencies_are_loaded_from_installed_packages() -> None:
    assert hasattr(y_py, "__path__")
    assert y_py.YDoc
    assert hasattr(ypy_websocket, "__path__")
    assert ypy_websocket.ystore.BaseYStore
    assert ypy_websocket.websocket.Websocket


def test_tests_do_not_replace_the_nats_package_in_sys_modules() -> None:
    violations: list[str] = []

    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and _is_sys_modules(node.value)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "nats"
            ):
                violations.append(f"{path.name}:{node.lineno}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"setdefault", "__setitem__"}
                and _is_sys_modules(node.func.value)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "nats"
            ):
                violations.append(f"{path.name}:{node.lineno}")

    assert violations == [], (
        "Tests must use the installed nats-py package instead of replacing the "
        f"process-wide module: {', '.join(violations)}"
    )
