"""Hub-owned release source selection, independent of installed provenance."""
from __future__ import annotations

import os
import re
from typing import Any, Mapping

from adaos.services.core_update_policy import current_env_type
from adaos.services.node_runtime_state import load_node_runtime_state, mutate_node_runtime_state

from adaos.domain.release_source import DEFAULT_SOURCE_BRANCH, SourcePolicyError, validate_source_branch

SCHEMA = "adaos.source_policy.v1"
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")



def _config(conf: Any = None) -> Any:
    if conf is not None:
        return conf
    from adaos.services.agent_context import get_ctx
    return get_ctx().config


def configured_source_branch() -> str:
    return validate_source_branch(os.getenv("SRC_BRANCH") or DEFAULT_SOURCE_BRANCH)


def source_policy(conf: Any = None) -> dict[str, Any]:
    """Resolve the configured source, retaining member policy across disconnects."""
    conf = _config(conf)
    hub = str(getattr(conf, "subnet_id", "") or "").strip()
    if current_env_type() == "dev":
        return {"schema": SCHEMA, "branch": configured_source_branch(), "revision": 0,
                "hub_id": hub, "authority": "developer", "confirmed": True}
    if str(getattr(conf, "role", "") or "").lower() != "hub":
        saved = load_node_runtime_state().get("source_policy") or {}
        if saved.get("hub_id") == hub and saved.get("authority") == "hub" and saved.get("confirmed"):
            return dict(saved)
        return {"schema": SCHEMA, "branch": DEFAULT_SOURCE_BRANCH, "revision": 0,
                "hub_id": hub, "authority": "hub", "confirmed": False}
    if not hub:
        raise SourcePolicyError("source_policy_hub_identity_required")
    branch = configured_source_branch()

    def update(state):
        previous = state.get("source_policy") or {}
        if (previous.get("branch") == branch and previous.get("hub_id") == hub
                and previous.get("authority") == "hub" and previous.get("confirmed")):
            return
        state["source_policy"] = {"schema": SCHEMA, "branch": branch,
            "revision": int(previous.get("revision") or 0) + 1, "hub_id": hub,
            "authority": "hub", "confirmed": True}

    return dict(mutate_node_runtime_state(update)["source_policy"])


def inherit_source_policy(payload: Mapping[str, Any], conf: Any = None) -> dict[str, Any]:
    """Called only by the authenticated member/Hub link, never by Root events."""
    conf = _config(conf)
    hub = str(getattr(conf, "subnet_id", "") or "").strip()
    if current_env_type() == "dev":
        return source_policy(conf)
    if str(getattr(conf, "role", "") or "").lower() != "member":
        raise SourcePolicyError("source_policy_member_required")
    revision = payload.get("revision")
    if (payload.get("schema") != SCHEMA or not hub or payload.get("hub_id") != hub
            or type(revision) is not int or revision < 1
            or payload.get("authority") != "hub" or payload.get("confirmed") is not True):
        raise SourcePolicyError("source_policy_authority_mismatch")
    branch = validate_source_branch(payload.get("branch"))
    policy = {"schema": SCHEMA, "branch": branch, "revision": revision,
              "hub_id": hub, "authority": "hub", "confirmed": True}

    def update(state):
        previous = state.get("source_policy") or {}
        if previous.get("hub_id") == hub:
            old_revision = int(previous.get("revision") or 0)
            if old_revision > revision:
                raise SourcePolicyError("source_policy_stale")
            if old_revision == revision and previous.get("branch") != branch:
                raise SourcePolicyError("source_policy_revision_conflict")
        state["source_policy"] = policy

    return dict(mutate_node_runtime_state(update)["source_policy"])


def verify_source_release(branch: str, commit: str, *, conf: Any = None,
                          policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = dict(policy) if policy is not None else source_policy(conf)
    if not selected.get("confirmed"):
        raise SourcePolicyError("source_policy_not_confirmed")
    if validate_source_branch(branch) != selected["branch"]:
        raise SourcePolicyError("release_source_branch_mismatch")
    if not _COMMIT.fullmatch(str(commit or "")):
        raise SourcePolicyError("release_exact_commit_required")
    return selected


def delivery_snapshot(conf: Any = None) -> dict[str, Any]:
    """Project configured, prepared and active identities without private paths."""
    from adaos.services.core_slots import active_slot_manifest, slot_status
    from adaos.services.core_update import read_status
    selected = source_policy(conf)
    manifest = active_slot_manifest() or {}
    update = read_status() or {}
    slots = slot_status() or {}
    desired = load_node_runtime_state().get("desired_core_release") or {}
    desired_current = desired.get("policy") == selected
    active = {"branch": manifest.get("source_branch") or manifest.get("target_rev"),
              "commit": manifest.get("git_commit"),
              "version": manifest.get("build_version") or manifest.get("target_version"),
              "slot": slots.get("active_slot")}
    return {"schema": "adaos.source_delivery.snapshot.v1", "ok": True, "policy": selected,
        "active": active, "branch_matches": bool(selected.get("confirmed") and active["branch"] == selected["branch"]),
        "desired": desired if desired_current else {},
        "update": {key: update.get(key) for key in ("state", "phase", "action", "target_rev", "target_version",
            "started_at", "finished_at", "error", "message", "recovery_required") if key in update},
        "prepared": _prepared_slot(slots),
        "reconciliation_required": (not selected.get("confirmed") or active["branch"] != selected["branch"]
            or bool(desired_current and desired.get("commit") != active["commit"]))}


def remember_desired_release(release: Mapping[str, Any], *, conf: Any = None,
                             policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Retain an authenticated Root release separately from installed provenance.

    Called after Root lookup or an authenticated Hub link, never with browser
    input. Producer run order fences delayed notifications within a source list.
    """
    branch = str(release.get("source_branch") or release.get("branch") or "")
    commit = str(release.get("head_sha") or "")
    selected = verify_source_release(branch, commit, conf=conf, policy=policy)
    proof = release.get("qualification") or {}
    if proof and (proof.get("head_sha") != commit or proof.get("source_branch") != branch):
        raise SourcePolicyError("release_qualification_identity_mismatch")
    run_id, attempt = proof.get("run_id", 0), proof.get("run_attempt", 0)
    if type(run_id) is not int or type(attempt) is not int or min(run_id, attempt) < 0:
        raise SourcePolicyError("release_qualification_order_invalid")
    desired = {"branch": branch, "commit": commit, "version": release.get("version"),
               "policy": selected, "run_id": run_id, "run_attempt": attempt}

    def update(state):
        if state.get("source_policy") and state["source_policy"] != selected:
            raise SourcePolicyError("source_policy_changed")
        previous = state.get("desired_core_release") or {}
        if previous.get("policy") == selected:
            old_order = (int(previous.get("run_id") or 0), int(previous.get("run_attempt") or 0))
            if old_order > (run_id, attempt):
                raise SourcePolicyError("release_notification_stale")
            if old_order == (run_id, attempt) and run_id and previous.get("commit") != commit:
                raise SourcePolicyError("release_notification_conflict")
        state["desired_core_release"] = desired

    return dict(mutate_node_runtime_state(update)["desired_core_release"])


def _prepared_slot(slots: Mapping[str, Any]) -> dict[str, Any]:
    inactive = "B" if slots.get("active_slot") == "A" else "A"
    candidate = (slots.get("slots") or {}).get(inactive) or {}
    manifest = candidate.get("manifest") or {}
    return {"slot": inactive, "branch": manifest.get("source_branch") or manifest.get("target_rev"),
            "commit": manifest.get("git_commit"),
            "version": manifest.get("build_version") or manifest.get("target_version")}
