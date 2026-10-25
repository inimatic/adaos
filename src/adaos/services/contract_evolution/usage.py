"""Data-only package analysis; importing an Application is never necessary."""

from __future__ import annotations

import ast
import io
import json
from typing import Any, Mapping
import zipfile

from .producers import current_contracts, unit_digest
from .records import digest, seal
from .service import USAGE


def build_usage_lock(files: Mapping[str, bytes], *, application_ref: str,
                     consumer_ref: str, consumer_digest: str,
                     contracts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    from adaos.services.applications.sdk_compatibility import _call_aliases, _qualified
    units = contracts if contracts is not None else current_contracts()
    used: dict[str, set[str]] = {}
    unresolved = []

    def observe(ref, location):
        if ref in units:
            used.setdefault(ref, set()).add(location)
        else:
            unresolved.append({"code": "contract_not_indexed", "contract_ref": ref, "location": location})

    def walk(value, location):
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}[{index}]")
        elif isinstance(value, dict):
            widget = value.get("type")
            if isinstance(widget, str):
                ref = "webui:" + widget
                if ref in units:
                    observe(ref, location)
                lifecycle_ref = "webui:action.type:" + widget
                if lifecycle_ref in units:
                    observe(lifecycle_ref, location)
            if value.get("kind") == "mcp" or "toolId" in value:
                tool = value.get("toolId")
                if isinstance(tool, str) and not tool.startswith("$"):
                    observe("mcp:" + tool, location)
                elif tool:
                    unresolved.append({"code": "dynamic_mcp_tool", "location": location})
            for key, child in value.items():
                walk(child, f"{location}.{key}")

    for path, content in sorted(files.items()):
        if any(part in {"tests", ".runtime", "node_modules", "__pycache__"} for part in path.split("/")):
            continue
        if path.endswith(".py"):
            try:
                tree = ast.parse(content.decode("utf-8-sig"))
            except (SyntaxError, UnicodeError):
                unresolved.append({"code": "source_unreadable", "location": path})
                continue
            aliases = _call_aliases(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("adaos.sdk"):
                    for alias in node.names:
                        if alias.name == "*":
                            unresolved.append({"code": "wildcard_sdk_import", "location": path})
                        else:
                            symbol = "sdk:" + node.module + "." + alias.name
                            if symbol in units:
                                observe(symbol, f"{path}:{node.lineno}")
                if not isinstance(node, ast.Call):
                    continue
                symbol = _qualified(node.func, aliases.get(id(node), {}))
                if symbol and symbol.startswith("adaos.sdk."):
                    observe("sdk:" + symbol, f"{path}:{node.lineno}")
                    if any(isinstance(arg, ast.Starred) for arg in node.args) or any(k.arg is None for k in node.keywords):
                        unresolved.append({"code": "dynamic_sdk_arguments", "location": f"{path}:{node.lineno}"})
                if isinstance(node.func, ast.Name) and node.func.id in {"getattr", "__import__", "eval", "exec"}:
                    unresolved.append({"code": "dynamic_python_usage", "location": f"{path}:{node.lineno}"})
        elif path.endswith(".json"):
            try:
                walk(json.loads(content), path)
            except (ValueError, UnicodeError):
                unresolved.append({"code": "descriptor_unreadable", "location": path})
    # Producer identity includes all analyzer code and the selected policy.
    from pathlib import Path
    producer = digest({name: digest((Path(__file__).parent / name).read_text(encoding="utf-8"))
                       for name in ("usage.py", "producers.py")})
    artifacts = {}
    for path, content in files.items():
        if path.endswith(".json"):
            try:
                artifacts[path] = digest(json.loads(content))
            except (ValueError, UnicodeError):
                pass
    return seal(USAGE, recorded_at="1970-01-01T00:00:00+00:00",
        application_ref=application_ref, consumer_ref=consumer_ref,
        consumer_digest=consumer_digest, producer_digest=producer, complete=not unresolved,
        unresolved=unresolved, source_artifacts=artifacts,
        edges=[{"contract_ref": ref, "contract_digest": unit_digest(units[ref]),
            "surface": sorted(paths), "exposure": "static" if ref.startswith("sdk:") else "compiled"}
            for ref, paths in sorted(used.items())])


def package_usage_evidence(packages, *, project_id: str) -> tuple[dict[str, Any], ...]:
    records = []
    for package in packages:
        with zipfile.ZipFile(io.BytesIO(package.archive_bytes)) as archive:
            files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        records.append(build_usage_lock(files, application_ref="project:" + project_id,
            consumer_ref=package.ref.key, consumer_digest=package.ref.digest))
    return tuple(records)


def release_usage_locks(release, *, application_ref: str) -> list[dict[str, Any]]:
    """Rebind build evidence to its admitted Application release, preserving package subjects."""
    from .records import validate
    records = []
    components = {component.key: component.digest for component in release.components}
    for item in release.validation_evidence:
        if item.get("schema") != USAGE:
            continue
        entry = validate(item)
        if components.get(entry["consumer_ref"]) != entry["consumer_digest"]:
            raise ValueError("usage evidence does not belong to an exact release component")
        fields = {key: value for key, value in entry.items() if key not in {"schema", "usage_lock_id", "digest", "recorded_at"}}
        fields.update(application_ref=application_ref)
        records.append(seal(USAGE, recorded_at=entry["recorded_at"], **fields))
    return records
