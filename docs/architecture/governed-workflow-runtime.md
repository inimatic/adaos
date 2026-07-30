# Explainable Workflow Model and Interaction Architecture

Status: target architecture and system boundary.

Last reviewed: 2026-07-30.

This document defines the AdaOS-wide model for explainable and validatable
states, transitions, guards, effects, interactions, and natural-language
input. Its primary purpose is to prevent each skill, channel, and UI from
inventing an incompatible workflow. Persistence and durable execution support
that model but do not define it. The implementation sequence is owned by the
[Explainable Workflow Model Roadmap](governed-workflow-runtime-roadmap.md).

Domain documents continue to own their business vocabulary and state
transitions. In particular:

- [Governed Evolution](governed-evolution.md) owns the long-term human signal
  to verified capability loop;
- [Builder](builder.md) and
  [Builder Conversational Development](builder-conversational-development.md)
  own the development domain;
- [Conversation and Channel Architecture](conversation-and-channel-architecture.md)
  owns conversations, identities, and transport-independent threads;
- [Pending Actions](pending-actions.md) owns the current compatibility plane
  for explicit human decisions;
- [Operational Event Model](operational-event-model.md) owns runtime event and
  projection semantics.

This document owns the shared workflow metamodel, definition compiler,
transition resolver, explanation and projection rules, interaction and intent
mediation contracts, and only then the optional execution/recovery boundary
used by those domains.

## Decision

AdaOS will describe every governed process through one bounded workflow model:

```text
natural conversation and deterministic controls
  -> typed interpretation or command
  -> statechart + context + invariants
  -> guard and policy evaluation
  -> named transition
  -> registered effect or activity
  -> canonical state, evidence, and derived interactions/projections
```

The model or NLU layer may interpret, propose, summarize, plan, and produce
artifacts. It does not commit a state transition directly. The deterministic
kernel validates the target, current generation, workflow invariant, policy,
authority, and transition before an effect is admitted.

The same definition is the source for command admission, `allowed_actions`,
human explanation, Web/Telegram controls, tests, and workflow visualization.
No UI or skill maintains a second handwritten table of legal transitions.

A persistence layer or durable-execution product may later host long-running
instances. It is an implementation adapter below the model. DBOS, Temporal,
Restate, SQLite, NATS, and other infrastructure are not the architectural goal
and are not selected by this document.

The first implementation must be a bounded domain runtime, not a universal
BPMN system, a generic user-programmable workflow DSL, or global event sourcing
for every AdaOS service.

## Primary Problem

The problem is semantic fragmentation, not the absence of a workflow engine.

Today the meaning of a process may be spread across a scenario tree, skill
handlers, buttons, conversation prompts, status strings, background tasks, and
stored JSON. Each representation can be individually reasonable while their
combined behavior is inconsistent. A button may be visible although its
command is invalid; a chat may suggest a transition the UI cannot perform; a
Lifecycle node may look like a state while actually being only navigation; a
background task may be called complete while required evidence is missing.

AdaOS needs one model that can answer, for any workflow instance:

1. What state is it in?
2. What facts and immutable targets make that state true?
3. Which commands are legal now?
4. Why is each other command unavailable?
5. What guard, policy, effect, evidence, and resulting state belong to a legal
   transition?
6. What should Web, Telegram, NLU, an operator, and a test observe from that
   same answer?

If the model cannot answer those questions without reading UI code or an LLM
transcript, it is incomplete.

## Existing Reliability Symptoms

AdaOS already has stateful behavior in Builder, NLU Teacher, pending actions,
scenario workflows, package activation, core updates, conversations, and
background jobs. These paths currently use several combinations of JSON
state, SQLite records, local tasks, event callbacks, retries, and UI-specific
actions.

That fragmentation creates recurring failure classes:

- a background task finishes but its originating channel receives no result;
- a process remains in a preparing or waiting state after a restart;
- a transport error causes an uncertain modifying command to be repeated;
- a stale button or reply acts on a different revision or project;
- the UI, task record, and artifact state disagree about completion;
- Web and Telegram expose different actions for the same business state;
- a model treats conversational context as authority;
- an implementation reports success without durable verification evidence.

The target model removes the semantic inconsistencies first. Shared durability
then prevents a correctly modelled process from being lost or repeated during
execution.

## Goals

The architecture must:

1. provide one declarative and typed vocabulary for states, commands,
   transitions, guards, invariants, effects, evidence, and projections;
2. make the current state, reason, target, and allowed next actions explainable;
3. validate every transition independently from its Web, Telegram, CLI, MCP,
   or model origin;
4. derive Web/Telegram actions, Lifecycle and progress projections, and test
   expectations from the same model;
5. accept both deterministic controls and bounded natural-language answers;
6. statically detect unreachable states, missing outcomes, invalid effects,
   unsafe transitions, and unexplainable waiting states;
7. separate business state, execution state, artifact lineage, and UI focus;
8. preserve long-running work across process and transport restarts where the
   domain requires it;
9. prevent silent duplication of modifying effects;
10. retain the originating principal, context, and reply route;
11. remain local-first and useful while Root is unreachable;
12. provide evidence and traces suitable for debugging, evaluation, and future
    multi-user governance.

## Non-Goals

The first model will not:

- formalize every conversational utterance as a workflow transition;
- allow arbitrary scripts inside declarative workflow definitions;
- replace Git, package storage, or domain databases with workflow history;
- use Yjs or browser projections as workflow truth;
- infer permission from an LLM decision or prior approval alone;
- guarantee exactly-once behavior for an external side effect that offers no
  idempotency or transactional boundary;
- require Temporal, DBOS, Restate, Dapr, Root, or NATS JetStream on every node;
- migrate live workflow instances between providers automatically;
- expose the internal state graph as the primary end-user interface;
- implement federated or marketplace workflows before the single-user path is
  stable.

## Success Criterion

The first success is not an external engine integration. It is a Builder
workflow definition that:

- is small enough for a person to inspect;
- is rejected at build/test time when internally inconsistent;
- returns the same current state and allowed commands to Web, Telegram, NLU,
  SDK callers, and tests;
- explains every admitted or rejected transition;
- keeps artifact lineage separate from process state;
- makes adding or changing a workflow transition a change to one canonical
  definition plus registered domain code, not several UI and handler patches.

Only after this semantic proof should AdaOS decide whether the existing
persistence is sufficient or a durable workflow product materially reduces
execution risk.

## A Bounded Graph, Not a General Graph Platform

The workflow is a constrained statechart rather than an arbitrary property
graph:

```text
node       = named business state
edge       = named command + source + target
guard      = pure predicate over typed context
effect     = registered state/evidence operation
projection = explanation and available interactions derived from snapshot
```

Loops are expected for revise/review processes. Hierarchical or parallel states
are allowed only when they reduce state explosion. Runtime values remain typed
context and refs; they do not dynamically create new state names or executable
edges.

This does not require a graph database. A validated definition plus a compact
instance snapshot and transition ledger is sufficient for the initial model.

## Related Models That Must Stay Separate

One large graph or state enum would create state explosion. AdaOS therefore
keeps four related models separate.

### Workflow Statechart

The statechart describes legal business transitions and may contain loops:

```text
draft -> ready -> implementation -> verification -> trial -> accepted
                  ^                       |
                  +-------- revise -------+
```

Nested or orthogonal regions may be used where they express independent
business facts. They must not be used to combine every operational status into
one state name.

### Artifact Lineage DAG

Artifact provenance is acyclic:

```text
Prototype revision
  -> Automation result derived from it
       -> Trial candidate
            -> immutable Release
```

Returning from Automation to Prototype creates a new revision. It does not
rewrite the historical edge or thaw an accepted artifact.

### Execution State

Execution describes a `Run` or activity attempt, not the business stage:

```text
created | queued | working | input_required | completed | failed
cancel_requested | cancelled | outcome_unknown
```

Retries create distinct attempts linked to the same logical activity. An
uncertain modifying result enters `outcome_unknown`; it is not replayed merely
because the caller timed out.

### View and Command Context

View context answers what one Webspace is displaying. Command context answers
which project, workflow, and revision a user/conversation is controlling. They
may differ and neither changes business state by selection alone.

The command context is bound to a principal and conversation/thread, carries a
generation, and fails closed when the target is missing or ambiguous.

## Workflow Metamodel

Every domain workflow is expressed with the same concepts.

### State

A state is a stable business condition with a human-readable meaning. It must
describe truth, not an action being attempted and not a screen currently open.
For example, `prototype_review_required` is a state;
`click_approve_button` and `lifecycle_panel_open` are not.

### Command

A command names an intention to change or query the workflow. It has typed
arguments, actor/context requirements, risk, and an idempotency contract.
Channels and NLU resolve input into the same command identity.

### Transition

A transition binds a command and source state to a target state. It references
guards, effects, evidence requirements, and explanation metadata. An internal
automatic transition is still named and observable; hidden state mutation is
not allowed.

### Guard and Invariant

A guard is a pure predicate deciding whether one transition is currently
available. An invariant must hold for every valid instance snapshot. Guards
and invariants return typed reason codes with localized explanation keys, not
only booleans.

### Effect and Activity

An effect updates domain state or records evidence through registered code. A
long-running or external effect is an activity with timeout, cancellation,
idempotency, and outcome rules. Definition files refer to stable identifiers;
they do not embed arbitrary executable code.

### Evidence and Gate

Evidence is a typed assertion about an immutable target: a review decision,
test result, preview digest, trial observation, permission decision, or release
record. A gate is a guard whose decision depends on required evidence. A state
such as `verified` must identify the evidence supporting it.

### Projection and Affordance

A projection converts the canonical snapshot into a channel-neutral
explanation: current state, progress, target, blockers, evidence, and available
commands. An affordance is a presentation of one available command as a Web
button, Telegram option, chat suggestion, CLI command, or rich view. It never
defines legality independently.

## Definition, Instance, and Projection Flow

```text
WorkflowDefinition + domain context
  -> validated WorkflowSnapshot
  -> explain(snapshot)
       -> state/reason/blockers/evidence
       -> allowed commands
       -> semantic interactions
  -> invoke(command, expected_generation)
       -> guards + policy + invariant check
       -> transition decision
       -> effect/activity request
       -> next snapshot + event
       -> explain(next snapshot)
```

`explain()` and `invoke()` use the same transition table and guards. The UI
must not infer an action from state labels, and the command handler must not
accept an action absent from the generated affordances unless policy marks it
as a non-interactive system command.

An illustrative definition fragment looks like this (the exact serialization
is not frozen yet):

```yaml
workflow_type: builder_change
initial: clarification
states:
  prototype_review:
    explanation: builder.prototype_review_required
  automation_ready:
    explanation: builder.automation_ready
transitions:
  - id: accept_prototype
    from: prototype_review
    command: builder.accept_prototype
    to: automation_ready
    guards:
      - builder.prototype_target_is_current
      - builder.review_is_authorized
    effects:
      - builder.record_prototype_acceptance
    evidence:
      - prototype_review
```

From this one edge the compiler/resolver must derive command admission, the
blocked reasons, a Web button or Telegram option when allowed, the chat
explanation, transition tests, and a graph edge for inspection. Those outputs
are projections; none may redefine the edge.

## Canonical Workflow Model Records

The exact schemas will be frozen during implementation. The following records
define the target responsibilities.

### WorkflowDefinition

`adaos.workflow.definition.v1` contains:

- stable workflow type and definition version;
- aggregate/domain type;
- initial, final, nested, and optional parallel states;
- typed commands and transitions;
- registered guard, activity, and compensation identifiers;
- risk and confirmation policies;
- required interaction and explanation templates;
- schema and migration policy.

Declarative definitions may reference registered code. They may not contain
arbitrary executable expressions, filesystem paths, shell commands, or model
prompts with authority to mutate state.

### WorkflowInstance

`adaos.workflow.instance.v1` contains:

- stable instance id and domain aggregate reference;
- workflow type and exact definition version;
- persistence/execution adapter reference when one is used;
- current state snapshot and monotonic generation;
- authority and tenancy scope;
- pending interaction, task, activity, and evidence references;
- lifecycle, timestamps, and terminal reason.

An adapter choice is an implementation property, not part of the domain state.
If more than one adapter exists, exactly one is recorded as execution authority
for an instance and the runtime must not silently switch after accepting a
command.

### WorkflowCommand

`adaos.workflow.command.v1` contains:

- command and target identity;
- typed arguments;
- actor/principal and origin channel;
- expected workflow and context generations;
- idempotency key;
- correlation, causation, and trace references;
- authority witness and requested risk class;
- durable reply route when a later result is expected.

### WorkflowEvent

`adaos.workflow.event.v1` is an immutable fact recording:

- event and instance identity plus monotonic sequence;
- accepted or rejected command reference;
- prior and resulting state/generation;
- actor, authority, correlation, and causation;
- guard/policy decision and reason;
- related task, artifact, and evidence references;
- workflow definition and runtime versions.

Events are sufficient to audit transitions. They are not a requirement to
event-source unrelated AdaOS domain data.

### ConversationInteraction

`adaos.conversation.interaction.v1` is a semantic request for user input. It
contains:

- message and interaction kind;
- workflow, task, target, revision, and generation bindings;
- typed options or requested form fields;
- risk, expiry, single-use, and confirmation policy;
- presentation hints and channel fallbacks;
- opaque action tokens rather than raw tool names and arguments;
- reply-route and status references.

Web may render buttons, selectors, review cards, search, or a rich view.
Telegram may render an inline keyboard, pagination, a deep link, or a compact
message. A text-only channel may render numbered choices. All are projections
of the same interaction.

### IntentProposal

`adaos.intent.proposal.v1` records a provisional interpretation of natural
language:

- source message and normalized locale;
- pending interaction and context snapshot used for interpretation;
- one or more semantic acts;
- candidate workflow commands and typed arguments;
- alternatives, unresolved references, and material ambiguity;
- allowed-command snapshot;
- model, prompt, tool, and schema identities;
- disposition: accepted, clarified, corrected, rejected, or comment-only.

An IntentProposal is evidence, not authority and not a committed transition.

### Task, Run, Evidence, and ReplyRoute

Long-running work uses a stable logical task with distinct attempts. Builder's
canonical `Run` remains the development-domain attempt and may reference a
system task rather than duplicating it.

Evidence records what was checked, against which immutable input and in which
environment. `completed` means execution ended; `verified` requires linked
evidence.

`ReplyRoute` retains the principal, conversation/thread, channel, Webspace or
command context, Telegram bot/chat/thread/reply ids when applicable, and the
initiating message. Delivery state distinguishes:

```text
materialized -> queued -> sent -> acknowledged
                         -> delivery_failed
```

A local ledger flag named `notified` must not imply transport delivery.

## Command and Transition Boundary

Every state-changing command follows the same logical boundary:

1. normalize and schema-validate the command;
2. load the exact instance and expected generation;
3. bind the current principal, context, target, and authority witness;
4. verify that the command is available in the current state;
5. execute pure guards and policy checks;
6. revalidate target digest, permission, approval, and eligibility at commit;
7. atomically record the accepted transition and required outbox work where
   the selected persistence boundary permits it;
8. return a fresh state and interaction frame;
9. execute external work as a registered activity;
10. append the activity outcome and evidence through an idempotent command.

Stale generations, expired tokens, reused single-use actions, ambiguous
targets, moved artifact bases, and revoked authority fail closed and return a
fresh explanation of the currently available actions.

Commit-time validation is distinct from an earlier approval. A valid approval
does not authorize a different effect, revision, principal, or later context
generation.

## NLU and Informal Human Responses

NLU is an adapter into the workflow protocol, not its owner.

Input resolution order is:

1. validate an opaque deterministic action token when one is present;
2. bind a reply to its explicit interaction/task reference;
3. when exactly one compatible pending interaction exists, interpret the text
   against only that interaction's response schema and available commands;
4. otherwise interpret the message as one or more open semantic acts;
5. ask for clarification when target or consequence is materially ambiguous.

Typical semantic acts include:

- answer a requested field or choice;
- invoke a workflow command;
- add or amend an Issue;
- add review feedback;
- select a command context;
- request information without changing state;
- start unrelated work;
- cancel, defer, or correct a prior interpretation.

One utterance may contain several acts. For example, "в целом хорошо, но
перенеси кнопку влево" carries positive feedback and a new revision request;
it must not be reduced to unconditional approval.

Model confidence alone is not an admission rule. The resolver instead checks
whether the target is unique, the command is currently allowed, required
arguments are complete, and policy permits a free-text confirmation at the
given risk. Publication, destructive operations, permission grants, secrets,
and other protected effects require an explicit review surface and current
effect summary.

The original utterance, proposed interpretation, correction, and committed
event are retained according to conversation privacy and retention policy.
This supports audit and future offline evaluation without treating raw chat as
the workflow database.

## Validation Model

Validation is a primary product capability, not a side effect of executing a
workflow.

### Structural Validation

Before activation, the definition compiler validates:

- schema and stable identifiers;
- initial state, reachability, explicit terminals, and deliberate loops;
- references to registered guards, effects, activities, compensations, and
  policies;
- deterministic ordering or explicit priority for competing transitions;
- required command parameters and result schemas;
- definition-version and migration policy.

### Semantic Validation

Domain conformance tests validate:

- invariants before and after every transition;
- legal and illegal commands for representative contexts;
- evidence and approval gates;
- confirmation rules for protected risk classes;
- success, failure, cancellation, timeout, and unknown-outcome handling;
- absence of transitions that silently broaden scope or authority;
- consistency between artifact lineage and claimed workflow state.

### Projection Validation

Every reachable snapshot must produce:

- a stable state and reason code;
- human-readable explanation and blockers;
- the same semantic `allowed_actions` for all channels;
- an interaction or explicit wait reason for every human-input state;
- a Preview/Lifecycle target only when a valid domain ref exists;
- no affordance for a command that the resolver would reject under the same
  snapshot and actor policy.

Generated transition-coverage tests ensure every declared edge is exercised
and every state has a valid explanation. Golden examples may verify labels and
channel projection, but they do not become a second transition definition.

Runtime validation repeats target-, permission-, generation-, and
evidence-dependent checks. Static validation never substitutes for commit-time
policy.

## Definition Completeness And Complexity Control

A domain is not considered modelled because it has a state enum or a diagram.
Its authoritative definition must include:

- the initial, waiting, terminal, cancellation, failure, and reconciliation
  states;
- every user, system, activity-result, timeout, and recovery command;
- source and target for every transition;
- guards, policy, required evidence, effect/activity, and explanation;
- concurrency scope and conflict key for modifying commands;
- artifact refs that are inputs or outputs without turning lineage into state;
- projections and channel-neutral affordances for each reachable snapshot;
- definition migration and in-flight instance policy.

The compiler emits a review report with reachable states, transition count,
outgoing-command count per state, cycles, competing guards, waiting states,
unhandled activity outcomes, projection coverage, and generated test coverage.
Thresholds initially require review rather than impose one universal numeric
limit, because a count alone does not measure understandable behavior.

Complexity is bounded through named domain workflows and explicit subworkflow
commands, not by creating one global AdaOS graph. A subprocess has typed input,
output, cancellation, and parent correlation. It cannot reach into its parent's
state or add a UI-only transition. Orthogonal state regions are used only for
independent business facts; task progress, artifact lineage, and view focus
remain separate models to avoid a Cartesian product of states.

Every domain roadmap must link a discussion/requirement decision to its owning
definition element, implementation task, and acceptance evidence. This
traceability map is navigation, not a duplicate source of transition truth.

## Persistence and Durable Execution Are Secondary Adapters

The workflow definition, transition resolver, explanation, and conformance
tests must run without DBOS, Temporal, Restate, or another workflow product.
For short local processes, the existing AdaOS SQLite transaction and task
model may be sufficient. Long waits, crash recovery, retries, cross-node work,
or many concurrent instances may justify a durable-execution adapter.

Only then does AdaOS require a provider-neutral port such as:

```text
start(workflow_type, instance_id, input, idempotency_key)
invoke(instance_id, command, expected_generation, idempotency_key)
describe(instance_id)
cancel(instance_id, reason, idempotency_key)
recover(scope, cursor)
```

Any adapter must preserve canonical AdaOS commands, events, state generations,
guards, explanation, and activity contracts. Provider-native handles do not
escape into skills, NLU, Web, Telegram, or workflow definitions.

Candidate evaluation is deliberately postponed until the Builder semantic
model works on the reference persistence path:

- SQLite is the compatibility/reference storage for local-first proof;
- DBOS may be evaluated as a Python/SQLite/PostgreSQL implementation aid;
- Temporal may be evaluated for Root-level distributed orchestration;
- Restate may be evaluated as a keyed workflow/actor sidecar;
- NATS or JetStream may carry events and outbox work but do not define workflow
  semantics.

The architecture has succeeded even if no external engine is adopted. An
engine is selected only when measurements show that it removes more reliability
and operational risk than it introduces.

## Local-First and Root Topology

The runtime supports two deployment classes:

- node-local workflows that must continue without Root and retain their state
  in a node-owned provider;
- Root-coordinated workflows whose purpose inherently spans nodes, users, or
  centrally hosted resources.

A local workflow may enqueue a Root activity and wait durably while offline.
Root reconnection delivers a correlated result through the inbox. It does not
move the entire local workflow to Root.

Conversely, a Root workflow may request an activity from a node, but must
record node identity, capability version, deadline, and disconnect semantics.
Transport readiness and workflow readiness remain separate status planes.

## Activity Contract

Activities perform non-deterministic or externally visible work such as:

- LLM and Codex execution;
- filesystem, Git, and package mutation;
- validation and tests;
- trial preparation and activation;
- publication and channel-pointer movement;
- notifications and external API calls.

Every activity declares:

- typed input/output and bounded payload policy;
- side-effect and risk class;
- idempotency key and target digest when supported;
- retryable and terminal failures;
- timeout, heartbeat, cancellation, and abandonment behavior;
- compensation or reconciliation path;
- required evidence and redaction;
- executor and environment requirements.

Read-only and idempotent activities may be retried by policy. Non-idempotent
activities require a transactional target, an external idempotency contract,
or manual reconciliation after an uncertain outcome. Automatic repetition of
an uncertain mutation is forbidden.

## Domain Truth, Journal, and Projections

The following ownership rules prevent dual truth:

- domain stores own projects, Issues, Changes, artifacts, releases, policies,
  and permissions;
- the selected execution provider owns the progress journal for one workflow
  instance;
- activities call idempotent AdaOS domain commands rather than writing UI or
  duplicated workflow state;
- canonical workflow events feed AdaOS read models through an inbox/outbox
  boundary;
- Web/Telegram messages, status cards, Lifecycle, and Yjs trees are disposable
  projections;
- external provider history is not read directly by skills or browser code.

Where the reference provider shares the AdaOS SQLite database, a domain event,
workflow transition, and outbox item should commit atomically. With an external
provider, the adapter uses correlation, idempotency, expected generations, and
reconciliation rather than claiming a distributed transaction.

## Security and Authority

The workflow boundary enforces:

- opaque, high-entropy, scoped, expiring action tokens;
- principal, conversation, workflow, target, and generation binding;
- single-use and replay protection where declared;
- authorization at invocation and again at durable commit;
- separation of approval, permission, and successful execution;
- no raw tool target/arguments in untrusted channel callbacks;
- no secrets in model prompts, workflow history, or action tokens;
- explicit consent and URL-mode handoff for sensitive external interactions;
- fail-closed handling of prompt injection or model-produced authority claims;
- audit without copying unrestricted sensitive payloads into every trace.

## Versioning and Migration

Four versions remain independent:

- workflow schema version;
- workflow definition version;
- executor/provider and worker code version;
- domain artifact or release version.

New instances use the admitted definition version. In-flight instances either
remain pinned to compatible worker code, use an explicit deterministic
migration, or enter a visible operator-required state. Deployment must never
reinterpret old history with incompatible code silently.

Provider changes apply to new instances first. Cross-provider migration of an
active instance is deferred until a real requirement and a reversible protocol
exist.

## Observability and User Explanation

Every describe/projection response should answer:

- what workflow and immutable target are active;
- what state and generation are current;
- what is running or waiting, and for whom;
- what evidence exists or remains missing;
- what actions are currently allowed;
- why a requested action is unavailable or rejected;
- where a later response will be delivered;
- whether state is fresh, degraded, recovering, or requires reconciliation.

Operational telemetry correlates conversation message, intent proposal,
workflow command/event, task/activity attempt, artifact digest, evidence, and
delivery attempt. Trace success is not acceptance evidence by itself.

## Builder Reference Workflow

Builder is the first complete proving domain:

```text
request
  -> Issues and Change
  -> clarification/ready
  -> Prototype or direct Automation route
  -> review/input_required
  -> isolated Automation Run
  -> verification
  -> immutable Trial
  -> accept/revise
  -> Publication
```

The normative Builder states, transition catalogue, concurrency/focus rules,
Run purposes, data modes, and Review lifecycle are owned by
[Builder Conversational Development](builder-conversational-development.md#builder-change-statechart).

The workflow instance is keyed by `change_id`. Prototype, Automation, and
Publication nodes are artifact lineage projections, not three independent
mutable process buckets. Lifecycle selection changes view context; workflow
commands change business state; Preview selection changes only the rendered
immutable target.

The pilot must cover Web and Telegram, natural-language feedback, explicit
controls, Codex completion, restart recovery, stale action rejection,
cancellation, trial evidence, publication, and final delivery.

## Multi-User Extension Seam

The single-user implementation already records principal, authority scope,
actor, generation, and evidence. Future collaboration may add:

- role- or zone-specific approval policies;
- several reporters and reviewers;
- competing Changes and conflict detection;
- change extraction and reusable proposals;
- release trains and shared candidate channels;
- delegation and quorum decisions;
- cross-node activities and Root coordination.

It must not replace serialized workflow commands with CRDT merging. CRDTs may
help co-edit artifact content; state transitions, approvals, and durable
effects remain ordered, authorized events.

## Technology Positioning

The architecture is a hybrid symbolic/probabilistic control plane: a typed
statechart decides what is legal, while models help interpret language and
produce artifacts inside those rails. Relevant precedents cover different
layers rather than one product solving the whole problem:

- [XState states](https://stately.ai/docs/states) and
  [actors](https://stately.ai/docs/actors) demonstrate statecharts, pure
  guards, explainable available events, nested/parallel states, and invoked
  long-running work;
- [MCP Elicitation](https://modelcontextprotocol.io/specification/draft/client/elicitation)
  provides useful interoperability direction for projecting structured human
  input without defining the business workflow;
- [OpenAI Agents SDK human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)
  and [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
  demonstrate pause, approval, and resume patterns for agent runs;

Durable execution products are optional implementation references below that
semantic model:

- [Temporal architecture](https://github.com/temporalio/temporal/blob/main/docs/architecture/README.md)
  documents append-only workflow history, deterministic workflow code, and
  idempotent or non-retryable activities;
- [DBOS Python](https://docs.dbos.dev/python/programming-guide) documents a
  library-based durable workflow model with SQLite and PostgreSQL providers;
- [Restate services](https://docs.restate.dev/foundations/services) documents
  durable workflows, signals, and keyed single-writer services;
- [MCP Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
  provides an interoperability direction for long-running requests and
  `input_required` while remaining an external protocol concern;
- [NATS JetStream](https://docs.nats.io/nats-concepts/jetstream) provides
  durable delivery and replay but is not a business workflow model.

AdaOS's product opportunity is the integration of natural conversation,
deterministic interaction affordances, formal transition policy, artifact
lineage, evidence, local-first trial, and cross-channel delivery. The durable
engine is optional infrastructure supporting that model, not the objective of
the architecture.
