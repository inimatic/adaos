"""Live Prototype-generation ablation through the native DEV Builder boundary.

This is not a chat-routing/Automation/Trial benchmark. A process-local evaluation
adapter projects _builder_llm_messages before the native request journal and Root
submission. Native parsing, validation, bounded repairs, revision writing and
Prototype resource materialization are used unchanged. No DEV handler is patched
on disk and no installed application is modified.
"""
from __future__ import annotations

import argparse
import copy
from functools import wraps
import hashlib
import importlib.util
import json
import logging
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from adaos.e2e.builder import _write_json
from adaos.services.builder_context import canonical, digest, estimate_tokens
from adaos.services.builder.prototype_context_delivery import compile_prototype_messages
from adaos.services.builder.prototype_mcp import indexed_messages, redact_credentials, retrieval_index


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def source_fingerprints(repo):
    files = ["src/adaos/services/builder/semantic_prototype.py", "src/adaos/services/builder_intent.py",
        "src/adaos/services/builder/prototype_context.py", "src/adaos/services/builder/semantic_bindings.py",
        "src/adaos/sdk/llm/llm_client.py", "src/adaos/services/builder/model_controls.py",
        "src/adaos/sdk/builder/prototype.py", "src/adaos/services/builder/prototype_mcp.py",
        "src/adaos/sdk/builder/preview.py", "src/adaos/services/builder/cbs.py",
        "src/adaos/services/builder/workbench.py",
        "src/adaos/abi/builder.cbs_compilation.v1.schema.json",
        "src/adaos/integrations/adaos-client/src/assets/i18n/en.json",
        "src/adaos/integrations/adaos-client/src/assets/i18n/ru.json",
        "src/adaos/services/builder/prototype_pipeline.py",
        "src/adaos/services/builder/prototype_task_context.py", "src/adaos/e2e/request_budget.py",
        "e2e/stand/run-prototype-context-composition.py",
        "e2e/stand/browser/prototype-delivery-latency.mjs",
        "e2e/builder/development/context-ablation/prototype-guidance-oracles.json",
        "src/adaos/services/root/service.py",
        "src/adaos/services/workspace_registry.py",
        "src/adaos/services/scenario/webspace_runtime.py",
        "src/adaos/services/scenario/webspace_components/materialization.py",
        "src/adaos/services/scenario/webspace_components/materialization_runtime.py",
        "src/adaos/services/pending_action_projection.py",
        "src/adaos/services/yjs/gateway_ws.py",
        "src/adaos/integrations/adaos-client/src/app/renderer/widgets/table.widget.component.ts",
        "src/adaos/integrations/adaos-backend/backend/root-llm-stream-timing.ts",
        "src/adaos/integrations/adaos-backend/backend/root-llm-job-budget.ts",
        "src/adaos/e2e/prototype_context_ablation.py", "e2e/stand/repair-prototype-pipeline-cohort.py",
        "src/adaos/e2e/prototype_mcp_eval.py", "e2e/stand/qualify-prototype-context-ablation.py",
        "src/adaos/integrations/adaos-backend/backend/prototype-context-library.ts",
        "src/adaos/integrations/adaos-backend/backend/prototype-guidance-cache.ts",
        "src/adaos/integrations/adaos-backend/backend/app.ts",
        "src/adaos/integrations/adaos-backend/backend/prototype-context-library.json",
        "src/adaos/integrations/adaos-client/src/app/runtime/page-modal.service.ts",
        "src/adaos/integrations/adaos-client/src/app/runtime/focus-runtime.service.ts",
        "src/adaos/integrations/adaos-client/src/app/runtime/layout-render-plan.ts",
        *[f"src/adaos/integrations/adaos-client/src/app/renderer/desktop/desktop.component.{extension}"
          for extension in ("ts", "html", "scss")],
        "src/adaos/integrations/adaos-client/src/app/app.component.html",
        "src/adaos/integrations/adaos-client/src/app/app.component.scss",
        "e2e/stand/browser/prototype-usability.mjs",
        *[f"e2e/stand/browser/{name}.mjs" for name in ("prototype-client-health", "prototype-navigation", "prototype-review",
            "prototype-capabilities", "prototype-interactions", "prototype-interaction-fields", "prototype-procurement",
            "prototype-procurement-lines", "prototype-procurement-composition", "prototype-procurement-fields")]]
    return {file: "sha256:" + hashlib.sha256((repo / file).read_bytes()).hexdigest() for file in files}


def compile_experiment_messages(messages, *, strategy, generation_options, context_mode="advisory", mcp_available=False,
                                oracle_fragment_ids=None):
    if context_mode == "composition":
        from adaos.services.builder.prototype_pipeline import compile_messages
        from adaos.services.builder.prototype_mcp import library_snapshot
        if strategy not in {"full-no-mcp", "full-mcp", "core-indexed", "task-aware", "task-aware-compact", "oracle"}:
            raise ValueError("Unknown composition arm")
        source = copy.deepcopy(messages)
        snapshot = library_snapshot()
        for message in source:
            try:
                body = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            stable = body.get("stable_builder_context") if isinstance(body, dict) else None
            if isinstance(stable, dict):
                # Shared library identity lets the full+MCP arm make valid pinned
                # calls. Both full controls have byte-identical messages; only
                # their actual provider tool definitions differ.
                stable.pop("prototype_retrieval", None)
                stable["prototype_library"] = {"library_digest": snapshot["library_digest"],
                    "authority": snapshot["authority"], "policy": "If tools are supplied, they read exact public guidance from this pinned library; otherwise use inline guidance."}
                message["content"] = canonical(body)
        projected, receipt = compile_messages(source, strategy="full" if strategy.startswith("full-") else strategy,
            retrieval_available=mcp_available, oracle_fragment_ids=oracle_fragment_ids)
        core = copy.deepcopy(source)
        for message in core:
            try:
                body = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            stable = body.get("stable_builder_context") if isinstance(body, dict) else None
            if isinstance(stable, dict):
                for key in ("semantic_invariants", "prototype_retrieval", "platform_accents", "prototype_library"):
                    stable.pop(key, None)
                message["content"] = canonical(body)
        return projected, {**receipt, "arm": strategy, "core_sha256": digest(core), "must_keep_complete": True,
            "estimated_tokens": estimate_tokens(canonical(projected) + canonical(redact_credentials(generation_options))) + 256,
            "omitted": [{"key": key, "reason": "exact_guidance_retrievable_via_mcp"} for key in receipt["omitted"]]}
    if context_mode == "pipeline":
        from adaos.services.builder.prototype_pipeline import compile_messages
        if strategy not in {"full", "adaptive"}:
            raise ValueError("Unknown pipeline arm")
        projected, receipt = compile_messages(messages, strategy="full" if strategy == "full" else "hybrid-focused",
                                               retrieval_available=mcp_available)
        # Mandatory core includes every dynamic/task message and output contract;
        # it excludes only the known library and its retrieval delivery metadata.
        core = copy.deepcopy(messages)
        for message in core:
            try:
                body = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            stable = body.get("stable_builder_context") if isinstance(body, dict) else None
            if isinstance(stable, dict):
                stable.pop("semantic_invariants", None)
                stable.pop("prototype_retrieval", None)
            message["content"] = canonical(body)
        return projected, {**receipt, "core_sha256": digest(core), "must_keep_complete": True,
            "estimated_tokens": estimate_tokens(canonical(projected) + canonical(redact_credentials(generation_options))) + 256,
            "omitted": [{"key": key, "reason": "exact_optional_guidance_via_mcp"} for key in receipt["omitted"]]}
    if context_mode == "advisory":
        return compile_prototype_messages(messages, strategy=strategy, generation_options=generation_options)
    if context_mode != "indexed" or strategy not in {"full", "adaptive"}:
        raise ValueError("Unknown Prototype experiment projection")
    # Both arms see the identical dimension catalog and availability statement.
    full = copy.deepcopy(messages)
    for message in full:
        try:
            body = json.loads(message["content"])
        except (ValueError, TypeError):
            continue
        stable = body.get("stable_builder_context") if isinstance(body, dict) else None
        if isinstance(stable, dict) and isinstance(stable.get("semantic_invariants"), dict):
            stable["prototype_retrieval"] = retrieval_index(available=mcp_available)
            message["content"] = canonical(body)
    indexed, indexed_receipt = indexed_messages(full, available=mcp_available)
    projected = full if strategy == "full" else indexed
    options = redact_credentials(generation_options)
    return projected, {**indexed_receipt, "strategy": "full" if strategy == "full" else "adaptive-indexed",
        "input_sha256": digest(messages), "output_sha256": digest(projected),
        "core_sha256": digest(indexed), "must_keep_complete": True,
        "estimated_tokens": estimate_tokens(canonical(projected) + canonical(options)) + 256,
        "full_estimated_tokens": estimate_tokens(canonical(full) + canonical(options)) + 256,
        "omitted": [] if strategy == "full" else [{"key": key, "reason": "indexed_platform_guidance"} for key in indexed_receipt["omitted"]],
        "authority_policy": "user_facts_refs_provider_schema_stage_limits_inline; exact_platform_guidance_indexed"}


def verify_root_job_budget_support(client):
    """An invalid model and invalid budget prove delivery support without a paid call."""
    from adaos.services.root.client import RootHttpError
    primary, config = client._root_http_client()
    primary_url = client._normalize_root_base_url(primary.base_url)
    headers = client._identity_headers(client._current_ctx(), config)
    receipts = []
    for index, base_url in enumerate(client._root_llm_base_urls(primary)):
        http = client._root_http_for_base(primary, primary_url, base_url, index)
        try:
            http.request("POST", "/v1/llm/jobs", json={"model": "__invalid_builder_preflight_model__",
                "provider_timeout_ms": 900001, "input": []}, headers=headers or None, timeout=15)
        except RootHttpError as exc:
            code = client._root_http_error_code(exc)
            upstream = (exc.payload or {}).get("upstream", {}) if isinstance(exc.payload, dict) else {}
            upstream_json = upstream.get("json", {})
            if code == "llm_proxy_upstream_failed" and upstream.get("status") == 400 and isinstance(upstream_json, dict):
                code = upstream_json.get("error")
            if exc.status_code != 400 or code != "invalid_provider_timeout":
                raise ValueError(f"Root route {base_url} does not confirm job budget support") from exc
            receipts.append({"base_url": base_url, "status": 400, "error": "invalid_provider_timeout", "paid_call": False})
        else:
            raise ValueError("Root accepted an invalid job budget")
    return receipts


def verify_root_retry_budget_support(client):
    """Invalid transport metadata must fail before model/provider access."""
    from adaos.services.root.client import RootHttpError
    primary, config = client._root_http_client()
    headers = client._identity_headers(client._current_ctx(), config)
    try:
        primary.request("POST", "/v1/llm/jobs", json={"model": "__invalid_builder_preflight_model__",
            "provider_max_attempts": 0, "input": []}, headers=headers or None, timeout=15)
    except RootHttpError as exc:
        code = client._root_http_error_code(exc)
        upstream = (exc.payload or {}).get("upstream", {}) if isinstance(exc.payload, dict) else {}
        upstream_json = upstream.get("json", {})
        if code == "llm_proxy_upstream_failed" and upstream.get("status") == 400 and isinstance(upstream_json, dict):
            code = upstream_json.get("error")
        if exc.status_code != 400 or code != "invalid_provider_max_attempts":
            raise ValueError("Root does not validate strict provider-attempt budgets before paid delivery") from exc
        return {"base_url": primary.base_url, "status": 400, "error": "invalid_provider_max_attempts", "paid_call": False}
    raise ValueError("Root accepted an invalid provider-attempt budget")


def remap(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [remap(item, old, new) for item in value]
    if isinstance(value, dict):
        return {remap(key, old, new): remap(item, old, new) for key, item in value.items()}
    return value


def native_builder(path):
    spec = importlib.util.spec_from_file_location("builder_prototype_context_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_scenario_id(output, order):
    """Run ownership includes the parent run, not just a reused cohort name."""
    return "test_protoctx_" + digest(str(Path(output).resolve()))[7:17] + f"_{order:02d}"


def generation_configuration(model="gpt-5", reasoning_effort="low"):
    """One authority for primary generation, native repair and recorded options."""
    if not model.strip() or reasoning_effort not in {"minimal", "low", "medium", "high"}:
        raise ValueError("An explicit model and supported reasoning effort are required")
    environment = {"ADAOS_CONTROL_URL": "http://127.0.0.1:8777", "ADAOS_BUILDER_LLM_MODEL": model,
        "ADAOS_BUILDER_LLM_REASONING_EFFORT": reasoning_effort, "ADAOS_BUILDER_LLM_MAX_TOKENS": "128000",
        "ADAOS_BUILDER_LLM_SYNC_USE_JOBS": "1", "ADAOS_BUILDER_LLM_JOB_TIMEOUT_S": "1200"}
    options = {"text": {"verbosity": "low"}, "max_tokens": 128000, "reasoning": {"effort": reasoning_effort}}
    metadata = {"builder_llm_model": model, "builder_llm_reasoning_effort": reasoning_effort,
        "builder_llm_max_output_tokens": 128000}
    return environment, options, metadata


def evaluation_model_controls(builder, model, reasoning_effort):
    """Bridge GPT-6 controls on this isolated module, never the installed skill.

    The legacy handler only recognizes GPT-5 reasoning models. Letting GPT-6
    silently use provider defaults would invalidate the matched-effort study.
    Prompt assembly, compilation and repair logic remain the native functions.
    """
    metadata = {"builder_llm_reasoning_effort": reasoning_effort}
    expected = {"effort": reasoning_effort}
    native_reasoning = builder._builder_llm_reasoning_for_model
    native_temperature = builder._builder_llm_temperature_for_model
    reasoning_before = native_reasoning(model, metadata)
    if reasoning_before == expected and native_temperature(model) is None:
        return {"applied": False, "model": model, "reasoning": reasoning_before}
    if not model.startswith("gpt-6"):
        if reasoning_before != expected:
            raise ValueError("Native Builder does not preserve the requested reasoning effort")
        return {"applied": False, "model": model, "reasoning": reasoning_before}
    if reasoning_effort == "minimal":
        raise ValueError("GPT-6 evaluation requires low, medium or high effort")
    builder._builder_llm_reasoning_for_model = lambda selected, _meta=None: (
        expected.copy() if selected == model else native_reasoning(selected, _meta))
    builder._builder_llm_temperature_for_model = lambda selected, *, repair=False: (
        None if selected == model else native_temperature(selected, repair=repair))
    return {"applied": True, "scope": "process-local model capability bridge only",
        "model": model, "native_reasoning": reasoning_before, "reasoning": expected, "temperature": None}


def matched_reference_checks(reference, output, plan):
    """Fail before paid calls if anything other than model/effort has drifted."""
    previous = read(reference / "plan.json")
    keys = ("instruction_sha256", "brief_sha256", "provider_schema_sha256",
        "initial_webui_sha256", "native_builder_sha256", "hint_budget_tokens", "order", "acceptance")
    checks = {key: plan[key] == previous[key] for key in keys}
    for arm in ("full", "adaptive"):
        current, original = (read(folder / f"planned-{arm}-input.json") for folder in (output, reference))
        checks[f"{arm}_messages"] = current["messages"] == original["messages"]
        checks[f"{arm}_purposes"] = current["purposes"] == original["purposes"]
        current_options, original_options = copy.deepcopy(current["options"]), copy.deepcopy(original["options"])
        for options in (current_options, original_options):
            options.pop("model", None)
            options.pop("reasoning", None)
        checks[f"{arm}_other_generation_options"] = current_options == original_options
    receipt = {"reference": str(reference), "allowed_changes": ["model", "reasoning_effort"], "checks": checks}
    _write_json(output / "reference-parity.json", receipt)
    if not all(checks.values()):
        raise ValueError(f"Frozen experiment drift: {[key for key, ok in checks.items() if not ok]}")
    return receipt


def validate_resumption_plan(current, previous):
    keys = ("model", "effort", "max_output_tokens", "max_attempts", "hint_budget_tokens", "order",
        "instruction_sha256", "brief_sha256", "provider_schema_sha256", "initial_webui_sha256",
        "native_builder_sha256", "delivery_source_sha256", "acceptance", "acceptance_source_sha256",
        "provider_timeout_ms", "runtime_source_fingerprints", "browser_policy", "mcp", "context_mode",
        "oracle_fragment_ids", "oracle_source_sha256", "request_budget_db", "root_base_url", "provider_max_attempts")
    drift = [key for key in keys if current.get(key) != previous.get(key)]
    if drift:
        raise ValueError(f"Refusing to resume a changed experiment: {drift}")


def recover_submitted_primary(builder, client, sample, expected_messages, *, baseline, brief, project_ref, wait_job=None):
    """Read the already paid Root response and run native parsing, never resubmit."""
    if (sample / "call-02-request.json").exists():
        raise ValueError("Resume currently supports a retained primary, not an incomplete repair chain")
    request = read(sample / "call-01-request.json")
    submitted = read(sample / "call-01-submission.json")
    if request["messages"] != expected_messages:
        raise ValueError("Retained primary messages differ from the frozen assembled input")
    envelope = client.get_response_job(submitted["job_id"], timeout=15)
    if envelope.get("status") in {"queued", "running"}:
        envelope = (wait_job or client.wait_response_job)(submitted["job_id"], timeout_s=1200)
    _write_json(sample / "call-01-terminal.json", redact_credentials(envelope))
    _write_json(sample / "primary-recovery.json", {"job_id": submitted["job_id"],
        "primary_resubmitted": False, "retained_messages_verified": True, "terminal_status": envelope.get("status")})
    call = {"request": request, "submitted": submitted, "terminal": envelope}
    if envelope.get("status") != "succeeded":
        return {"ok": False, "error": "retained_provider_job_failed", "detail": str(envelope.get("error"))}, call
    raw = envelope.get("output_text") or ""
    try:
        result = builder._parse_llm_webui_transform_output(output_text=raw,
            previous_preview=baseline.get("preview_state") or {}, before_webui=baseline,
            prototype_brief=brief, project_ref=project_ref, output_mode="semantic_v2",
            request_id=request["options"]["request_id"], job_id=submitted["job_id"])
    except Exception as exc:
        result = {"ok": False, "last_response": raw, "error": "llm_response_parse_failed",
            "detail": str(exc), "validation": {"ok": False, "detail": str(exc), "findings": getattr(exc, "findings", [])}}
    return result, call


def acceptance(result, brief, task="inventory-procurement-ru"):
    """Preregistered independent structural gates; not an LLM self-grade."""
    from adaos.sdk.builder.prototype import model_context
    semantic = result.get("semantic_document") or {}
    resources, views, commands = (semantic.get(key) or [] for key in ("resources", "views", "commands"))
    text = json.dumps(semantic, ensure_ascii=False).casefold()
    checks = {
        "native_compiler_and_postconditions": result.get("ok") is True,
        "rich_interface_at_least_four_resources": len(resources) >= 4,
        "collections_and_editors": any(view.get("role") == "collection" for view in views)
            and any(view.get("role") == "editor" for view in views),
        "create_and_update_commands": ({operation.get("kind") for operation in brief.get("operations", [])}
            & {"create", "update", "delete", "transition"}).issubset({command.get("kind") for command in commands}),
        "reachable_query_controls": any(view.get("query_controls") for view in views),
        "linked_record_selection": any(view.get("selection_filter") for view in views),
        "numeric_quantity": any(field.get("value_type") == "number" for resource in resources for field in resource.get("fields", [])),
        "currency_is_visible": any(term in text for term in ("currency", "валют", "rub", "usd", "₽", "руб.", "$", "€")),
        "stock_and_procurement_visible": any(term in text for term in ("stock", "inventory", "остат", "запас"))
            and any(term in text for term in ("purchase", "procurement", "заявк", "закуп")),
        "supplier_comparison_visible": any(term in text for term in ("supplier", "поставщик")),
        "no_silent_capability_gap": not result.get("capability_gaps"),
        "russian_dictionary": bool(result.get("locale_dictionaries", {}).get("ru")),
    }
    if task == "support-desk-ru":
        for key in ("currency_is_visible", "stock_and_procurement_visible", "supplier_comparison_visible", "numeric_quantity"):
            checks.pop(key)
        checks["ticket_customer_agent_and_history"] = all(any(term in text for term in group) for group in (
            ("ticket", "обращ", "заявк"), ("customer", "клиент"), ("agent", "сотрудник", "исполнител"),
            ("comment", "комментар", "истори")))
        checks["explicit_state_examples"] = len(semantic.get("representative_states") or []) >= 2
    elif task != "inventory-procurement-ru":
        raise ValueError("Unknown preregistered task")
    return {"ok": all(checks.values()), "checks": checks,
        "required_references": len(model_context(brief, compact=True)["required_references"]),
        "resources": len(resources), "views": len(views), "commands": len(commands),
        "states": len(semantic.get("representative_states") or []),
        "scope": "structural admission only; browser and independent visual review are separate"}


class PreviewRestorationError(RuntimeError):
    """A failed restoration must stop the cohort, not become a model failure."""


@contextmanager
def preserved_preview(host, output):
    """Temporarily use one existing Preview and restore its exact pinned target."""
    from adaos.sdk.builder import preview
    from adaos.sdk.developer import projects
    binding = preview.get_binding(host)
    target = binding.get("preview_target") or {}
    if not all(target.get(key) for key in ("object_type", "object_id", "stage", "revision", "scenario_id")):
        raise ValueError("Benchmark requires an existing explicitly pinned restorable Preview")
    source = projects.resolve_root("scenario", target["scenario_id"]) / "webui.json"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    _write_json(output / "preview-before.json", binding)
    try:
        yield
    finally:
        restored = preview.select_target(target["object_type"], target["object_id"],
            stage=target["stage"], revision=target["revision"], source_webspace_id=host,
            follow_active=bool(target.get("follow_active")), via_owner=True)
        unchanged = hashlib.sha256(source.read_bytes()).hexdigest() == before
        _write_json(output / "preview-restored.json", {"result": restored,
            "protected_existing_source_unchanged": unchanged, "source_sha256_before": before})
        if not restored.get("ok") or not unchanged:
            raise PreviewRestorationError("Existing Preview restoration/protected-source verification failed")


def browser_review(builder, session, result, sample, created, subnet, run_id, *, host, selected_probes=None, observe_delivery=False):
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.sdk.builder import preview
    stages = {}
    started = time.perf_counter()
    root = Path(session["artifact_root"])
    webui = result["payload"]
    _write_json(root / "semantic.webui.json", result["semantic_document"])
    builder._write_scenario_locale_dictionaries(str(root), result["locale_dictionaries"])
    builder._write_webui_payload(str(root), webui)
    info = builder._write_ui_revision(session=session, request_text=session["source_idea"],
        patch={"operation": "llm_webui_transform", "change_id": session["active_change_id"]},
        before_webui=session["initial_webui"], after_webui=webui,
        preview_state=result["preview_state"], llm_result=result,
        llm_model=session.get("builder_llm_model", "gpt-5"), revision="001")
    revision = info["revision"]
    resource_receipt = builder._materialize_llm_prototype_resource(session=session,
        patch={"change_id": session["active_change_id"]}, revision=revision, webui=webui, llm_result=result)
    _write_json(sample / "resource-materialization.json", resource_receipt)
    _write_json(sample / "revision-recording.json", builder._record_prototype_revision(
        session, revision=revision, change_id=session["active_change_id"]))
    stages["revision_and_resource_seconds"] = round(time.perf_counter() - started, 3)
    delivery_env = {**os.environ, "ADAOS_E2E_HUB_URL": "http://127.0.0.1:8777",
        "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url="http://127.0.0.1:8777"),
        "ADAOS_E2E_SUBNET_ID": subnet, "ADAOS_E2E_CLIENT_URL": "http://127.0.0.1:8100/",
        "ADAOS_E2E_SCENARIO_ID": session["scenario_id"]}
    observer = None
    observer_output = sample / "delivery-latency"
    if observe_delivery:
        binding = preview.get_binding(host)
        delivery_env.update(ADAOS_E2E_WEBSPACE_ID=binding["preview_webspace_id"], ADAOS_E2E_OUTPUT=str(observer_output.resolve()))
        observer = subprocess.Popen(["node", "e2e/stand/browser/prototype-delivery-latency.mjs"], env=delivery_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.perf_counter() + 70
        while not (observer_output / "armed.json").exists() and observer.poll() is None and time.perf_counter() < deadline:
            time.sleep(.2)
        if not (observer_output / "armed.json").exists():
            observer.terminate()
            observer.wait(timeout=10)
            observer = None
            stages["warm_observer_error"] = "Could not arm before publication; no inferred Client latency"
    publication_epoch_ms = time.time() * 1000
    started = time.perf_counter()
    materialized = preview.select_target("scenario", session["scenario_id"],
        stage="prototype", revision=revision, source_webspace_id=host, via_owner=True)
    _write_json(sample / "preview-materialization.json", materialized)
    stages["preview_publication_seconds"] = round(time.perf_counter() - started, 3)
    stages["publication_started_epoch_ms"] = publication_epoch_ms
    stages["publication_completed_epoch_ms"] = time.time() * 1000
    if observer is not None:
        _write_json(observer_output / 'publication-ready.json', {'scenario_id': session['scenario_id'],
            'publication_completed_epoch_ms':stages['publication_completed_epoch_ms']})
    if observer is not None:
        try:
            observer.wait(timeout=130)
            visible = read(observer_output / "result.json")
            stages["warm_client"] = visible
            if visible.get("ok"):
                stages["publish_to_visible_seconds"] = round((visible["visible_at_epoch_ms"] - publication_epoch_ms) / 1000, 3)
                # Negative is possible: Client can paint while Hub finishes its receipt.
                stages["receipt_to_visible_seconds"] = round((visible["visible_at_epoch_ms"] - stages["publication_completed_epoch_ms"]) / 1000, 3)
                if visible.get('navigation_started_epoch_ms'):
                    stages['warm_navigation_to_visible_seconds'] = round((visible['visible_at_epoch_ms']-visible['navigation_started_epoch_ms'])/1000,3)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            observer.terminate(); observer.wait(timeout=10)
            stages["warm_observer_error"] = str(error)
    _write_json(sample / "delivery-timings.json", stages)
    if not materialized.get("ok"):
        raise ValueError("Owned Prototype materialization failed")
    owned_space = materialized["preview_webspace_id"]
    checkpoint = {"context": {"locale": "ru", "run_id": run_id},
        "steps": [{"id": "create", "output": {**created, "scenario_id": session["scenario_id"], "artifact_root": str(root)}}],
        "cleanup": {"test": True, "status": "retained_for_review", "acceptance": "not_approved",
            "owned_artifacts": [{"primary_ref": "scenario:" + session["scenario_id"], "project_id": session["scenario_id"]}],
            "previews": [{"webspace_id": owned_space, "scenario_id": session["scenario_id"], "test": True, "stage": "prototype"}]}}
    _write_json(sample / "checkpoint.json", checkpoint)
    env = {**os.environ, "ADAOS_E2E_CHECKPOINT": str((sample / "checkpoint.json").resolve()),
        "ADAOS_E2E_HUB_URL": "http://127.0.0.1:8777", "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url="http://127.0.0.1:8777"),
        "ADAOS_E2E_SUBNET_ID": subnet, "ADAOS_E2E_CLIENT_URL": "http://127.0.0.1:8100/"}
    statuses = {}
    qualification_started = time.perf_counter()
    probes = ["prototype-review", "prototype-capabilities", "prototype-procurement", "prototype-procurement-composition"]
    if any(command.get("kind") == "update" for command in result["semantic_document"].get("commands", [])):
        probes.append("prototype-interactions")
    if selected_probes is not None:
        if not selected_probes or len(set(selected_probes)) != len(selected_probes) or set(selected_probes) - set(probes):
            raise ValueError("Unknown, duplicate or empty selected browser probes")
        probes = list(selected_probes)
    for probe in probes:
        output = sample / probe
        env["ADAOS_E2E_OUTPUT"] = str(output.resolve())
        env.update(ADAOS_E2E_SCENARIO_ID=session["scenario_id"], ADAOS_E2E_WEBSPACE_ID=owned_space,
            ADAOS_E2E_LOCALE="ru", ADAOS_E2E_REVIEW_STAGE="prototype", ADAOS_E2E_EXPECTED_WEBUI=str(root / "webui.json"))
        checked = subprocess.run(["node", "e2e/stand/browser/" + probe + ".mjs"], env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=300)
        output.mkdir(parents=True, exist_ok=True)
        (output / "process.log").write_text(checked.stdout + checked.stderr, encoding="utf-8")
        statuses[probe] = checked.returncode
    stages["functional_probes_seconds"] = round(time.perf_counter() - qualification_started, 3)
    _write_json(sample / "delivery-timings.json", stages)
    return {"ok": all(value == 0 for value in statuses.values()), "probes": statuses, "timings": stages,
        "scenario_id": session["scenario_id"], "webspace_id": owned_space, "revision": revision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--fixture-run", type=Path)
    source_group.add_argument("--source-run", type=Path, help="Reuse the exact retained instruction from an owned ablation cohort")
    parser.add_argument("--task-file", type=Path, help="Frozen held-out task definition with id and instruction")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--defer-browser", action="store_true", help="Freeze generated candidates first; qualify all on one stable Client later")
    parser.add_argument("--primary-only", action="store_true", help="Pipeline confirmation: exactly one request per candidate, with no repair stage")
    parser.add_argument("--model", default="gpt-5", help="Explicit provider model; never automatically substituted")
    parser.add_argument("--reasoning-effort", default="low", choices=("minimal", "low", "medium", "high"))
    parser.add_argument("--reference-run", type=Path, help="Require exact frozen-input parity, except model/effort")
    parser.add_argument("--resume", action="store_true", help="Resume the same frozen cohort without resubmitting a retained primary")
    parser.add_argument("--host", help="Existing Builder with one explicitly paired, restorable Preview")
    parser.add_argument("--repetitions", type=int, default=2, choices=(1, 2))
    parser.add_argument("--mcp", choices=("off", "on"), default="off", help="Enable the two native, read-only Prototype guidance tools")
    parser.add_argument("--context-mode", choices=("advisory", "indexed", "pipeline", "composition"), default="advisory", help="Frozen context delivery experiment")
    parser.add_argument("--arms", nargs="+", help="Composition arm order, preregistered by the series driver")
    parser.add_argument("--oracle-file", type=Path)
    parser.add_argument("--request-budget-db", type=Path)
    parser.add_argument("--root-base-url", choices=("https://ru.api.inimatic.com", "https://api.inimatic.com"))
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    logging.disable(logging.INFO)
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Prototype ablation requires DEV")
    repo = Path.cwd().resolve()
    output = args.output.resolve()
    if not output.is_relative_to(repo / "e2e/artifacts/builder") or (output.exists() != args.resume):
        parser.error("Requires a new output, or an existing frozen output with --resume, below e2e/artifacts/builder")
    from adaos.apps.cli.app import Settings, init_ctx
    from adaos.sdk.builder import intent, prototype, workflow
    from adaos.sdk.developer import compositions, projects
    from adaos.sdk.llm import llm_client
    from adaos.services.agent_context import get_ctx
    if args.root_base_url:
        os.environ["ADAOS_ROOT_LLM_BASE_URL"] = args.root_base_url
        os.environ["ADAOS_ROOT_LLM_PREFER_GLOBAL"] = "0"
        # One known healthy plane; avoid ambiguous cross-plane resubmissions.
        llm_client._root_llm_base_urls = lambda _primary, **_kwargs: [args.root_base_url]
    init_ctx(Settings.from_sources())
    if not args.plan_only:
        if not args.host:
            parser.error("Live browser qualification requires an existing --host")
        from adaos.e2e.builder_host import require_builder_host
        require_builder_host(args.host)
        from adaos.sdk.builder import preview
        previous_target = preview.get_binding(args.host).get("preview_target") or {}
        if not all(previous_target.get(key) for key in ("object_type", "object_id", "stage", "revision", "scenario_id")):
            parser.error("Select a pinned restorable Preview before any live generation")
    environment, options, generation_metadata = generation_configuration(args.model, args.reasoning_effort)
    os.environ.update(environment)
    os.environ["ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED"] = "1" if args.mcp == "on" else "0"
    os.environ["ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY"] = "full"  # adapter invokes the same SDK projection once
    options.update(prototype.mcp_generation_options())
    options = redact_credentials(options)
    ctx = get_ctx()
    handler = Path(ctx.paths.dev_skills_dir()) / "builder_skill/handlers/main.py"
    builder = native_builder(handler)
    control_adapter = evaluation_model_controls(builder, args.model, args.reasoning_effort)
    if args.fixture_run:
        checkpoint = read(args.fixture_run / "checkpoints/inventory-procurement-ru/attempt-01.json")
        original = checkpoint["context"]["outputs"]["create"]
        if not checkpoint["cleanup"].get("test") or not original["scenario_id"].startswith("test_"):
            raise ValueError("An owned frozen Prototype fixture is required")
    # Historical source projects may have been legitimately cleaned up. Freeze
    # the shipped empty canvas, not a stale runtime path or an Automation UI.
    source_webui = repo / "src/adaos/scenario_templates/scenario_default/webui.json"
    protected = hashlib.sha256(source_webui.read_bytes()).hexdigest()
    initial = read(source_webui)
    task = "inventory-procurement-ru"
    if args.source_run:
        source_run = args.source_run.resolve()
        if not source_run.is_relative_to(repo / "e2e/artifacts/builder"):
            parser.error("Owned retained instruction required")
        instruction = (source_run / "instruction.txt").read_text(encoding="utf-8")
        source_plan = read(source_run / "plan.json")
        if digest(instruction) != source_plan["instruction_sha256"]:
            parser.error("Retained instruction digest mismatch")
        task = source_plan["task"]
    else:
        request = read(next((args.fixture_run / "evidence/model-io/inventory-procurement-ru-attempt-01").rglob("*.request.json")))
        instruction = json.loads(request["messages"][-1]["content"])["builder_request"]["instruction"]
    if args.task_file:
        definition = read(args.task_file)
        task, instruction = definition["id"], definition["instruction"]
        if task != "support-desk-ru" or not isinstance(instruction, str) or not instruction.strip():
            parser.error("Requires the frozen support-desk held-out task")
    oracle_ids = read(args.oracle_file)["tasks"][task] if args.oracle_file else None
    composition_arms = {"full-no-mcp", "full-mcp", "core-indexed", "task-aware", "oracle"}
    compact_pair = {"full-mcp", "task-aware-compact"}
    if args.context_mode == "composition":
        if (not args.arms or set(args.arms) not in (composition_arms, compact_pair)
                or len(args.arms) != len(set(args.arms)) or not args.request_budget_db or not args.root_base_url):
            parser.error("Composition requires five canonical arms or the two compact paired arms, a global budget and Root route")
        if set(args.arms) == composition_arms and not oracle_ids:
            parser.error("The five-arm composition requires a pinned oracle")
        if set(args.arms) == compact_pair and args.mcp != "on":
            parser.error("The compact comparison requires working MCP in both arms")
    if args.context_mode != "composition" and args.arms:
        parser.error("Explicit arms require composition mode")
    if args.primary_only and args.context_mode != "pipeline":
        parser.error("Primary-only requires the native pipeline experiment")
    task_title = "Служба поддержки [TEST]" if task == "support-desk-ru" else "Запасы и закупки [TEST]"
    brief = intent.compile_brief(instruction)
    output.mkdir(parents=True, exist_ok=args.resume)
    if not args.resume:
        _write_json(output / "initial-webui.json", initial)
        _write_json(output / "source-brief.json", brief)
        (output / "instruction.txt").write_text(instruction, encoding="utf-8")
    schema = prototype.semantic_provider_contract(version="v2", locales=("ru",), brief=brief)
    options["text"]["format"] = {"type": "json_schema", "strict": True, "schema": schema}
    meta = {"locale": "ru", "prototype_brief": brief, "builder_e2e_generation_contract": "semantic.v2",
        "builder_semantic_compiler": True, **generation_metadata}
    plan_session = {"id": "scenario.test_prototype_context", "scenario_id": "test_prototype_context",
        "title": task_title, "source_idea": instruction, "project_ref": "project:test_prototype_context",
        "ui_revision": "001", "webui_payload": remap(initial, "template-id", "test_prototype_context"),
        "builder_llm_model": args.model}
    plan_request = builder._builder_llm_webui_transform_request(session=plan_session,
        instruction=instruction, preview_state=initial.get("preview_state") or {}, _meta=meta)
    original_messages, purposes = builder._builder_llm_messages(plan_request, plan_request["user_prompt"])
    planned = {}
    arms = args.arms if args.context_mode == "composition" else ("full", "adaptive")
    for arm in arms:
        arm_options = copy.deepcopy(options)
        if arm == "full-no-mcp":
            for key in ("tools", "tool_choice", "max_tool_calls"):
                arm_options.pop(key, None)
        messages, receipt = compile_experiment_messages(original_messages, strategy=arm, generation_options=arm_options,
            context_mode=args.context_mode, mcp_available=args.mcp == "on" and arm != "full-no-mcp", oracle_fragment_ids=oracle_ids)
        assembled = {"messages": messages, "options": arm_options, "purposes": purposes}
        if args.resume:
            if assembled != read(output / f"planned-{arm}-input.json"):
                raise ValueError("Refusing to resume changed planned inputs")
        else:
            _write_json(output / f"planned-{arm}-input.json", assembled)
        planned[arm] = receipt
    if len({receipt["core_sha256"] for receipt in planned.values()}) != 1:
        raise ValueError("Matched mandatory cores differ")
    plan = {"schema": "adaos.e2e.prototype_context_ablation.v1", "task": task,
        "stage": "Prototype", "scope": "native_generation_compiler_repairs_resources_browser; not chat dispatch, Automation or publication",
        "model": args.model, "effort": args.reasoning_effort, "max_output_tokens": 128000,
        "mcp": args.mcp, "context_mode": args.context_mode, "library_digest": retrieval_index(available=False)["library_digest"],
        "max_attempts": 1 if args.primary_only else 2 if args.context_mode in {"pipeline", "composition"} else 3, "hint_budget_tokens": 512, "repetitions": args.repetitions,
        "order": [arm for repetition in range(args.repetitions) for arm in (arms if repetition % 2 == 0 else list(reversed(arms)))] if args.context_mode == "composition" else ["full", "adaptive"] if args.repetitions == 1 else ["full", "adaptive", "adaptive", "full"],
        "instruction_sha256": digest(instruction), "brief_sha256": digest(brief), "provider_schema_sha256": digest(schema),
        "initial_webui_sha256": digest(initial), "protected_source_sha256": protected,
        "native_builder_sha256": "sha256:" + hashlib.sha256(handler.read_bytes()).hexdigest(),
        "delivery_source_sha256": "sha256:" + hashlib.sha256((repo / "src/adaos/services/builder/prototype_context_delivery.py").read_bytes()).hexdigest(),
        "acceptance_source_sha256": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model_control_adapter": control_adapter,
        "provider_timeout_ms": 900000,
        "browser_policy": "deferred_frozen_candidates" if args.defer_browser else "inline",
        "runtime_source_fingerprints": source_fingerprints(repo),
        "planned": planned, "plan_only": args.plan_only,
        "acceptance": ["native compiler and exact Brief coverage", "task-grounded structural gates v2", "desktop/mobile tabs, filters, linked records and public compact panels", "desktop/mobile create request, reopen, submit for approval and independent persisted status", "desktop/mobile procurement line, supplier offer and exact independently persisted quantity", "generated generic update commands, if present: validation, edit, cancel and reload", "independent screenshot review"],
        "existing_apps": "read-only; only new test_ applications and their owned DEV previews may be written"}
    if args.context_mode == "composition":
        plan.update(oracle_fragment_ids=oracle_ids, oracle_source_sha256=digest(read(args.oracle_file)) if args.oracle_file else None,
            request_budget_db=str(args.request_budget_db.resolve()), root_base_url=args.root_base_url,
            provider_max_attempts=1, repairs="deferred single native repair; global repair budget=8")
    if task == "support-desk-ru":
        plan["acceptance"] = ["native compiler and exact Brief coverage", "four domain resources, collections/editors, query controls and linked selection",
            "populated and empty state examples", "desktop/mobile render, filters and linked record selection",
            "desktop/mobile generated update: invalid form, edit, cancel, independent persisted value and reload", "independent screenshot review"]
    if args.resume:
        previous = read(output / "plan.json")
        from adaos.e2e.runtime_amendment import validated_sources
        previous["runtime_source_fingerprints"] = validated_sources(repo, previous, output)
        previous["acceptance_source_sha256"] = plan["acceptance_source_sha256"]
        validate_resumption_plan(plan, previous)
        _write_json(output / "resume-plan.json", plan)
        (output / "resume-harness-source.py").write_bytes(Path(__file__).read_bytes())
    else:
        _write_json(output / "plan.json", plan)
        (output / "harness-source.py").write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
        (output / "delivery-source.py").write_bytes((repo / "src/adaos/services/builder/prototype_context_delivery.py").read_bytes())
    if args.reference_run:
        matched_reference_checks(args.reference_run.resolve(), output, plan)
    print(json.dumps({"stage": "plan", "tokens_estimate_not_usage": {arm: receipt["estimated_tokens"] for arm, receipt in planned.items()},
        "omitted": {arm: [item["key"] for item in receipt["omitted"]] for arm, receipt in planned.items()}}, ensure_ascii=False), flush=True)
    if args.plan_only:
        return 0
    _write_json(output / "root-delivery-preflight.json", verify_root_job_budget_support(llm_client))
    if args.context_mode == "composition":
        _write_json(output / "root-retry-cap-preflight.json", verify_root_retry_budget_support(llm_client))
    healthy_client = subprocess.run(["node", "e2e/stand/browser/prototype-client-health.mjs"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    _write_json(output / "client-health-preflight.json", json.loads(healthy_client.stdout))
    rows = []
    counts = {arm: 0 for arm in arms}
    if args.request_budget_db:
        from adaos.e2e.request_budget import RequestBudget
        request_budget = RequestBudget(args.request_budget_db)
    for order, arm in enumerate(plan["order"], 1):
        os.environ["ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED"] = "1" if args.mcp == "on" and arm != "full-no-mcp" else "0"
        if source_fingerprints(repo) != plan["runtime_source_fingerprints"]:
            raise ValueError("Runtime implementation changed during the frozen paired series")
        counts[arm] += 1
        sample = output / f"{order:02d}-{arm}"
        scenario = sample_scenario_id(output, order)
        if args.resume and (sample / "result.json").exists():
            retained = read(sample / "result.json")
            if retained["scenario_id"] != scenario or retained["arm"] != arm or retained["order"] != order:
                raise ValueError("Retained sample identity mismatch")
            rows.append(retained)
            print(json.dumps({"stage": "retained_sample", "sample": sample.name}), flush=True)
            continue
        retained_primary = args.resume and (sample / "call-01-submission.json").exists()
        sample.mkdir(exist_ok=retained_primary)
        if retained_primary:
            created = read(sample / "creation.json")
        else:
            if projects.resolve_root("scenario", scenario, required=False).exists() or compositions.resolve_root(scenario, required=False).exists():
                raise ValueError("Refusing to overwrite a Prototype sample")
            created = compositions.create_with_primary_component(scenario, kind="scenario", template="scenario_default",
                title=task_title, description="Isolated live Prototype context ablation", tags=["test", "prototype-context-ablation"])
            _write_json(sample / "creation.json", created)
        root = projects.resolve_root("scenario", scenario)
        baseline = remap(initial, "template-id", scenario)
        if retained_primary:
            if read(root / "webui.json") != baseline:
                raise ValueError("Retained primary source is no longer the frozen empty canvas")
            changed = workflow.get_state("scenario", scenario)
        else:
            builder._write_webui_payload(str(root), baseline)
            changed = workflow.transition("scenario", scenario, "plan_change_set", actor="agent:builder-e2e",
                metadata={"change_set_id": scenario, "request": instruction,
                    "issues": [{"issue_id": "prototype-context", "title": "Generate the fixed Prototype", "lane": "prototype",
                        "acceptance_criteria": ["Native compilation, exact frozen Brief coverage, and preregistered structural and functional browser gates. Full instruction is retained in change request and source-brief.json."]}]})["workflow"]
        session = {**plan_session, "id": "scenario." + scenario, "scenario_id": scenario,
            "project_ref": "project:" + scenario, "artifact_root": str(root), "webui_payload": baseline,
            "initial_webui": baseline, "active_change_id": changed["change"]["change_id"], "patches": []}
        calls, deliveries = [], []
        _write_json(sample / "session.json", session)
        native_messages = builder._builder_llm_messages
        native_submit, native_wait = llm_client.submit_response_job, llm_client.wait_response_job

        def projected_messages(request, prompt):
            messages, purposes = native_messages(request, prompt)
            delivery_options = {key: value for key, value in options.items()
                if arm != "full-no-mcp" or key not in {"tools", "tool_choice", "max_tool_calls"}}
            projected, receipt = compile_experiment_messages(messages, strategy=arm, generation_options=delivery_options,
                context_mode=args.context_mode, mcp_available=args.mcp == "on" and arm != "full-no-mcp", oracle_fragment_ids=oracle_ids)
            deliveries.append(receipt)
            _write_json(sample / f"delivery-{len(deliveries):02d}.json", receipt)
            return projected, purposes

        @wraps(native_submit)
        def submit(messages, **kwargs):
            if len(calls) >= (1 if args.context_mode == "composition" else plan["max_attempts"]):
                raise RuntimeError("Preregistered per-candidate request cap reached; refusing a paid call")
            if kwargs.get("provider_timeout_s") != 900:
                raise ValueError("Native Builder did not supply the preregistered Root job budget")
            if args.mcp == "on" and arm != "full-no-mcp":
                from adaos.services.builder.prototype_mcp import OPENAI_TOOLS
                tools = kwargs.get("tools") or []
                if (len(tools) != 1 or tools[0].get("type") != "mcp"
                    or set(tools[0].get("allowed_tools", [])) != set(OPENAI_TOOLS)
                    or not tools[0].get("authorization") or kwargs.get("tool_choice") != "auto"
                    or kwargs.get("max_tool_calls") != 40):
                    raise ValueError("Native Builder failed to deliver the exact preregistered MCP options")
            elif kwargs.get("tools"):
                raise ValueError("No-MCP control unexpectedly received tools")
            if args.context_mode == "composition":
                kwargs["provider_max_attempts"] = 1
                kwargs["timeout"] = 60
                slot = request_budget.reserve("primary", str(sample.relative_to(repo)) + ":primary")
                _write_json(sample / "budget-primary.json", {"ordinal": slot, "kind": "primary", "provider_max_attempts": 1})
            call = {"request": {"messages": copy.deepcopy(messages), "options": copy.deepcopy(kwargs)}}
            if args.context_mode == "composition":
                call["budget_slot"] = slot
            calls.append(call)
            _write_json(sample / f"call-{len(calls):02d}-request.json", redact_credentials(call["request"]))
            envelope = native_submit(messages, **kwargs)
            if args.context_mode == "composition":
                request_budget.observe(slot, job_id=envelope.get("job_id"), status=envelope.get("status", "submitted"))
            call["submitted"] = envelope
            _write_json(sample / f"call-{len(calls):02d}-submission.json", redact_credentials(envelope))
            if envelope.get("status") == "succeeded":
                call["terminal"] = envelope
                _write_json(sample / f"call-{len(calls):02d}-terminal.json", redact_credentials(envelope))
            print(json.dumps({"stage": "submitted", "sample": sample.name, "call": len(calls), "job": envelope.get("job_id"), "status": envelope.get("status")}), flush=True)
            return envelope

        def wait(job_id, **kwargs):
            if args.context_mode == "composition":
                kwargs["request_timeout"] = 15
            envelope = native_wait(job_id, **kwargs)
            calls[-1]["terminal"] = envelope
            _write_json(sample / f"call-{len(calls):02d}-terminal.json", redact_credentials(envelope))
            if envelope.get("telemetry", {}).get("provider_timeout_ms") != 900000:
                raise ValueError("Root did not confirm the preregistered provider budget")
            if args.context_mode == "composition":
                if envelope.get("telemetry", {}).get("provider_max_attempts") != 1 or envelope.get("telemetry", {}).get("provider_attempts") != 1:
                    raise ValueError("Root did not honor the exact paid provider-attempt cap")
                request_budget.observe(calls[-1]["budget_slot"], job_id=job_id, status=envelope.get("status", "terminal"))
            return envelope

        print(json.dumps({"stage": "generation", "sample": sample.name, "scenario": scenario}), flush=True)
        started = time.perf_counter()
        with patch.object(builder, "_builder_llm_messages", projected_messages), patch.object(llm_client, "submit_response_job", submit), patch.object(llm_client, "wait_response_job", wait):
            if retained_primary:
                assembled = builder._builder_llm_webui_transform_request(session=session,
                    instruction=instruction, preview_state=baseline.get("preview_state") or {}, _meta=meta)
                expected, _ = projected_messages(assembled, assembled["user_prompt"])
                result, recovered_call = recover_submitted_primary(builder, llm_client, sample, expected,
                    baseline=baseline, brief=brief, project_ref=session["project_ref"], wait_job=native_wait)
                calls.append(recovered_call)
            else:
                result = builder._apply_llm_webui_transform(session=session, instruction=instruction,
                    preview_state=baseline.get("preview_state") or {}, _meta=meta)
            primary_result = copy.deepcopy(result)
            _write_json(sample / "primary-generation-result.json", primary_result)
            if args.context_mode not in {"pipeline", "composition"} and not result.get("ok") and result.get("last_response"):
                try:
                    builder._parse_llm_webui_transform_output(output_text=result["last_response"],
                        previous_preview=baseline.get("preview_state") or {}, before_webui=baseline,
                        prototype_brief=brief, project_ref=session["project_ref"], output_mode="semantic_v2")
                except Exception as exc:
                    validation = {"ok": False, "error": "semantic_candidate_invalid", "detail": str(exc),
                        "findings": getattr(exc, "findings", [])}
                    result = builder._repair_llm_semantic_transform_output(session=session, instruction=instruction,
                        previous_preview=baseline.get("preview_state") or {}, output_text=primary_result["last_response"],
                        validation_error=validation, prototype_brief=brief, project_ref=session["project_ref"],
                        request_id=calls[0]["request"]["options"]["request_id"],
                        job_id=calls[0]["submitted"].get("job_id") or "", output_mode="semantic_v2", _meta=meta)
        elapsed = time.perf_counter() - started
        _write_json(sample / "generation-result.json", result)
        checked = acceptance(result, brief, task=task)
        _write_json(sample / "structural-acceptance.json", checked)
        telemetry = [builder._llm_job_telemetry(call.get("terminal") or call.get("submitted") or {}) for call in calls]
        _write_json(sample / "telemetry.json", telemetry)
        from adaos.e2e.prototype_mcp_eval import mcp_trace
        _write_json(sample / "native-mcp-traces.json", [mcp_trace(call.get("terminal") or call.get("submitted") or {}) for call in calls])
        browser = {"ok": False, "skipped": "generation_or_structural_acceptance_failed"}
        if result.get("ok") and args.defer_browser:
            browser = {"ok": False, "deferred": True, "reason": "Same frozen candidates require separate native materialization and browser qualification"}
        elif result.get("ok"):
            try:
                with preserved_preview(args.host, sample):
                    browser = browser_review(builder, session, result, sample, created,
                        Path(ctx.paths.dev_dir()).name, output.name, host=args.host,
                        selected_probes=["prototype-review", "prototype-capabilities", "prototype-interactions"] if task == "support-desk-ru" else None)
            except PreviewRestorationError:
                raise
            except Exception as exc:
                browser = {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
        row = {"order": order, "arm": arm, "repetition": counts[arm], "scenario_id": scenario,
            "generation_ok": result.get("ok") is True, "first_pass": primary_result.get("ok") is True and len(calls) == 1,
            "calls": len(calls), "generation_seconds": round(elapsed, 3), "structural": checked, "browser": browser,
            "telemetry": telemetry, "input_bytes": [sum(len(m["content"].encode("utf-8")) for m in c["request"]["messages"]) for c in calls],
            "output_bytes": len(str(result.get("raw_response") or result.get("last_response") or "").encode("utf-8")),
            "failure": {key: result.get(key) for key in ("error", "detail", "validation")
                if result.get(key) and not result.get("ok")}}
        _write_json(sample / "result.json", row)
        rows.append(row)
        _write_json(output / "results.json", {"samples": rows, "complete": len(rows) == len(plan["order"]), "protected_source_unchanged": hashlib.sha256(source_webui.read_bytes()).hexdigest() == protected})
        print(json.dumps({"stage": "sample_complete", "sample": sample.name, "generation_ok": row["generation_ok"], "calls": row["calls"], "structural": checked["ok"], "browser": browser}, ensure_ascii=False), flush=True)
        if args.context_mode in {"pipeline", "composition"} and any((call.get("terminal") or {}).get("status") == "failed" for call in calls):
            raise RuntimeError("Provider/transport failure: stop the cohort before another paid candidate; retain evidence")
        if args.context_mode == "composition" and any(not call.get("terminal") for call in calls):
            raise RuntimeError("Ambiguous delivery: recover the retained request before spending another paid candidate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
