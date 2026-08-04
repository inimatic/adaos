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
| Workflow authoring/admission records | `src/adaos/services/workflow_authoring.py`, `workflow_admission.py`, `workflow_registry.py`, `workflow_artifacts.py` | Governed workflow runtime + artifact pipeline | LLM/human authoring context, attempts, adapter registry, manifest-bound definition artifact, activation admission evidence | `projection/evidence`; cannot alter definition digest |
| Publication admission | `WorkspaceActivationManager.admit_release_candidate`, `adaos.workflow.publication_admission.v1` | Governed workflow runtime + artifact pipeline | One pre-channel gate over immutable package/code, manifest, definition, validation, adapter binding, role policy, desired WorkspaceLock, and migration set | `projection/evidence`; publication and activation consume the same admitted candidate |
| Workflow ingress conformance | `workflow_execution.cross_channel_ingress_conformance`, `adaos.workflow.ingress_conformance.v1` | Governed workflow runtime | Compares Web, Telegram, numbered text, and SDK invocation semantics, guards, generation, target refs, executor readiness, and execution result | `projection/evidence`; deterministic local proof, not a transport durability receipt |
| Workflow static, trace, and metrics reports | `workflow_static_reports.py`, `workflow_trace_identity.py`, `workflow_metrics.py` | Governed workflow runtime | Statechart/review/story coverage, full turn-to-delivery trace identity, complexity/context/cycle-time and clarification/repair/retry/action-failure evidence | `projection/evidence`; compact metrics evidence is stored on Builder Runs and Trials |

## Builder And Package State

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Builder project prompt state | `<skill-or-scenario>/prompt_state.json` via `BuilderWorkflowService._state_path` | Builder Roadmap / GWR4 | Compatibility persistence for Builder Change projection, governed instance pin, Runs, Trial/Publication evidence, interaction context, and legacy-field migration | `compatibility`; with `ADAOS_BUILDER_REQUIRE_ACTIVE_PACKAGE=true` transition admission has no DEV/Python-definition fallback, but global default rollout and final projection-store migration remain open |
| Manifest-bound workflow source | `<skill-or-scenario>/workflow.json` referenced from `skill.yaml` or `scenario.yaml` | Artifact owner + governed workflow runtime | Canonical source for package-owned process definitions | `canonical workflow` once admitted; package publication rejects code, definition, validation, adapter-binding, or role-policy mismatch through one admission gate |
| Builder workflow migration checkpoints | `state/builder/workflow_migrations/*.json` | Builder Roadmap / GWR4 | Exact before/after instance, definition/package/binding pins, idempotent restart completion, and exact rollback witness | `projection/evidence`; migration mutates only the governed instance in `prompt_state.json` |
| Builder context packet | `BuilderWorkflowService.build_context_packet` | Builder Roadmap / Conversational Interface | Bounded executor context containing workflow authoring context, static review, graph diff, conversational package validation, conversation snippets, pending-action refs, and coverage | `projection/evidence`; executor input only |
| Builder Automation sessions | `src/adaos/services/builder/automation.py` session files under Builder state | Builder Roadmap / Skill Factory | Queued/assigned/running/test/commit state for one exact Automation task and Change/Run correlation | `separate canonical model`; executor lifecycle linked to workflow activity/Run, never transition authority |
| Builder Preview reconciliation | `state/builder/workbench/runtime/*.json`, process-local `_TASKS` in `builder/preview_reconciler.py` | Builder Product Experience | Desired/observed Preview materialization generation and best-effort async reconcile task | durable record is `projection/evidence`; process-local task is recoverable from desired state and is not business workflow truth |
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
| Conversation story execution | `conversational_artifacts.run_conversation_story` and validation report timelines | Conversational Interface | Deterministic design-time execution with repair, interaction, fallback, stale/concurrent, retry, executor-unavailable, and negative assertions | `projection/evidence`; no durable provider/model/effect calls and no runtime transition authority |
| NLU Teacher runtime overlays | `state/interpreter/nlu_teacher_overlays.json`, `nlu.teacher_overlay_store.v1`, `nlu.teacher_promotion_candidate.v1` | NLU Teacher / Conversational Interface | Scoped runtime examples plus Builder promotion candidates for git-versioned conversational package source | `separate canonical model`; runtime benefit is allowed, public/source promotion requires Builder review |

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
| Artifact candidates and Trials | `artifact_pipeline/candidates.py` candidate/trial files | Artifact Source Package Activation Roadmap | Candidate state, immutable trial identity, data mode, health/rollback receipts, and workflow metrics evidence | `separate canonical model`; Trial admits a package and supplies evidence but does not transition a workflow instance |
| Publication promotion journal | `artifact_pipeline/publication.py` promotion-operation files | Artifact Source Package Activation Roadmap | Admission, channel CAS, activation, registry projection, subscription observation, and restart continuation receipts | `separate canonical model`; no unknown channel mutation is replayed |
| Workspace activation journal | `artifact_pipeline/activation.py` activation operations, delayed verification, and lock history | Artifact Source Package Activation Roadmap | ProjectRelease/WorkspaceLock CAS, staged materialization, migration, reload, health, commit, rollback, and delayed verification | `separate canonical model`; package/runtime generation gate, not business workflow journal |
| Attestation publication and remote recovery | `artifact_pipeline/attestation_publication.py`, `reconciliation.py`, `recovery.py` | Artifact Source Package Activation Roadmap | Immutable trust publication, no-replay reconciliation, explicit continuation, and exact remote recovery evidence | `separate canonical model`; release transport/recovery only |
| Generic operation manager | `state/operations/operations.json`, Yjs runtime projection, process-local task handles in `operations/manager.py` | Runtime operations owner | Install/update operation status, cancellation/retry request, restart classification, and UI notifications | `separate canonical model`; controls runtime/package operations, not domain workflow state |
| Skill Factory task queue | `state/skill_factory/state.json`, per-task worker runtime/evidence directories | Skill Factory | Realization request, assignment, heartbeat, task result, isolated Codex run, source checkpoint, and cleanup status | `separate canonical model`; Builder Run/activity references exact task and result |
| Core update state files | `state/core_update/plan.json`, `status.json`, `last_result.json` via `src/adaos/services/core_update.py` | Core update/runtime owner | Node/core update plan, status, and result with root-promotion sidecars | `separate canonical model`; runtime lifecycle only |
| Core slot pointers and backups | `state/core_slots/*` via `core_slots.py` and `core_update.py` | Supervisor/runtime owner | Active/previous slot pointers, root promotion backup metadata | `separate canonical model`; package/runtime activation evidence |
| Skill service supervisor state | `state/services/<skill>/issues.json`, `doctor_requests.json`, service venv markers | Skill service supervisor | Health, dependency doctor requests, service environment markers | `separate canonical model`; service health, not business workflow |
| Runtime memory profiling artifacts | `runtime_memory_profile.py` JSON artifacts under configured artifact dirs | Runtime diagnostics | Profiling sessions, incidents, memory artifacts, retry-chain metadata | `projection/evidence`; diagnostic state only |
| Root MCP session leases | `state/root_mcp/mcp_sessions.json` via `root_mcp/sessions.py` | Root MCP | Expiring capability-bearing session leases and revocation status | `separate canonical model`; authorization input, never workflow state or evidence by itself |
| Autonomous release validation | `state/release_validation/autonomous/*` | Release validation/runtime owner | Exact installed-build smoke attempt and terminal validation evidence after update | `projection/evidence`; does not activate or roll back by itself |
| Generic SQLite durable_state | `src/adaos/adapters/db/sqlite.py` table `durable_state` | Owning service per namespace | Namespace/key JSON persistence used by multiple features | `separate canonical model`; each namespace must declare owner before becoming workflow evidence |
| In-process scheduler and task registries | `scheduler.py`, service-local `asyncio.create_task` registries | Owning service | Timers and duplicate suppression for recoverable derived/runtime work | `open reliability gap` if ever used as authoritative wait; current uses must recover from durable desired state or remain best-effort |

## Backend And Transport Retry Loops

| Surface | Location | Owner | Current role | Disposition |
| --- | --- | --- | --- | --- |
| Root LLM proxy/job registry | `src/adaos/integrations/adaos-backend/backend/app.ts` root LLM job types and retry loops | Backend LLM gateway / Builder Automation | Queued/running/succeeded/failed jobs and upstream retry attempts | `projection/evidence`; Builder work must be represented by a Run/activity, not raw provider job state |
| Telegram webhook/root relay | `backend/io/telegram/*`, backend NATS relay, `conversation_transport_ingress` | Telegram transport / GWR6 | Normalizes Telegram callbacks, relays root requests, registers webhook owner, and records inbound claims | `open reliability gap`; GWR6-16 still requires per-hub durable inbox and target-zone acceptance receipt |
| NATS sidecar routing | backend route proxy, `src/adaos/services/nats_config.py`, realtime sidecar docs | Realtime/transport owners | At-most-once routing, reconnect cleanup, hub-root stream recovery | `separate canonical model`; transport success is not workflow commit or delivery receipt |
| Hub-root protocol pending stream | `state/hub_root_protocol/streams.json` via `hub_root_protocol_store.py` | Hub/root protocol | Per-stream cursor, one pending payload, attempts, acknowledgement, and TTL for root/hub control traffic | `separate canonical model`; does not satisfy Telegram target-hub ingress acceptance and cannot become workflow truth |
| Git sparse/rebase retry handling | `src/adaos/adapters/git/cli_git.py` | Git adapter / artifact pipeline | Bounded local Git retry/conflict diagnostics for source publication | `projection/evidence`; source-control operation evidence only |

## Audit Closure And Provider Consequence

The 2026-08-04 audit searched service and backend sources for durable schemas,
JSON state paths, SQLite tables, queued/running/pending status records, retry
loops, `asyncio.create_task` registries, activation/publication journals, and
transport acknowledgements. The tables above group implementation-specific
records under their owning canonical model; caches, locks, temporary files,
materialized package trees, and pure read projections are intentionally not
listed as independent workflows.

No remaining node-local business process requires an external durable workflow
provider. The canonical workflow journal plus conversation/delivery stores
cover the bounded Builder path; package activation, runtime operations, Skill
Factory, core update, and authorization leases are separate lifecycle models
that would not become simpler by being reclassified as Builder workflow state.
The only current must-level distributed durability gap is GWR6-16 target-zone
Telegram ingress acceptance. That gap requires a per-hub durable inbox and
receipt protocol, not replacement of the workflow resolver or journal. The
external-provider decision therefore remains postponed until one of the
measurable ADR admission criteria is observed.

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
