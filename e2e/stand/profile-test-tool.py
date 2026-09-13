"""Profile an existing TEST skill's read tool without changing its runtime source."""

import argparse
import asyncio
import cProfile
import json
import os
from pathlib import Path
import pstats
import time

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.apps.api.tool_bridge import _skill_manager_for_context
from adaos.e2e.builder import _write_json
from adaos.services.agent_context import get_ctx
from adaos.services.personalization_runtime import personalization_access_service
from adaos.services.policy.caller import verified_caller
import adaos.services.skill.manager as manager_module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    load_dotenv()
    root = (Path.cwd() / "e2e/artifacts/builder").resolve()
    output = args.output.resolve()
    if os.getenv("ENV_TYPE") != "dev" or not args.skill.startswith("test_") or not output.is_relative_to(root) or output.exists():
        parser.error("Requires an existing TEST skill, DEV and a new evidence directory")
    init_ctx(Settings.from_sources())
    manager = asyncio.run(_skill_manager_for_context(get_ctx()))
    status = manager.dev_runtime_status(args.skill)
    manifest = json.loads(Path(status["resolved_manifest"]).read_text(encoding="utf-8"))
    method = "new_record_token"
    if manifest["tools"][method].get("side_effects") != "none":
        raise ValueError("Probe requires a manifest-declared read tool")
    output.mkdir(parents=True)
    original = manager_module.execute_tool
    samples = []
    call_profile = cProfile.Profile()
    def profiled(*positional, **keywords):
        return call_profile.runcall(original, *positional, **keywords)
    manager_module.execute_tool = profiled
    try:
        outer = cProfile.Profile()
        for index in range(6):
            started = time.perf_counter()
            # Explicit trusted local profiling identity, not HTTP authorization evidence.
            with verified_caller(personalization_access_service().owner):
                result = outer.runcall(manager.run_dev_tool, args.skill, method, {})
            samples.append({"index": index, "ms": (time.perf_counter() - started) * 1000, "ok": bool(result.get("token"))})
        for name, profile in (("execution", call_profile), ("manager", outer)):
            with (output / f"{name}.txt").open("w", encoding="utf-8") as stream:
                pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(45)
        _write_json(output / "profile.json", {"scope": "Isolated process profile, not live HTTP latency", "skill": args.skill, "samples": samples})
        print(json.dumps(samples))
    finally:
        manager_module.execute_tool = original


if __name__ == "__main__":
    main()
