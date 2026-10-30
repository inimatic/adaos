import pytest

from adaos.sdk.builder.prototype import task_admission_failure
from adaos.services.builder.task_diagnostics import interpretation_refusal_text
from adaos.services.builder.task_interpretation import TaskInterpretationRejected


@pytest.mark.parametrize(
    "locale,expected", [("ru", "Прототип не изменён"), ("en", "Prototype is unchanged")]
)
def test_typed_refusal_has_owner_diagnostics_but_never_copies_arbitrary_exception_prose(
    locale, expected
):
    error = TaskInterpretationRejected(
        [
            {"code": "task.fact_not_source", "detail": "PRIVATE-PROVIDER-DATA"},
            {"code": "<script>PRIVATE</script>", "detail": "PRIVATE"},
        ]
    )
    refusal = task_admission_failure(error, locale=locale)
    text = interpretation_refusal_text(refusal, locale=locale)
    assert expected in text and "task.fact_not_source" in text
    assert "PRIVATE" not in text and "<script>" not in text
    assert (
        refusal["paid_candidate_generation"] is False
        and refusal["candidate_created"] is False
    )


def test_non_interpretation_failures_keep_the_existing_router_path():
    assert (
        interpretation_refusal_text({"error": "provider_failed", "detail": "PRIVATE"})
        is None
    )
    assert task_admission_failure(ValueError("PRIVATE")) is None


@pytest.mark.parametrize("locale", ["en", "ru"])
def test_provider_failure_is_not_projected_as_bad_user_meaning(locale):
    from adaos.services.builder.task_pipeline import TaskInterpretationProviderFailed

    failed = TaskInterpretationProviderFailed(
        {
            "job_id": "ai-failed",
            "status": "failed",
            "error": "PRIVATE",
            "tool_telemetry": {
                "provider_rejection": {"kind": "schema", "http_status": 400}
            },
        }
    )
    result = task_admission_failure(failed, locale=locale)
    assert (
        result["next_action"] == "repair_provider_contract"
        and result["interpretation_job_id"] == "ai-failed"
    )
    assert "PRIVATE" not in str(result)
    assert interpretation_refusal_text(result, locale=locale) == result["detail"]


@pytest.mark.parametrize("locale", ["en", "ru"])
def test_native_async_submission_wrapper_retains_typed_failure_owner_not_wrapper_prose(
    locale,
):
    from adaos.services.builder.task_pipeline import TaskInterpretationProviderFailed

    inner = task_admission_failure(
        TaskInterpretationProviderFailed(
            {
                "job_id": "ai-failed",
                "status": "failed",
                "tool_telemetry": {
                    "provider_rejection": {"kind": "schema", "http_status": 400}
                },
            }
        ),
        locale=locale,
    )
    wrapper = {
        "ok": False,
        "status": "llm_submit_failed",
        "error": inner["error"],
        "detail": "PRIVATE-WRAPPER",
        "llm_job": inner,
    }
    assert interpretation_refusal_text(wrapper, locale=locale) == inner["detail"]
    wrapper["llm_job"] = {
        "error": "different",
        "provider_rejection": {"kind": "schema", "http_status": 400},
    }
    assert interpretation_refusal_text(wrapper, locale=locale) != inner["detail"]
    assert "PRIVATE" not in interpretation_refusal_text(wrapper, locale=locale)


def test_native_async_meaning_refusal_keeps_its_exact_diagnostic_codes():
    inner = task_admission_failure(
        TaskInterpretationRejected(
            [{"code": "task.fact_not_source", "detail": "PRIVATE"}]
        )
    )
    wrapper = {"error": inner["error"], "llm_job": inner, "detail": "PRIVATE"}
    text = interpretation_refusal_text(wrapper)
    assert "task.fact_not_source" in text and "PRIVATE" not in text


def test_cancellation_remains_cancellation_through_safe_native_wrapper_projection():
    from adaos.services.builder.task_pipeline import TaskInterpretationProviderFailed

    inner = task_admission_failure(
        TaskInterpretationProviderFailed(
            {"job_id": "ai-cancelled", "status": "cancelled"}
        )
    )
    assert (
        inner["status"] == "cancelled"
        and inner["next_action"] == "submit_new_turn_if_needed"
    )
    assert (
        interpretation_refusal_text({"error": inner["error"], "llm_job": inner})
        == inner["detail"]
    )
    assert "cancelled" in inner["detail"] and "provider did not" not in inner["detail"]


@pytest.mark.parametrize(
    "gate,owner,action",
    [
        ("G0", "source_admission", "select_matching_scope"),
        ("G4", "capability_admission", "resolve_capability_gap"),
        ("G5", "context_compilation", "repair_context_contract"),
        ("G6", "context_delivery", "repair_context_contract"),
    ],
)
def test_non_meaning_gates_never_authorize_model_response_repair(gate, owner, action):
    result = task_admission_failure(
        TaskInterpretationRejected(
            [
                {
                    "code": "task.preflight_example",
                    "gate": gate,
                    "owner": owner,
                    "detail": "PRIVATE",
                }
            ]
        )
    )
    assert result["next_action"] == action
    assert "does not authorize model-response repair" in result["detail"]
    assert "PRIVATE" not in interpretation_refusal_text(
        {"error": result["error"], "llm_job": result}
    )


@pytest.mark.parametrize(
    "findings", [None, {}, [{"gate": ["G5"], "owner": {}, "code": "PRIVATE"}]]
)
def test_malformed_unknown_tool_evidence_cannot_crash_refusal_projection_or_authorize_repair(
    findings,
):
    from adaos.services.builder.task_diagnostics import interpretation_refusal_kind

    assert interpretation_refusal_kind(findings) == "unknown"
    text = interpretation_refusal_text(
        {"error": "prototype_task_interpretation_rejected", "findings": findings}
    )
    assert "Prototype is unchanged" in text and "PRIVATE" not in text
