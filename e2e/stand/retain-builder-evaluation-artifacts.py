"""Retain immutable pre-interaction evaluation artifacts for owned DEV tests."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _evaluation_application_context, validate_builder_e2e_record
from adaos.services.agent_context import get_ctx
from adaos.services.resources.prototype import PrototypeResourceService, prototype_webui_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    init_ctx(Settings.from_sources())
    dev = Path(get_ctx().paths.dev_scenarios_dir()).resolve()
    for checkpoint in sorted(args.run.glob("checkpoints/*/attempt-*.json")):
        value = json.loads(checkpoint.read_text(encoding="utf-8"))
        ownership = value.get("cleanup", {})
        if not ownership.get("test") or ownership.get("acceptance") != "not_approved":
            continue
        if not any(step["id"] == "validate" and step["status"] == "passed" for step in value["steps"]):
            continue
        created = next(step["output"] for step in value["steps"] if step["id"] == "create")
        source = Path(created["artifact_root"]).resolve()
        if source.parent != dev or source.name != created["scenario_id"]:
            raise ValueError("Checkpoint source is not the expected DEV scenario")
        webui = json.loads((source / "webui.json").read_text(encoding="utf-8"))
        meta = webui["ui"]["application"]["desktop"]["pageSchema"]["meta"]["builder"]
        types = []
        def collect(value):
            if isinstance(value, dict):
                if value.get("kind") == "resourceQuery" and str(value.get("resourceType", "")).startswith("prototype."):
                    if value["resourceType"] not in types:
                        types.append(value["resourceType"])
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(webui)
        project_ref = f"project:{created['scenario_id']}"
        revision = str(meta.get("ui_revision") or meta.get("proto") or "")
        artifact = {"schema": "adaos.builder.prototype_evaluation_artifact.v1", "project_ref": project_ref,
                    "revision": revision, "webui": webui,
                    "prototype_resources": PrototypeResourceService().evaluation_snapshots(
                        project_ref=project_ref, revision=revision, webui_digest=prototype_webui_digest(webui), resource_types=types)}
        application = _evaluation_application_context(value["context"], project_ref=project_ref)
        if application:
            artifact["application"] = application
        validate_builder_e2e_record(artifact["schema"], artifact)
        output = args.run / "evidence" / "evaluation-artifacts" / f"{checkpoint.parent.name}-{checkpoint.stem}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as target:
            json.dump(artifact, target, ensure_ascii=False, indent=2)
            target.write("\n")
        print(json.dumps({"case": checkpoint.parent.name, "attempt": checkpoint.stem, "resources": len(types), "output": str(output)}), flush=True)


if __name__ == "__main__":
    main()
