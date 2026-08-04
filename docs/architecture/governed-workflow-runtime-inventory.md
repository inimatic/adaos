# Governed Workflow Runtime Inventory

Status: source inventory for GWR0-06.

Inventory date: 2026-08-04.

Owners: Governed Data-Driven Workflow Model Roadmap, Builder Roadmap,
Conversational Interface, Artifact Source Package Activation Roadmap, and
Runtime/Supervisor owners for their local state files.

## Scope

This document inventories current durable and ad-hoc workflow-like state,
pending-response surfaces, retry loops, background task registries, and local
state files that can be confused with business workflow truth. It is not a
schema registry. Its job is to name the owner and migration disposition for
each surface so data-driven workflow implementation does not create a second
source of truth.

## Disposition Vocabulary

- `canonical workflow`: governed workflow truth or the accepted path toward it.
- `separate canonical model`: authoritative for a different domain and linked by
  refs only.
- `compatibility`: retained while callers migrate; must not gain new workflow
  authority.
- `projection/evidence`: derived, review, audit, or rollback data; never drives
  business transition admission.
- `open reliability gap`: known missing durability or acceptance boundary.

## Canonical Workflow Surfaces

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Pure workflow compiler/resolver | `src/adaos/services/governed_workflow.py`, `src/adaos/abi/workflow.*` | Governed workflow runtime | Validates definitions, commands, refs, events, decisions, migrations, composition, static/trace/metrics evidence records | `canonical workflow`; no I/O or provider state |
| Reference workflow persistence | `src/adaos/services/workflow_persistence.py` tables `governed_workflow_instances`, `governed_workflow_journal`, `governed_workflow_inbox`, `governed_workflow_outbox`, `governed_workflow_activity_attempts` | Governed workflow runtime | Node-local SQLite snapshot, journal, idempotent inbox, workflow outbox, and activity attempt boundary | `canonical workflow` for the bounded reference path |
| Workflow authoring/admission records | `src/adaos/services/workflow_authoring.py`, `workflow_admission.py`, `workflow_registry.py`, `workflow_artifacts.py` | Governed workflow runtime + artifact pipeline | LLM/human authoring context, attempts, adapter registry, manifest-bound definition artifact, admission evidence | `projection/evidence`; cannot alter definition digest |
| Workflow static, trace, and metrics reports | `workflow_static_reports.py`, `workflow_trace_identity.py`, `workflow_metrics.py` | Governed workflow runtime | Statechart/review/story coverage, cross-surface trace identity, complexity/context/cycle-time evidence | `projection/evidence`; report against definition digest |

## Builder And Package State

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Builder project prompt state | `<skill-or-scenario>/prompt_state.json` via `BuilderWorkflowService._state_path` | Builder Roadmap / GWR4 | Current compatibility state for Builder Change, Prototype, Automation, Trial, Publication, interaction context, and local migration from legacy fields | `compatibility`; keep until GWR4-23 restart/rollback/in-flight migration proof removes Python transition authority |
| Manifest-bound workflow source | `<skill-or-scenario>/workflow.json` referenced from `skill.yaml` or `scenario.yaml` | Artifact owner + governed workflow runtime | Canonical source for package-owned process definitions | `canonical workflow` once admitted; package publication must reject code/definition/policy mismatch |
| Builder context packet | `BuilderWorkflowService.build_context_packet` | Builder Roadmap / Conversational Interface | Bounded executor context containing workflow authoring context, static review, graph diff, conversational package validation, conversation snippets, pending-action refs, and coverage | `projection/evidence`; executor input only |
| Automation snapshots | `state/builder/workflow_snapshots/<kind>/<project>/automation` | Builder Roadmap | Artifact rollback/evidence snapshot for Automation handoff | `projection/evidence`; not workflow state |
| Prototype revisions | `<artifact>/ui_revisions/*.json`, `<artifact>/ui_revisions/current.txt` | Builder Product Experience | Immutable UI artifact lineage and current prototype pointer | `separate canonical model`; view/artifact lineage only |
| Conversational package source | `<artifact>/conversational/*.yaml` | Conversational Interface | Intent/entity/example/affordance/repair/output/story sources for controlling a workflow or skill | `separate canonical model`; validated by `compile_conversational_package`, never a workflow definition |
| Skill/scenario validation hooks | `src/adaos/services/skill/validation.py`, `src/adaos/services/scenario/validation.py` | Skill/scenario validators | Admit declared conversational packages and manifest-bound workflow definitions during local validation | `projection/evidence`; prevents publication of invalid source |

## Conversation, Interaction, And Delivery

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Conversation ledger and threads | `conversation_store.py` tables `conversation_conversations`, `conversation_threads`, `conversation_messages`, `conversation_dialog_*`, `conversation_memory_items`, `conversation_turn_traces`, `conversation_audit_events` | Conversational Interface | Durable human conversation, memory, trace, audit, dialog channel, and privacy model | `separate canonical model`; linked to workflow by refs |
| Transport ingress claims | `conversation_store.py` table `conversation_transport_ingress` | Conversation transport / GWR6 | Idempotently claims inbound transport updates such as Telegram callbacks | `open reliability gap`; not enough for GWR6-16 target-zone durable hub acceptance |
| Conversation interactions | `conversation_store.py` tables `conversation_interactions`, `conversation_interaction_presentations`, `conversation_interaction_responses`, `conversation_intent_proposals` | Conversational Interface / GWR2 | Durable semantic human input wait, negotiated presentation, response, and intent proposal lifecycle | `separate canonical model`; canonical invocation must preserve command identity |
| Response outbox and delivery attempts | `durable_delivery.py` tables `conversation_reply_routes`, `conversation_response_outbox`, `conversation_delivery_attempts` | Conversational Interface / Durable Delivery | ReplyRoute, ResponseEnvelope, progress coalescing, terminal response, and transport delivery attempts | `separate canonical model`; delivery retry must never rerun workflow work |
| Segment summary jobs | `conversation_store.py` table `conversation_segment_summary_jobs` | Conversation context | Bounded retry queue for summarizing conversation segments | `projection/evidence`; derived memory/context only |
| Development changes and runs | `conversation_store.py` tables `conversation_development_changes`, `conversation_development_runs` | Conversational Interface / Builder | Candidate/change/run evidence for conversational development flows | `projection/evidence`; promotion must go through Builder Change |

## Pending Actions And UI Projections

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Core Pending Actions | `src/adaos/services/pending_actions.py`, `src/adaos/sdk/data/pending_actions.py`, Yjs `data` root | Pending Actions / Builder | User-visible approval/action cards with pending/postponed/responded/expired/cancelled states | `compatibility`; governed flows should project from Interaction/workflow commands |
| Builder process/lifecycle projections | `src/adaos/services/builder/project_aggregate.py`, `builder/workflow.py` | Builder Roadmap | Process summary, lifecycle tree, focus, preview target, current project answer | `projection/evidence`; view/context changes do not transition business state |
| Yjs store/update metadata | `src/adaos/services/yjs/store.py`, `owner_guard.py`, `update_origin.py`, `state/ystores` | Projection Subscription / Yjs runtime | Collaborative document storage, owner guards, replay pressure, backend-room retry indicators | `separate canonical model`; projection transport/storage, not workflow truth |
| SDK projection refresh tasks | `src/adaos/sdk/data/projections.py` `_pending_refresh` | Projection SDK | In-process de-duplication of projection refresh work | `projection/evidence`; process-local cache only |

## Runtime, Supervisor, And Activation

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Artifact release, trial, and activation | `src/adaos/services/artifact_pipeline/*` | Artifact Source Package Activation Roadmap | ProjectRelease, WorkspaceLock, trial evidence, activation history, rollback and trust records | `separate canonical model`; package lifecycle gates workflow source but is not the workflow journal |
| Core update state files | `state/core_update/plan.json`, `status.json`, `last_result.json` via `src/adaos/services/core_update.py` | Core update/runtime owner | Node/core update plan, status, and result with root-promotion sidecars | `separate canonical model`; runtime lifecycle only |
| Core slot pointers and backups | `state/core_slots/*` via `core_slots.py` and `core_update.py` | Supervisor/runtime owner | Active/previous slot pointers, root promotion backup metadata | `separate canonical model`; package/runtime activation evidence |
| Skill service supervisor state | `state/services/<skill>/issues.json`, `doctor_requests.json`, service venv markers | Skill service supervisor | Health, dependency doctor requests, service environment markers | `separate canonical model`; service health, not business workflow |
| Runtime memory profiling artifacts | `runtime_memory_profile.py` JSON artifacts under configured artifact dirs | Runtime diagnostics | Profiling sessions, incidents, memory artifacts, retry-chain metadata | `projection/evidence`; diagnostic state only |
| Generic SQLite durable_state | `src/adaos/adapters/db/sqlite.py` table `durable_state` | Owning service per namespace | Namespace/key JSON persistence used by multiple features | `separate canonical model`; each namespace must declare owner before becoming workflow evidence |

## Backend And Transport Retry Loops

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Root LLM proxy/job registry | `src/adaos/integrations/adaos-backend/backend/app.ts` root LLM job types and retry loops | Backend LLM gateway / Builder Automation | Queued/running/succeeded/failed jobs and upstream retry attempts | `projection/evidence`; Builder work must be represented by a Run/activity, not raw provider job state |
| Telegram webhook/root relay | `backend/io/telegram/*`, backend NATS relay, `conversation_transport_ingress` | Telegram transport / GWR6 | Normalizes Telegram callbacks, relays root requests, registers webhook owner, and records inbound claims | `open reliability gap`; GWR6-16 still requires per-hub durable inbox and target-zone acceptance receipt |
| NATS sidecar routing | backend route proxy, `src/adaos/services/nats_config.py`, realtime sidecar docs | Realtime/transport owners | At-most-once routing, reconnect cleanup, hub-root stream recovery | `separate canonical model`; transport success is not workflow commit or delivery receipt |
| Git sparse/rebase retry handling | `src/adaos/adapters/git/cli_git.py` | Git adapter / artifact pipeline | Bounded local Git retry/conflict diagnostics for source publication | `projection/evidence`; source-control operation evidence only |

## Migration Rules

1. A mutating domain process must be represented by `workflow.json` plus the
   governed compiler/resolver before it is admitted as a data-driven workflow.
2. Existing state files may remain while they are explicitly labelled
   `compatibility`, `projection/evidence`, or a separate canonical model.
3. Pending human input belongs to `ConversationInteraction`; pending transport
   delivery belongs to ReplyRoute/ResponseEnvelope/DeliveryAttempt; neither may
   silently execute a workflow command.
4. Retry loops may retry transport, provider requests before an accepted effect
   boundary, or derived projections. Retrying a started external workflow
   effect requires explicit reconciliation, not automatic replay.
5. Publication and activation evidence may gate workflow source admission, but
   they do not redefine state names, commands, guards, or outcomes.
6. Any new durable table, state file, background queue, or retry loop that can
   affect workflow behavior must be added to this inventory with an owner and
   disposition before it ships.
