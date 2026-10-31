"""One source-pinned creation cell on the existing Builder E2E rail.

The control is today's default direct semantic route, not a reconstructed old
release. Projects are fresh; v1 projects are never downgraded. Only the ordinary
browser composer submits generation. Acceptance never enters model context.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time


SCHEMA = "adaos.e2e.creation_episode.v1"
PROFILES = {"direct": "default", "v1": "s2-inline-typed-v1"}
TERMINAL = {"completed", "failed", "cancelled", "canceled"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def attempts(state):
    """Read metadata only. Never copy credentials or arbitrary provider output."""
    result = {}
    for path in (Path(state) / "ai").glob("*/ai.sqlite3"):
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            for item in db.execute("SELECT id,request_id,status,usage,telemetry,error FROM attempts"):
                row = dict(item)
                for field in ("usage", "telemetry"):
                    row[field] = json.loads(row[field])
                if row["id"] in result:
                    raise ValueError("Duplicate AI attempt identity")
                result[row["id"]] = row
    return result


def episode_attribution(episodes):
    violations, receipts = [], []
    for episode in episodes:
        for violation in episode.get("accounting_findings", []):
            violations.append({"code": violation, "project": episode["scenario_id"]})
        receipts.extend(episode.get("provider_attempts", []))
    return {"status": "failed" if violations else "passed", "violations": violations,
            "observed_model_calls": sum(row["telemetry"].get("provider_calls", 0) for row in receipts),
            "unique_receipt_count": len(receipts), "receipts": receipts,
            "scope": "All new attempts joined to exact owned Core/legacy journals; no historical totals"}


def execute(inputs, context, *, repo_root):
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.e2e.builder import _write_json, _run_command
    from adaos.e2e.builder_host import step_builder_host
    from adaos.e2e.request_budget import RequestBudget
    from adaos.services.agent_context import get_ctx
    from adaos.services.builder.prototype_publications import PrototypePublications
    import requests

    if os.getenv("ENV_TYPE") != "dev" or context.get("profile") != "generic":
        raise ValueError("Creation pilot requires a generic DEV development run")
    arm = inputs["arm"]
    if arm not in PROFILES:
        raise ValueError("Unknown creation arm")
    bundle = Path(context["bundle_dir"]).resolve()
    output = bundle / "creation" / f"{context['case_id']}-{context['repetition']}"
    output.mkdir(parents=True, exist_ok=False)  # interruption never resubmits
    repo_root = Path(repo_root).resolve()
    state = Path(get_ctx().paths.state_dir()).resolve()
    dev = Path(get_ctx().paths.dev_dir()).resolve()
    source = (repo_root / inputs["prompt_ref"]).resolve()
    if not source.is_relative_to(repo_root / "e2e/builder/development/creation"):
        raise ValueError("Creation task must be a visible development fixture")
    prompt = source.read_text(encoding="utf-8")
    host = step_builder_host(inputs, context)
    project = "test_creation_" + hashlib.sha256(
        f"{context['run_id']}:{context['case_id']}:{context['repetition']}".encode()).hexdigest()[:18]
    if (dev / "projects" / project).exists() or (dev / "scenarios" / project).exists():
        raise ValueError("Creation requires a fresh owned project")
    hub, client = "http://127.0.0.1:8777", "http://127.0.0.1:8100/"
    session = requests.Session()
    session.trust_env = False
    session.headers["X-AdaOS-Token"] = resolve_control_token(base_url=hub)

    def api(method, path, payload=None):
        response = session.request(method, hub + path, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()

    def call(name, args=None):
        return api("POST", "/api/tools/call", {"tool": "builder_sdk_control_skill:" + name,
            "arguments": args or {"object_type": "project", "object_id": project},
            "context": {"webspace_id": host, "current_scenario": "builder"}})["result"]

    before = attempts(state)
    if any(row["status"] not in TERMINAL for row in before.values()):
        raise ValueError("Active AI work must finish before a serial creation cell")
    model = "gpt-5.6-luna"
    # Written before generation. Subsequent cells must see identical product bits.
    pins = {"core": api("GET", "/health/ready")["adaos"],
            "client_commit": _run_command(["git", "rev-parse", "HEAD"], repo_root / "src/adaos/integrations/adaos-client"),
            "model": model, "resource": "ai-resource:personal-chatgpt",
            "builder_runtime": read(dev / "skills/.runtime/builder_skill/current_runtime.json"),
            "control_runtime": read(dev / "skills/.runtime/builder_sdk_control_skill/current_runtime.json"),
            "fixture_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "evaluation_commit": _run_command(["git", "rev-parse", "HEAD"], repo_root),
            "suite_sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted(source.parent.iterdir()) if path.is_file()},
            "browser_sources": {name: hashlib.sha256((repo_root / "e2e/stand/browser" / name).read_bytes()).hexdigest()
                for name in ("builder-creation-request.mjs", "builder-creation-infrastructure.mjs", "prototype-navigation.mjs")},
            "scope": "matched CE component ablation; one visible task, two repetitions, NOT statistical noninferiority",
            "order": ["direct", "v1", "v1", "direct"], "calls_per_turn": 4,
            "timeout_seconds": 720, "oracle_timeout_seconds": 240,
            "promotion_margins": {"success_loss": 0, "safety_loss": 0, "extra_calls": 0,
                                  "extra_fresh_tokens": 0, "extra_output_tokens": 0, "extra_wall_seconds": 0},
            "stopping": "No code/model/source changes within cohort, no manual repair, no retry after ambiguous submission; stop if active work remains or accounting/budget changes",
            "cache": "natural provider cache; counterbalanced order, report fresh/cached separately",
            "cost": "subscription usage only; no invented API bill; no model judge",
            "reasoning_and_output_budget": "unchanged project/provider defaults, captured effective options in exact journals"}
    plan = bundle / "creation-preregistration.json"
    if plan.exists():
        if read(plan) != pins:
            raise ValueError("Creation preregistration drift")
    else:
        _write_json(plan, pins)
    ordinal = inputs["ordinal"]
    if not isinstance(ordinal, int) or not 0 <= ordinal < 4 or pins["order"][ordinal] != arm:
        raise ValueError("Unexpected counterbalanced cell")
    # Reserve the full worst-case turn allowance before its one chat send. These
    # are allowance slots, NOT a claim that unused slots incurred inference.
    budget = RequestBudget(bundle / "creation-allowance.sqlite", maximum=16, limits={"allowance": 16})
    if budget.snapshot()["attempted_requests"] != ordinal * 4:
        raise ValueError("Counterbalanced cells cannot be skipped or replayed")
    allowance = [budget.reserve("allowance", f"{project}:{i}") for i in range(4)]
    _write_json(output / "allowance.json", {"slots": allowance, "maximum_actual_provider_calls": 4})
    created = call("create_project", {"object_type": "scenario", "object_id": project,
        "title": "[TEST] Matched creation productivity", "description": "Owned visible CE component ablation; synthetic data.",
        "webspace_id": host})
    call("set_llm_profile", {"object_type": "project", "object_id": project, "model": "chatgpt/" + model})
    configured = call("set_prototype_generation_profile", {"object_type": "project", "object_id": project, "profile": PROFILES[arm]})
    selected = call("select_preview", {"object_type": "project", "object_id": project, "webspace_id": host})
    settings = call("get_model_settings")
    preferences = call("get_prompt_context", {"object_type": "scenario", "object_id": project})
    if (settings.get("prototype_resource") != pins["resource"] or settings.get("prototype_model") != model
            or preferences.get("prototype_generation_profile") != PROFILES[arm]):
        raise ValueError("Exact project/model/profile selection did not persist")
    _write_json(output / "setup.json", {"created": created, "selected": selected, "configured": configured, "model": settings})
    request = {"project": project, "model": model, "builderHost": host, "prompt": prompt,
               "timeout_seconds": 720, "state_dir": str(state)}
    _write_json(output / "input.json", request)
    env = {key: value for key, value in os.environ.items() if not key.startswith("ADAOS_E2E_")}
    env.update(ADAOS_E2E_HUB_URL=hub, ADAOS_E2E_HUB_TOKEN=session.headers["X-AdaOS-Token"],
        ADAOS_E2E_CLIENT_URL=client, ADAOS_E2E_SUBNET_ID=dev.name,
        ADAOS_E2E_CREATION_INPUT=str(output / "input.json"), ADAOS_E2E_OUTPUT=str(output / "chat"))

    def browser(script, timeout):
        try:
            completed = subprocess.run(["node", str(repo_root / "e2e/stand/browser" / script)],
                cwd=repo_root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired as error:
            captured = [part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part or ""
                        for part in (error.stdout, error.stderr)]
            (output / (script + ".log")).write_text("".join(captured) + "\nProbe execution budget exhausted.\n", encoding="utf-8")
            return 124
        (output / (script + ".log")).write_text(completed.stdout + completed.stderr, encoding="utf-8")
        return completed.returncode

    started = time.perf_counter()
    browser("builder-creation-request.mjs", 900)
    chat = read(output / "chat/chat-report.json") if (output / "chat/chat-report.json").is_file() else {
        "ok": False, "submitted": None, "failure": "Probe did not return a receipt; submission may be ambiguous, never retry"}
    workflow = call("get_workflow")
    _write_json(output / "workflow.json", workflow)
    # Copy only this project's journals, then join exact request/job identities.
    roots = [dev / "scenarios" / project / "llm_jobs"]
    for directory in (state / "builder/generation_journals").glob("*"):
        if (directory / "owner.json").is_file() and read(directory / "owner.json").get("source_scope", {}).get("object_id") == project:
            roots.append(directory)
    journal_texts = []
    for i, directory in enumerate(roots):
        for path in sorted(directory.glob("*.json")):
            raw = path.read_text(encoding="utf-8")
            journal_texts.append(raw)
            _write_json(output / "journals" / str(i) / path.name, json.loads(raw))
    corpus = "\n".join(journal_texts)
    after = attempts(state)
    observed = [row for key, row in after.items() if key not in before and
                (json.dumps(key) in corpus or json.dumps(row["request_id"]) in corpus)]
    for row in observed:
        if "provider_calls" in row["telemetry"]:
            row["usage"]["model_calls"] = row["telemetry"]["provider_calls"]
    foreign = [key for key in after if key not in before and key not in {row["id"] for row in observed}]
    findings = []
    if foreign: findings.append("unjoined_new_attempts")
    if chat.get("submitted") and not observed: findings.append("submitted_without_attempt_receipt")
    calls = sum(row["telemetry"].get("provider_calls", 0) for row in observed)
    if calls > 4: findings.append("provider_budget_exceeded")
    for row in observed:
        telemetry = row["telemetry"]
        if row["status"] not in TERMINAL: findings.append("active_attempt_remains")
        if telemetry.get("tool_calls"): findings.append("unexpected_mcp_calls")
        if telemetry.get("wire", {}).get("model") != model: findings.append("actual_model_not_pinned")
        if "provider_calls" not in telemetry: findings.append("provider_accounting_missing")
    for slot, row in zip(allowance, observed):
        budget.observe(slot, job_id=row["id"], status=row["status"])
    _write_json(output / "provider-attempts.json", {"attempts": observed, "unjoined": foreign, "findings": findings})
    prototype = workflow.get("prototype") or {}
    accepted = None
    if prototype.get("head_revision") == "001":
        publication_ref = prototype.get("publication_ref")
        if publication_ref:
            publication = PrototypePublications(state).load(publication_ref)
            if publication["seed"]["project_ref"] != "scenario:" + project:
                raise ValueError("Foreign accepted publication")
            accepted = {"project": project, "publication_ref": publication_ref,
                "webui": publication["compiled"]["webui"], "semantic_document": publication["document"]}
        else:
            root = dev / "scenarios" / project
            if (root / "ui_revisions/current.txt").read_text(encoding="utf-8").strip() != "001":
                raise ValueError("Direct route head/source mismatch")
            accepted = {"project": project, "webui": read(root / "webui.json"),
                        "semantic_document": read(root / "semantic.webui.json")}
    oracle = {"ok": False, "not_run": "No accepted Alpha"}
    if accepted:
        _write_json(output / "oracle-input.json", {"project": project, "accepted": accepted,
            "preview_webspace_id": host + "-dev"})
        env.update(ADAOS_E2E_CREATION_INPUT=str(output / "oracle-input.json"), ADAOS_E2E_OUTPUT=str(output / "oracle"))
        browser("builder-creation-infrastructure.mjs", 240)
        oracle = read(output / "oracle/functional-browser.json") if (output / "oracle/functional-browser.json").is_file() else {
            "ok": False, "failure": "Oracle did not return a receipt; see retained probe log"}
    result = {"schema": SCHEMA, "ok": chat.get("ok") is True and oracle.get("ok") is True and not findings,
        "scenario_id": project, "arm": arm, "ordinal": ordinal, "head": prototype,
        "submitted": chat.get("submitted"), "generation_ms": chat.get("elapsed_ms"),
        "request_to_qualified_alpha_ms": round((time.perf_counter() - started) * 1000) if oracle.get("ok") else None,
        "elapsed_ms_including_failed_outcome": round((time.perf_counter() - started) * 1000),
        "provider_attempts": observed, "accounting_findings": findings, "actual_provider_calls": calls,
        "oracle_passed": oracle.get("ok"), "chat_failure": chat.get("failure"),
        "manual_application_edits": 0, "clarifications": 0, "evidence_ref": str((output / "result.json").relative_to(bundle))}
    _write_json(output / "result.json", result)
    return result
