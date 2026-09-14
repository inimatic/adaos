# Builder Roadmap

Status: active target delivery roadmap; implemented mechanisms do not imply
complete user, lifecycle or production acceptance.

Last reviewed: 2026-09-14.

## Document Ownership Audit

This page owns cross-cutting sequencing and the remaining lifecycle,
conversation and context work. The
[corrective BIP register](builder-intent-to-prototype-roadmap.md#current-task-register)
owns small-application understanding, generation, repair and qualification;
the [SDK checklist](builder-sdk-boundary.md#roadmap-and-checklist) owns SDK
migration. Do not maintain duplicate tasks in verification reports.

Only target contracts and delivery checklists belong in these documents.
The single [Builder Engineering Journal](builder-engineering-journal.md)
retains useful dated measurements, audits and failure evidence. Raw model,
HTTP and browser artifacts remain outside documentation. Journal entries
cannot change a gate or define a competing target; link decisions back here.

| Document | Authority |
| --- | --- |
| [Builder intent architecture](builder-intent-to-prototype.md) | Intent/Brief/semantic/context/acceptance contracts |
| [Corrective roadmap](builder-intent-to-prototype-roadmap.md) | Stable BIP tasks and precise implementation boundaries |
| [Evaluation pipeline](builder-evaluation-pipeline.md) | One technological CLI, immutable evidence, metrics and dataset lifecycle |
| [Conversational development](builder-conversational-development.md) | Project/Change/Issue/Run, interactions, authority and context |
| [SDK boundary](builder-sdk-boundary.md) | SDK contract and its remaining migration checklist |
| [Automation skill](builder-automation-skill.md) | Skill/SDK/worker contract, finalization versus acceptance |
| [Preview runtime](builder-preview-runtime.md) | Paired topology, exact identity, reconciliation and materialization |
| [Streaming patches](builder-streaming-patches.md) | Transport/journal/atomic commit; renderer JSONL is compatibility only |
| [Scenario development guide](../guides/builder-scenario-development.md) | Normative stage-aware authoring procedure, not experimental results |
| [Verification guide](../guides/builder-verification.md) | Verification procedure, not permission or a completion verdict |
| [Functional parity fixture](builder-functional-parity.json) | Executable Builder-product compatibility data; not generic model context |
| [Engineering journal](builder-engineering-journal.md) | The only incremental Builder observation record |
| [Client roadmap](client-component-system-roadmap.md) | Client-owned integrity, component contracts and renderer acceptance |

The former dated forensic, model-comparison, context-routing, workflow and
Phase 11 reports are consolidated into the journal. Their observations do not
remain independent plans. Original immutable artifacts are not rewritten.
The functional parity JSON remains unchanged because tests consume it.

## Reading Rules

- Checked tasks establish only their stated scope and referenced acceptance.
- `must` gates supported product/authority behavior; `should` gates a
  maintainable supported path; `could` needs demonstrated benefit;
  `deferred` stays outside the current correction.
- Partial implementation, missing verification, user-paused work and missing
  external dependencies are distinct. Do not turn blocked work into deferred.
- Prototype fixtures/local CRUD, realized source, activated DEV code, accepted
  Automation, installed behavior and publication are different facts.
- Human review remains explicit. Local agent/browser evidence is not human
  production acceptance.
- The following phase anchors group responsibility, not a requirement for
  a fixed sequence of model calls.

## Current Sequence

1. Reconcile contracts and evidence, then stabilize frozen small DEV
   Prototype/Automation behavior and execution latency.
2. Establish clean baseline prerequisites and an undisclosed 40-prompt EN/RU
   set across eight other archetypes before tuning on its results.
3. Complete ownership, Brief, component, semantic and context contracts with
   dependency-aligned should work.
4. Qualify repeated tasks, preservation, cost and latency; compare routes and
   recreate Applications from `scenario_default` without domain solution input.
5. Resume Trial/publication only after explicit user direction, then prove
   installation/update/data preservation and delegated authorization.
6. Evaluate conditional could work; large-project orchestration and preview
   leasing remain deferred.

Current work permits sequential frozen DEV Automation in one existing Builder
and its single owned preview. Trial/publication are paused, not removed from
non-deferred scope. Do not manually repair generated application code,
regenerate approved prototypes, introduce subject-specific Core/Client logic
or remove compatibility without rollback evidence.

## Phase 0. Terminology And Ownership

Application is the user-facing product; Project remains the persisted
development aggregate and SDK identity. DevelopmentSession/Change hold bounded
work, not global mutable project focus. Core owns authority and state; skills
adapt SDK operations to conversation/UI.

Implementation boundary: scoped identities and SDK surfaces exist. Full
Prototype ownership transfer remains
[BIP-07](builder-intent-to-prototype-roadmap.md#bip-07), and residual Brief
understanding remains [BIP-10](builder-intent-to-prototype-roadmap.md#bip-10).

## Phase 1. Read-Only Context Surface

Inspection must use exact project/session refs, return bounded render-safe
data and never start a model, create topology or mutate release acceptance.
Read-only projections and explicit missing-project/runtime errors exist;
all-purpose context and truthful partial progress remain Phase 13 and BIP-02.

## Phase 2. Task And Candidate Model

One bounded Change owns Issues, Runs, immutable Revisions, approval and
delivery evidence. Reconnect restores canonical identity; it must not invent
another task from chat history.

- [ ] `[deferred]` Link completed tasks back to an originating Teacher
  candidate or user idea beyond the current explicit source-ref contract.

## Phase 3. Draft Generation Rails

Generate a minimum working semantic Prototype from ordinary language and a
generic scaffold. Domain packs are explicit compatibility/evaluation inputs,
never hidden dispatch. The BIP register owns generation, clarification and
repair work; schema validity is not task completion.

## Phase 4. Validation And Preview

Validation must cover ABI, declared dependencies, executable commands, data
authority and exact runtime identity. Preview targets are obtained through the
topology SDK: registered W -> W-dev, with only DEV Builder allowed W-dev-dev.
Sequential cases reuse a preview. Implementation exists; remaining reliability
and cleanup are BIP-03, BIP-16 and BIP-22.

## Phase 5. Human-In-The-Loop Apply

Review binds an exact revision, authority and acceptance evidence. Rejection
creates scoped follow-up work without rewriting the accepted artifact.
Prototype approval does not authorize external effects or accept undeclared
Automation obligations.

- [ ] `[deferred]` Add delegated Pending Actions response-handler subscription;
  retain explicit `response_route` in the first supported path.

## Phase 6. Runtime Activation And Rollback

Release/source publication, installation, activation and placement are distinct
operations. Confirm immutable source/task receipts, exact loaded versions and
health, not just changed archive bytes. BIP-28 owns installed lifecycle proof.

### Publication-owned setup design

Setup inputs, secret refs, permissions and verification belong to a declarative
release-owned contract, not generated ad hoc installation code.

- [ ] `[could]` Add a setup assistant that renders missing inputs, secret refs,
  capability review and verification results from that contract.
- [ ] `[deferred]` Generate or execute setup automatically before publication
  and durable source-checkpoint contracts are stable.

## Phase 7. Observation And Repair Loop

Convert failures into owning-layer Issues/Dev Tickets, retain exact candidate
and first attempt, and repair only the admitted scope. BIP-13/BIP-21 own
scoped repairs and bounded review. Reusable guidance requires evidence-gated
promotion; failure text must not become an unconditional model rule.

## Phase 8. Product Experience

The existing Builder provides a searchable/sortable project table, exact
preview, project-scoped Conversation, files, specification, issues and process.
Rich views supplement conversation without replacing canonical work state.

- [ ] `[could]` Add governed open/copy actions for Automation event, stderr and
  result evidence; share one implementation with SDK operator diagnostics.
- [ ] `[could]` Add API-driven prototyping from an explicit OpenAPI/docs
  contract with provenance and auth placeholders, never captured credentials.
- [ ] `[could]` Expose completed work consistently in application, scenario
  and skill history.
- [ ] `[deferred]` Recreate the entire control skill from empty DEV source
  through autonomous programming; keep functional-parity and rollback gates.
- [ ] `[deferred]` Decompose unrelated Prompt IDE/scenario/conversation modules.
  Prototype authority transfer is active BIP-07, not deferred by file size.
- [ ] `[deferred]` Remove Prompt IDE only after replacement and rollback proof.

## Phase 9. Reference Runtime And Evaluation

The technological `adaos builder e2e` is the sole public evaluation entrypoint;
stand helpers must converge on its contracts, not a second product CLI.
Attribution, true task outcomes, human calibration and held-out integrity remain
BIP-01/BIP-04 through BIP-06/BIP-17.

- [ ] `[could]` Add Root push progress after polling/replay reliability is
  qualified. Polling stays the recovery path.

Exact bounded ABI retrieval is active BIP-09. It is not restricted to a
last-resort patch-repair fallback.

## Phase 10. Skill Factory And Isolated Dev Nodes

The bounded worker consumes exact owned source and a typed implementation
brief, then validates and reports evidence. Finalization is not independent
acceptance. No runtime fixture seeding or hidden public-service simulation.

- [ ] `[could]` Add multi-node pools, placement and parallel tasks after
  one-task-per-node isolation and the separately deferred queue/topology
  design are proven. This is conditional expansion, not current work.

## Phase 11. Conversational Development Control Plane

Implementation boundary: state-derived commands, scoped conversations,
Interaction/Response handling, workflow activities and recovery projections
exist. Cross-channel human acceptance and complete durable continuation are
not established by those mechanism tests.

- [ ] `[must]` **BC-11.1** Derive the dependent Prototype -> Automation ->
  verification -> Trial -> Publication bridge from one governed snapshot.
  Prose actions must create durable input-required continuations; preserve
  lineage, generation and exact delivery identity, and prioritize continuation
  over optional inspection. Delivery qualification is blocked by the pause.
- [ ] `[must]` **BC-11.2** Complete human wide/compact comparison, generated
  Preview-link acceptance and one mutating Telegram callback on the target
  deployment. Existing automated/local ingress proof is not this gate.
- [ ] `[deferred]` Build a workflow/conversation studio only after the static
  contract and runners are stable.
- [ ] `[deferred]` Migrate legacy unsent browser-overlay drafts into submitted
  Review. New submitted reviews use durable Change-owned records now.

Bounded visual review and sanitized feedback/golden rules are BIP-20/BIP-21/
BIP-34. Applications Prototype, Automation and delivery proof is BIP-29/BIP-28,
not a duplicate Phase 11 checklist. Broad data sandboxing/concurrent shared
versions remain distinct from the isolated candidate execution required today.

## Phase 12. Project Composition And Scoped Development

Implementation boundary: explicit Project composition, source authority,
preview relations and lifecycle adapters exist; the complete chat-created
application delivery chain and all projection/recovery paths remain partial.

- [ ] `[must]` **BC-12.1** Preserve one resumable Change/Candidate identity from
  chat/template creation through preview, Trial, stable promotion and source
  publication. Qualify the complete chain after BIP-28 resumes.
- [ ] `[should]` **BC-12.2** Project DEV/beta/stable source, package, release,
  migration, health and cleanup accurately in Process and diagnostics.
- [ ] `[should]` **BC-12.3** Merge governed Candidate/Trial events created outside
  the current Automation session into delivery/Process continuation actions.
- [ ] `[should]` **BC-12.4** Qualify detached YDoc ownership and clean Windows
  shutdown for synchronous chat-driven materialization.
- [ ] `[should]` **BC-12.5** Open an exact stable Project deterministically using
  Workspace authority, without a generation request or unnecessary model packet.
- [ ] `[should]` **BC-12.6** Complete application catalog projections with
  profiles, localized categories, tags, scopes and advanced raw-component view.
- [ ] `[should]` **BC-12.7** Export/import local artifact groups with exact
  cross-node verification receipts.
- [ ] `[could]` Add a bounded retained-Trial selector after routing, expiry,
  conflict and cleanup proof. Selection never copies Trial into stable runtime.
- [ ] `[deferred]` Add external artifact groups/object storage/MCP resolution
  after the native local artifact contract is validated.
- [ ] `[deferred]` Expand to remote multi-component transactional publication,
  shared-dependency accounting and broad data sandboxing in their owning
  artifact/registry roadmaps. This does not defer the small installed lifecycle
  and isolated Trial authority required by BIP-28.

## Phase 13. Context-Compiled Builder Execution

Implementation boundary: bounded capsules and context refs exist. Complete
cold replay across every purpose/initiator and evidence-gated learning remain
open. A passing application generation does not establish these platform gates.

- [ ] `[must]` **BC-13.1** Resolve every qualification/planning/Automation/
  validation/re-entry packet from exact DevelopmentSession subjects, purpose,
  audience, policy, source generation and agent profile.
- [ ] `[must]` **BC-13.2** Consume domain handoffs as immutable read-only refs;
  return clarification/capability/SDK/Core proposals without changing the
  initiating application's contract.
- [ ] `[must]` **BC-13.3** Prove cold restoration of a Dev Ticket project and
  Research Workbench ImplementationTrack after process/model restart,
  including write scope and subscription accounting. Its publication/Trial
  portion remains blocked until delivery resumes.
- [ ] `[must]` **BC-13.4** Route run results/rejections/findings into episodic
  memory candidates only; require independent evidence-gated promotion and
  rollback before reuse as generic repair/SDK guidance.
- [ ] `[should]` Enable warm role/focus caches only after cold-replay equivalence
  and matched-budget benefit.
- [ ] `[could]` Add model-assisted context ranking after deterministic identity,
  authority, trust, dependency and freshness filtering; retain a fallback plan.

## Builder Workbench Lifecycle Surface

The workbench must preserve functional project/context/preview/issue/process/
conversation/review controls through its own redesign. Its product-specific
parity fixture is not a template or benchmark oracle for other applications.

- [ ] `[should]` Complete the remaining human comparison and remove a temporary
  reference scenario only when no longer needed for visual regression.
- [ ] `[deferred]` Extend local Issues to federated multi-user extraction and
  proposal exchange after the single-user lifecycle is qualified.

## Closure Boundary

Closing the small corrective register alone is insufficient. Builder readiness
also requires the active parent and SDK obligations for the supported scope,
Client contract conformance, explicit human gates and installed lifecycle
proof. Conditional expansion must receive an evidence-backed decision.
No dated journal entry or checked compatibility fixture can substitute for
that acceptance.
