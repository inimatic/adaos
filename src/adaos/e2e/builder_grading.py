"""Outcome grading for Builder E2E prototypes.

The rubric is deliberately supplied after generation. It is evaluation data,
not Builder context, and therefore cannot steer the candidate being graded.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from typing import Any, Callable, Mapping, Sequence


PROTOTYPE_GRADE_SCHEMA = "adaos.builder.prototype_grade.v1"
PROTOTYPE_GRADER_VERSION = "12"
_DEFAULT_GRADER_MODEL = os.getenv("ADAOS_BUILDER_E2E_GRADER_MODEL", "gpt-4.1")

_MODEL_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "primary_jobs",
        "representative_states",
        "prohibited_assumptions",
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
                        "enum": ["not_violated", "violated", "unclear"]
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
Acceptance stage is PROTOTYPE, not automation or release readiness. Under
/webui/ui/application/desktop/pageSchema/meta/builder/automation_requirements the
compiler preserves pending business rules and integrations with the original Brief
statement, prototype_refs, requested-locale disclosure and a testable Automation acceptance.
For such a rule, supported at this stage requires BOTH visible representative
evidence in /webui or /prototype_resources AND this explicit pending obligation
matching the requested outcome. Cite both. This means demonstrated and deferred,
NOT implemented or safe for real use. Never treat a fixture status as computed.
Supported CRUD, selection, detail disclosure, search, filters and field validation
still require working declarative controls; an obligation cannot excuse their absence.
Evaluate a minimum useful working interpretation, not an exhaustive imagined product.
The user request is normally shorter than a design specification. Unspecified layout,
field counts, screen counts, workflow detail and visual richness are design choices,
not missing requirements. A simple working design and a richer working design can both
pass. Do not demand an automatic optimizer, a dashboard or a specific component when
a simpler interactive path satisfies the requested outcome. Judge explicit requested
outcomes and the controls necessary for them, not hypothetical professional features.
Do not add obligations from common domain conventions. Rubric acceptance clarifies the
user request; it must not silently enlarge it. Detail and polish belong to separate
human quality review and cannot compensate for broken controls or block a working path.
Silently omitted requirements, unsupported claims and bare capability gaps fail.
For prohibited real-world behavior, an explicitly disclosed unimplemented rule with
an acceptance test and visible conflict/failure example is not a claim of safety;
evaluate the prototype-stage rubric, not production enforcement. Never excuse a
prototype that explicitly presents the prohibited behavior as valid or safe.
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
For media viewing, a filename, URL text, attachment input or ordinary item.details
fields do not prove a viewer. item.details renders actual media only with inputs.mediaKey
(image/video/audio, optional mediaKindKey and mediaPosterKey) or an image with imageKey.
Cite that explicit binding and its selected record/source. A table image column or
list imageKey can show covers but does not by itself prove video playback. Loading and
unavailable states must belong to the actual viewer, not merely an unrelated status
field. No second language is required unless the user explicitly requested it.
Each rubric item supplies a statement plus optional acceptance and exclusions. Apply
its acceptance literally and do not import an excluded concern from another item.
Every supported or partial verdict must cite one or more existing RFC 6901 JSON
pointers copied exactly from the supplied evidence_pointers list. Never
construct or edit an array index, and never append a label, explanation, or
parenthetical note to evidence.pointer. Each cited object must itself contain the fact
described in the reason; a sibling or nearby object is not evidence. Cite the nearest
containing object when a more specific property is not available in the enum.
For an empty representative state, cite an explicit emptyState/state object or its
containing widget; a query or filter object proves filtering, not empty-state rendering.
For each prohibited behavior, use not_violated when the complete artifact does not
implement the complete behavior, violated when an executable path or explicit artifact
fact implements or necessarily relies on it, and unclear only when conflicting or
partial evidence prevents a decision. Mere absence is not uncertainty: a violated
verdict requires positive evidence, while not_violated needs none. In particular, a
direct mutation with no explicit confirmation control or policy violates a prohibition
against an action without confirmation; an explicit confirmation means that prohibition
is not violated. Silence must not be interpreted as hidden confirmation.
Judge the exact subject, action, object, field, and condition named by each assumption.
Do not broaden one mutation into all mutations: an action that changes only an owner,
for example, is not evidence about actions that change status. Evidence and reasoning
for a violated behavior must identify the same behavior named by that prohibition;
unrelated or merely adjacent controls and actions are irrelevant.
A capability gap is evidence of disclosed non-support, not implementation. Grade a
corresponding required job as unsupported. For a prohibited assumption, the gap alone
is not positive evidence that the behavior occurs; inspect executable paths separately.
Be conservative: use unclear when evidence is insufficient. Return only the requested
JSON object.
"""


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _evidence_pointers(artifact: Mapping[str, Any]) -> list[str]:
    """Index meaningful object pointers for schema-constrained grader evidence."""

    entries: list[str] = []
    identity_keys = {"id", "type", "kind", "on", "resource_type", "status"}
    semantic_container_names = {
        "condition",
        "emptyState",
        "filters",
        "initialState",
        "payload",
        "query",
        "validation",
        "visibleWhen",
        "capability_gaps",
        "automation_requirements",
    }

    def visit(value: Any, pointer: str) -> None:
        if isinstance(value, Mapping):
            if pointer and (
                pointer.count("/") <= 5
                or identity_keys.intersection(value)
                or pointer.rsplit("/", 1)[-1] in semantic_container_names
                or (
                    "/prototype_resources/" in pointer
                    and "/records/" in pointer
                )
            ):
                entries.append(pointer)
            for key, nested in value.items():
                visit(nested, f"{pointer}/{_pointer_token(key)}")
            return
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            if pointer.rsplit("/", 1)[-1] in semantic_container_names:
                entries.append(pointer)
            for index, nested in enumerate(value):
                visit(nested, f"{pointer}/{index}")

    visit(artifact, "")
    return entries


def _model_result_schema(evidence_pointers: Sequence[str]) -> dict[str, Any]:
    del evidence_pointers
    return copy.deepcopy(_MODEL_RESULT_SCHEMA)


def _rubric_criteria(values: Sequence[Any]) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, Mapping):
            statement = str(value.get("statement") or "").strip()
            acceptance = str(value.get("acceptance") or "").strip()
            raw_exclusions = value.get("exclusions") or []
            exclusions = [
                str(item).strip()
                for item in raw_exclusions
                if str(item).strip()
            ] if isinstance(raw_exclusions, Sequence) and not isinstance(
                raw_exclusions, (str, bytes, bytearray)
            ) else []
        else:
            statement = str(value).strip()
            acceptance = ""
            exclusions = []
        if not statement:
            continue
        criteria.append(
            {
                "statement": statement,
                "acceptance": acceptance,
                "exclusions": exclusions,
            }
        )
    return criteria


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
    requirements: Sequence[Mapping[str, Any]],
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
    for index, criterion in enumerate(requirements):
        requirement = str(criterion.get("statement") or "")
        item = indexed.get(index, {})
        admitted = (
            {"not_violated", "violated", "unclear"}
            if assumption
            else {"supported", "partial", "unsupported", "unclear"}
        )
        verdict = str(item.get("verdict") or "unclear").strip().lower()
        if verdict not in admitted:
            verdict = "unclear"
        if assumption:
            verdict = {
                "not_violated": "absent",
                "violated": "present",
                "unclear": "unclear",
            }[verdict]
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
            verdict in {"supported", "partial", "present"} and not evidence
        ):
            verdict = "unclear"
        results.append(
            {
                "requirement": requirement,
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
    prohibited_assumptions: Sequence[Any],
    locale: str,
    threshold: float = 0.85,
    model: str | None = None,
    timeout_seconds: float = 180,
    submitter: Callable[..., Mapping[str, Any]] | None = None,
    waiter: Callable[..., Mapping[str, Any]] | None = None,
    request_recorder: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Grade one immutable artifact and return the grade plus exact request record."""

    jobs = _rubric_criteria(requirements.get("primary_jobs") or [])
    states = _rubric_criteria(requirements.get("representative_states") or [])
    assumptions = _rubric_criteria(prohibited_assumptions)
    evidence_pointers = _evidence_pointers(artifact)
    payload = {
        "schema": "adaos.builder.prototype_grade_request.v1",
        "locale": str(locale or "en"),
        "user_turns": [str(item) for item in user_turns],
        "rubric": {
            "primary_jobs": jobs,
            "representative_states": states,
            "prohibited_assumptions": assumptions,
        },
        "evidence_pointers": evidence_pointers,
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
                    "schema": _model_result_schema(evidence_pointers),
                }
            },
            request_id=request_id,
            prompt_cache_key=f"adaos-builder-e2e-prototype-grader-v{PROTOTYPE_GRADER_VERSION}",
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
    supported_jobs = sum(
        item["verdict"] == "supported" for item in job_checks
    )
    supported_states = sum(
        item["verdict"] == "supported" for item in state_checks
    )
    absent_assumptions = sum(
        item["verdict"] == "absent" for item in assumption_checks
    )
    summary = (
        f"Primary jobs supported: {supported_jobs}/{len(job_checks)}; "
        f"representative states supported: {supported_states}/{len(state_checks)}; "
        "prohibited assumptions absent: "
        f"{absent_assumptions}/{len(assumption_checks)}."
    )
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
            "summary": summary,
            "grader": {
                "kind": "model",
                "version": PROTOTYPE_GRADER_VERSION,
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


__all__ = [
    "PROTOTYPE_GRADE_SCHEMA",
    "PROTOTYPE_GRADER_VERSION",
    "grade_builder_prototype",
]
