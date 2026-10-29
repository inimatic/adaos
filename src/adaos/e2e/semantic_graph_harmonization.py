"""Model-free proof that semantic-v2 graph harmonization preserves behavior.

The runner compares the retained document compiler with the graph route on two
independent tracked fixtures and one deterministic current-contract extension.
It also proves exact round-trip, dependency closure, bounded reduction, diff
scope and inverse replay.  Evidence is retained under ``e2e/artifacts``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_bytes(value)).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _current_contract_fixture(source: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(source))
    value["document_id"] = "semantic-graph-current-contract"
    value["layout"] = {
        "pattern": "workbench",
        "regions": [
            {
                "id": "primary", "role": "main", "priority": 100,
                "scroll": "region", "wide": "pane", "compact": "stack",
                "min_px": 480, "preferred_px": 800, "max_px": 1600, "grow": 1,
            },
            {
                "id": "supporting", "role": "inspector", "priority": 60,
                "scroll": "region", "wide": "pane", "compact": "sheet",
                "min_px": 320, "preferred_px": 420, "max_px": 800, "grow": 0,
            },
        ],
    }
    value["capability_surfaces"] = [
        {
            "id": "preview", "capability_ref": "visual.sitePreview",
            "region_role": "primary",
            "title": {"key": "preview.title", "en": "Preview", "ru": "Предпросмотр"},
            "site_id": "graph-proof", "route": "/", "min_height_px": 480,
        }
    ]
    shared = {
        "requirement_ref": "job:shared",
        "disclosure": {
            "en": "Automation remains separate.",
            "ru": "Автоматизация остаётся отдельной.",
        },
        "acceptance": "The declared authority boundary remains observable.",
    }
    value["automation_requirements"] = [
        {**shared, "reason": "business_rule"},
        {**shared, "reason": "external_integration"},
    ]
    value["requirement_bindings"].append(
        {"requirement_ref": "job:shared", "semantic_refs": ["view:records-list"]}
    )
    return value


def _case(name: str, source: Mapping[str, Any]) -> dict[str, Any]:
    from adaos.services.builder.semantic_graph import reduce_semantic_graph
    from adaos.services.builder.semantic_graph_model import (
        semantic_document_to_graph,
        semantic_graph_dependency_closure,
        semantic_graph_diff,
        semantic_graph_to_document,
        validate_semantic_graph,
    )
    from adaos.services.builder.semantic_prototype import (
        _compile_semantic_prototype_v2,
        _compile_semantic_prototype_v2_graph,
        _validate_semantic_prototype_v2,
    )

    normalized = _validate_semantic_prototype_v2(source)
    graph = semantic_document_to_graph(normalized)
    receipt = validate_semantic_graph(graph)
    roundtrip = semantic_graph_to_document(graph)
    if roundtrip != normalized:
        raise ValueError(f"{name}: graph round-trip changed semantic source")

    project_ref = f"project:semantic-graph-proof-{name}"
    direct = _compile_semantic_prototype_v2(normalized, project_ref=project_ref)
    harmonized = _compile_semantic_prototype_v2_graph(normalized, project_ref=project_ref)
    route_receipt = harmonized.pop("semantic_graph_receipt")
    if direct != harmonized:
        raise ValueError(f"{name}: graph compiler differs from the retained compiler")

    target = next(item for item in normalized["views"] if "en" in item["title"])
    ref = f"view:{target['id']}"
    op = {
        "seq": 1, "kind": "label.set", "ref": ref, "locale": "en",
        "expected": target["title"]["en"],
        "text": target["title"]["en"] + " (graph proof)",
    }
    reduced = reduce_semantic_graph(graph, [op], write_refs=[ref])
    if reduced["graph_diff"]["write_refs"] != [ref]:
        raise ValueError(f"{name}: graph diff escaped the admitted write ref")
    inverse = reduce_semantic_graph(
        reduced["semantic_graph"], reduced["inverse_ops"], write_refs=[ref]
    )
    if inverse["semantic_graph"] != graph or inverse["semantic_document"] != normalized:
        raise ValueError(f"{name}: inverse replay did not restore the exact graph")
    independent_diff = semantic_graph_diff(graph, reduced["semantic_graph"])
    if independent_diff != reduced["graph_diff"]:
        raise ValueError(f"{name}: reducer diff differs from independent graph diff")

    closure = semantic_graph_dependency_closure(graph, [ref])
    if ref not in closure or not any(item.startswith("resource:") for item in closure):
        raise ValueError(f"{name}: graph dependency closure is incomplete")

    return {
        "case": name,
        "ok": True,
        "source_digest": _digest(normalized),
        "graph_digest": graph["graph_digest"],
        "webui_digest": _digest(direct["webui"]),
        "locale_digest": _digest(direct["locale_dictionaries"]),
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "roundtrip_exact": True,
        "compiler_result_exact": True,
        "webui_exact": direct["webui"] == harmonized["webui"],
        "browser_input_exact": _bytes(direct["webui"]) == _bytes(harmonized["webui"]),
        "reducer_write_refs": reduced["graph_diff"]["write_refs"],
        "reducer_change_count": len(reduced["graph_diff"]["changes"]),
        "inverse_exact": True,
        "dependency_root": ref,
        "dependency_closure": closure,
        "graph_receipt": receipt,
        "compiler_graph_receipt": route_receipt,
    }


def run(repo: Path, output: Path) -> dict[str, Any]:
    if not output.is_relative_to((repo / "e2e/artifacts").resolve()):
        raise ValueError("semantic graph evidence must stay under e2e/artifacts")
    if output.exists():
        raise ValueError("semantic graph evidence output already exists")
    output.mkdir(parents=True)

    fixture_paths = [
        repo / "e2e/builder/development/semantic-increments/fixture.json",
        repo / "e2e/builder/development/contract-conformance/inventory-fixture.json",
    ]
    sources = [(path.stem + "-" + path.parent.name, _read(path)) for path in fixture_paths]
    sources.append(("current-contract-extension", _current_contract_fixture(sources[0][1])))
    rows = [_case(name, source) for name, source in sources]
    pinned_paths = [
        repo / "src/adaos/abi/builder.semantic_graph.v1.schema.json",
        repo / "src/adaos/abi/webui.semantic.v2.schema.json",
        repo / "src/adaos/services/builder/semantic_graph_model.py",
        repo / "src/adaos/services/builder/semantic_graph.py",
        repo / "src/adaos/services/builder/semantic_prototype.py",
        repo / "src/adaos/e2e/semantic_graph_harmonization.py",
        *fixture_paths,
    ]

    report = {
        "schema": "adaos.evidence.semantic_graph_harmonization.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "source_authority": "semantic.webui.json",
            "graph": "canonical Core in-memory model",
            "provider_calls": 0,
            "browser_claim": (
                "The graph route emits byte-identical WebUI to the retained compiler. "
                "Browser execution is qualified separately by the semantic browser cohort."
            ),
        },
        "environment": {"python": sys.version, "platform": platform.platform()},
        "implementation_pins": {
            str(path.relative_to(repo)).replace("\\", "/"): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in pinned_paths
        },
        "fixtures": [str(path.relative_to(repo)).replace("\\", "/") for path in fixture_paths],
        "rows": rows,
        "summary": {
            "ok": all(row["ok"] for row in rows),
            "case_count": len(rows),
            "roundtrip_exact": sum(row["roundtrip_exact"] for row in rows),
            "compiler_result_exact": sum(row["compiler_result_exact"] for row in rows),
            "browser_input_exact": sum(row["browser_input_exact"] for row in rows),
            "inverse_exact": sum(row["inverse_exact"] for row in rows),
            "provider_calls": 0,
        },
    }
    _write(output / "report.json", report)
    _write(output / "sources.json", {
        name: {"digest": _digest(source), "document_id": source["document_id"]}
        for name, source in sources
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    repo = Path.cwd().resolve()
    output = (args.output or (
        repo / "e2e/artifacts" /
        ("semantic-graph-harmonization-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    )).resolve()
    report = run(repo, output)
    print(json.dumps({"output": str(output), **report["summary"]}, ensure_ascii=False))
    return 0 if report["summary"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
