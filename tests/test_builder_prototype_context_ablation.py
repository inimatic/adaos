import copy
import json
from types import SimpleNamespace

import pytest

from adaos.e2e.prototype_context_ablation import acceptance, evaluation_model_controls, generation_configuration, matched_reference_checks, recover_submitted_primary, remap, validate_resumption_plan, verify_root_job_budget_support
from adaos.sdk.builder.intent import compile_brief


def test_composition_full_controls_have_identical_messages_and_every_arm_keeps_the_same_core():
    from adaos.e2e.prototype_context_ablation import compile_experiment_messages
    from adaos.sdk.builder.prototype import semantic_generation_guidance
    source = [{"role": "user", "content": json.dumps({"stable_builder_context": {
        "semantic_invariants": semantic_generation_guidance(), "prototype_retrieval": {"old": True},
        "output_contract": {"exact": True}}})}, {"role": "user", "content": json.dumps({
        "builder_request": {"instruction": "Search and edit records", "facts": ["exact"]}}, indent=2)}]
    before = copy.deepcopy(source)
    outputs, cores = {}, set()
    for arm in ("full-no-mcp", "full-mcp", "core-indexed", "task-aware", "oracle"):
        messages, receipt = compile_experiment_messages(source, strategy=arm, generation_options={},
            context_mode="composition", mcp_available=arm != "full-no-mcp", oracle_fragment_ids=["query_filters"])
        outputs[arm] = messages
        cores.add(receipt["core_sha256"])
        assert messages[1] == source[1]
    assert len(cores) == 1
    assert outputs["full-no-mcp"] == outputs["full-mcp"]
    assert source == before


def candidate():
    return {"ok": True, "semantic_document": {
        "resources": [{"id": name, "fields": [{"value_type": "number"}]} for name in
            ("inventory", "purchase_requests", "supplier_quotes", "currency")],
        "views": [{"id": "inventory", "role": "collection", "query_controls": [{"id": "category"}],
                   "selection_filter": {"field_ref": "item_id"}}, {"id": "editor", "role": "editor"}],
        "commands": [{"kind": "create"}, {"kind": "update"}],
        "representative_states": [{"id": "empty"}],
    }, "locale_dictionaries": {"ru": {"save": "Сохранить"}}, "capability_gaps": []}


def test_acceptance_reads_canonical_view_role_and_field_value_type():
    result = candidate()
    before = copy.deepcopy(result)
    checked = acceptance(result, compile_brief("Create and edit purchase requests"))
    assert checked["ok"]
    assert checked["checks"]["collections_and_editors"]
    assert checked["checks"]["numeric_quantity"]
    assert result == before


def test_pipeline_preserves_all_non_library_inputs_and_provider_options():
    from adaos.e2e.prototype_context_ablation import compile_experiment_messages
    from adaos.sdk.builder.prototype import semantic_generation_guidance
    messages = [{"role": "system", "content": "exact policy"}, {"role": "user", "content": json.dumps({
        "stable_builder_context": {"semantic_invariants": semantic_generation_guidance(), "output_contract": {"schema": "v2"}}})},
        {"role": "user", "content": json.dumps({"builder_request": {"facts": ["exact"], "required_refs": ["req:1"]}})}]
    options = {"model": "gpt-5", "reasoning": {"effort": "high"}, "text": {"format": {"schema": {"exact": True}}}}
    original_options = copy.deepcopy(options)
    full, full_receipt = compile_experiment_messages(messages, strategy="full", generation_options=options, context_mode="pipeline", mcp_available=True)
    adaptive, adaptive_receipt = compile_experiment_messages(messages, strategy="adaptive", generation_options=options, context_mode="pipeline", mcp_available=True)
    assert full == messages
    assert full_receipt["core_sha256"] == adaptive_receipt["core_sha256"]
    assert json.loads(adaptive[-1]["content"]) == json.loads(messages[-1]["content"])
    assert options == original_options


def test_acceptance_does_not_accept_a_wrong_view_property():
    result = candidate()
    for view in result["semantic_document"]["views"]:
        view["kind"] = view.pop("role")
    checked = acceptance(result, compile_brief("Create and edit purchase requests"))
    assert not checked["checks"]["collections_and_editors"]


def test_missing_update_is_still_a_failure_not_a_reinterpreted_pass():
    result = candidate()
    result["semantic_document"]["commands"] = [{"kind": "create"}]
    checked = acceptance(result, compile_brief("Create and edit purchase requests"))
    assert not checked["ok"]
    assert not checked["checks"]["create_and_update_commands"]


def test_invalid_native_candidate_cannot_pass_structural_quality():
    result = candidate()
    result["ok"] = False
    assert not acceptance(result, compile_brief("Create purchase requests"))["ok"]


def test_task_commands_do_not_require_unrequested_generic_update():
    result = candidate()
    result["semantic_document"]["commands"] = [{"kind": "create"}, {"kind": "transition"}]
    brief = compile_brief("Собирать заявку на закупку и отправлять заявку на согласование")
    assert acceptance(result, brief)["checks"]["create_and_update_commands"]
    result["semantic_document"]["commands"] = [{"kind": "create"}]
    assert not acceptance(result, brief)["checks"]["create_and_update_commands"]


def test_currency_symbol_is_not_a_missing_currency_failure():
    result = candidate()
    result["semantic_document"]["resources"][-1]["id"] = "totals"
    result["semantic_document"]["views"][0]["title"] = "Стоимость, ₽"
    assert acceptance(result, compile_brief("Create purchase requests"))["checks"]["currency_is_visible"]


def test_representative_states_are_counted_from_canonical_collection():
    assert acceptance(candidate(), compile_brief("Create purchase requests"))["states"] == 1


def test_fixture_remapping_is_reversible():
    original = {"template-id": ["prototype.template-id.item", {"source": "template-id"}]}
    assert remap(remap(original, "template-id", "test_target"), "test_target", "template-id") == original


def test_same_cohort_in_distinct_runs_has_distinct_owned_scenarios(tmp_path):
    from adaos.e2e.prototype_context_ablation import sample_scenario_id
    first = sample_scenario_id(tmp_path / "run1" / "gpt5-inventory", 1)
    second = sample_scenario_id(tmp_path / "run2" / "gpt5-inventory", 1)
    assert first != second
    assert first == sample_scenario_id(tmp_path / "run1" / "gpt5-inventory", 1)


@pytest.mark.parametrize("model,effort", [("gpt-5", "low"), ("gpt-5", "high"), ("gpt-6", "high")])
def test_primary_and_repair_configuration_share_explicit_model_and_effort(model, effort):
    environment, options, metadata = generation_configuration(model, effort)
    assert environment["ADAOS_BUILDER_LLM_MODEL"] == metadata["builder_llm_model"] == model
    assert environment["ADAOS_BUILDER_LLM_REASONING_EFFORT"] == metadata["builder_llm_reasoning_effort"] == options["reasoning"]["effort"] == effort
    assert options["max_tokens"] == metadata["builder_llm_max_output_tokens"] == 128000
    assert options["text"]["verbosity"] == "low"


def test_invalid_effort_is_not_silently_substituted():
    with pytest.raises(ValueError):
        generation_configuration("gpt-5", "typo")


@pytest.mark.parametrize("updated,relay", [(True, False), (True, True), (False, False)])
def test_delivery_preflight_checks_all_root_routes_without_a_valid_generation_request(updated, relay):
    from adaos.services.root.client import RootHttpError
    requests = []
    def request(method, path, *, json, **kwargs):
        requests.append(json)
        assert json["model"] == "__invalid_builder_preflight_model__"
        assert json["provider_timeout_ms"] == 900001
        raise RootHttpError("preflight", status_code=400 if updated else 403,
            error_code="llm_proxy_upstream_failed" if relay else "invalid_provider_timeout" if updated else "model_not_allowed",
            payload={"upstream": {"status": 400, "json": {"error": "invalid_provider_timeout"}}} if relay else None)
    primary = SimpleNamespace(base_url="regional", request=request)
    client = SimpleNamespace(_root_http_client=lambda: (primary, None), _normalize_root_base_url=lambda value: value,
        _identity_headers=lambda *args: {}, _current_ctx=lambda: None, _root_llm_base_urls=lambda primary: ["regional", "central"],
        _root_http_for_base=lambda *args: primary, _root_http_error_code=lambda exc: exc.error_code)
    if updated:
        receipts = verify_root_job_budget_support(client)
        assert len(requests) == len(receipts) == 2
        assert all(item["paid_call"] is False for item in receipts)
    else:
        with pytest.raises(ValueError, match="does not confirm"):
            verify_root_job_budget_support(client)


def test_gpt6_control_bridge_is_explicit_and_does_not_change_other_models():
    builder = SimpleNamespace(_builder_llm_reasoning_for_model=lambda model, meta=None: None,
        _builder_llm_temperature_for_model=lambda model, *, repair=False: 0.0 if repair else 0.7)
    receipt = evaluation_model_controls(builder, "gpt-6-astra", "high")
    assert receipt["applied"] and receipt["native_reasoning"] is None
    assert builder._builder_llm_reasoning_for_model("gpt-6-astra", {}) == {"effort": "high"}
    assert builder._builder_llm_reasoning_for_model("other", {}) is None
    assert builder._builder_llm_temperature_for_model("gpt-6-astra", repair=True) is None
    assert builder._builder_llm_temperature_for_model("other", repair=True) == 0.0


def test_unrecognized_native_effort_fails_before_submission():
    builder = SimpleNamespace(_builder_llm_reasoning_for_model=lambda model, meta=None: None,
        _builder_llm_temperature_for_model=lambda model, *, repair=False: None)
    with pytest.raises(ValueError, match="does not preserve"):
        evaluation_model_controls(builder, "gpt-5", "high")


@pytest.mark.parametrize("drift", [False, True])
def test_reference_parity_only_allows_model_and_effort_changes(tmp_path, drift):
    reference, output = tmp_path / "reference", tmp_path / "output"
    reference.mkdir()
    output.mkdir()
    plan = {key: "same" for key in ("instruction_sha256", "brief_sha256", "provider_schema_sha256",
        "initial_webui_sha256", "native_builder_sha256", "hint_budget_tokens", "order", "acceptance")}
    (reference / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    for arm in ("full", "adaptive"):
        original = {"messages": [{"role": "user", "content": "frozen task"}], "purposes": ["task"],
            "options": {"model": "gpt-5", "reasoning": {"effort": "low"}, "max_tokens": 128000}}
        current = copy.deepcopy(original)
        current["options"].update(model="gpt-6", reasoning={"effort": "high"})
        if drift:
            current["messages"][0]["content"] = "different task"
        for folder, payload in ((reference, original), (output, current)):
            (folder / f"planned-{arm}-input.json").write_text(json.dumps(payload), encoding="utf-8")
    if drift:
        with pytest.raises(ValueError, match="Frozen experiment drift"):
            matched_reference_checks(reference, output, plan)
    else:
        assert all(matched_reference_checks(reference, output, plan)["checks"].values())


def test_resume_replays_paid_response_without_another_submission(tmp_path):
    messages = [{"role": "user", "content": "frozen"}]
    (tmp_path / "call-01-request.json").write_text(json.dumps({"messages": messages, "options": {"request_id": "request"}}), encoding="utf-8")
    (tmp_path / "call-01-submission.json").write_text(json.dumps({"job_id": "job"}), encoding="utf-8")
    client = SimpleNamespace(get_response_job=lambda job, timeout: {"status": "succeeded", "output_text": "original output"})
    builder = SimpleNamespace(_parse_llm_webui_transform_output=lambda **kwargs: {"ok": True, "raw_response": kwargs["output_text"]})
    result, call = recover_submitted_primary(builder, client, tmp_path, messages, baseline={}, brief={}, project_ref="project:test")
    assert result == {"ok": True, "raw_response": "original output"}
    assert call["submitted"]["job_id"] == "job"
    receipt = json.loads((tmp_path / "primary-recovery.json").read_text(encoding="utf-8"))
    assert receipt["primary_resubmitted"] is False


def test_resume_rejects_changed_inputs_before_fetching_or_submitting(tmp_path):
    (tmp_path / "call-01-request.json").write_text(json.dumps({"messages": [], "options": {}}), encoding="utf-8")
    (tmp_path / "call-01-submission.json").write_text(json.dumps({"job_id": "job"}), encoding="utf-8")
    with pytest.raises(ValueError, match="messages differ"):
        recover_submitted_primary(None, None, tmp_path, [{"role": "user", "content": "changed"}], baseline={}, brief={}, project_ref="project:test")


def test_pending_resume_uses_the_original_wait_not_the_new_submission_capture(tmp_path):
    (tmp_path / "call-01-request.json").write_text(json.dumps({"messages": [], "options": {"request_id": "request"}}), encoding="utf-8")
    (tmp_path / "call-01-submission.json").write_text(json.dumps({"job_id": "job"}), encoding="utf-8")
    client = SimpleNamespace(get_response_job=lambda job, timeout: {"status": "running"})
    waited = []
    def original_wait(job, timeout_s):
        waited.append((job, timeout_s))
        return {"status": "succeeded", "output_text": "retained"}
    builder = SimpleNamespace(_parse_llm_webui_transform_output=lambda **kwargs: {"ok": True})
    result, _ = recover_submitted_primary(builder, client, tmp_path, [], baseline={}, brief={}, project_ref="project:test", wait_job=original_wait)
    assert result["ok"] and waited == [("job", 1200)]


def test_resumption_keeps_model_input_policy_and_acceptance_frozen():
    keys = ("model", "effort", "max_output_tokens", "max_attempts", "hint_budget_tokens", "order",
        "instruction_sha256", "brief_sha256", "provider_schema_sha256", "initial_webui_sha256",
        "native_builder_sha256", "delivery_source_sha256", "acceptance", "browser_policy")
    original = {key: "same" for key in keys}
    validate_resumption_plan(original, copy.deepcopy(original))
    for key in ("model", "effort", "acceptance", "delivery_source_sha256", "browser_policy"):
        changed = {**original, key: "changed"}
        with pytest.raises(ValueError, match="changed experiment"):
            validate_resumption_plan(changed, original)
