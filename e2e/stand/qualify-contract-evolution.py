"""Qualify Contract Evolution on this machine using fresh owned local stores."""

import argparse
from pathlib import Path
import json

from adaos.e2e.contract_evolution import qualify_contract_evolution
from adaos.services.artifact_pipeline.storage import atomic_write_json


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--owned-root", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
repo = Path.cwd().resolve()
if not args.owned_root.resolve().is_relative_to(repo / ".tmp"):
    parser.error("--owned-root must be a fresh directory under repository .tmp")
if not args.output.resolve().is_relative_to(repo / "e2e/artifacts"):
    parser.error("--output must be under e2e/artifacts")
report = qualify_contract_evolution(args.owned_root, repo_root=repo)
atomic_write_json(args.output, report)
print(json.dumps({"passed": report["passed"], "output": str(args.output),
                  "ticket_id": report["ticket_id"], "candidate_id": report["candidate_id"]}))
