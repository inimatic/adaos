"""Validate WebUI documents against the current schema and layout invariants."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from adaos.services.webui_layout import layout_v2_findings  # noqa: E402


SCHEMA_PATH = ROOT / "src" / "adaos" / "abi" / "webui.v1.schema.json"
EXCLUDED_PATH_PARTS = {
    ".git",
    ".runtime",
    "history",
    "recovery",
    "snapshots",
    "state",
    "ui_revisions",
}


def _paths(inputs: list[str]) -> list[Path]:
    result: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            result.extend(
                item
                for item in sorted(path.rglob("*webui.json"))
                if item.name != "semantic.webui.json"
                and not EXCLUDED_PATH_PARTS.intersection(item.parts)
            )
        elif path.is_file() and (
            path.name == "webui.json" or path.name.endswith(".webui.json")
        ) and path.name != "semantic.webui.json":
            result.append(path)
    return list(dict.fromkeys(item.resolve() for item in result))


def _pages(value: Any, path: str = "$") -> Iterator[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if (
            isinstance(value.get("layout"), Mapping)
            and isinstance(value.get("widgets"), list)
            and value.get("id")
        ):
            yield path, value
        for key, child in value.items():
            yield from _pages(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _pages(child, f"{path}[{index}]")


def check_path(path: Path, validator: Draft202012Validator) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{"code": "webui.json_invalid", "path": "$", "message": str(exc)}]

    findings: list[dict[str, Any]] = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
        location = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.path
        )
        findings.append(
            {
                "code": "webui.schema_invalid",
                "path": location,
                "message": error.message,
            }
        )
    for page_path, page in _pages(document):
        findings.extend(layout_v2_findings(page, schema_path=page_path))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    paths = _paths(args.paths)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    failures: list[dict[str, Any]] = []
    for path in paths:
        for finding in check_path(path, validator):
            failures.append({"file": str(path), **finding})
    for finding in failures:
        print(json.dumps(finding, ensure_ascii=False), file=sys.stderr)
    print(
        json.dumps(
            {"files": len(paths), "findings": len(failures), "ok": not failures},
            ensure_ascii=False,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
