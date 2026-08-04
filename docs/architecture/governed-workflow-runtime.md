# Governed Data-Driven Workflow Model and Interaction Architecture

Status: target architecture and system boundary.

Last reviewed: 2026-07-31.

This document defines the AdaOS-wide governed data-driven model for explainable
and validatable states, transitions, guards, effects, interactions, and
natural-language input. "Data-driven" means the authoritative transition
catalogue is a validated, versioned artifact; it does not move authority or
executable code into that artifact. Its primary purpose is to prevent each
skill, channel, and UI from inventing an incompatible workflow. Persistence
and durable execution support that model but do not define it. The
implementation sequence is owned by the
[Governed Data-Driven Workflow Model Roadmap](governed-workflow-runtime-roadmap.md).
The current durable/ad-hoc state, retry, pending-response, background-task, and
transport inventory is tracked in
[Governed Workflow Runtime Inventory](governed-workflow-runtime-inventory.md).

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

Workflow definitions are data artifacts. A human, Builder, or LLM may propose
one, but activation treats the artifact as untrusted until it passes the ABI
schema, compiler, registered-code, migration, and conformance gates. The data
may select registered guard, effect, activity, compensation, policy, and
projection identifiers with typed parameters. It cannot introduce executable
code, authority, filesystem operations, shell commands, network calls, or
model instructions that mutate state.

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
13. negotiate channel/client/surface capabilities without changing command
    legality, risk, confirmation, or target identity;
14. separate task outcome, conversation materialization, transport delivery,
    and acknowledgement so delivery recovery never repeats work;
15. keep Issue, Change, workflow, artifact, dependency, execution,
    conversation, release, authority, and view relationship planes distinct.
16. allow skills, scenarios, Builder, and LLM-assisted authoring to produce
    workflow definitions as inspectable data while keeping execution in
    registered AdaOS code.

## Non-Goals

The first model will not:

- formalize every conversational utterance as a workflow transition;
- allow arbitrary scripts inside declarative workflow definitions;
- treat an LLM-authored workflow artifact as trusted code or policy;
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
- can be proposed by Builder or an LLM and then rejected deterministically when
  its structure, registered identifiers, safety policy, or conformance cases do
  not match the AdaOS contract.

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

One large graph or state enum would create state explosion and unclear
authority. AdaOS therefore keeps the following relationship planes separate:

| Plane | Nodes and edges | Cycle rule | Source of truth |
| --- | --- | --- | --- |
| Issue graph | Issues with `related`, `duplicate`, `depends`, and `blocks` | `related` may cycle; dependency/blocking cycles are rejected or explicitly diagnosed | Issue/project store |
| Change graph | Changes containing Issues and linked by `depends`, `alternative`, `supersedes`, and `split_from` | dependency, split, and supersession edges are acyclic | Builder/project store |
| Workflow statechart | Named business states connected by command transitions | deliberate review/revision loops are allowed | versioned WorkflowDefinition plus instance snapshot |
| Artifact lineage | Revisions, candidates, and releases connected by `derived_from`, `implements`, and `published_as` | immutable DAG | artifact and release stores |
| Component dependency | Scenarios, skills, packages, and contracts connected by declared requirements and resolved bindings | source constraints may be recursive; an activated lock must resolve without a dependency cycle | manifests and ProjectRelease/WorkspaceLock |
| Execution | Tasks, Runs, attempts, child work, retries, and recovery | attempt/causation graph is append-only and acyclic | task/Run journal |
| Conversation / interaction graph | Conversations, threads, messages, interactions, responses, tasks, and ReplyRoutes connected by correlation and causation | message order is monotonic per conversation; correlation is not business state | conversation ledger and interaction registry |
| Release / deployment graph | SourceRefs, packages, releases, candidates, channels, activation locks, and receipts | releases are immutable; channel/slot pointer history is append-only | registry and WorkspaceLock |
| Authority / trust | Principals, roles, scopes, approvals, delegations, and policy witnesses | policy-defined; never inferred from another plane | identity, policy, and audit stores |
| View / command context | Focused project/Change, inspected ref, Preview target, and channel surface | not an authoritative graph and freely replaceable | scoped context projection |

Each plane has typed refs into other planes, but may not copy another plane's
mutable state or reinterpret its edges. For example, an Issue `blocks` edge
does not create a workflow transition, a completed Run does not imply an
accepted Change, and selecting an artifact does not promote it.

The first eight rows are the normative product/execution graph family.
Authority/trust and view/command context are independent cross-cutting planes,
not omitted edges of that family. `adaos.workflow.relationship_edge.v1` and
the relationship-plane validator reject unknown plane/relation pairs,
self-links in acyclic relations, forbidden cycles, duplicate edge ids, and
embedded mutable state. Historical `demand`, `delivery`, and `workflow` names
are input aliases only and normalize to `issue`, `change`, and
`workflow_statechart`.

The four planes most frequently confused in the current Builder are described
in more detail below.

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

### TransitionDescriptor

`adaos.workflow.transition.v1` is the normative edge contract. A transition is
incomplete unless it declares or explicitly marks as not applicable:

- stable transition id, definition version, source selector, and target state;
- user/system/activity-result/timeout/recovery trigger and typed input schema;
- actor, authority, target-resolution, and command-context requirements;
- pure guards, invariant checks, policy refs, and typed rejection reasons;
- expected generation, concurrency scope, conflict key, and idempotency
  contract;
- risk and side-effect class plus the transactional commit boundary;
- registered effect or activity and typed success result;
- mappings for known failure, `input_required`, timeout, cancellation, and
  `outcome_unknown`;
- retry eligibility, attempt policy, compensation, and reconciliation path;
- required approval/evidence gates and immutable input/output refs;
- emitted domain/workflow events and transactional outbox work;
- asynchronous reply policy, progress policy, and ReplyRoute requirements;
- interaction capability requirements and secure/rich fallback policy;
- localized available, blocked, running, completed, and failed explanations;
- audit, correlation, causation, redaction, metrics, and trace requirements;
- definition migration and in-flight compatibility behavior.

Defaults may reduce repetition, but the compiled descriptor contains the
effective value of every field. A UI hint, model prompt, activity function, or
transport adapter cannot add a guard, retry rule, or outcome absent from the
compiled descriptor.

The reference compiler materializes this effective record through
`normalize_transition_descriptor(...)` before validation and execution. Raw
definition source/digest remains pinned for in-flight compatibility, but
executors and projections consume the same compiled descriptor rather than
maintaining partial transition structures.

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

Availability includes execution readiness. A command that names an external
activity is not presentable merely because its state edge is legal: the
runtime must have a registered invocation adapter capable of atomically
recording or enqueuing that activity with the declared idempotency, recovery,
and reply contracts. If the adapter is absent or unhealthy, `explain()`
returns a typed `executor_unavailable` blocker. A channel must withhold the
control and show that blocker; it must never advance only the state-machine
record and pretend that Codex, Trial, Publication, or another effect ran.

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
    to: automation_ready
    trigger:
      command: builder.accept_prototype
      input_schema: adaos.builder.prototype_accept.v1
    authority:
      actor_policy: builder.prototype_reviewer
      target_ref: prototype_ref
    concurrency:
      expected_generation: required
      scope: change
      conflict_key: change_id
    risk: isolated_write
    guards:
      - builder.prototype_target_is_current
      - builder.review_is_authorized
    evidence:
      - prototype_review
    effect:
      activity: builder.record_prototype_acceptance
      transaction: builder_change_store
      idempotency: command_key
      outcomes:
        success: automation_ready
        known_failure: prototype_review
        unknown: reconciliation_required
    async_reply:
      policy: terminal_result
      route: originating_conversation
    interaction:
      requires: [explicit_confirmation]
      fallback: secure_deep_link
    explanation:
      available: builder.prototype.accept.available
      blocked: builder.prototype.accept.blocked
      completed: builder.prototype.accept.completed
    emits:
      - builder.prototype.accepted
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
- typed commands and compiled `TransitionDescriptor` records;
- registered guard, activity, and compensation identifiers;
- optional typed subworkflow declarations and join policies;
- risk and confirmation policies;
- interaction capability requirements and explanation templates;
- schema and migration policy.

Declarative definitions may reference registered code. They may not contain
arbitrary executable expressions, filesystem paths, shell commands, or model
prompts with authority to mutate state.

### Data-Driven Authoring Boundary

Data-driven workflow authoring means the transition graph, typed command
surface, risk model, guard/effect identifiers, explanations, and projection
requirements are serialized and versioned. It does not mean AdaOS accepts a
general workflow programming language from a scenario, skill, marketplace
package, or model response.

The authoring pipeline is:

```text
current definition + ABI + adapter catalogue + role/policy ceilings
  -> workflow.authoring_context
  -> LLM/human candidate definition
  -> ABI schema validation
  -> compile_definition()
  -> registered guard/effect/activity/policy lookup
  -> definition review report
  -> generated conformance cases
  -> workflow.authoring_attempt provenance + bounded repair history
  -> domain activation or rejection
```

Generated definitions therefore fail before runtime execution when they name
unknown code, create ambiguous edges, omit required outcomes, broaden
authority, lack explanations, or cannot prove representative legal and illegal
paths. A domain may offer higher-level templates or macros to reduce authoring
noise, but the activated artifact is the expanded `WorkflowDefinition`.
Authoring context and attempt provenance remain separate records, so model
identity, prompt/context digest, diagnostics, and repair history cannot change
the definition digest.

### Artifact Location And Manifest Binding

A skill or scenario that owns a governed process stores exactly one canonical
definition in `workflow.json` at the artifact root, alongside `skill.yaml` or
`scenario.yaml`. The canonical manifests reference it explicitly in the same
style that a scenario references `webui.json`:

```yaml
# scenario.yaml
ui:
  manifest: webui.json
workflow:
  manifest: workflow.json
```

```yaml
# skill.yaml
workflow:
  manifest: workflow.json
```

The bootstrap contract deliberately permits zero or one workflow per skill or
scenario:

- an absent `workflow` field means that the component owns no governed business
  process; it does not cause an implicit workflow to be inferred from tools,
  events, UI, or prompts;
- when present, `workflow.manifest` must be exactly `workflow.json` for v1 and
  the file contains exactly one `adaos.workflow.definition.v1` object;
- arbitrary paths, inline governed definitions, and several workflow files or
  role-specific variants in one component are rejected;
- a Project may contain a scenario and several skills, each with its own single
  component workflow; Project coordination and parent/child composition use
  typed refs rather than merging them into one global graph;
- the component manifest, `workflow.json`, registered adapter contracts, and
  executable code are one package candidate. `workflow.json` cannot be pushed,
  installed, activated, or rolled back independently from that package.

Governed v1 definitions use strict UTF-8 JSON, not YAML. This removes YAML tag,
alias, duplicate-key, and implicit-type ambiguity from the LLM authoring and
admission boundary. Legacy inline `scenario.yaml.workflow` remains a bounded
translation input during migration, never an activated second authority.

The first implemented slice uses
`workflow_artifacts.load_manifest_bound_workflow` as the shared skill/scenario
admission path. It enforces the exact filename, strict UTF-8 JSON with
duplicate-key rejection, bounded bytes/depth/state/command/transition counts,
complete ABI compilation, and a canonical semantic digest. `builder_skill` is
the first owner: its DEV `workflow.json` is loaded and compiled by
`BuilderWorkflowService`; the in-core JSON resource exists only as an
isolated-test/rollback compatibility source when no DEV Builder skill is
present. There is no remaining Python transition-table constructor.

### One Graph, Role-Dependent Access

Role differences do not create alternative workflow definitions. The resolver
evaluates the same state, transition catalogue, target generation, and domain
facts against the principal's verified role and permission claims. `explain()`
therefore may project different allowed commands and blockers for two actors
without changing the workflow state or definition.

The first local validation profile defines two stable role ids:

- `guest`: an unauthenticated or anonymous principal. It receives only commands
  explicitly allowed by both the definition and the platform guest policy;
  publication, installation, authority management, and protected external
  effects remain denied;
- `registered`: an authenticated principal. It is eligible for commands that
  explicitly admit `registered`, but authentication alone grants no
  administrator, publication, filesystem, network, or destructive authority.

Role claims come from the AdaOS identity/authority plane, never from a model,
channel payload, workflow context write, or definition. Transition `actors`
and `permissions` can narrow platform authority; they cannot grant a role,
invent a permission, or weaken a platform policy floor. Unknown roles and
permissions fail closed. More roles, responsibility zones, delegation, quorum,
and revocation remain later extensions of this same policy boundary.

### Registered Code And Trust Levels

"Registered" describes resolvable executable code, not trusted authorship.
The registry distinguishes:

- platform-owned adapters shipped and attested with the AdaOS runtime;
- package-owned adapters whose code, tool ABI, permissions, and package digest
  are admitted with the skill or scenario;
- dependency-owned adapters resolved to an exact package in the containing
  `ProjectRelease`.

An LLM-authored package adapter does not become trusted merely because its id is
present in a registry. Admission validates its input/output contract,
side-effect and permission declaration, runtime isolation policy, and exact
package binding. A definition may only narrow those declarations. If the
definition requests broader authority or risk than the registered contract,
compilation fails. Runtime dispatch resolves the adapter through the immutable
release binding, never through a mutable global name alone.

### Workflow Artifact, Validation, Registry, And Admission Records

The existing `src/adaos/abi/workflow.*` contracts remain the normative
foundation and evolve in place under normal schema-version rules. The
definition schema references the complete transition schema and resolves from
the published ABI files without relying on private Python globals. The compiler
and registry validate guard params, activity and compensation params, declared
input/output schemas, side-effect/risk class, permission ceilings, owner
scope/package, sandbox class, and immutable contract digests.

Authoring and activation add records around, not inside, the pure definition:

- `adaos.workflow.definition_artifact.v1` binds the canonical definition,
  package digest, validation lock, adapter locks, binding digest, definition
  change flag, binding change flag, and required migration id;
- `adaos.workflow.validation_report.v1` carries structured schema/compiler/
  registry diagnostics, graph diffs, bounded metrics, and repair-facing paths;
- `adaos.workflow.registry_entry.v1` describes a registered adapter's owner,
  implementation identity, typed input/output, params schema, side effects,
  permissions, sandbox, and contract digest;
- `adaos.workflow.admission.v1` records the exact WorkspaceLock digest,
  ProjectRelease digest, admitted workflow artifacts, required migrations, and
  candidate-generation digest. The first admission record has `admitted` and
  `not_required` statuses; longer candidate/review/rollback lifecycle state
  remains in activation history and Builder evidence.
- `adaos.workflow.authoring_context.v1` gives an LLM or human author the exact
  schema digests, registered adapter catalogue, role-policy floor, domain
  invariants, examples, and complexity limits for one candidate.
- `adaos.workflow.authoring_attempt.v1` persists model identity,
  prompt/context digests, candidate digest, validation-report digest,
  diagnostics, status, and bounded repair history.
- `adaos.workflow.static_report.v1` projects an admitted definition and,
  when present, its conversational package into a non-authoritative statechart,
  definition review, generated conformance cases, story summaries, and
  state/command/transition/output coverage. Story summaries intentionally keep
  semantic command/output/correlation evidence and omit exact user prose.
- `adaos.workflow.metrics_report.v1` records the measured proof surface:
  definition complexity, governed context sufficiency, conversation-story
  outcomes, current/legacy cycle-time probes, diagnosis effort, and signed
  deltas. Metrics are evidence attached to a definition digest, not executable
  transition semantics.

`WorkflowDefinition` remains deterministic process data. Mutable review state,
LLM provenance, package installation state, and activation pointers do not
become fields of the definition and cannot change its semantic digest.

### Packaging, Release Binding, And Atomic Activation

`workflow.json` participates in the existing artifact pipeline rather than a
new workflow-specific delivery channel:

```text
source component
  -> immutable PackageRef(code + manifest + workflow.json)
  -> ProjectRelease(package set + resolved adapter bindings)
  -> WorkspaceLock CAS(package and workflow bindings)
  -> one runtime generation
```

The package file inventory records the raw `workflow.json` hash. The target
package manifest additionally carries one enriched `workflow_lock` containing:

- `path: workflow.json`, workflow schema, type, and definition version;
- the canonical semantic `definition_digest`;
- the validation-report/evidence digest;
- the required registered adapter contract ids and digests.

Illustrative package-manifest projection:

```json
{
  "workflow_lock": {
    "path": "workflow.json",
    "schema": "adaos.workflow.definition.v1",
    "workflow_type": "builder.change",
    "definition_version": "1.0.0",
    "definition_digest": "sha256:...",
    "validation_report_digest": "sha256:...",
    "required_adapter_contracts": [
      {
        "id": "builder.codex.run",
        "contract_digest": "sha256:..."
      }
    ]
  }
}
```

The locally validated first package slice serializes the same identity as an
`ArtifactContractLock`: `lock_id` is
`workflow:<workflow_type>@<definition_version>` and `digest` is the canonical
definition digest. Package verification derives that lock again from the
packaged manifest and `workflow.json`; ProjectRelease and WorkspaceLock retain
it inside the immutable component PackageRef. Package verification also derives
the validation-report lock, resolves registered adapter contract locks, and
recomputes the aggregate `workflow_binding_digest` against the active registry.
Activation records those fields in the workflow admission candidate before a
WorkspaceLock switch can proceed.

The package digest already covers the manifest, executable code, schemas, and
every file, so changing either code or `workflow.json` creates a different
package. The component version must be advanced for either change; a workflow
definition version remains independently meaningful for instance migration.

`ProjectRelease` resolves every required adapter to a platform runtime contract
or an exact component `PackageRef` and records a `workflow_binding_digest`.
`WorkspaceLock` pins the ProjectRelease/package digests and projects, for
inspection, the workflow definition and binding digests selected for each
component. Runtime instances pin the exact definition digest and may additionally
pin the selected package and binding digests when they are created from an
activated package. Definition migration must provide replacement package and
binding pins for already pinned instances. A mutable registry lookup cannot
reinterpret an existing instance.

Activation stages the complete ProjectRelease, verifies all package and
workflow locks, builds a candidate adapter registry, compiles the definitions,
checks migration and health, and only then performs one compare-and-switch of
the WorkspaceLock and runtime-generation pointer. Failure before that switch
leaves the prior generation authoritative. Failure after an uncertain external
effect enters the declared reconciliation path; it never mixes old code with a
new definition. Rollback selects the prior complete WorkspaceLock/runtime
generation, not an individual workflow file.

### WorkflowInstance

`adaos.workflow.instance.v1` contains:

- stable instance id and domain aggregate reference;
- workflow type, exact definition version, and canonical definition digest;
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
- required/optional capabilities, semantic presentation hints, and allowed
  fallback classes;
- opaque action tokens rather than raw tool names and arguments;
- reply-route and status references.

Web may render buttons, selectors, review cards, search, or a rich view.
Telegram may render an inline keyboard, pagination, a deep link, or a compact
message. A text-only channel may render numbered choices. All are projections
of the same interaction.

Interaction lifecycle is independent from message delivery:

```text
created -> projected -> awaiting_input -> partially_answered
                                      -> answered -> accepted -> completed
                                                  -> validation_failed
awaiting_input | partially_answered -> expired | cancelled | superseded
```

`projected` means at least one safe presentation plan exists, not that a
transport delivered it. `answered` means input was captured, not that the
workflow accepted it. A corrected answer supersedes the prior response through
a new record; it never rewrites conversation history.

### InteractionResponse

`adaos.conversation.interaction_response.v1` contains:

- interaction, presentation, action-token, and response identity;
- principal, conversation/thread, channel, and command-context binding;
- selected option or typed form values plus source message refs;
- original text and IntentProposal when natural language was interpreted;
- expected workflow, context, and interaction generations;
- validation result, correction/supersession refs, and expiry decision;
- consumed command/event ref or typed rejection reason.

Deterministic controls produce an InteractionResponse without NLU. Free text
may propose one, but the same schema, generation, guard, and policy checks
apply before it is consumed.

### Capability Profile And Presentation Negotiation

`adaos.conversation.channel_capability_profile.v1` describes the effective capabilities of
one transport + client + surface combination. It is versioned and includes:

- text, markdown, choices, multi-select, typed forms, file upload/download,
  rich view, deep link, miniapp, secure input, progress, cancel, message edit,
  replace/coalesce, delivery receipt, and acknowledgement support;
- limits such as button count, label/text/payload size, form fields, attachment
  size, update frequency, and callback-token size;
- locale, directionality, accessibility, and supported media hints;
- reconnect/resume and cross-channel handoff support;
- profile source, freshness, and downgrade reason.

`ConversationInteraction` carries an embedded
`adaos.conversation.interaction_requirements.v1` rather than naming a chosen
widget: required and optional capabilities, input schema, risk, secure-entry
requirement, fallback classes, and whether text-only representation preserves
meaning.

Negotiation produces
`adaos.conversation.interaction_presentation_plan.v1`, retained by
`adaos.conversation.interaction_presentation.v1`, containing:

- interaction and capability-profile versions;
- selected presentation and bounded layout/transport parameters;
- included semantic actions and input fields;
- omitted optional features with reason codes;
- chosen fallback or cross-channel handoff;
- expiry, refresh, replacement/coalescing, and acknowledgement policy;
- proof that every required command remains reachable with equivalent risk and
  confirmation semantics.

Negotiation order is deterministic:

1. intersect interaction requirements with the current effective profile;
2. apply policy, privacy, accessibility, and risk restrictions;
3. choose the least complex presentation preserving required semantics;
4. otherwise select an allowed text/deep-link/Web/miniapp fallback;
5. if none is safe, return `unsupported` with a reason and keep the workflow in
   an explainable waiting state.

A capability is not permission, authority, business availability, or consent.
Negotiation cannot remove a required confirmation, expose a secret as ordinary
chat text, make a blocked command available, or silently omit the only safe
way to continue. Reconnect, client change, or channel handoff creates a new
presentation plan over the same Interaction; it does not create a second
business request.

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

`ResponseEnvelope` is channel-neutral outbound content. It is not an
Interaction, user answer, task result, or delivery receipt. It contains a
message class (`accepted`, `started`, `progress`, `input_required`, `resumed`,
`terminal`, `cancelled`, or `notification`), conversation/task/workflow refs, content, sensitivity,
presentation requirements, and optional replacement/coalescing key.

`adaos.conversation.delivery_attempt.v1` records one attempt to materialize or
deliver one envelope through one presentation/transport. It contains attempt,
message/envelope, presentation, recipient, external ref, idempotency key,
status, provider receipt, error, timestamps, and retry disposition. Retrying a
DeliveryAttempt can never repeat the workflow command or activity.

The asynchronous protocol is:

```text
command accepted and journaled
  -> accepted ResponseEnvelope
  -> Task/Run progress events (optional, monotonic)
  -> input_required Interaction (optional)
  -> resumed command/activity
  -> one canonical terminal outcome
  -> terminal ResponseEnvelope
  -> one or more independent DeliveryAttempts
  -> acknowledged or explicitly undeliverable
```

Required invariants:

- an HTTP/tool acknowledgement means accepted, not completed;
- workflow state, task outcome, conversation materialization, and transport
  delivery have separate statuses and timestamps;
- one terminal outcome is committed idempotently before notification;
- restart rebuilds pending envelopes and attempts from the outbox/journal;
- an original channel failure may use another policy-authorized ReplyRoute, but
  never another principal, Change, or target;
- late and out-of-order progress carries a monotonic sequence; clients ignore
  stale progress without hiding the terminal result;
- progress may be rate-limited or coalesced by key, while evidence remains
  available for diagnostics;
- cancellation and `input_required` are addressable commands/interactions, not
  special transport messages;
- expiry or loss of every ReplyRoute leaves the result queryable in its
  canonical conversation/task and exposes an undeliverable reason.

User-attention policy decides whether an event appends a message, replaces a
status card, updates only a progress projection, records evidence silently, or
raises a notification. It considers risk, urgency, user preferences, quiet
periods, channel limits, and whether input is required. It cannot suppress a
required approval or make delivery success part of the business transition.

The normative records are `adaos.conversation.attention_policy.v1` and
`adaos.conversation.attention_plan.v1`. The policy classifies events into
`append`, `update`, `evidence_only`, or `projection_only`; defines progress
coalescing windows; records channel preferences and quiet hours; and escalates
`input_required`, terminal failure, and expiry. The resulting plan is attached
to the envelope so restart/re-delivery does not re-decide user attention from
different transient client state.

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
policy. Executor readiness and the exact activity adapter are checked again at
admission. This makes “shown and accepted” mean “the declared effect can be
started or durably queued,” not only “the destination state exists.”

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
The separate metrics report records these structural counts alongside context
coverage and story-derived rates, so complexity review and user-facing cycle
time are comparable without changing the workflow definition digest.

Complexity is bounded through named domain workflows and explicit subworkflow
commands, not by creating one global AdaOS graph. A subprocess has typed input,
output, cancellation, and parent correlation. It cannot reach into its parent's
state or add a UI-only transition. Orthogonal state regions are used only for
independent business facts; task progress, artifact lineage, and view focus
remain separate models to avoid a Cartesian product of states.

Every domain roadmap must link a discussion/requirement decision to its owning
definition element, implementation task, and acceptance evidence. This
traceability map is navigation, not a duplicate source of transition truth.

## Workflow Composition

A large process is composed through explicit parent/child workflow commands,
not shared mutable state. A subworkflow declaration contains:

- child workflow type/version and stable parent correlation;
- typed input refs and result schema;
- authority delegation and narrower permission scope;
- start/idempotency key and concurrency/conflict scope;
- wait mode plus named join policy (`all`, `any`, `quorum`, or domain-specific
  registered policy);
- timeout, cancellation propagation, abandonment, and late-result behavior;
- child success/failure/input-required/unknown mappings into parent commands;
- compensation/reconciliation responsibility;
- ReplyRoute and evidence aggregation policy.

The child owns its state and journal. It can only affect the parent by sending
a typed result command; it cannot mutate the parent snapshot, UI projection,
or artifact refs directly. The parent records every admitted child identity
and exact definition version.

Partial success is explicit. A multi-artifact Builder Change may wait for
several component Runs and still produce no promotable candidate until its
registered join policy verifies the complete dependency lock. A failed child
does not silently roll back successful external effects; compensation or
residual-effect evidence is part of the outcome.

Composition is introduced only when a bounded child has an independent
lifecycle or executor. Simple deterministic guards/effects remain in the
parent definition; AdaOS does not turn every function call into a workflow.

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

The first measurement gate is complete. The
[Workflow Reference Persistence Decision](workflow-reference-persistence-decision.md)
accepts shared node-local SQLite for the bounded single-user Builder path and
postpones every external provider until a named distributed, availability,
scale, timer, or operator-cost requirement is measured. This decision does not
expand the reference provider's claim to active-active or multi-user work.

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
- `guest` and `registered` bootstrap role claims issued only by the identity
  plane, with definition rules allowed to narrow but never grant authority;
- package-owned registered code constrained by its admitted package digest,
  adapter contract, sandbox, and permission ceiling;
- audit without copying unrestricted sensitive payloads into every trace.

## Versioning and Migration

The following versions remain independent:

- workflow schema version;
- workflow definition version;
- canonical definition and workflow-binding digests;
- interaction/response/envelope schema versions;
- capability-profile and presentation-plan versions;
- executor/provider and worker code version;
- domain artifact or release version.

New instances use the admitted definition version and exact package/binding
digests selected by WorkspaceLock. In-flight instances either remain pinned to
compatible worker code, use an explicit deterministic migration, or enter a
visible operator-required state. Deployment must never reinterpret old history
with incompatible code silently or combine a definition from one package with
code from another.

An in-flight Interaction remains pinned to its semantic schema and target
generation. A channel/client change may negotiate a new presentation against a
new capability profile, but cannot reinterpret already captured values or
weaken its confirmation policy. Schema-incompatible responses fail with a
fresh explanation and replacement Interaction rather than lossy coercion.

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
- which Interaction is awaiting input and how current capabilities present or
  hand it off;
- where a later response will be delivered and whether its task outcome,
  conversation materialization, and transport delivery have succeeded;
- whether state is fresh, degraded, recovering, or requires reconciliation.

Operational telemetry correlates conversation message, intent proposal,
workflow command/event, task/activity attempt, artifact digest, evidence, and
delivery attempt. Trace success is not acceptance evidence by itself.
Cross-surface workflow traces can also be captured as
`adaos.workflow.trace_identity.v1`: a compact report that links the turn trace,
intent proposal, interaction/response when present, canonical invocation,
workflow event, semantic conversation output, response envelope, and delivery
attempt. The report is valid only when those records preserve the same command,
workflow identity, event, envelope, and reply route lineage.

Conversation stories are executable semantic tests, not transcript snapshots.
They may assert repair behavior, store-free `ConversationInteraction`
projection, and channel presentation fallback for the same workflow state.
Those assertions fail when command identity, expected generation, fallback mode,
reason code, semantic equivalence, or repair next-input semantics drift.

Measured acceptance evidence can be captured as
`adaos.workflow.metrics_report.v1`. A metrics report links one definition
digest to definition complexity, context-packet coverage, clarification/repair
rates, repeated corrections, action mismatch defects, presentation fallback,
unsupported presentation, semantic-equivalence failures, and current-versus-
legacy cycle-time deltas. Missing current or legacy measurements are explicit
warnings rather than implicit zeros.

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
The activated transition catalogue is a versioned governed workflow definition
data artifact. Builder code owns registered effects, activities, guards,
legacy adapters, and projections; it does not own a second authoritative
transition table.

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
