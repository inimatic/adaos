# Governed Data-Driven Workflow Model Roadmap

Status: domain roadmap for the AdaOS workflow metamodel, validation,
explanation, interaction projection, NLU mediation, and optional durable
execution adapters.

Last reviewed: 2026-07-31.

This roadmap implements the
[Governed Data-Driven Workflow Model and Interaction Architecture](governed-workflow-runtime.md).
The current local and live-channel proof ledger is
[Builder Governed Workflow Verification 2026-08-01](builder-workflow-verification-2026-08-01.md).
It owns sequencing and acceptance inside this domain. It does not replace the
[Governed Evolution Roadmap](governed-evolution-roadmap.md), Builder roadmap,
conversation architecture, Conversational Control Interface, NLU roadmap, or
operational event roadmap. Tasks that belong to those domains are linked rather
than redefined.

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
6. [Conversational Control Interface](conversational-interface.md) owns the
   shared conversational input/output, NLU data, Teacher-promotion, and
   conversation-story artifact boundary; this roadmap owns the workflow command,
   resolver, guard, state, and path-proof semantics consumed by that boundary.
7. [Pending Actions](pending-actions.md) is a compatibility input and may be
   migrated; it is not a second permanent interaction authority.
8. [Builder Roadmap](builder-roadmap.md) owns Builder product acceptance; this
   roadmap owns the reusable runtime proven by that acceptance.
9. A consistent and validated workflow model is the delivery objective.
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
| Telegram actions | callback normalization, canonical keyboard projection, opaque token ingress, and idempotent response persistence | live read-only Builder turn with five inline actions validated 2026-07-31; mutating callbacks and provider receipts remain open |
| Capability negotiation | renderer hints and channel-specific limits | no versioned effective profile or auditable presentation plan |
| Asynchronous replies | ResponseEnvelope materialization, task records, and route fragments | completion, conversation materialization, and per-transport delivery are not one recovered protocol |
| Builder workflow | persisted phase/change state, Runs, revisions, trial/publication evidence | domain-specific and partially integrated; recovery remains fragmented |
| Builder project coordination | manifests, selected project, Changes, releases, and component locks | no canonical Project portfolio/conflict aggregate |
| Conversation stories | early golden-conversation fixtures in NLU tests; workflow graph export exists as a non-authoritative projection | no shared story schema, workflow-path coverage, or Builder package admission yet |
| Long-running tasks | local asyncio tasks, worker records, polling, domain retries | several implementations; no common pause/resume authority |
| NATS | Core NATS transport and sidecar routing, bounded reconnect cleanup | at-most-once transport path; target-zone publish is not durable hub acceptance or a workflow journal |
| Shared workflow metamodel | `src/adaos/abi/workflow.*`, compiler/resolver, strict manifest-bound loader, definition review, migration/composition fixtures, package workflow lock | validated-local artifact foundation; registry trust, complete adapter binding, and activation admission remain open |
| External durable engine | none | optional later evaluation, not the objective |

## Milestone Sequence

| Milestone | Outcome | Current maturity | Horizon |
| --- | --- | --- | --- |
| GWR0 | The semantic problem, boundaries, and authority are fixed | `specified` | now |
| GWR1 | A canonical metamodel, definition compiler, pure resolver, and admitted `workflow.json` artifact contract exist | `validated-local` semantic core; data authoring/package admission open | now |
| GWR2 | State explanation and semantic interactions are capability-negotiated consistently for every channel | `validated-local` with bounded compatibility adapters | now |
| GWR3 | Free text is constrained by pending interaction and allowed transitions | `validated-local` | next |
| GWR4 | Builder uses one dependent Prototype -> Automation -> Publication workflow model whose authoritative transition catalogue is data | `validated-local`; DEV `builder_skill/workflow.json` is authority and the Python transition constructor is retired | next |
| GWR5 | The model passes cross-channel, transition, lineage, and failure consistency proofs | `validated-local` | next |
| GWR6 | Actual workflow, async-reply, and delivery durability gaps are measured and closed on the reference path | `validated-local` | later |
| GWR7 | An external durable adapter is adopted only if it wins the evidence gate | `postponed by evidence` | later |
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
- [x] `[should]` `GWR0-06` Inventory every current durable/ad-hoc workflow,
  pending response, retry loop, background task registry, and state file with
  an owner and migration disposition. The source inventory is maintained in
  [Governed Workflow Runtime Inventory](governed-workflow-runtime-inventory.md)
  and classifies each surface as canonical workflow, separate canonical model,
  compatibility, projection/evidence, or open reliability gap.
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
external execution engine; one strict `workflow.json` can then be admitted as
an immutable skill/scenario package input.

**Admission gate:** GWR0 is complete.

**Exit proof:** unit and property tests compile a representative workflow,
reject invalid definitions, exercise every legal/illegal transition, produce
stable fresh interaction descriptors, and prove that an LLM-authored candidate
cannot become active without manifest, registry, policy, conformance, package,
and release-lock admission.

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
- [x] `[should]` `GWR1-10` Add schema compatibility tests and a definition
  version/migration fixture. The explicit migration contract validates source
  and target definitions, authority, admitted state maps, context deltas, and
  generation; versioned replay crosses the migration event deterministically.
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
- [x] `[should]` `GWR1-17` Define typed parent/child workflow composition,
  authority delegation, join policies, partial outcomes, cancellation,
  compensation, evidence aggregation, and late-result behavior. The
  composition ABI enforces unique child/correlation identities and narrower
  delegated permissions; the pure join resolver cannot promote an incomplete
  required-child set.
- [x] `[must]` `GWR1-18` Freeze the single-definition component contract:
  optional `workflow.manifest: workflow.json` in `skill.yaml` and
  `scenario.yaml`, strict UTF-8 JSON, exactly one
  `adaos.workflow.definition.v1` object, no arbitrary path, inline governed
  definition, multiple file, or role-specific variants. Absence remains an
  explicit no-workflow case.
- [x] `[must]` `GWR1-19` Make the existing `src/adaos/abi/workflow.*` family a
  self-contained authoring ABI: `definition.transitions[]` references the full
  transition schema; stable schema resolution works outside the Python
  process; registry refs and typed parameters cover guards, effects,
  activities, compensation, policy, evidence, and projections. Preserve v1
  only for compatible tightening; publish a new schema id for incompatible
  changes.
- [x] `[must]` `GWR1-20` Add the bounded JSON loader, duplicate-key rejection,
  size/depth/state/transition limits, canonical serialization, and semantic
  definition digest. Raw formatting and object-key order cannot change the
  semantic digest; semantic array order and transition priority remain
  deliberate data.
- [x] `[must]` `GWR1-21` Publish typed
  `workflow.definition_artifact`, `workflow.validation_report`,
  `workflow.registry_entry`, and `workflow.admission` records. Keep mutable
  candidate/review/activation state and LLM provenance outside the pure
  definition while binding every decision to exact definition, policy,
  registry-contract, source, and package digests.
- [x] `[must]` `GWR1-22` Implement the registered-code trust model: distinguish
  platform-, package-, and dependency-owned adapters; validate typed input/
  output, side effect, permission ceiling, sandbox policy, owner package, and
  contract digest; reject mutable global-name resolution and any definition
  that broadens its registered contract.
- [x] `[must]` `GWR1-23` Bootstrap one-graph role policy with verified `guest`
  and `registered` role claims. Generate role-dependent `allowed_actions` from
  the same resolver, default unknown roles/permissions to deny, prohibit role
  self-assignment, and prove that authentication alone grants no privileged
  effect. Advanced role/zone approval remains GWR8.
- [x] `[must]` `GWR1-24` Add one structured authoring validation surface for
  humans, Builder, and LLM repair. Its stable diagnostics cover JSON/schema
  paths, unknown or incompatible registry refs, ambiguity/reachability,
  missing outcomes/explanations, unsafe authority/risk, migration, complexity,
  and generated conformance failures without weakening policy to obtain a
  pass.
- [x] `[must]` `GWR1-25` Bind `workflow.json` to the existing artifact pipeline:
  package file record plus canonical `workflow_lock`; validation evidence and
  required adapter contract locks; ProjectRelease adapter resolution and
  `workflow_binding_digest`; inspectable WorkspaceLock definition/binding
  digests. Changing code or workflow produces a new package and component
  version; neither can be delivered independently.
- [x] `[must]` `GWR1-26` Make activation atomic across code and definition:
  stage the complete release, build the candidate adapter registry, compile and
  validate migrations, health-check, then switch one WorkspaceLock/runtime
  generation through CAS. Pin exact definition/package/binding digests in each
  instance and roll back only to a prior complete generation.
- [x] `[should]` `GWR1-27` Supply LLM authoring with the exact ABI, registered
  adapter catalogue, role/policy ceilings, domain invariants, examples, and
  current definition digest; persist model/context provenance and a bounded
  diagnostic repair history without treating model output as admission.

Checked local evidence for the completed authoring slice:
`tests/test_workflow_artifacts.py`, `tests/test_manifest_abi.py`,
`tests/test_governed_workflow.py`, `tests/test_workflow_registry.py`,
`tests/test_workflow_admission.py`, `tests/test_artifact_package_store.py`,
`tests/test_workflow_persistence.py`, `tests/test_builder_governed_workflow.py`,
and `tests/test_governed_workflow_artifact_e2e.py`. The loader rejects missing,
unreferenced, wrongly named, duplicate-key, multi-value, unsupported-schema,
and over-limit inputs. ABI validation resolves the workflow schema family from
published schema files, package verification recomputes canonical definition,
validation, adapter, and binding locks, ProjectRelease/WorkspaceLock preserve
those locks, activation produces a typed `workflow.admission` candidate, and
runtime instances pin definition/package/binding digests when they are created
from an activated package. `workflow.authoring_context` now exports the exact
workflow ABI digests, active adapter catalogue, role-policy floor, domain
invariants, examples, and limits; `WorkflowAuthoringHistoryStore` persists
model/context provenance, validation-report digests, candidate digests,
diagnostics, and bounded repair history outside the pure definition.

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

- [x] `[must]` `GWR2-01` Define `WorkflowSnapshot` and a pure `explain()` result
  containing state, target, progress, blockers, evidence, and allowed commands.
  `WorkflowResolver.describe()` now returns those facets from the canonical
  instance and the same guard evaluation used by command admission.
- [x] `[must]` `GWR2-02` Derive `allowed_actions` from the same transitions and
  guards used by `invoke()`; forbid separately maintained UI action tables.
- [x] `[must]` `GWR2-03` Define `adaos.conversation.interaction.v1` as a semantic
  projection of commands, typed input, risk, and channel fallback. The shared
  projection adapter binds workflow commands into a durable Interaction.
- [x] `[must]` `GWR2-04` Generate state/transition coverage tests and reject a
  definition with an unexplained reachable or waiting state. The compiler and
  generated conformance cases cover every state and transition edge.
- [x] `[must]` `GWR2-05` Define stable reason codes and localization keys for
  admitted, blocked, stale, ambiguous, and policy-denied commands.
- [x] `[must]` `GWR2-06` Adapt Builder Lifecycle, process summary, and Preview
  target to canonical projections rather than independently inferred stages.
  `adaos.builder.process_projection.v1` now derives the dependent lineage,
  blockers, available commands, and exact `proto:`/`active:`/`public:` choices
  from the pinned Builder Change snapshot. View selection remains a separate
  non-business generation.
- [ ] `[must]` `GWR2-07` Adapt Web chat, Telegram controls, and text fallback to
  the same semantic interaction and canonical invocation ingress.
  Web actions, Telegram inline callbacks, and numbered text now originate from
  one InteractionPresentation; callbacks enter the shared response service
  without NLU or direct Builder invocation. Builder's current-project answer
  now uses this path for its bounded inspect/intake/Preview controls. Completion
  still requires registered activity adapters and live Web/Telegram acceptance
  for every mutating Builder control; unavailable executors are deliberately
  not projected. A live Telegram turn on 2026-07-31 proved webhook ingress,
  Builder materialization, root relay, and five inline actions for the read-only
  current-project interaction. On 2026-08-01 the production backend relay and
  local current core repeated the real-bot proof: Telegram received the same
  five inline actions as the Web presentation, and exact action-label text was
  resolved before Automation/NLU. This is evidence for the channel adapter,
  not completion evidence for executor-backed mutating controls.
- [x] `[must]` `GWR2-08` Bind actions to principal, command context, workflow,
  immutable target, and expected generation with opaque tokens. Tokens are
  presentation references, never authority; generation, principal scope,
  target, risk, and confirmation remain in the semantic action.
- [x] `[should]` `GWR2-09` Unify Pending Actions with the semantic interaction
  model or document one bounded compatibility adapter and retirement path.
  Builder's migration map retains Pending Action refs only as compatibility
  evidence/input, routes new decisions through ConversationInteraction and
  InteractionResponse, and retires direct Pending Action mutation after all
  skill callers use the shared registry.
- [x] `[should]` `GWR2-10` Add a developer inspector showing why a transition is
  available or blocked without exposing provider internals. The pure
  description includes allowed/blocked commands, reason codes/keys, blockers,
  evidence refs, and progress without activity-provider details.
- [x] `[could]` `GWR2-11` Export a generated graph/timeline for review and docs.
  The non-authoritative statechart projection is generated from compiled
  definitions.
- [x] `[deferred]` `GWR2-12` Defer universal Telegram parity for file trees,
  search, and rich Preview; semantic command parity is sufficient.
- [x] `[must]` `GWR2-13` Implement the independent ConversationInteraction
  lifecycle from creation/projection through partial answer, validation,
  completion, expiry, cancellation, and supersession. Lifecycle transitions
  are generation-guarded and persist in the node-local conversation store.
- [x] `[must]` `GWR2-14` Add typed `InteractionResponse` records binding actor,
  values/source message, presentation, target, generations, validation,
  correction, and consumed command/rejection. Responses are append-only,
  payload-bound idempotent, and corrections require an explicit prior ref.
- [x] `[must]` `GWR2-15` Add versioned effective transport + client + surface
  capability profiles with feature limits, locale/accessibility, secure input,
  progress/update, handoff, acknowledgement, and freshness metadata.
- [x] `[must]` `GWR2-16` Implement deterministic capability/policy negotiation
  that produces an auditable InteractionPresentation, preserves every required
  command/risk/confirmation, and otherwise returns a typed fallback or
  `unsupported` wait reason. Sensitive input never degrades to plain text and
  action limits cannot silently drop commands.
- [x] `[should]` `GWR2-17` Add presentation conformance fixtures for Web,
  Telegram, text-only, reconnect/profile change, secure handoff, and a client
  whose capability limits cannot represent the requested interaction. The
  local suite covers native buttons, numbered fallback, action limits,
  sensitive rejection, and profile-version renegotiation.
- [x] `[must]` `GWR2-18` Bind every presentable mutating command to a registered
  effect/activity executor and expose `executor_unavailable` from the same
  explanation resolver when it cannot be started or durably queued. The first
  Builder chat migration fails closed by filtering unadapted Codex, Trial, and
  Publication controls; the shared guard is now in
  `description_with_executor_readiness` and
  `interaction_from_workflow_description`: allowed mutating commands without a
  ready executor are moved to `executor_unavailable` blockers, and any raw
  workflow description that tries to present such a command fails closed before
  Interaction/Presentation creation.

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

- [x] `[must]` `GWR3-01` Add `adaos.intent.proposal.v1` with source message,
  semantic acts, alternatives, allowed-command snapshot, model identity, and
  disposition. The ABI and durable conversation-store record now preserve the
  complete proposal rather than only a selected intent label.
- [x] `[must]` `GWR3-02` Resolve a deterministic action token without NLU and
  route free text against its explicit pending interaction first. Opaque Web
  and Telegram tokens retain the direct InteractionResponse path; only free
  text enters `intent_mediation`.
- [x] `[must]` `GWR3-03` Require clarification when more than one pending target
  or command context fits a free-text answer. Ambiguous candidates are stored
  and no Interaction generation changes.
- [x] `[must]` `GWR3-04` Support multi-act utterances instead of forcing every
  message into one intent. Newline/semicolon-separated acts retain their own
  typed classifications while at most one governed response is committable.
- [x] `[must]` `GWR3-05` Keep new Issue/change feedback, read-only questions,
  context selection, and workflow commands distinct. The proposal ABI carries
  each as a separate semantic-act kind.
- [x] `[must]` `GWR3-06` Reject proposed commands absent from `allowed_actions`
  or lacking required typed arguments. Commit re-reads the current Interaction,
  verifies action identity and generation, and uses the action's typed value.
- [x] `[must]` `GWR3-07` Define risk policy for bounded free-text confirmation
  versus an explicit protected interaction. Read/local reversible actions may
  use deterministic text; confirmation-required, publication, external,
  privileged, irreversible, and destructive actions require an explicit token.
- [x] `[must]` `GWR3-08` Persist interpretation, clarification, correction, and
  committed result with privacy and retention controls. Corrections supersede
  rather than overwrite and UTF-8 source text is stored unchanged.
- [x] `[should]` `GWR3-09` Build Russian and English offline evaluation from
  real corrected cases, including UTF-8 transport coverage. A versioned local
  fixture covers ordinal answers, Issue intake, questions, and Cyrillic text.
- [x] `[should]` `GWR3-10` Measure false-transition and clarification rates,
  not model confidence alone. Conversation metrics expose committed,
  clarified, and corrected proposal counts and rates.
- [x] `[could]` `GWR3-11` Parse common short answers and ordinal choices
  deterministically before invoking a model. Russian/English ordinals,
  numeric choices, and bounded yes/no forms use the deterministic mediator.
- [x] `[deferred]` `GWR3-12` Defer autonomous workflow induction from arbitrary
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

- [x] `[must]` `GWR4-01` Encode the normative Builder Change statechart and
  transition catalogue from
  [Builder Conversational Development](builder-conversational-development.md#builder-change-statechart),
  explicitly separating it from artifact lineage and task execution state.
  `builder.governed` compiles 15 states and the complete first compatibility
  transition catalogue through the shared compiler; Run and view state are
  not copied into the Change state enum.
- [x] `[must]` `GWR4-02` Key the workflow instance by canonical `change_id` and
  retain exact project, base release, artifact, and command-context refs.
  The workflow instance id, Project Change summary, context packet, and shared
  Interaction binding retain those identities without deriving them from the
  last selected Process node.
- [x] `[must]` `GWR4-03` Model Prototype -> Automation -> Publication as
  derivation and promotion, not three mutually independent mutable stages.
  The compatibility buckets remain readable, but the canonical state and
  Process projection bind Automation to its source Prototype and Trial /
  Publication beneath that Automation lineage.
- [x] `[must]` `GWR4-04` Define direct Automation, prototype-first, revise,
  cancel, failure, retry-as-new-Run, Trial reject/accept, and Publication paths.
- [x] `[must]` `GWR4-05` Define invariants: one focused Change per command
  context but multiple open Changes per project; one admitted overlapping
  mutation per base generation; immutable accepted revisions; exact source
  Prototype for Automation; and exact candidate digest for Publication.
  Project coordination enforces scoped focus, project/artifact generations,
  fail-safe unknown scope, active overlap exclusion, and explicit verified
  rebase; the retained Builder lineage continues to bind source Prototype and
  candidate digests.
- [x] `[must]` `GWR4-06` Define LLM, Codex, validation, Git checkpoint, Trial,
  Publication, and notification as registered effects/activities rather than
  implicit phase code. The definition declares activity, retry,
  reconciliation, compensation, outbox, and asynchronous-reply policy; the
  legacy executor is currently its bounded activity adapter.
- [x] `[must]` `GWR4-07` Generate Lifecycle hierarchy, process summary,
  available controls, conversation focus, and Preview target from the same
  snapshot and lineage refs. Compatibility `builder.*` actions now carry the
  exact admitted workflow command and canonical generation, while the shared
  ConversationInteraction projection consumes the same `explain()` result.
- [x] `[must]` `GWR4-08` Keep Lifecycle selection and Preview selection as view
  context; neither changes the workflow without an explicit command. Tests
  prove an inspection/Preview update leaves canonical Change state and
  generation unchanged.
- [x] `[must]` `GWR4-09` Map current Builder JSON, sessions, Pending Actions,
  and UI handlers to canonical concepts with retain/adapt/retire disposition.
  The Builder architecture migration table names each legacy surface, its
  canonical owner, bounded adapter, and retirement condition.
- [x] `[should]` `GWR4-10` Add a compact workflow explanation to the chat so a
  user can ask what is happening, why, and what can happen next. Builder now
  derives one channel-neutral state/reason/next-command explanation from the
  canonical snapshot and uses it as the Interaction Frame prompt.
- [ ] `[could]` `GWR4-11` Add a generated graph/timeline inspector as a rich
  detail view, not the primary control surface.
- [x] `[deferred]` `GWR4-12` Defer simultaneous multi-user approval and artifact
  merging to GWR8.
- [x] `[must]` `GWR4-13` Implement scoped Change focus and write-conflict keys;
  switching focus is view/command context and never a business transition.
- [x] `[must]` `GWR4-14` Distinguish `iteration`, `experiment`, `evaluation`, and
  `recovery` Runs; require explicit reviewed adoption before an Experiment can
  advance the accepted Revision line. Run ABI and ledger now retain purpose
  and adoption status; experimental revisions stay off the Prototype head
  until confirmed adoption and can be discarded without changing active or
  published lineage.
- [x] `[must]` `GWR4-15` Define typed `mock`, `fixture`, `sandbox`,
  `live_readonly`, and `live` binding profiles, Prototype isolation policy,
  implementation mappings, and visible Preview data mode. Profile switching
  is generation-guarded, leaves UI Revision unchanged, requires confirmation
  for sandbox/live-readonly, forbids live mutation in Prototype, and blocks
  Automation handoff for declared missing mappings.
- [x] `[must]` `GWR4-16` Complete the Review lifecycle with submit, withdraw,
  dismiss, convert-to-Issue, accept-as-constraint, supersede, and resolve
  commands; hard deletion remains limited to unsent local drafts. Withdrawn
  records retain a minimal tombstone but leave future model context; accepted
  constraints require an explicit reason plus replacement or waiver to be
  superseded.
- [x] `[must]` `GWR4-17` Define context-facet requirements and a packet coverage
  report that fails before LLM/Codex submission when target structure, ABI,
  constraints, data policy, or execution authority is missing or ambiguous.
  Automation now enforces the declared facet set; spatial work is resolved by
  stable semantic refs with parent/sibling/order/responsive fragments and
  fails before executor submission when a target is missing or ambiguous.
- [x] `[must]` `GWR4-18` Publish `adaos.builder.project.v1` as a portfolio and
  coordination aggregate with source/stable/installed/DEV/candidate refs,
  project policy, component boundary, open Changes, conflict/dependency index,
  scoped focus, workflow versions, and archive state. The aggregate contains
  reference-only Change summaries while a bounded compatibility portfolio
  preserves old single-Change projections during migration.
- [x] `[must]` `GWR4-19` Derive project summary and commands from its linked
  planes; never infer one global project stage from the focused Change or most
  recent Run. The summary reports portfolio counts, active/unknown mutations,
  conflict/stale sets, scoped focus, and Project commands without a synthetic
  project lifecycle stage.
- [x] `[must]` `GWR4-20` Move the canonical Builder Change definition from
  Python construction in `builder.governed` into the owning `builder_skill`
  package's strict `workflow.json`, referenced from `skill.yaml`. Keep Python
  as the generic loader/compiler/cache plus registered guard/effect/activity
  adapter registry and bounded compatibility projection; the Builder scenario
  must not contain a duplicate Change definition.
- [x] `[must]` `GWR4-21` Retire direct dependence on legacy
  `scenario.yaml.workflow.states.actions.next_state` for governed workflows.
  Simple scenario workflows may remain as compatibility projections or be
  translated into `adaos.workflow.definition.v1`, but any mutating governed
  process must use the shared compiler/resolver and registered adapters.
- [x] `[must]` `GWR4-22` Implement a deterministic legacy-to-governed translator
  and inventory every Builder and scenario reader/writer. During migration run
  one write authority and shadow-compare compiled state, commands, targets,
  explanations, and role projections; divergence blocks cutover rather than
  repairing either source silently.
  `scenario.workflow_translation` translates legacy inline state/action data,
  inventories legacy/governed/no-workflow manifests, and shadow-compares state
  and edge projections. `ScenarioWorkflowRuntime` rejects legacy `next_state`
  mutation for manifest-bound governed workflows.
- [ ] `[must]` `GWR4-23` Cut Builder over by immutable candidate package and
  feature-gated WorkspaceLock activation. Preserve the prior complete package,
  workflow binding, and runtime generation as the rollback target; remove the
  Python transition table only after restart, rollback, and in-flight instance
  migration evidence passes.
- [ ] `[must]` `GWR4-24` Update Builder templates, context packets, Specification,
  artifact inspection, and publication evidence so an LLM can create or repair
  one `workflow.json`, see its structured validation report and graph diff, and
  cannot publish code/definition or role-policy mismatches. The 2026-08-01
  worker slice now preserves the exact governed context packet in task evidence,
  places its bounded Issue/acceptance/facet projection in Codex `task.md`, adds
  project workflow inspection to context facets, and compiles every
  manifest-bound definition before accepting worker output. Context packets now
  carry stable workflow authoring ABI digests, the registered adapter catalogue,
  default fail-closed role policy, validation `graph_diff`, and a static review
  summary derived from `adaos.workflow.static_report.v1`; Codex task projection
  retains that facet through the governed context packet. The shared
  `compile_conversational_package` pipeline now runs from Builder context
  packets, skill validation, scenario validation, and the developer SDK; context
  facets expose conversational package digest, validation diagnostics, bounded
  story summaries, and static workflow-story coverage. Publication-lock proof
  keeps this item open.
- [x] `[must]` `GWR4-25` Route workflow-definition corrections to the isolated
  Automation/Codex lane while keeping visual process-layout requests in
  Prototype and process inspection read-only. DEV Builder regression tests
  cover Russian/English `workflow.json`/statechart corrections and a visual
  process-panel counterexample; the Prototype LLM has no workflow-definition
  write path.
- [x] `[must]` `GWR4-26` Keep one-shot Preview materialization within the Yjs
  owner thread and detach all nested document values before bounded CPU work.
  The real `select_preview_target` CLI path now exits cleanly after restoring
  `proto: test04_recipes · UI 003`; persistent runtime materialization keeps
  its bounded executor, and ordinary DEV tools keep their timeout contract.

## GWR5. Cross-Channel and End-to-End Consistency Proof

**Outcome:** the Builder model remains consistent when driven through Web,
Telegram, free text, deterministic controls, background results, and direct SDK
tests.

**Admission gate:** the GWR4 definition is compiled and projected through GWR2
and GWR3.

**Exit proof:** one empty representative scenario completes the full flow on
this development machine; every channel observes the same state and actions;
lineage, evidence, and final Publication agree without direct state repair.

- [x] `[must]` `GWR5-01` Run request -> Issues/Change -> Prototype or direct
  Automation -> verification -> Trial -> Publication through the canonical
  resolver. `test_builder_governed_e2e.py` takes a fresh empty scenario through
  all dependent gates and ends in the canonical `published` state.
- [ ] `[must]` `GWR5-02` Prove Web buttons, Telegram options, informal replies,
  and SDK commands invoke identical command identities and guards. All three
  presentations retain the same semantic actions; token and intent responses
  enter `invoke_interaction_response` through one compatibility adapter. The
  deterministic control path is locally covered; repeat it through the live
  Web client and Telegram bot for each executor-backed mutating class. The
  2026-07-31 live read-only control establishes the Telegram baseline; it does
  not replace the required mutating command/guard matrix.
- [x] `[must]` `GWR5-03` Prove every UI action shown by `explain()` succeeds or
  returns a typed concurrency/policy change, never an unrelated handler rule.
  Generated resolver conformance plus the Builder ingress tests bind displayed
  `workflow_command` and generation back to the canonical resolver. The live
  defect showed that transition admission alone is insufficient when an
  external activity executor is absent; `tests/test_workflow_execution.py`,
  `tests/test_conversation_interactions.py`, and
  `tests/test_governed_workflow_artifact_e2e.py` now cover executor readiness,
  negative `executor_unavailable` projection, and canonical interaction/SDK
  invocation convergence.
- [x] `[must]` `GWR5-04` Prove blocked commands expose the same reason code and
  semantically equivalent explanation across channels. Capability negotiation
  consumes one semantic Interaction and preserves resolver explanation facts;
  unsupported capabilities remain a typed unsupported presentation.
- [x] `[must]` `GWR5-05` Bind review and Publication to exact immutable target
  digests and reject stale Lifecycle/chat actions. Trial decision and
  Publication now require the candidate package digest; action target refs
  include that digest and stale generations/digests fail before persistence.
- [x] `[must]` `GWR5-06` Prove a background Codex/LLM result advances only the
  originating Change and cannot inherit another Webspace's view context.
  Background metadata carries `originating_change_id`; a scoped portfolio
  update preserves the currently inspected Change.
- [x] `[must]` `GWR5-07` Prove Lifecycle nodes, process status, conversation
  focus, and proto:/active:/public: Preview labels remain mutually consistent.
  The E2E proof asserts the exact parent chain and all three preview prefixes.
- [x] `[must]` `GWR5-08` Record transition coverage, artifact lineage, tests,
  Trial, Git, Publication, and delivery evidence for the representative run.
  Canonical history and Run evidence retain review, Automation test, Git
  checkpoint, Trial, and registry-publication refs. The post-fix combined
  acceptance gate passes 219 tests; exact Automation task identity now closes
  one Run instead of leaving a synthetic start Run active.
- [x] `[must]` `GWR5-09` Update the Builder roadmap with the accepted semantic
  proof without copying this checklist. Phase 11 evidence points to the
  governed E2E suite and commit `7717319d`.
- [x] `[should]` `GWR5-10` Measure time to understand current state, action
  mismatch defects, clarification rate, and diagnosis effort versus the old
  Builder path. `adaos.workflow.metrics_report.v1` records current/legacy
  cycle-time probes, signed deltas, clarification/repair rates, action
  mismatch defects, repeated corrections, and presentation fallback rates.
- [ ] `[could]` `GWR5-11` Add mutation testing that deliberately removes guards
  or projections and proves conformance tests fail.
- [x] `[deferred]` `GWR5-12` Do not block the semantic proof on choosing or
  integrating an external durable engine.
- [x] `[must]` `GWR5-13` Prove two open Changes can be inspected independently,
  focus changes no business state, non-overlapping work is admitted, and
  overlapping stale writes fail with an explicit rebase/split/supersede choice.
- [x] `[must]` `GWR5-14` Prove an Experiment can be compared and discarded
  without changing `active:` or Publication, and only an explicit
  `adopt_experiment` transition can promote its Revision.
- [x] `[must]` `GWR5-15` Prove Prototype defaults to mock/fixture, switching a
  compatible Preview binding profile does not rewrite the UI Revision, and
  undeclared live reads/writes fail closed.
- [x] `[must]` `GWR5-16` Prove a withdrawn Review disappears from active model
  context without losing its audit tombstone, while an accepted constraint can
  only be superseded with a reason.
- [x] `[must]` `GWR5-17` Prove a spatial UI request receives parent/sibling/order,
  responsive, ABI, data-binding, and active-constraint facets or stops for
  clarification before the model is called.
- [x] `[should]` `GWR5-18` Record definition complexity and context-sufficiency
  metrics alongside cycle time, clarification, repeated-correction, and action
  mismatch rates. `workflow_metrics_report` derives complexity from the
  compiled definition, context sufficiency from the governed context packet
  coverage, and story outcomes from conversation-story reports under the
  `adaos.workflow.metrics_report.v1` ABI.
- [x] `[must]` `GWR5-19` Prove one Interaction preserves command identity,
  risk, confirmation, and target when negotiated as a Web form, Telegram
  choices, numbered text, or a cross-channel deep-link handoff.
- [x] `[must]` `GWR5-20` Prove an unsupported required capability leaves the
  workflow waiting with an explanation rather than hiding controls or
  weakening confirmation.
- [x] `[must]` `GWR5-21` Prove several pending interactions are independently
  addressable and an unbound free-text answer changes no state until the target
  is clarified.
- [x] `[must]` `GWR5-22` Prove the Project aggregate reports two independent
  Changes concurrently while detecting an indirect conflict through a shared
  skill/component dependency. The Project computes transitive `requires` and
  `derives` footprints and reports `component_dependency` separately from a
  direct affected-ref collision.
- [x] `[should]` `GWR5-23` Prove one multi-component Change joins exact scenario
  and skill Runs into one dependency-locked candidate and reports partial
  success without partial promotion. The reference composition proof requires
  both scenario and skill child results, aggregates successful evidence, and
  reports one-child failure as non-promotable `partial_failed`.
- [x] `[must]` `GWR5-24` Prove manifest/file cardinality and strict JSON
  admission: missing referenced file, unreferenced workflow, wrong filename,
  duplicate keys, unsupported schema, multiple definitions, or exceeded limits
  fail before package visibility.
- [x] `[must]` `GWR5-25` Prove registry trust and role policy: unknown/mutable or
  permission-broadening adapters fail compilation; `guest` and `registered`
  receive different allowed controls from one snapshot where declared, and a
  forged role or direct unauthorized command still fails at commit.
- [x] `[must]` `GWR5-26` Prove package atomicity: code-only, definition-only, and
  adapter-contract changes create new package/release/binding digests; a mixed
  old/new set cannot activate; injected failure leaves the prior complete
  WorkspaceLock/runtime generation authoritative.
- [x] `[must]` `GWR5-27` Prove LLM authoring convergence with one valid proposal
  and representative invalid proposals. Structured diagnostics permit bounded
  repair of structural errors while authority, risk, policy, and validation
  gates remain unchanged and every attempt retains provenance.
- [x] `[must]` `GWR5-28` Prove definition upgrade and rollback with an in-flight
  instance: pin compatible old code or apply an explicit migration, reject
  silent reinterpretation, and restore the prior complete package/binding
  without repeating an external effect.
- [x] `[must]` `GWR5-29` Migrate one small non-Builder skill or scenario through
  the same manifest, ABI, role, package, activation, explanation, and rollback
  contracts to demonstrate that the model is not Builder-specific.
- [x] `[must]` `GWR5-30` Define and run the first workflow-facing conversation
  stories as executable conversation -> workflow -> output paths. The first
  contract slice validates deterministic `IntentProposal` fixtures, admitted or
  rejected `WorkflowCommand`, state generation, and semantic output without
  relying on exact chat prose.
- [x] `[must]` `GWR5-31` Keep story execution side-effect isolated by default:
  mutating effects use registered mock activities, provider/model calls use
  fixtures unless an explicit integration-trial profile admits them, and every
  live-effect story records its risk and environment. First runner records
  activities as mocked timeline entries and makes no provider calls.
- [x] `[must]` `GWR5-32` Add the pure conversational runtime ABI bridge that
  connects proposed workflow acts to canonical workflow invocation and workflow
  execution results to semantic `ConversationOutput` plus `ResponseEnvelope`
  refs, without durable store writes, provider calls, or hidden workflow
  dispatch.

Checked local evidence for the workflow proof slice:
`tests/test_workflow_registry.py`, `tests/test_governed_workflow.py`,
`tests/test_workflow_authoring.py`, `tests/test_artifact_package_store.py`,
`tests/test_artifact_workspace_activation.py`,
`tests/test_governed_workflow_artifact_e2e.py`,
`tests/test_scenario_workflow_translation.py`,
`tests/test_conversational_runtime.py`,
`tests/test_workflow_static_reports.py`, and
`tests/test_scenario_workflow_runtime.py`. The suite covers immutable adapter
contracts, role-claim derivation and forged-role rejection, package workflow
locks and binding digests, failed-health rollback to the prior WorkspaceLock,
authoring invalid-to-valid convergence with persisted provenance, explicit
definition migration with package/binding pins, non-Builder scenario activation
and rollback, legacy inline workflow translation/shadow comparison, and static
workflow/conversational review reports with statechart, conformance cases,
story summaries, and state/command/transition/output coverage.
- [x] `[should]` `GWR5-33` Generate static statechart, story, and coverage
  reports from admitted workflow/conversational package sources for human
  review, Builder context packets, and trial evidence. The first report ABI is
  `adaos.workflow.static_report.v1`; `workflow_static_report` and
  `conversational_package_static_report` generate non-authoritative statechart,
  definition review, conformance, story summary, and coverage sections from
  compiled workflow and package sources while omitting exact chat prose.
- [x] `[should]` `GWR5-34` Preserve trace identity across turn, proposal,
  dialog frame, interaction, response, command, workflow event, Run/activity,
  semantic output, and delivery attempt. The first proof ABI is
  `adaos.workflow.trace_identity.v1`; `workflow_trace_identity_report` links
  `IntentProposal`, canonical `WorkflowInvocation`, workflow decision event,
  semantic `ConversationOutput`, `ResponseEnvelope`, and delivery attempt IDs
  and fails the report on command, workflow identity, event, envelope, or reply
  route mismatches.
- [x] `[must]` `GWR5-35` Extend story assertions to full dialog repair,
  `ConversationInteraction`, and channel fallback coverage once the workflow
  contract slice is stable. `run_conversation_story` now accepts
  `expect.repair`, `expect.interaction`, and `expect.presentation`, projects
  store-free `ConversationInteraction`/`InteractionPresentation` records, and
  fails the story on command identity, generation, presentation mode, reason,
  semantic-equivalence, or repair next-input mismatches.
- [x] `[deferred]` `GWR5-36` Defer an interactive workflow/conversation studio
  until static graph/story exports, runner evidence, and package admission are
  stable.

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

- [x] `[must]` `GWR6-01` Inventory waits, timers, callbacks, long activities,
  retries, cancellation, unknown outcomes, and reply delivery in the accepted
  Builder definition. The reference-persistence decision records the exact
  states, four modifying activities, timeout/heartbeat, no-retry, cancellation,
  reconciliation, human response, and reply-delivery contracts.
- [x] `[must]` `GWR6-02` Define the minimum persistence contract: snapshot
  generation, transition journal, idempotent inbox, outbox, activity attempts,
  pending interactions, and ReplyRoute. The contract and table ownership are
  documented in the accepted persistence decision.
- [x] `[must]` `GWR6-03` Implement only the missing reference SQLite semantics,
  reusing current AdaOS stores rather than creating another per-skill state
  system. Workflow and reply tables use the shared AdaOS SQLite provider.
- [x] `[must]` `GWR6-04` Inject crashes before and after every modifying effect
  and distinguish safe retry from `outcome_unknown` reconciliation. The matrix
  covers Codex, Prototype derivation, Trial, and Publication at both boundaries.
- [x] `[must]` `GWR6-05` Cover delayed human input, process restart,
  cancellation, definition upgrade, backup/restore, and offline Root. Local
  tests retain an undeliverable terminal result when Root has no usable route.
- [x] `[must]` `GWR6-06` Add commit-time checks for permission, target digest,
  approval witness, current generation, and effect binding.
- [x] `[must]` `GWR6-07` Record resource use, recovery complexity, defect rate,
  and operator repair cost for the reference path. The decision records eight
  shared-database tables, bounded write shape, two recovery branches, zero
  unsafe repeats in eight crash cases, and the one-step reconciliation cost.
- [x] `[must]` `GWR6-08` Write an ADR: reference persistence sufficient, or
  external durable adapter evaluation admitted with named unmet requirements.
  The accepted decision keeps SQLite and postpones external adapters behind
  five measurable admission conditions.
- [x] `[should]` `GWR6-09` Add bounded retention, compaction, redacted
  diagnostics, and operator describe/recover/cancel surfaces. Delivered outbox
  compaction retains the canonical journal; operator views expose only redacted
  state, metrics, and safe recovery classification.
- [x] `[could]` `GWR6-10` Use JetStream for a transport/outbox experiment only
  if delivery durability is one of the measured gaps. It is not admitted: the
  reference delivery fault suite exposed no such unresolved gap.
- [x] `[deferred]` `GWR6-11` Defer distributed consensus and active-active local
  workflow execution.
- [x] `[must]` `GWR6-12` Implement channel-neutral ResponseEnvelopes for
  accepted, progress, input-required, terminal, and notification messages with
  workflow/task correlation, monotonic sequence, sensitivity, and coalesce key.
- [x] `[must]` `GWR6-13` Persist ReplyRoutes, an outbound envelope outbox, and
  idempotent per-presentation/transport DeliveryAttempts; redelivery must never
  reinvoke the originating command or activity.
- [x] `[must]` `GWR6-14` Recover pending interactions, terminal envelopes, and
  delivery attempts after restart; preserve a queryable terminal result when
  every route expires or is undeliverable.
- [x] `[should]` `GWR6-15` Add ordered progress, update coalescing, attention
  policy, quiet periods/preferences, alternate authorized routes, delivery
  receipts, and operator inspection without coupling delivery to business
  completion. `adaos.conversation.attention_policy.v1` and
  `attention_plan.v1` classify append/update/evidence/projection behavior,
  coalesce progress, retain quiet-hours/channel preferences, and escalate
  input-required/failure/expiry. ReplyRoute and DeliveryAttempt keep alternate
  authorized delivery and acknowledgement independent from terminal outcome;
  local protocol tests cover terminal-once, retry, acknowledgement, and
  restart-safe materialization.
- [ ] `[must]` `GWR6-16` Establish an end-to-end Telegram ingress acceptance
  boundary. The public webhook may acknowledge an update only after the target
  zone has durably accepted it for the addressed hub. A successful core NATS
  publish is not that receipt because an offline hub-root subscriber loses the
  event. Persist an idempotent per-hub inbox keyed by Telegram `update_id`,
  redeliver after reconnect, expose pending/terminal ingress status, and keep
  root-to-zone retry distinct from zone-to-hub delivery. The 2026-07-31 outage
  proved both failure classes: root relay timeout now returns retryable `503`,
  and bounded hub-root cleanup prevents an indefinitely trapped reconnect; the
  durable zone-to-hub receipt remains open. The deployment-side admission gap
  is closed: webhook authentication and Telegram registration now converge in
  the same deploy, only the domain owning `TG_WEBHOOK_BASE` may register, bot
  route IDs are canonicalized, and `drop_pending_updates=false` is mandatory.
  Infra run `30657956114` exposed the mixed-case production bot-ID edge; run
  `30658617393` then proved canonical owner registration and non-owner skip
  with the new regression case. This prevents configuration-induced loss
  but does not replace the durable per-hub inbox required by this item. A
  signed UTF-8 fixture then traversed public root, RU relay, local hub, Builder,
  and Telegram outbound under request `telegram:adaos_home_bot:<chat>:990055`;
  the durable conversation retained exact Russian code points and the response
  carried all five canonical actions.

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

- [x] `[deferred]` `GWR7-01` Implement no external adapter until GWR6 admits a
  measured need. The 2026-07-30 decision admitted none.
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
- [x] `[must]` `GWR7-08` Record an adoption/postponement/rejection ADR with
  measured benefit, operational cost, migration, and exit plan. The reference
  persistence decision records postponement and provider-specific admission
  conditions without selecting an implementation.
- [x] `[deferred]` `GWR7-09` Do not maintain several production providers in
  parallel without separate proven deployment classes.

## GWR8. Root, Multi-User, and Federation Extensions

**Outcome:** only after the single-user plane is stable, workflow instances may
coordinate multiple principals, nodes, responsibility zones, and reusable
change proposals.

**Admission gate:** GWR6 is accepted for bounded single-user work; GWR7 is
complete only if an external adapter was admitted; and a concrete multi-user
use case supplies authority, privacy, conflict, and availability requirements.

- [ ] `[deferred]` `GWR8-01` Extend the GWR1 `guest`/`registered` bootstrap with
  domain/zone-specific roles, approvals, delegation, quorum, revocation, and
  cross-user audit.
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
34. **Single workflow artifact:** a manifest may omit workflow or reference the
    one root `workflow.json`; missing, additional, inline governed, multi-file,
    and role-variant definitions are rejected.
35. **Canonical definition:** equivalent JSON formatting and object-key order
    produce one semantic digest, while a semantic transition or policy change
    produces a new digest and component package.
36. **Registry trust:** unknown, mutable-name-only, schema-incompatible, or
    authority-broadening registered code cannot compile or dispatch.
37. **Role projection:** one snapshot yields the policy-correct guest and
    registered affordances; a forged role or hidden direct command cannot cross
    the same commit-time guard.
38. **Atomic package activation:** code, `workflow.json`, adapter contracts,
    ProjectRelease binding, and WorkspaceLock move as one candidate; no fault
    leaves a mixed runtime generation.
39. **LLM repair:** a malformed or incomplete proposal receives structured
    diagnostics, can be repaired without changing policy ceilings, and remains
    inactive until the exact repaired digest passes every gate.
40. **Legacy shadow cutover:** translated `scenario.yaml.workflow` and the
    governed definition agree for representative states/actions before the
    legacy reader is disabled; divergence blocks migration.
41. **Pinned upgrade:** an in-flight instance remains on its admitted package
    and binding or follows an explicit migration; update and rollback never
    reinterpret history or repeat an uncertain effect.
42. **Second-domain proof:** one non-Builder component is authored, packaged,
    activated, exercised as guest/registered, updated, and rolled back through
    the same shared contracts.

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
- every governed skill/scenario uses zero or one manifest-bound
  `workflow.json`; no inline, role-specific, or separately activated definition
  remains authoritative;
- package, ProjectRelease, WorkspaceLock, runtime instance, registry contracts,
  and validation/admission evidence agree on exact definition and binding
  digests, and code/definition update or rollback is atomic;
- LLM-assisted workflow authoring produces data definitions that can be
  rejected or activated by the same deterministic validation gates as
  hand-authored definitions;
- Web and Telegram pass the same semantic interaction cases;
- NLU cannot bypass allowed transitions or policy;
- restart, duplicate, stale authority, cancellation, unknown outcome,
  upgrade, and backup/restore cases have repeatable evidence;
- external provider selection is not required for semantic completion and, if
  adopted, remains below the AdaOS workflow contract;
- remaining GWR8 items are explicitly admitted or remain deferred without
  blocking the bounded single-user architecture.
