"""Non-executing consumer audit against the current public Python SDK surface."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import Any


_SKIP = {".git", ".runtime", "__pycache__", ".venv", "tests", "node_modules"}


def _resolve(symbol: str) -> Any:
    """Import only trusted Core SDK modules, never application handlers."""
    parts = symbol.split(".")
    for count in range(len(parts), 1, -1):
        try:
            value = importlib.import_module(".".join(parts[:count]))
        except ModuleNotFoundError as exc:
            # A missing optional dependency is not proof of a removed SDK API.
            if exc.name and not (symbol == exc.name or symbol.startswith(exc.name + ".")):
                raise RuntimeError("sdk_dependency_unavailable") from exc
            continue
        for name in parts[count:]:
            value = getattr(value, name)
        return value
    raise AttributeError(symbol)


def _qualified(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _lexical_nodes(node: ast.AST):
    """Walk one Python scope, excluding nested function/class bodies."""
    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield child
        else:
            yield from _lexical_nodes(child)


def _call_aliases(tree: ast.Module) -> dict[int, dict[str, str]]:
    result = {}

    def scope(node, inherited):
        aliases = dict(inherited)
        nodes = [item for statement in node.body for item in _lexical_nodes(statement)]
        for item in nodes:
            if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
                aliases.pop(item.id, None)
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                aliases.pop(item.name, None)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                        *([node.args.vararg] if node.args.vararg else []),
                        *([node.args.kwarg] if node.args.kwarg else [])):
                aliases.pop(arg.arg, None)
        for item in nodes:
            if isinstance(item, ast.ImportFrom) and not item.level and item.module:
                if item.module == "adaos.sdk" or item.module.startswith("adaos.sdk."):
                    for alias in item.names:
                        if alias.name != "*":
                            aliases[alias.asname or alias.name] = f"{item.module}.{alias.name}"
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    if alias.name == "adaos.sdk" or alias.name.startswith("adaos.sdk."):
                        aliases[alias.asname or "adaos"] = alias.name if alias.asname else "adaos"
        for item in nodes:
            if isinstance(item, ast.Call):
                result[id(item)] = aliases
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope(item, aliases)
    scope(tree, {})
    return result


def audit_sdk_usage(source_root: Path) -> dict[str, Any]:
    """Prove statically visible imports/calls; dynamic behavior needs smoke tests.

    This is deliberately an observation, not a new blanket rejection gate for
    old apps. Unknown dependencies and dynamic calls remain visible as unknown.
    No consumer module, model, provider or application tool is executed.
    """
    root = Path(source_root)
    findings: list[dict[str, Any]] = []
    symbols: set[str] = set()
    resolved: dict[str, Any] = {}
    checked_calls = 0
    dynamic_calls = 0
    files = 0

    def resolve(symbol: str, relative: str, line: int):
        if symbol not in resolved:
            try:
                resolved[symbol] = ("resolved", _resolve(symbol))
            except (ModuleNotFoundError, AttributeError):
                resolved[symbol] = ("missing_sdk_symbol", None)
            except Exception:
                resolved[symbol] = ("sdk_resolution_unknown", None)
        status, value = resolved[symbol]
        if status != "resolved":
            findings.append({"code": status, "symbol": symbol, "file": relative,
                             "line": line, "severity": "error" if status == "missing_sdk_symbol" else "unknown"})
        return value

    for path in sorted(root.rglob("*.py")):
        relative_path = path.relative_to(root)
        if _SKIP.intersection(relative_path.parts):
            continue
        relative = relative_path.as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeError, OSError):
            findings.append({"code": "source_unreadable", "file": relative, "severity": "unknown"})
            continue
        files += 1
        call_aliases = _call_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                if node.module == "adaos.sdk" or node.module.startswith("adaos.sdk."):
                    for alias in node.names:
                        if alias.name == "*":
                            dynamic_calls += 1
                            continue
                        symbol = f"{node.module}.{alias.name}"
                        symbols.add(symbol)
                        resolve(symbol, relative, node.lineno)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "adaos.sdk" or alias.name.startswith("adaos.sdk."):
                        symbols.add(alias.name)
                        resolve(alias.name, relative, node.lineno)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            symbol = _qualified(node.func, call_aliases.get(id(node), {}))
            if not symbol or not symbol.startswith("adaos.sdk."):
                continue
            symbols.add(symbol)
            value = resolve(symbol, relative, node.lineno)
            if value is None or not callable(value):
                continue
            try:
                signature = inspect.signature(value)
            except (TypeError, ValueError):
                dynamic_calls += 1
                continue
            dynamic = any(isinstance(arg, ast.Starred) for arg in node.args) or any(
                keyword.arg is None for keyword in node.keywords)
            if dynamic:
                dynamic_calls += 1
                # Unknown unpacking can provide missing inputs, not legitimize
                # an explicit keyword that the SDK no longer accepts.
                if not any(p.kind == p.VAR_KEYWORD for p in signature.parameters.values()):
                    bad = [k.arg for k in node.keywords if k.arg and (
                        k.arg not in signature.parameters or
                        signature.parameters[k.arg].kind == inspect.Parameter.POSITIONAL_ONLY)]
                    if bad:
                        findings.append({"code": "sdk_call_signature_mismatch", "symbol": symbol,
                            "file": relative, "line": node.lineno, "severity": "error",
                            "detail": f"unsupported explicit keywords: {', '.join(bad)}"})
                continue
            try:
                signature.bind(*([None] * len(node.args)), **{k.arg: None for k in node.keywords})
                checked_calls += 1
            except TypeError as exc:
                findings.append({"code": "sdk_call_signature_mismatch", "symbol": symbol,
                    "file": relative, "line": node.lineno, "severity": "error", "detail": str(exc)})
    errors = [item for item in findings if item["severity"] == "error"]
    unknown = [item for item in findings if item["severity"] == "unknown"]
    return {"schema": "adaos.application.sdk_usage_audit.v1",
            "status": "incompatible" if errors else "unknown" if unknown else "static_checks_passed",
            "scope": "python_imports_and_call_signatures_only", "files": files,
            "checked_calls": checked_calls, "dynamic_calls": dynamic_calls,
            "symbols": sorted(symbols), "findings": findings,
            "runtime_smoke_required": True}
