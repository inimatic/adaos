"""DEV-only lifecycle steps over the same SDK gates used by Builder."""

from __future__ import annotations

import json
import hashlib
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.sdk.builder import automation, lifecycle, workflow
from adaos.sdk.developer import projects
from adaos.services.resources.prototype import prototype_webui_digest


STEP_TYPES = frozenset({"builder.workflow", "prototype.accept", "automation.start", "automation.submit", "automation.wait",
                        "trial.prepare", "trial.decide", "release.promote"})


def _target(inputs: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[str, str]:
    if os.getenv("ENV_TYPE", "").strip().lower() != "dev" or not context.get("retain_test_projects"):
        raise ValueError("lifecycle evaluation requires ENV_TYPE=dev and retained test projects")
    kind, identifier = str(inputs.get("object_type") or "scenario"), str(inputs.get("object_id") or "")
    ref = f"{kind}:{identifier}"
    if kind not in {"scenario", "skill"} or not identifier or not any(
        item.get("primary_ref") == ref and item.get("draft_id")
        for item in context.get("owned_artifacts", [])
    ):
        raise ValueError("lifecycle target must be a primary component created by this case")
    if inputs.get("project_ref") and not any(
        item.get("primary_ref") == ref and inputs["project_ref"] == f"project:{item.get('project_id')}"
        for item in context.get("owned_artifacts", [])
    ):
        raise ValueError("publication project must own this case's target")
    return kind, identifier


def _evidence_path(raw: str, context: Mapping[str, Any]) -> Path:
    root = Path(context["bundle_dir"]).resolve()
    path = (root / raw).resolve()
    if not raw or path == root or not path.is_relative_to(root):
        raise ValueError("review evidence must be a file inside this run bundle")
    return path


def _review(inputs: Mapping[str, Any], context: Mapping[str, Any], *, expected: Mapping[str, str]) -> tuple[dict[str, Any], str]:
    from adaos.e2e.builder import BuilderE2EUnavailable

    path = _evidence_path(str(inputs.get("review_file") or ""), context)
    deadline = time.monotonic() + max(0.0, min(float(inputs.get("wait_seconds") or 0), float(inputs.get("timeout_seconds") or 1800)))
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise BuilderE2EUnavailable(f"explicit review is required at {path}")
        time.sleep(1)
    review = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(review, Mapping) or any(review.get(key) != value for key, value in expected.items()):
        raise ValueError("review does not match the exact current lifecycle artifact")
    refs = [str(ref) for check in review.get("behavior_checks", []) for ref in check.get("evidence_refs", [])]
    refs.extend(str(check.get("evidence_ref") or "") for check in review.get("visual_checks", []))
    if not refs or any(not _evidence_path(ref, context).is_file() for ref in refs):
        raise ValueError("review must reference existing evidence files within this run")
    return dict(review), path.relative_to(Path(context["bundle_dir"]).resolve()).as_posix()


def _webui(kind: str, identifier: str) -> dict[str, Any]:
    result = projects.read_file(kind, identifier, "webui.json", max_bytes=1_048_576)
    if result.get("truncated"):
        raise ValueError("Prototype exceeds the bounded review read contract")
    return json.loads(result["content"])


def execute(step_type: str, inputs: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    kind, identifier = _target(inputs, context)
    webspace = str(context["webspace_id"])
    actor = f"agent:builder-e2e:{context['run_id']}"
    key = f"{context['run_id']}:{context['case_id']}:{context['repetition']}:{context['step_id']}"
    if step_type == "builder.workflow":
        return {"ok": True, "state": workflow.get_state(kind, identifier)}
    if step_type == "prototype.accept":
        webui = _webui(kind, identifier)
        expected = {"stage": "prototype", "project_ref": f"{kind}:{identifier}", "webui_digest": prototype_webui_digest(webui)}
        review, ref = _review(inputs, context, expected=expected)
        # Re-read after a possible reviewer wait; never approve a newly edited UI.
        current = _webui(kind, identifier)
        if prototype_webui_digest(current) != expected["webui_digest"]:
            raise ValueError("prototype changed while waiting for review")
        state = workflow.get_state(kind, identifier)
        existing = (state.get("prototype") or {}).get("acceptance") or {}
        if existing.get("acceptance_id") == f"acceptance:e2e:{key}":
            admitted = workflow.require_current_prototype_acceptance(kind, identifier)
            return {"ok": True, "acceptance": admitted, "duplicate": True, "evidence_ref": ref, "review_interventions": 1}
        result = workflow.accept_prototype(kind, identifier, reviewer=review["reviewer"],
            behavior_checks=review["behavior_checks"], visual_checks=review["visual_checks"],
            actor=actor, acceptance_id=f"acceptance:e2e:{key}", expected_generation=state.get("generation"))
        return {"ok": True, **result, "evidence_ref": ref, "review_interventions": 1}
    if step_type == "automation.start":
        brief = str(inputs.get("implementation_brief") or "").strip()
        if not brief:
            raise ValueError("Automation needs an explicit implementation brief, not inferred fixture behavior")
        conversation_id = f"conversation:e2e:automation:{key}"
        intent_path = _evidence_path(f"evidence/lifecycle/{context['case_id']}-{context['repetition']}-{context['step_id']}.intent.json", context)
        digest = hashlib.sha256(json.dumps(dict(inputs), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if intent_path.is_file():
            if json.loads(intent_path.read_text(encoding="utf-8"))["input_sha256"] != digest:
                raise ValueError("Automation resume inputs differ from the original step")
        else:
            from adaos.e2e.builder import _write_json
            _write_json(intent_path, {"input_sha256": digest, "conversation_id": conversation_id})
        existing = automation.get_state(object_type=kind, object_id=identifier, webspace_id=webspace)
        session = existing.get("session") or {}
        if session.get("conversation_id") == conversation_id:
            return {**existing, "duplicate": True}
        workflow.require_current_prototype_acceptance(kind, identifier)
        return automation.start(object_type=kind, object_id=identifier, implementation_brief=brief,
            webspace_id=webspace, conversation_id=conversation_id,
            execution_budget=inputs.get("execution_budget"), agent_profile=inputs.get("agent_profile"))
    if step_type == "automation.submit":
        instruction = str(inputs.get("text") or "").strip()
        expected_session = str(inputs.get("expected_session_id") or "")
        iteration = inputs.get("expected_iteration")
        if not instruction or not expected_session or type(iteration) is not int or iteration < 0:
            raise ValueError("Automation correction requires text and the exact expected session iteration")
        current = automation.get_state(object_type=kind, object_id=identifier, webspace_id=webspace)
        session = current.get("session") or {}
        if (session.get("session_id") != expected_session or session.get("iteration", 0) != iteration
                or session.get("status") not in {"failed", "completed", "awaiting_input"}):
            raise ValueError("Automation correction requires its exact terminal session iteration")
        intent = _evidence_path(f"evidence/lifecycle/{context['case_id']}-{context['repetition']}-{context['step_id']}.correction.json", context)
        intent.parent.mkdir(parents=True, exist_ok=True)
        # A lost acknowledgement is ambiguous. Never automatically resubmit it.
        with intent.open("x", encoding="utf-8") as stream:
            json.dump({"input": dict(inputs), "session_id": expected_session,
                       "iteration": iteration, "review_interventions": 1}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        result = automation.submit(instruction, object_type=kind, object_id=identifier,
            webspace_id=webspace, conversation_id=session.get("conversation_id"),
            expected_session_id=expected_session, expected_iteration=iteration)
        if result.get("status") == "automation_busy":
            return {**result, "ok": False, "review_interventions": 1}
        return {**result, "review_interventions": 1}
    if step_type == "automation.recover":
        current = automation.get_state(object_type=kind, object_id=identifier, webspace_id=webspace)
        session = current.get("session") or {}
        if (not inputs.get("expected_task_id") or session.get("current_task_id") != inputs["expected_task_id"]
                or not inputs.get("session_id") or session.get("session_id") != inputs["session_id"]
                or (current.get("automation") or {}).get("delivery", {}).get("aprobation_required") is not False):
            raise ValueError("Recovery requires the exact task without automatic Trial delivery")
        if (current.get("automation") or {}).get("failure_stage") == "forge_checkpoint":
            return automation.reconcile_checkpoint(object_type=kind, object_id=identifier)
        return automation.recover_validated_result(object_type=kind, object_id=identifier)
    if step_type == "automation.wait":
        timeout = max(1.0, float(inputs.get("timeout_seconds") or context.get("timeout_seconds") or 1800))
        started, polls = time.monotonic(), 0
        while True:
            result = automation.get_state(object_type=kind, object_id=identifier, webspace_id=webspace)
            session = result.get("session") or {}
            status = str(session.get("status") or "missing")
            expected_session = str(inputs.get("session_id") or "")
            if not expected_session or session.get("session_id", session.get("id")) != expected_session:
                raise ValueError("Automation wait must observe its exact started session")
            if inputs.get("expected_task_id") and session.get("current_task_id") != inputs["expected_task_id"]:
                raise ValueError("Automation observation must not follow a replacement task")
            polls += 1
            if status in {"completed", "failed", "cancelled", "expired", "awaiting_input"}:
                return {**result, "ok": status == "completed", "status": status, "poll_count": polls,
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
            if time.monotonic() - started >= timeout:
                return {**result, "ok": False, "status": "timeout", "worker_cancelled": False, "poll_count": polls}
            time.sleep(min(2.0, max(0.1, timeout - (time.monotonic() - started))))
    if step_type == "trial.prepare":
        return lifecycle.prepare_trial(kind, identifier, actor=actor, idempotency_key=key,
            source_webspace_id=webspace, target_webspace_id=webspace,
            publication_project_ref=str(inputs.get("project_ref") or "") or None)
    if step_type in {"trial.decide", "release.promote"}:
        state = workflow.get_state(kind, identifier)
        delivery = state.get("delivery") or {}
        candidate = str(delivery.get("candidate_id") or "")
        digest = str(delivery.get("package_digest") or "")
        if not candidate or not digest or inputs.get("candidate_id") != candidate:
            raise ValueError("release step must target the exact current candidate")
        if step_type == "trial.decide":
            review, ref = _review(inputs, context, expected={"stage": "trial", "candidate_id": candidate, "package_digest": digest})
            if review.get("decision") not in {"accept", "reject"}:
                raise ValueError("Trial review requires accept or reject")
            if review["decision"] == "accept" and any(check.get("status") != "passed"
                    for check in [*review.get("behavior_checks", []), *review.get("visual_checks", [])]):
                raise ValueError("Trial acceptance requires passed review checks")
            return {**lifecycle.decide_trial(kind, identifier, accepted=review["decision"] == "accept", actor=actor, idempotency_key=key),
                    "evidence_ref": ref, "review_interventions": 1}
        if inputs.get("confirmed") is not True:
            raise ValueError("stable promotion requires an explicit confirmed=true case input")
        return lifecycle.publish_candidate(kind, identifier, actor=actor, idempotency_key=key)
    raise ValueError(f"unsupported lifecycle step: {step_type}")


def retained_stage(steps: list[Mapping[str, Any]]) -> dict[str, str]:
    result = {"stage": "prototype", "acceptance": "not_approved"}
    for step in steps:
        if step.get("status") != "passed":
            continue
        kind = step.get("type")
        if kind == "prototype.accept":
            result["acceptance"] = "accepted"
        elif kind in {"automation.start", "automation.submit"}:
            result["stage"] = "automation"
        elif kind == "trial.prepare":
            result["stage"] = "trial"
        elif kind == "release.promote":
            result["stage"] = "stable"
    return result
