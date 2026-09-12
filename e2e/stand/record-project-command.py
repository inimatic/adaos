"""Retain local project lifecycle CLI evidence without changing its public API."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from dotenv import load_dotenv

from adaos.e2e.stand import redact_value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    verbs = {"trial", "candidate", "trial-decide", "promote", "push", "status"}
    if os.getenv("ENV_TYPE") != "dev" or command[:2] != ["dev", "project"] or len(command) < 4 or command[2] not in verbs:
        parser.error("Only explicit DEV-node project lifecycle commands are admitted")
    output = args.output.resolve()
    if not output.is_relative_to(root / "e2e/artifacts"):
        parser.error("Evidence must remain in e2e/artifacts")
    output.mkdir(parents=True, exist_ok=False)
    invocation = [sys.executable, "-X", "utf8", "-c", "from adaos.apps.cli.app import app; app()", *command]
    if "--json" not in invocation:
        invocation.append("--json")
    started = time.monotonic()
    result = subprocess.run(invocation, cwd=root, env=os.environ.copy(), capture_output=True, text=True, encoding="utf-8")
    (output / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (output / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = None
    receipt = redact_value({"command": command, "exit_code": result.returncode,
                            "duration_s": round(time.monotonic() - started, 3), "payload": payload})
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "exit_code": result.returncode, "duration_s": receipt["duration_s"]}))
    return result.returncode or int(payload is None)


if __name__ == "__main__":
    raise SystemExit(main())
