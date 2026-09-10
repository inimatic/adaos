"""Outcome grading for Builder E2E prototypes.

The rubric is deliberately supplied after generation. It is evaluation data,
not Builder context, and therefore cannot steer the candidate being graded.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable, Mapping, Sequence


PROTOTYPE_GRADE_SCHEMA = "adaos.builder.prototype_grade.v1"
_DEFAULT_GRADER_MODEL = os.getenv("ADAOS_BUILDER_E2E_GRADER_MODEL", "gpt-4.1")

_MODEL_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "primary_jobs",
        "representative_states",
        "prohibited_assumptions",
        "summary",
    ],
    "properties": {
        "primary_jobs": {"$ref": "#/$defs/checks"},
        "representative_states": {"$ref": "#/$defs/checks"},
        "prohibited_assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "verdict", "evidence", "reason"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "verdict": {
                        "enum": ["absent", "present", "unclear"]
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/evidence"},
                        "maxItems": 8,
                    },
                    "reason": {"type": "string", "maxLength": 800},
                },
            },
        },
        "summary": {"type": "string", "maxLength": 1200},
    },
    "$defs": {
        "evidence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["pointer"],
            "properties": {
                "pointer": {
                    "type": "string",
                    "pattern": r"^/(?:[^~/]|~[01])*(?:/(?:[^~/]|~[01])*)*$",
                    "maxLength": 500,
                }
            },
        },
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "verdict", "evidence", "reason"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "verdict": {
                        "enum": ["supported", "partial", "unsupported", "unclear"]
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/evidence"},
                        "maxItems": 8,
                    },
                    "reason": {"type": "string", "maxLength": 800},
                },
            },
        }
    },
}

_SYSTEM_PROMPT = """You are an independent evaluator of an AdaOS declarative UI prototype.
Grade only the supplied evaluation artifact against the supplied user turns and rubric.
The artifact contains executable WebUI under /webui and exact revision-bound local
Prototype records under /prototype_resources. Records may prove representative data
states, but they do not prove an interaction unless /webui exposes the required
control and executable declarative action or binding.
Do not reward intent, labels, hidden state, or static sample values as proof of an
interactive job. A supported job must have visible controls/data and executable
declarative actions or bindings sufficient for that job. A representative state
must be explicitly renderable or reachable. Do not infer runtime behavior that
is absent from the document. In particular, field definitions prove only that
values can be entered, table columns and static data prove only that rows can be
displayed, and a generic submit action proves only the state update it explicitly
declares. They do not prove row editing, filtering, selection, navigation, file
attachment, validation, or lifecycle transitions. Mutating jobs need both an
available control and an executable action or binding that consumes its value.
Every supported or partial verdict must cite one or more existing RFC 6901 JSON
pointers in the evaluation artifact. Put the exact pointer alone in evidence.pointer;
never append a label, explanation, or parenthetical note to it. Be conservative: use
unclear when evidence is insufficient. Return only the requested JSON object.
"""


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_pointer_exists(document: Any, pointer: str) -> bool:
    if pointer == "":
        return True
    if not pointer.startswith("/"):
        return False
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            try:
                current = current[int(token)]
                continue
            except (ValueError, IndexError):
                return False
        return False
    return True


def _parse_result(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"prototype grader returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("prototype grader returned a non-object JSON value")
    return dict(parsed)


def _usage(value: Any) -> dict[str, int]:
    root = value if isinstance(value, Mapping) else {}

    def child(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        candidate = parent.get(key)
        return candidate if isinstance(candidate, Mapping) else {}

    candidates = (
        child(root, "usage"),
        child(child(root, "response"), "usage"),
        child(child(root, "result"), "usage"),
        child(child(root, "error"), "usage"),
        child(child(root, "_protocol"), "usage"),
    )
    usage = next(
        (
            dict(candidate)
            for candidate in candidates
            if any(
                key in candidate
                for key in (
                    "input_tokens",
                    "prompt_tokens",
                    "output_tokens",
                    "completion_tokens",
                )
            )
        ),
        {},
    )
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    details = usage.get("input_tokens_details")
    cached = int(usage.get("cached_input_tokens") or usage.get("cached_tokens") or 0)
    if isinstance(details, Mapping):
        cached = int(details.get("cached_tokens") or cached)
    output_details = usage.get("output_tokens_details")
    reasoning = (
        int(output_details.get("reasoning_tokens") or 0)
        if isinstance(output_details, Mapping)
        else 0
    )
    return {
        "input_fresh_tokens": max(0, input_tokens - cached),
        "input_cached_tokens": max(0, cached),
        "generated_tokens": max(
            0, int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        ),
        "reasoning_tokens": max(0, reasoning),
        "calls": 1,
    }


def _normalize_checks(
    *,
    requirements: Sequence[str],
    raw: Any,
    artifact: Mapping[str, Any],
    assumption: bool = False,
) -> list[dict[str, Any]]:
    indexed = {
        int(item.get("index")): dict(item)
        for item in raw or []
        if isinstance(item, Mapping)
        and isinstance(item.get("index"), int)
        and not isinstance(item.get("index"), bool)
    }
    results: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements):
        item = indexed.get(index, {})
        admitted = (
            {"absent", "present", "unclear"}
            if assumption
            else {"supported", "partial", "unsupported", "unclear"}
        )
        verdict = str(item.get("verdict") or "unclear").strip().lower()
        if verdict not in admitted:
            verdict = "unclear"
        evidence_pointers = [
            str(evidence_item.get("pointer") or "")
            for evidence_item in item.get("evidence") or []
            if isinstance(evidence_item, Mapping)
            and str(evidence_item.get("pointer") or "")
        ]
        evidence = [
            pointer
            for pointer in evidence_pointers
            if _json_pointer_exists(artifact, pointer)
        ]
        invalid_evidence = [
            pointer
            for pointer in evidence_pointers
            if not _json_pointer_exists(artifact, pointer)
        ]
        if invalid_evidence or (
            verdict in {"supported", "partial"} and not evidence
        ):
            verdict = "unclear"
        results.append(
            {
                "requirement": str(requirement),
                "verdict": verdict,
                "evidence": evidence,
                "invalid_evidence": invalid_evidence,
                "reason": str(item.get("reason") or "").strip()[:800],
            }
        )
    return results


def _dimension_score(checks: Sequence[Mapping[str, Any]], *, assumption: bool) -> float:
    if not checks:
        return 1.0
    weights = (
        {"absent": 1.0, "present": 0.0, "unclear": 0.0}
        if assumption
        else {"supported": 1.0, "partial": 0.5, "unsupported": 0.0, "unclear": 0.0}
    )
    return sum(weights.get(str(item.get("verdict")), 0.0) for item in checks) / len(checks)


def grade_builder_prototype(
    *,
    artifact: Mapping[str, Any],
    user_turns: Sequence[str],
    requirements: Mapping[str, Any],
    prohibited_assumptions: Sequence[str],
    locale: str,
    threshold: float = 0.85,
    model: str | None = None,
    timeout_seconds: float = 180,
    submitter: Callable[..., Mapping[str, Any]] | None = None,
    waiter: Callable[..., Mapping[str, Any]] | None = None,
    request_recorder: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Grade one immutable artifact and return the grade plus exact request record."""

    jobs = [str(item) for item in requirements.get("primary_jobs") or []]
    states = [str(item) for item in requirements.get("representative_states") or []]
    assumptions = [str(item) for item in prohibited_assumptions]
    payload = {
        "schema": "adaos.builder.prototype_grade_request.v1",
        "locale": str(locale or "en"),
        "user_turns": [str(item) for item in user_turns],
        "rubric": {
            "primary_jobs": jobs,
            "representative_states": states,
            "prohibited_assumptions": assumptions,
        },
        "artifact": dict(artifact),
    }
    selected_model = str(model or _DEFAULT_GRADER_MODEL).strip()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT.strip()},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    ]
    request_digest = _digest({"messages": messages, "model": selected_model})
    request_id = "builder-e2e-grade-" + request_digest.removeprefix("sha256:")[:32]
    request_record = {
        "schema": "adaos.builder.prototype_grade_input.v1",
        "request_id": request_id,
        "request_digest": request_digest,
        "artifact_digest": _digest(artifact),
        "rubric_digest": _digest(payload["rubric"]),
        "messages": messages,
        "model": selected_model,
    }
    if request_recorder is not None:
        request_recorder(request_record)
    if submitter is None or waiter is None:
        from adaos.sdk.llm.llm_client import submit_response_job, wait_response_job

        submitter = submit_response_job
        waiter = wait_response_job
    started = time.perf_counter()
    submitted = dict(
        submitter(
            messages,
            model=selected_model,
            temperature=0.0,
            max_tokens=1800,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "adaos_builder_prototype_grade",
                    "strict": True,
                    "schema": _MODEL_RESULT_SCHEMA,
                }
            },
            request_id=request_id,
            prompt_cache_key="adaos-builder-e2e-prototype-grader-v2",
            timeout=min(15.0, timeout_seconds),
        )
    )
    job_id = str(submitted.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("prototype grader submission returned no job_id")
    client = submitted.get("_client")
    base_url = str(
        client.get("base_url") if isinstance(client, Mapping) else ""
    ).strip()
    response = dict(
        waiter(
            job_id,
            base_url=base_url or None,
            timeout_s=timeout_seconds,
            poll_interval_s=1.0,
            request_timeout=6.0,
        )
    )
    if str(response.get("status") or "").lower() != "succeeded":
        raise ValueError(
            "prototype grader job did not succeed: "
            + str(response.get("status") or "unknown")
        )
    parsed = _parse_result(str(response.get("output_text") or ""))
    job_checks = _normalize_checks(
        requirements=jobs,
        raw=parsed.get("primary_jobs"),
        artifact=artifact,
    )
    state_checks = _normalize_checks(
        requirements=states,
        raw=parsed.get("representative_states"),
        artifact=artifact,
    )
    assumption_checks = _normalize_checks(
        requirements=assumptions,
        raw=parsed.get("prohibited_assumptions"),
        artifact=artifact,
        assumption=True,
    )
    job_score = _dimension_score(job_checks, assumption=False)
    state_score = _dimension_score(state_checks, assumption=False)
    assumption_score = _dimension_score(assumption_checks, assumption=True)
    score = round(0.60 * job_score + 0.25 * state_score + 0.15 * assumption_score, 6)
    hard_gate = all(item["verdict"] == "supported" for item in job_checks) and all(
        item["verdict"] == "supported" for item in state_checks
    ) and all(item["verdict"] == "absent" for item in assumption_checks)
    normalized_threshold = max(0.0, min(float(threshold), 1.0))
    threshold_passed = score >= normalized_threshold
    passed = hard_gate and threshold_passed
    findings = [
        {
            "dimension": dimension,
            "requirement": item["requirement"],
            "verdict": item["verdict"],
            "reason": item["reason"],
        }
        for dimension, checks, accepted in (
            ("primary_job", job_checks, "supported"),
            ("representative_state", state_checks, "supported"),
            ("prohibited_assumption", assumption_checks, "absent"),
        )
        for item in checks
        if item["verdict"] != accepted
    ]
    return (
        {
            "schema": PROTOTYPE_GRADE_SCHEMA,
            "ok": True,
            "status": "passed" if passed else "failed",
            "passed": passed,
            "score": score,
            "threshold": normalized_threshold,
            "gate": {
                "passed": hard_gate,
                "policy": "all_required_outcomes",
                "threshold_passed": threshold_passed,
                "failed_dimensions": sorted(
                    {item["dimension"] for item in findings}
                ),
            },
            "dimensions": {
                "primary_jobs": {"score": round(job_score, 6), "checks": job_checks},
                "representative_states": {
                    "score": round(state_score, 6),
                    "checks": state_checks,
                },
                "prohibited_assumptions": {
                    "score": round(assumption_score, 6),
                    "checks": assumption_checks,
                },
            },
            "findings": findings,
            "summary": str(parsed.get("summary") or "").strip()[:1200],
            "grader": {
                "kind": "model",
                "version": "2",
                "model": response.get("model"),
                "response_id": response.get("id")
                or dict(response.get("response") or {}).get("id"),
                "job_id": job_id,
                "request_id": request_id,
            },
            "grader_metrics": {
                **_usage(response),
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            },
            "artifact_digest": request_record["artifact_digest"],
            "rubric_digest": request_record["rubric_digest"],
        },
        request_record,
    )


__all__ = ["PROTOTYPE_GRADE_SCHEMA", "grade_builder_prototype"]
