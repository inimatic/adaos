from __future__ import annotations

import json
from pathlib import Path

from scripts.research.summarize_llm_usage import summarize


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_usage_ledger_deduplicates_recovered_provider_job_and_keeps_unknown(tmp_path: Path) -> None:
    usage = {
        "accounting_scope": "researcher_llm",
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 50,
        "reasoning_tokens": 10,
        "total_tokens": 150,
        "accuracy": "provider_reported",
    }
    _write(
        tmp_path / "failed" / "llm-failure.json",
        {
            "schema": "adaos.research.llm_run_failure.v1",
            "operation": "synthesis_authoring",
            "provider_job_id": "job-1",
            "status": "validation_failed",
            "usage": usage,
        },
    )
    _write(
        tmp_path / "recovered" / "authoring-run.json",
        {
            "schema": "adaos.research.authoring_run.v1",
            "provider_job_id": "job-1",
            "status": "candidate_generated",
            "usage": usage,
        },
    )
    _write(
        tmp_path / "unknown" / "llm-failure.json",
        {
            "schema": "adaos.research.llm_run_failure.v1",
            "operation": "synthesis_review",
            "provider_job_id": "job-2",
            "status": "provider_failed",
            "usage": {
                "accounting_scope": "researcher_llm",
                "input_tokens": None,
                "cached_input_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
                "accuracy": "unavailable",
            },
        },
    )

    ledger = summarize(tmp_path)

    assert ledger["unique_provider_jobs"] == 2
    assert ledger["provider_reported_jobs"] == 1
    assert ledger["unknown_usage_jobs"] == 1
    assert ledger["known_usage_lower_bound"]["total_tokens"] == 150
    assert ledger["exact_total_available"] is False
    recovered = next(item for item in ledger["jobs"] if item["provider_job_id"] == "job-1")
    assert len(recovered["receipt_paths"]) == 2
