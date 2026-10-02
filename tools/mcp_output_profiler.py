"""Profile Root MCP response sizes without retaining response bodies.

Run from the repository root::

    .venv/Scripts/python tools/mcp_output_profiler.py --sample

The complete report is written under ``.tmp/``; stdout stays bounded to the
largest five tools and the search-contract findings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
SAFE_SAMPLE_TOOL_IDS = frozenset(
    {
        "development.list_contracts",
        "development.list_descriptor_sets",
        "adaos_dev.get_architecture_catalog",
        "adaos_dev.get_named_entity_registry",
        "adaos_dev.get_public_scenario_registry",
        "adaos_dev.get_public_skill_registry",
        "adaos_dev.get_sdk_metadata",
        "adaos_dev.get_template_catalog",
    }
)
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

load_dotenv(REPOSITORY_ROOT / ".env")

from adaos.apps.bootstrap import init_ctx  # noqa: E402
from adaos.services.project_deployment.default_runtime import (  # noqa: E402
    configure_default_distributed_runtimes,
)
from adaos.services.root_mcp.audit import list_audit_events  # noqa: E402
from adaos.services.root_mcp.output_profile import (  # noqa: E402
    audit_search_contracts,
    profile_audit_events,
)
from adaos.services.root_mcp.service import (  # noqa: E402
    invoke_tool,
    list_tool_contracts,
)


def _has_required_arguments(contract: Any) -> bool:
    schema = contract.input_schema if isinstance(contract.input_schema, dict) else {}
    return bool(schema.get("required"))


def _sample_safe_tools() -> list[dict[str, Any]]:
    sampled: list[dict[str, Any]] = []
    for contract in list_tool_contracts():
        if (
            contract.id not in SAFE_SAMPLE_TOOL_IDS
            or
            contract.side_effects != "none"
            or contract.availability.value != "enabled"
            or _has_required_arguments(contract)
            or contract.id == "development.get_mcp_output_profile"
        ):
            continue
        try:
            response = invoke_tool(
                contract.id,
                arguments={},
                actor="system:mcp-output-profiler",
                auth_method="owner_token",
                dry_run=True,
            )
            sampled.append(
                {
                    "tool_id": contract.id,
                    "ok": response.ok,
                    "status": response.status,
                    "error_code": response.error.code if response.error else None,
                }
            )
        except Exception as exc:  # a profiler must report one broken source and continue
            sampled.append(
                {
                    "tool_id": contract.id,
                    "ok": False,
                    "status": "exception",
                    "error_code": type(exc).__name__,
                }
            )
    return sampled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="call safe no-argument read tools")
    parser.add_argument("--event-limit", type=int, default=100_000)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / ".tmp" / "mcp-output-profile.json",
    )
    args = parser.parse_args()

    ctx = init_ctx()
    configure_default_distributed_runtimes(ctx, authoritative=False)
    sampled = _sample_safe_tools() if args.sample else []
    contracts = list_tool_contracts()
    profile = profile_audit_events(
        list_audit_events(limit=max(1, args.event_limit)),
        top_n=max(1, args.top),
    )
    report = {
        "schema": "adaos.mcp.output_profiler_report.v1",
        "profile": profile,
        "contract_audit": audit_search_contracts(contracts),
        "sampling": {
            "requested": bool(args.sample),
            "count": len(sampled),
            "failures": [item for item in sampled if not item["ok"]],
        },
    }
    output_path = args.output.resolve()
    tmp_root = (REPOSITORY_ROOT / ".tmp").resolve()
    if not output_path.is_relative_to(tmp_root):
        parser.error("--output must stay under the repository .tmp directory")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "report": str(output_path),
                "top": profile["top"],
                "search_contract_review_count": report["contract_audit"]["review_count"],
                "sampling_count": len(sampled),
                "sampling_failure_count": len(report["sampling"]["failures"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
