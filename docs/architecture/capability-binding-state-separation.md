# Capability, Binding, and State Separation

Status: target architecture and normative vocabulary.

Last reviewed: 2026-09-17.

Implementation sequencing is owned by
[Capability, Binding, and State Separation Roadmap](capability-binding-state-separation-roadmap.md).

## Decision Summary

AdaOS separates six identities that are currently easy to collapse into a
skill, package, provider, or database name:

1. a semantic capability;
2. the state contract used by that capability;
3. a portable implementation binding;
4. the immutable package release that delivers the implementation;
5. a local binding instance;
6. a local state space.

An Application declares requirements against `CapabilityContract` and
`StateContract`. It does not name packages, Python classes, skills, providers,
database paths, or credentials. A semantic resolver first produces ephemeral
semantic candidates and package constraints. The existing package resolver
then selects an exact package closure, after which evidence and policy gates
decide whether to admit one immutable `ApplicationResolution`. A planner
compares that admitted resolution with the active workspace and produces an
immutable `ResolutionPlan`. Activation executes the exact plan and atomically
changes authority by committing a new `WorkspaceLock`.

```text
Application requirement
        |
        v
CapabilityContract ------ state_port ------> StateContract
        ^                                         ^
        | implements                              | supports
BindingDefinition -------------------------------+
        |
        | delivered by
        v
PackageRelease

BindingInstance ---- reads/writes/appends ----> StateSpace
        ^                                         ^
        +------------ ApplicationResolution -----+
                              |
                     current WorkspaceLock
                              |
                              v
                      ResolutionPlan
                              |
                    prepare / verify / commit
                              |
                              v
                       WorkspaceLock
```

The resolution/admission order is normative:

```text
semantic requirements
  -> semantic candidate (contracts, binding definition, package constraints)
  -> existing package resolver
  -> exact PackageRelease closure
  -> evidence and policy checks
  -> ADMIT immutable ApplicationResolution
```

No `ApplicationResolution` exists before the exact package closure and its
evidence have passed admission. Candidate records remain ephemeral and have no
runtime authority.

The Semantic Graph, Impact Graph, viability projections, and Evolver
observations are derived projections over these authoritative records. They
may be deleted and rebuilt without loss of architectural truth.

The central design rule is:

> AdaOS evolution may change implementations, bindings, package topology, and
> materialization while preserving semantic and state identities whose
> contracts have not changed. Every identity-changing transition is explicit,
> reviewed, and verifiable.

## Scope

This document owns:

- the distinction between semantic, implementation, package, installation,
  state, resolution, and evidence identities;
- the minimum contracts for capability and state resolution;
- the boundary between `ApplicationResolution`, `ResolutionPlan`, activation,
  and `WorkspaceLock`;
- state continuity and writer-authority invariants during rebinding;
- evidence subject, environment, dependency, and freshness semantics;
- the canonical inputs of derived semantic, impact, and viability views;
- the first CRUD and domain-capability executable proofs.

This document does not replace:

- package construction, storage, migration execution, activation journaling,
  rollback, or retention owned by
  [Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md);
- Application lifecycle, Catalog, installation, and publisher authority owned
  by
  [Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md);
- resource declarations and common CRUD presentation owned by
  [Declarative Resource Workbench](declarative-resource-workbench.md);
- relational provider negotiation owned by the current relational storage ABI;
- workflow execution semantics owned by
  [Governed Data-Driven Workflow Model](governed-workflow-runtime.md);
- UI rendering owned by [Web UI Architecture](web-ui-architecture.md).

It composes those mechanisms. It must not introduce a second package manager,
a second workspace authority, or a second database-lifecycle system.

## Why The Separation Is Required

The current system has strong implementation mechanisms, but several names
still carry more than one meaning:

- a skill name can mean a semantic service, Python implementation, package,
  runtime instance, permission subject, and data owner;
- `skill.yaml.capabilities` describes requested SDK authority, not semantic
  capabilities provided to Applications;
- a package member can be treated as both an Application requirement and the
  selected implementation of that requirement;
- local CRUD state is commonly derived from a skill owner and storage path;
- evidence that one release worked in one environment can be mistaken for a
  timeless property of the abstract capability;
- a resolver could accidentally acquire mutation authority if selection and
  transition planning are not separate.

These couplings make ordinary evolution look like identity change. Moving an
implementation between packages can force an Application rewrite. Rebinding a
provider can accidentally allocate new state. A stale external API check can
make a package appear intrinsically broken. The target model keeps each of
those changes in its own dimension.

## Architectural Principles

### Orthogonal identity

Each durable concept has its own stable reference. Display aliases and package
locations are not identities. A package move does not rename a capability; a
provider move does not rename a portable state space.

### Contract before materialization

Portable contracts state what is required or guaranteed. Local instances state
where and how those contracts are materialized. An Application requirement is
resolved against contracts before package and provider details are selected.

### Selection is not authority

A resolver may enumerate and rank candidates. An admitted
`ApplicationResolution` identifies a chosen configuration. Neither may change
the active runtime, data authority, or `WorkspaceLock`.

### Plans are compare-and-switch proposals

A `ResolutionPlan` is based on exact current digests and generations. Its
preconditions are checked again immediately before authority changes. A plan
cannot silently rebase itself during activation.

### State identity outlives eligible providers

For a portable state class, `state_space_ref` remains stable when a compatible
binding or package is substituted. This is conditional on the state
portability policy, migration proof, and activation preconditions; it is not a
claim that every external system is detachable.

### Evidence is immutable; trust is time-dependent

An `EvidenceClaim` records what was observed. It is never mutated from
`verified` to `stale`. A current `EvidenceAssessment` applies freshness,
dependency, environment, and policy rules to immutable claims.

### Derived intelligence is disposable

Graphs, indexes, rankings, observations, and recommendations are rebuilt from
portable and local source records. No identity or activation authority exists
only in a graph database or model-generated summary.

### Existing authorities are extended, not duplicated

`ProjectCompositionLock`, immutable package records, activation journals,
state migration locks, and `WorkspaceLock` remain authoritative in their
existing domains. New records refer to or extend them by digest.

## Three Architectural Planes

### Portable plane

Portable records may be packaged, published, verified, cached, and resolved
without revealing installation secrets or local data locators.

```text
CapabilityContract
StateContract
BindingDefinition
PackageRelease
portable EvidenceClaim
```

Portability does not mean universal compatibility. Every portable record is
versioned, digest-addressed, and explicit about environment constraints.

### Local plane

Local records belong to one workspace, tenant, member, or installation.

```text
BindingInstance
StateSpace
ApplicationResolution
ResolutionPlan
Activation operation and journal
operational EvidenceClaim / EvidenceAssessment
WorkspaceLock
```

These records may contain opaque references to local endpoints, accounts, and
secret slots. They must not copy credentials into portable artifacts or
derived graphs.

### Derived plane

```text
SemanticGraph
ImpactGraph
ViabilityProjection
EvolverObservation
```

Derived records carry source digests and an `as_of` point. They may accelerate
queries or support decisions, but consumers must be able to explain each edge
through authoritative source records.

## Identity Model

| Identity | Stable across | Changes when |
| --- | --- | --- |
| `capability_ref` | package moves, provider changes, compatible implementation substitutions | the semantic responsibility is intentionally forked or replaced |
| `capability_contract_digest` | nothing; it identifies exact content | any contract field changes |
| `state_contract_ref` | provider and local storage changes | the semantic class of state is intentionally replaced |
| `state_contract_digest` | nothing; it identifies exact content | schema, invariants, or guarantee envelope changes |
| `binding_definition_ref` | package moves, installation/account changes | the portable adapter or implementation semantics intentionally change |
| `binding_definition_digest` | package moves and delivery-layout changes | any portable binding semantic, logical entry point, or supported-contract field changes |
| `package_digest` | channel and registry location changes | package bytes or locked metadata change |
| `binding_instance_ref` | revision, health observations, credential rotation, and eligible package relocation | a distinct installed/provider attachment is created |
| `state_space_ref` | eligible rebinding, package moves, state-safe migration generations | a new logical data space is explicitly created, imported, forked, or replaced |
| `application_resolution_digest` | repeated reads of the admitted solution | any selected contract, binding, package, instance, state attachment, or evidence obligation changes |
| `resolution_plan_digest` | retries against identical preconditions | desired resolution, base lock, expected generation, step, or policy changes |
| `workspace_lock_digest` | observation and derived-index rebuilds | active authority changes |

Stable references and exact digests serve different purposes. A stable
reference follows an intentional lineage. A digest identifies one immutable
revision in that lineage. Runtime decisions pin digests, never a mutable alias
alone.

## Canonical Vocabulary

### ApplicationRequirement

An Application requirement describes a semantic need. It belongs to the
Application semantic document and contains no implementation topology.

```yaml
requirement:
  requirement_ref: requirement:booking/reserve
  capability_ref: capability:booking.reserve
  version: ">=1.0 <2.0"
  environment_profile_ref: profile:production/default
  policy:
    evidence_at_least: verified
```

Requirements may constrain behavior, quality, effect policy, environment, or
evidence. They must not name a package, skill, module, database, provider, or
credential.

### CapabilityContract

A `CapabilityContract` is the portable semantic identity of a service AdaOS
can satisfy. It defines behavior independently of the implementation that
provides it.

Minimum contents:

- stable `capability_ref` and contract version;
- exact input, output, and error schema references;
- behavioral invariants and effect classification;
- idempotency, temporal, and compensation expectations where applicable;
- required authority and permission classes;
- dependencies on other capability contracts;
- typed state ports;
- conformance scenario references;
- deprecation and compatibility policy.

It does not contain Python classes, package names, algorithms, storage paths,
provider accounts, or secrets.

Capability categories are descriptive, not different identity systems:

- foundation capabilities such as `resource.records.manage`, event publish,
  or schedule execution;
- domain capabilities such as `booking.reserve` or `order.approve`;
- composite capabilities whose contract is fulfilled through an admitted
  composition of other capabilities.

The current `skill.yaml.capabilities` field remains a permission declaration
for SDK authority. It must not be reinterpreted as a provided
`CapabilityContract`. The first ABI uses explicit sidecar contract files or
new unambiguous manifest references.

### StateContract

A `StateContract` is a portable description of a class of governed state. It
is not a concrete database and does not identify an installation. Its
durability envelope may require persistent state or permit transient,
reconstructible materialization.

Minimum contents:

- stable `state_contract_ref`, version, and digest;
- record or aggregate schema references and schema digests;
- domain invariants;
- supported consistency, isolation, and durability envelope;
- ownership and mutation-authority constraints;
- lifecycle, retention, backup, and restore expectations;
- compatibility and migration policy;
- portability policy;
- fixture or conformance scenario references.

The contract advertises what a conforming materialization may guarantee. It
does not promote every consumer's strongest need into a universal property of
the state class.

```yaml
state_contract:
  schema: adaos.state.contract.v1
  state_contract_ref: state-contract:booking-records
  version: 1.0.0
  record_schema_ref: abi:booking.record.v1
  record_schema_digest: sha256:...
  supported_consistency: [snapshot, serializable]
  supported_durability: [persistent]
  portability:
    class: migration_required
  lifecycle_ref: lifecycle:booking-records/default
```

The first schema must reference current package schema locks, migration locks,
and `skill.yaml:data_lifecycle` declarations instead of copying migration SQL
or inventing a second lifecycle truth.

### State portability classes

| Class | Meaning during provider or binding substitution |
| --- | --- |
| `portable` | The same `state_space_ref` can be attached to another conforming binding without semantic transformation. |
| `migration_required` | Identity may remain stable only through an explicit, verified migration edge. |
| `provider_bound` | Provider identity is part of effective state identity; ordinary rebinding is rejected. |
| `external_authoritative` | AdaOS owns a reference and access binding, not the external records or their lifecycle. |
| `reconstructible` | Materialized state may be discarded and rebuilt from authoritative inputs under declared policy. |

The contract declares allowed portability policy. The effective portability of
one transition also depends on the source and target bindings, environment,
data classification, and evidence.

### State port

A state port belongs to a capability contract. It describes what one consumer
needs from a state class.

```yaml
state_port:
  port_ref: bookings
  contract_ref: state-contract:booking-records
  contract_version: "^1"
  access: read_write
  requirements:
    consistency_at_least: serializable
    durability: persistent
    isolation: tenant
```

`access` is one of `read`, `write`, `read_write`, `append`, or `cache` in v1.
Mutation authority is separately resolved; declaring write access does not by
itself grant authority.

The resolver admits an attachment only when both statements hold:

```text
StateSpace conforms_to StateContract

Requirements(port) subset_of EffectiveGuarantees(
  StateContract,
  StateSpace,
  BindingDefinition,
  BindingInstance,
  EnvironmentProfile
)
```

### BindingDefinition

A `BindingDefinition` is a portable description of one implementation or
adapter capable of satisfying a capability contract in specified conditions.

Minimum contents:

- stable definition reference, version, and digest;
- implemented `CapabilityContract` range and exact tested revisions;
- logical implementation entry point and adapter protocol version;
- supported state contracts and port access modes;
- simulation, sandbox, and production modes;
- required environment features and permissions;
- provider dependency declarations;
- conformance suite and evidence requirements.

A definition may be delivered by different package releases over time. A
package release may deliver several definitions. Those are relations, not
shared identities. A physical package member or archive path is never part of
canonical `BindingDefinition` content.

### PackageRelease

`PackageRelease` is the immutable delivery identity used by this model. In the
current codebase, its facts come from content-addressed `ArtifactPackageRef`
records and their enclosing `ProjectRelease` or Application release. This
architecture does not require a competing package aggregate.

The package relation answers “which exact immutable bytes deliver this binding
definition?” It does not answer “which semantic capability does the
Application require?”

Package delivery metadata maps an exact binding-definition digest and logical
implementation entry point to a physical member in that package:

```yaml
delivers:
  - binding_definition_digest: sha256:...
    implementation_entrypoint: resource-records-adapter
    package_member: skills/resource_records/adapter.py
```

`package_member` is covered by package identity, not binding-definition
identity. Moving the member from Package A to Package B therefore changes the
package closure and delivery relation while preserving the binding-definition
ref and digest.

### BindingInstance

A `BindingInstance` is one local instantiation of a binding definition.

```yaml
binding_instance:
  schema: adaos.binding.instance.v1
  binding_instance_ref: binding-instance:workspace/orders-local
  revision: 7
  revision_digest: sha256:...
  previous_revision_digest: sha256:...
  binding_definition_digest: sha256:...
  package_digest: sha256:...
  mode: production
  environment_profile_digest: sha256:...
  provider_ref: provider:relational/local
  endpoint_ref: locator:opaque-token
  secret_refs: [secret:orders-database]
  generation: 7
```

Endpoint and secret references are opaque. Credentials and raw DSNs never
enter package manifests, portable evidence, semantic graphs, or Application
documents.

`binding_instance_ref` is the stable identity. Every material change is an
immutable, digest-addressed revision linked to its predecessor. Package
selection, provider attachment, configuration/secret reference, and generation
changes create a new revision. Readiness and health are timestamped operational
observations bound to an exact revision digest; heartbeats do not rewrite the
canonical revision.

### StateSpace

A `StateSpace` is the local identity of a concrete logical data space for its
declared lifecycle. A production state space is normally durable; a simulation
state space may be explicitly disposable. The name deliberately avoids
`StateInstance`, which collides with workflow and UI state, and avoids
`state_ref`, which is already used by the Web UI ABI for query and component
state.

```yaml
state_space:
  schema: adaos.state.space.v1
  state_space_ref: state-space:workspace/orders
  revision: 13
  revision_digest: sha256:...
  previous_revision_digest: sha256:...
  state_contract_ref: state-contract:order-records
  state_contract_digest: sha256:...
  logical_owner_ref: application:orders
  lifecycle_ref: lifecycle:orders/default
  storage_ref: locator:opaque-token
  active_generation: 12
  authority_epoch: 31
```

`state_space_ref` is the stable data identity. Its configuration and authority
history is an append-only sequence of immutable, digest-addressed revisions.
Changing the state-contract revision, storage attachment, active generation,
or authority epoch writes revision N+1; it never mutates revision N. Health,
capacity, and readiness are operational observations against an exact revision
and generation.

`StateSpace` keeps these concepts distinct:

- logical owner: whose domain records these are;
- lifecycle owner: who approves retention, migration, and destruction;
- custodian binding: which binding currently stores or exposes them;
- mutation authority: which active writer may commit changes;
- physical location: an opaque, replaceable storage reference.

Installation may allocate or attach a state space without becoming its logical
owner. One binding may read and write several state spaces. A state space may
have several readers. Relations such as `reads`, `writes`, `appends`, and
`caches` belong to `ApplicationResolution`, not to either object's identity.

`WorkspaceLock` pins exact binding-instance and state-space revision digests as
well as the active state generation and authority epoch. Stable refs support
lineage and lookup; revision digests support replay, impact analysis,
compare-and-switch activation, and recovery.

These are revision semantics inside the `BindingInstance` and `StateSpace`
contracts, not additional top-level entity families or competing authorities.

For a state class whose policy requires one writer:

```text
ActiveWriters(StateSpace) <= 1
```

This invariant is enforced through an authority epoch or fencing token. It is
not inferred by counting processes. A writer holding an old epoch is rejected
after the authority switch.

### EvidenceClaim

An `EvidenceClaim` is an immutable statement about exact subjects under exact
observed conditions.

Minimum contents:

- claim ID, kind, result, and immutable digest;
- subject refs and exact subject digests;
- environment profile and digest;
- external dependency identities and observed versions or fingerprints;
- suite, fixture, runner, and evidence digests;
- `issued_at` and optional observation interval;
- freshness policy and invalidation inputs;
- provenance and signer or trust domain;
- redaction and portability scope.

Canonical claim kinds include:

| Kind | Subjects |
| --- | --- |
| `capability_conformance` | capability contract, binding definition, package release, environment profile |
| `state_compatibility` | binding definition, state contract, provider/environment profile |
| `migration_correctness` | source and target state contracts, migration digest, fixtures |
| `installation_health` | binding instance, state space, active generations, local environment |

Portable claims contain no installation secrets and concern portable
subjects. Operational claims may remain local. Reverification creates a new
claim; it does not rewrite history.

### EvidenceAssessment

An `EvidenceAssessment` is the current policy evaluation of one or more claims:

```text
admissible | stale | superseded | incompatible | revoked
```

External dependency drift normally changes the assessment from `admissible`
to `stale`. It does not retroactively change a successful historical claim
into a failed claim or declare the package intrinsically broken. A new
verification claim can restore admissibility or prove incompatibility.

### ApplicationResolution

Semantic candidates and package-resolution attempts are ephemeral. An
`ApplicationResolution` is created only after semantic selection, exact package
closure, and evidence/policy admission have all succeeded. Once admitted, it
is immutable and digest-addressed.

It answers:

> Which exact configuration satisfies this Application's requirements in this
> environment under this policy?

It records:

- semantic Application revision digest;
- environment and resolver-policy digests;
- requirement to capability-contract mapping;
- capability to binding-definition mapping;
- exact package closure and package delivery mappings for every selected
  binding definition;
- selected binding-instance refs and revision digests, or reserved refs and
  obligations for instances to provision;
- state-port to state-space attachments and access relations;
- selected evidence claims and unresolved evidence obligations;
- alternative rejection reasons needed for explanation;
- viability class of the admitted result.

It is read-only. It does not provision, migrate, fence a writer, or change a
workspace lock.

### ResolutionPlan

A `ResolutionPlan` is the explicit transition proposal between the active
workspace and one desired `ApplicationResolution`.

It answers:

> How can AdaOS safely move from this exact active configuration to that exact
> resolved configuration?

Minimum contents:

- desired `application_resolution_digest`;
- base `workspace_lock_digest` and lock revision;
- desired binding-instance and state-space refs and revision digests;
- binding instances or state spaces to provision;
- required migrations, checkpoints, backups, and compensation actions;
- pre-existing evidence gates and evidence to produce during staging;
- expected binding, state, topology, and authority generations;
- ordered steps with idempotency and replay classifications;
- pre-commit and post-commit verification;
- rollback or recovery strategy;
- expiry and freshness preconditions;
- exact plan digest.

The plan is immutable. A change to a step, base lock, expected generation,
migration, or evidence obligation produces a new plan digest and requires
validation again.

### Activation

Activation executes one exact `ResolutionPlan`. It owns mutable work and uses
the existing durable phase journal, staging, checkpoint, lock switch, reload,
health verification, commit, and recovery mechanisms.

The generic transition is:

```text
build semantic candidates (read only)
  -> resolve exact package closure (read only)
  -> evaluate evidence and policy (read only)
  -> admit ApplicationResolution
  -> build ResolutionPlan from exact WorkspaceLock
  -> validate plan
  -> prepare/provision/migrate in staging
  -> verify evidence and preconditions
  -> fence old writer and atomically switch authority
  -> commit WorkspaceLock
  -> reload and observe
  -> finalize or execute explicit recovery
```

Atomicity means atomic authority selection, not a fictional distributed
transaction over arbitrary external systems. Irreversible external actions
must be completed before the authority switch under explicit approval, be
idempotent, have verified compensation or reconciliation, or make unattended
activation ineligible.

Before the commit boundary:

```text
FailedActivation
  => WorkspaceLock_after = WorkspaceLock_before

FailedUncommittedMigration
  => ActiveStateGeneration_after = ActiveStateGeneration_before
```

Staged data, backup records, evidence, and journal entries may remain for
diagnosis and bounded cleanup. They are not active authority.

After the commit boundary, a crash is not reported as a clean pre-commit
failure. Recovery reads the journal and either completes the exact committed
intent or performs an explicit reconciled rollback permitted by the plan.

### WorkspaceLock

`WorkspaceLock` remains the authoritative statement of what is active. The
target lock extends or digest-links the current package and dependency lock
with:

- admitted Application resolution digests;
- active binding-instance refs and exact revision digests;
- state-port attachments;
- state-space refs and exact revision digests;
- active state generations and authority epochs;
- evidence-set or assessment digests required by activation policy.

These additions may first be an extension section referenced by digest. They
must not become a competing “capability lock” with independent activation
authority.

`ProjectCompositionLock` remains the exact package composition lock. The
capability model explains why those components were selected; it does not
replace their package-level reproducibility.

## Resolution Model

Resolution is deliberately split into two read-only stages followed by a
separate admission gate. Neither stage produces an authoritative
`ApplicationResolution` on its own.

### Stage 1: semantic resolution

The semantic resolver evaluates:

- Application requirements and semantic revision;
- capability and state contracts;
- binding definitions;
- effective state guarantees;
- environment profile;
- evidence requirements that the final selected subjects must satisfy;
- local policy, trust, risk, and viability target.

Its output is one or more ephemeral `SemanticCandidate` values or typed
unsatisfied requirements. A candidate contains selected contracts, a binding
definition, state attachments, package constraints, and evidence obligations.
It does not contain an exact package closure and does not choose a mutable
`latest` package by implication.

### Stage 2: package resolution

The existing package resolver selects exact package versions and digests,
dependency closure, component paths, and composition locks for the chosen
binding definitions. Existing package conflict and compatibility semantics
remain authoritative.

Its output is an ephemeral candidate with an exact package closure and delivery
mapping. A package conflict rejects that candidate; it cannot be hidden by the
semantic resolver.

### Admission gate

The admission gate evaluates the exact candidate's package-, definition-,
state-, environment-, and dependency-scoped evidence and current assessments.
It also applies trust, access, risk, and viability policy. Only a successful
gate creates the immutable `ApplicationResolution`.

The admitted resolution pins the selected package closure and delivery
mapping, so a later channel move does not silently change the answer. A
package relocation may preserve the same binding-definition digest while
creating a new Application-resolution digest because its exact delivery
closure changed.

### Viability targets

Resolution is policy-sensitive:

| Target | Typical admission |
| --- | --- |
| `semantic` | all requirements map to well-formed contracts; no runnable binding required |
| `simulation` | deterministic simulation bindings and their contract evidence exist |
| `sandbox` | isolated bindings, bounded side effects, and sandbox evidence exist |
| `production` | production bindings, state guarantees, authority, fresh evidence, migration safety, and activation policy all pass |

These are derived viability projections over one semantic Application. They
are not separate Application identities.

## Evidence Freshness And External Change

Freshness policy may include:

- maximum age;
- dependency version ranges;
- observed API/schema fingerprint;
- environment profile digest;
- credential or permission epoch without exposing credential values;
- provider health or conformance observation window;
- explicit revocation source.

An External Change Monitor adds observations such as a changed Jira API
version, provider schema fingerprint, or environment policy. It then rebuilds
`EvidenceAssessment`. It does not edit packages, contracts, or historical
claims.

Resolver policy decides whether stale evidence is:

- acceptable for semantic or simulation viability;
- acceptable only with a pre-activation revalidation obligation;
- inadmissible for production;
- proof of incompatibility only after a new negative claim.

## Semantic, Impact, And Evolution Views

The derived Semantic Graph includes explainable edges such as:

```text
requirement --satisfied_by--> capability contract
capability contract --implemented_by--> binding definition
binding definition --delivered_by--> package release
capability contract --has_state_port--> state contract
state port --attached_to--> state space
binding instance --reads/writes/appends--> state space
evidence claim --supports--> exact subjects in an environment
```

The Impact Graph is a projection specialized for change analysis. A change to
`CustomerState v1` can find state ports, capability contracts, admitted
resolutions, installed Applications, migration obligations, and evidence that
must be rerun without scanning arbitrary source code first.

The first Evolver is observational. It may identify:

- unsatisfied capability requirements;
- repeated local schemas or operations;
- repeated package co-occurrence;
- duplicate or overlapping capability contracts;
- recurring migration or provider failures;
- evidence that is frequently stale;
- candidates for capability extraction or composition.

It may propose a contract, composition, or migration plan. It may not rewrite
production state, publish a contract, or activate a binding autonomously.

Capability maturity prevents a one-off whole-Application implementation from
being counted as ecosystem reuse:

```text
application-local -> candidate -> reusable -> platform
```

Promotion requires independent consumers and conformance evidence, not size or
usage count alone.

## Relationship To Builder And Client

Builder authors the semantic Application revision and its requirements. It may
use simulation bindings for an executable prototype, but it must not bake
simulation package names into the Application document. Its final report
explains unresolved requirements, selected contracts, viability, and evidence
gaps.

The existing semantic prototype ABI and fixed `adaos.webui.v1` client remain
the first compiler target. Composite capability authoring and resolution occur
server-side. A client plug-in ABI is not required for the first proof and is
explicitly deferred.

The Resource Workbench remains the declarative UI and operation surface. This
architecture can supply a `resource.records.manage` capability beneath it, but
does not turn Resource Definition or UI state into persistent state identity.

## Compatibility With Current Runtime

The first implementation is additive.

### Legacy skill projection

An existing skill-owned CRUD store may be projected as:

```text
capability_ref       = capability:resource.records.manage/<resource-type>
binding_definition   = legacy skill adapter
binding_instance_ref = installed skill/runtime selection
state_space_ref      = state-space:legacy/skill/<skill-id>/<resource-type>
logical_owner_ref    = current owner declaration
```

The projection is deterministic and read-only until explicitly adopted. It
does not move records or rename an existing database. Once a native state
space is admitted, the mapping is preserved as migration provenance.

### Current relational bindings

`RelationalStorageRequirements`, provider capability profiles, opaque
locators, owner-scoped bindings, and migration ownership are inputs to
effective state guarantees. They are not discarded. The new state contract
adds semantic state identity and consumer ports above those provider
negotiation records.

### Current package and activation pipeline

The current activation phases already provide resolve, fetch, verify,
dependency planning, permission planning, migration planning, staging,
checkpoint, lock switching, reload, health verification, and commit. The new
`ResolutionPlan` binds these phases to an admitted semantic resolution, exact
state attachments, evidence obligations, and expected generations.

### Terminology collision

Web UI `state_ref` continues to mean component/query state. Persistent data
identity always uses `state_space_ref` in this architecture.

## First Executable Proof: CRUD

The first vertical slice uses one real typed CRUD resource because the runtime
already has schema validation, persistence, optimistic revision conflict,
authorization, tracing, and Resource Workbench presentation.

The proof introduces:

- a `resource.records.manage` capability contract;
- one record state contract;
- a deterministic simulation binding with its own disposable state space;
- a local production binding over existing Core storage mechanisms;
- one stable production state space;
- conformance scenarios shared by both bindings;
- one semantic Application revision resolved first to simulation and then to
  local production;
- a separate production Binding A to production Binding B rebinding over the
  stable production state space;
- activation failure injection around migration and authority switch.

Core retains transaction, persistence, validation, access, and tracing
authority. The binding composes those mechanisms; it does not reimplement
them.

The proof contains two independent transitions:

```text
simulation binding + disposable simulation StateSpace
  -> production Binding A + production StateSpace
```

This transition proves semantic Application identity only. Simulation and
production state-space refs are expected to differ; requiring them to be equal
would incorrectly turn prototype data into production authority.

```text
production Binding A + production StateSpace
  -> production Binding B + the same production StateSpace
```

This transition proves production state continuity and atomic rebinding.

The proof passes only when all five invariants hold:

1. **Materialization independence.** Simulation changes to production without
   changing the semantic Application revision.
2. **Package-topology independence.** The implementation moves between
   packages without changing the requirement, capability, or binding-definition
   identity/digest.
3. **State continuity.** A portable production `StateSpace` keeps its identity
   and records through production Binding A to Binding B rebinding; no
   simulation state identity is preserved into production.
4. **Application independence.** The Application semantic document contains
   no package, binding, storage, provider, or credential names.
5. **Atomic rebinding.** The new materialization becomes fully authoritative or
   the prior `WorkspaceLock`, state generation, and writer authority remain
   active.

CRUD is an architectural proof, not proof that the model covers all domain
behavior.

## Second Executable Proof: `booking.reserve`

The next proof is `booking.reserve`. It is preferred over `order.approve`
because it forces provider and state separation rather than allowing the proof
to collapse into workflow transitions alone. It uses at least:

```text
AvailabilityState  read
BookingState       write
AuditState         append
```

The case must exercise:

- non-trivial business invariants;
- temporal behavior and concurrent decisions;
- observable side effects;
- provider failure;
- compensation or explicit reconciliation;
- composition with at least one supporting capability;
- evidence invalidation caused by a changed external dependency.

This proof determines whether capability boundaries remain useful beyond
generic record operations.

## Evaluation Model

The architecture should be evaluated as an evolutionary system, not only by a
single successful demo. A matched benchmark compares Applications built with
the growing verified capability inventory against equivalent held-out work
without those reusable contracts.

Minimum measurements:

- accepted requirement coverage by verified reusable capabilities;
- reuse across independent Applications;
- composition depth and complexity;
- residual implementation work;
- semantic overlap and duplicate capability rate;
- implementation and binding substitution cost;
- state migrations and regressions;
- time, model tokens, and human interventions;
- marginal cost of the nth Application;
- semantic and end-to-end regression rate.

The primary claim is supported only if marginal cost declines as the verified
capability inventory grows without increasing semantic or end-to-end
regression rates. Application-local capabilities do not count as reuse.
Prompts, rubrics, recipes, and target capability labels for held-out cases must
not leak into the treatment inputs.

Benchmark design remains late, but benchmark-compatible instrumentation starts
with the CRUD proof. Every proof run records at least total and resolved
requirements, candidate count and binding-selection cost, resolution and
activation time, context/model tokens where applicable, residual
implementation, package and contract reuse, manual interventions, and E2E
result. Metric definitions and provenance are versioned before the first run so
later comparisons have a historical baseline.

## Security And Privacy Rules

- Portable artifacts contain no credentials, raw DSNs, user records, or local
  account identifiers.
- Secret and endpoint references are opaque and resolved only in the local
  policy boundary.
- Evidence is redacted according to its portability scope before publication.
- Resolver explanations expose rejection categories without leaking hidden
  candidates, secrets, or inaccessible contract metadata.
- Capability satisfaction never grants runtime permission by itself; normal
  Application and actor access decisions still apply.
- A state attachment never grants write authority by itself; activation and
  fencing establish the active writer.
- External authoritative state is never copied, deleted, or migrated merely
  because a binding changed.
- Destructive or irreversible migrations require explicit lifecycle authority
  and cannot be inferred from semantic compatibility alone.

## Failure Semantics

| Failure point | Required result |
| --- | --- |
| resolution | typed unsatisfied requirement; no local mutation |
| plan validation | rejected plan with explanation; no staging or authority change |
| provisioning | durable failed/recoverable operation; old lock remains active |
| migration before commit | staged generation is inactive; old generation and writer remain active |
| evidence verification | no authority switch; claim and assessment history retained |
| precondition or generation conflict | plan becomes inapplicable; create a new plan instead of rebasing silently |
| crash during authority switch | journal determines whether commit occurred; fence prevents two active writers |
| post-commit health failure | execute only the recorded recovery/rollback policy and preserve both lock histories |
| external irreversible failure | explicit reconcile/manual decision unless idempotency or compensation was proven |

## Ownership And Source Of Truth

| Concern | Source of truth |
| --- | --- |
| Application intent and requirements | immutable semantic Application revision |
| capability semantics | `CapabilityContract` revision |
| state semantics | `StateContract` revision plus existing schema/migration locks |
| portable implementation eligibility | `BindingDefinition` and package locks |
| local provider attachment | stable `BindingInstance` ref plus immutable revision chain |
| governed data identity and authority history | stable `StateSpace` ref plus immutable revision chain |
| selected satisfying configuration | admitted `ApplicationResolution` |
| proposed transition | `ResolutionPlan` |
| active runtime and authority | `WorkspaceLock` plus activation journal/history |
| historical verification | immutable `EvidenceClaim` |
| current trust/admissibility | derived `EvidenceAssessment` under named policy |
| graph and impact queries | rebuildable derived projections |

Human-facing labels such as “Semantic HEAD” or “Materialized HEAD” may be used
in UI, but machine contracts use exact semantic revision, resolution, plan,
and workspace-lock digests.

## Explicit Non-Goals For The First Program

- a universal ontology of all business domains;
- a graph database as a prerequisite;
- automatic extraction and publication of contracts by an Evolver;
- automatic unattended migrations across arbitrary external providers;
- multiple independent workspace authorities;
- rewriting the package manager or existing activation journal;
- making every legacy skill immediately provider-independent;
- a client plug-in or arbitrary composite-widget runtime ABI;
- multi-user semantic merge or CRDT mainline semantics;
- public registry federation before local resolution and rebinding are proven.

## Open Decisions

The roadmap must resolve these through executable evidence rather than naming
alone:

1. whether capability and state contract revisions use SemVer ranges, explicit
   compatibility edges, or both;
2. the smallest safe extension shape for `ProjectCompositionLock` and
   `WorkspaceLock`;
3. whether a binding instance exists before planning or may be provisioned as
   a plan output while retaining a reserved identity;
4. which state classes require a single writer and which support explicit
   multi-writer protocols;
5. how environment profiles are normalized and digest-addressed;
6. which evidence freshness policies are mandatory for production;
7. which external provider, time model, and compensation boundary the
   `booking.reserve` proof uses;
8. when a candidate capability has enough independent adoption to become
   reusable or platform-level.

## Related Documents

- [Capability, Binding, and State Separation Roadmap](capability-binding-state-separation-roadmap.md)
- [Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md)
- [Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md)
- [Project Composition, Presentation, and Development Context](project-composition-and-development-context.md)
- [Declarative Resource Workbench](declarative-resource-workbench.md)
- [Builder Intent-to-Prototype Architecture](builder-intent-to-prototype.md)
- [Governed Evolution](governed-evolution.md)
- [Distributed Service and Data Topology](distributed-service-and-data-topology.md)
- [Application Access, Permissions, and Roles](application-access-permissions.md)
