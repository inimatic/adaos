# Governed Workflow and Interaction Runtime

Status: target architecture and system boundary.

Last reviewed: 2026-07-30.

This document defines the AdaOS-wide contract for explainable stateful
workflows, conversational input, deterministic interactions, durable
execution, and long-running human or agent work. It is not a commitment to one
workflow product. The implementation sequence and adoption gates are owned by
the [Governed Workflow Runtime Roadmap](governed-workflow-runtime-roadmap.md).

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

This document owns the shared workflow kernel, interaction, intent mediation,
durable-execution provider, activity, and recovery boundaries used by those
domains.

## Decision

AdaOS will use a hybrid control model:

```text
natural conversation and deterministic controls
  -> typed interpretation or command
  -> deterministic workflow kernel
  -> durable execution provider
  -> registered activities
  -> domain records, evidence, and projections
```

The model or NLU layer may interpret, propose, summarize, plan, and produce
artifacts. It does not commit a state transition directly. The deterministic
kernel validates the target, current generation, policy, authority, and
transition before a durable effect is admitted.

A durable-execution product may implement the execution provider, but it does
not become the product model of AdaOS. `Change`, `Revision`, `Trial`, `Release`,
project dependencies, permissions, and artifact provenance remain AdaOS
domain concepts.

The first implementation must be a bounded domain runtime, not a universal
BPMN system, a generic user-programmable workflow DSL, or global event sourcing
for every AdaOS service.

## Motivation

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

The target runtime makes these boundaries explicit and reusable instead of
repairing them independently in each skill.

## Goals

The runtime must:

1. make the current state, reason, target, and allowed next actions explainable;
2. validate every transition independently from its Web, Telegram, CLI, MCP,
   or model origin;
3. accept both deterministic controls and bounded natural-language answers;
4. preserve long-running work across process and transport restarts;
5. prevent silent duplication of modifying effects;
6. retain the originating principal, context, and reply route;
7. separate business state, execution state, artifact lineage, and UI focus;
8. remain local-first and useful while Root is unreachable;
9. admit an external durable engine without making domain contracts
   vendor-specific;
10. provide evidence and traces suitable for debugging, evaluation, and future
    multi-user governance.

## Non-Goals

The first runtime will not:

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

## Four Models That Must Stay Separate

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

## Canonical Runtime Records

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
- execution provider and provider instance reference;
- current state snapshot and monotonic generation;
- authority and tenancy scope;
- pending interaction, task, activity, and evidence references;
- lifecycle, timestamps, and terminal reason.

Exactly one provider is the execution authority for an instance. A runtime
must not silently fall back to another provider after accepting a command.

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
   the provider permits it;
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

## Workflow Definition Validation

Before activation, a workflow definition compiler validates:

- schema and stable identifiers;
- reachability of required states and explicit terminal states;
- references to registered guards, activities, compensations, and policies;
- deterministic ordering or explicit priority for competing transitions;
- required parameters and result schemas;
- confirmation rules for protected risk classes;
- idempotency/retry declaration for every activity;
- success, failure, cancellation, timeout, and unknown-outcome handling;
- user explanation and interaction projection for every waiting state;
- definition-version and in-flight migration policy.

Runtime validation repeats target-, permission-, and generation-dependent
checks. Static validation never substitutes for commit-time policy.

## Durable Execution Provider Boundary

AdaOS exposes a provider-neutral port conceptually equivalent to:

```text
start(workflow_type, instance_id, input, idempotency_key)
invoke(instance_id, command, expected_generation, idempotency_key)
describe(instance_id)
cancel(instance_id, reason, idempotency_key)
list/recover(scope, cursor)
stream_events(instance_id, after_sequence)
```

The port requires:

- durable instance identity and status;
- persisted waits and external signals;
- idempotent command intake;
- activity retry, timeout, cancellation, and heartbeat semantics;
- definition/runtime version visibility;
- recovery and replay diagnostics;
- event or outbox integration;
- bounded payloads with external artifact references for large content;
- backup, retention, and privacy controls.

The port does not expose provider-native handles to skills or UI code. Skills
use AdaOS SDK commands and receive canonical interaction/task projections.

## Provider Strategy

Provider adoption is evidence-driven.

### Reference SQLite Provider

The first provider establishes semantics using the node's existing local-first
SQLite deployment model. It is a correctness reference and compatibility path,
not permission to create another set of ad-hoc per-skill state files.

It must use WAL where supported, monotonic generations, an append transition
ledger, idempotent inbox, transactional outbox, leases/heartbeats for workers,
and explicit unknown-outcome recovery.

### DBOS Candidate

DBOS is the preferred first external pilot because its Python library can use
SQLite locally and PostgreSQL for multi-process production. The pilot must
prove compatibility with AdaOS async execution, upgrades, backup, Windows and
Linux, and existing database ownership before adoption.

### Temporal Candidate

Temporal is the reference for mature distributed durable execution and is a
strong candidate for future Root-level, multi-node, or multi-user workflows.
It must not become a prerequisite for an offline personal node. An adapter may
map AdaOS commands to Updates/Signals, activities to workers, and workflow
events to AdaOS projections.

### Restate Candidate

Restate's workflow and keyed single-writer service model is a close conceptual
fit for workflow instances. Its separate server and platform packaging must be
evaluated against AdaOS native Windows/Linux deployment and supervisor cost.

### NATS and JetStream

Core NATS remains a transport. JetStream may later provide durable outbox,
work-queue, or replay delivery, but a stream does not define business guards,
human waits, compensation, workflow upgrades, or artifact authority. AdaOS
will not build an unbounded custom Temporal clone merely because NATS is
already deployed.

For every workflow instance the selected provider is persisted. Provider
unavailability is visible; it does not trigger silent cross-provider replay.

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

This design intentionally adopts established durable-execution patterns
without binding the product contract to one implementation:

- [Temporal architecture](https://github.com/temporalio/temporal/blob/main/docs/architecture/README.md)
  documents append-only workflow history, deterministic workflow code, and
  idempotent or non-retryable activities;
- [DBOS Python](https://docs.dbos.dev/python/programming-guide) documents a
  library-based durable workflow model with SQLite and PostgreSQL providers;
- [Restate services](https://docs.restate.dev/foundations/services) documents
  durable workflows, signals, and keyed single-writer services;
- [MCP Elicitation](https://modelcontextprotocol.io/specification/draft/client/elicitation)
  and [MCP Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
  provide useful interoperability direction for structured input and
  `input_required`, while remaining external protocol concerns;
- [NATS JetStream](https://docs.nats.io/nats-concepts/jetstream) provides
  durable delivery and replay but is not a business workflow runtime;
- [OpenAI Agents SDK human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)
  and [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
  demonstrate durable pause, approval, and resume patterns for agent runs.

AdaOS's product opportunity is the integration of natural conversation,
deterministic interaction affordances, formal transition policy, artifact
lineage, evidence, local-first trial, and cross-channel delivery. The durable
engine is infrastructure supporting that model, not the model itself.
