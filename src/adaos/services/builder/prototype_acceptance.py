"""Fail-closed acceptance evidence for executable Builder prototypes."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.resources.prototype import prototype_webui_digest
from adaos.services.ui_capabilities import evaluate_ui_request

from .workflow import BuilderWorkflowError


PROTOTYPE_ACCEPTANCE_SCHEMA = "adaos.builder.prototype_acceptance.v1"


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: Mapping[str, Any], *, label: str) -> None:
    try:
        Draft202012Validator(_schema(name)).validate(dict(value))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            location = ".".join(str(item) for item in exc.absolute_path)
            suffix = f" at {location}" if location else ""
            message = f"{exc.message}{suffix}"
        else:
            message = str(exc)
        raise BuilderWorkflowError(f"invalid {label}: {message}") from exc


def _required_behavior_ids(evaluation: Mapping[str, Any]) -> set[str]:
    qualification = (
        evaluation.get("qualification")
        if isinstance(evaluation.get("qualification"), Mapping)
        else {}
    )
    requirements = (
        qualification.get("requirements")
        if isinstance(qualification.get("requirements"), Mapping)
        else {}
    )
    required = {"render.ready"}
    if requirements.get("component_type") == "collection.board":
        required.update({"board.lanes", "board.select"})
    if requirements.get("drag_drop") is True:
        required.update({"board.move", "board.move.alternative"})
    if requirements.get("resource_query") is True:
        required.update({"resource.query", "resource.filter"})
    required.update(
        f"resource.{operation}"
        for operation in requirements.get("operation_kinds") or []
        if str(operation).strip()
    )
    return required


def _checks(
    values: Sequence[Mapping[str, Any]],
    *,
    required: set[str],
) -> list[dict[str, Any]]:
    checks = [copy.deepcopy(dict(item)) for item in values]
    identifiers = [str(item.get("id") or "").strip() for item in checks]
    if len(identifiers) != len(set(identifiers)):
        raise BuilderWorkflowError("prototype behavior check ids must be unique")
    failed = sorted(
        identifier
        for identifier, item in zip(identifiers, checks, strict=True)
        if not identifier
        or str(item.get("status") or "").strip().lower() != "passed"
        or not [str(ref).strip() for ref in item.get("evidence_refs") or [] if str(ref).strip()]
    )
    if failed:
        raise BuilderWorkflowError(
            "prototype behavior checks require passed evidence: " + ", ".join(failed)
        )
    missing = sorted(required - set(identifiers))
    if missing:
        raise BuilderWorkflowError(
            "prototype acceptance is missing behavior evidence: " + ", ".join(missing)
        )
    return checks


def _visual_checks(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    checks = [copy.deepcopy(dict(item)) for item in values]
    breakpoints = {str(item.get("breakpoint") or "").strip(): item for item in checks}
    missing = sorted({"compact", "wide"} - set(breakpoints))
    if missing:
        raise BuilderWorkflowError(
            "prototype acceptance is missing visual evidence: " + ", ".join(missing)
        )
    compact_width = int(dict(breakpoints["compact"].get("viewport") or {}).get("width") or 0)
    wide_width = int(dict(breakpoints["wide"].get("viewport") or {}).get("width") or 0)
    if compact_width > 768 or wide_width < 1024 or compact_width >= wide_width:
        raise BuilderWorkflowError("prototype visual evidence does not cover compact and wide layouts")
    return checks


def build_prototype_acceptance(
    *,
    acceptance_id: str,
    project_ref: str,
    change_id: str,
    revision: str,
    webui: Mapping[str, Any],
    request: str,
    reviewer: Mapping[str, Any],
    behavior_checks: Sequence[Mapping[str, Any]],
    visual_checks: Sequence[Mapping[str, Any]],
    prototype_records: Sequence[Mapping[str, Any]] | None = None,
    prototype_resources: Sequence[Mapping[str, Any]] | None = None,
    accepted_at: str | None = None,
) -> dict[str, Any]:
    """Build acceptance only after deterministic, behavioral, and visual checks pass."""

    _validate("webui.v1.schema.json", webui, label="prototype WebUI")
    evaluation = evaluate_ui_request(request, webui, prototype_records=prototype_records)
    if not bool(evaluation.get("ok")):
        failures = [
            str(item.get("id") or item.get("code") or "unknown")
            for item in [
                *list(evaluation.get("postconditions") or []),
                *list(evaluation.get("capability_gaps") or []),
                *list(dict(evaluation.get("capability_validation") or {}).get("findings") or []),
            ]
            if isinstance(item, Mapping)
            and (item.get("ok") is False or item.get("severity") == "error" or "code" in item)
        ]
        raise BuilderWorkflowError(
            "prototype does not satisfy its qualified request"
            + (": " + ", ".join(failures) if failures else "")
        )
    checks = _checks(behavior_checks, required=_required_behavior_ids(evaluation))
    visuals = _visual_checks(visual_checks)
    timestamp = str(accepted_at or datetime.now(timezone.utc).isoformat()).strip()
    payload: dict[str, Any] = {
        "schema": PROTOTYPE_ACCEPTANCE_SCHEMA,
        "acceptance_id": str(acceptance_id).strip(),
        "project_ref": str(project_ref).strip(),
        "change_id": str(change_id).strip(),
        "revision": str(revision).strip(),
        "webui_digest": prototype_webui_digest(webui),
        "request_digest": canonical_payload_digest({"request": str(request)}),
        "reviewer": copy.deepcopy(dict(reviewer)),
        "decision": "accepted",
        "deterministic_evaluation": copy.deepcopy(dict(evaluation)),
        "prototype_resources": [
            copy.deepcopy(dict(item)) for item in prototype_resources or [] if isinstance(item, Mapping)
        ],
        "behavior_checks": checks,
        "visual_checks": visuals,
        "accepted_at": timestamp,
    }
    payload["digest"] = canonical_payload_digest(payload)
    _validate(
        "builder.prototype_acceptance.v1.schema.json",
        payload,
        label="prototype acceptance",
    )
    return payload


def admit_prototype_acceptance(
    value: Mapping[str, Any],
    *,
    expected_project_ref: str,
    expected_change_id: str,
    expected_revision: str,
    expected_webui_digest: str,
    expected_prototype_resources: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify immutable acceptance identity before crossing the Automation gate."""

    acceptance = copy.deepcopy(dict(value))
    _validate(
        "builder.prototype_acceptance.v1.schema.json",
        acceptance,
        label="prototype acceptance",
    )
    supplied_digest = str(acceptance.get("digest") or "")
    unsigned = copy.deepcopy(acceptance)
    unsigned.pop("digest", None)
    if supplied_digest != canonical_payload_digest(unsigned):
        raise BuilderWorkflowError("prototype acceptance digest does not match its evidence")
    expected = {
        "project_ref": expected_project_ref,
        "change_id": expected_change_id,
        "revision": expected_revision,
        "webui_digest": expected_webui_digest,
    }
    mismatches = [
        key for key, expected_value in expected.items() if str(acceptance.get(key) or "") != str(expected_value)
    ]
    if mismatches:
        raise BuilderWorkflowError(
            "prototype acceptance is stale or belongs to another Change: " + ", ".join(mismatches)
        )
    if expected_prototype_resources is not None:
        actual_resources = list(acceptance.get("prototype_resources") or [])
        expected_resources = [dict(item) for item in expected_prototype_resources]
        if canonical_payload_digest(actual_resources) != canonical_payload_digest(expected_resources):
            raise BuilderWorkflowError("prototype acceptance is stale: prototype_resources")
    if not bool(dict(acceptance.get("deterministic_evaluation") or {}).get("ok")):
        raise BuilderWorkflowError("prototype acceptance contains a failed deterministic evaluation")
    return acceptance


__all__ = [
    "PROTOTYPE_ACCEPTANCE_SCHEMA",
    "admit_prototype_acceptance",
    "build_prototype_acceptance",
]
