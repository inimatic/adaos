"""Model-free semantic increment qualification on one owned disposable fixture.

This extends the public Builder E2E rail. Initial fixture provisioning is not
evidence of autonomous generation, ordinary-language routing or human acceptance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping
from unittest.mock import patch


def execute(inputs: Mapping[str, Any], context: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.e2e.builder import _write_json
    from adaos.e2e.builder_host import step_builder_host
    from adaos.sdk.builder import preview, prototype, semantic_ui, workflow
    from adaos.sdk.developer import compositions, projects, prototypes
    from adaos.sdk.llm import llm_client
    from adaos.services.agent_context import get_ctx
    from adaos.services.artifact_pipeline.storage import atomic_write_json, atomic_write_bytes
    from adaos.services.builder.semantic_graph import digest
    from adaos.sdk import conversation

    if os.getenv("ENV_TYPE") != "dev" or context.get("profile") != "generic":
        raise ValueError("semantic episode requires a generic DEV development fixture")
    bundle = Path(context["bundle_dir"])
    output = bundle / "semantic-episodes" / f"{context['case_id']}-{context['repetition']}"
    output.mkdir(parents=True, exist_ok=True)
    source = (repo_root / str(inputs["fixture_ref"])).resolve()
    if not source.is_relative_to((repo_root / "e2e/builder/development").resolve()):
        raise ValueError("semantic fixture must belong to the development suite")
    fixture = json.loads(source.read_text(encoding="utf-8"))
    scenario_id = "test_semantic_" + hashlib.sha256(f"{context['run_id']}:{context['case_id']}:{context['repetition']}".encode()).hexdigest()[:18]
    if projects.resolve_root("scenario", scenario_id, required=False).exists():
        raise ValueError("existing semantic fixture requires explicit retained-run recovery")
    created = compositions.create_with_primary_component(scenario_id, kind="scenario", template="scenario_default",
        title="[TEST] Semantic increment qualification", description="Model-free development fixture", tags=["test", "semantic-increments"])
    _write_json(output / "creation.json", created)
    root = projects.resolve_root("scenario", scenario_id)
    project_ref = f"project:{scenario_id}"
    host = step_builder_host(inputs, context)
    route = str(inputs.get("request_route") or "sdk")
    if route not in {"sdk", "installed_conversation"}:
        raise ValueError("unsupported semantic episode request route")
    conversational = route == "installed_conversation"
    column_order = inputs.get("include_column_order", False)
    if not isinstance(column_order, bool) or (column_order and not conversational):
        raise ValueError("column-order qualification requires the installed conversation route")
    compiled = prototype.compile_semantic(fixture, project_ref=project_ref)
    fixture = compiled["semantic_document"]
    source_before = copy.deepcopy(fixture)
    ui = compiled["webui"]
    ui["ui"]["application"]["resources"] = {
        f"{fixture['document_id']}.i18n.{locale}": {"kind": "data", "role": "i18n", "locale": locale,
            "path": f"assets/i18n/{locale}.json", "mime": "application/json", "delivery": "core"}
        for locale in compiled["locale_dictionaries"]}
    for name, value in {"semantic.webui.json": fixture, "webui.json": ui,
                        **{f"assets/i18n/{locale}.json": messages for locale, messages in compiled["locale_dictionaries"].items()}}.items():
        projects.write_file("scenario", scenario_id, name, json.dumps(value, ensure_ascii=False, indent=2))
    # Seed immutable fixture revision once; later writes use only the product SDK.
    atomic_write_json(root / "ui_revisions/001.json", {"schema": "adaos.builder.ui_revision.v1", "revision": "001",
        "scenario_id": scenario_id, "after_webui": ui, "fixture_digest": digest(fixture), "source_kind": "diagnostic_fixture"})
    atomic_write_bytes(root / "ui_revisions/current.txt", b"001\n")
    change_id = f"semantic-{scenario_id}"
    workflow.transition("scenario", scenario_id, "plan_change_set", actor="e2e:semantic-increments", metadata={
        "change_set_id": change_id, "request": "Change existing labels and table column order without altering records, relationships or commands." if column_order else "Change existing labels without altering records, relationships or commands.",
        "issues": [{"issue_id": "bounded-semantic-edits", "title": "Preserve behavior through bounded semantic edits", "lane": "prototype",
                    "acceptance_criteria": ["Admitted label/order changes and undo are visible; existing records, relationships and actions remain usable."]}]})
    specs = prototypes.derive_resource_specs(ui, compiled["prototype_resources"])
    _write_json(output / "resource-initialization.json", prototypes.materialize_resources(project_ref=project_ref,
        change_id=change_id, revision="001", webui=ui, resources=specs))
    baseline = semantic_ui.verify_source(fixture, ui, project_ref=project_ref)
    _write_json(output / "baseline-verification.json", {"semantic_digest": baseline["semantic_digest"], "primary_locale": baseline["primary_locale"]})
    rows = []
    expected_view = "Records"
    expected_field = "Name"
    revision = "001"
    column_moved = False

    def observe(label):
        evidence = output / label
        evidence.mkdir(exist_ok=True)
        materialized = preview.select_target("scenario", scenario_id, source_webspace_id=host,
            revision=revision, stage="prototype", follow_active=conversational, via_owner=True)
        _write_json(evidence / "materialization.json", materialized)
        if not materialized.get("ok"):
            raise ValueError("semantic fixture materialization failed")
        checkpoint = {"context": {"locale": "en", "run_id": context["run_id"]},
            "steps": [{"id": "create", "output": {**created, "scenario_id": scenario_id, "artifact_root": str(root)}}],
            "cleanup": {"test": True, "acceptance": "not_approved", "owned_artifacts": [{"primary_ref": f"scenario:{scenario_id}", "project_id": scenario_id}],
                        "previews": [{"webspace_id": materialized["preview_webspace_id"], "scenario_id": scenario_id, "test": True, "stage": "prototype"}]}}
        _write_json(evidence / "checkpoint.json", checkpoint)
        environment = {**os.environ, "ENV_TYPE": "dev", "ADAOS_E2E_HUB_URL": "http://127.0.0.1:8777",
            "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url="http://127.0.0.1:8777"), "ADAOS_E2E_SUBNET_ID": Path(get_ctx().paths.dev_dir()).name,
            "ADAOS_E2E_SCENARIO_ID": scenario_id, "ADAOS_E2E_WEBSPACE_ID": materialized["preview_webspace_id"],
            "ADAOS_E2E_CLIENT_URL": "http://127.0.0.1:8100/", "ADAOS_E2E_LOCALE": "en", "ADAOS_E2E_EXPECTED_WEBUI": str(root / "webui.json"),
            "ADAOS_E2E_OUTPUT": str(evidence), "ADAOS_E2E_CHECKPOINT": str(evidence / "checkpoint.json"),
            "ADAOS_E2E_LABEL_VIEW": expected_view, "ADAOS_E2E_LABEL_FIELD": expected_field}
        if column_order:
            environment["ADAOS_E2E_COLUMN_ORDER"] = json.dumps(
                ["State", expected_field, "Team"] if column_moved else [expected_field, "State", "Team"])
        completed = subprocess.run(["node", str(repo_root / "e2e/stand/browser/semantic-increments.mjs")],
            cwd=repo_root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=180)
        (evidence / "browser.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        receipt = json.loads((evidence / "result.json").read_text(encoding="utf-8")) if (evidence / "result.json").exists() else {"ok": False}
        row = {"stage": label, "revision": revision, "ok": completed.returncode == 0 and receipt.get("ok") is True,
               "browser": receipt, "evidence_ref": str((evidence / "result.json").relative_to(bundle))}
        rows.append(row)
        _write_json(output / "progress.json", {"complete": False, "rows": rows, "model_calls": 0})
        if not row["ok"]:
            raise ValueError(f"semantic episode browser stage failed: {label}")

    def send_request(instruction, order, *, receipt_prefix="Label"):
        import requests
        evidence = output / f"request-{order}"
        evidence.mkdir(exist_ok=False)
        token = resolve_control_token(base_url="http://127.0.0.1:8777")
        client = requests.Session()
        client.trust_env = False
        opened = client.get("http://127.0.0.1:8777/api/builder/workbench/open",
            headers={"X-AdaOS-Token": token}, params={"webspace_id": host, "surface": "authoring",
                "selected_object_type": "project", "selected_object_id": scenario_id,
                "base_url": "http://127.0.0.1:8100"}, timeout=120)
        opened.raise_for_status()
        _write_json(evidence / "authoring.json", opened.json())
        runtime = client.get("http://127.0.0.1:8777/api/skills/runtime/status/builder_skill",
                             headers={"X-AdaOS-Token": token}, timeout=30)
        runtime.raise_for_status()
        _write_json(evidence / "runtime.json", runtime.json())
        expected = f"{int(revision) + 1:03d}"
        environment = {**os.environ, "ENV_TYPE": "dev", "ADAOS_E2E_HUB_URL": "http://127.0.0.1:8777",
            "ADAOS_E2E_HUB_TOKEN": token, "ADAOS_E2E_SUBNET_ID": Path(get_ctx().paths.dev_dir()).name,
            "ADAOS_E2E_HOST": host, "ADAOS_E2E_CLIENT_URL": "http://127.0.0.1:8100/",
            "ADAOS_E2E_SCENARIO_ID": scenario_id,
            "ADAOS_E2E_INSTRUCTION": instruction, "ADAOS_E2E_EXPECTED_REVISION": expected,
            "ADAOS_E2E_RECEIPT_PREFIX": receipt_prefix,
            "ADAOS_E2E_COMPACT": "1" if order == 2 else "0", "ADAOS_E2E_OUTPUT": str(evidence)}
        completed = subprocess.run(["node", str(repo_root / "e2e/stand/browser/builder-semantic-request.mjs")],
            cwd=repo_root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=180)
        (evidence / "browser.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        if completed.returncode or not json.loads((evidence / "result.json").read_text(encoding="utf-8")).get("ok"):
            raise ValueError(f"installed Builder conversation request failed: {order}")
        retained = json.loads((root / f"ui_revisions/{expected}.json").read_text(encoding="utf-8"))
        receipt = retained["semantic_transaction"]
        origin = receipt["source_request"]
        if origin["instruction"] != instruction:
            raise ValueError("retained semantic source request differs from browser input")
        messages = conversation.get("conv.skill.builder_skill.default", thread_id=f"prompt-project:scenario:{scenario_id}", limit=100)
        _write_json(evidence / "conversation.json", messages)
        records = messages.get("messages") or messages.get("items") or []
        if not any(row.get("id", row.get("message_id")) == origin["source_message_id"] and row.get("text") == instruction
                   and row.get("from", row.get("role")) == "user" for row in records):
            raise ValueError("actual semantic request has no matching durable user message")
        packet = semantic_ui.prepare_request(project_ref=f"scenario:{scenario_id}", instruction=instruction,
            source_message_id=origin["source_message_id"], expected_revision=revision)
        if not packet.get("replayed"):
            raise ValueError("installed label request was not retained for replay")
        return packet["request"], receipt

    def inspect_in_browser(label, *, compact=False):
        evidence = output / label
        evidence.mkdir(exist_ok=False)
        environment = {**os.environ, "ENV_TYPE": "dev", "ADAOS_E2E_HUB_URL": "http://127.0.0.1:8777",
            "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url="http://127.0.0.1:8777"),
            "ADAOS_E2E_SUBNET_ID": Path(get_ctx().paths.dev_dir()).name,
            "ADAOS_E2E_HOST": host, "ADAOS_E2E_CLIENT_URL": "http://127.0.0.1:8100/",
            "ADAOS_E2E_SCENARIO_ID": scenario_id, "ADAOS_E2E_EXPECTED_REVISION": revision,
            "ADAOS_E2E_COMPACT": "1" if compact else "0", "ADAOS_E2E_OUTPUT": str(evidence)}
        completed = subprocess.run(["node", str(repo_root / "e2e/stand/browser/builder-semantic-inspector.mjs")],
            cwd=repo_root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=180)
        (evidence / "browser.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        if completed.returncode or not json.loads((evidence / "result.json").read_text(encoding="utf-8")).get("ok"):
            raise ValueError("installed semantic inspector browser qualification failed: " + label)

    with patch.object(llm_client, "submit_response_job", side_effect=AssertionError("model-free semantic episode cannot submit inference")):
        observe("00-baseline")
        last_request = None
        for order, (ref, locale, text) in enumerate([
            ("view:records-list", "en", "Work queue"),
            ("field:records.name", "en", "Task subject"),
            ("view:records-list", "ru", "Очередь работ"),
        ], 1):
            before = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
            target = next(view["title"] for view in before["views"] if view["id"] == "records-list") if ref.startswith("view:") else before["resources"][0]["fields"][0]["label"]
            request = {"schema": "adaos.builder.semantic_graph_change.v1", "project_ref": f"scenario:{scenario_id}",
                "change_id": change_id, "transaction_id": f"label-{order}", "source_revision": revision,
                "expected_generation": workflow.get_state("scenario", scenario_id)["generation"], "base_hash": digest(before),
                "write_refs": [ref], "ops": [{"seq": 1, "kind": "label.set", "ref": ref, "locale": locale, "expected": target[locale], "text": text}]}
            started = time.perf_counter()
            if conversational:
                instruction = (f'Rename {"view" if ref.startswith("view:") else "field"} "{target[locale]}" '
                               f'to "{text}" in {"English" if locale == "en" else "Russian"}')
                request, receipt = send_request(instruction, order)
            else:
                receipt = semantic_ui.apply(request)
            revision = receipt["revision"]
            after = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
            replay = semantic_ui.replay(before, request["ops"], write_refs=[ref])
            if replay["semantic_document"] != after or after["commands"] != source_before["commands"] or after["resources"][0]["records"] != source_before["resources"][0]["records"]:
                raise ValueError("semantic replay or preserved behavior mismatch")
            if semantic_ui.apply(request)["revision"] != revision:
                raise ValueError("exact SDK retry created a second revision")
            _write_json(output / f"operation-{order}.json", {"request": request, "receipt": receipt,
                "request_seconds" if conversational else "sdk_seconds": time.perf_counter() - started, "replay_equal": True})
            actual_ui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
            _write_json(output / f"resource-carry-forward-{order}.json", prototypes.carry_forward_resources(
                project_ref=project_ref, change_id=change_id, revision=revision, webui=actual_ui))
            if locale == "en":
                if ref.startswith("view:"):
                    expected_view = text
                else:
                    expected_field = text
            observe(f"0{order}-label")
            last_request, last_receipt = request, receipt
        if column_order:
            before_order = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
            request, receipt = send_request(f'Move column "State" before "{expected_field}" in view "{expected_view}" in English', 4,
                                            receipt_prefix="Semantic")
            revision = receipt["revision"]
            after_order = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
            replay = semantic_ui.replay(before_order, request["ops"], write_refs=request["write_refs"])
            expected_source = copy.deepcopy(before_order)
            target = next(item for item in expected_source["views"] if item["id"] == "records-list")
            target["field_refs"] = [target["field_refs"][1], target["field_refs"][0], *target["field_refs"][2:]]
            if after_order != expected_source or replay["semantic_document"] != after_order:
                raise ValueError("column order changed semantics outside the admitted view property")
            if semantic_ui.apply(request)["revision"] != revision:
                raise ValueError("column order retry created a second revision")
            _write_json(output / "operation-4.json", {"request": request, "receipt": receipt, "replay_equal": True})
            _write_json(output / "semantic-inspector-after-order.json", semantic_ui.inspect(project_ref=f"scenario:{scenario_id}"))
            column_moved = True
            observe("04-column-order")
            inspect_in_browser("inspector-after-order-wide")
            last_request, last_receipt = request, receipt
        undo = {**last_request, "transaction_id": "undo-last-label", "source_revision": revision,
                "base_hash": last_receipt["semantic_digest"], "ops": last_receipt["inverse_ops"],
                "expected_generation": workflow.get_state("scenario", scenario_id)["generation"]}
        if conversational:
            undo, undone = send_request("Undo the last column order change" if column_order else "Undo the last label change",
                                       5 if column_order else 4, receipt_prefix="Semantic" if column_order else "Label")
        else:
            undone = semantic_ui.apply(undo)
        revision = undone["revision"]
        _write_json(output / "undo.json", {"request": undo, "receipt": undone})
        prototypes.carry_forward_resources(project_ref=project_ref, change_id=change_id, revision=revision,
            webui=json.loads((root / "webui.json").read_text(encoding="utf-8")))
        column_moved = False
        if column_order:
            restored = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
            if restored != before_order:
                raise ValueError("undo did not restore column order while preserving prior label changes")
            _write_json(output / "semantic-inspector-after-undo.json", semantic_ui.inspect(project_ref=f"scenario:{scenario_id}"))
        observe("05-undo" if column_order else "04-undo")
        if column_order:
            inspect_in_browser("inspector-after-undo-compact", compact=True)
    result = {"ok": True, "scenario_id": scenario_id, "artifact_root": str(root), "created": created,
              "revision": revision, "rows": rows, "model_calls": 0, "usd": 0,
              "initial_source": "diagnostic_fixture", "acceptance": "not_approved", "host": host,
              "request_route": route,
              "include_column_order": column_order,
              "scope": ("Real installed Builder chat bounded label/column-order requests, family-specific undo, durable messages, semantic inspector and live wide/compact Preview; fixture initial source, not autonomous generation"
                        if conversational else "Public SDK semantic labels/replay/undo plus live wide/compact Client; not language interpretation or generation")}
    _write_json(output / "result.json", result)
    return result
