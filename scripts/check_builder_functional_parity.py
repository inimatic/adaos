"""Check a Builder ``webui.json`` against its functional parity contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs" / "architecture" / "builder-functional-parity.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)


def _bindings(webui: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for item in _objects(webui):
        if item.get("type") == "callSkill" and item.get("target"):
            found.add(str(item["target"]))
        if item.get("kind") == "skill" and item.get("name"):
            found.add(str(item["name"]))
        if item.get("kind") == "stream" and item.get("receiver"):
            found.add(f"stream:{item['receiver']}")
    return found


def _widget(page: dict[str, Any], widget_id: str) -> dict[str, Any]:
    return next(
        (item for item in page.get("widgets", []) if item.get("id") == widget_id),
        {},
    )


def inspect(
    webui: dict[str, Any],
    contract: dict[str, Any],
    *,
    include_forward: bool = True,
) -> dict[str, list[str]]:
    application = webui["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    widget_ids = {str(item.get("id")) for item in page.get("widgets", [])}
    modal_ids = set(application.get("modals", {}))
    bindings = _bindings(webui)
    lifecycle = _widget(page, "project-tree")
    lifecycle_buttons = {
        str(item.get("id"))
        for item in lifecycle.get("inputs", {}).get("buttons", [])
    }
    new_project = application.get("modals", {}).get("new-project", {})
    new_widgets = new_project.get("schema", {}).get("widgets", [])
    new_form = next(
        (item for item in new_widgets if item.get("id") == "new-project-form"),
        {},
    )
    kind_field = next(
        (
            item
            for item in new_form.get("inputs", {}).get("fields", [])
            if item.get("id") == "object_type"
        ),
        {},
    )
    project_kinds = {
        str(item.get("value")) for item in kind_field.get("options", [])
    }
    required_bindings = set(contract["required_bindings"])
    if include_forward:
        required_bindings.update(contract.get("forward_required_bindings", []))
    forbidden_bindings = set(contract.get("forbidden_bindings", []))
    return {
        "missing_widgets": sorted(set(contract["required_widget_ids"]) - widget_ids),
        "missing_modals": sorted(set(contract["required_modal_ids"]) - modal_ids),
        "missing_bindings": sorted(required_bindings - bindings),
        "missing_lifecycle_buttons": sorted(
            set(contract["required_lifecycle_buttons"]) - lifecycle_buttons
        ),
        "missing_project_kinds": sorted(
            set(contract["required_project_kinds"]) - project_kinds
        ),
        "forbidden_bindings": sorted(forbidden_bindings & bindings),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("webui", type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument(
        "--profile",
        choices=("reference", "recovered"),
        default="recovered",
    )
    args = parser.parse_args()
    report = inspect(
        _read(args.webui),
        _read(args.contract),
        include_forward=args.profile == "recovered",
    )
    failures = {key: value for key, value in report.items() if value}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
