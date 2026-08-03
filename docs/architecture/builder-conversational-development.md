# Builder Conversational Development Architecture

Status: target architecture and migration contract for the Builder chat-first
refactoring.

This document defines how AdaOS turns a conversation into a governed software
change without making either chat history, a browser widget, an LLM session, or
a Git branch the development source of truth. It refines the broader
[Builder](builder.md), [Conversation and Channel Architecture](conversation-and-channel-architecture.md),
[Governed Evolution](governed-evolution.md), and
[Artifact Source, Package, and Activation](artifact-source-package-activation.md)
contracts.

Delivery status and priority are tracked only in the
[Builder Roadmap](builder-roadmap.md). This page owns the target concepts and
invariants.

## Decision Summary

Builder is a **chat-first, state-backed development control plane**.

- Conversation is the primary user interaction surface.
- `Change` is the bounded unit of delivery and coordination.
- Rich views are loaded when structured inspection, comparison, selection,
  editing, or spatial review is useful.
- Deterministic actions execute typed commands; they do not ask an LLM to
  reinterpret a button press.
- An LLM may interpret intent, propose scope, construct a Prototype, or prepare
  a command, but AdaOS owns validation, authorization, execution admission,
  activation, and rollback.
- Prototype, Implementation, Trial, and Publication are linked artifacts and
  decisions of one Change, not independent top-level work queues.
- Git records source history, but the product-level Change graph is expressed
  with AdaOS identities, source digests, evidence, and decisions.
- Cross-channel navigation is expressed as a typed destination, not as a
  transport-specific URL mode. AdaOS Connect and Builder delegate link
  construction to the shared Navigation SDK; the client resolves zone,
  authentication, subnet, Webspace, source boundary, synchronization, and
  scenario explicitly. See
  [Navigation Intent And Location](navigation-intent-and-location.md).

`Automation` remains the compatibility name of the current internal execution
lane. User-facing surfaces should prefer **Implementation**. Migration must not
rename persisted actions, SDK methods, or evidence until versioned adapters are
available.

## Product Objective

Builder should minimize the time between a user's intent and trustworthy
working behavior while preserving enough structure to:

- continue work after a model, process, device, or conversation-context change;
- explain what is being changed and what the current Preview renders;
- isolate speculative work from an installed Workspace;
- verify subjective interface feedback and deterministic behavior separately;
- publish and activate only an immutable accepted candidate;
- attribute decisions and evidence in future multi-user development;
- extract a bounded personal adaptation into a reusable proposal later.

The target is not a universal browser IDE and not a visualization of every
internal Builder service. It is an interaction and governance layer over
ordinary AdaOS artifacts and execution environments.

## Canonical Development Model

The canonical hierarchy is:

```text
Project
  -> Issue
  -> Change
       -> Run
       -> Revision
       -> Trial
       -> Release
```

### Project Aggregate

`adaos.builder.project.v1` is the stable coordination boundary for one skill,
scenario, or atomic multi-component capability. It is not itself one workflow
instance and has no single mutable "stage".

Minimum fields:

- canonical project ref, kind, title, description, owner/trust scope, and
  archive state;
- authoritative manifest/source identity and exact current public/stable
  SourceRef and ProjectRelease when they exist;
- installed WorkspaceLock/component bindings and current DEV base refs;
- accepted Prototype and retained Implementation refs plus active candidate,
  Trial, and Publication refs where applicable;
- open/terminal Issue and Change refs, Change dependency/conflict summary, and
  focused Change refs by scoped command context;
- component boundary, declared contracts, resolved dependency-lock refs, and
  affected-ref index used for conflict detection;
- project policy refs for routing, risk, approvals, executors, data modes,
  retention, localization, and publication channels;
- workflow-definition type/version used for each live Change;
- created/updated/archive metadata and a projection generation.

The project owns a portfolio, not a global current Change. Its summary is a
derived projection answering:

- which stable/installed/DEV/candidate identities are current;
- which Changes are open, focused, blocked, conflicting, or awaiting input;
- which shared components and contracts they affect;
- what can safely start, continue, trial, publish, archive, or restore;
- why a project-level command is unavailable.

Project policy may serialize overlapping mutations while allowing independent
Changes and read/evaluation Runs concurrently. Change dependencies use typed
`depends_on`, `blocks`, `alternative_to`, `supersedes`, and `split_from`
edges. Dependency cycles fail validation; symmetric `related` links belong to
Issues, not the execution order.

Cross-component delivery remains one Change only when its scenario/skills must
be accepted and promoted as one ProjectRelease dependency lock. Otherwise the
work is split into linked Changes with explicit contract and join evidence.
The Project aggregate links the separate relationship planes defined by the
[Governed Data-Driven Workflow Model](governed-workflow-runtime.md#related-models-that-must-stay-separate)
without copying their mutable state.

### Issue

An `Issue` is one independently understandable requirement, defect, risk, or
acceptance concern. It answers **what or why** and can outlive one delivery
attempt.

Minimum fields:

- stable `issue_id`;
- original signal references and normalized title;
- status, lane, priority, and confidence;
- acceptance criteria;
- affected semantic refs when known;
- relationship to other Issues;
- decision and verification references.

Project-local Issues are sufficient for the single-user slice. Federation and
cross-user discovery are deferred, but local identities must already be stable
and collision-resistant.

### Change

A `Change` is one bounded delivery scope containing one or more Issues. It
answers **what will be delivered together**.

The Change replaces `change_set` as the canonical product term. During
migration, `change_set_id` is a compatibility alias of `change_id`; there must
not be two independently mutable objects.

Minimum fields:

- `schema=adaos.builder.change.v1` and stable `change_id`;
- project identity and immutable base source/release digest when available;
- intent, request addenda, Issues, acceptance criteria, and route;
- current focus/gate and derived status;
- linked Run, Revision, Trial, Release, decision, and evidence refs;
- actor/trust scope and timestamps;
- supersession and parent/alternative relationships.

A chat message does not automatically create a new Change. Follow-up remarks
normally extend the focused non-terminal Change. A separate Change is created
only for unrelated scope, an explicit alternative, or work following a
terminal Change.

### Change Concurrency And Focus

A project may have several open Changes. "Focused" is a property of one
principal's command context, not a project-wide lock:

- one conversation/thread or Webspace command context has at most one focused
  Change;
- changing focus does not change a Change state or Preview target;
- several users or conversations may inspect different Changes;
- read-only and evaluation Runs may proceed concurrently;
- a state-changing Run is admitted against an exact base generation and
  affected-ref set;
- overlapping writes never use last-writer-wins: the later commit must rebase,
  split, supersede, or fail with an explicit conflict;
- one Change cannot have two accepted state-changing Runs over the same refs
  and generation.

"One active bounded Change" therefore means one mutation target for one
command, Run, and delivery decision. It does not mean that a project can have
only one open Change.

### Run

A `Run` is one attempt by an LLM, Codex, deterministic transformer, evaluator,
or recovery operation. It answers **who or what attempted the work, with which
inputs, and what happened**.

The per-turn `adaos.conversation.development_change.v1` aggregate is a
compatibility predecessor of Run. The node ledger now stores strict
`adaos.builder.run.v1` records under one existing canonical Change. During the
release-pipeline migration, an old checkpoint/publication evidence id may
remain as a compatibility record, but it is explicitly labelled with
`canonical_change_id`, `run_id`, and `evidence_role=builder_run_compatibility`;
the Process projection must not present it as another product Change.

Minimum fields:

- stable `run_id`, `change_id`, executor kind, and lifecycle state;
- immutable context-packet digest;
- allowed paths, permissions, side-effect class, and environment identity;
- input and output artifact refs;
- model/tool identity where applicable;
- verification, failure, retry, cancellation, and commit evidence;
- parent Run for a deliberate retry or evaluator/repair loop.

Retries create new Runs. They never silently replace prior attempts or repeat
an already confirmed modifying operation.

### Iteration, Experiment, Evaluation, And Recovery

Every Run declares a purpose:

| Purpose | Meaning | May advance the Change automatically |
| --- | --- | --- |
| `iteration` | Advances the currently accepted direction from an exact base | Only through the declared transition and gates |
| `experiment` | Explores an alternative without replacing the accepted line | No |
| `evaluation` | Produces evidence about an immutable target | Only by submitting typed evidence to a gate |
| `recovery` | Reconciles or resumes an interrupted known operation | Only when recovery proves the original outcome |

An Experiment has an `alternative_id`, immutable base ref, hypothesis, and
comparison criteria. Its output remains an alternative Revision until an
explicit `adopt_experiment` command records review evidence and creates or
selects a new Revision on the Change's accepted line. `discard_experiment`
closes the alternative without rewriting its evidence. An Experiment cannot
publish, replace Preview's `active:` target, or satisfy a delivery gate merely
because its Run completed.

### Revision

A `Revision` is an immutable artifact snapshot. Prototype UI revisions,
Implementation snapshots, and publication source snapshots use typed refs and
record their source Change and Run.

### Trial And Release

Trial proves one immutable candidate in an isolated activation context. Release
is a promoted immutable `ProjectRelease`; Publication is the decision and
operation that moves a channel pointer to it. Neither is an editable phase.

## Builder Change Statechart

The following is the normative single-user business statechart. Exact
serialization belongs to the shared workflow definition contract; the state
and command meanings belong here.

Builder owns the domain vocabulary and invariants below. The activated
transition catalogue is a versioned governed workflow definition data
artifact loaded through the shared compiler/resolver. Builder implementation
code owns only the registered guards, effects, activities, legacy adapters,
and projections needed to execute that definition.

The first data-driven migration places this definition in the owning
`builder_skill` package as `workflow.json` and declares
`workflow.manifest: workflow.json` in `skill.yaml`. The Builder scenario owns
its UI and view projections and must not carry a duplicate Change workflow.
`builder_skill` code and `workflow.json` are built, trialled, published,
activated, and rolled back as one immutable package; Builder instances pin the
exact definition, package, and resolved adapter-binding digests.

As of the 2026-08-01 local cutover, DEV `builder_skill@0.3.32` is the
transition-catalogue authority and Workspace `builder_skill@0.3.27` contains
the byte-identical handler, definition, and tests. Both runtimes are activated;
instances pin definition version plus semantic digest. The skill push produced
an immutable PackageRef with the same workflow lock. Complete per-instance
package/adapter-binding pins and atomic WorkspaceLock admission remain open;
the preceding paragraph is the target contract, not a claim that those later
gates are closed.

```text
intake -> clarification_required <-> ready
ready -> prototype_editing -> prototype_review -> automation_ready
  |              ^                 |
  |              +---- revise -----+
  +-------------------------------> automation_ready

automation_ready -> automation_waiting -> verification -> trial_ready
       ^                    |              |              |
       |                    +-> reconciliation_required   |
       +-------- revise implementation -------------------+
verification -- revise interface --> prototype_editing

trial_ready -> trial_waiting -> trial_review -> publication_ready
                                  |       |
                                  |       +-> prototype_editing
                                  +----------> automation_ready

publication_ready -> publication_waiting -> published
                           |
                           +-> publication_ready (failed attempt recorded)
```

`cancelled` and `superseded` are explicit terminal outcomes from any
non-terminal business state when policy admits them. An uncertain modifying
activity enters `reconciliation_required`; it never becomes an implicit
retry.

The minimum transition catalogue is:

| Command/event | From | To | Required invariant or result |
| --- | --- | --- | --- |
| `clarify` / `mark_ready` | `intake`, `clarification_required` | `clarification_required`, `ready` | Scope, Issues, acceptance, and route are explainable |
| `start_prototype` | `ready` | `prototype_editing` | Route requires interface/design work; bounded mock/data policy exists |
| `request_prototype_review` | `prototype_editing` | `prototype_review` | Immutable Prototype revision and active Review set exist |
| `revise_prototype` | `prototype_review`, `verification`, `trial_review` | `prototype_editing` | Creates a new revision line; prior artifacts remain immutable |
| `accept_prototype` | `prototype_review` | `automation_ready` | Approval binds the exact Prototype digest |
| `choose_direct_automation` | `ready` | `automation_ready` | No unresolved interface Issue requires Prototype review |
| `start_automation` | `automation_ready` | `automation_waiting` | Exact base, source Prototype when present, context packet, and Run are bound |
| `record_automation_result` | `automation_waiting` | `verification`, `automation_ready`, or `reconciliation_required` | Typed success, known failure, or uncertain outcome |
| `accept_verification` | `verification` | `trial_ready` | Required deterministic checks and evidence pass |
| `revise_automation` | `verification`, `trial_review` | `automation_ready` | New Run will retain prior result as history |
| `start_trial` / `record_trial_result` | `trial_ready`, `trial_waiting` | `trial_waiting`, `trial_review` | Trial binds one immutable candidate and environment |
| `accept_trial` | `trial_review` | `publication_ready` | Acceptance binds candidate digest and evidence |
| `publish` / `record_publication_result` | `publication_ready`, `publication_waiting` | `publication_waiting`, `published` or `publication_ready` | Channel move is idempotent; failure is recorded |
| `reconcile_outcome` | `reconciliation_required` | the declared success successor or the corresponding ready/failure state | Target observation proves whether the original effect committed; it is not repeated blindly |
| `cancel` / `supersede` | any non-terminal state | `cancelled`, `superseded` | Actor, reason, and residual effects are recorded |

Only `prototype_editing` permits mutation of the Prototype accepted line.
Automation states freeze the exact source Prototype. Returning to Prototype
creates a new Revision and invalidates promotion eligibility of Automation
results derived from an older source, but retains those results as historical
or experimental evidence. Starting a new Automation Run never overwrites the
retained last working Implementation or installed Publication.

The `*_waiting` states are business waits pointing to a Run. Run attempt state
(`queued`, `working`, `input_required`, `completed`, `failed`,
`outcome_unknown`) remains separate and cannot be copied into the Change state
enum.

### Workflow Definition Correction Boundary

The word "workflow" is ambiguous in natural language. Builder resolves it by
target and risk rather than by one broad keyword:

- moving a process button, renaming a stage label, or changing the visible
  process layout is a Prototype Issue and may change only `webui.json`;
- changing `workflow.json`, a statechart transition, guard, invariant,
  TransitionDescriptor, registered adapter binding, migration, or role policy
  is an Automation Issue and requires an isolated Codex Run;
- `Show process` / `Показать процесс` is a deterministic read command and
  cannot create either kind of Issue or start an executor;
- a mixed request is split into typed Issues; definition work cannot be hidden
  inside the UI LLM context.

The Prototype LLM receives no write authority over `workflow.json`. The current
Automation slice receives the complete project source, definition ref/digest,
validation diagnostics, governed Issue/acceptance context, and worker-side
structural compilation. Supplying a self-contained exact ABI plus admitted
adapter catalogue remains GWR1-27/GWR4-24 work and must not be inferred as
complete. A valid JSON result is still only a candidate: package, policy,
migration, conformance, release-lock, and atomic activation gates remain
authoritative.

## Development Capsule

Every non-trivial Run receives a bounded `adaos.builder.context_packet.v1`
instead of an unbounded transcript. The packet is the portable development
capsule for one attempt.

It contains references or compact values for:

- project, Change, Issues, route, and acceptance criteria;
- exact base source/release/package identities;
- selected Prototype and retained Implementation refs;
- relevant source paths, schemas, architectural instructions, and dependencies;
- mock/real-data policy and external capability boundaries;
- permissions, risk class, allowed paths, and requested verification;
- prior Run summary and unresolved evidence;
- source conversation messages by id, not a copied full transcript;
- packet digest and construction evidence.

Context construction follows progressive disclosure. The packet carries small
high-signal summaries and stable refs; an executor uses governed tools to load
additional source or evidence only when needed. Raw chat, logs, and unrelated
files must not be copied into every Run.

Automation now builds and persists this packet before every isolated Codex
submission. A direct legacy Automation call first projects a minimal
`automation_direct` Change instead of bypassing the model. The normalized
realize request carries the complete bounded packet plus its digest and
canonical Change id; the Automation session and workflow Run keep the same
identities. Candidate validation and checkpoint/publication metadata carry the
digest forward so a release can be traced to the exact execution capsule.

Interactive Prototype work uses the same boundary. Router supplies its bounded
`adaos.context.packet.v1`; `builder_skill` adds only Pending Action references
belonging to the selected project, asks the workflow service to construct and
persist the canonical development capsule, and supplies that capsule to the
Prototype LLM as `development_context`. Arbitrary transport fields, raw
transcripts, Pending Action payloads, and unrelated project actions are
discarded. Conversation, memory, revisions, and action references are marked
as retrieved untrusted evidence: they provide continuity but cannot grant
authority or override system policy. The LLM submission fails closed if the
Change-bound packet cannot be constructed. Builder session and revision
evidence retain its digest rather than a second mutable copy of the packet.

### Context Sufficiency Contract

A bounded packet must still be sufficient for the requested work. Compactness
is not permission to remove semantics. Every Run declares required context
facets, and packet construction returns a machine-readable coverage report.

For an interface change the packet contains or provides governed retrieval for:

- exact target semantic refs and source revision;
- the target's parent, siblings, order, grouping, and responsive constraints;
- applicable ABI version plus the complete referenced schema definitions, not
  an informal lossy summary;
- current declarative fragment, labels/locales, actions, data bindings, and
  validation constraints;
- the requested outcome, negative constraints, and active Review/Issue
  acceptance criteria;
- relevant Prototype, retained Implementation, installed Publication, and
  dependency refs;
- permitted data modes, side effects, paths, tools, and verification;
- prior failed attempts or evaluator evidence relevant to the same targets.

Screenshots and prose may supplement this structure but cannot replace stable
refs or ABI constraints. The executor may retrieve additional referenced
material through governed tools; it cannot silently scrape an unrelated
workspace or infer missing authority.

Packet construction fails before model submission when a required facet is
missing or ambiguous. The failure names the missing facet and offers
clarification or inspection rather than asking the model to guess. Evaluation
measures target-selection accuracy, constraint retention, unnecessary-context
ratio, clarification rate, and repeated correction rate in addition to token
count.

## Interaction Contract

After each meaningful turn Builder returns an interaction frame. The frame is
a projection, not durable truth:

```yaml
schema: adaos.builder.interaction_frame.v1
message: Prototype P4 is ready for review.
context:
  project_ref: scenario:builder
  change_id: CH-142
  focus_ref: prototype:P4
  preview_ref: proto:builder:P4
status:
  phase: prototype_review
  progress: 0.5
actions:
  - command: builder.prototype.approve
    label: Approve and implement
    risk: isolated_write
    expected_generation: 17
views:
  - kind: prototype_preview
    ref: prototype:P4
    fallback: deep_link
```

An action includes a command, an expected generation or precondition, a risk
class, and a presentation hint. A stale action fails safely and returns the
fresh frame; the client never infers the next workflow state.

`adaos.builder.interaction_frame.v1` is the Builder domain snapshot and
compatibility projection. Human input is requested through the shared
`adaos.conversation.interaction.v1`; its response, capability negotiation,
`adaos.conversation.interaction_requirements.v1`,
`adaos.conversation.interaction_presentation_plan.v1`, ReplyRoute,
ResponseEnvelope, attention plan, and DeliveryAttempts follow
the [shared workflow interaction protocol](governed-workflow-runtime.md#conversationinteraction).
Builder must not invent a second action-token, fallback, acknowledgement, or
delivery lifecycle inside the frame.

### Channel Capability Boundary

All channels should support the interaction core:

- messages and compact status;
- explicit context and focus;
- deterministic actions and approvals;
- progress, cancellation, and error recovery;
- links to richer surfaces.

Rich functionality is capability-dependent, not a universal Telegram parity
requirement. Browser surfaces may provide search, large lists, artifact trees,
diffs, editors, spatial Review, and responsive Preview. A limited channel may
show recent items, summaries, attachments, or a deep link that preserves the
same project, Change, and focus.

Future miniapps can render selected rich views without changing the command or
Change contracts.

The first limited-channel slice uses one dialog contract for Web and Telegram:

- Telegram text is normalized to `dialog.user_message`; the transport does not
  call Builder or NLU directly;
- Router resolves the active/addressed dialog and passes transport metadata in
  `_meta` while the project topic remains the canonical development context;
- Builder replies use `io.out.chat.append`; Router projects assistant text back
  to the originating Telegram bot/chat and preserves `reply_to`;
- each inbound Telegram update is claimed in the node conversation store before
  dispatch. A duplicate is suppressed and a reused key with different content
  is rejected. An uncertain interrupted turn is never replayed automatically;
  the user deliberately sends a new message;
- envelope-based NATS delivery and the raw HTTP fallback share the same UTF-8
  normalization and idempotency contract.
- exact text matching an action label on the latest live Interaction resolves
  through its opaque action token before Automation or NLU; fuzzy text cannot
  infer authority;
- the backend relay validates and preserves Telegram `inline_keyboard` rows;
  a renderer may not recreate controls from localized prose or silently drop
  them.

Live acceptance on 2026-08-01 proved the current-project frame in Web and the
real Telegram bot with five actions. It also proved the activation invariant:
publishing changed skill files is insufficient while an older runtime process
is still loaded; one explicit activation/reload and a health/behavior probe are
part of the publication acceptance.

Telegram pairing may additionally bind a trusted `webspace_id`. The binding is
persisted with the bot/chat-to-hub route and is copied into every normalized
dialog turn. Runtime selection then follows the persisted Webspace manifest:

- a DEV Webspace executes the DEV skill runtime directly;
- a non-DEV or unbound route executes the installed Workspace runtime;
- a DEV route never tries the installed skill first and therefore cannot
  silently accept a stale Workspace implementation;
- a missing DEV runtime fails explicitly instead of changing runtime authority
  as a fallback.

`adaos dev telegram --webspace <id>` is the local developer entry point for
this binding. It is an execution-authority choice, not a Preview command.

This does not make the transport the owner of project focus. An explicit,
trusted route may carry a Webspace id for diagnostics, but ordinary Telegram
use follows the same Builder project topic after the user selects or names the
project. Preview materialization remains local to the relevant Builder host, so
a Telegram turn cannot silently replace the scenario shown in another host.
Selecting or naming a project through a limited channel changes the durable
conversation focus only. The selected Prototype/Implementation/Publication is
materialized only by a separate `Open in Preview` command on the owning Builder
host. This keeps text clients useful without letting transport routing mutate a
browser session as a side effect.

Builder self-hosting also keeps surface and target identity separate.
`_meta.current_scenario` names the declarative surface currently executing
(`builder`), while `scenario_id` / `project_id` name the project selected for
development. Reusing `scenario_id` for both roles silently redirects compact
or Telegram-capability calls to Builder itself and is therefore invalid. The
source Webspace must be restored from trusted conversation routing metadata;
limited-channel clients do not synthesize DEV suffixes or choose preview hosts.

## Builder Workbench Projection

The default Web Workbench is conversation-first:

- the header reserves its title line for the complete Project title; a separate
  compact Preview indicator carries the exact Preview identity without
  truncating that title;
- the left control/process area shows focused Change, working activity,
  blockers, and the available process commands without consuming title width;
- the central surface is the canonical Builder conversation and dynamic action
  row;
- Preview may occupy a persistent adjacent area when useful;
- Process, Project Overview, Specification, Artifacts, Run detail, and Release
  evidence are requested as contextual views or drawers;
- compact layouts render the same view sequentially or in a modal/drawer.

The former Lifecycle tree becomes **Process view**, a derived provenance and
progress projection:

```text
Change CH-142
  -> Prototype P4 (approved)
       -> Implementation I3 (based on P4)
            -> Trial T2 (passed)
                 -> Release 0.2.46
```

Three selections remain independent:

1. conversation focus: what the next message discusses;
2. inspected ref: what the detail surface displays;
3. Preview target: what the paired Preview materializes.

Selecting a Process item changes focus/inspection only. `Open in Preview` is an
explicit command. The Preview indicator shows `proto:`, `active:`, or
`public:` and the exact revision/version.

## Semantic UI Change IR

AdaOS should maximize changes performed against the deterministic declarative
UI representation before requesting general-purpose code generation.

`adaos.builder.semantic_ui_change.v1` describes bounded operations such as:

- `move` or `reorder` one stable semantic element;
- `rename` visible copy or a label;
- `show` / `hide` an element;
- set a bounded layout or presentation property;
- replace a data mode with declared mock/real binding;
- add/remove a declarative field or widget when its schema is known.

Every operation contains:

- stable target refs, expected source revision, and preconditions;
- normalized intent and resulting acceptance constraint;
- reversible before/after values where practical;
- risk classification and validation requirements;
- originating Review/Issue refs;
- apply result and new Revision ref.

Operations are compiled to ordinary `webui.json` changes and validated by the
existing Web UI ABI. Raw JSON or source diffs remain the compatibility fallback
for changes that cannot be expressed semantically.

Semantic operations must never silently target an element by visible text when
a stable widget/field ref is available.

## Data Modes And Prototype Isolation

The declarative UI addresses logical data contracts. Environment-specific
bindings are separate, typed Preview binding profiles:

| Mode | Intended use | Default Prototype policy |
| --- | --- | --- |
| `mock` | Generated in-memory examples with no external authority | allowed |
| `fixture` | Versioned, sanitized deterministic data | allowed |
| `sandbox` | Isolated connector or test tenant | explicit scoped command |
| `live_readonly` | Real data without modifying effects | explicit policy and visible warning |
| `live` | Real data and modifying effects | forbidden for Prototype; governed Automation/Trial only |

Every binding profile records logical schema, source ref, sensitivity,
capabilities, read/write policy, owner, expiry, and redaction. Preview always
shows its current mode. Switching a compatible profile is an explicit Preview
command and does not rewrite the UI Revision. Changing the logical data
contract is a new semantic/source Revision.

Prototype generation defaults to `mock` or `fixture`. Moving to Automation
requires a mapping from every Prototype data contract to an implementation
binding or an explicit decision to retain it as fixture-only behavior.
Validation rejects undeclared live access, mock-only assumptions presented as
implemented behavior, and interface changes that accidentally bind real writes
during design review. Safe detachment or sanitization is recorded as an
evidence-producing transformer; an LLM claim that data is safe is not evidence.

## Review And Executable Acceptance

Review belongs to a Change, not to browser local storage or a transient page
component.

`adaos.builder.review_anchor.v1` contains:

- `review_id`, `change_id`, author, status, and timestamps;
- artifact/revision ref;
- semantic target ref and optional bounded visual coordinates as secondary
  evidence;
- comment, disposition, resulting Issue/constraint refs;
- resolution Run/Revision and verification evidence.

Its lifecycle is explicit:

```text
local_draft -> submitted
submitted -> accepted_as_constraint | converted_to_issue | dismissed | withdrawn
accepted_as_constraint -> resolved | superseded
converted_to_issue -> resolved | superseded
```

Only an unsent `local_draft` may be hard-deleted. `withdraw_review` removes an
erroneous submitted Review from active model/Run context while preserving a
minimal audit tombstone. `dismiss_review` records why it is not actionable.
An accepted constraint is never silently deleted: `supersede_constraint`
requires a reason and replacement or explicit waiver. Models receive only
active Review and constraint refs, so an erroneous or withdrawn prompt is not
inevitable work.

A Review comment may remain narrative, become an Issue, or compile into a
semantic acceptance constraint such as order, presence, visibility, label,
alignment, data mode, or interaction outcome. The constraint is checked on
later revisions so accepted feedback does not have to be repeatedly explained.

The first executable slice publishes
`adaos.builder.acceptance_constraint.v1`. It deliberately compiles only
structured Review intent supplied by a trusted UI action; narrative text is
never guessed into an operation. Presence, label/property equality,
visibility, sibling order, and declared data mode are evaluated against stable
widget/field refs. Constraints live under the canonical Change, enter every
later development capsule, and are re-evaluated after each Prototype revision.
A violation returns the Change to `changes_requested`; an evaluator never
silently repairs the interface or grants approval.

Client memory or local storage may cache an unsent text draft only. Added
Review records, decisions, and dispositions are backend-owned durable state.
The compatibility browser overlay may still hold unstructured annotations
locally until its store migration; only annotations explicitly compiled through
the typed Review command have durable acceptance semantics in this slice.

## Risk-Aware Deterministic Actions

Builder actions use a small common risk model:

| Risk | Examples | Default admission |
| --- | --- | --- |
| `read` | inspect, search, compare, Preview metadata | automatic |
| `local_reversible` | semantic layout edit with undo | automatic with evidence |
| `isolated_write` | Prototype generation, DEV Run, tests | automatic after scoped Change |
| `trial_activation` | activate immutable candidate in trial slot | explicit policy or approval |
| `workspace_activation` | change installed WorkspaceLock | explicit reviewed action |
| `publication` | move stable channel, external push/release | explicit reviewed action |
| `destructive` | delete, irreversible migration, history rewrite | explicit target-specific approval |

Risk is computed by deterministic policy from the command, target, permissions,
data mode, and environment. Model confidence may contribute rationale but is
not authorization or verification.

Every projected action embeds `adaos.builder.action_risk.v1`. The policy turns
the risk class into explicit side-effect scope and confirmation, approval,
isolation, rollback, and limited-channel admission requirements. `read` may be
an immediate callback; `local_reversible` additionally requires a fresh
generation/precondition; isolated writes and Trial require confirmation; and
Workspace activation, Publication, and destructive operations require the rich
review path. Web and Telegram therefore consume one decision contract instead
of maintaining separate button allowlists.

Risk admission does not by itself make a control executable. A mutating
command is projected only when its declared effect/activity has a registered
adapter that can durably start or queue the exact Codex, Trial, Workspace, or
Publication operation and report its outcome. If that adapter is unavailable,
the shared explanation returns `executor_unavailable`; no channel may expose a
button that only advances Builder state. During migration, withholding such a
control is the required fail-closed behavior.

## Execution, Evidence, And Provenance

The orchestration brain and execution hands are separate:

- Builder conversation/Change services construct intent and context;
- deterministic semantic transformers apply bounded operations;
- Prototype LLM produces declarative revisions only;
- Codex/Skill Factory runs in an isolated task environment;
- evaluators inspect immutable output and append evidence;
- publication/activation services admit exact digests.

A successful Change preserves a trace:

```text
Issue
 -> Change
 -> context packet digest
 -> Run
 -> source/semantic changes
 -> Forge commits
 -> candidate package and dependency lock
 -> Trial evidence and decision
 -> Release
 -> WorkspaceLock activation and observation
```

Evidence is linked by stable refs and digests. UI projections, chat summaries,
Git trailers, and Yjs state must not become competing mutable copies.

## Multi-User Extension Seams

The single-user implementation must not require a shared writable checkout.
Every Change records an immutable base and one owned DEV context.

A future collaboration proposal can carry:

- Change and Issue subset;
- base SourceRef/Release digest;
- semantic operations and source commits;
- required contracts, dependencies, permissions, setup, and migrations;
- Trial/evaluator evidence;
- author, trust scope, signatures, and consent-safe provenance.

The recipient imports the proposal into its own Change/DEV context, evaluates
compatibility, rebases when necessary, and repeats local Trial. Semantic merge
is preferred for declarative operations; contract-aware or source merge is a
fallback. Last-writer-wins shared filesystem mutation is forbidden.

WorkLog-to-Change extraction, trusted groups, public candidate discovery,
evidence aggregation, editions, licensing, and simultaneous multi-version
runtime bindings remain deferred until the single-user loop is repeatable.

## Decision Traceability

The architecture is intentionally split by authority, but its decisions must
remain discoverable from one map:

| Decision | Owning contract | Delivery/evidence owner |
| --- | --- | --- |
| One validated state/transition model drives commands and explanations | [Governed Data-Driven Workflow Model](governed-workflow-runtime.md) | GWR1-GWR5 in the [workflow roadmap](governed-workflow-runtime-roadmap.md) |
| Project is a portfolio/coordination aggregate, not one global stage | [Project Aggregate](#project-aggregate) | Builder Phase 11 and GWR4 project/concurrency proof |
| Conversation is primary; rich views are contextual | [Interaction Contract](#interaction-contract) and [Workbench Projection](#builder-workbench-projection) | Phase 11 in the [Builder Roadmap](builder-roadmap.md) |
| Channel capabilities select presentation but never change command legality | [Channel Capability Boundary](#channel-capability-boundary) and the shared interaction protocol | GWR2 negotiation/conformance evidence |
| Async completion, conversation materialization, and delivery are independent | Shared ReplyRoute/DeliveryAttempt protocol | GWR6 recovery and delivery evidence |
| A message does not automatically equal a Change | [Issue/Change/Run model](#canonical-development-model) | Builder Phase 11 plus GWR4 |
| Multiple open Changes are allowed; focus and write admission are scoped | [Change Concurrency And Focus](#change-concurrency-and-focus) | GWR4/GWR5 conflict and focus evidence |
| Prototype -> Automation -> Trial -> Publication is one governed path | [Builder Change Statechart](#builder-change-statechart) | GWR4 definition and GWR5 end-to-end proof |
| Iterations, experiments, evaluations, and recovery have different authority | [Run purpose contract](#iteration-experiment-evaluation-and-recovery) | Builder Phase 11 and GWR4 conformance tests |
| Deterministic semantic UI changes precede general coding when sufficient | [Semantic UI Change IR](#semantic-ui-change-ir) | Builder semantic-operation tests |
| Prototype data is mock/fixture by default; real effects are gated | [Data Modes And Prototype Isolation](#data-modes-and-prototype-isolation) | Builder data-mode tests and Trial evidence |
| Review is durable, withdrawable, and may become an executable constraint | [Review And Executable Acceptance](#review-and-executable-acceptance) | Builder Review lifecycle and reload tests |
| LLM/Codex receive bounded but sufficient governed context | [Development Capsule](#development-capsule) and [Context Sufficiency](#context-sufficiency-contract) | Builder packet coverage/evaluation evidence |
| DEV, Candidate, Trial, Release, and Workspace are distinct | [Artifact Source, Package, and Activation](artifact-source-package-activation.md) | Artifact pipeline roadmap and release evidence |
| Extraction, trusted groups, and proposal exchange remain extension seams | [Multi-User Extension Seams](#multi-user-extension-seams) and [Governed Evolution](governed-evolution.md) | GWR8/GE4-GE5, deferred until single-user proof |

## Source Of Truth And Projection Rules

- `scenario.yaml` / `skill.yaml` remain canonical artifact manifests.
- Project/Change/Issue/Run records live in backend-owned durable storage or a
  versioned project contract during migration.
- `webui.json` is the active declarative UI source; UI revisions are immutable
  snapshots.
- ProjectRelease, PackageRef, and WorkspaceLock own delivery and activation.
- Conversation ledger owns messages; Change records reference message ids.
- Operational event/evidence stores own facts and receipts.
- Yjs, Workbench frames, Lifecycle/Process trees, status cards, and browser
  local state are disposable projections.

## Migration From Current Builder

The refactoring is additive and compatibility-preserving:

1. expose a canonical `change` projection over the existing persisted
   `change_set`;
2. treat `change_set_id` as an alias of `change_id` and reject divergent ids;
3. link per-turn development-change evidence as `run` records;
4. construct and digest a bounded context packet for Prototype and
   Implementation execution;
5. expose a Project portfolio projection over existing manifest, selection,
   Change, dependency, release, and archive records;
6. adapt Builder Interaction Frames to the shared Interaction/Response,
   capability-negotiation, async envelope, and delivery contracts;
7. expose typed interaction actions with risk and generation preconditions;
8. add semantic Review and UI-operation contracts;
9. project the current Lifecycle as on-demand Process view;
10. make the Workbench conversation-first while retaining feature-parity gates;
11. migrate durable state before removing compatibility fields or tools.

The migration boundary is explicit rather than inferred from whichever UI is
currently visible:

| Existing surface | Disposition | Canonical owner |
|---|---|---|
| `prompt_state.json.workflow` | Adapt, then retire as authority | Project/Change instances plus event journal |
| `change_set` / `change_set_id` | Retain as a bounded compatibility alias | `Change` / `change_id` |
| Per-turn development records | Adapt as evidence only | Immutable `Run` linked to one Change |
| Lifecycle stage buckets | Retain as read-only compatibility data | Dependent Process projection from Change and artifact lineage |
| Builder Interaction Frame | Retain as domain snapshot; adapt all input | Shared ConversationInteraction/InteractionResponse ingress |
| Pending Actions | Adapt through one bounded bridge, then retire duplicate lifecycle | ConversationInteraction plus protected confirmation policy |
| UI selection and Preview target | Retain as disposable view context | Scoped command-context/view refs |
| Browser-local Review drafts | Retain only while unsent | Durable submitted Review and acceptance constraints |
| Direct handler transition rules | Retire after adapter coverage | Pure governed resolver and registered activities |

Compatibility code may translate names and payloads, but it may not decide
legality, infer a different target, weaken risk, or manufacture a second
generation. Every retained adapter has tests and a named retirement owner.

The old functional Builder revision remains an acceptance reference during the
migration. Self-hosting is allowed only after deterministic parity, contract,
scenario, SDK, and browser tests prove that the current DEV Builder can recover
its own control plane without LLM-generated mock replacement.

## Required Acceptance Evidence

The first refactoring slice is accepted only when:

1. existing project state reads as one Change without losing issue or delivery
   evidence;
2. a new request and follow-up create one Change and multiple Runs rather than
   multiple ambiguous Changes;
3. the context packet is bounded, stable-digested, and reconstructable by refs;
4. context actions reject stale generations and publish their risk class;
5. one semantic UI operation produces a reversible validated Revision;
6. typed submitted Reviews are durable and withdrawable; the current
   client-only free-form cache is explicitly treated as an unsent compatibility
   draft with a separate migration;
7. Process selection does not implicitly switch Preview;
8. Prototype, Implementation, Trial, Publication, and Workspace activation
   retain exact provenance;
9. a representative non-Builder scenario completes request -> Change ->
   Prototype or Implementation -> Trial -> Publication in DEV;
10. one semantic interaction negotiates equivalent Web, Telegram/text, and
    handoff presentations without changing its commands or confirmation;
11. an accepted long Run survives restart and failed first delivery, then
    returns its terminal result without repeated mutation;
12. a Project exposes multiple independent Changes and an indirect
    shared-component conflict without inventing one global project stage;
13. the recovered Builder retains the complete functional-parity contract.
