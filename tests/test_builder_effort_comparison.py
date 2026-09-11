import json
import runpy
from pathlib import Path

import pytest


HELPER = runpy.run_path(str(Path(__file__).parents[1] / "e2e/stand/compare-builder-effort.py"))


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("repetition", [1, 2])
def test_effort_summary_separates_repair_without_double_counting(tmp_path, repair, repetition):
    def write(ref, value):
        target = tmp_path / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    primary_usage = {"output_tokens": 100, "reasoning_tokens": 40}
    total_usage = {"output_tokens": 150, "reasoning_tokens": 60} if repair else primary_usage
    telemetry = {"usage": total_usage, "timing": {"execution_ms": 2000}}
    artifacts = [{"kind": "terminal", "evidence_ref": "terminal.json"},
                 {"kind": "request", "evidence_ref": "request.json"}]
    request = {"messages": [{"role": "user", "content": "\u041f\u0440\u0438\u043c\u0435\u0440"}],
               "generation": {"model": "gpt-5", "options": {
                   "reasoning": {"effort": "low"}, "max_tokens": 128000}}}
    if repair:
        telemetry["usage_breakdown"] = {"primary": primary_usage}
        telemetry["repair"] = {"usage": {"output_tokens": 50, "reasoning_tokens": 20},
                               "timing": {"execution_ms": 1000}}
        artifacts.append({"kind": "semantic_repair_request", "evidence_ref": "repair.request.json"})
        write("repair.request.json", request)
    write("run.json", {"run_id": "example"})
    write("report.json", {"case_results": ["case.json"], "summary": {}, "metrics": {}})
    write("case.json", {"case_id": "example", "repetition": repetition, "status": "passed", "duration_ms": 5000,
                        "metrics": {**total_usage, "fresh_input_tokens": 40, "cached_input_tokens": 60,
                                    "model_calls": 2 if repair else 1},
                        "steps": [{"id": "grade", "output": {"score": 1.0}}]})
    write(f"evidence/generation/example-attempt-{repetition:02}.json", {"model_io_artifacts": artifacts, "repair_attempted": repair})
    write("terminal.json", {"diagnostic": {"telemetry": telemetry}})
    write("request.json", request)

    case = HELPER["summarize"](tmp_path)["cases"][0]
    assert case["repetition"] == repetition
    assert case["model_seconds"] == (3 if repair else 2)
    assert case["pipeline_seconds"] == 5
    assert case["input_tokens"] == 100
    assert case["cached_input_tokens"] == 60
    assert sum(call["usage"]["output_tokens"] for call in case["calls"]) == case["output_tokens"]
    assert case["non_reasoning_output_tokens"] == (90 if repair else 60)
    assert len(case["requests"]) == (2 if repair else 1)
    assert all(item["max_tokens"] == 128000 and item["message_chars"] == [6] for item in case["requests"])
