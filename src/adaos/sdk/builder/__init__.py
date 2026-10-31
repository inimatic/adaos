"""Public Builder capability facades.

Skills import these modules instead of constructing ``adaos.services.builder``
objects. Service imports stay lazy so validation can inspect skill handlers
without bootstrapping the full runtime.
"""

from __future__ import annotations

from . import (
    artifacts,
    applications,
    automation,
    conversation,
    development_sessions,
    intent,
    issues,
    lifecycle,
    model_settings,
    observability,
    preview,
    project_catalog,
    prototype,
    releases,
    review,
    semantic_ui,
    source_recovery,
    sources,
    workflow,
)

from adaos.sdk.core.authoring import bind_authoring_operations as _bind_authoring

for _module in (artifacts, applications, automation, development_sessions, issues,
                lifecycle, model_settings, preview, project_catalog, releases,
                review, source_recovery, sources, workflow):
    _bind_authoring(_module)
_bind_authoring(semantic_ui, (
    "apply", "prepare_label_request", "inspect", "record_turn_timing", "admit",
    "prepare_proposal", "propose", "prepare_request", "proposal_observation"))
_bind_authoring(prototype, (
    "prepare_generation_journal", "generation_journal_dir",
    "history", "retain_revision", "read_revision", "recover_evidenced_revision",
    "prepare_generation_publication", "commit_generation_publication",
    "finish_generation_failure", "generation_publication_projection",
    "add_reference", "reference_context", "reconcile_semantic_revision"))
_bind_authoring(observability, (
    "retain_llm_evidence", "read_llm_evidence", "configure_usd_budget", "usd_budget"))
# submit_request/candidate_status, conversations and application data runtimes
# deliberately keep the invoking Trial context and its exact execution package.

__all__ = [
    "artifacts",
    "applications",
    "automation",
    "conversation",
    "development_sessions",
    "intent",
    "issues",
    "lifecycle",
    "model_settings",
    "observability",
    "preview",
    "project_catalog",
    "prototype",
    "releases",
    "review",
    "semantic_ui",
    "source_recovery",
    "sources",
    "workflow",
]
