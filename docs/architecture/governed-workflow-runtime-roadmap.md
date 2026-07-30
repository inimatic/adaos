# Explainable Workflow Model Roadmap

Status: domain roadmap for the AdaOS workflow metamodel, validation,
explanation, interaction projection, NLU mediation, and optional durable
execution adapters.

Last reviewed: 2026-07-30.

This roadmap implements the
[Explainable Workflow Model and Interaction Architecture](governed-workflow-runtime.md).
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

1. The architecture document owns the workflow metamodel, invariants,
   transition/explanation semantics, record responsibilities, and adapter
   boundaries.
2. This roadmap owns implementation order and the workflow-runtime proof.
3. Domain workflow definitions remain owned by their domain architecture and
   roadmap.
4. [Operational Event Model](operational-event-model.md) owns event envelopes
   and projections; this roadmap owns workflow command/event semantics.
5. [Conversation and Channel Architecture](conversation-and-channel-architecture.md)
   owns durable threads/messages, capability-profile persistence, routing,
   materialization, and transport DeliveryAttempts; this roadmap owns semantic
   Interaction/Response lifecycles, negotiation invariants, action invocation,
   task/workflow correlation, and reply binding.
6. [Pending Actions](pending-actions.md) is a compatibility input and may be
   migrated; it is not a second permanent interaction authority.
7. [Builder Roadmap](builder-roadmap.md) owns Builder product acceptance; this
   roadmap owns the reusable runtime proven by that acceptance.
8. A consistent and validated workflow model is the delivery objective.
   External engine adoption is an optional decision produced by later
   reliability evidence.

## Current Baseline

The current repository has useful but fragmented foundations:

| Capability | Baseline | Assessment |
| --- | --- | --- |
| Local events | `LocalEventBus` and `adaos.operational-event-envelope.v1` | implemented compatibility foundation; not durable orchestration |
| Persistent node data | SQLite stores, conversation ledger, idempotent ingress records | implemented fragments; no shared workflow journal |
| Human decisions | Pending Actions and several domain-specific confirmations | implemented compatibility path; no canonical interaction registry |
| Web actions | structured chat actions and page action runtime | partial projection; transport and authorization contracts differ |
| Telegram actions | callback normalization and backend keyboard support | partial plumbing; canonical outbound/inbound bridge incomplete |
| Capability negotiation | renderer hints and channel-specific limits | no versioned effective profile or auditable presentation plan |
| Asynchronous replies | ResponseEnvelope materialization, task records, and route fragments | completion, conversation materialization, and per-transport delivery are not one recovered protocol |
| Builder workflow | persisted phase/change state, Runs, revisions, trial/publication evidence | domain-specific and partially integrated; recovery remains fragmented |
| Builder project coordination | manifests, selected project, Changes, releases, and component locks | no canonical Project portfolio/conflict aggregate |
| Long-running tasks | local asyncio tasks, worker records, polling, domain retries | several implementations; no common pause/resume authority |
| NATS | Core NATS transport and sidecar routing | at-most-once transport path; not a workflow journal |
| Shared workflow metamodel | none | primary architecture gap |
| External durable engine | none | optional later evaluation, not the objective |

## Milestone Sequence

| Milestone | Outcome | Current maturity | Horizon |
| --- | --- | --- | --- |
| GWR0 | The semantic problem, boundaries, and authority are fixed | `specified` | now |
| GWR1 | A canonical metamodel, definition compiler, and pure resolver exist | `hypothesis` | now |
| GWR2 | State explanation and semantic interactions are capability-negotiated consistently for every channel | `hypothesis` with compatibility fragments | now |
| GWR3 | Free text is constrained by pending interaction and allowed transitions | `hypothesis` | next |
| GWR4 | Builder uses one dependent Prototype -> Automation -> Publication workflow model | `hypothesis` | next |
| GWR5 | The model passes cross-channel, transition, lineage, and failure consistency proofs | `hypothesis` | next |
| GWR6 | Actual workflow, async-reply, and delivery durability gaps are measured and closed on the reference path | `hypothesis` | later |
| GWR7 | An external durable adapter is adopted only if it wins the evidence gate | `deferred decision` | later |
| GWR8 | Root/multi-user and cross-provider extensions are admitted by evidence | `deferred` | long-term |

Milestones are cumulative. GWR0-GWR5 solve the workflow fragmentation problem
without depending on an external engine. A provider experiment may run behind
a feature flag, but it cannot redefine the model or become a dependency before
the semantic proof exposes a measured durability need.

## GWR0. Architecture and Decision Baseline

**Outcome:** AdaOS has one documented boundary for workflow semantics,
validation, explanation, interactions, NLU mediation, and subordinate
persistence/execution adapters.

**Exit gate:** architecture and roadmap are discoverable, distinguish current
implementation from target state, and do not claim an external provider is
already selected.

- [x] `[must]` `GWR0-01` Define the workflow metamodel, compiler, resolver,
  explanation, interaction, IntentProposal, effect/activity, evidence, and
  ReplyRoute boundaries.
- [x] `[must]` `GWR0-02` Separate workflow statechart, artifact lineage DAG,
  execution state, and view/command context.
- [x] `[must]` `GWR0-03` Record local-first, no-silent-retry,
  commit-time-authorization, and no-dual-truth invariants.
- [x] `[must]` `GWR0-04` Record persistence and external durable engines as
  subordinate adapters evaluated only after the semantic model proof.
- [x] `[must]` `GWR0-05` Register this roadmap in the architecture navigation
  and roadmap authority map.
- [ ] `[should]` `GWR0-06` Inventory every current durable/ad-hoc workflow,
  pending response, retry loop, background task registry, and state file with
  an owner and migration disposition.
- [ ] `[could]` `GWR0-07` Produce diagrams generated from the future canonical
  workflow definitions for architecture review.
- [x] `[deferred]` `GWR0-08` Do not harmonize all historical documentation in
  this milestone; update owning documents only as implementation reaches them.
- [x] `[must]` `GWR0-09` Add a cross-document decision map linking shared
  workflow semantics, the normative Builder domain model, artifact delivery,
  multi-user seams, roadmap tasks, and acceptance evidence owners.

## GWR1. Workflow Metamodel, Compiler, and Pure Resolver

**Outcome:** workflow state, legal transitions, guards, effects, evidence, and
explanation can be described and validated without a UI, NLU model, worker, or
external execution engine.

**Admission gate:** GWR0 is complete.

**Exit proof:** unit and property tests compile a representative workflow,
reject invalid definitions, exercise every legal/illegal transition, and
produce stable fresh interaction descriptors from the resulting state.

- [x] `[must]` `GWR1-01` Add versioned schemas and typed models for
  `WorkflowDefinition`, `WorkflowInstance`, `WorkflowCommand`, and
  `WorkflowEvent`. The ABI package now publishes all four schemas, typed
  construction helpers, and a contract snapshot.
- [x] `[must]` `GWR1-02` Add typed refs for principal, aggregate, task,
  artifact, evidence, interaction, command context, and reply route. The
  shared `adaos.workflow.ref.v1` also covers workflow, component, release, and
  view refs without copying referenced state.
- [x] `[must]` `GWR1-03` Implement a pure transition resolver that consumes a
  definition, snapshot, and command and returns an accepted transition or a
  typed rejection without side effects. `governed_workflow.WorkflowResolver`
  now returns immutable before/after snapshots plus activity/event intents;
  it performs no persistence, projection, or activity execution.
- [x] `[must]` `GWR1-04` Implement definition validation for reachability,
  terminals, registered guards/activities, competing transitions, risk,
  retries, failure outcomes, and waiting-state explanation. Compilation now
  fails closed for unreachable states, ambiguous source/command edges,
  outgoing terminal edges, unexplained waits, and unknown registered code.
- [x] `[must]` `GWR1-05` Require monotonic generation and
  `expected_generation` for state-changing commands. The first resolver slice
  rejects missing/stale generations and binds duplicate detection to command
  input through a payload digest.
- [x] `[must]` `GWR1-06` Define pure guards and invariants with typed reason
  codes instead of boolean-only results. Registered guards return an admission
  result plus a stable reason code and unknown guards fail closed.
- [x] `[must]` `GWR1-07` Define effect/activity metadata for idempotency,
  side-effect class, timeout, heartbeat, cancellation, compensation, and
  unknown outcome. These are required fields of the transition ABI rather
  than executor-local defaults.
- [x] `[must]` `GWR1-08` Generate `allowed_actions` and rejection explanations
  from the same guards used to admit commands. `describe(...)` and `apply(...)`
  share the same authority and registered-guard evaluation paths.
- [x] `[should]` `GWR1-09` Add model-based tests proving state snapshots and
  projections can be rebuilt from canonical workflow events. The canonical
  transition event reducer verifies pinned definition, contiguous generation,
  state continuity, and idempotency records.
- [ ] `[should]` `GWR1-10` Add schema compatibility tests and a definition
  version/migration fixture.
- [x] `[should]` `GWR1-11` Define the stable persistence/execution adapter port
  without making it necessary for the pure resolver. `WorkflowInstanceStore`
  and `WorkflowActivityDispatcher` are narrow Protocol ports; the resolver has
  no dependency on either.
- [x] `[could]` `GWR1-12` Export a statechart representation for visualization
  and review without making the visualization format authoritative. The
  projection carries `authoritative=false` and only compiled state/edge facts.
- [x] `[deferred]` `GWR1-13` Defer arbitrary end-user workflow authoring and
  executable expressions; definitions reference only registered code.
- [x] `[should]` `GWR1-14` Emit a definition-review report covering reachable
  states, transition and cycle counts, competing guards, waiting/outcome
  coverage, concurrency scopes, projection coverage, and generated tests.
  The first report exposes reachability, terminals, waits, cycle edges, unused
  commands, and conflict scopes; conformance coverage remains in GWR5.
- [x] `[must]` `GWR1-15` Publish and compile the complete
  `adaos.workflow.transition.v1` descriptor: trigger/input, authority, guards,
  concurrency, risk, effect, every outcome, retry/cancel/reconciliation,
  evidence, events/outbox, async reply, capability requirements, explanation,
  observability, and migration. Every category is explicit and schema
  validated; executors cannot silently inject defaults.
- [x] `[must]` `GWR1-16` Add typed refs and cycle/ownership validators for
  Issue, Change, workflow, artifact, component dependency, execution,
  conversation/interaction, release/deployment, authority, and view/context
  planes; prohibit copying mutable state between them. The relationship-edge
  ABI is reference-only and validates each plane's independent cycle policy.
- [ ] `[should]` `GWR1-17` Define typed parent/child workflow composition,
  authority delegation, join policies, partial outcomes, cancellation,
  compensation, evidence aggregation, and late-result behavior.

## GWR2. Explanation, Projections, and Semantic Affordances

**Outcome:** one snapshot produces one explanation and one semantic Interaction
set; capability negotiation then selects a safe Web, Telegram, text, CLI, or
test presentation without changing command legality.

**Admission gate:** GWR1 can compile and resolve a representative definition.

**Exit proof:** generated conformance tests prove that every reachable state is
explainable, every displayed action is accepted under the same context, every
blocked action has a reason code, channel limits produce an auditable safe
presentation/fallback, and an unsupported requirement remains explainably
blocked.

- [ ] `[must]` `GWR2-01` Define `WorkflowSnapshot` and a pure `explain()` result
  containing state, target, progress, blockers, evidence, and allowed commands.
- [ ] `[must]` `GWR2-02` Derive `allowed_actions` from the same transitions and
  guards used by `invoke()`; forbid separately maintained UI action tables.
- [ ] `[must]` `GWR2-03` Define `adaos.conversation.interaction.v1` as a semantic
  projection of commands, typed input, risk, and channel fallback.
- [ ] `[must]` `GWR2-04` Generate state/transition coverage tests and reject a
  definition with an unexplained reachable or waiting state.
- [ ] `[must]` `GWR2-05` Define stable reason codes and localization keys for
  admitted, blocked, stale, ambiguous, and policy-denied commands.
- [ ] `[must]` `GWR2-06` Adapt Builder Lifecycle, process summary, and Preview
  target to canonical projections rather than independently inferred stages.
- [ ] `[must]` `GWR2-07` Adapt Web chat, Telegram controls, and text fallback to
  the same semantic interaction and canonical invocation ingress.
- [ ] `[must]` `GWR2-08` Bind actions to principal, command context, workflow,
  immutable target, and expected generation with opaque tokens.
- [ ] `[should]` `GWR2-09` Unify Pending Actions with the semantic interaction
  model or document one bounded compatibility adapter and retirement path.
- [ ] `[should]` `GWR2-10` Add a developer inspector showing why a transition is
  available or blocked without exposing provider internals.
- [ ] `[could]` `GWR2-11` Export a generated graph/timeline for review and docs.
- [ ] `[deferred]` `GWR2-12` Defer universal Telegram parity for file trees,
  search, and rich Preview; semantic command parity is sufficient.
- [ ] `[must]` `GWR2-13` Implement the independent ConversationInteraction
  lifecycle from creation/projection through partial answer, validation,
  completion, expiry, cancellation, and supersession.
- [ ] `[must]` `GWR2-14` Add typed `InteractionResponse` records binding actor,
  values/source message, presentation, target, generations, validation,
  correction, and consumed command/rejection.
- [ ] `[must]` `GWR2-15` Add versioned effective transport + client + surface
  capability profiles with feature limits, locale/accessibility, secure input,
  progress/update, handoff, acknowledgement, and freshness metadata.
- [ ] `[must]` `GWR2-16` Implement deterministic capability/policy negotiation
  that produces an auditable InteractionPresentation, preserves every required
  command/risk/confirmation, and otherwise returns a typed fallback or
  `unsupported` wait reason.
- [ ] `[should]` `GWR2-17` Add presentation conformance fixtures for Web,
  Telegram, text-only, reconnect/profile change, secure handoff, and a client
  whose capability limits cannot represent the requested interaction.

## GWR3. NLU Mediation and Informal Responses

**Outcome:** natural language can answer pending questions, request
transitions, add feedback, or start unrelated work without becoming direct
mutation authority.

**Admission gate:** GWR2 exposes current context, pending interactions, and
allowed commands as a bounded interpretation grammar.

**Exit proof:** a multilingual evaluation set covers explicit and informal
answers, multi-act feedback, ambiguous targets, corrections, negation, stale
context, and unrelated requests; no model output changes state outside the
resolver.

- [ ] `[must]` `GWR3-01` Add `adaos.intent.proposal.v1` with source message,
  semantic acts, alternatives, allowed-command snapshot, model identity, and
  disposition.
- [ ] `[must]` `GWR3-02` Resolve a deterministic action token without NLU and
  route free text against its explicit pending interaction first.
- [ ] `[must]` `GWR3-03` Require clarification when more than one pending target
  or command context fits a free-text answer.
- [ ] `[must]` `GWR3-04` Support multi-act utterances instead of forcing every
  message into one intent.
- [ ] `[must]` `GWR3-05` Keep new Issue/change feedback, read-only questions,
  context selection, and workflow commands distinct.
- [ ] `[must]` `GWR3-06` Reject proposed commands absent from `allowed_actions`
  or lacking required typed arguments.
- [ ] `[must]` `GWR3-07` Define risk policy for bounded free-text confirmation
  versus an explicit protected interaction.
- [ ] `[must]` `GWR3-08` Persist interpretation, clarification, correction, and
  committed result with privacy and retention controls.
- [ ] `[should]` `GWR3-09` Build Russian and English offline evaluation from
  real corrected cases, including UTF-8 transport coverage.
- [ ] `[should]` `GWR3-10` Measure false-transition and clarification rates,
  not model confidence alone.
- [ ] `[could]` `GWR3-11` Parse common short answers and ordinal choices
  deterministically before invoking a model.
- [ ] `[deferred]` `GWR3-12` Defer autonomous workflow induction from arbitrary
  conversations.

## GWR4. Builder Domain Workflow Model

**Outcome:** Builder has one inspectable model connecting request, Issues,
Change, Prototype, Automation, Trial, and Publication instead of independent
stage buckets and UI-specific rules.

**Admission gate:** GWR1-GWR3 can define, explain, project, and invoke the
required transitions.

**Exit proof:** the Builder definition compiles; every state and edge has a
domain meaning, guard, effect/evidence contract, and explanation; generated
tests cover all legal and representative illegal paths.

- [ ] `[must]` `GWR4-01` Encode the normative Builder Change statechart and
  transition catalogue from
  [Builder Conversational Development](builder-conversational-development.md#builder-change-statechart),
  explicitly separating it from artifact lineage and task execution state.
- [ ] `[must]` `GWR4-02` Key the workflow instance by canonical `change_id` and
  retain exact project, base release, artifact, and command-context refs.
- [ ] `[must]` `GWR4-03` Model Prototype -> Automation -> Publication as
  derivation and promotion, not three mutually independent mutable stages.
- [ ] `[must]` `GWR4-04` Define direct Automation, prototype-first, revise,
  cancel, failure, retry-as-new-Run, Trial reject/accept, and Publication paths.
- [ ] `[must]` `GWR4-05` Define invariants: one focused Change per command
  context but multiple open Changes per project; one admitted overlapping
  mutation per base generation; immutable accepted revisions; exact source
  Prototype for Automation; and exact candidate digest for Publication.
- [ ] `[must]` `GWR4-06` Define LLM, Codex, validation, Git checkpoint, Trial,
  Publication, and notification as registered effects/activities rather than
  implicit phase code.
- [ ] `[must]` `GWR4-07` Generate Lifecycle hierarchy, process summary,
  available controls, conversation focus, and Preview target from the same
  snapshot and lineage refs.
- [ ] `[must]` `GWR4-08` Keep Lifecycle selection and Preview selection as view
  context; neither changes the workflow without an explicit command.
- [ ] `[must]` `GWR4-09` Map current Builder JSON, sessions, Pending Actions,
  and UI handlers to canonical concepts with retain/adapt/retire disposition.
- [ ] `[should]` `GWR4-10` Add a compact workflow explanation to the chat so a
  user can ask what is happening, why, and what can happen next.
- [ ] `[could]` `GWR4-11` Add a generated graph/timeline inspector as a rich
  detail view, not the primary control surface.
- [ ] `[deferred]` `GWR4-12` Defer simultaneous multi-user approval and artifact
  merging to GWR8.
- [ ] `[must]` `GWR4-13` Implement scoped Change focus and write-conflict keys;
  switching focus is view/command context and never a business transition.
- [ ] `[must]` `GWR4-14` Distinguish `iteration`, `experiment`, `evaluation`, and
  `recovery` Runs; require explicit reviewed adoption before an Experiment can
  advance the accepted Revision line.
- [ ] `[must]` `GWR4-15` Define typed `mock`, `fixture`, `sandbox`,
  `live_readonly`, and `live` binding profiles, Prototype isolation policy,
  implementation mappings, and visible Preview data mode.
- [ ] `[must]` `GWR4-16` Complete the Review lifecycle with submit, withdraw,
  dismiss, convert-to-Issue, accept-as-constraint, supersede, and resolve
  commands; hard deletion remains limited to unsent local drafts.
- [ ] `[must]` `GWR4-17` Define context-facet requirements and a packet coverage
  report that fails before LLM/Codex submission when target structure, ABI,
  constraints, data policy, or execution authority is missing or ambiguous.
- [ ] `[must]` `GWR4-18` Publish `adaos.builder.project.v1` as a portfolio and
  coordination aggregate with source/stable/installed/DEV/candidate refs,
  project policy, component boundary, open Changes, conflict/dependency index,
  scoped focus, workflow versions, and archive state.
- [ ] `[must]` `GWR4-19` Derive project summary and commands from its linked
  planes; never infer one global project stage from the focused Change or most
  recent Run.

## GWR5. Cross-Channel and End-to-End Consistency Proof

**Outcome:** the Builder model remains consistent when driven through Web,
Telegram, free text, deterministic controls, background results, and direct SDK
tests.

**Admission gate:** the GWR4 definition is compiled and projected through GWR2
and GWR3.

**Exit proof:** one empty representative scenario completes the full flow on
this development machine; every channel observes the same state and actions;
lineage, evidence, and final Publication agree without direct state repair.

- [ ] `[must]` `GWR5-01` Run request -> Issues/Change -> Prototype or direct
  Automation -> verification -> Trial -> Publication through the canonical
  resolver.
- [ ] `[must]` `GWR5-02` Prove Web buttons, Telegram options, informal replies,
  and SDK commands invoke identical command identities and guards.
- [ ] `[must]` `GWR5-03` Prove every UI action shown by `explain()` succeeds or
  returns a typed concurrency/policy change, never an unrelated handler rule.
- [ ] `[must]` `GWR5-04` Prove blocked commands expose the same reason code and
  semantically equivalent explanation across channels.
- [ ] `[must]` `GWR5-05` Bind review and Publication to exact immutable target
  digests and reject stale Lifecycle/chat actions.
- [ ] `[must]` `GWR5-06` Prove a background Codex/LLM result advances only the
  originating Change and cannot inherit another Webspace's view context.
- [ ] `[must]` `GWR5-07` Prove Lifecycle nodes, process status, conversation
  focus, and proto:/active:/public: Preview labels remain mutually consistent.
- [ ] `[must]` `GWR5-08` Record transition coverage, artifact lineage, tests,
  Trial, Git, Publication, and delivery evidence for the representative run.
- [ ] `[must]` `GWR5-09` Update the Builder roadmap with the accepted semantic
  proof without copying this checklist.
- [ ] `[should]` `GWR5-10` Measure time to understand current state, action
  mismatch defects, clarification rate, and diagnosis effort versus the old
  Builder path.
- [ ] `[could]` `GWR5-11` Add mutation testing that deliberately removes guards
  or projections and proves conformance tests fail.
- [ ] `[deferred]` `GWR5-12` Do not block the semantic proof on choosing or
  integrating an external durable engine.
- [ ] `[must]` `GWR5-13` Prove two open Changes can be inspected independently,
  focus changes no business state, non-overlapping work is admitted, and
  overlapping stale writes fail with an explicit rebase/split/supersede choice.
- [ ] `[must]` `GWR5-14` Prove an Experiment can be compared and discarded
  without changing `active:` or Publication, and only an explicit
  `adopt_experiment` transition can promote its Revision.
- [ ] `[must]` `GWR5-15` Prove Prototype defaults to mock/fixture, switching a
  compatible Preview binding profile does not rewrite the UI Revision, and
  undeclared live reads/writes fail closed.
- [ ] `[must]` `GWR5-16` Prove a withdrawn Review disappears from active model
  context without losing its audit tombstone, while an accepted constraint can
  only be superseded with a reason.
- [ ] `[must]` `GWR5-17` Prove a spatial UI request receives parent/sibling/order,
  responsive, ABI, data-binding, and active-constraint facets or stops for
  clarification before the model is called.
- [ ] `[should]` `GWR5-18` Record definition complexity and context-sufficiency
  metrics alongside cycle time, clarification, repeated-correction, and action
  mismatch rates.
- [ ] `[must]` `GWR5-19` Prove one Interaction preserves command identity,
  risk, confirmation, and target when negotiated as a Web form, Telegram
  choices, numbered text, or a cross-channel deep-link handoff.
- [ ] `[must]` `GWR5-20` Prove an unsupported required capability leaves the
  workflow waiting with an explanation rather than hiding controls or
  weakening confirmation.
- [ ] `[must]` `GWR5-21` Prove several pending interactions are independently
  addressable and an unbound free-text answer changes no state until the target
  is clarified.
- [ ] `[must]` `GWR5-22` Prove the Project aggregate reports two independent
  Changes concurrently while detecting an indirect conflict through a shared
  skill/component dependency.
- [ ] `[should]` `GWR5-23` Prove one multi-component Change joins exact scenario
  and skill Runs into one dependency-locked candidate and reports partial
  success without partial promotion.

## GWR6. Durability Gap Assessment and Reference Persistence

**Outcome:** AdaOS knows which workflow, human-wait, asynchronous result, and
delivery reliability requirements the semantic model actually creates and
whether the current local persistence can satisfy them.

**Admission gate:** GWR5 passes semantically; failures can now be attributed to
execution/recovery rather than an inconsistent transition model.

**Exit proof:** restart and fault tests identify concrete gaps, the minimal
reference persistence closes the required single-user workflow and
result/delivery gaps, and an ADR states whether an external engine evaluation
is warranted.

- [ ] `[must]` `GWR6-01` Inventory waits, timers, callbacks, long activities,
  retries, cancellation, unknown outcomes, and reply delivery in the accepted
  Builder definition.
- [ ] `[must]` `GWR6-02` Define the minimum persistence contract: snapshot
  generation, transition journal, idempotent inbox, outbox, activity attempts,
  pending interactions, and ReplyRoute.
- [ ] `[must]` `GWR6-03` Implement only the missing reference SQLite semantics,
  reusing current AdaOS stores rather than creating another per-skill state
  system.
- [ ] `[must]` `GWR6-04` Inject crashes before and after every modifying effect
  and distinguish safe retry from `outcome_unknown` reconciliation.
- [ ] `[must]` `GWR6-05` Cover delayed human input, process restart,
  cancellation, definition upgrade, backup/restore, and offline Root.
- [ ] `[must]` `GWR6-06` Add commit-time checks for permission, target digest,
  approval witness, current generation, and effect binding.
- [ ] `[must]` `GWR6-07` Record resource use, recovery complexity, defect rate,
  and operator repair cost for the reference path.
- [ ] `[must]` `GWR6-08` Write an ADR: reference persistence sufficient, or
  external durable adapter evaluation admitted with named unmet requirements.
- [ ] `[should]` `GWR6-09` Add bounded retention, compaction, redacted
  diagnostics, and operator describe/recover/cancel surfaces.
- [ ] `[could]` `GWR6-10` Use JetStream for a transport/outbox experiment only
  if delivery durability is one of the measured gaps.
- [ ] `[deferred]` `GWR6-11` Defer distributed consensus and active-active local
  workflow execution.
- [ ] `[must]` `GWR6-12` Implement channel-neutral ResponseEnvelopes for
  accepted, progress, input-required, terminal, and notification messages with
  workflow/task correlation, monotonic sequence, sensitivity, and coalesce key.
- [ ] `[must]` `GWR6-13` Persist ReplyRoutes, an outbound envelope outbox, and
  idempotent per-presentation/transport DeliveryAttempts; redelivery must never
  reinvoke the originating command or activity.
- [ ] `[must]` `GWR6-14` Recover pending interactions, terminal envelopes, and
  delivery attempts after restart; preserve a queryable terminal result when
  every route expires or is undeliverable.
- [ ] `[should]` `GWR6-15` Add ordered progress, update coalescing, attention
  policy, quiet periods/preferences, alternate authorized routes, delivery
  receipts, and operator inspection without coupling delivery to business
  completion.

## GWR7. Optional External Durable Adapter

**Outcome:** only if GWR6 admits the work, an external product is evaluated as
an interchangeable execution adapter without changing workflow semantics.

**Admission gate:** the GWR6 ADR names reliability requirements the reference
path cannot meet economically.

**Exit proof:** a candidate runs the same Builder model and conformance/fault
suite behind a feature flag, and an ADR records adopt, postpone, or reject.

### Evaluation Matrix

Candidates are assessed against the admitted gaps plus local-first operation,
Python 3.11, Windows/Linux packaging, SQLite/PostgreSQL topology, signals and
waits, upgrades, backup, privacy, observability, resource cost, license,
maturity, and exit cost.

- [ ] `[deferred]` `GWR7-01` Implement no external adapter until GWR6 admits a
  measured need.
- [ ] `[should]` `GWR7-02` If admitted, freeze a provider conformance suite from
  GWR5-GWR6 before choosing a product.
- [ ] `[could]` `GWR7-03` Evaluate DBOS for an embedded Python/local and
  PostgreSQL-central topology.
- [ ] `[could]` `GWR7-04` Evaluate Temporal for Root-level distributed and
  multi-user orchestration.
- [ ] `[could]` `GWR7-05` Evaluate Restate for a keyed workflow/actor sidecar.
- [ ] `[must]` `GWR7-06` If a candidate is adopted, keep provider-native APIs
  below the AdaOS SDK and preserve canonical commands, events, and explanation.
- [ ] `[must]` `GWR7-07` If adopted, package, supervise, update, health-check,
  back up, and roll back the adapter through AdaOS lifecycle rails.
- [ ] `[must]` `GWR7-08` Record an adoption/postponement/rejection ADR with
  measured benefit, operational cost, migration, and exit plan.
- [ ] `[deferred]` `GWR7-09` Do not maintain several production providers in
  parallel without separate proven deployment classes.

## GWR8. Root, Multi-User, and Federation Extensions

**Outcome:** only after the single-user plane is stable, workflow instances may
coordinate multiple principals, nodes, responsibility zones, and reusable
change proposals.

**Admission gate:** GWR6 is accepted for bounded single-user work; GWR7 is
complete only if an external adapter was admitted; and a concrete multi-user
use case supplies authority, privacy, conflict, and availability requirements.

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
- [ ] `[deferred]` `GWR8-09` Project an approved personal Change as an optional
  candidate-upgrade offer bound to exact release and evidence refs; registry
  subscriptions and channels remain owned by the artifact pipeline.

## Cross-Cutting Acceptance Scenarios

The following scenarios are mandatory evidence inputs for GWR1-GWR6 and, if
admitted, the GWR7 adapter:

1. **Invalid model:** unreachable state, missing target, unknown effect,
   conflicting transitions, or unexplained wait makes definition compilation
   fail before runtime.
2. **Complete explanation:** every reachable snapshot returns state, target,
   reason, blockers, evidence, and allowed commands.
3. **Affordance equivalence:** every action projected by `explain()` is admitted
   by `invoke()` under the same actor/context, or returns only a typed
   generation/policy change.
4. **Blocked explanation:** representative invalid commands produce stable
   reason codes and equivalent meaning in Web, Telegram, CLI, and tests.
5. **Lineage separation:** selecting a Prototype/Automation/Publication node
   changes the view but cannot change workflow state or rewrite provenance.
6. **Dependent Builder path:** Automation identifies its exact source
   Prototype, and Publication identifies its exact accepted candidate.
7. **Explicit Web action:** one command changes exactly one expected workflow
   generation and returns a fresh interaction frame.
8. **Telegram callback:** the same command is authorized and projected without
   raw tool parameters.
9. **Informal reply:** "мне нравится" approves only the uniquely bound current
   review and cannot approve another project or revision.
10. **Multi-act feedback:** "хорошо, но перенеси кнопку" records review feedback
    and revision work rather than silently accepting the candidate.
11. **Ambiguous reply:** plain "да" with two pending interactions asks for
    clarification and changes no state.
12. **Stale interaction:** an old control after revision/generation change fails
    closed and returns the current target and allowed actions.
13. **Background result isolation:** an LLM/Codex callback can advance only the
    Change and Run captured at submission, never the current view selection.
14. **Crash before effect:** restart resumes or safely reschedules a retryable
    activity.
15. **Crash after effect:** idempotency prevents duplicate mutation; otherwise
    the workflow enters reconciliation instead of retrying.
16. **Delayed human input:** restart and channel reconnect preserve the pending
    request and its ReplyRoute.
17. **Cancellation and rollback:** cancellation is observable and linked to
    compensation or residual-effect evidence.
18. **Publication:** acceptance is bound to one immutable candidate digest and
    publication produces an idempotent release/channel result.
19. **Backup/restore:** active and waiting instances restore with correct
    generations and no repeated external effect.
20. **Scoped focus and concurrency:** two open Changes retain separate bases,
    focus, Reviews, Runs, and replies; changing focus mutates neither, while an
    overlapping stale commit fails with a typed conflict.
21. **Experiment isolation:** a completed Experiment remains an alternative
    until an explicit adoption decision and cannot silently replace `active:`
    or satisfy Publication.
22. **Data-mode isolation:** Prototype uses mock/fixture by default; a compatible
    Preview binding switch preserves the Revision; undeclared live effects are
    rejected before execution.
23. **Review correction:** an erroneous submitted Review can be withdrawn and
    is excluded from subsequent context, while its audit tombstone remains.
24. **Context sufficiency:** a request such as "put checkboxes on the left"
    either receives the target's parent/sibling/order and ABI constraints or
    asks for clarification before LLM/Codex execution.
25. **Capability downgrade:** one Interaction preserves command, risk,
    confirmation, and target across Web form, Telegram choices, numbered text,
    and deep-link handoff; an unsafe downgrade returns `unsupported`.
26. **Partial and corrected answer:** a multi-field response can be validated,
    corrected, and consumed once without rewriting the original message or
    accepting a stale generation.
27. **Async restart:** a command is acknowledged, the process restarts, and the
    eventual terminal result materializes in the originating conversation from
    the persisted task/outbox.
28. **Delivery independence:** a terminal result is durable while Telegram
    delivery fails; redelivery succeeds without repeating the skill, LLM,
    Codex, activity, or workflow transition.
29. **Progress ordering:** late progress cannot replace a newer progress or
    terminal projection; coalescing reduces noise without losing evidence.
30. **Interaction ambiguity:** two pending interactions plus an unbound "yes"
    produce clarification and no state change.
31. **Relationship-plane separation:** an Issue dependency, completed Run,
    selected Preview, and channel delivery receipt cannot independently change
    the Change state or artifact lineage.
32. **Project portfolio:** two non-overlapping Changes progress independently;
    an indirect shared-component conflict is visible and blocks only the
    conflicting commit/promotion scope.
33. **Subworkflow partial result:** one child succeeds and one fails; the
    parent records both, applies its declared join/failure policy, and never
    promotes a partial dependency lock.

## Definition of Done for This Roadmap

This roadmap is complete only when:

- GWR0-GWR6 exit proofs are accepted, and GWR7 has an explicit
  not-admitted/adopted/postponed/rejected decision;
- one canonical model drives Builder state, transitions, explanations,
  Lifecycle, interactions, NLU constraints, and conformance tests;
- the normative Builder statechart, transition catalogue, concurrency scope,
  Run purposes, data modes, Review lifecycle, and context-sufficiency rules
  map to implementation tasks and repeatable evidence;
- semantic Interaction, InteractionResponse, capability negotiation,
  ResponseEnvelope, ReplyRoute, and DeliveryAttempt lifecycles remain distinct
  and pass cross-channel/restart tests;
- Project coordination and every relationship plane have explicit owners,
  edge/cycle rules, typed refs, and no shared mutable truth;
- Builder and at least one second domain use the shared workflow model;
- Web and Telegram pass the same semantic interaction cases;
- NLU cannot bypass allowed transitions or policy;
- restart, duplicate, stale authority, cancellation, unknown outcome,
  upgrade, and backup/restore cases have repeatable evidence;
- external provider selection is not required for semantic completion and, if
  adopted, remains below the AdaOS workflow contract;
- remaining GWR8 items are explicitly admitted or remain deferred without
  blocking the bounded single-user architecture.
