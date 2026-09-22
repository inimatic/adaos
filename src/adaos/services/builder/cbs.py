"""Compile admitted Builder prototypes into package-neutral CBS requirements."""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import ApplicationRequirement

from .prototype_acceptance import admit_prototype_acceptance
from .workflow import BuilderWorkflowError


BUILDER_CBS_COMPILATION_SCHEMA = "adaos.builder.cbs_compilation.v1"
BUILDER_CBS_COMPILER_VERSION = "1.0.0"


@lru_cache(maxsize=1)
def _compilation_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / "builder.cbs_compilation.v1.schema.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def validate_cbs_compilation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed compilation ABI and its canonical digest."""

    result = copy.deepcopy(dict(value))
    try:
        Draft202012Validator(_compilation_schema()).validate(result)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            location = ".".join(str(item) for item in exc.absolute_path)
            suffix = f" at {location}" if location else ""
            message = f"{exc.message}{suffix}"
        else:
            message = str(exc)
        raise BuilderWorkflowError(f"invalid Builder CBS compilation: {message}") from exc
    supplied = str(result.get("compilation_digest") or "")
    unsigned = copy.deepcopy(result)
    unsigned.pop("compilation_digest", None)
    if supplied != canonical_payload_digest(unsigned):
        raise BuilderWorkflowError("Builder CBS compilation digest does not match its content")
    # Re-parse embedded requirements so their own closed ABI and digest are
    # verified rather than delegated to the intentionally compositional schema.
    for item in result["requirements"]:
        ApplicationRequirement.from_mapping(item)
    return result


def _token(value: str, *, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._/-]+", "-", str(value).strip()).strip("-./")
    return token[:96] or fallback


def _requirement(
    *,
    requirement_ref: str,
    capability_ref: str,
    environment_target: Mapping[str, Any],
    stateful: bool = False,
) -> dict[str, Any]:
    claim_kinds = ["capability_conformance"]
    if stateful:
        claim_kinds.append("state_compatibility")
    return ApplicationRequirement.create(
        requirement_ref=requirement_ref,
        capability_ref=capability_ref,
        contract_range="^1.0.0",
        environment_target=environment_target,
        policy_constraints={
            "locality": "remote_allowed",
            "privacy": "application-declared",
            "required_authorities": [],
        },
        evidence_threshold={
            "required_claim_kinds": claim_kinds,
            "allow_stale": False,
        },
    ).to_dict()


def compile_prototype_cbs(
    acceptance: Mapping[str, Any],
    *,
    environment_profile_ref: str = "profile:local/default",
    allowed_modes: Sequence[str] = ("simulation", "production"),
) -> dict[str, Any]:
    """Compile one admitted Prototype acceptance without selecting packages.

    The output deliberately carries semantic requirements and disposable
    simulation attachments only. BindingDefinition, PackageRelease,
    BindingInstance, and production StateSpace selection remain resolver and
    activation concerns.
    """

    source = dict(acceptance)
    admitted = admit_prototype_acceptance(
        source,
        expected_project_ref=str(source.get("project_ref") or ""),
        expected_change_id=str(source.get("change_id") or ""),
        expected_revision=str(source.get("revision") or ""),
        expected_webui_digest=str(source.get("webui_digest") or ""),
        expected_prototype_resources=[
            dict(item) for item in source.get("prototype_resources") or []
        ],
        expected_automation_requirements=[
            dict(item) for item in source.get("automation_requirements") or []
        ],
    )
    modes = tuple(dict.fromkeys(str(item).strip() for item in allowed_modes if str(item).strip()))
    if not modes or any(item not in {"simulation", "sandbox", "production"} for item in modes):
        raise BuilderWorkflowError("CBS allowed_modes must contain known environment modes")
    profile_ref = str(environment_profile_ref or "").strip()
    if not profile_ref.startswith("profile:"):
        raise BuilderWorkflowError("CBS environment profile must be a profile: reference")

    application_ref = str(admitted["project_ref"])
    application_token = _token(application_ref.replace(":", "."), fallback="application")
    environment_target = {
        "profile_ref": profile_ref,
        "allowed_modes": list(modes),
    }
    requirements = [
        _requirement(
            requirement_ref=f"requirement:{application_token}.ui",
            capability_ref="capability:application.ui.render",
            environment_target=environment_target,
        )
    ]
    resources = [
        dict(item)
        for item in admitted.get("prototype_resources") or []
        if str(item.get("resource_type") or "") != "prototype.locale_dictionaries"
    ]
    attachments: list[dict[str, Any]] = []
    if resources:
        requirements.append(
            _requirement(
                requirement_ref=f"requirement:{application_token}.records",
                capability_ref="capability:resource.records.manage",
                environment_target=environment_target,
                stateful=True,
            )
        )
        for item in sorted(resources, key=lambda value: str(value.get("resource_type") or "")):
            resource_type = str(item["resource_type"])
            resource_token = _token(resource_type.removeprefix("prototype."), fallback="records")
            attachments.append(
                {
                    "resource_type": resource_type,
                    "state_space_ref": (
                        f"state-space:{application_token}.simulation.{resource_token}"
                    ),
                    "portability_class": "reconstructible",
                    "generation": int(item["generation"]),
                    "definition_digest": str(item["definition_digest"]),
                    "records_digest": str(item["records_digest"]),
                }
            )

    obligations: list[dict[str, Any]] = []
    seen_refs = {str(item["requirement_ref"]) for item in requirements}
    for index, item in enumerate(admitted.get("automation_requirements") or []):
        source_ref = str(item.get("requirement_ref") or f"automation-{index + 1}")
        identity_digest = canonical_payload_digest(
            {
                "source_requirement_ref": source_ref,
                "brief_ref": str(item.get("brief_ref") or ""),
                "brief_digest": str(item.get("brief_digest") or ""),
            }
        )
        short = identity_digest.removeprefix("sha256:")[:16]
        requirement_ref = f"requirement:{application_token}.automation.{short}"
        if requirement_ref in seen_refs:
            raise BuilderWorkflowError("prototype automation requirements do not have unique identities")
        seen_refs.add(requirement_ref)
        capability_ref = f"capability:application.automation.{short}"
        requirements.append(
            _requirement(
                requirement_ref=requirement_ref,
                capability_ref=capability_ref,
                environment_target=environment_target,
            )
        )
        obligations.append(
            {
                "source_requirement_ref": source_ref,
                "requirement_ref": requirement_ref,
                "capability_ref": capability_ref,
                "statement_digest": canonical_payload_digest(
                    {
                        "statement": str(item.get("statement") or ""),
                        "acceptance": str(item.get("acceptance") or ""),
                    }
                ),
                "status": "unresolved",
            }
        )

    semantic_revision_digest = canonical_payload_digest(
        {
            "application_ref": application_ref,
            "revision": str(admitted["revision"]),
            "webui_digest": str(admitted["webui_digest"]),
            "request_digest": str(admitted["request_digest"]),
            "requirements": requirements,
        }
    )
    result: dict[str, Any] = {
        "schema": BUILDER_CBS_COMPILATION_SCHEMA,
        "compiler_version": BUILDER_CBS_COMPILER_VERSION,
        "application_ref": application_ref,
        "source_acceptance_digest": str(admitted["digest"]),
        "semantic_revision_digest": semantic_revision_digest,
        "environment_target": environment_target,
        "requirements": requirements,
        "simulation_attachments": attachments,
        "automation_obligations": obligations,
        "viability": {
            "semantic": "compiled",
            "simulation": "accepted" if "simulation" in modes else "pending",
            "production": "unresolved",
            "unresolved_requirement_refs": [
                str(item["requirement_ref"]) for item in requirements
            ],
        },
    }
    result["compilation_digest"] = canonical_payload_digest(result)
    return validate_cbs_compilation(result)


__all__ = [
    "BUILDER_CBS_COMPILATION_SCHEMA",
    "BUILDER_CBS_COMPILER_VERSION",
    "compile_prototype_cbs",
    "validate_cbs_compilation",
]
