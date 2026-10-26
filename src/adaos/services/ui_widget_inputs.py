"""Strict ABI-backed inputs and bounded structural composition for WebUI."""
from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

INPUT_DEFINITIONS = {
    "visual.imageCompare": "imageCompareInputs",
    "layout.group": "groupInputs",
    "feedback.taskProgress": "taskProgressInputs",
}


def input_contracts() -> dict[str, Any]:
    source = Path(__file__).resolve().parents[1] / "abi/webui.v1.schema.json"
    stat = source.stat()
    return _input_contracts(str(source), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=1)
def _input_contracts(source: str, modified: int, size: int) -> dict[str, Any]:
    definitions = json.loads(Path(source).read_text(encoding="utf-8"))["$defs"]
    return {widget: definitions[name] for widget, name in INPUT_DEFINITIONS.items()}


def flatten_widgets(widgets: Any, depth: int = 0) -> list[Mapping[str, Any]]:
    if depth > 8 or not isinstance(widgets, list):
        return []
    result = []
    for widget in widgets[:500]:
        if not isinstance(widget, Mapping):
            continue
        result.append(widget)
        if widget.get("type") == "layout.group":
            result.extend(flatten_widgets(widget.get("children"), depth + 1))
    return result[:500]


def widget_input_findings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    contracts = input_contracts()
    findings: list[dict[str, Any]] = []

    def report(path: str, message: str) -> None:
        findings.append({"code": "ui.component.contract_invalid", "severity": "error", "path": path, "message": message})

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if isinstance(value, Mapping):
            if "widgets" in value and isinstance(value["widgets"], list):
                ids: set[str] = set()
                count = 0

                def widget(item: Any, at: str, level: int) -> None:
                    nonlocal count
                    if not isinstance(item, Mapping):
                        return
                    count += 1
                    if count > 500:
                        if count == 501:
                            report(at, "Composition is limited to 8 nested groups and 500 widgets per surface.")
                        return
                    if level > 8:
                        report(at, "Composition is limited to 8 nested groups and 500 widgets per surface.")
                        return
                    identity = item.get("id")
                    if isinstance(identity, str):
                        if identity in ids:
                            report(at + ".id", "Widget ids must be unique across the complete surface, including groups.")
                        ids.add(identity)
                    contract = contracts.get(item.get("type"))
                    if contract:
                        for error in Draft202012Validator(contract).iter_errors(item.get("inputs", {})):
                            report(at + ".inputs" + "".join(f".{part}" for part in error.path), error.message)
                    children = item.get("children")
                    if children is not None:
                        if item.get("type") != "layout.group" or not isinstance(children, list) or len(children) > 100:
                            report(at + ".children", "Only layout.group owns children, with at most 100 direct children.")
                        else:
                            for i, child in enumerate(children):
                                widget(child, f"{at}.children[{i}]", level + 1)

                for index, item in enumerate(value["widgets"]):
                    widget(item, f"{path}.widgets[{index}]", 0)
            for key, child in value.items():
                if key not in {"widgets", "children", "inputs", "dataSource", "prototypeFixture"}:
                    visit(child, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]", depth + 1)

    visit(document, "$")
    return findings
