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

### Revision

A `Revision` is an immutable artifact snapshot. Prototype UI revisions,
Implementation snapshots, and publication source snapshots use typed refs and
record their source Change and Run.

### Trial And Release

Trial proves one immutable candidate in an isolated activation context. Release
is a promoted immutable `ProjectRelease`; Publication is the decision and
operation that moves a channel pointer to it. Neither is an editable phase.

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

This does not make the transport the owner of project focus. An explicit,
trusted route may carry a Webspace id for diagnostics, but ordinary Telegram
use follows the same Builder project topic after the user selects or names the
project. Preview materialization remains local to the relevant Builder host, so
a Telegram turn cannot silently replace the scenario shown in another host.

## Builder Workbench Projection

The default Web Workbench is conversation-first:

- the header always shows Project, focused Change, working activity, and exact
  Preview identity;
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
explicit command. The header shows `proto:`, `active:`, or `public:` and the
exact revision/version.

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

## Source Of Truth And Projection Rules

- `scenario.yaml` / `skill.yaml` remain canonical artifact manifests.
- Change/Issue/Run records live in backend-owned durable storage or a versioned
  project contract during migration.
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
5. expose typed interaction actions with risk and generation preconditions;
6. add semantic Review and UI-operation contracts;
7. project the current Lifecycle as on-demand Process view;
8. make the Workbench conversation-first while retaining feature-parity gates;
9. migrate durable state before removing compatibility fields or tools.

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
6. the Review anchor contract is versioned and the current client-only cache is
   explicitly treated as a compatibility draft; durable backend migration is a
   separate scheduled slice;
7. Process selection does not implicitly switch Preview;
8. Prototype, Implementation, Trial, Publication, and Workspace activation
   retain exact provenance;
9. a representative non-Builder scenario completes request -> Change ->
   Prototype or Implementation -> Trial -> Publication in DEV;
10. the recovered Builder retains the complete functional-parity contract.
