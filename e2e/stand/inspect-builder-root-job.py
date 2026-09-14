"""Retain one original Root response and validate replay without applying or resubmitting."""

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.builder.prototype import candidate_status
from adaos.sdk.llm.llm_client import get_response_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repair", action="store_true", help="Explicitly replay through Builder, allowing its bounded repair")
    parser.add_argument("--creation", type=Path, help="Original browser creation receipt; required for repair")
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev" or not re.fullmatch(r"workbench_test_[a-z0-9_]+", args.scenario):
        parser.error("Requires an owned workbench TEST on a DEV node")
    if not re.fullmatch(r"llm_job_[a-zA-Z0-9_]+", args.job):
        parser.error("Expected a Root job identifier")
    if args.repair:
        receipt = json.loads(args.creation.read_text(encoding="utf-8")) if args.creation else {}
        created = receipt.get("created_test") or {}
        if created.get("id") != args.scenario or created.get("result", {}).get("project", {}).get("created_by") != "builder.user":
            parser.error("Repair requires this stand's exact native creation receipt")
    init_ctx(Settings.from_sources())
    logging.disable(logging.DEBUG)
    session = candidate_status(f"scenario.{args.scenario}", webspace_id="desktop-dev")["session"]
    if session.get("scenario_id") != args.scenario or session.get("ui_revision") != args.revision:
        parser.error("Source scenario/revision mismatch")
    root = Path(session["artifact_root"])
    request_path = root / "llm_jobs" / f"{args.job}.request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("scenario_id") != args.scenario or request.get("job_id") != args.job:
        parser.error("Original request identity mismatch")
    output = args.output.resolve()
    if not output.is_relative_to(Path("e2e/artifacts/builder").resolve()):
        parser.error("Evidence must stay in e2e/artifacts/builder")
    output.mkdir(parents=True, exist_ok=False)

    def write(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    job = get_response_job(args.job, timeout=15)
    write("root-response.json", job)
    write("original-request.json", request)
    write("source-brief.json", session.get("accepted_prototype_brief"))
    handler = root.parent.parent / "skills/builder_skill/handlers/main.py"
    spec = importlib.util.spec_from_file_location("builder_root_replay_review", handler)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    dynamic = json.loads(request["messages"][-1]["content"])["builder_request"]
    result = builder._replay_failed_llm_webui_result(
        session=session, job_id=args.job, request_text=dynamic["instruction"],
        expected_ui_revision=args.revision, previous_preview=session.get("preview_state") or {},
        before_webui=json.loads((root / "webui.json").read_text(encoding="utf-8")),
    )
    write("replay-validation.json", result)
    summary = {"schema": "adaos.e2e.root_job_review.v1", "scope": "read/replay validation, not applied or accepted",
               "request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
               "status": job.get("status"), "telemetry": job.get("_protocol"),
               "replay_ok": result.get("ok"), "error": result.get("error"), "detail": result.get("detail")}
    write("review.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    if args.repair:
        from adaos.adapters.builder.legacy_dev_skill import LegacyDevSkillPrototypeExecution
        payload = {"text": dynamic["instruction"], "webspace_id": "desktop-dev", "auto_apply": True,
                   "retry_job_id": args.job, "expected_ui_revision": args.revision,
                   "conversation_context": {"locale": "ru"},
                   "_meta": {"thread_id": f"prompt-project:scenario:{args.scenario}",
                             "conversation_id": session.get("topic_ref", {}).get("conversation_id"),
                             "prototype_request_source": "e2e", "locale": "ru"}}
        write("explicit-recovery-intent.json", payload)
        result = LegacyDevSkillPrototypeExecution().submit_turn(payload, timeout_seconds=900)
        write("builder-recovery.json", result)
        print(json.dumps({key: result.get(key) for key in ("ok", "status", "error", "detail", "replay")}, ensure_ascii=False))
        if not result.get("ok"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
