# Semantic Application Composition And Evolution Roadmap

Status: cross-roadmap delivery sequence.

Last reviewed: 2026-09-22.

This roadmap coordinates the existing Application, Builder, capability,
package, access, state, and activation architectures into one linear product
outcome. It does not create another contract authority, package manager,
activation lock, or Builder state machine. Each mechanism remains owned by its
normative architecture and roadmap.

## Outcome

AdaOS can create and evolve an Application from user intent without making the
Application depend on a particular skill, provider, package layout, node, data
path, or credential.

The accepted path is:

```text
user messages
  -> traced requirements and clarification decisions
  -> semantic Application revision
  -> ApplicationRequirement[]
  -> executable simulation bindings
  -> accepted Prototype
  -> immutable Candidate ApplicationRelease and implementation packages
  -> admitted ApplicationResolution
  -> exact ResolutionPlan
  -> Trial/Beta with migration and access evidence
  -> Stable WorkspaceLock
  -> observations and governed evolution
```

The first full result is one new CRUD Application. The second result evolves
that installed Application without changing its Application identity or losing
eligible state. A third result extracts one independently useful implementation
from an Application-local component into a reusable package and resolves both
the original and a second Application against it.

## Authority Map

| Concern | Normative owner |
| --- | --- |
| Application identity, releases, channels, installation and Applications UI | [Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md) |
| Capability, state, binding, resolution and transition identities | [Capability, Binding, and State Separation](capability-binding-state-separation.md) |
| Source, immutable package closure, activation and `WorkspaceLock` | [Artifact Source, Package, and Activation](artifact-source-package-activation.md) |
| Intent, Prototype Brief, semantic UI and compiler behavior | [Builder Intent-to-Prototype](builder-intent-to-prototype.md) |
| Issue/Change/Run/Revision lineage, conversations and human decisions | [Builder Conversational Development](builder-conversational-development.md) |
| Executable simulation and Automation handoff | [Executable Prototype Architecture](executable-prototype-architecture.md) |
| Application permissions, roles, connected accounts and actor decisions | [Application Access, Permissions, and Roles](application-access-permissions.md) |
| Application search and browser-safe read projections | [Application Registry Projection](application-registry-projection.md) |
| Runtime placement, service instances, state topology and fencing | [Distributed Service And Data Topology](distributed-service-and-data-topology.md) |

If this roadmap conflicts with an owning architecture, the owning architecture
wins. The conflict must be corrected here rather than implemented as a second
mechanism.

## Product Invariants

1. A semantic Application names required capabilities and state contracts, not
   skills, packages, Python modules, providers, nodes, paths, or credentials.
2. A Prototype may use fixture or simulation bindings without changing the
   semantic Application identity.
3. LLM output may select only admitted semantic references supplied in bounded
   context. It does not persist arbitrary MCP tool names as Application
   dependencies.
4. Exact skills, scenarios, packages, providers, placements, and credentials
   appear only in resolved or local materialization records.
5. `ApplicationResolution` is admitted before an `ApplicationRelease` is
   activated in a target environment. Exact package resolution and evidence
   admission precede that resolution. The environment-specific resolution is
   not embedded in the portable `ApplicationRelease`.
6. `WorkspaceLock` remains the only active-workspace authority.
7. Application identity, implementation identity, package identity, local
   binding identity, and state identity are independently observable.
8. Eligible production state survives implementation and package substitution.
9. Permissions are evaluated over the Application grant, semantic capability
   authority requirements, selected binding, runtime component authority, and
   actor/session constraints.
10. Builder preserves traceability from each user message through requirements,
    decisions, semantic refs, implementation work, evidence, and release.
11. Applications renders authoritative projections and reviewed operations; it
    never infers lifecycle or health from card color, local component scans, or
    browser-only state.
12. Extraction into a reusable package never silently transfers data,
    credentials, ownership, or publication authority.

## Compatibility Boundary

Current manifests and releases may still contain `skill:*`, `scenario:*`,
`depends`, `runtime.skills`, `components.owned`, and physical entry-point
bindings. These are compatibility and resolved-delivery records.

During migration:

- current direct component declarations remain readable and activatable;
- a deterministic projector can describe them as Application-local binding
  definitions and state spaces;
- new semantic Application revisions use `ApplicationRequirement`;
- Builder and resolver shadow-compare semantic resolution with the existing
  component closure before semantic authority is enabled;
- a physical component closure is still retained in `ApplicationRelease` and
  `WorkspaceLock` for deterministic activation and recovery;
- compatibility fields are removed only after all local Applications are
  migrated and the rollback window closes.

## Target Creation Process

Every creation or evolution increment uses the existing Builder
`Issue -> Change -> Run -> Revision -> Release` lineage. A Change exposes an
OpenSpec-like, AdaOS-owned specification projection rather than treating the
latest prompt as the specification:

```text
source messages
  -> requirement delta (add / change / remove / unchanged)
  -> clarification and design decisions
  -> implementation and verification tasks
  -> Runs and result receipts
  -> accepted semantic revision
  -> release / resolution / activation evidence
```

Each requirement retains source message refs, stable semantic ID, status,
acceptance criteria, affected semantic refs, implementation status, evidence,
and supersession history. Model suggestions, inferred UX quality, technical
tasks, and external gaps are separate from user requirements. Several tasks may
implement one requirement and one task may cover several explicitly linked
requirements; neither relation is inferred from display order. The detailed
interaction and storage model remains owned by
[Builder Conversational Development](builder-conversational-development.md).

### 1. Understand

Builder preserves the original messages, extracts independently traceable
requirements, identifies unresolved material decisions, and asks only bounded
clarification questions. Suggestions and inferred UX quality are not silently
promoted into user requirements.

### 2. Compose

The semantic compiler creates one Application revision containing product
metadata, resources, views, commands, workflows, access intent, state needs,
and `ApplicationRequirement` records. UI composition and capability
requirements share stable semantic refs but remain separate contracts.

### 3. Simulate

The semantic resolver selects deterministic simulation bindings or reports a
typed gap. Preview executes through declared prototype activities. Fixtures,
failure states, input-required states, and access-denied states are visible and
replayable.

### 4. Accept Prototype

Human acceptance freezes the semantic Application revision and Prototype
evidence. It accepts observable behavior and unresolved limitations, not a
particular generated package topology.

### 5. Automate

Automation reuses an admitted binding package where possible. Otherwise it
implements an Application-local `BindingDefinition`, its conformance tests,
state adapter, migrations, and package delivery metadata. Direct component
source remains an implementation detail of this step. The Candidate
`ApplicationRelease` binds the accepted semantic revision and distributable
artifacts, not one installation's local binding instances or state spaces.

### 6. Resolve And Verify

Resolution selects capability/state contracts, binding definitions, exact
packages, local binding/state obligations, permissions, placement constraints,
and evidence. Builder Final Verification reports every unresolved requirement,
gap, stale claim, migration, permission elevation, and unsupported runtime
condition.

### 7. Trial

Trial/Beta materializes the exact Candidate, migrates from the current Stable
state when applicable, inherits governed settings and secret references, and
executes browser, contract, access, migration, restart, and rollback checks.
Trial does not create a second semantic Application.

### 8. Promote

Promotion executes the reviewed `ResolutionPlan`, commits one new
`WorkspaceLock`, preserves eligible state identity, records exact evidence, and
makes the accepted release Stable. No package is rebuilt during promotion.

### 9. Observe And Evolve

Applications, Builder, Dev Tickets, logs, traces, screenshots, health
conditions, Development Reports, and runtime evidence feed a new Change. The
next revision starts from the exact accepted semantic and implementation base.

### 10. Curate Reuse

Repeated or independently useful Application-local behavior becomes a
capability-package candidate. A reviewed curation process defines or reuses a
contract, extracts the binding, proves conformance, publishes an immutable
package, migrates consumers, and only then deprecates embedded implementations.

## Linear Delivery Plan

```text
ASC0 -> ASC1 -> ASC2 -> ASC3 -> ASC4 -> ASC5
  -> ASC6 -> ASC7 -> ASC8 -> ASC9
```

Applications may implement the no-regret projection envelope and navigation
before `ASC3`, but it cannot claim semantic resolution support until the
resolver and registry projection pass together. Package curation design may
proceed earlier, but no implementation is promoted to reusable before the first
end-to-end creation/evolution proof establishes the contracts it relies on.

No stage is complete from document or unit-test evidence alone. Each stage has
an exact local evidence bundle and leaves the previous runtime path available
until its rollback gate closes.

The checkboxes below are integration gates, not duplicate implementation-task
authorities. A gate closes only when the referenced owning roadmaps close their
required items and the combined exit proof passes. Progress notes and defects
stay in those owning items, Dev Tickets, and evidence artifacts rather than
being copied here.

Current integration assessment:

| Stage | Maturity | Remaining boundary |
| --- | --- | --- |
| `ASC0` | partial | broader compatibility inventory and semantic lint |
| `ASC1` | specified | portable schemas, registry and package delivery metadata |
| `ASC2` | partial | requirement ABI, semantic discovery and compiler lowering |
| `ASC3` | specified | local identities, resolver, admitted resolution and read projection |
| `ASC4` | specified | resolution plan, lock/access/placement integration and fault proof |
| `ASC5` | not started | uncontaminated full semantic Application creation proof |
| `ASC6` | not started | two installed-Application evolution cycles with state/access preservation |
| `ASC7` | specified | governed extraction and two-consumer reuse proof |
| `ASC8` | partial | current Applications UX plus semantic explanation/control completion |
| `ASC9` | deferred | local migration, shadow comparison and compatibility retirement |

Historical Builder and Application lifecycle runs remain useful evidence for
their original contracts. They do not close these integration stages unless
their retained artifacts contain the semantic identities and cross-stage
evidence required here.

### ASC0. Documentation And Compatibility Baseline

**Outcome:** every architecture uses the same distinction between Application,
capability, binding, package, runtime component, state, and placement.

- [ ] Update product terminology and cross-document ownership links.
- [ ] Mark direct skill/scenario dependencies as compatibility or resolved
  runtime closure, not semantic Application requirements.
- [ ] Inventory current Application manifests, Builder semantic records,
  provider contracts, direct MCP bindings, component locks, state owners, and
  Applications projections.
- [ ] Add lint that rejects new ambiguous persistent `capabilities` fields and
  absolute/local implementation refs in semantic Application documents.

**Exit proof:** the inventory maps at least Applications, Users & Access,
Desktop, Builder, Reading List, and one distributed Application without
changing runtime state.

### ASC1. Portable Contract Spine

**Outcome:** Applications can declare portable requirements and packages can
declare portable implementations.

Owned by `CBS1` and the Artifact Pipeline.

- [ ] Implement and validate `CapabilityContract`, `StateContract`, state ports,
  `BindingDefinition`, and package delivery metadata.
- [ ] Define `ApplicationRequirement` with capability range, target,
  constraints, authority needs, and evidence threshold.
- [ ] Define compatibility projection from current provider contracts and
  component manifests without treating MCP tools as semantic identities.
- [ ] Register immutable local contracts and definitions with provenance,
  maturity, deprecation, and conformance references.

**Exit proof:** two differently packaged implementations provide one unchanged
binding-definition digest and no portable record contains a local path,
credential, node, or provider account.

### ASC2. Semantic Application And Prototype Compilation

**Outcome:** Builder compiles requirements and executable simulation from one
semantic Application authority.

Owned by `BIP-09`, `BIP-11`, and the Executable Prototype architecture.

- [ ] Extend the semantic Application ABI with requirement and state-port refs.
- [ ] Expose bounded capability discovery by semantic contract, operations,
  effects, authority, evidence and supported mode.
- [ ] Compile Prototype activities to simulation binding requirements; reject
  arbitrary persisted MCP tool IDs, skill IDs, and provider IDs.
- [ ] Preserve message-to-task-to-requirement-to-view/activity traceability.
- [ ] Surface typed gaps and material clarification instead of inventing a
  direct implementation dependency.
- [x] Document the graph-first streaming direction: semantic graph operations
  are the incremental source, while `pageSchema` is a deterministic preview or
  compatibility projection.
- [ ] Add semantic operation replay fixtures that produce the same semantic
  Application digest and projected Prototype artifact as the terminal
  materialization path.

**Exit proof:** one ordinary prompt produces a working CRUD Prototype whose
semantic digest remains unchanged when its fixture binding is replaced by a
different simulation binding, and replaying its semantic operation log produces
the same graph digest plus projected Prototype artifact as terminal
materialization.

### ASC3. Semantic Resolution And Applications Read Model

**Outcome:** one unchanged semantic Application resolves to explainable
simulation and production candidates, and Applications can display the result.

Owned by `CBS2`, `CBS3`, and Application Registry Projection.

- [ ] Implement local `BindingInstance` and `StateSpace` identities.
- [ ] Implement bounded deterministic semantic and exact-package resolution.
- [ ] Admit immutable `ApplicationResolution` only after package, evidence, and
  policy checks.
- [ ] Project requirements, selected contracts, bindings, package closure,
  viability, gaps, state portability, and evidence freshness into the
  Application registry.
- [ ] Provide browser-safe summary and advanced detail without exposing secret
  refs or local paths.

**Exit proof:** Applications explains why each requirement is satisfied or
blocked, which implementation is selected, and whether the Application is
viable for simulation, Trial, and production.

### ASC4. Resolution Plan And Governed Materialization

**Outcome:** admitted resolution becomes an exact, recoverable activation plan.

Owned by `CBS4`, Artifact Pipeline, Application Access, and Distributed
Topology.

- [ ] Create immutable `ResolutionPlan` with base lock, package closure,
  binding/state changes, migrations, permissions, placement, evidence, and
  rollback obligations.
- [ ] Extend existing locks and journals by digest; do not introduce a second
  active-workspace authority.
- [ ] Bind capability authority requirements to Application grants and runtime
  component permissions.
- [ ] Resolve desired placement after implementation selection and preserve
  observed placement as separate operational state.

**Exit proof:** failure injection before commit preserves the previous
`WorkspaceLock`, state generation, writer authority, grants, and usable Stable
Application.

### ASC5. First End-To-End Application Creation

**Outcome:** one new CRUD Application completes the full Builder cycle with no
direct engineering mutation after the reviewed task starts.

- [ ] Run Understand through Stable using simulation and local production
  bindings.
- [ ] Verify wide/compact UI, keyboard, accessibility, empty/error/access-denied
  states, restart, logs, traces, screenshots, and exact task evidence.
- [ ] Verify Trial settings/secret inheritance and synthetic-to-Stable data
  boundary.
- [ ] Publish DEV and Stable source/package checkpoints at the declared gates.

**Exit proof:** one evidence bundle links messages, requirements, semantic
revision, contracts, Prototype acceptance, code, packages, resolution, plan,
Trial, browser evidence, and final Workspace lock.

### ASC6. Installed Application Evolution

**Outcome:** Builder evolves the installed Application while preserving its
identity, eligible data, access, settings, and placement intent.

- [ ] Add one schema-affecting feature and one capability requirement.
- [ ] Build an algorithmic forward migration and apply Stable data to Beta.
- [ ] Exercise permission and connected-account diff review.
- [ ] Replace Beta incrementally, restart during the operation, recover, and
  promote only the exact accepted result.
- [ ] Preserve the old Stable recovery point and explicitly disclose possible
  post-Beta write loss; reverse migration remains deferred.

**Exit proof:** two successive changes reach Stable with unchanged
`application_id`, retained eligible `state_space_ref`, correct data digests, and
complete before/after access and lock evidence.

### ASC7. Capability Package Curation And Extraction

**Outcome:** reusable behavior can leave an Application without coupling
consumer identity to the original source tree.

- [ ] Detect a repeated or independently useful Application-local candidate.
- [ ] Decide whether to reuse, refine, fork, compose, or reject an existing
  capability contract.
- [ ] Establish package ownership, trust domain, compatibility, versioning,
  deprecation, support, and evidence policy.
- [ ] Extract the binding and conformance suite without moving the Application's
  state or credentials into the package.
- [ ] Publish the package through the existing artifact pipeline.
- [ ] Resolve the original and one independent consumer against it.
- [ ] Retain a reviewed fallback until both consumers pass substitution and
  rollback evidence.

**Exit proof:** the two Applications contain no source dependency on each other,
share one capability contract and reusable package, and preserve independent
Application, binding-instance, and state-space identities.

### ASC8. Applications Product Completion

**Outcome:** Applications is the understandable control and observation plane
for the new lifecycle.

- [ ] Keep product lifecycle primary; place semantic composition under an
  expandable advanced section rather than exposing raw skills as products.
- [ ] Display required capabilities, satisfaction, selected implementation,
  package provenance, viability, evidence freshness, state portability,
  permissions, connected accounts, placement, and action-required conditions.
- [ ] Keep `Release channel`, `Implementation source`, `Installation state`,
  `Runtime health`, and `Update available` as independent fields.
- [ ] Route provider/account selection, implementation substitution, migration,
  update, and repair through reviewed plans.
- [ ] Add impact previews for contract/package/provider/evidence changes.

**Exit proof:** a user can answer what the Application does, what it needs,
which version is selected, where it executes, what data it owns, which external
accounts it uses, why it is healthy or blocked, and what an available action
will change.

### ASC9. Migration And Legacy Retirement

**Outcome:** local Applications use semantic requirements as authoring
authority while existing component execution remains deterministic.

- [ ] Migrate all supported local Application definitions and regenerate exact
  resolved component closures.
- [ ] Shadow-compare old and new resolution for the declared rollback window.
- [ ] Reject new source-level direct dependencies except explicit compatibility
  imports and platform bootstrap components.
- [ ] Retire duplicate component-discovery and direct-tool authoring paths only
  after usage reaches zero and retained releases remain recoverable.

**Exit proof:** clean-node creation, update, rollback, reinstall, and restore use
the semantic path; old immutable releases remain readable and activatable under
their compatibility contract.

## Applications No-Regret Requirements

The Applications product is being implemented before the full semantic resolver.
The following decisions must be represented now to avoid a UI and storage
rewrite later:

1. Store and render stable refs plus exact digests. Never use display names as
   join keys.
2. Keep `Application`, `SemanticApplicationRevision`, `ApplicationRelease`,
   `ApplicationInstallation`, `ApplicationResolution`, and `RuntimeSelection`
   as separate identities.
3. Model conditions as typed records with source, observed generation,
   severity, reason, action target, and freshness. Do not encode condition
   meaning in color or badges alone.
4. Reserve browser-safe projection sections for `requirements`, `resolution`,
   `bindings`, `state_spaces`, `evidence`, and `placement`, even while their
   initial value is unavailable.
5. Keep required capability and selected implementation separate. One
   capability may have several eligible bindings and one package may deliver
   several bindings.
6. Keep publisher/source, Catalog channel, installed release, selected update
   track, implementation provider, and execution node independent.
7. Treat connected accounts and secrets as local binding configuration and
   access facts, never package or Application source fields.
8. Treat state portability and migration impact as first-class update review
   facts. An update badge alone is insufficient.
9. Make every mutation plan-based, digest-bound, idempotent, auditable, and
   recoverable. Read projections never become mutation authority.
10. Use extensible detail sections with stable semantic IDs. The current five
    top-level sections remain product-oriented; advanced composition appears
    inside them rather than adding one tab per internal entity.
11. Support `unknown`, `not_applicable`, `unresolved`, `stale`, and `blocked`
    explicitly. Empty UI must not imply absence or success.
12. Keep compact list projections bounded, but preserve a route to complete
    explanation and evidence.

## Acceptance Matrix

| Change | Must remain stable | Must change | Required evidence |
| --- | --- | --- | --- |
| fixture -> simulation binding | semantic Application revision | binding selection and disposable state | Prototype trace and resolution diff |
| simulation -> production | semantic Application revision | production binding/state attachment and authority | conformance, plan, Trial and lock |
| package relocation | requirement, capability and binding-definition identities | package closure and resolution digest | old/new delivery mapping |
| provider substitution | Application identity and eligible state identity | binding instance/configuration | state compatibility and migration evidence |
| feature increment | Application identity and unaffected requirements | semantic revision and affected resolution | task trace, impact and regression evidence |
| capability extraction | consumer Application identities and state ownership | package ownership and delivery mapping | two-consumer conformance and rollback |
| node relocation | Application and release identities | observed binding/service placement | placement operation and health evidence |

## Deferred

- public or federated capability registry before the local contract and
  resolver proof;
- arbitrary model-authored MCP composition;
- automatic contract publication or production migration by Evolver;
- reverse data migration after Beta rollback;
- global optimization across unbounded providers and packages;
- general multi-user concurrent semantic authoring;
- removing physical skill/scenario closure from immutable releases;
- exposing every internal capability or package as a normal Catalog product.

## Definition Of Done

This program is complete only when:

1. one new Application and two successive installed-Application changes pass
   the full governed cycle;
2. accepted semantic revisions never contain implementation topology;
3. simulation, production, package relocation, provider substitution, and node
   relocation preserve exactly the identities declared stable;
4. one implementation is extracted and reused by an independent Application;
5. Applications and Builder explain requirements, resolution, evidence, state,
   access, placement, and current actions from authoritative projections;
6. a clean node can install and recover the result from immutable artifacts;
7. no direct engineering repair is hidden from the evidence bundle;
8. legacy immutable releases remain recoverable while new authoring uses the
   semantic path.

## Related Roadmaps

- [Capability, Binding, and State Separation Roadmap](capability-binding-state-separation-roadmap.md)
- [Builder Intent-to-Prototype Roadmap](builder-intent-to-prototype-roadmap.md)
- [Application Lifecycle and Distribution Roadmap](application-lifecycle-and-distribution-roadmap.md)
- [Artifact Source, Package, and Activation Roadmap](artifact-source-package-activation-roadmap.md)
- [Application Access, Permissions, and Roles Roadmap](application-access-permissions-roadmap.md)
- [Distributed Service and Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md)
