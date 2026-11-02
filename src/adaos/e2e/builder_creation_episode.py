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
from adaos.services.ai.service import TERMINAL as AI_TERMINAL


SCHEMA = "adaos.e2e.creation_episode.v1"
PROFILES = {"direct": "default", "v1": "s2-inline-typed-v1"}
TERMINAL = set(AI_TERMINAL) | {"canceled"}  # Legacy spelling, not a successful result.


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def creation_design(order, *, qualification_only=False):
    """A diagnostic cell cannot silently become a comparative productivity trial."""
    if qualification_only:
        if order != ['v1']:
            raise ValueError('Single-arm qualification requires exactly one v1 cell')
        return {'scope': 'single-arm CE regression qualification; one visible task; NOT a productivity comparison',
                'title': '[TEST] CE regression qualification',
                'cache': 'natural provider cache; one cell, report fresh/cached separately'}
    if order not in (['direct', 'v1'], ['direct', 'v1', 'v1', 'direct']):
        raise ValueError('Use the declared matched pair or counterbalanced pilot')
    return {'scope': 'matched CE component ablation; one visible task; NOT statistical noninferiority',
            'title': '[TEST] Matched creation productivity',
            'cache': 'natural provider cache; declared arm order, report fresh/cached separately'}


def runtime_identity(dev, name):
    """Pin active package contents, not rebuild timestamps or A/B slot names."""
    runtime = Path(dev) / 'skills/.runtime' / name
    marker = read(runtime / 'current_runtime.json')
    source = runtime / marker['runtime_bucket'] / 'slots' / marker['slot'] / 'src/skills' / name
    files = {str(path.relative_to(source)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(source.rglob('*')) if path.is_file()
             and path.suffix in {'.py', '.json', '.yaml', '.yml', '.txt'}
             and '__pycache__' not in path.parts and 'tests' not in path.relative_to(source).parts}
    if 'handlers/main.py' not in files:
        raise ValueError('Active package source is unavailable')
    return {'version': marker['version'], 'files': files}


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

    # Lazy package activation must settle before freezing active source contents.
    # These existing read ports neither change project selection nor invoke AI.
    call('get_model_settings', {'object_type': 'scenario', 'object_id': 'builder'})
    api('POST', '/api/tools/call', {'tool': 'builder_skill:get_workspace_binding',
        'arguments': {'webspace_id': host}, 'context': {'webspace_id': host, 'current_scenario': 'builder'}})
    before = attempts(state)
    if any(row["status"] not in TERMINAL for row in before.values()):
        raise ValueError("Active AI work must finish before a serial creation cell")
    model = "gpt-5.6-luna"
    manifest = read(bundle / "run.json")
    order = [row['case_id'].rsplit('-', 1)[-1] for row in manifest['cases']]
    design = creation_design(order, qualification_only=os.getenv('ADAOS_E2E_CREATION_QUALIFICATION_ONLY') == '1')
    prior = None
    prior_ref = os.getenv("ADAOS_E2E_CREATION_PRIOR_REF")
    if prior_ref:
        prior_path = (repo_root / prior_ref).resolve()
        if not prior_path.is_relative_to((repo_root / "e2e/artifacts/builder").resolve()):
            raise ValueError("Prior expenditure must be retained Builder evidence")
        previous = read(prior_path)
        if (not isinstance(previous.get('total_provider_calls'), int)
                or isinstance(previous['total_provider_calls'], bool) or previous['total_provider_calls'] < 0):
            raise ValueError("Prior provider expenditure is missing or invalid")
        prior = {"ref": prior_ref, "sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest(),
                 "provider_calls": previous['total_provider_calls']}
    if (prior or {}).get('provider_calls', 0) + len(order) * 4 > 16:
        raise ValueError("Prior calls plus worst-case fresh allowance exceed the overall 16-call pilot cap")
    # Written before generation. Subsequent cells must see identical product bits.
    pins = {"core": api("GET", "/health/ready")["adaos"],
            "client_commit": _run_command(["git", "rev-parse", "HEAD"], repo_root / "src/adaos/integrations/adaos-client"),
            "model": model, "resource": "ai-resource:personal-chatgpt",
            "builder_runtime": runtime_identity(dev, 'builder_skill'),
            "control_runtime": runtime_identity(dev, 'builder_sdk_control_skill'),
            "fixture_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "evaluation_commit": _run_command(["git", "rev-parse", "HEAD"], repo_root),
            "suite_sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted(source.parent.iterdir()) if path.is_file()},
            "browser_sources": {name: hashlib.sha256((repo_root / "e2e/stand/browser" / name).read_bytes()).hexdigest()
                for name in ("builder-creation-request.mjs", "builder-creation-infrastructure.mjs", "prototype-navigation.mjs",
                             "timestamp-caption-grammar.mjs")},
            "scope": design['scope'],
            "order": order, "calls_per_turn": 4, "prior_partial": prior, "overall_provider_limit": 16,
            "timeout_seconds": 720, "oracle_timeout_seconds": 240,
            "terminal_delivery_grace_seconds": 30,
            "promotion_margins": {"success_loss": 0, "safety_loss": 0, "extra_calls": 0,
                                  "extra_fresh_tokens": 0, "extra_output_tokens": 0, "extra_wall_seconds": 0},
            "stopping": "No code/model/source changes within cohort, no manual repair, no retry after ambiguous submission; stop if active work remains or accounting/budget changes",
            "cache": design['cache'],
            "cost": "subscription usage only; no invented API bill; no model judge",
            "reasoning_and_output_budget": "unchanged project/provider defaults, captured effective options in exact journals"}
    plan = bundle / "creation-preregistration.json"
    if plan.exists():
        if read(plan) != pins:
            raise ValueError("Creation preregistration drift")
    else:
        _write_json(plan, pins)
    ordinal = inputs["ordinal"]
    if not isinstance(ordinal, int) or not 0 <= ordinal < len(order) or pins["order"][ordinal] != arm:
        raise ValueError("Unexpected counterbalanced cell")
    # Reserve the full worst-case turn allowance before its one chat send. These
    # are allowance slots, NOT a claim that unused slots incurred inference.
    budget = RequestBudget(bundle / "creation-allowance.sqlite", maximum=len(order)*4, limits={"allowance": len(order)*4})
    if budget.snapshot()["attempted_requests"] != ordinal * 4:
        raise ValueError("Counterbalanced cells cannot be skipped or replayed")
    if any(read(path).get('accounting_findings') for path in (bundle / 'creation').glob('*/result.json')):
        raise ValueError("Prior accounting failure forbids further inference")
    allowance = [budget.reserve("allowance", f"{project}:{i}") for i in range(4)]
    _write_json(output / "allowance.json", {"slots": allowance, "maximum_actual_provider_calls": 4})
    created = call("create_project", {"object_type": "scenario", "object_id": project,
        "title": design['title'], "description": "Owned visible CE qualification; synthetic data.",
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
               "timeout_seconds": 720, "state_dir": str(state), "dev_dir": str(dev)}
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
        from adaos.services.builder.timestamp_labels import caption_grammar
        _write_json(output / "oracle-input.json", {"project": project, "accepted": accepted,
            "timestamp_caption_grammar":caption_grammar(),
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
