"""Compare retained Builder runs without generating or changing their verdicts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
from urllib.parse import urlencode


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def summarize(root: Path) -> dict:
    run = read(root / "run.json")
    report = read(root / "report.json")
    cases = []
    for ref in report["case_results"]:
        result = read(root / ref)
        case_id = result["case_id"]
        repetition = int(result.get("repetition", 1))
        generation_path = root / "evidence/generation" / f"{case_id}-attempt-{repetition:02}.json"
        generation = read(generation_path) if generation_path.exists() else {}
        terminals = [item for item in generation.get("model_io_artifacts", []) if item["kind"] == "terminal"]
        terminal = read(root / terminals[0]["evidence_ref"]) if terminals else {}
        diagnostic = terminal.get("diagnostic", {})
        telemetry = diagnostic.get("telemetry", {})
        primary_usage = telemetry.get("usage_breakdown", {}).get("primary", telemetry.get("usage", {}))
        calls = []
        for stage, info, usage in [("primary", telemetry, primary_usage),
                                   ("repair", telemetry.get("repair", {}), telemetry.get("repair", {}).get("usage", {}))]:
            if not info:
                continue
            calls.append({"stage": stage, "model": info.get("provider", {}).get("model"),
                          "response_status": info.get("provider", {}).get("response_status"),
                          "timing": info.get("timing", {}), "usage": usage})
        requests = []
        for item in generation.get("model_io_artifacts", []):
            if item["kind"] not in {"request", "semantic_repair_request"}:
                continue
            request = read(root / item["evidence_ref"])
            messages = request.get("messages", [])
            options = request.get("generation", {}).get("options", {})
            requests.append({"kind": item["kind"], "ref": item["evidence_ref"], "model": request.get("generation", {}).get("model"),
                             "effort": options.get("reasoning", {}).get("effort"), "max_tokens": options.get("max_tokens"),
                             "schema_digest": digest(options.get("text", {}).get("format", {})),
                             "message_chars": [len(message["content"]) for message in messages],
                             "stable_message_digests": [digest(message) for message in messages[:2]]})
        grade = next((step["output"] for step in result["steps"] if step["id"] == "grade"), {})
        checkpoint_path = root / "checkpoints" / case_id / f"attempt-{repetition:02}.json"
        checkpoint = read(checkpoint_path) if checkpoint_path.exists() else {}
        previews = checkpoint.get("cleanup", {}).get("previews", [])
        metrics = result["metrics"]
        cases.append({"case_id": case_id, "repetition": repetition, "status": result["status"], "pipeline_seconds": result["duration_ms"] / 1000,
                      "model_seconds": sum(call["timing"].get("execution_ms", 0) for call in calls) / 1000,
                      "input_tokens": metrics.get("fresh_input_tokens", 0) + metrics.get("cached_input_tokens", 0),
                      "cached_input_tokens": metrics.get("cached_input_tokens", 0),
                      "output_tokens": metrics.get("output_tokens", 0), "reasoning_tokens": metrics.get("reasoning_tokens", 0),
                      "non_reasoning_output_tokens": metrics.get("output_tokens", 0) - metrics.get("reasoning_tokens", 0),
                      "model_calls": metrics.get("model_calls", 0), "repair_attempted": generation.get("repair_attempted", False),
                      "grade": grade.get("score"), "grade_summary": grade.get("summary"),
                      "grade_findings": grade.get("findings"), "grader_metrics": grade.get("grader_metrics"),
                      "failure": result.get("failure"), "generation_error": diagnostic.get("result", {}).get("error"),
                      "generation_detail": diagnostic.get("result", {}).get("detail"),
                      "generation_validation": diagnostic.get("result", {}).get("validation"),
                      "attempts": generation.get("attempts"), "calls": calls, "requests": requests,
                      "preview": {key: previews[-1][key] for key in ("webspace_id", "scenario_id")} if previews else None,
                      "created": next((step["output"] for step in result["steps"] if step["id"] == "create"), {})})
    return {"run_id": run["run_id"], "run": run, "summary": report["summary"], "metrics": report["metrics"], "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("low", type=Path)
    parser.add_argument("high", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--subnet-id", help="Local subnet used for optional preview links")
    parser.add_argument("--client-url", default="http://127.0.0.1:8100/")
    args = parser.parse_args()
    low, high = summarize(args.low), summarize(args.high)
    if low["run"]["input_attribution"]["case_digests"] != high["run"]["input_attribution"]["case_digests"]:
        raise ValueError("Case inputs differ")
    if low["run"]["evaluation"] != high["run"]["evaluation"]:
        raise ValueError("Grader configuration differs")
    for cohort, effort in [(low, "low"), (high, "high")]:
        for case in cohort["cases"]:
            if not case["requests"]:
                raise ValueError(f"Missing request evidence: {effort}/{case['case_id']}")
            for request in case["requests"]:
                if (request["model"], request["effort"], request["max_tokens"]) != ("gpt-5", effort, 128000):
                    raise ValueError(f"Unexpected effective generation options: {request}")
    for a, b in zip(low["cases"], high["cases"], strict=True):
        if a["case_id"] != b["case_id"] or a["requests"][0]["schema_digest"] != b["requests"][0]["schema_digest"]:
            raise ValueError("Case order or primary output contract differs")
        if a["requests"][0]["stable_message_digests"] != b["requests"][0]["stable_message_digests"]:
            raise ValueError("Stable primary context differs")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "comparison.json").write_text(json.dumps({"low": low, "high": high}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# GPT-5 Effort Comparison", "", "One fresh generation per case and effort; identical 128,000 model-maximum output ceiling.",
             "Times include repairs. Pipeline time also includes setup, validation, materialization and grading.",
             "Output includes reasoning; non-reasoning output is not an exact count of visible text tokens.", "",
             "| Case | Result low / high | Grade low / high | Pipeline s low / high | Model s low / high | Input low / high | Output low / high | Reasoning low / high | Calls low / high |",
             "|---|---|---|---|---|---|---|---|---|"]
    for a, b in zip(low["cases"], high["cases"], strict=True):
        cells = [a["case_id"]]
        for key in ("status", "grade", "pipeline_seconds", "model_seconds", "input_tokens", "output_tokens", "reasoning_tokens", "model_calls"):
            cells.append(" / ".join("n/a" if case[key] is None else f"{case[key]:.2f}" if isinstance(case[key], float) else str(case[key]) for case in (a, b)))
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "## Observed Ranges", ""])
    for cohort in (low, high):
        lines.append(f"### {cohort['run_id']}")
        for key in ("pipeline_seconds", "model_seconds", "input_tokens", "output_tokens", "reasoning_tokens"):
            values = [case[key] for case in cohort["cases"]]
            lines.append(f"- {key}: min={min(values):.2f}, median={median(values):.2f}, max={max(values):.2f}, sum={sum(values):.2f}")
    lines.extend(["", "## Retained Previews", "", "Unapproved DEV test artifacts, not published releases.", ""])
    for effort, cohort in [("low", low), ("high", high)]:
        for case in cohort["cases"]:
            preview = case["preview"]
            if not preview:
                lines.append(f"- {effort}/{case['case_id']}: no admitted prototype")
                continue
            if not args.subnet_id:
                lines.append(f"- {effort}/{case['case_id']}: {preview['scenario_id']} ({preview['webspace_id']})")
                continue
            query = urlencode({"intent": "webspace.open", "zone": "lo", "subnet_id": args.subnet_id,
                               "webspace_id": preview["webspace_id"], "space_kind": "development",
                               "expected_scenario_id": preview["scenario_id"], "try_local_hub": "1"})
            lines.append(f"- [{effort}/{case['case_id']}]({args.client_url}?{query})")
    (args.output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
