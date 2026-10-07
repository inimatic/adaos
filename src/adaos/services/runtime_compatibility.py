from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


CLASSIFICATION_SCHEMA = "adaos.runtime_compatibility.classification.v1"
SNAPSHOT_SCHEMA = "adaos.runtime_compatibility.snapshot.v1"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _boolean(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _active_builder_work(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    terminal = {"closed", "cancelled", "failed", "rejected", "superseded", "verified"}
    return [
        dict(item)
        for item in items
        if _text(item.get("status")).lower() not in terminal
    ][:20]


def classify_runtime_compatibility(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Classify runtime compatibility evidence without mutation or model calls.

    The classifier intentionally fails closed. A user decision is offered only
    when the exact desired and installed package identities are known and the
    proposed update is pinned to that desired identity.
    """

    desired = _mapping(snapshot.get("desired_release"))
    installed = _mapping(snapshot.get("installed_release"))
    loaded = _mapping(snapshot.get("loaded_runtime"))
    policy = _mapping(snapshot.get("receiver_policy"))
    observation = _mapping(snapshot.get("observation"))
    core = _mapping(snapshot.get("core_contract"))
    update = _mapping(snapshot.get("eligible_update"))
    builder_work = _active_builder_work(
        [item for item in snapshot.get("builder_work") or () if isinstance(item, Mapping)]
    )

    desired_digest = _text(desired.get("package_digest"))
    installed_digest = _text(installed.get("package_digest"))
    loaded_digest = _text(loaded.get("package_digest"))
    desired_admitted = desired.get("admitted") is True
    module_available = _boolean(loaded.get("module_available"))
    source_drift = bool(loaded.get("source_drift"))
    selection_drift = bool(loaded.get("selection_drift"))
    policy_state = _text(policy.get("state")).lower()
    own_stream = _boolean(observation.get("own_stream"))
    core_supported = _boolean(core.get("supported"))

    missing_evidence: list[str] = []
    if not desired_digest:
        missing_evidence.append("desired_release.package_digest")
    if not installed_digest:
        missing_evidence.append("installed_release.package_digest")
    if not _text(loaded.get("generation")):
        missing_evidence.append("loaded_runtime.generation")
    if module_available is None:
        missing_evidence.append("loaded_runtime.module_available")
    if not policy_state:
        missing_evidence.append("receiver_policy.state")
    if core_supported is None:
        missing_evidence.append("core_contract.supported")

    code = "compatible"
    owner = "runtime"
    recommended_action = "none"
    automatic_recovery_eligible = False
    human_decision_required = False
    explanation = "The admitted package, loaded runtime, and receiver policy agree."

    if builder_work:
        code = "existing_builder_work"
        owner = "builder"
        recommended_action = "continue_existing_builder_work"
        explanation = "A scoped Builder repair already exists; continue it instead of creating another request."
    elif core_supported is False:
        code = "unsupported_core_contract"
        owner = "core"
        recommended_action = "route_to_core_upgrade"
        explanation = "The installed package requires a Core contract that this runtime does not support."
    elif desired.get("admitted") is False:
        code = "desired_release_not_admitted"
        owner = "artifact_authority"
        recommended_action = "restore_admitted_selection"
        explanation = "The desired release is not admitted and cannot be activated or repaired implicitly."
    elif own_stream is False:
        code = "foreign_stream_observation"
        owner = "runtime"
        recommended_action = "record_diagnostic_only"
        explanation = "The observed broadcast is owned by another component and must not expand this skill's policy."
    elif module_available is False:
        code = "unavailable_module"
        owner = "runtime"
        recommended_action = "restore_exact_admitted_package"
        automatic_recovery_eligible = bool(desired_admitted and desired_digest and desired_digest == installed_digest)
        explanation = "The exact admitted package is installed, but its runtime module is unavailable."
    elif selection_drift or (
        desired_digest
        and installed_digest == desired_digest
        and loaded_digest
        and loaded_digest != installed_digest
    ):
        code = "stale_runtime_memory"
        owner = "runtime"
        recommended_action = "reactivate_exact_admitted_package"
        automatic_recovery_eligible = bool(
            desired_admitted
            and desired_digest == installed_digest
            and not source_drift
        )
        explanation = "The selected admitted package differs from the handlers currently held in memory."
    elif source_drift:
        code = "application_source_drift"
        owner = "application"
        recommended_action = "open_scoped_builder_repair"
        explanation = "The loaded handler bytes no longer match the installed package source."
    elif desired_digest and installed_digest and desired_digest != installed_digest:
        exact_update = (
            update.get("eligible") is True
            and _text(update.get("package_digest")) == desired_digest
            and _text(update.get("from_package_digest")) == installed_digest
        )
        if exact_update:
            code = "eligible_exact_update"
            owner = "artifact_authority"
            recommended_action = "offer_exact_update"
            human_decision_required = True
            explanation = "An admitted update exactly matches the desired package identity."
        else:
            code = "release_identity_mismatch"
            owner = "artifact_authority"
            recommended_action = "reconcile_release_identity"
            explanation = "Desired and installed package identities differ without an eligible exact update."
    elif policy_state in {"absent", "invalid", "explicit_none"}:
        code = "application_declaration_defect"
        owner = "application"
        recommended_action = "open_scoped_builder_repair"
        explanation = "The current admitted application package has no valid owned receiver declaration."
    elif own_stream is True and observation.get("receiver_admitted") is False:
        code = "application_declaration_defect"
        owner = "application"
        recommended_action = "open_scoped_builder_repair"
        explanation = "The application owns this receiver but its current declaration does not admit it."
    elif missing_evidence:
        code = "insufficient_evidence"
        owner = "runtime"
        recommended_action = "collect_read_only_evidence"
        explanation = "The runtime cannot safely choose update, reactivation, or repair from the available evidence."

    # Even if an earlier rule is suggestive, incomplete identity evidence must
    # never become a human update decision or an automatic package mutation.
    evidence_complete = not missing_evidence
    if not evidence_complete:
        human_decision_required = False
        automatic_recovery_eligible = False
        if code == "compatible":
            code = "insufficient_evidence"
            recommended_action = "collect_read_only_evidence"
            explanation = "The runtime cannot prove that the loaded and admitted package identities agree."

    return {
        "schema": CLASSIFICATION_SCHEMA,
        "code": code,
        "owner": owner,
        "evidence_complete": evidence_complete,
        "missing_evidence": missing_evidence,
        "recommended_action": recommended_action,
        "automatic_recovery_eligible": automatic_recovery_eligible,
        "human_decision_required": human_decision_required,
        "explanation": explanation,
        "existing_builder_work": builder_work,
        "desired_package_digest": desired_digest or None,
        "installed_package_digest": installed_digest or None,
        "loaded_package_digest": loaded_digest or None,
        "eligible_update_version": _text(update.get("version")) or None,
        "eligible_update_package_digest": _text(update.get("package_digest")) or None,
        "eligible_update_from_package_digest": _text(update.get("from_package_digest")) or None,
        "eligible_update_consequences": _mapping(update.get("consequences")),
    }


def collect_skill_runtime_compatibility_snapshot(
    skill_id: str,
    *,
    admission: Mapping[str, Any],
    builder_work: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Collect bounded process-local evidence for a receiver guard finding.

    Package identity is deliberately left unknown when the runtime registries
    do not expose an authoritative digest. The classifier then requests more
    read-only evidence instead of guessing from a path, version, or broadcast.
    """

    from adaos.services.skill.declarations import runtime_skill_declarations_snapshot
    from adaos.services.skills_loader_importlib import skill_handler_source_snapshot

    skill = _text(skill_id)
    declaration = runtime_skill_declarations_snapshot(skill)
    sources = skill_handler_source_snapshot()
    handlers = [
        dict(item)
        for item in sources.get("items") or ()
        if isinstance(item, Mapping) and _text(item.get("skill")) == skill
    ][:20]
    selected_versions = sorted({_text(item.get("selected_version")) for item in handlers if _text(item.get("selected_version"))})
    generations = [float(item.get("loaded_at") or 0.0) for item in handlers]
    policy_reason = _text(admission.get("reason"))
    policy_state = "absent" if policy_reason == "stream_receiver_policy_missing" else "valid"
    if policy_reason == "stream_receiver_not_declared":
        policy_state = "valid"
    return {
        "schema": SNAPSHOT_SCHEMA,
        "skill_id": skill,
        "desired_release": {
            "admitted": bool(handlers),
            "version": selected_versions[0] if len(selected_versions) == 1 else None,
            "package_digest": None,
        },
        "installed_release": {
            "version": selected_versions[0] if len(selected_versions) == 1 else None,
            "package_digest": None,
        },
        "loaded_runtime": {
            "module_available": bool(handlers),
            "generation": str(max(generations)) if generations else None,
            "package_digest": None,
            "handler_count": len(handlers),
            "source_drift": any(bool(item.get("source_drift")) for item in handlers),
            "selection_drift": any(bool(item.get("selection_drift")) for item in handlers),
            "handlers": [
                {
                    "module": item.get("module"),
                    "loaded_bucket": item.get("loaded_bucket"),
                    "loaded_slot": item.get("loaded_slot"),
                    "selected_bucket": item.get("selected_bucket"),
                    "selected_slot": item.get("selected_slot"),
                    "current_exists": item.get("current_exists"),
                    "source_drift": item.get("source_drift"),
                    "selection_drift": item.get("selection_drift"),
                }
                for item in handlers
            ],
        },
        "receiver_policy": {
            "state": policy_state,
            "patterns": list(declaration.get("receiver_patterns") or admission.get("receiver_patterns") or ())[:12],
        },
        "observation": {
            "reason": policy_reason,
            "receiver": _text(admission.get("receiver")) or None,
            "own_stream": None,
            "receiver_admitted": admission.get("allowed") is True,
        },
        "core_contract": {"supported": True},
        "eligible_update": {},
        "builder_work": [dict(item) for item in builder_work if isinstance(item, Mapping)][:20],
    }


__all__ = [
    "CLASSIFICATION_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "classify_runtime_compatibility",
    "collect_skill_runtime_compatibility_snapshot",
]
