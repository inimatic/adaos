"""Retain a terminal Root response for diagnosis; never apply it."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.llm.llm_client import get_response_job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reason", choices=("runner_interruption", "grader_failure", "provider_diagnosis"), default="runner_interruption")
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev" or args.output.exists():
        parser.error("Requires a DEV node and a new evidence path")
    init_ctx(Settings.from_sources())
    response = get_response_job(args.job_id, base_url=args.base_url)
    if response.get("status") not in {"succeeded", "failed", "cancelled", "canceled", "incomplete"}:
        parser.error("Job is not terminal; response has not been retained")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump({"kind": "terminal_root_response_not_applied", "reason": args.reason, "job_id": args.job_id,
                   "response": response}, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({"status": response["status"], "output_chars": len(response.get("output_text") or ""),
                      "evidence": str(args.output)}))


if __name__ == "__main__":
    main()
