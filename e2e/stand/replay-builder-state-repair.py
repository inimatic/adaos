"""Replay retained state-repair input without modifying a project or its verdict."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.builder.prototype import apply_state_repair
from adaos.sdk.llm.llm_client import submit_response_job, wait_response_job
from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--effort", choices=["minimal", "low", "medium", "high"], required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--response", type=Path, help="Validate an existing response without another model call")
    args = parser.parse_args()
    load_dotenv()
    base = Path(os.getenv("ADAOS_BASE_DIR") or ".adaos").resolve()
    if os.getenv("ENV_TYPE") != "dev" or not (base / "node.yaml").is_file():
        parser.error("An existing enrolled ENV_TYPE=dev node is required")
    raw = args.request.read_bytes()
    captured = json.loads(raw)
    generation = captured["generation"]
    dynamic = json.loads(captured["messages"][-1]["content"])["semantic_repair"]
    checkpoint = json.loads(args.checkpoint.read_bytes())
    brief = checkpoint["context"]["outputs"]["design"]["patch"]["prototype_brief"]
    if brief["digest"] != dynamic["prototype_brief"]["brief_digest"]:
        parser.error("Checkpoint Brief does not match the captured model context")
    options = generation["options"]
    schema_id = options["text"]["format"]["schema"]["properties"]["schema"]["enum"]
    if generation["model"] != "gpt-5" or schema_id != ["adaos.builder.state_repair.v1"]:
        parser.error("Only retained GPT-5 state-repair requests are supported")
    args.output.mkdir(parents=True, exist_ok=False)

    def write(name: str, value: object) -> None:
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    kwargs = {key: value for key, value in options.items() if key in inspect.signature(submit_response_job).parameters}
    kwargs.update(model=generation["model"], reasoning={"effort": args.effort}, request_id=f"state-repair-calibration-{uuid.uuid4().hex}")
    write("input.json", {"source": str(args.request.resolve()), "source_sha256": hashlib.sha256(raw).hexdigest(),
                         "messages": captured["messages"], "options": kwargs, "changed_variable": "reasoning.effort"})
    started = time.perf_counter()
    if args.response:
        job = json.loads(args.response.read_bytes())
    else:
        init_ctx(Settings.from_sources())
        job = submit_response_job(captured["messages"], **kwargs)
        write("submitted.json", job)
        if job.get("status") != "succeeded":
            job = wait_response_job(job["job_id"], base_url=job.get("_client", {}).get("base_url"), timeout_s=420)
    write("response.json", job)
    report = {"elapsed_s": time.perf_counter() - started, "effort": args.effort,
              "scope": "retained repair replay; not a fresh cohort or user-task pass", "compiled": False,
              "reused_response": str(args.response) if args.response else None}
    try:
        patch = json.loads(job["output_text"])
        merged = apply_state_repair(dynamic["candidate"], patch, dynamic["validation_findings"])
        compile_semantic_prototype_candidate(merged, brief=brief)
        report["compiled"] = True
        write("merged.json", merged)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    write("review.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
