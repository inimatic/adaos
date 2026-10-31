"""Stable SDK façade for executable Builder prototypes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

_COMPATIBILITY_PORT: Any = None
_WORKSPACE_PORT: Any = None
_PERSONAL_PORT: Any = None


def history(object_type: str, object_id: str, *, limit: int = 50,
            cursor: str | None = None) -> dict[str, Any]:
    """Read paginated revision metadata; missing snapshots remain explicit."""
    from adaos.services.builder.prototype_history import PrototypeHistoryService
    from adaos.sdk.builder.workflow import _service
    return PrototypeHistoryService(_service()).list(object_type, object_id, limit=limit, cursor=cursor)


def prepare_generation_journal(*, project_ref: str) -> dict[str, Any]:
    """Freeze a Core-owned journal location; do not import legacy DEV journals."""
    from adaos.sdk import access
    from adaos.sdk.builder.workflow import _service
    from adaos.services.builder.generation_journals import GenerationJournals

    access.require('applications.develop')
    return GenerationJournals(_service()).prepare(project_ref)


def generation_journal_dir(journal_ref: str):
    """Resolve an existing frozen journal without rebinding current selection."""
    from adaos.sdk import access
    from adaos.sdk.builder.workflow import _service
    from adaos.services.builder.generation_journals import GenerationJournals

    access.require('applications.develop')
    return GenerationJournals(_service()).resolve(journal_ref)


def retain_revision(object_type: str, object_id: str, revision: str) -> dict[str, Any]:
    """Explicitly retain an existing revision outside disposable DEV sources."""
    from adaos.services.builder.prototype_history import PrototypeHistoryService
    from adaos.sdk.builder.workflow import _service
    return PrototypeHistoryService(_service()).retain(object_type, object_id, revision)


def read_revision(object_type: str, object_id: str, revision: str) -> dict[str, Any]:
    """Read a retained or legacy snapshot without switching current revision."""
    from adaos.services.builder.prototype_history import PrototypeHistoryService
    from adaos.sdk.builder.workflow import _service
    return PrototypeHistoryService(_service()).read(object_type, object_id, revision)


def prepare_generation_publication(*, journal_dir, request_id: str, output_ref: str) -> dict[str, Any]:
    """Prepare an opted-in v1 result without changing accepted UI/source files."""
    from adaos.sdk import access
    from adaos.sdk.builder.workflow import _service
    from adaos.services.builder.generation_authoring import prepare_generation_publication as prepare

    access.require('applications.develop')
    return prepare(_service(), journal_dir=journal_dir, request_id=request_id, output_ref=output_ref)


def commit_generation_publication(*, journal_dir, request_id: str, output_ref: str,
                                  publication_ref: str) -> dict[str, Any]:
    """Commit one prepared v1 publication with its original Run, not a legacy revision."""
    from adaos.sdk import access
    from adaos.sdk.builder.workflow import _service
    from adaos.services.builder.generation_authoring import commit_generation_publication as commit

    access.require('applications.develop')
    return commit(_service(), journal_dir=journal_dir, request_id=request_id,
        output_ref=output_ref, publication_ref=publication_ref)


def finish_generation_failure(*, journal_dir, request_id: str, outcome: str,
                              cause_ref: str | None = None) -> dict[str, Any]:
    """Close only a retained failed/cancelled result, without changing the UI."""
    from adaos.sdk import access
    from adaos.sdk.builder.workflow import _service
    from adaos.services.builder.generation_authoring import (
        finish_generation_failure as finish,
    )

    access.require('applications.develop')
    return finish(_service(), journal_dir=journal_dir, request_id=request_id,
        outcome=outcome, cause_ref=cause_ref)


def recover_evidenced_revision(
    object_type: str,
    object_id: str,
    source_snapshot: Mapping[str, Any],
    acceptance_id: str,
) -> dict[str, Any]:
    """Maintenance recovery from a verified snapshot and durable acceptance."""
    from adaos.services.builder.prototype_history import PrototypeHistoryService
    from adaos.sdk.builder.workflow import _service
    return PrototypeHistoryService(_service()).recover_evidenced_acceptance(
        object_type, object_id, source_snapshot, acceptance_id)


def _compatibility_execution_port():
    global _COMPATIBILITY_PORT
    from adaos.adapters.builder.legacy_dev_skill import (
        LegacyDevSkillPrototypeExecution,
    )

    if _COMPATIBILITY_PORT is None:
        _COMPATIBILITY_PORT = LegacyDevSkillPrototypeExecution()
    return _COMPATIBILITY_PORT


def personal_provider_jobs(metadata: Mapping[str, Any]):
    """Foreground-only Core AI transport for a frozen personal selection.

    Does not enable Automation, hosted tools, paid fallback, or provider retries.
    Job observation can outlive a foreground tool invocation.
    """
    from adaos.services.builder.personal_prototype import PersonalBuilderJobs
    return PersonalBuilderJobs(metadata)


def provider_jobs(metadata: Mapping[str, Any], *, journal_dir=None):
    """Select the pinned native transport and bind its admitted generation plan."""
    from adaos.services.builder.provider_jobs import provider_jobs as select
    return select(metadata, journal_dir=journal_dir,
        run_observer_factory=_generation_run_observer)


def _generation_workflow():
    """Freeze authoring paths only; provider transport keeps invoking Trial."""
    from adaos.sdk import access
    from adaos.sdk.core.authoring import development_context
    from adaos.sdk.builder.workflow import _service

    with development_context('builder.prototype.generation_observation'):
        access.require('applications.develop')
        return _service()


def _generation_run_observer():
    from adaos.services.builder.generation_runs import GenerationRuns

    return GenerationRuns(_generation_workflow())


def model_reasoning(model: str | None, effort: str = 'low'):
    """Validate explicit reasoning effort against the reviewed model family."""
    from adaos.services.builder.model_controls import reasoning_for_model
    return reasoning_for_model(model, effort)


def model_supports_sampling(model: str | None) -> bool:
    from adaos.services.builder.model_controls import supports_sampling
    return supports_sampling(model)


def submit_request(
    statement: str,
    *,
    webspace_id: str,
    locale: str = "en",
    conversation_context: Mapping[str, Any] | None = None,
    interface_context: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    source_kind: Literal["chat", "api", "e2e", "unknown"] = "api",
    auto_apply: bool = True,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Submit one Prototype turn through the public SDK contract.

    The current execution port is named in the receipt and remains a
    compatibility backend until generation ownership moves fully into Core.
    """

    from adaos.services.builder.prototype_requests import submit_request as submit

    global _WORKSPACE_PORT, _PERSONAL_PORT
    # Scope this cutover to the admitted semantic proposer. General generation
    # retains its explicitly named compatibility backend until separately qualified.
    mode = (metadata or {}).get("builder_semantic_edit_mode")
    if (metadata or {}).get('builder_ai_resource_ref') and mode not in {None, 'full_candidate', 'bounded_ops', 'full_document'}:
        raise ValueError('Personal ChatGPT supports foreground Prototype only; no Root or Automation fallback')
    selected = (metadata or {}).get('builder_selected_target') or {}
    from adaos.sdk.builder.semantic_ui import recognizes_request
    managed_edit = (isinstance(selected, Mapping) and selected.get('object_type') in {'scenario', 'project'}
                    and recognizes_request(statement))
    if (metadata or {}).get('builder_ai_resource_ref'):
        from adaos.adapters.builder.legacy_dev_skill import PersonalChatGPTPrototypeExecution
        if _PERSONAL_PORT is None:
            _PERSONAL_PORT = PersonalChatGPTPrototypeExecution()
        port = _PERSONAL_PORT
    elif mode in {"bounded_ops", "full_document"} or managed_edit:
        from adaos.adapters.builder.legacy_dev_skill import WorkspaceSkillPrototypeExecution
        if _WORKSPACE_PORT is None:
            _WORKSPACE_PORT = WorkspaceSkillPrototypeExecution()
        port = _WORKSPACE_PORT
    else:
        port = _compatibility_execution_port()
    return submit(
        port,
        statement,
        webspace_id=webspace_id,
        locale=locale,
        conversation_context=conversation_context,
        interface_context=interface_context,
        metadata=metadata,
        source_kind=source_kind,
        auto_apply=auto_apply,
        timeout_seconds=timeout_seconds,
    )


def candidate_status(
    session_id: str,
    *,
    webspace_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Read one Prototype candidate through the public SDK contract."""

    from adaos.services.builder.prototype_requests import candidate_status as status

    from adaos.services.agent_context import get_ctx
    # Trial sessions belong to the sealed runtime that admitted the request.
    # Polling must not resolve a same-named mutable DEV skill instead.
    if getattr(get_ctx(), "authority_context", None) is not None:
        global _WORKSPACE_PORT
        from adaos.adapters.builder.legacy_dev_skill import WorkspaceSkillPrototypeExecution
        if _WORKSPACE_PORT is None:
            _WORKSPACE_PORT = WorkspaceSkillPrototypeExecution()
        port = _WORKSPACE_PORT
    else:
        port = _compatibility_execution_port()
    return status(
        port,
        session_id,
        webspace_id=webspace_id,
        timeout_seconds=timeout_seconds,
    )


def add_reference(*, project_ref: str, name: str, media_type: str, text: str) -> dict[str, Any]:
    """Retain an explicit text/HTML design reference; never execute or fetch it."""
    from adaos.sdk import access
    access.require('applications.develop')
    access.require('workspace.write')
    from adaos.services.builder.reference_artifacts import BuilderReferenceArtifacts
    return BuilderReferenceArtifacts().add(project_ref=project_ref, name=name, media_type=media_type, text=text)


def reference_context(*, project_ref: str, receipts: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Verify exact project ownership and content before adding model input."""
    from adaos.sdk import access
    access.require('applications.develop')
    access.require('workspace.read')
    from adaos.services.builder.reference_artifacts import BuilderReferenceArtifacts
    return BuilderReferenceArtifacts().resolve(project_ref=project_ref, receipts=receipts)


def model_context(brief: Mapping[str, Any], *, compact: bool = False) -> dict[str, Any]:
    """Return the bounded generation-stage view of a full Prototype Brief."""

    from adaos.services.builder.prototype_context import (
        compile_prototype_model_context,
    )

    return compile_prototype_model_context(brief, compact=compact)


def prepare_generation_request(request: Mapping[str, Any], **options) -> dict[str, Any]:
    """Admit Core-owned interpretation before a native candidate request."""
    from adaos.services.builder.task_pipeline import prepare_generation_request as prepare
    if 'run_observer' not in options or 'generation_run_observer' not in options:
        workflow = _generation_workflow()
    if 'run_observer' not in options:
        from adaos.services.builder.interpretation_runs import InterpretationRuns
        options['run_observer'] = InterpretationRuns(workflow)
    if 'generation_run_observer' not in options:
        from adaos.services.builder.generation_runs import GenerationRuns
        options['generation_run_observer'] = GenerationRuns(workflow)
    return prepare(request, **options)


def admit_task_source(instruction: str, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Capture and validate the one source shared by discovery and generation."""
    from adaos.services.builder.task_pipeline import admit_task_source as admit
    return admit(instruction, metadata=metadata)


def task_admission_failure(error: Exception, *, locale: str = 'en') -> dict[str, Any] | None:
    """Project typed interpretation refusal without an unhandled chat failure."""
    from adaos.services.builder.task_interpretation import TaskInterpretationRejected
    if not isinstance(error, TaskInterpretationRejected):
        return None
    from adaos.services.builder.task_pipeline import TaskInterpretationProviderFailed
    from adaos.services.builder.interpretation_runs import InterpretationRunRejected
    if isinstance(error, InterpretationRunRejected):
        from adaos.services.builder.task_diagnostics import execution_evidence_failure_text
        return {'ok':False,'status':'blocked','error':'prototype_execution_evidence_unavailable',
            'detail':execution_evidence_failure_text(locale=locale),'findings':error.findings,
            'next_action':'restore_execution_evidence','candidate_created':False,'paid_candidate_generation':False}
    if isinstance(error, TaskInterpretationProviderFailed):
        from adaos.services.builder.task_diagnostics import interpretation_provider_failure_text
        kind = (error.provider_rejection or {}).get('kind')
        actions = {'schema':'repair_provider_contract', 'model':'select_available_model',
            'authentication':'reconnect_account', 'account_scope':'reconnect_account',
            'quota':'check_provider_allowance', 'tools':'repair_tool_contract',
            'reasoning':'check_reasoning_support', 'access':'check_provider_access',
            'provider_unavailable':'check_provider_availability'}
        return {'ok':False, 'status':'cancelled' if error.provider_status=='cancelled' else 'blocked', 'error':'prototype_task_provider_failed',
            'detail':interpretation_provider_failure_text(error.provider_rejection, locale=locale,status=error.provider_status),
            'findings':error.findings, 'next_action':'submit_new_turn_if_needed' if error.provider_status=='cancelled' else actions.get(kind, 'inspect_provider_attempt'),
            'interpretation_job_id':error.job_id, 'provider_status':error.provider_status,
            'provider_rejection':error.provider_rejection,
            'candidate_created':False, 'paid_candidate_generation':False}
    from adaos.services.builder.task_diagnostics import interpretation_refusal_text,interpretation_refusal_kind
    summary = interpretation_refusal_text({'error': 'prototype_task_interpretation_rejected', 'findings': error.findings}, locale=locale)
    action={'source':'select_matching_scope','context':'repair_context_contract','capability':'resolve_capability_gap',
            'source_semantics':'resolve_source_semantics',
            'clarification':'answer_material_questions','interpretation':'correct_interpretation',
            'unknown':'inspect_admission'}[interpretation_refusal_kind(error.findings)]
    return {'ok': False, 'status': 'blocked', 'error': 'prototype_task_interpretation_rejected',
            'detail': summary, 'findings': error.findings,
            'next_action': action,
            'candidate_created': False, 'paid_candidate_generation': False}


def prepare_repair_context(**options) -> dict[str, Any] | None:
    """Recover the original wire admission and issue a bounded repair plan."""
    from adaos.services.builder.repair_context import prepare_repair_context as prepare
    if 'generation_run_observer' not in options:
        options['generation_run_observer'] = _generation_run_observer()
    return prepare(**options)


def replay_base_ref(*, project_ref, artifact_root, revision=None) -> str:
    """Identify a real revision or the exact unaccepted initial source base."""
    from adaos.services.builder.prototype_recovery import replay_base_ref as identify
    return identify(project_ref=project_ref,artifact_root=artifact_root,revision=revision)


def prepare_initial_replay(*, project_ref, artifact_root, journal_dir, job_id,
                           request_text, expected_base_ref) -> dict[str, Any] | None:
    """Recover only retained Core meaning and acknowledged original input."""
    from adaos.services.builder.prototype_recovery import prepare_initial_replay as prepare
    return prepare(project_ref=project_ref,artifact_root=artifact_root,journal_dir=journal_dir,
        job_id=job_id,request_text=request_text,expected_base_ref=expected_base_ref)


def semantic_revision_context(
    document: Mapping[str, Any],
    brief: Mapping[str, Any],
    *,
    source_ref: str,
    revision: Any,
) -> dict[str, Any]:
    """Return a digest-addressed compact view of an existing semantic revision."""

    from adaos.services.builder.prototype_context import (
        compile_semantic_revision_model_context,
    )

    return compile_semantic_revision_model_context(
        document, brief, source_ref=source_ref, revision=revision
    )


def output_locales(instruction: str, *, locale: str, existing: list[str] = (),
                   brief: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    from adaos.services.builder.prototype_context import prototype_output_locales
    return prototype_output_locales(instruction, locale=locale, existing=existing, brief=brief)


def merge_briefs(*briefs: Mapping[str, Any]) -> dict[str, Any]:
    """Merge accepted turn Briefs into a cumulative project contract."""

    from adaos.services.builder_intent import merge_prototype_briefs

    return merge_prototype_briefs(*briefs)


def compile_semantic(
    document: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
    runtime_scope_ref: str | None = None,
) -> dict[str, Any]:
    """Compile a semantic Prototype document into canonical runtime artifacts."""

    from adaos.services.builder.semantic_prototype import compile_semantic_prototype

    return compile_semantic_prototype(
        document,
        brief=brief,
        project_ref=project_ref,
        **({"runtime_scope_ref": runtime_scope_ref} if runtime_scope_ref is not None else {}),
    )


def compile_semantic_candidate(
    candidate: Mapping[str, Any],
    *,
    brief: Mapping[str, Any],
    project_ref: str | None = None,
    runtime_scope_ref: str | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a strict model candidate into canonical runtime artifacts."""

    from adaos.services.builder.semantic_prototype import (
        compile_semantic_prototype_candidate,
    )

    compiled = compile_semantic_prototype_candidate(
        candidate,
        brief=brief,
        project_ref=project_ref,
        **({"runtime_scope_ref": runtime_scope_ref} if runtime_scope_ref is not None else {}),
        **({'previous':previous} if previous is not None else {}),
    )
    if previous is not None:
        from adaos.services.builder.task_preservation import fixture_revision_findings
        from adaos.services.builder.semantic_revision import SemanticRevisionError
        findings, scope = fixture_revision_findings(previous, compiled['semantic_document'], brief)
        if findings:
            raise SemanticRevisionError(findings)
        if scope:
            compiled['preservation_receipt'] = {'schema': 'adaos.builder.fixture_preservation_receipt.v1',
                'status': 'passed', 'write_scope': scope,
                'candidate_digest': compiled['semantic_digest'], 'model_certified': False}
        reconciled = reconcile_semantic_revision(
            previous, compiled["semantic_document"], brief=brief
        )
        if reconciled["normalizations"]:
            preserved = compile_semantic(
                reconciled["semantic_document"],
                brief=brief,
                project_ref=project_ref,
                **(
                    {"runtime_scope_ref": runtime_scope_ref}
                    if runtime_scope_ref is not None
                    else {}
                ),
            )
            compiled = {
                **compiled,
                **preserved,
                "semantic_document": reconciled["semantic_document"],
                "normalizations": [
                    *compiled.get("normalizations", []),
                    *reconciled["normalizations"],
                ],
            }
    return compiled


def verify_semantic_source(document: Mapping[str, Any], webui: Mapping[str, Any], *, project_ref: str) -> dict[str, Any]:
    """Verify effective UI before constructing a semantic follow-up context."""
    from adaos.services.builder.semantic_graph import verify_baseline
    result = verify_baseline(document, webui, project_ref=project_ref)
    return {"semantic_digest": result["semantic_digest"], "primary_locale": result["primary_locale"]}


def reconcile_semantic_revision(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    brief: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply deterministic preservation invariants to an incremental revision."""

    from adaos.services.builder.semantic_revision import (
        reconcile_semantic_revision as reconcile,
    )

    return reconcile(previous, current, brief=brief)


def expand_requirement_aliases(
    candidate: Mapping[str, Any], *, brief: Mapping[str, Any]
) -> dict[str, Any]:
    """Expand compact model coverage to the full canonical requirement set."""

    from adaos.services.builder.prototype_context import (
        expand_prototype_requirement_aliases,
    )

    return expand_prototype_requirement_aliases(candidate, brief)


def prepare_binding_repair(candidate: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    from adaos.services.builder.semantic_repair import prepare_binding_repair as prepare
    return prepare(candidate, findings)


def apply_binding_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    from adaos.services.builder.semantic_repair import apply_binding_repair as apply
    return apply(candidate, repair, findings)


def prepare_record_repair(candidate: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    from adaos.services.builder.semantic_repair import prepare_record_repair as prepare
    return prepare(candidate, findings)


def apply_record_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    from adaos.services.builder.semantic_repair import apply_record_repair as apply
    return apply(candidate, repair, findings)


def prepare_collection_repair(candidate: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    from adaos.services.builder.semantic_repair import prepare_collection_repair as prepare
    return prepare(candidate, findings)


def apply_collection_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    from adaos.services.builder.semantic_repair import apply_collection_repair as apply
    return apply(candidate, repair, findings)


def prepare_state_repair(candidate: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select a bounded repair contract without changing candidate data."""
    from adaos.services.builder.semantic_repair import prepare_state_repair as prepare
    return prepare(candidate, findings)


def apply_state_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge an in-scope repair; the complete candidate still requires compilation."""
    from adaos.services.builder.semantic_repair import apply_state_repair as apply
    return apply(candidate, repair, findings)


def normalize_semantic_candidate(
    candidate: Mapping[str, Any], *, brief: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate and lower a strict model candidate to the semantic ABI."""

    from adaos.services.builder.semantic_prototype import (
        normalize_semantic_prototype_candidate,
    )

    return normalize_semantic_prototype_candidate(candidate, brief=brief)


def semantic_contract(*, version: str = "v1") -> dict[str, Any]:
    """Return the schema used for semantic Prototype generation."""

    from adaos.services.builder.semantic_prototype import semantic_prototype_contract

    return semantic_prototype_contract(version=version)


def semantic_candidate_contract(*, version: str = "v1") -> dict[str, Any]:
    """Return the strict schema supplied to the Prototype design model."""

    from adaos.services.builder.semantic_prototype import (
        semantic_prototype_candidate_contract,
    )

    return semantic_prototype_candidate_contract(version=version)


def semantic_provider_contract(*, version: str = "v1", locales: tuple[str, ...] = ("en", "ru"), brief: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the OpenAI strict-subset projection of the model contract."""

    from adaos.services.builder.semantic_prototype import (
        semantic_prototype_provider_contract,
    )

    return semantic_prototype_provider_contract(version=version, locales=locales, brief=brief)


def semantic_generation_guidance() -> dict[str, Any]:
    """Return versioned generic invariants from the executable Core contracts."""
    from adaos.services.builder.semantic_prototype import semantic_prototype_generation_guidance
    return semantic_prototype_generation_guidance()


def mcp_generation_options() -> dict[str, Any]:
    """Return opt-in, least-privilege native Root MCP generation controls."""
    from adaos.services.builder.prototype_mcp import generation_options
    return generation_options()


def mcp_retrieval_index() -> dict[str, Any] | None:
    """Expose the public, digest-pinned guide index only when tools are enabled."""
    from adaos.services.builder.prototype_mcp import enabled, retrieval_index
    return retrieval_index() if enabled() else None


def redact_generation_credentials(value: Any) -> Any:
    """Keep private MCP authentication out of model journals and public receipts."""
    from adaos.services.builder.prototype_mcp import redact_credentials
    return redact_credentials(value)


def compile_generation_messages(messages, *, strategy: str | None = None):
    """Deliver an opt-in exact platform kernel and digest-bound repair context."""
    import os
    selected = strategy or os.getenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", "full")
    if selected in {"adaptive", "core-prefetch"}:
        from adaos.services.builder.prototype_context_delivery import compile_prototype_messages
        projected, receipt = compile_prototype_messages(messages, strategy="adaptive")
        if selected == "core-prefetch":
            from adaos.services.builder.prototype_prefetch import prefetch_guidance
            return prefetch_guidance(projected, receipt=receipt)
        return projected, receipt
    from adaos.services.builder.prototype_pipeline import compile_messages
    return compile_messages(messages, strategy=strategy)


def start_data_runtime(definition: Mapping[str, Any]):
    from adaos.services.builder.prototype_runtime import PrototypeDataRuntime

    return PrototypeDataRuntime.start(definition)


def composition_slice(
    webui: Mapping[str, Any],
    target_ref: str,
    *,
    source_revision: str,
    acceptance: list[Mapping[str, Any]] | None = None,
    evidence_budget: int = 5,
    renderer_snapshots: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    from adaos.services.builder.composition import extract_composition_slice

    return extract_composition_slice(
        webui,
        target_ref,
        source_revision=source_revision,
        acceptance=acceptance,
        evidence_budget=evidence_budget,
        renderer_snapshots=renderer_snapshots,
    )


def check_spatial_constraint(
    slice_value: Mapping[str, Any], constraint: Mapping[str, Any]
) -> dict[str, Any]:
    from adaos.services.builder.composition import evaluate_spatial_constraint

    return evaluate_spatial_constraint(slice_value, constraint)


def validate_workflow_slice(
    value: Mapping[str, Any], *, source_definition: Mapping[str, Any]
) -> dict[str, Any]:
    from adaos.services.builder.conversational_prototype import (
        validate_conversational_workflow_slice,
    )

    return validate_conversational_workflow_slice(
        value, source_definition=source_definition
    )


def automation_handoff(**kwargs: Any) -> dict[str, Any]:
    from adaos.services.builder.prototype_handoff import build_automation_handoff

    return build_automation_handoff(**kwargs)


def prepare_reference_repair(candidate: Mapping[str, Any], findings: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    from adaos.services.builder.semantic_repair import prepare_reference_repair as prepare
    return prepare(candidate, findings)


def apply_reference_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: list[Mapping[str, Any]]) -> dict[str, Any]:
    from adaos.services.builder.semantic_repair import apply_reference_repair as apply
    return apply(candidate, repair, findings)


__all__ = [
    "prepare_generation_journal",
    "generation_journal_dir",
    "history",
    "retain_revision",
    "read_revision",
    "recover_evidenced_revision",
    "prepare_generation_publication",
    "commit_generation_publication",
    "finish_generation_failure",
    "personal_provider_jobs",
    "model_reasoning",
    "model_supports_sampling",
    "prepare_collection_repair",
    "apply_collection_repair",
    "prepare_record_repair",
    "apply_record_repair",
    "prepare_reference_repair",
    "apply_reference_repair",
    "prepare_binding_repair",
    "apply_binding_repair",
    "prepare_state_repair",
    "apply_state_repair",
    "candidate_status",
    "check_spatial_constraint",
    "compile_semantic",
    "compile_semantic_candidate",
    "expand_requirement_aliases",
    "composition_slice",
    "automation_handoff",
    "model_context",
    "provider_jobs",
    "prepare_generation_request",
    "admit_task_source",
    "task_admission_failure",
    "add_reference",
    "reference_context",
    "semantic_revision_context",
    "verify_semantic_source",
    "output_locales",
    "prepare_repair_context",
    "replay_base_ref",
    "prepare_initial_replay",
    "reconcile_semantic_revision",
    "merge_briefs",
    "normalize_semantic_candidate",
    "semantic_candidate_contract",
    "semantic_contract",
    "semantic_generation_guidance",
    "mcp_generation_options",
    "mcp_retrieval_index",
    "redact_generation_credentials",
    "compile_generation_messages",
    "semantic_provider_contract",
    "start_data_runtime",
    "submit_request",
    "validate_workflow_slice",
]
