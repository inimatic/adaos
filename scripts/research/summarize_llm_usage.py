"""Summarize Researcher LLM usage receipts without double-counting recovery runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def _json(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _record(path: Path, value: Mapping[str, Any], *, root: Path) -> dict[str, Any] | None:
    schema = str(value.get("schema") or "")
    if schema == "adaos.research.authoring_run.v1":
        operation = "synthesis_authoring"
        job_id = value.get("provider_job_id")
        usage = value.get("usage")
        status = value.get("status")
    elif schema == "adaos.research.synthesis_review.v1":
        operation = "synthesis_review"
        reviewer = value.get("reviewer") if isinstance(value.get("reviewer"), Mapping) else {}
        job_id = reviewer.get("provider_job_id")
        usage = value.get("usage")
        status = value.get("verdict")
    elif schema == "adaos.research.llm_run_failure.v1":
        operation = value.get("operation")
        job_id = value.get("provider_job_id")
        usage = value.get("usage")
        status = value.get("status")
    elif schema == "adaos.research.case_run_failure.v1":
        operation = value.get("stage")
        job_id = value.get("provider_job_id")
        usage = value.get("researcher_llm_usage")
        status = value.get("status")
    else:
        return None
    checked_usage = dict(usage) if isinstance(usage, Mapping) else {}
    return {
        "provider_job_id": str(job_id or "").strip() or None,
        "operation": str(operation or "unknown"),
        "status": str(status or "unknown"),
        "usage": {field: checked_usage.get(field) for field in TOKEN_FIELDS},
        "accuracy": str(checked_usage.get("accuracy") or "unavailable"),
        "receipt_paths": [str(path.relative_to(root))],
    }


def summarize(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    patterns = ("authoring-run.json", "synthesis-review-*.json", "llm-failure.json", "failure.json")
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            value = _json(path)
            record = _record(path, value, root=root) if value is not None else None
            if record is not None:
                records.append(record)

    by_job: dict[str, dict[str, Any]] = {}
    unidentified: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for record in records:
        job_id = record["provider_job_id"]
        if not job_id:
            unidentified.append(record)
            continue
        previous = by_job.get(job_id)
        if previous is None:
            by_job[job_id] = record
            continue
        if previous["usage"] != record["usage"]:
            conflicts.append(job_id)
            continue
        previous["receipt_paths"].extend(record["receipt_paths"])
        if previous["status"] != "candidate_generated":
            previous["status"] = record["status"]

    unique = list(by_job.values())
    known = [
        record
        for record in unique
        if record["accuracy"] == "provider_reported"
        and all(record["usage"].get(field) is not None for field in TOKEN_FIELDS)
    ]
    unknown = [record for record in unique if record not in known]
    totals = {
        field: sum(int(record["usage"][field]) for record in known)
        for field in TOKEN_FIELDS
    }
    return {
        "schema": "adaos.research.llm_usage_ledger.v1",
        "accounting_scope": "researcher_llm",
        "aggregation_key": "provider_job_id",
        "unique_provider_jobs": len(unique),
        "provider_reported_jobs": len(known),
        "unknown_usage_jobs": len(unknown) + len(unidentified),
        "conflicting_usage_job_ids": sorted(set(conflicts)),
        "known_usage_lower_bound": totals,
        "exact_total_available": not unknown and not unidentified and not conflicts,
        "jobs": sorted(unique, key=lambda item: str(item["provider_job_id"])),
        "unidentified_attempts": unidentified,
        "builder_codex": {
            "required": False,
            "invoked": False,
            "total_tokens": 0,
            "accounting_scope": "builder_codex",
        },
        "current_codex_session_accounted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.root.resolve())
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.resolve().write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
