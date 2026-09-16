"""Stable SDK operations for the Builder Automation loop."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any


def _service():
    from adaos.services.builder.automation import BuilderAutomationService

    execution_mode = str(os.getenv("ADAOS_DEV_TOOL_EXECUTION_MODE") or "").strip().lower()
    return BuilderAutomationService.from_context(background=execution_mode != "oneshot")


def standard_prompt_version() -> str:
    """Return the exact standard realization prompt contract in this core build."""

    from adaos.services.builder.automation import STANDARD_PROMPT_VERSION

    return str(STANDARD_PROMPT_VERSION)


def _foreground_result(
    service: Any,
    result: Mapping[str, Any],
    *,
    object_type: str | None,
    object_id: str | None,
    webspace_id: str,
) -> dict[str, Any]:
    """Replace a queued acknowledgement with the durable final one-shot projection."""

    merged = dict(result or {})
    if bool(getattr(service, "background", True)):
        return merged
    final = service.projection(
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )
    if not isinstance(final, Mapping):
        return merged
    session = final.get("session") if isinstance(final.get("session"), Mapping) else None
    projection = final.get("automation") if isinstance(final.get("automation"), Mapping) else None
    if session is not None:
        merged["session"] = dict(session)
    if projection is not None:
        merged["automation"] = dict(projection)
        if projection.get("waiting_for_input"):
            merged.update(ok=True, status="automation_awaiting_input")
            return merged
    final_status = str((session or {}).get("status") or "").strip()
    if final_status:
        merged["status"] = f"automation_{final_status}"
    if final_status in {"completed", "failed", "cancelled", "expired"}:
        merged["ok"] = final_status == "completed"
    return merged


def start(
    *,
    object_type: str,
    object_id: str,
    implementation_brief: str,
    webspace_id: str = "desktop",
    conversation_id: str | None = None,
    brief_path: str | None = None,
    change_set_id: str | None = None,
    prototype_handoff: Mapping[str, Any] | None = None,
    development_session_id: str | None = None,
    links: Mapping[str, Any] | None = None,
    execution_budget: Mapping[str, Any] | None = None,
    agent_profile: Mapping[str, Any] | None = None,
    mcp: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Start or resume implementation from an approved brief."""

    if agent_profile is None:
        from adaos.sdk.developer import prompt_context

        agent_profile = prompt_context.get(object_type, object_id).get("builder_codex_profile")
    if agent_profile and agent_profile.get("model"):
        from adaos.sdk.builder.model_settings import require_local_profile

        require_local_profile(agent_profile)
    service = _service()
    result = service.start_from_execute(
        object_type=object_type,
        object_id=object_id,
        implementation_brief=implementation_brief,
        webspace_id=webspace_id,
        conversation_id=conversation_id,
        brief_path=brief_path,
        change_set_id=change_set_id,
        prototype_handoff=prototype_handoff,
        development_session_id=development_session_id,
        links=links,
        execution_budget=execution_budget,
        agent_profile=agent_profile,
        mcp=mcp,
    ) or {}
    return _foreground_result(
        service,
        result,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )


def submit(
    text: str,
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    webspace_id: str = "desktop",
    conversation_id: str | None = None,
    development_session_id: str | None = None,
    expected_session_id: str | None = None,
    expected_iteration: int | None = None,
    agent_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit one follow-up instruction and include the current projection."""

    if agent_profile and agent_profile.get("model"):
        from adaos.sdk.builder.model_settings import require_local_profile

        require_local_profile(agent_profile)
    service = _service()
    result = dict(
        service.submit_turn(
            text=text,
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
            conversation_id=conversation_id,
            development_session_id=development_session_id,
            expected_session_id=expected_session_id,
            expected_iteration=expected_iteration,
            agent_profile=agent_profile,
        )
        or {}
    )
    if not isinstance(result.get("automation"), Mapping):
        state = service.projection(
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
        )
        if isinstance(state, Mapping):
            result["automation"] = dict(state.get("automation") or {})
    return _foreground_result(
        service,
        result,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )


def get_clarification(*, object_type: str, object_id: str) -> dict[str, Any]:
    """Read the current model question batch, exact bindings and retained answers."""
    return _service().clarification_state(object_type=object_type, object_id=object_id)


def answer_clarification(*, object_type: str, object_id: str, interaction_id: str,
                         expected_generation: int, answers: Mapping[str, str],
                         idempotency_key: str) -> dict[str, Any]:
    """Save partial owner answers; this never starts a model execution."""
    return _service().answer_clarification(object_type=object_type, object_id=object_id,
        interaction_id=interaction_id, expected_generation=expected_generation,
        answers=dict(answers), idempotency_key=idempotency_key)


def resume_clarification(*, object_type: str, object_id: str, interaction_id: str,
                         expected_generation: int, confirmed: bool) -> dict[str, Any]:
    """Explicitly continue the same Change after every required question is answered."""
    service = _service()
    result = service.resume_clarification(object_type=object_type, object_id=object_id,
        interaction_id=interaction_id, expected_generation=expected_generation, confirmed=confirmed)
    return _foreground_result(service, result, object_type=object_type, object_id=object_id,
                              webspace_id=str((result.get("session") or {}).get("webspace_id") or "desktop"))


def retry_failed(
    *,
    object_type: str,
    object_id: str,
    webspace_id: str = "desktop",
    conversation_id: str | None = None,
    execution_budget: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Retry a failed run without accepting a replacement user instruction."""

    service = _service()
    result = service.retry_failed(
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
        conversation_id=conversation_id,
        execution_budget=execution_budget,
    ) or {}
    return _foreground_result(
        service,
        result,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )


def return_to_prototype(
    *,
    object_type: str,
    object_id: str,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Ask the built-in Automation LLM to derive a safe, disconnected prototype."""

    instruction = (
        "Workflow transition: derive a new safe Prototype revision from the current Automation result. "
        "Preserve the user-facing information architecture, layout, copy, and interaction intent, but remove "
        "all real service, credential, device, external-network, and production-data bindings. Replace them with "
        "typed contracts plus local mock or internal declarative data suitable for rapid prototyping. Do not "
        "publish or activate a release. Validate the resulting scenario and webui declarations and add or update "
        "tests that prove the prototype has no functional production bindings."
    )
    service = _service()
    result = service.submit_turn(
        text=instruction,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
        workflow_transition="return_to_prototype",
    ) or {}
    return _foreground_result(
        service,
        result,
        object_type=object_type,
        object_id=object_id,
        webspace_id=webspace_id,
    )


def get_state(
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    webspace_id: str = "desktop",
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Return the compact render-safe automation projection."""

    result = dict(
        _service().projection(
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
            conversation_id=conversation_id,
        )
        or {}
    )
    if result.get("error") == "automation_session_not_found" and isinstance(
        result.get("automation"), Mapping
    ):
        result["ok"] = True
        result["session_present"] = False
        result.pop("error", None)
    elif result.get("ok"):
        result.setdefault("session_present", True)
    return result


def trial_verification_evidence(
    *,
    object_type: str,
    object_id: str,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Project trusted Automation evidence for Application Trial admission.

    The projection contains only evidence sealed by the Automation service. It
    deliberately excludes browser and live-runtime observations, which belong
    to the later publication gate after the Trial is active.
    """

    service = _service()
    state = dict(
        service.projection(
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
        )
        or {}
    )
    automation = (
        state.get("automation")
        if isinstance(state.get("automation"), Mapping)
        else {}
    )
    task_id = str(automation.get("task_id") or "").strip()
    if (
        automation.get("status") != "completed"
        or not automation.get("terminal")
        or not task_id
        or Path(task_id).name != task_id
    ):
        return {
            "ok": False,
            "status": "blocked",
            "reason": "completed_automation_required",
        }

    run_root = (Path(service.runs_root) / task_id).resolve()
    runs_root = Path(service.runs_root).resolve()
    if not run_root.is_relative_to(runs_root):
        raise ValueError("Automation task path escapes the configured run root")
    result_path = run_root / "output" / "result.json"
    test_report_path = run_root / "output" / "test_report.json"
    if not result_path.is_file() or not test_report_path.is_file():
        return {
            "ok": False,
            "status": "blocked",
            "reason": "automation_evidence_missing",
            "task_id": task_id,
        }

    result = json.loads(result_path.read_text(encoding="utf-8"))
    report = json.loads(test_report_path.read_text(encoding="utf-8"))
    if (
        result.get("status") != "completed"
        or not isinstance(result.get("tests"), Mapping)
        or result["tests"].get("status") != "passed"
        or report.get("status") != "passed"
        or report.get("errors")
    ):
        return {
            "ok": False,
            "status": "blocked",
            "reason": "automation_tests_not_passed",
            "task_id": task_id,
        }

    manifest = result.get("evidence") if isinstance(result.get("evidence"), Mapping) else {}
    artifacts = [
        dict(item)
        for item in manifest.get("artifacts") or ()
        if isinstance(item, Mapping)
    ]
    test_artifact = next(
        (item for item in artifacts if item.get("kind") == "test_report"),
        None,
    )
    provenance_artifact = next(
        (item for item in artifacts if item.get("kind") == "provenance"),
        None,
    )
    if not test_artifact or not provenance_artifact:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "sealed_automation_artifacts_missing",
            "task_id": task_id,
        }

    passed_checks = [
        dict(item)
        for item in report.get("checks") or ()
        if isinstance(item, Mapping) and item.get("ok") is True
    ]
    access_checks = [
        item
        for item in passed_checks
        if str(item.get("path") or "").endswith("test_application_contract.py")
    ]
    if not access_checks:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "access_matrix_test_missing",
            "task_id": task_id,
        }

    def artifact_ref(item: Mapping[str, Any], prefix: str) -> str:
        return (
            f"{prefix}:{item.get('logical_path')}"
            f"#{str(item.get('digest') or '').removeprefix('sha256:')}"
        )

    source_commit = str(result.get("commit_hash") or "").strip()
    if not source_commit:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "automation_source_commit_missing",
            "task_id": task_id,
        }
    return {
        "ok": True,
        "status": "ready",
        "task_id": task_id,
        "source_commit": source_commit,
        "release_scope": "trial",
        "regression_evidence": [artifact_ref(test_artifact, "suite:checkpoint")],
        "access_matrix_evidence": list(
            dict.fromkeys(
                "suite:access-matrix:" + str(item.get("path") or "")
                for item in access_checks
            )
        ),
        "audit_evidence": [artifact_ref(provenance_artifact, "provenance")],
        "evidence_manifest_schema": manifest.get("schema"),
    }


def reconcile_checkpoint(*, object_type: str, object_id: str) -> dict[str, Any]:
    """Explicitly recover a failed post-Codex Forge checkpoint without rerunning Codex."""

    return dict(
        _service().reconcile_checkpoint(
            object_type=object_type,
            object_id=object_id,
        )
        or {}
    )


def repackage_checkpoint(
    *,
    object_type: str,
    object_id: str,
    publication_project_ref: str,
    actor: str,
    idempotency_key: str,
    reason: str = "",
) -> dict[str, Any]:
    """Advance only the owning Project version for validated unchanged source."""

    return dict(
        _service().repackage_checkpoint(
            object_type=object_type,
            object_id=object_id,
            publication_project_ref=publication_project_ref,
            actor=actor,
            idempotency_key=idempotency_key,
            reason=reason,
        )
        or {}
    )


def recover_validated_result(*, object_type: str, object_id: str) -> dict[str, Any]:
    """Activate a preserved validated result without assigning Codex again."""

    return dict(
        _service().recover_validated_result(
            object_type=object_type,
            object_id=object_id,
        )
        or {}
    )


def release_candidate_runtime(
    *,
    object_type: str,
    object_id: str,
    development_session_id: str,
) -> dict[str, Any]:
    """Release a terminal Builder candidate runtime and retain its evidence."""

    return dict(
        _service().release_candidate_runtime(
            object_type=object_type,
            object_id=object_id,
            development_session_id=development_session_id,
        )
        or {}
    )


__all__ = [
    "get_clarification",
    "answer_clarification",
    "resume_clarification",
    "get_state",
    "release_candidate_runtime",
    "reconcile_checkpoint",
    "repackage_checkpoint",
    "recover_validated_result",
    "return_to_prototype",
    "retry_failed",
    "start",
    "standard_prompt_version",
    "submit",
    "trial_verification_evidence",
]
