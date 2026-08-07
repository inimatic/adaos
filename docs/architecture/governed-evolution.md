# Governed Evolution

Status: target architecture and strategic direction. This document defines the
shared vocabulary, boundaries, and long-term shape of governed software
evolution in AdaOS. It is not a delivery commitment and does not replace the
domain roadmaps linked below.

## Purpose

AdaOS is intended to make software change a continuous, governed product
capability rather than a sequence of isolated code-generation sessions. The
long-term direction is a system in which a person can express a need in ordinary
language, receive a reviewable implementation, activate it safely, and turn
runtime evidence into the next improvement.

The durable unit of work is an AdaOS Issue. Conversation is an interface for
creating, clarifying, prioritizing, and reviewing that work; it is not the
source of truth for development state. Generated code is an implementation
artifact; it is not sufficient evidence that the requested capability works.

This direction is deliberately broader than the current Builder implementation.
It gives existing architecture tracks a common destination without claiming
that Support Agent, cross-user collaboration, or a capability network already
exists.

## Product, Platform, Channel, And Network

These concepts must remain distinct:

| Concept | Role |
| --- | --- |
| AdaOS runtime | The platform foundation that installs, activates, executes, observes, guards, and rolls back skills and scenarios. |
| Managed deployment | A product/service offer in which AdaOS is installed and operated for a user or organization. |
| Deployment profile | Versioned topology, role, policy, application, skill, and integration defaults for a class of managed deployments such as Home, Campus, or Enterprise. It is configuration and composition, not a separate runtime. |
| Solution pack | A domain-oriented composition of skills, scenarios, workflows, policies, templates, and UI projections that uses the shared AdaOS lifecycle. |
| Solution agent or workbench | A user-facing role and interaction surface over solution packs, such as aResearcher. It does not own a second persistence or workflow truth. |
| Personal Builder | The user-facing product for turning approved work into governed AdaOS artifacts in an isolated DEV space. |
| Support Agent | The proposed issue-intake and support role that turns human and machine signals into actionable, non-duplicated work. |
| Activation channel | A way a deployment reaches users. Direct local installation, institutional deployment, or a device reuse program are channel experiments, not definitions of AdaOS. |
| Trusted development group | A consent-based collaboration boundary for exchanging proposals and evidence between independently owned DEV spaces. |
| Verified capability network | A long-term possibility in which reusable capability packages can move between deployments with contracts, provenance, and validation evidence. |

No single deployment offer, endpoint class, UI, or distribution experiment is
the platform architecture. Conversely, calling a future capability a network
does not imply that a marketplace or network effect has been validated.

The portfolio-level definitions, named solution directions, and maturity
vocabulary are governed by the [AdaOS Product Model](../product/index.md) and
[Solution Directions](../product/solution-directions.md).

## Target Evolution Loop

The target system connects existing AdaOS planes through typed records:

```text
Human or machine signal
  -> NLU / Support intake
  -> durable AdaOS Issue
  -> Builder task
  -> isolated realization
  -> deterministic validation and approval
  -> publication and activation
  -> runtime evidence
  -> repair, follow-up Issue, or closure
```

The loop has the following invariants:

1. A signal is evidence, not automatically a defect or a command to change the
   system.
2. NLU and Support intake classify, deduplicate, clarify, and preserve the
   original signal before development begins.
3. The AdaOS Issue records the agreed problem, scope, acceptance criteria,
   urgency, authority, and links to evidence.
4. Builder realizes approved work through AdaOS development contracts; it does
   not mutate production state directly.
5. Realization occurs in an isolated DEV space and produces reviewable
   artifacts and reproducible verification evidence.
6. Publication and activation are separate governed transitions. A valid build
   is not automatically authorized for production use.
7. Runtime evidence may confirm acceptance, reveal a regression, or create a
   follow-up Issue. It never silently edits the original request or deployed
   artifact.

## Roles And Authority

### Person

A person reports a problem, requests a capability, supplies context, answers
material clarifications, and exercises decisions reserved by policy. People do
not need to maintain issue-tracker mechanics or inspect code for routine work,
but they must retain visibility and meaningful control over consequential
changes.

### NLU And NLU Teacher

NLU identifies the meaning and target of an utterance. NLU Teacher improves
recognition and distinguishes a descriptor correction from a missing
capability. It may propose `descriptor_fix`, `development_task`, or other typed
candidates, but it does not own development execution. Its current contracts
remain defined by [NLU Teacher LLM](../concepts/nlu-teacher-llm.md) and the
[NLU Roadmap](../concepts/nlu-roadmap.md).

### Support Agent

Support Agent is the proposed owner of issue flow, analogous to a capable
second-line support function rather than a general-purpose Case Manager. Its
responsibilities are expected to include:

- recognizing whether a signal should become an Issue;
- finding possible duplicates and related work;
- rewriting a report into a precise problem statement without losing the
  reporter's original words;
- requesting clarification when acceptance or scope is materially ambiguous;
- assessing impact, urgency, confidence, and likely owning component;
- asking whether eligible work should execute immediately or remain queued;
- handing accepted implementation work to Builder through a typed contract;
- reporting progress, decisions, verification, and residual risk to the person;
- reacting to deterministic error and symptom-checker signals when policy and
  consent allow it.

Support Agent must not infer consent for invasive monitoring, broaden an Issue
silently, approve its own high-risk change, or treat model confidence as
verification.

### Builder

Builder owns design and realization of the requested AdaOS change. It consumes
an Issue or another governed development request and returns linked artifacts,
commits, tests, validation results, release candidates, and failure evidence.
Its authoritative scope and implementation pipeline remain in
[Builder](builder.md), [Builder Roadmap](builder-roadmap.md), and
[Skill Factory](skill-factory.md).

### Deterministic Runtime And Policy

AdaOS owns validation, permission checks, publication and activation gates,
runtime isolation, observation, quarantine, and rollback. Human or policy
approval must be separate from model recommendation wherever consequences,
uncertainty, privacy, or irreversible effects require it. Relevant controls are
defined in [Runtime Guarding](runtime-guarding.md),
[Pending Actions](pending-actions.md), and
[Authority And Degraded Mode](authority-and-degraded-mode.md).

### Symptom Checkers And Operational Evidence

Deterministic checks should identify known symptoms cheaply and reproducibly.
Models may help discover patterns and propose new checkers, but routine Support
Agent reactions should consume structured incidents and checker results instead
of repeatedly interpreting unrestricted raw logs. The operational evidence
contract belongs to [Incident Registry](incident-registry.md) and the
[Operational Event Model](operational-event-model.md).

## AdaOS Issue Model

An AdaOS Issue is a durable, addressable work record independent of whether the
affected skill or scenario already has a DEV checkout. It may refer to a public
artifact, an installed production version, a runtime component, a proposed new
capability, or a cross-component contract.

The minimum conceptual record is:

- stable issue identity and status;
- reporter and owning trust scope;
- original signal plus normalized problem statement;
- affected artifact, version, deployment, or contract when known;
- severity, priority, confidence, and duplicate/relationship links;
- consent and data-handling constraints;
- acceptance criteria and required approval class;
- linked Builder tasks, DEV spaces, commits, revisions, releases, and rollbacks;
- structured runtime evidence and verification results;
- decision history and closure reason.

The target lifecycle is:

```text
observed
  -> triaged
  -> needs_clarification | accepted | duplicate | rejected
  -> planned
  -> in_realization
  -> validation
  -> awaiting_approval | ready
  -> published
  -> activated
  -> observing
  -> resolved | reopened | superseded
```

Not every Issue traverses every state. A documentation correction may close
after validation; a production repair may require activation, an observation
window, and rollback readiness. Duplicate, rejected, deferred, and superseded
records remain durable so that later agents can understand prior decisions.

The concrete schema, storage model, APIs, and retention policy are future
contract work. This document establishes only that the Issue, not chat history
or a browser projection, is the durable coordination record.

## Personal Builder Isolation

Each Personal Builder owns a separate DEV space. A public skill or scenario may
seed that space, but it does not create a shared writable directory.

Required invariants are:

- one Builder task has an explicit workspace and base artifact revision;
- another user's Builder cannot mutate that workspace implicitly;
- generated artifacts, tool permissions, secrets, and runtime access are scoped
  to the task and trust boundary;
- public repositories receive reviewed publication proposals, not concurrent
  writes from unrelated agents;
- commits and releases preserve the Issue, authoring agent, base revision,
  validation, and approval provenance;
- conflicts are explicit integration work, never last-writer-wins behavior.

The execution and sandbox boundary is owned by [Skill Factory](skill-factory.md)
and [Local Skill Factory Worker](local-skill-factory-worker.md). Builder's SDK
boundary is owned by [Builder SDK Boundary](builder-sdk-boundary.md).

## Trusted Development Groups

Trusted groups are a possible collaboration layer over isolated DEV spaces.
They allow participants to expose selected Issues, planned contract changes,
capability proposals, and evidence to one another by consent. They do not grant
all agents write access to a common checkout.

A collaboration flow should use structured proposals:

1. publish a bounded change or design proposal with base version and affected
   contracts;
2. discover related Issues, overlapping ownership, and incompatible plans;
3. negotiate interface changes through explicit decisions and approvals;
4. import or merge into the recipient's DEV space;
5. validate against the recipient's policy and environment;
6. retain provenance from proposal through local release.

This creates room for LLM-guided frequent integration and joint design at
component boundaries while keeping ownership, accountability, and rollback
local. The proposal protocol, group identity, discovery model, and conflict
semantics are hypotheses to specify after the single-Builder loop is proven.

## Verified Capability Package

The intended unit of reuse is a verified capability, not an unqualified source
diff. A transferable package may contain:

- skill or scenario behavior and manifests;
- semantic UI descriptors;
- typed input, output, event, and permission contracts;
- tests, probes, and acceptance criteria;
- compatibility and dependency declarations;
- setup and migration plans with rollback behavior;
- provenance linking Issues, decisions, commits, and releases;
- bounded validation and runtime evidence;
- localization and user-facing explanation where applicable.

The package does not claim universal correctness. The recipient must validate
it against local versions, permissions, data policy, hardware, and user intent.
Evidence increases confidence and reuse efficiency; it does not transfer
authority from the receiving deployment.

Artifact lifecycle and registry details remain owned by
[Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md),
[Skill Runtime Lifecycle](../skill_runtime.md), and
[Skill Activation And Scenario Binding](skill-activation-and-scenario-binding.md).

## Production-To-Development Feedback

Production feedback must work even when no DEV fork exists for the installed
artifact. The target sequence is:

1. runtime or a person emits a bounded signal with artifact and deployment
   provenance;
2. Support intake creates or links an Issue in a registry independent of DEV
   filesystem presence;
3. ownership policy selects an existing DEV space, creates a new fork from the
   deployed/public version, or queues the Issue without realization;
4. Builder receives only the authorized context and evidence needed for the
   task;
5. a release links back to the Issue and the deployment evidence that motivated
   it;
6. post-activation checks append results and may resolve or reopen the Issue.

Setup and migration validation can use an observation window. A deterministic
post-install report records the applied version, migration checkpoints,
health assertions, warnings, and rollback availability. A failed or anomalous
report may create or update an Issue in the responsible development scope. Such
automation must be idempotent, deduplicate repeated symptoms, and respect
telemetry consent and retention policy.

## Privacy, Safety, And Policy Invariants

- Monitoring is opt-in or policy-authorized, purpose-limited, visible, and
  revocable. Membership in a trusted group is not blanket telemetry consent.
- Raw logs, prompts, credentials, personal data, and unrelated workspace files
  are not copied into Issues by default.
- Structured signals include the minimum evidence required to reproduce or
  assess the symptom; access to deeper diagnostics is separately authorized.
- Retrieved Issues, conversations, and external proposals are untrusted input
  to models and tools. They cannot grant permissions or alter policy.
- Agents communicate consequential work through versioned typed contracts;
  free-form inter-agent conversation may explain a proposal but is not an
  execution or approval protocol.
- An agent cannot approve a transition merely because it authored, triaged, or
  implemented the change.
- Destructive production effects, secrets, data migrations, and irreversible
  external actions require deterministic guards and the approval class defined
  by deployment policy.
- Every automated transition is attributable and replay-safe where the
  underlying operation supports idempotency.
- Runtime projections and notifications improve visibility but never become a
  second source of truth for Issues, releases, operations, or approvals.

## Compounding Value Ladder

The strategic value is expected to compound only when each preceding layer is
demonstrated:

1. **Governed runtime:** artifacts can be installed, activated, observed, and
   rolled back predictably.
2. **Managed deployment:** a deployment can be operated with measurable setup,
   support, and recovery cost.
3. **Personal Builder:** one user can take an Issue to a validated change in an
   isolated DEV space.
4. **Issue-first repair:** support signals reliably become traceable work and
   verified outcomes.
5. **Trusted collaboration:** independently owned Builders exchange proposals
   and integrate them without a shared writable workspace.
6. **Verified reuse:** a capability package is reused across deployments with
   provenance and local validation intact.
7. **Network learning:** aggregated, consented evidence improves compatibility,
   symptom detection, and reuse decisions without exposing private content.

This is an evidence ladder, not a promise of automatic network effects. A later
layer should not be treated as validated until real use demonstrates its
benefit and operational cost.

## Current Reality And Hypothesis Boundary

### Existing or partially implemented foundations

- skills and scenarios as governed artifacts;
- Builder terminology and a working local development pipeline;
- separate SDK, service, runtime, projection, and policy boundaries;
- DEV-space creation, static validation, Git checkpoints, and publication
  paths in varying stages of integration;
- NLU Teacher `development_task` candidates;
- structured incident and operational event foundations;
- runtime lifecycle, guarding, activation, and rollback mechanisms;
- registry and long-running-operation implementation slices.

These capabilities still require the acceptance evidence tracked by their
domain roadmaps. Presence in the codebase is not equivalent to production
readiness.

### Near-term architecture work

- complete the repeatable single-user Issue-to-release Builder loop;
- formalize setup, migration, post-install verification, and failure evidence;
- define the AdaOS Issue contract and its relationship to existing
  `development_task`, incident, task, operation, and release records;
- make production-to-development routing possible without requiring a
  pre-existing DEV checkout;
- prove isolated autonomous realization on a real skill or scenario.

### Unvalidated hypotheses

- Support Agent as an LLM-owned issue-intake role;
- proactive reaction to deterministic symptom-checker evidence;
- trusted-group discovery and cross-Builder proposal exchange;
- automated cross-component design coordination;
- verified capability exchange across independent deployments;
- a marketplace or evidence network with compounding value.

These hypotheses may guide contract choices, but must not broaden current
implementation scope or be represented as delivered functionality.

## Documentation Authority

This document owns only the cross-domain direction, vocabulary, invariants, and
role boundaries for governed evolution. It intentionally contains no delivery
checklist.

Authoritative execution detail remains in:

- [MVP Roadmap](../mvp_roadmap.md) for the active platform path;
- [Roadmap Inventory](roadmap-inventory.md) for roadmap ownership and status;
- [Builder Roadmap](builder-roadmap.md) for Builder readiness and gates;
- [Skill Factory](skill-factory.md) for isolated autonomous realization;
- [NLU Roadmap](../concepts/nlu-roadmap.md) for understanding and Teacher
  handoffs;
- [Incident Registry](incident-registry.md) and
  [Operational Event Model Roadmap](operational-event-model-roadmap.md) for
  runtime evidence;
- [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md)
  for distribution, installation, and long-running operations;
- [Conversation And Channel Architecture](conversation-and-channel-architecture.md)
  for conversational surfaces and durable conversation records;
- [Personalization, Identity, And Access](personalization-identity-access.md)
  for subjects, trust, consent, and authority.

The companion governed-evolution roadmap may reference stable milestones and
proof gates from those owners. It must not copy their technical tasks. A task
has one authoritative checklist; cross-domain documents link to it and record
only the evidence required to cross a larger product or architecture gate.
