# Governed Evolution Roadmap

Status: cross-cutting product and architecture roadmap.

Last reviewed: 2026-08-18.

AdaOS is intended to make software change a governed, observable lifecycle:

```text
human or runtime signal
  -> structured work
  -> bounded implementation
  -> validation and approval
  -> release and activation
  -> runtime evidence
  -> repair or the next change
```

This roadmap orders the proof needed to extend that lifecycle from one managed
runtime to personal Builders and, later, to trusted collaboration between
independent Builders. It does not replace the domain roadmaps and does not
promise that a later milestone is implemented merely because its architecture
is described.

## Reading And Authority Rules

1. This document owns the milestone order, cross-domain outcomes, and admission
   and exit proof gates for governed evolution.
2. Domain architecture documents own terms, invariants, and responsibility
   boundaries inside their domains.
3. Domain roadmaps own implementation sequencing and low-level checklists. A
   task has one canonical owner; this roadmap links to that owner instead of
   copying the task.
4. [MVP Roadmap](../mvp_roadmap.md) owns the active sequence to the current MVP.
   [Roadmap Inventory](roadmap-inventory.md) identifies the authoritative
   roadmap for each technical domain.
5. [Builder Roadmap](builder-roadmap.md) owns the local idea-to-release vertical
   slice. [Skill Factory](skill-factory.md) owns isolated autonomous
   implementation. [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md)
   owns distribution, installation, publication, and registry operations.
6. [Development Signals Roadmap](development-signals-roadmap.md) owns the
   feedback-intake, scoped-signal, Dev Ticket, conversational-disambiguation,
   and ticket-to-Builder handoff sequence before full Issue-first support is
   admitted.
7. [Incident Registry](incident-registry.md), [Operational Event Model](operational-event-model.md),
   and [Post-Deploy E2E Testing](post-deploy-e2e-testing.md) own runtime feedback
   and verification contracts. [Conversation Architecture](conversation-and-channel-architecture.md)
   owns conversational transport and durable thread boundaries.
8. A checked item means that the stated cross-cutting proof exists at the
   recorded maturity. It does not imply production acceptance unless the
   evidence explicitly says so.
9. When this document and a domain owner disagree on implementation state, the
   domain owner is authoritative. Correct the summary here; do not fork the
   detailed task.

## Progress Model

Four independent fields prevent a checkbox from overstating readiness.

### Priority

- `[must]`: required to pass the current milestone exit gate.
- `[should]`: required before broad or unattended use, but not required for the
  narrow milestone proof.
- `[could]`: useful experiment or improvement that must not displace the gate.
- `[deferred]`: deliberately assigned to a named later milestone or proof.

### Maturity

Use the highest state supported by linked evidence:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

`validated-local` is repeatable on a development machine. `validated-stand`
requires a representative independent deployment or design partner.
`production-accepted` requires operational evidence and an explicit acceptance
decision.

### Horizon

- `now`: part of the active MVP or current milestone.
- `next`: admitted when the current milestone exits.
- `later`: ordered, but not admitted for implementation.
- `long-term`: an architectural direction whose value or feasibility remains
  to be proven.

### Evidence

Every exit decision must link to durable evidence: a test or evaluation report,
release record, operation/incident record, acceptance decision, measured trial,
or reproducible commit and command. Plans, UI screenshots, and an implementation
checkbox alone are not exit evidence. Evidence should record date, revision,
environment, result, and the responsible acceptance decision.

Checklist entries below use stable `GE<n>-<nn>` identifiers. Their checkbox is
only the cross-cutting status; the linked owner remains the source of detailed
work and evidence.

## Milestone Sequence

| Milestone | User-visible outcome | Current assessment | Horizon |
| --- | --- | --- | --- |
| GE0 | A governed runtime can install, activate, observe, and recover a capability. | `validated-stand` bounded artifact slice; default rollout and broader runtime acceptance open | now |
| GE1 | AdaOS can be delivered and supported as a managed deployment. | `implemented`; exit proof open | now / next |
| GE2 | One user can take a request to a validated, reversible release through a Personal Builder. | `validated-stand` bounded artifact slice; autonomous Builder-from-empty acceptance open | next |
| GE3 | Development Signals, Dev Tickets, and Issues, rather than chat transcripts, become durable support and repair work. | `hypothesis`; Development Signal and Dev Ticket slice now specified, full Issue aggregate not admitted | later |
| GE4 | Independent Builders collaborate without sharing a writable DEV workspace. | `hypothesis`; not admitted | later |
| GE5 | A verified capability can be reused across deployments with provenance and evidence. | `hypothesis`; not admitted | long-term |

The assessment reports the most mature meaningful slice, not completion of the
milestone. The exit-proof column in the milestone section remains decisive.
GE0 is the active cross-cutting gate; GE1 and GE2 may continue bounded proof
work where they do not bypass GE0 acceptance.

The current evidence is
[Artifact Pipeline Local Evidence — 2026-07-24](artifact-pipeline-local-evidence-2026-07-24.md):
it includes deterministic packages, dependency and permission rejection,
transactional activation/rollback, subscription update, the built-in
LLM → isolated Codex → trial → publication path, deployed backend routes, and a
fresh empty-cache/Workspace activation through the external package backend.
Its maturity remains explicitly below broad production acceptance.

Milestones are cumulative. Work may explore a later milestone, but it must not
be reported as the active delivery goal until the preceding exit gate has
evidence.

## GE0. Governed Runtime Foundation

**User outcome:** a capability can enter a user runtime through explicit
lifecycle rails, expose health and behavior, and be rolled back or repaired
without editing runtime state by hand.

**Admission gate:** AdaOS has a runnable Root/runtime, skill and scenario
descriptors, and a development lifecycle on which the governance contract can
be tested.

**Exit proof gate:** one representative skill and scenario complete a
repeatable install/validate/prepare/test/activate/observe/rollback path; policy
denials and failures produce durable, user-actionable evidence.

- [x] `[must]` `GE0-01` Record the end-to-end lifecycle proof and its rollback
  evidence. Owner: [MVP Roadmap](../mvp_roadmap.md),
  [Skill Runtime Lifecycle](../skill_runtime.md).
- [ ] `[must]` `GE0-02` Demonstrate stable runtime projections, readiness, and
  bounded recovery rather than timer-driven replacement of whole UI state.
  Owner: [Projection Subscription Roadmap](projection-subscription-roadmap.md),
  [Runtime Guarding](runtime-guarding.md).
- [ ] `[should]` `GE0-03` Tie operational events, incidents, and post-deploy
  checks to the exact activated revision. Owner:
  [Operational Event Model](operational-event-model.md),
  [Post-Deploy E2E Testing](post-deploy-e2e-testing.md).
- [ ] `[could]` `GE0-04` Add operator summaries that reduce diagnosis time
  without making raw logs a product interface. Owner:
  [UI Runtime Diagnostics](ui-runtime-diagnostics.md).
- [ ] `[deferred]` `GE0-05` Defer autonomous cross-deployment repair to GE3;
  GE0 only proves governed local recovery. Owner: [Builder Roadmap](builder-roadmap.md).

**Non-goals:** Personal Builder autonomy, an Issue system, multi-Builder
collaboration, marketplace economics, or claiming production readiness from a
single local smoke test.

## GE1. Managed Deployment And Activation

**User outcome:** a user or delivery partner can install an identified release,
complete its setup or migration, verify the result, and receive support with a
known rollback path.

**Admission gate:** GE0 exit evidence exists, and publication and installation
use the same versioned artifact identity as runtime activation.

**Exit proof gate:** a representative external or stand deployment records its
release, setup/migration decisions, post-install verification, rollback target,
activation cost, and support outcome. ReDevice may supply this proof, but is one
endpoint/channel experiment rather than the identity of AdaOS.

- [ ] `[must]` `GE1-01` Prove publication-to-install artifact identity and a
  versioned release record. Owner:
  [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md).
- [ ] `[must]` `GE1-02` Define and exercise setup, migration, credentials,
  environment checks, human-decision points, verification, and rollback as a
  governed installation plan. Owner: [Builder Roadmap](builder-roadmap.md),
  [Skill Activation And Scenario Binding](skill-activation-and-scenario-binding.md).
- [ ] `[must]` `GE1-03` Persist a delayed post-install result that can route a
  failed verification or migration back to a development owner. Owner:
  [Post-Deploy E2E Testing](post-deploy-e2e-testing.md),
  [Incident Registry](incident-registry.md).
- [ ] `[should]` `GE1-04` Measure activation time, setup failure rate, rollback
  rate, and support load for each trial channel. Owner:
  [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md).
- [ ] `[could]` `GE1-05` Use ReDevice as a falsifiable service-assisted
  activation and endpoint proof. Owner: [Device Access Roadmap](device-access-roadmap.md).
- [ ] `[deferred]` `GE1-06` Defer marketplace and network-effect claims until
  GE5 proves cross-deployment capability reuse. Owner:
  [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md).

**Non-goals:** selecting one channel as the permanent go-to-market model,
automatic authoring of every setup procedure, or treating a successful upload
as a successful activation.

## GE2. Personal Builder

**User outcome:** one user can describe a bounded change and take it through
draft, implementation, preview, validation, approval, versioning, publication,
activation, and rollback in the user's own DEV space.

**Admission gate:** GE1 provides release, setup, verification, and rollback
contracts that Builder can target rather than bypass.

**Exit proof gate:** Builder is reproducibly created or restored from an empty
DEV project and completes an issue/request-to-release scenario on a clean
machine through public SDK boundaries, isolated implementation, Git evidence,
local/stand validation, and rollback. Legacy Prompt IDE retirement is a
separate acceptance decision.

- [ ] `[must]` `GE2-01` Pass the canonical Builder end-to-end acceptance flow,
  including visible failure evidence. Owner: [Builder Roadmap](builder-roadmap.md).
- [ ] `[must]` `GE2-02` Enforce SDK-only skill/scenario integration at the
  product boundary. Owner: [Builder SDK Boundary](builder-sdk-boundary.md).
- [ ] `[must]` `GE2-03` Prove bounded autonomous implementation plus User Hub
  validation without granting a worker control of the active runtime. Owner:
  [Skill Factory](skill-factory.md).
- [ ] `[should]` `GE2-04` Validate setup authoring separately from setup
  execution and preserve explicit approval for credentials, destructive
  migration, new permissions, and external I/O. Owner:
  [Builder Roadmap](builder-roadmap.md).
- [ ] `[could]` `GE2-05` Compare multiple implementation agents or models
  against the same typed request and acceptance evidence. Owner:
  [Skill Factory](skill-factory.md).
- [ ] `[deferred]` `GE2-06` Defer large-module decomposition unless a touched
  seam blocks the acceptance flow; track it as a dedicated later refactoring
  preparation effort. Owner: [Builder Roadmap](builder-roadmap.md).
- [ ] `[should]` `GE2-07` Accept a scoped Dev Ticket as Builder input
  through the same typed context whether the user chooses autonomous repair or
  opens an interactive Builder session. Owner:
  [Development Signals Roadmap](development-signals-roadmap.md),
  [Builder Roadmap](builder-roadmap.md).
- [ ] `[should]` `GE2-08` Materialize Builder work for installed, catalog,
  remote, or read-only artifacts from a workspace ticket without requiring a
  pre-existing DEV checkout. Owner:
  [Development Signals Roadmap](development-signals-roadmap.md),
  [Skill Factory](skill-factory.md).

**Non-goals:** unrestricted repository autonomy, shared writable DEV trees,
removing human authority at consequential boundaries, or preserving Prompt IDE
and Builder as two permanent development systems.

## GE3. Issue-First Support And Repair

**User outcome:** a user can speak naturally while durable, deduplicated Issues
carry the work, decisions, acceptance criteria, execution timing, release links,
and runtime evidence across conversations and restarts.

**Admission gate:** GE2 can accept a typed unit of work and return structured
implementation, validation, publication, and failure evidence.

**Exit proof gate:** a Support Agent turns user input or a deterministic symptom
into a reviewable Issue, handles duplicates and clarification, obtains consent
for immediate or deferred execution, hands accepted work to Builder, and closes
the Issue only against release and verification evidence.

- [ ] `[must]` `GE3-01` Specify the AdaOS Issue aggregate, lifecycle,
  deduplication, acceptance, consent, provenance, and access-control contracts.
  Owner: future Issue architecture; integrate with
  [Conversation Architecture](conversation-and-channel-architecture.md).
- [ ] `[must]` `GE3-02` Implement Support Agent intake from NLU to a typed Issue
  and Builder handoff, keeping chat as an interface rather than durable work
  state. Owner: future Support Agent roadmap; integrate with
  [Builder](builder.md).
- [ ] `[must]` `GE3-03` Link Issue, Builder task, commits, release, activation,
  symptoms, incidents, and verification evidence without duplicating their
  domain records. Owner: future Issue architecture,
  [Operational Event Model](operational-event-model.md).
- [ ] `[should]` `GE3-04` Allow periodic health monitoring only by policy and
  consent; promote recurring analysis into deterministic symptom checkers.
  Owner: [Incident Registry](incident-registry.md),
  [Runtime Guarding](runtime-guarding.md).
- [ ] `[could]` `GE3-05` Add proactive support suggestions with explicit
  suppression, postponement, and false-positive feedback. Owner: future Support
  Agent roadmap.
- [ ] `[deferred]` `GE3-06` Defer issue exchange across trust groups until GE4
  defines visibility and proposal boundaries. Owner: GE4.
- [ ] `[must]` `GE3-07` Promote workspace- and artifact-scoped Dev Tickets into
  accepted Issues only after triage establishes problem scope, authority,
  acceptance criteria, and owning lifecycle. Owner:
  [Development Signals Roadmap](development-signals-roadmap.md), future Issue
  architecture.
- [ ] `[must]` `GE3-08` Preserve the boundary between Feedback Skill intake, NLU
  Teacher correction, Builder development conversation, and Support Agent
  issue flow through typed refs instead of shared chat state. Owner:
  [Development Signals Roadmap](development-signals-roadmap.md),
  [NLU Teacher Evolution Roadmap](nlu-evolution-roadmap.md),
  [Builder Roadmap](builder-roadmap.md).
- [ ] `[must]` `GE3-09` Prove one runtime compatibility finding, such as a
  legacy receiver declaration gap, moving from deterministic evidence to
  Development Signal, Dev Ticket, Pending Action, Builder repair, validation,
  and closure by version or explicit deferral. Owner:
  [Development Signals Roadmap](development-signals-roadmap.md),
  [Runtime Guarding](runtime-guarding.md).

**Non-goals:** making the LLM an unreviewable authority, continuous raw-log
analysis as the normal monitoring path, replacing deterministic checks with
prompts, or equating every conversation with an Issue.

## GE4. Trusted Multi-Builder Collaboration

**User outcome:** users in a consented trust group can discover related planned
or completed work, coordinate component boundaries, and integrate proposals
without any agent writing into another user's DEV workspace.

**Admission gate:** GE3 provides durable Issues and provenance, and every
Builder has an isolated DEV space derived from identified public or private
artifact revisions.

**Exit proof gate:** two independent Builders propose related changes, detect a
contract or requirement relationship, exchange a bounded proposal, validate it
locally, resolve or reject conflicts, and preserve authorship, consent, and
release evidence without shared write access.

- [ ] `[must]` `GE4-01` Specify trust groups, consent, visibility, revocation,
  proposal, and audit contracts. Owner: future collaboration architecture;
  align with [Personalization, Identity, And Access Roadmap](personalization-identity-access-roadmap.md).
- [ ] `[must]` `GE4-02` Enforce one writable DEV workspace per Builder and
  proposal-based exchange through immutable revisions or forge refs. Owner:
  [Skill Factory](skill-factory.md).
- [ ] `[must]` `GE4-03` Detect related Issues, planned contract changes, and
  incompatible proposals at component boundaries before integration. Owner:
  future collaboration architecture and future Issue architecture.
- [ ] `[should]` `GE4-04` Support LLM-guided integration that presents user
  impact, alternatives, validation evidence, and unresolved decisions rather
  than raw merge mechanics. Owner: [Builder Roadmap](builder-roadmap.md).
- [ ] `[could]` `GE4-05` Explore joint design sessions whose durable outputs
  are Issues, contracts, proposals, and decisions. Owner:
  [Conversation Architecture](conversation-and-channel-architecture.md).
- [ ] `[deferred]` `GE4-06` Defer global discovery and public exchange until
  GE5 proves safe reuse inside bounded trust groups. Owner: GE5.

**Non-goals:** a new GitHub implementation, shared writable directories,
automatic merging based on trust alone, cross-group data disclosure, or hiding
unresolved semantic conflicts behind a clean source merge.

## GE5. Verified Capability Network

**User outcome:** users can reuse a verified capability across deployments as a
governed unit that carries behavior, semantic UX, contracts, tests,
compatibility, setup/migration, provenance, and runtime evidence.

**Admission gate:** GE4 proves bounded proposal exchange and independent local
validation across at least two Builders and deployments.

**Exit proof gate:** at least one capability is transferred between independent
deployments, passes compatibility and setup gates, preserves provenance and
policy, produces comparable runtime evidence, and can be upgraded or rolled
back without relying on the originating source workspace.

- [ ] `[must]` `GE5-01` Specify the verified capability package and its links to
  existing skill, scenario, UI, setup, test, provenance, and release records.
  Owner: [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md).
- [ ] `[must]` `GE5-02` Prove independent compatibility validation,
  installation, observation, upgrade, and rollback for a reused capability.
  Owner: [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md),
  [Post-Deploy E2E Testing](post-deploy-e2e-testing.md).
- [ ] `[must]` `GE5-03` Preserve consent, authorship, policy, dependency, and
  evidence lineage across every reuse and derived proposal. Owner: future
  collaboration architecture and
  [Operational Event Model](operational-event-model.md).
- [ ] `[should]` `GE5-04` Define evidence quality, expiry, comparability, and
  privacy rules before ranking or recommending capabilities. Owner:
  [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md).
- [ ] `[could]` `GE5-05` Test discovery, recommendation, and commercial models
  only after verified reuse demonstrates user value. Owner:
  [Registry and Operations Roadmap](registry-marketplace-operations-roadmap.md).
- [ ] `[deferred]` `GE5-06` Defer claims of a marketplace, proprietary evidence
  graph, or network effects until repeated production evidence supports them.
  Owner: future product strategy.

**Non-goals:** distributing source diffs without lifecycle evidence, global
trust by default, centralizing all user data, ranking on unverified telemetry,
or treating a published artifact as a portable capability before installation
and operation are proven.

## Review Cadence

Review this roadmap at milestone admission or exit, and when a domain roadmap
changes a dependency that invalidates a gate. A review should:

1. update maturity and horizon from linked evidence;
2. identify the one active cross-cutting exit gate;
3. add missing owner links rather than new low-level tasks;
4. move deliberately postponed work to `[deferred]` with a named destination;
5. remove obsolete summaries when their domain owner supersedes them; and
6. record rejected hypotheses so that future work does not silently revive
   them.

The roadmap is successful when it makes the next proof and its owner obvious,
not when it accumulates the largest checklist.
