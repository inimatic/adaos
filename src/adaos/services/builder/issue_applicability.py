"""Typed Issue lifetime at the existing Builder context admission boundary.

The implementation lane selects an author, not the lifetime of a prohibition.
Legacy Issues retain their existing behavior; explicit scope is never inferred
from natural language or silently rewritten at a phase transition.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence

PHASES = {"prototype", "automation"}
KINDS = {"product_requirement", "phase_constraint", "process_constraint", "platform_obligation"}
SCOPES = {"prototype", "automation", "production", "development", "core"}


def normalize_issue_applicability(value: Mapping) -> dict:
    """Validate optional typed fields without guessing metadata for old Issues."""
    result = {}
    for name, choices in (("kind", KINDS), ("verification_scope", SCOPES),
                          ("effect", {"require", "forbid"}), ("owner", {"application", "core"})):
        if name in value:
            if not isinstance(value[name], str) or value[name] not in choices:
                raise ValueError(f"Issue {name} must be one of {sorted(choices)}")
            result[name] = value[name]
    if "applicable_phases" in value:
        phases = value["applicable_phases"]
        if not isinstance(phases, list) or not phases or any(not isinstance(p, str) or p not in PHASES for p in phases):
            raise ValueError("Issue applicable_phases requires prototype and/or automation")
        result["applicable_phases"] = sorted(set(phases))
    for name in ("effect_ref", "source_ref"):
        if name in value:
            ref = value[name]
            if not isinstance(ref, str) or not ref.strip() or len(ref) > 500:
                raise ValueError(f"Issue {name} must be nonempty text of at most 500 characters")
            result[name] = ref.strip()
    if "phase_dispositions" in value:
        dispositions = value["phase_dispositions"]
        if not isinstance(dispositions, Mapping) or set(dispositions) - PHASES:
            raise ValueError("Issue phase_dispositions has an invalid phase")
        for phase, entry in dispositions.items():
            if not isinstance(entry, Mapping) or entry.get("action") not in {"retain", "discharge", "replace"}:
                raise ValueError("Issue disposition requires retain, discharge, or replace")
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
                raise ValueError("Issue disposition requires a bounded reason")
            refs = entry.get("replacement_issue_ids", [])
            if not isinstance(refs, list) or len(refs) > 50 or any(not isinstance(r, str) or not r for r in refs):
                raise ValueError("Issue disposition has invalid replacement_issue_ids")
            if (entry["action"] == "replace") != bool(refs):
                raise ValueError("Only replace requires replacement_issue_ids")
        result["phase_dispositions"] = copy.deepcopy(dict(dispositions))
    return result


def project_phase_issues(issues: Sequence[Mapping], *, phase: str) -> dict:
    """Return author obligations and explicit transition receipts, or reject.

    This is read-only. A discharged phase restriction is still retained in the
    canonical Change and may be required when returning to Prototype.
    """
    if phase not in PHASES:
        raise ValueError("Invalid Issue execution phase")
    by_id = {str(row["issue_id"]): row for row in issues}
    active, platform, transitions, effects = [], [], [], {}
    for raw in issues:
        row = copy.deepcopy(dict(raw))
        typed = normalize_issue_applicability(row)
        identity = row["issue_id"]
        applicable = phase in typed.get("applicable_phases", PHASES)
        explicit = typed.get("phase_dispositions", {}).get(phase, {})
        action = explicit.get("action", "retain" if applicable else "discharge")
        if action == "retain" and not applicable:
            raise ValueError(f"Issue {identity}: retain contradicts applicable_phases for {phase}")
        if action == "replace":
            for ref in explicit["replacement_issue_ids"]:
                replacement = by_id.get(ref)
                if (ref == identity or replacement is None
                        or phase not in replacement.get("applicable_phases", PHASES)
                        or replacement.get("status") in {"deferred", "resolved"}
                        or replacement.get("phase_dispositions", {}).get(phase, {}).get("action", "retain") != "retain"):
                    raise ValueError(f"Issue {identity}: replacement {ref} is not active in {phase}")
        transitions.append({"issue_id": identity, "phase": phase, "action": action,
                            "reason": explicit.get("reason", "applicable_phase" if applicable else "outside_declared_phase"),
                            "replacement_issue_ids": list(explicit.get("replacement_issue_ids", []))})
        if action != "retain":
            continue
        target = platform if typed.get("owner") == "core" or typed.get("kind") == "platform_obligation" else active
        target.append(row)
        if row.get("status") in {"resolved", "deferred"} or row.get("structural_status", "active") != "active":
            continue
        if typed.get("effect_ref") and typed.get("effect"):
            key = (typed["effect_ref"], typed.get("verification_scope", phase), typed.get("owner", "application"))
            previous = effects.get(key)
            if previous and previous[0] != typed["effect"]:
                raise ValueError(f"Conflicting Issues {previous[1]} and {identity} for {key[0]} in {key[1]}")
            effects[key] = (typed["effect"], identity)
    return {"schema": "adaos.builder.issue_phase_projection.v1", "phase": phase,
            "issues": active, "platform_obligations": platform, "transitions": transitions}


def automation_gate_contract() -> dict:
    """Describe the actual post-return worker gates without granting authorship."""
    return {"schema": "adaos.builder.automation_gate_contract.v1",
            "prototype_acceptance_authority": "acceptance_stage identifies the reviewed phase. Prototype disclosures and local examples describe that phase; pending Automation clauses still require production behavior checks. Preserve inherited text and its provenance, resolve ambiguous scope against the current typed Change and explicit application brief, and report unresolved conflicts. A Prototype review does not certify Automation behavior or create a production API prohibition.",
            "requirement_authority": "Use the current Change's typed phase dispositions and verification scope. Retained historical briefs do not reinstate discharged phase restrictions; report ambiguous untyped conflicts instead of silently discarding them.",
            "candidate_return": "Return the implementation and an honest structured outcome. The worker validates after return; do not claim its gates passed.",
            "gates": [{"id": name, "owner": "worker", "prerequisite": prerequisite,
                       "invocation": "automatic_after_candidate_return", "evidence": "output/test_report.json",
                       "application_may_certify": False}
                      for name, prerequisite in (("source_boundary", "candidate_returned"),
                                                 ("workspace_validation", "source_boundary_passed"),
                                                 ("post_test_source_boundary", "workspace_validation_finished"))],
            "platform_obligations": "Report Core-owned blockers through development feedback; do not edit Core or fabricate verification receipts."}
