"""Start Automation for the exact user-accepted DEV Builder design."""

import argparse
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.sdk.builder import automation, workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()
    load_dotenv()
    if os.environ.get("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    init_ctx(Settings.from_sources())
    current = workflow.get_state("scenario", "builder")
    acceptance = current["prototype"].get("acceptance") or {}
    if acceptance.get("revision") != "071":
        parser.error("Requires user-accepted Builder design 071")
    brief = args.brief.read_text(encoding="utf-8")
    result = automation.start(
        object_type="project", object_id="builder", webspace_id="desktop-dev",
        implementation_brief=brief, brief_path=str(args.brief.resolve()),
        change_set_id=current["change_set"]["change_set_id"],
        agent_profile={"provider": "openai-codex-cli", "model": args.model,
                       "reasoning_effort": args.effort},
        mcp={"enabled": False},
    )
    session = result.get("session") or {}
    receipt = {"ok": result.get("ok"), "duplicate": result.get("duplicate"),
               "session_id": session.get("session_id"),
               "task_id": session.get("current_task_id"), "status": session.get("status"),
               "prototype_revision": acceptance["revision"],
               "brief_digest": hashlib.sha256(brief.encode("utf-8")).hexdigest(),
               "agent_profile": session.get("agent_profile"),
               "context_control": session.get("context_control")}
    _write_json(args.output, receipt)
    print({key: receipt[key] for key in ("ok", "duplicate", "task_id", "status")})


if __name__ == "__main__":
    main()
