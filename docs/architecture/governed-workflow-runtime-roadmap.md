# Governed Workflow Runtime Roadmap

Status: domain roadmap for the AdaOS workflow, interaction, NLU mediation, and
durable-execution plane.

Last reviewed: 2026-07-30.

This roadmap implements the
[Governed Workflow and Interaction Runtime](governed-workflow-runtime.md).
It owns sequencing and acceptance inside this domain. It does not replace the
[Governed Evolution Roadmap](governed-evolution-roadmap.md), Builder roadmap,
conversation architecture, NLU roadmap, or operational event roadmap. Tasks
that belong to those domains are linked rather than redefined.

## Priority and Status Rules

- `[must]`: required for the current workflow-runtime proof gate;
- `[should]`: required before broad, repeated, or unattended use;
- `[could]`: useful experiment that must not delay the current gate;
- `[deferred]`: deliberately postponed until the stated admission condition.

Checkboxes report only the exact statement beside them:

- `[x]` means the stated artifact or evidence exists;
- `[ ]` means it is not complete or has not met its evidence gate.

Priority is independent from maturity. Use:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

No task advances beyond `implemented` without a reproducible test or evidence
record. A successful happy-path UI demonstration does not prove durability.

## Planning Authority

1. The architecture document owns invariants, record responsibilities, and
   provider boundaries.
2. This roadmap owns implementation order and the workflow-runtime proof.
3. Domain workflow definitions remain owned by their domain architecture and
   roadmap.
4. [Operational Event Model](operational-event-model.md) owns event envelopes
   and projections; this roadmap owns workflow command/event semantics.
5. [Conversation and Channel Architecture](conversation-and-channel-architecture.md)
   owns durable threads and message storage; this roadmap owns interactions,
   action invocation, and workflow reply binding.
6. [Pending Actions](pending-actions.md) is a compatibility input and may be
   migrated; it is not a second permanent interaction authority.
7. [Builder Roadmap](builder-roadmap.md) owns Builder product acceptance; this
   roadmap owns the reusable runtime proven by that acceptance.
8. External engine adoption is a decision produced by evidence, not a
   prerequisite assumed by this document.

## Current Baseline

The current repository has useful but fragmented foundations:

| Capability | Baseline | Assessment |
| --- | --- | --- |
| Local events | `LocalEventBus` and `adaos.operational-event-envelope.v1` | implemented compatibility foundation; not durable orchestration |
| Persistent node data | SQLite stores, conversation ledger, idempotent ingress records | implemented fragments; no shared workflow journal |
| Human decisions | Pending Actions and several domain-specific confirmations | implemented compatibility path; no canonical interaction registry |
| Web actions | structured chat actions and page action runtime | partial projection; transport and authorization contracts differ |
| Telegram actions | callback normalization and backend keyboard support | partial plumbing; canonical outbound/inbound bridge incomplete |
| Builder workflow | persisted phase/change state, Runs, revisions, trial/publication evidence | domain-specific and partially integrated; recovery remains fragmented |
| Long-running tasks | local asyncio tasks, worker records, polling, domain retries | several implementations; no common pause/resume authority |
| NATS | Core NATS transport and sidecar routing | at-most-once transport path; not a workflow journal |
| External durable engine | none | hypothesis/evaluation only |

## Milestone Sequence

| Milestone | Outcome | Current maturity | Horizon |
| --- | --- | --- | --- |
| GWR0 | Target boundary, risks, and authority are fixed | `specified` | now |
| GWR1 | Canonical contracts and a pure workflow kernel exist | `hypothesis` | now |
| GWR2 | One semantic interaction works across Web, Telegram, and text fallback | `hypothesis` with compatibility fragments | now |
| GWR3 | Node-local workflow execution survives restart without duplicate mutation | `hypothesis` | next |
| GWR4 | Free text is constrained by pending interaction and allowed transitions | `hypothesis` | next |
| GWR5 | Builder completes the reference workflow through the shared runtime | `hypothesis` | next |
| GWR6 | External provider candidates are measured and one adoption decision is recorded | `hypothesis` | later |
| GWR7 | The chosen runtime is hardened for broad single-user use | `hypothesis` | later |
| GWR8 | Root/multi-user and cross-provider extensions are admitted by evidence | `deferred` | long-term |

Milestones are cumulative. A provider experiment may run early behind a
feature flag, but it does not redefine the contracts or become a dependency
before GWR5 produces the reference proof.

## GWR0. Architecture and Decision Baseline

**Outcome:** AdaOS has one documented boundary for workflow semantics,
interactions, NLU mediation, durable execution, and provider evaluation.

**Exit gate:** architecture and roadmap are discoverable, distinguish current
implementation from target state, and do not claim an external provider is
already selected.

- [x] `[must]` `GWR0-01` Define the workflow kernel, durable provider,
  interaction, IntentProposal, activity, evidence, and ReplyRoute boundaries.
- [x] `[must]` `GWR0-02` Separate workflow statechart, artifact lineage DAG,
  execution state, and view/command context.
- [x] `[must]` `GWR0-03` Record local-first, no-silent-retry,
  commit-time-authorization, and no-dual-truth invariants.
- [x] `[must]` `GWR0-04` Record the reference SQLite, DBOS, Temporal, Restate,
  and NATS/JetStream positions without selecting a vendor by assertion.
- [x] `[must]` `GWR0-05` Register this roadmap in the architecture navigation
  and roadmap authority map.
- [ ] `[should]` `GWR0-06` Inventory every current durable/ad-hoc workflow,
  pending response, retry loop, background task registry, and state file with
  an owner and migration disposition.
- [ ] `[could]` `GWR0-07` Produce diagrams generated from the future canonical
  workflow definitions for architecture review.
- [x] `[deferred]` `GWR0-08` Do not harmonize all historical documentation in
  this milestone; update owning documents only as implementation reaches them.

## GWR1. Canonical Contracts and Pure Kernel

**Outcome:** workflow state and allowed transitions can be described and
validated without a UI, NLU model, worker, or external provider.

**Admission gate:** GWR0 is complete.

**Exit proof:** unit and property tests compile a representative workflow,
reject invalid definitions, exercise every legal/illegal transition, and
produce stable fresh interaction descriptors from the resulting state.

- [ ] `[must]` `GWR1-01` Add versioned schemas and typed models for
  `WorkflowDefinition`, `WorkflowInstance`, `WorkflowCommand`, and
  `WorkflowEvent`.
- [ ] `[must]` `GWR1-02` Add typed refs for principal, aggregate, task,
  artifact, evidence, interaction, command context, and reply route.
- [ ] `[must]` `GWR1-03` Implement a pure transition resolver that consumes a
  definition, snapshot, and command and returns an accepted transition or a
  typed rejection without side effects.
- [ ] `[must]` `GWR1-04` Implement definition validation for reachability,
  terminals, registered guards/activities, competing transitions, risk,
  retries, failure outcomes, and waiting-state explanation.
- [ ] `[must]` `GWR1-05` Require monotonic generation and
  `expected_generation` for state-changing commands.
- [ ] `[must]` `GWR1-06` Define the stable provider port and provider capability
  descriptor.
- [ ] `[must]` `GWR1-07` Define activity metadata for idempotency, side-effect
  class, timeout, heartbeat, cancellation, compensation, and unknown outcome.
- [ ] `[must]` `GWR1-08` Generate `allowed_actions` and rejection explanations
  from the same guards used to admit commands.
- [ ] `[should]` `GWR1-09` Add model-based tests proving state snapshots and
  projections can be rebuilt from canonical workflow events.
- [ ] `[should]` `GWR1-10` Add schema compatibility tests and a definition
  version/migration fixture.
- [ ] `[could]` `GWR1-11` Export a statechart representation for visualization
  and review without making the visualization format authoritative.
- [ ] `[deferred]` `GWR1-12` Defer arbitrary end-user workflow authoring and
  executable expressions; definitions reference only registered code.

## GWR2. Conversation Interaction and Reply Routing

**Outcome:** a skill or system workflow emits one semantic interaction that is
controllable through Web, Telegram, and a text fallback.

**Admission gate:** GWR1 contracts are stable enough to bind interaction
targets and generations.

**Exit proof:** one low-risk and one confirmation-required interaction traverse
each channel; stale, expired, replayed, cross-user, and cross-context attempts
are rejected; a delayed result reaches its original route after restart.

- [ ] `[must]` `GWR2-01` Add `adaos.conversation.interaction.v1` and SDK methods
  for messages with typed controls, forms, and channel fallbacks.
- [ ] `[must]` `GWR2-02` Add a durable interaction registry with opaque tokens,
  expiry, principal/context/generation binding, single-use, and idempotency.
- [ ] `[must]` `GWR2-03` Add one canonical interaction-invocation ingress that
  bypasses NLU for valid deterministic actions.
- [ ] `[must]` `GWR2-04` Adapt current Web chat actions to the canonical
  interaction without exposing raw skill/tool parameters to the browser.
- [ ] `[must]` `GWR2-05` Project outbound Telegram inline keyboards and route
  callback actions through the canonical ingress.
- [ ] `[must]` `GWR2-06` Add a numbered/text fallback and capability negotiation
  for channels without rich controls.
- [ ] `[must]` `GWR2-07` Persist full ReplyRoute for asynchronous work and
  distinguish materialized, queued, sent, failed, and acknowledged delivery.
- [ ] `[must]` `GWR2-08` Ensure a callback/reply cannot escape its principal,
  conversation, task, workflow, or target generation.
- [ ] `[should]` `GWR2-09` Unify Pending Actions with the interaction registry or
  implement an explicit compatibility adapter and migration owner.
- [ ] `[should]` `GWR2-10` Add pagination, compact summaries, and deep links for
  Telegram rather than forcing full Web parity.
- [ ] `[could]` `GWR2-11` Add mini-app handoff for rich artifact/search views
  after the semantic interaction contract is stable.
- [ ] `[deferred]` `GWR2-12` Defer universal Telegram parity for complex file,
  tree, search, and preview interfaces.

## GWR3. Reference Node-Local Durable Execution

**Outcome:** a local AdaOS process can wait, resume, and complete a workflow
after restart without losing or silently duplicating work.

**Admission gate:** GWR1 contracts exist; GWR2 provides durable input/reply
bindings for human waits.

**Exit proof:** fault injection at every transition/activity/outbox boundary
demonstrates recovery, deduplication, explicit unknown outcome, cancellation,
and replayable explanation on the development machine.

- [ ] `[must]` `GWR3-01` Implement the reference SQLite provider using one
  canonical store rather than per-skill JSON state.
- [ ] `[must]` `GWR3-02` Add append transition journal, snapshot generation,
  idempotent command inbox, and transactional outbox.
- [ ] `[must]` `GWR3-03` Add worker leases, heartbeats, stale lease recovery,
  bounded concurrency, and restart reconciliation.
- [ ] `[must]` `GWR3-04` Persist timers, waits, external signals, cancellation,
  and task/activity attempts.
- [ ] `[must]` `GWR3-05` Never retry an uncertain modifying activity unless its
  target proves idempotency or reconciliation proves it did not commit.
- [ ] `[must]` `GWR3-06` Add commit-time checks for permission, target digest,
  approval witness, current generation, and effect binding.
- [ ] `[must]` `GWR3-07` Emit canonical workflow events through the operational
  envelope without treating the runtime event bus as durable truth.
- [ ] `[must]` `GWR3-08` Add backup/restore and schema migration coverage for
  active and waiting instances.
- [ ] `[should]` `GWR3-09` Add an operator describe/list/recover/cancel surface
  with redacted payloads and reasoned blocked states.
- [ ] `[should]` `GWR3-10` Add bounded retention, compaction/snapshots, and large
  artifact references instead of unbounded payload history.
- [ ] `[could]` `GWR3-11` Use JetStream for an optional durable transport/outbox
  experiment after the SQLite truth and offline recovery are proven.
- [ ] `[deferred]` `GWR3-12` Defer distributed consensus and active-active local
  workflow execution; one provider is authoritative for one instance.

## GWR4. NLU Mediation and Informal Responses

**Outcome:** natural language can safely answer pending questions, request
transitions, add feedback, or start unrelated work without becoming direct
mutation authority.

**Admission gate:** allowed transitions and pending interactions are
queryable through GWR1-GWR3.

**Exit proof:** a multilingual evaluation set covers explicit and informal
answers, multi-act feedback, ambiguous targets, corrections, negation, stale
context, and unrelated requests; no state changes outside the deterministic
resolver.

- [ ] `[must]` `GWR4-01` Add `adaos.intent.proposal.v1` with source message,
  semantic acts, alternatives, allowed-command snapshot, model identity, and
  disposition.
- [ ] `[must]` `GWR4-02` Route free text against an explicit pending
  interaction first and require clarification when more than one target fits.
- [ ] `[must]` `GWR4-03` Support multi-act utterances instead of forcing every
  message into one intent.
- [ ] `[must]` `GWR4-04` Keep new Issue/change feedback, read-only questions,
  context selection, and workflow commands distinct.
- [ ] `[must]` `GWR4-05` Reject model-proposed commands not present in the
  current `allowed_actions` set or lacking required typed arguments.
- [ ] `[must]` `GWR4-06` Define risk policy for accepting bounded free-text
  confirmation versus requiring an explicit protected review action.
- [ ] `[must]` `GWR4-07` Persist interpretation, clarification, correction, and
  committed result with privacy/retention controls.
- [ ] `[should]` `GWR4-08` Build offline evaluation from real corrected cases,
  including Russian and English locale/encoding coverage.
- [ ] `[should]` `GWR4-09` Measure per-act precision, false transition rate,
  clarification rate, and correction recovery rather than relying on model
  confidence.
- [ ] `[could]` `GWR4-10` Add deterministic parsers for common short answers and
  ordinal choices before invoking a model.
- [ ] `[deferred]` `GWR4-11` Defer autonomous workflow induction from arbitrary
  conversations until curated definitions and policy prove insufficient.

## GWR5. Builder Reference Vertical Slice

**Outcome:** Builder uses the shared runtime for request to Issue/Change,
Prototype or Automation, Trial, and Publication across Web and Telegram.

**Admission gate:** GWR1-GWR4 exit proofs exist for the required commands and
channels.

**Exit proof:** one empty representative scenario completes the full flow on
this development machine, survives injected restarts, and records artifact,
Git, test, trial, publication, delivery, and rollback evidence without direct
state repair.

- [ ] `[must]` `GWR5-01` Define and validate the canonical Builder Change
  workflow using registered guards and activities.
- [ ] `[must]` `GWR5-02` Key the workflow instance by canonical `change_id` and
  retain exact project, base release, artifact, and command-context refs.
- [ ] `[must]` `GWR5-03` Project Lifecycle from workflow state and artifact
  lineage instead of maintaining three independent stage buckets.
- [ ] `[must]` `GWR5-04` Route selection and Preview as view context only;
  selection must not mutate the active workflow.
- [ ] `[must]` `GWR5-05` Implement LLM, Codex, validation, Git checkpoint,
  trial, publication, and notification as typed activities.
- [ ] `[must]` `GWR5-06` Persist Automation input-required/review and completion
  routes so Web or Telegram can resume the same task after restart.
- [ ] `[must]` `GWR5-07` Prove no automatic repeat of an uncertain Codex,
  filesystem, Git, activation, or publication mutation.
- [ ] `[must]` `GWR5-08` Bind every acceptance/publication action to the exact
  immutable candidate digest and current authority generation.
- [ ] `[must]` `GWR5-09` Exercise revise-to-Prototype, direct Automation,
  cancel, failure, retry-as-new-Run, Trial reject/accept, and Publication.
- [ ] `[must]` `GWR5-10` Record repeatable local acceptance evidence and update
  the Builder roadmap without duplicating this runtime checklist.
- [ ] `[should]` `GWR5-11` Migrate Builder-specific pending actions, workflow
  JSON, and polling to compatibility projections or retire them explicitly.
- [ ] `[should]` `GWR5-12` Compare latency, token/tool overhead, storage growth,
  and operator diagnosis time with the current Builder path.
- [ ] `[could]` `GWR5-13` Add a graph/timeline inspector as a rich Builder view
  after the chat-first control path is accepted.
- [ ] `[deferred]` `GWR5-14` Defer simultaneous multi-user editing and approval
  to GWR8.

## GWR6. External Durable Provider Evaluation

**Outcome:** AdaOS has measured evidence for adopting an external provider or
retaining the reference provider for the next product horizon.

**Admission gate:** the GWR5 workflow and failure suite are provider-neutral
and pass on the reference provider.

**Exit proof:** at least one candidate executes the same conformance and fault
suite behind a feature flag, and an ADR records adopt, postpone, or reject with
measured reasons.

### Evaluation Matrix

Every candidate is assessed against:

- native AdaOS Python 3.11 integration;
- Windows development and Linux x64/ARM deployment;
- offline single-node operation;
- SQLite/local and PostgreSQL/central topology;
- restart and network-partition recovery;
- signals, waits, queries, cancellation, and compensation;
- activity idempotency and unknown-outcome handling;
- workflow/worker version upgrades and pinned in-flight work;
- multi-tenant authorization and action-context binding;
- backup, restore, retention, privacy, and payload limits;
- observability and operator repair;
- CPU, memory, disk, startup, packaging, and supervisor cost;
- license, project maturity, release cadence, and exit/migration cost.

- [ ] `[must]` `GWR6-01` Freeze a provider conformance suite from the GWR3 and
  GWR5 acceptance cases.
- [ ] `[must]` `GWR6-02` Record baseline resource and failure measurements for
  the reference SQLite provider.
- [ ] `[should]` `GWR6-03` Implement a DBOS SQLite/PostgreSQL pilot behind the
  provider port and feature flag.
- [ ] `[should]` `GWR6-04` Test DBOS with async activities, duplicate signals,
  process restart, schema/worker upgrade, backup, Windows, and Linux.
- [ ] `[could]` `GWR6-05` Implement a bounded Temporal Root pilot for a
  cross-node Builder activity and long human wait.
- [ ] `[could]` `GWR6-06` Evaluate Restate as a supervisor-managed sidecar on
  supported Linux hubs and document the Windows development gap.
- [ ] `[could]` `GWR6-07` Evaluate Dapr only if a broader AdaOS sidecar/building-
  block decision makes its operational cost shared.
- [ ] `[must]` `GWR6-08` Write an ADR selecting adopt/postpone/reject and keep
  provider-native APIs below the AdaOS SDK boundary.
- [ ] `[deferred]` `GWR6-09` Do not implement several production providers in
  parallel; only the selected path advances to GWR7.

## GWR7. Single-User Hardening and Adoption

**Outcome:** the admitted runtime can carry routine single-user workflows
without manual database or state-file repair.

**Admission gate:** GWR6 ADR selects the provider and defines rollback to the
reference path or an explicit migration plan.

**Exit proof:** repeatable local and independent-stand tests cover upgrades,
backup/restore, degraded transport, delayed human input, worker replacement,
and bounded operational repair with accepted SLOs.

- [ ] `[must]` `GWR7-01` Package, supervise, update, health-check, and back up
  the selected provider through AdaOS lifecycle rails.
- [ ] `[must]` `GWR7-02` Add definition/worker version deployment and in-flight
  compatibility gates.
- [ ] `[must]` `GWR7-03` Add health and availability projections that separate
  workflow readiness from NATS, Yjs, browser, or Root transport readiness.
- [ ] `[must]` `GWR7-04` Add bounded operator recovery for stuck,
  outcome-unknown, poisoned, and migration-required instances.
- [ ] `[must]` `GWR7-05` Complete security review for tokens, tenancy,
  authorization, secrets, history privacy, and commit-time effect binding.
- [ ] `[should]` `GWR7-06` Add traces linking message, interpretation, command,
  event, activity, artifact, evidence, and delivery without exposing secrets.
- [ ] `[should]` `GWR7-07` Add retention, archival, compaction, and export for
  completed workflows.
- [ ] `[should]` `GWR7-08` Migrate a second AdaOS domain such as controlled core
  update or NLU confirmation to prove the runtime is not Builder-specific.
- [ ] `[could]` `GWR7-09` Add a reusable workflow inspector for operators and
  developers.
- [ ] `[deferred]` `GWR7-10` Defer unattended high-risk publication or device
  mutation until multi-user policy and field evidence justify it.

## GWR8. Root, Multi-User, and Federation Extensions

**Outcome:** only after the single-user plane is stable, workflow instances may
coordinate multiple principals, nodes, responsibility zones, and reusable
change proposals.

**Admission gate:** GWR7 is production-accepted for bounded single-user work,
and a concrete multi-user use case supplies authority, privacy, conflict, and
availability requirements.

- [ ] `[deferred]` `GWR8-01` Add role/zone-specific approvals, delegation,
  quorum, revocation, and audit.
- [ ] `[deferred]` `GWR8-02` Add conflict and supersession handling for
  concurrent Changes over one immutable base.
- [ ] `[deferred]` `GWR8-03` Add extraction and promotion of proven personal
  changes into portable proposals.
- [ ] `[deferred]` `GWR8-04` Add Root-level workflow provider for inherently
  cross-node or cross-user processes.
- [ ] `[deferred]` `GWR8-05` Define disconnected node participation, delayed
  signals, leases, and reconciliation without global mutable state.
- [ ] `[deferred]` `GWR8-06` Evaluate cross-provider migration only when a live
  portability requirement exists.
- [ ] `[deferred]` `GWR8-07` Keep CRDT collaboration limited to content where
  appropriate; approvals and durable transitions remain ordered commands.
- [ ] `[deferred]` `GWR8-08` Defer marketplace automation and cross-group trust
  scoring until provenance and governance have field evidence.

## Cross-Cutting Acceptance Scenarios

The following scenarios are mandatory evidence inputs for GWR5-GWR7:

1. **Explicit Web action:** one action changes exactly one expected workflow
   generation and returns a fresh interaction frame.
2. **Telegram callback:** the same semantic action is authorized, deduplicated,
   and projected through Telegram without raw tool parameters.
3. **Informal reply:** "мне нравится" approves only the uniquely bound current
   review and cannot approve another project or revision.
4. **Multi-act feedback:** "хорошо, но перенеси кнопку" records review feedback
   and revision work rather than silently accepting the candidate.
5. **Ambiguous reply:** plain "да" with two pending interactions asks for
   clarification and changes no state.
6. **Stale interaction:** an old button after revision/generation change fails
   closed and returns the current target and available actions.
7. **Crash before effect:** restart resumes or safely reschedules a retryable
   activity.
8. **Crash after effect:** an idempotency witness prevents duplicate mutation;
   otherwise the workflow enters reconciliation instead of retrying.
9. **Delayed human input:** restart and channel reconnect preserve the pending
   request and its ReplyRoute.
10. **Worker upgrade:** in-flight work remains on compatible code or enters a
    visible migration-required state.
11. **Offline Root:** a node-local workflow remains inspectable and can continue
    local steps while a Root-dependent activity waits explicitly.
12. **Cancellation and rollback:** cancellation is observable, cooperative,
    and linked to compensation or residual-effect evidence.
13. **Publication:** acceptance is bound to one immutable candidate digest,
    and publication produces an idempotent release/channel result.
14. **Delivery failure:** completed work remains complete while notification
    status reports delivery failure and supports bounded redelivery.
15. **Backup/restore:** active, waiting, and completed instances restore with
    their generations, tokens invalidated or preserved by policy, and no
    repeated external effect.

## Definition of Done for This Roadmap

This roadmap is complete only when:

- GWR0-GWR7 exit proofs are accepted;
- the provider decision and rollback/migration stance are documented;
- Builder and at least one second domain use the shared runtime;
- Web and Telegram pass the same semantic interaction cases;
- NLU cannot bypass allowed transitions or policy;
- restart, duplicate, stale authority, cancellation, unknown outcome,
  upgrade, and backup/restore cases have repeatable evidence;
- remaining GWR8 items are explicitly admitted or remain deferred without
  blocking the bounded single-user architecture.
