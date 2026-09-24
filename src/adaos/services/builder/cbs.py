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

from .cbs_intent import validate_cbs_intent
from .prototype_acceptance import admit_prototype_acceptance
from .workflow import BuilderWorkflowError


BUILDER_CBS_COMPILATION_SCHEMA = "adaos.builder.cbs_compilation.v1"
BUILDER_CBS_COMPILER_VERSION = "1.2.0"
BUILDER_CBS_COMPILER_VIEW_SCHEMA = "adaos.builder.cbs_compiler_view.v1"


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


def cbs_compilation_registry_ref(compilation_digest: str) -> str:
    digest = str(compilation_digest or "").strip().lower()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise BuilderWorkflowError("CBS compilation registry reference requires a digest")
    return (
        "cbs-registry://applications/compilations/sha256/"
        + digest.removeprefix("sha256:")
    )


def cbs_compiler_view(compilation: Mapping[str, Any]) -> dict[str, Any]:
    """Project canonical CBS into a compact, registry-backed model input.

    The full compilation remains authoritative in ApplicationCBSService. The
    Builder model receives identities, constraints, and digests required for
    implementation, without another copy of every canonical record envelope.
    """

    value = validate_cbs_compilation(compilation)
    requirements = []
    for raw in value.get("requirements") or []:
        item = dict(raw)
        policy_constraints = dict(item.get("policy_constraints") or {})
        evidence_threshold = dict(item.get("evidence_threshold") or {})
        requirements.append(
            {
                "requirement_ref": item["requirement_ref"],
                "requirement_digest": item["requirement_digest"],
                "capability_ref": item["capability_ref"],
                "contract_range": item["contract_range"],
                "locality": policy_constraints.get("locality"),
                "privacy": policy_constraints.get("privacy"),
                "required_authorities": list(
                    policy_constraints.get("required_authorities") or []
                ),
                "required_claim_kinds": list(
                    evidence_threshold.get("required_claim_kinds") or []
                ),
            }
        )
    return {
        "schema": BUILDER_CBS_COMPILER_VIEW_SCHEMA,
        "registry_ref": cbs_compilation_registry_ref(value["compilation_digest"]),
        "application_ref": value["application_ref"],
        "compiler_version": value["compiler_version"],
        "compilation_digest": value["compilation_digest"],
        "semantic_revision_digest": value["semantic_revision_digest"],
        "environment_target": copy.deepcopy(value["environment_target"]),
        "requirements": requirements,
        "simulation_attachments": [
            {
                key: copy.deepcopy(item.get(key))
                for key in (
                    "resource_type",
                    "state_space_ref",
                    "portability_class",
                    "generation",
                    "definition_digest",
                    "records_digest",
                )
            }
            for item in value.get("simulation_attachments") or []
        ],
        "automation_obligations": [
            {
                key: copy.deepcopy(item.get(key))
                for key in (
                    "source_requirement_ref",
                    "requirement_ref",
                    "capability_ref",
                    "statement_digest",
                    "status",
                )
            }
            for item in value.get("automation_obligations") or []
        ],
        "authoring_telemetry": copy.deepcopy(
            value.get("authoring_telemetry") or {}
        ),
        "viability": copy.deepcopy(value["viability"]),
        "authority": {
            "canonical_content": "registry_only",
            "model_input": "compiler_view",
            "mutation": "denied",
        },
    }


def _token(value: str, *, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._/-]+", "-", str(value).strip()).strip("-./")
    return token[:96] or fallback


def _requirement(
    *,
    requirement_ref: str,
    capability_ref: str,
    environment_target: Mapping[str, Any],
    stateful: bool = False,
    contract_range: str = "^1.0.0",
    locality: str = "remote_allowed",
    privacy: str = "application-declared",
    required_authorities: Sequence[str] = (),
    required_claim_kinds: Sequence[str] | None = None,
) -> dict[str, Any]:
    claim_kinds = list(required_claim_kinds or ("capability_conformance",))
    if stateful and "state_compatibility" not in claim_kinds:
        claim_kinds.append("state_compatibility")
    return ApplicationRequirement.create(
        requirement_ref=requirement_ref,
        capability_ref=capability_ref,
        contract_range=contract_range,
        environment_target=environment_target,
        policy_constraints={
            "locality": locality,
            "privacy": privacy,
            "required_authorities": list(required_authorities),
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
    cbs_intent = (
        validate_cbs_intent(admitted["cbs_intent"])
        if isinstance(admitted.get("cbs_intent"), Mapping)
        else None
    )
    authoring_counts = {"human_explicit": 0, "builder_inferred": 0, "generated": 1}
    seen_refs = {str(item["requirement_ref"]) for item in requirements}
    for item in (cbs_intent or {}).get("requirements") or []:
        requirement_ref = f"requirement:{application_token}.{item['id']}"
        if requirement_ref in seen_refs:
            raise BuilderWorkflowError("Builder CBS intent requirement ids are not unique after compilation")
        seen_refs.add(requirement_ref)
        origin = str(item["origin"])
        authoring_counts[origin] += 1
        requirements.append(
            _requirement(
                requirement_ref=requirement_ref,
                capability_ref=str(item["capability_ref"]),
                contract_range=str(item["contract_range"]),
                environment_target=environment_target,
                locality=str(item.get("locality") or "remote_allowed"),
                privacy=str(item.get("privacy") or "application-declared"),
                required_authorities=tuple(item.get("required_authorities") or ()),
                required_claim_kinds=tuple(
                    item.get("required_claim_kinds") or ("capability_conformance",)
                ),
            )
        )
    resources = [
        dict(item)
        for item in admitted.get("prototype_resources") or []
        if str(item.get("resource_type") or "") != "prototype.locale_dictionaries"
    ]
    attachments: list[dict[str, Any]] = []
    if resources and not (cbs_intent or {}).get("requirements"):
        requirements.append(
            _requirement(
                requirement_ref=f"requirement:{application_token}.records",
                capability_ref="capability:resource.records.manage",
                environment_target=environment_target,
                stateful=True,
            )
        )
        authoring_counts["generated"] += 1
    if resources:
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
        obligation_ref = f"automation-obligation:{application_token}.{short}"
        obligations.append(
            {
                "source_requirement_ref": source_ref,
                "requirement_ref": obligation_ref,
                "capability_ref": None,
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
        "authoring_telemetry": {
            "human_authored_requirements": authoring_counts["human_explicit"],
            "builder_inferred_requirements": authoring_counts["builder_inferred"],
            "compiler_generated_requirements": authoring_counts["generated"],
        },
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
    "BUILDER_CBS_COMPILER_VIEW_SCHEMA",
    "cbs_compilation_registry_ref",
    "cbs_compiler_view",
    "compile_prototype_cbs",
    "validate_cbs_compilation",
]
