"""Run existing browser probes against a case-owned live Prototype."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from adaos.e2e.builder_lifecycle import _target


PROBES = {"review": ("prototype-review.mjs", "review.json"),
          "commands": ("prototype-command-probe.mjs", "commands.json"),
          "interactions": ("prototype-interactions.mjs", "review.json"),
          "capabilities": ("prototype-capabilities.mjs", "capabilities.json")}


def execute(inputs: Mapping[str, Any], context: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.e2e.builder import _write_json
    from adaos.services.agent_context import get_ctx

    kind, identifier = _target(inputs, context)
    probe = str(inputs.get("probe") or "review")
    if kind != "scenario" or probe not in PROBES:
        raise ValueError("Prototype browser step requires a scenario and a declared probe")
    outputs = context.get("outputs") or {}
    created = next((item for item in outputs.values() if item.get("scenario_id") == identifier and item.get("draft_id")), None)
    preview = next((item["review_preview"] for item in outputs.values() if item.get("review_preview", {}).get("scenario_id") == identifier), None)
    if not created or not preview or preview.get("stage") != "prototype" or not preview.get("test"):
        raise ValueError("Prototype browser step requires this case's validated test preview")
    if any(item.get("acceptance", {}).get("decision") == "accepted" for item in outputs.values()):
        raise ValueError("accepted prototypes cannot be mutated by a Prototype probe")
    root = Path(context["bundle_dir"]).resolve()
    parent = root / "browser" / f"{context['case_id']}-{context['repetition']}" / str(context["step_id"])
    attempt = 1
    while (parent / f"attempt-{attempt:02}").exists():
        attempt += 1
    output = parent / f"attempt-{attempt:02}"
    if not output.resolve().is_relative_to(root):
        raise ValueError("browser evidence path escapes this run")
    output.mkdir(parents=True)
    checkpoint = output / "input.json"
    _write_json(checkpoint, {"kind": "active_prototype_review", "run_id": context["run_id"], "context": dict(context),
        "steps": [{"id": "create", "output": created}],
        "cleanup": {"test": True, "status": "review_in_progress", "acceptance": "not_approved",
                    "owned_artifacts": context["owned_artifacts"], "previews": [preview]}})
    hub = str(inputs.get("hub_url") or "http://127.0.0.1:8778")
    subnet = str(inputs.get("subnet_id") or get_ctx().config.subnet_id_value)
    env = {**{key: value for key, value in os.environ.items() if not key.startswith("ADAOS_E2E_")},
           "ADAOS_E2E_CHECKPOINT": str(checkpoint), "ADAOS_E2E_OUTPUT": str(output),
           "ADAOS_E2E_HUB_URL": hub, "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub),
           "ADAOS_E2E_SCENARIO_ID": identifier, "ADAOS_E2E_WEBSPACE_ID": preview["webspace_id"],
           "ADAOS_E2E_SUBNET_ID": subnet, "ADAOS_E2E_LOCALE": str(context["locale"]),
           "ADAOS_E2E_CLIENT_URL": str(inputs.get("client_url") or "http://127.0.0.1:8100/"),
           "ADAOS_E2E_FIELD_TYPE": str(inputs.get("field_type") or ""),
           "ADAOS_E2E_EMPTY_STATES": "0", "ADAOS_E2E_READONLY": "0", "ADAOS_E2E_MEDIA_TESTS": "0"}
    script, receipt_name = PROBES[probe]
    script_path = repo_root / "e2e/stand/browser" / script
    try:
        completed = subprocess.run(["node", str(script_path)], cwd=repo_root, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=float(inputs.get("timeout_seconds") or 240))
    except subprocess.TimeoutExpired as exc:
        captured = [item.decode("utf-8", errors="replace") if isinstance(item, bytes) else item or ""
                    for item in (exc.stdout, exc.stderr)]
        (output / "probe.log").write_text("".join(captured) + "\nBrowser probe exceeded its execution budget.\n", encoding="utf-8")
        return {"ok": False, "probe": probe, "timed_out": True, "scenario_id": identifier,
                "evidence_ref": (output / "probe.log").relative_to(root).as_posix()}
    (output / "probe.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    receipt = output / receipt_name
    return {"ok": completed.returncode == 0 and receipt.is_file(), "probe": probe,
            "exit_code": completed.returncode, "scenario_id": identifier, "webspace_id": preview["webspace_id"],
            "evidence_ref": (receipt if receipt.is_file() else output / "probe.log").relative_to(root).as_posix(),
            "evidence_refs": [path.relative_to(root).as_posix() for path in sorted(output.iterdir()) if path.is_file()]}
