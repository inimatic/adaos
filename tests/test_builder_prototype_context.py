from __future__ import annotations

import json

from adaos.sdk.builder import intent, prototype


def test_model_context_keeps_semantics_without_repeating_full_intent() -> None:
    statement = (
        "A coordinator saves an unfinished request and then completes it. "
        "Show an empty queue and prevent completion without an owner."
    )
    brief = intent.compile_brief(statement)

    context = prototype.model_context(brief)

    assert context["schema"] == "adaos.builder.prototype_model_context.v1"
    assert context["brief_ref"] == brief["brief_id"]
    assert context["brief_digest"] == brief["digest"]
    assert "problem" not in context
    assert statement not in json.dumps(context)
    assert context["state_requirements"] == [
        {"job_ref": "job:01"},
        {"job_ref": "job:02"},
    ]
    assert context["constraints"]["locale"] == "en"
    assert context["boundaries"]["data_effects"] == "prototype_only"


def test_model_context_rejects_an_untyped_mapping() -> None:
    try:
        prototype.model_context({"schema": "other"})
    except ValueError as exc:
        assert "Prototype Brief" in str(exc)
    else:
        raise AssertionError("untyped context must be rejected")


def test_model_context_assigns_stable_ids_to_independent_state_requirements() -> None:
    brief = intent.compile_brief(
        "Move work through New, In progress, Blocked and Done."
    )

    context = prototype.model_context(brief)

    assert context["state_requirements"] == [
        {"id": "representative_state:01", "statement": "New"},
        {"id": "representative_state:02", "statement": "In progress"},
        {"id": "representative_state:03", "statement": "Blocked"},
        {"id": "representative_state:04", "statement": "Done"},
    ]


def test_model_context_keeps_attachment_capture_without_renderer_terms() -> None:
    brief = intent.compile_brief(
        "A field worker uploads a photo before completing the report."
    )

    context = prototype.model_context(brief)

    assert context["information_requirements"] == [
        {
            "id": "information:01",
            "kind": "attachment",
            "interaction": "capture",
            "statement": "uploads a photo",
        }
    ]
    assert "fileUpload" not in json.dumps(context)
