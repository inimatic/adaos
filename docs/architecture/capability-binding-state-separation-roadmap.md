# Capability, Binding, and State Separation Roadmap

Status: implementation roadmap for
[Capability, Binding, and State Separation](capability-binding-state-separation.md).

Last reviewed: 2026-09-26.

Implementation checkpoint: every non-deferred item through `CBS5` and every
`must`/`should` item in `CBS6` through `CBS9` has validated-local evidence as
of 2026-09-23. Optional `could` work and all explicitly deferred work remain
open. Exact implementation, compatibility, evidence, Builder-adaptation,
skill-migration, and legacy-removal status is recorded in
[CBS1-CBS5 Implementation](capability-binding-state-cbs1-cbs5-implementation.md).
The checklist below continues to distinguish that bounded proof from later
provider generalization and the remaining `should`, `could`, and `deferred`
work.

Post-checkpoint provider progress: the bounded Core-owned Gmail OAuth/REST
adapter now delivers a package-neutral mail contract and binding definition in
an independently published native provider package. Builder created a distinct
Inbox Triage consumer, release admission resolved both requirements against the
installed package, and an explicit second-Application attachment reused the
Core-owned account credential without exposing or copying secrets. Exact facts,
the Core authorization gap found by the proof, telemetry, and remaining latency
and identity-selection defects are recorded in
[Gmail CBS Reuse And Builder Beta Proof](gmail-cbs-reuse-builder-proof-2026-09-24.md).
The earlier single-consumer boundary remains in
[Gmail Mail Client: Stage-One CBS Beta Proof](gmail-mail-client-cbs-beta-proof-2026-09-24.md).
This closes the local two-consumer reuse proof, not federated registry naming,
automatic contract extraction, or universal provider portability.

Third-consumer checkpoint, 2026-09-25: Builder created and published the
minimal `mail_focus_reader@0.1.2` consumer against the same portable contract,
shared package, and provider-owned connected account. Native admission resolved
2 of 2 requirements; Trial, Applications permission review, publication,
workspace activation, real provider reads, and the Stable browser journey all
passed. Exact release facts, first-paint timings, token telemetry, systemic
fixes, and the still-failed one-shot criterion are recorded in
[Gmail CBS Third-Consumer Builder Proof](gmail-cbs-third-consumer-builder-proof-2026-09-25.md).
The follow-up `0.1.3` release was published with its exact source, Application
release, packages, and portable semantic projection. A separate clean subnet
imported 11 portable identities/records, installed the exact public release and
shared Gmail delivery, then automatically advanced `mail_focus_reader` from
`0.1.2` to `0.1.3` through the ordinary plan/apply protocol after registry
sync. That subnet deliberately has no copied Gmail secret, so the final local
account creation/attachment part of the cross-subnet reuse proof remains open.
Public callback materialization and autonomous-development efficiency retain
their separately recorded boundaries.

Registry follow-up, 2026-09-25: `mail_focus_reader@0.1.4` corrects all four
permission policies to canonical `grant_on_install`, and a reviewed clean-
subnet update now has exact install access plus native 2-of-2 CBS admission.
The signed `adaos-registry` GitHub webhook, global Root producer, zonal fanout,
retained reconnect replay, and subnet consumer have been exercised as one
production route. The clean subnet synchronized revision
`54b7cddc7cd2731c8a38a9110e5cc3dbd6a89827` and deterministically skipped its
three installed Applications as current. Portable semantic distribution no
longer depends on a Core restart or polling, although both remain recovery
paths.

Application lifecycle follow-up, 2026-09-26: the Applications dogfood Project
completed governed Trial, acceptance, exact finalize, shared publication, and
clean-subnet auto-update as `applications@0.1.38`. The exact release digest is
`sha256:92e3d0c12b8dbec064d609fee711f3aec682ce6d3592a5201fbbf4b61750959a`;
the registry source commit is `45a47c467429bb44e8dfd5cde251c64db57f4602`.
Run `appautorun.0c1a3099e37cde3113dd9054897b9dd9` applied the candidate on
the clean subnet with no failed or review-required result. A stale legacy
Scenario row found after activation led to a post-auto-update reconciliation
rail and a materialized-registry preference in the list API. The repaired
subnet now exposes Applications Scenario `0.1.65`, accepts it on Desktop, and
reaches ready materialization without missing required branches. This proves
that exact Application publication can reuse the registry notification and
ordinary plan/apply rails; it does not change the remaining local-credential
boundary in `CBS10-07`.

Maintained-Application follow-up, 2026-09-26: Research Platform was recovered
from its last canonical Forge source, preserved its immutable v1-v7 migration
chain, moved evidence to Core blob storage, and completed Trial through public
registry as `research_platform@0.1.20` with 4-of-4 exact admission. TLP then
published `research_tlp@0.3.16` as an independent consumer of the shared
research lifecycle and tracking contracts; its 4-of-4 admission, Trial,
acceptance, stable authority commit, and public registry publication passed.
The exact release digests and lifecycle findings are recorded in
[CBS1-CBS5 Implementation](capability-binding-state-cbs1-cbs5-implementation.md).
The run exposed one operational rather than semantic bottleneck: Candidate
materialization still recopies a large immutable MLflow vendor layer. A
verified content-addressed materialization cache/reflink optimization is
required before treating this latency as inherent CBS cost.

Maintained-Application batch qualification, 2026-09-26: Desktop plus the 15
selected maintained Applications were republished through the native
Application lifecycle and independently installed on subnet `192.168.0.30`.
Every installed model reports `auto_update=true`, `update_available=false`,
and the derived read-only CBS stages `compiled -> admitted -> ready -> active
-> committed`. The exact versions and release digests are recorded in
[CBS1-CBS5 Implementation](capability-binding-state-cbs1-cbs5-implementation.md).
Runtime receipts cover Applications scenario selection, Drive navigation,
Media Center's provider-owned library agent, and a byte-identical Notebook
binary attachment upload/download. The subnet runs one `api serve` listener on
`127.0.0.1:8777` and no production supervisor.

The batch also exercised a real Application auto-update failure chain. A
topology-incomplete Media Center update first failed closed, then a retry
exposed lock inversion between registry synchronization and the Application
deployment worker. Registry import/source alignment now remains under the
global component lock, while automatic Application updates run only after that
lock is released. Failed and known-partial terminal operations are
deterministically retried. The final run advanced Media Center from `0.6.110`
to `0.6.111` with one applied update, no failures, and no uncertain result.
This validates delivery and recovery semantics; it does not make startup
latency acceptable. The same run measured approximately 74 seconds to runtime
context entry, 19 seconds in router initialization, and 6.5 seconds in
materialization-status hydration. Duplicate `adaos_connect` view
materialization and SQLite/fsync pressure remain explicit runtime performance
debt.

Runtime-degradation follow-up, 2026-09-26: live logs traced a Media Center
feedback loop to terminal `media_library_agent` snapshot replays emitting
`catalog.changed` as if a new job had completed. The correction now marks
snapshot replay as non-authoritative, strips the internal marker before stream
delivery, and emits the catalog event only for a real terminal transition.
An intermediate `0.6.113` release proved that publication preflight must
inspect the authoritative owner-development workspace rather than its managed
runtime projection: its manifest advanced, but the intended handler change was
absent. The final `media_center@0.6.114` release
`sha256:4c47394b70f86cecf82c93c18029732b121f5a452358e6dd9d38d2430eaa9c7e`
contains exact `media_library_agent@0.6.54` package
`sha256:205f206c38665cd2ecc5f777ac1ab6b0e28e58172b707f92b332a9ce7083bb4d`.
The package was inspected before promotion, all 100 provider tests passed, and
forced rendition/scan snapshot requests on the independent subnet emitted no
new `catalog.changed` event. Auto-update deployed all four components and the
governed reconciliation API committed installation revision 7; automatic
reconciliation of a completed inner deployment whose outer
`ApplicationOperation` remains `applying` is tracked as MUST Dev Ticket
`dticket.01M3FA71K71C9ES1F5Y2S87RGV`.

## Outcome

AdaOS can preserve Application and eligible state identities while changing a
simulation into production, substituting an implementation, moving that
implementation between packages, or rebinding a state provider. Every change
is resolved, planned, evidenced, and committed through the existing activation
and `WorkspaceLock` authority.

The first accepted result is a CRUD vertical slice proving five architectural
invariants. The next result is a domain capability proving that the model also
handles business invariants, side effects, time, provider failure, and
compensation.

## Priority Vocabulary

- `[must]`: required for the CRUD architecture proof and its safe activation
  boundary. The program is not successful without it.
- `[should]`: required before claiming the model is useful beyond the bounded
  CRUD proof or before broad production use.
- `[could]`: useful leverage or ergonomics that must not delay the must proof.
- `[deferred]`: deliberately excluded from this program until its prerequisite
  evidence exists.

Priority does not report delivery state. Every checklist item remains unchecked
until its own acceptance evidence exists.

## Maturity Vocabulary

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

A checked task has at least reproducible `validated-local` evidence at an exact
revision. Documentation alone reaches `specified`, not `implemented`. A
successful happy-path demo does not close concurrency, rollback, recovery,
freshness, or substitution tasks.

## Current Baseline

Useful mechanisms already exist:

- semantic prototype v2 and its compiler provide an Application-level authoring
  seam;
- Resource Workbench and local CRUD machinery provide a realistic first proof;
- relational storage requirements, provider capability negotiation, opaque
  locators, owner isolation, and migration ownership already separate some
  provider concerns;
- deterministic package digests, `ProjectRelease`, `ProjectCompositionLock`,
  and package dependency resolution already exist;
- `WorkspaceLock`, writer locking, activation phases, staging, checkpoints,
  migrations, health verification, commit, rollback, and history already exist;
- package, workflow, Builder, Application, and E2E evidence surfaces already
  exist.

Not yet established as one coherent ABI:

- portable `CapabilityContract` and `StateContract`;
- portable `BindingDefinition` distinct from local `BindingInstance`;
- local `StateSpace` identity independent of a skill or provider path;
- state ports with consumer requirements and effective guarantee checks;
- immutable admitted `ApplicationResolution`;
- immutable `ResolutionPlan` between resolution and activation;
- immutable evidence claims with time/dependency-sensitive assessments;
- capability-aware extensions to the existing locks;
- a rebuildable semantic/impact graph over these records.

This baseline means the program is an integration and identity refactoring,
not a greenfield runtime rewrite.

## Program Guardrails

1. Do not reinterpret `skill.yaml.capabilities` as provided semantic
   capabilities.
2. Do not put package, skill, module, provider, storage, endpoint, or credential
   names in an Application requirement.
3. Do not introduce another active-workspace lock or activation authority.
4. Do not let the semantic resolver provision, migrate, or switch authority.
5. Do not mutate an admitted `ApplicationResolution`, `ResolutionPlan`, or
   `EvidenceClaim`.
6. Do not use Web UI `state_ref` as persistent data identity; use
   `state_space_ref`.
7. Do not duplicate schema locks, migration locks, or `data_lifecycle` SQL in a
   `StateContract`.
8. Do not claim portable state when the declared class is `provider_bound` or
   `external_authoritative`.
9. Do not infer atomicity across external systems; define the authority commit
   boundary and recovery behavior.
10. Do not use a graph, recommendation, or Evolver output as a source of truth.
11. Do not count an Application-local whole-Application capability as reuse.
12. Do not close a substitution task without proving the original semantic
    revision remained byte-identical.
13. Implement every `must` item before `CBS5` only to the smallest form needed
    by the selected CRUD proof; do not generalize an unexercised dimension.

## CRUD-First Scope Budget

`CBS1` through `CBS4` are not permission to build the final universal
framework before integration. Their first implementation is one walking
skeleton with these hard bounds:

| Dimension | Pre-CBS5 implementation |
| --- | --- |
| environment | one digest-addressed local profile with simulation and production modes |
| capability | one bounded `resource.records.manage` contract |
| state | one selected CRUD state contract and a portable production state space; simulation uses a separate disposable state space |
| evidence | `capability_conformance` and `state_compatibility` claims plus the minimum assessment needed for admission |
| providers | deterministic simulation and one current local production provider |
| writer protocol | one fenced writer; multi-writer semantics are not designed implicitly |
| resolver | bounded deterministic enumeration of registered candidates; no general optimizer |
| package resolution | the existing exact package/dependency resolver and locks |
| planning | only steps exercised by simulation activation, local production activation, package relocation, production rebinding, and injected recovery |
| UI | the existing fixed semantic/Web UI and Resource Workbench surfaces |

The validated-local extension adds a distinct restricted sandbox
materialization to the same local profile. This does not widen the resolver to
new profile classes or distributed providers.

Target schemas may reserve versioned extension points, but the runtime rejects
unsupported environment, evidence, portability, provider, and writer modes. A
pre-CBS5 task is not expanded merely because the target architecture names a
future case. Generalization requires evidence from the CRUD proof or an
explicitly re-prioritized requirement.

## Dependency And Ownership Rules

| Work | Owning roadmap | This roadmap consumes or contributes |
| --- | --- | --- |
| package build, fetch, dependency closure, activation journal, rollback | [Artifact Pipeline](artifact-source-package-activation-roadmap.md) | contributes resolution and state/evidence plan inputs; consumes exact package and activation outcomes |
| Application release, install, Trial, Catalog, publisher authority | [Application Lifecycle](application-lifecycle-and-distribution-roadmap.md) | contributes semantic resolution and viability evidence |
| declarative CRUD operations and UI | [Resource Workbench](declarative-resource-workbench-roadmap.md) | uses it as the first vertical slice; does not redefine its UI ABI |
| Application and actor permission decisions | [Application Access](application-access-permissions-roadmap.md) | declares capability authority needs; consumes effective decisions |
| distributed state placement and fencing | [Distributed Topology](distributed-service-and-data-topology-roadmap.md) | establishes local v1 fields and invariants; distributed protocols remain with that owner |
| Builder authoring and prototype compilation | [Builder Intent-to-Prototype](builder-intent-to-prototype-roadmap.md) | adds requirement and resolution reports without replacing semantic prototype ABI |
| Evolver governance | [Governed Evolution](governed-evolution-roadmap.md) | supplies derived observations and candidate proposals only |
| end-to-end Application creation, evolution, and capability-package extraction | [Semantic Application Composition And Evolution](application-semantic-composition-roadmap.md) | orders milestones from the owning roadmaps; owns no duplicate schema, resolver, package, or activation mechanism |

When another roadmap owns a mechanism, this roadmap closes only its adapter,
contract, or end-to-end proof. It does not duplicate the owning checklist.

## Delivery Sequence

```text
CBS0 vocabulary and legacy inventory
  -> CBS1 portable contract spine
  -> CBS2 local binding and state identities
  -> CBS3 semantic resolution
  -> CBS4 transition planning and activation integration
  -> CBS5 CRUD five-invariant proof
  -> CBS6 evidence freshness
  -> CBS7 booking.reserve domain capability proof
  -> CBS8 derived impact/evolution views
  -> CBS9 matched evolutionary benchmark
```

`CBS1` and the read-only inventory part of `CBS2` may proceed in parallel.
Native state adoption does not begin before deterministic legacy projection and
round-trip tests exist. `CBS8` must not become a prerequisite for `CBS3` or
`CBS4`.

The user-facing linear delivery order is maintained by the Semantic Application
Composition roadmap. This roadmap remains the implementation authority for the
capability, binding, state, resolution, and plan milestones referenced there.

## Milestone CBS0: Vocabulary And Compatibility Inventory

**Outcome:** the six identities and current compatibility seams are explicit
before schemas or runtime behavior change.

**Exit proof:** a machine-readable inventory maps representative current CRUD,
relational storage, package, lock, and evidence records to the target terms
without modifying runtime state.

- [x] `[must]` `CBS0-01` Adopt the normative vocabulary and ownership boundaries
  in the architecture and roadmap inventory.
- [x] `[must]` `CBS0-02` Inventory every current use of “capability” and classify
  it as semantic capability, requested SDK permission, provider feature, UI
  affordance, deployment feature, or legacy ambiguous use.
- [x] `[must]` `CBS0-03` Inventory current state identities, owners, database
  paths, `data_lifecycle`, schema locks, migration locks, relational bindings,
  and destructive lifecycle operations for the selected CRUD fixture.
- [x] `[must]` `CBS0-04` Inventory current `ProjectCompositionLock`,
  `ProjectRelease`, `WorkspaceLock`, activation-operation, and lock-history
  fields; identify additive extension points and reject a second authority.
- [x] `[must]` `CBS0-05` Reserve unambiguous schema and reference namespaces for
  capability contracts, state contracts, binding definitions/instances, state
  spaces, resolutions, plans, claims, and assessments.
- [x] `[must]` `CBS0-06` Define canonical JSON normalization, digest, stable-ref,
  version, compatibility, and unknown-field rejection rules shared by the new
  records.
- [x] `[must]` `CBS0-07` Select one existing real CRUD resource with fixture
  data, access rules, optimistic revision behavior, and an explicit migration
  boundary for the vertical proof.
- [x] `[should]` `CBS0-08` Publish a terminology lint or schema-review check that
  flags persistent `state_ref` and ambiguous new manifest `capabilities`
  declarations.
- [x] `[could]` `CBS0-09` Add a developer-facing identity map that explains the
  current package, semantic, binding, and state refs for one installation.
- [ ] `[deferred]` `CBS0-10` Rename all legacy skill, Project, provider, and
  permission identifiers to the new vocabulary.

Validated-local checkpoint, 2026-09-21:

- the selected fixture, current authority/state/package surfaces, reserved
  namespaces, compatibility rules, and canonical digest vector are fixed in
  [CBS0 Capability, Binding, and State Compatibility Inventory](capability-binding-state-cbs0-inventory.md)
  and its
  [machine-readable inventory](capability-binding-state-cbs0-inventory.json);
- focused inventory, local/prototype CRUD, and artifact contract tests pass;
- CBS0 changed no runtime state and introduced no new runtime ABI. Schema names
  are reserved for CBS1, not reported as implemented.

## Milestone CBS1: Portable Contract Spine

**Outcome:** semantic capability, state, implementation, package, and evidence
facts can be published and validated independently of an installation.

**Exit proof:** two package layouts deliver the exact same binding-definition
ref and digest for one capability, both validate against the same contracts,
and no portable record contains installation secrets or local paths.

- [x] `[must]` `CBS1-01` Add a fail-closed `adaos.capability.contract.v1` JSON
  Schema and immutable typed model with stable ref, version, I/O/errors,
  invariants, effects, authority requirements, dependencies, state ports,
  conformance refs, and compatibility metadata.
- [x] `[must]` `CBS1-02` Add a fail-closed `adaos.state.contract.v1` schema and
  model with schema locks, invariant refs, guarantee envelope, lifecycle,
  ownership constraints, portability class, and migration compatibility.
- [x] `[must]` `CBS1-03` Add `StatePort` validation for contract range, access
  mode, consumer consistency/durability/isolation requirements, and mutation
  authority requirements.
- [x] `[must]` `CBS1-04` Add a fail-closed
  `adaos.binding.definition.v1` schema and model for implemented contract,
  entry protocol, state support, modes, environment constraints, authority,
  logical implementation entry point, and conformance obligations; reject
  physical package paths or members in canonical definition content.
- [x] `[must]` `CBS1-05` Add package delivery metadata that maps an exact
  binding-definition digest and logical entry point to a physical member in
  existing immutable `ArtifactPackageRef` and `ProjectRelease` facts. Prove a
  package relocation changes delivery/package digests without changing the
  binding-definition digest.
- [x] `[must]` `CBS1-06` Add an immutable `adaos.evidence.claim.v1` model with
  typed claim kind, exact subjects, environment digest, dependency
  observations, suite/evidence digest, provenance, issue time, freshness
  policy, result, redaction, and portability scope.
- [x] `[must]` `CBS1-07` Define the first conformance scenario records only for
  `capability_conformance` and `state_compatibility`. Add migration-correctness
  and installation-health claim kinds only when a later proof exercises them.
- [x] `[must]` `CBS1-08` Add semantic validation that a state contract references
  current schema/migration/data-lifecycle locks without copying or contradicting
  their contents.
- [x] `[must]` `CBS1-09` Keep requested SDK permissions and provided semantic
  capabilities in different manifest keys/files, models, and validation error
  messages.
- [x] `[must]` `CBS1-10` Add canonical serialization and digest round-trip,
  mutation rejection, unknown-version, unknown-field, duplicate-ref, and
  malicious locator/secret fixture tests.
- [x] `[should]` `CBS1-11` Define explicit compatibility edges in addition to
  version ranges for contracts whose semantic compatibility cannot be inferred
  from SemVer alone.
- [x] `[should]` `CBS1-12` Add signature/trust-domain admission for portable
  contracts and claims by reusing package attestation policy.
- [x] `[could]` `CBS1-13` Generate human-readable contract reference pages and
  diffs from canonical records.
- [ ] `[deferred]` `CBS1-14` Publish a federated public capability registry or
  solve cross-registry naming governance.

## Milestone CBS2: BindingInstance And StateSpace

**Outcome:** installation materialization and durable data identity are local,
explicit, and independent of package topology.

**Exit proof:** the selected legacy CRUD store is projected to stable
`BindingInstance` and `StateSpace` refs with immutable revision histories, can
be reloaded deterministically, and preserves existing records and access
behavior.

- [x] `[must]` `CBS2-01` Add fail-closed
  `adaos.binding.instance.v1` and `adaos.state.space.v1` schemas and immutable
  typed revision models with stable refs, monotonic revision numbers,
  predecessor digests, workspace/tenant scope, and opaque local references.
- [x] `[must]` `CBS2-02` Separate logical owner, lifecycle authority, custodian
  binding, mutation authority, and physical locator in the state-space model.
- [x] `[must]` `CBS2-03` Define append-only binding-instance and state-space
  revision semantics. State generation, storage attachment, and writer
  authority epoch changes create a new revision; health/readiness observations
  bind to a revision without mutating it; stale writer epochs are rejected.
- [x] `[must]` `CBS2-04` Define relation records for binding-instance
  `reads`, `writes`, `appends`, and `caches` access without embedding a single
  state owner into the binding identity.
- [x] `[must]` `CBS2-05` Implement effective guarantee calculation from state
  contract, provider capabilities, state space, binding definition/instance,
  and environment profile.
- [x] `[must]` `CBS2-06` Reject a state-port attachment unless every consumer
  requirement is included in the effective guarantee set.
- [x] `[must]` `CBS2-07` Build a deterministic read-only compatibility projector
  for the selected skill-owned CRUD data and record its source package,
  owner, path/locator, schema, lifecycle, and generation provenance.
- [x] `[must]` `CBS2-08` Preserve current relational opaque-locator, secret-ref,
  private-owner, migration-owner, and provider-negotiation invariants.
- [x] `[must]` `CBS2-09` Define allocate, attach, adopt, import, fork,
  reconstruct, detach, archive, and destroy as distinct lifecycle operations;
  installation alone must not imply ownership or deletion authority.
- [x] `[must]` `CBS2-10` Add redaction tests proving portable output and graph
  inputs cannot expose DSNs, secrets, local absolute paths, or protected account
  identities.
- [x] `[should]` `CBS2-11` Add an operator/API inspector for state identity,
  owner, contract, custodian, generation, portability, and health.
- [x] `[should]` `CBS2-12` Add backup/restore provenance and storage-capacity
  observations to effective guarantees where existing providers expose them.
- [x] `[could]` `CBS2-13` Project additional existing stores to measure legacy
  shape diversity before native adoption.
- [ ] `[deferred]` `CBS2-14` Automatically migrate every legacy skill database
  or remove legacy owner/path conventions.

## Milestone CBS3: Semantic Resolution

**Outcome:** AdaOS selects exact satisfying contracts and implementations
without changing local authority.

**Exit proof:** one unchanged semantic Application resolves independently for
semantic, simulation, and local-production targets; rejected alternatives are
explainable; exact package closure precedes evidence admission; and resolution
performs no writes to bindings, state, or lock.

- [x] `[must]` `CBS3-01` Define the Application requirement ABI with capability
  ref/range, environment target, policy constraints, and evidence threshold;
  reject implementation and provider fields.
- [x] `[must]` `CBS3-02` Implement a bounded deterministic semantic resolver
  over the selected CRUD contracts, binding definitions, the local environment
  profile, and effective guarantees. Its output is an ephemeral
  `SemanticCandidate` with package constraints and evidence obligations, not an
  `ApplicationResolution`.
- [x] `[must]` `CBS3-03` Pass each semantic candidate to the existing exact
  package/dependency resolver, preserve package-level conflict authority, and
  obtain an exact package closure and delivery mapping before evidence
  admission.
- [x] `[must]` `CBS3-04` Add immutable
  `adaos.application.resolution.v1`, created only after exact-package evidence
  and policy admission, with semantic revision, environment and policy digests,
  selected contracts/definitions, exact package closure/delivery mappings,
  local instances or provisioning obligations, state attachments, evidence,
  and rejection explanations.
- [x] `[must]` `CBS3-05` Distinguish ephemeral semantic and package-resolved
  candidates from the selected, admitted, immutable resolution; prove no
  resolution record exists when package resolution or evidence admission fails.
- [x] `[must]` `CBS3-06` Produce typed unmet requirement, incompatible state,
  insufficient guarantee, missing authority, unavailable instance, stale
  evidence, and package-conflict results.
- [x] `[must]` `CBS3-07` Derive semantic, simulation, sandbox, and production
  viability without creating separate Application identities.
- [x] `[must]` `CBS3-08` Prove resolver purity with tests that snapshot all local
  stores and `WorkspaceLock` before and after success, ambiguity, and failure.
- [x] `[should]` `CBS3-09` Add deterministic ranking policy and an explanation
  trace for why the selected binding outranked eligible alternatives.
- [x] `[should]` `CBS3-10` Surface capability gaps and evidence obligations in
  the Builder final verification report.
- [x] `[could]` `CBS3-11` Return several Pareto candidates for interactive cost,
  locality, privacy, or provider selection.
- [ ] `[deferred]` `CBS3-12` Add a general-purpose global optimizer or
  unbounded SAT/constraint solver before the bounded resolver is measured.

## Milestone CBS4: ResolutionPlan And Activation Integration

**Outcome:** semantic selection becomes an explicit, generation-safe transition
that extends the existing activation pipeline.

**Exit proof:** activation executes an exact plan digest; stale locks,
generations, authority epochs, or evidence reject before commit; injected
pre-commit failures preserve the old lock, active state generation, and writer.

- [x] `[must]` `CBS4-01` Add immutable `adaos.resolution.plan.v1` with desired
  resolution, base lock, desired bindings and state attachments, provisioning,
  migrations, evidence, steps, expected generations, compensation/recovery,
  expiry, and canonical digest.
- [x] `[must]` `CBS4-02` Classify each plan step as read-only, idempotent,
  compensatable, reconcile-only, or irreversible and define retry rules.
- [x] `[must]` `CBS4-03` Extend the existing activation operation/journal to pin
  `application_resolution_digest` and `resolution_plan_digest` through every
  phase and recovery path.
- [x] `[must]` `CBS4-04` Validate base `WorkspaceLock` digest/revision, binding
  instance revision digests, state-space revision digests, state generations,
  authority epochs, package digests, and evidence freshness immediately before
  the commit boundary.
- [x] `[must]` `CBS4-05` Extend or digest-link `ProjectCompositionLock` and
  `WorkspaceLock` with active resolution, binding-instance and state-space refs
  plus exact revision digests, state attachments, generation/epoch, and
  evidence-set facts without creating another active lock.
- [x] `[must]` `CBS4-06` Implement state staging so a migrated/provisioned
  generation cannot receive active writes before the authority switch.
- [x] `[must]` `CBS4-07` Implement fencing-token/authority-epoch validation for
  the selected single-writer state class and prove an old writer is rejected
  after commit.
- [x] `[must]` `CBS4-08` Define the exact authority commit point and atomically
  persist the new `WorkspaceLock`, new immutable state-space revision, active
  state generation, and writer epoch within the supported local transaction
  boundary.
- [x] `[must]` `CBS4-09` Prove every injected failure before commit satisfies
  `WorkspaceLock_after == WorkspaceLock_before` and leaves the previous active
  state generation and writer usable.
- [x] `[must]` `CBS4-10` Prove crash recovery before, during, and after lock
  switching can distinguish uncommitted staging from committed intent and
  never admits two active writers.
- [x] `[must]` `CBS4-11` Preserve staged artifacts, evidence, backups, and
  journals for bounded diagnosis and idempotent cleanup without presenting them
  as active.
- [x] `[must]` `CBS4-12` Reject unattended plans containing unproven
  irreversible external actions; require explicit approval, idempotency,
  compensation, or reconcile-only recovery.
- [x] `[should]` `CBS4-13` Add a human-readable plan diff covering semantic,
  package, binding, state, migration, authority, evidence, and rollback changes.
- [x] `[should]` `CBS4-14` Add bounded plan expiry and background re-planning
  suggestions while preserving the rule that activation never silently rebases.
- [x] `[could]` `CBS4-15` Cache still-valid plans by full input digest for dry-run
  inspection.
- [ ] `[deferred]` `CBS4-16` Implement atomic transactions across arbitrary
  external SaaS systems or generic unattended destructive migration.

## Milestone CBS5: CRUD Vertical Proof

**Outcome:** the architecture is executable against current Core storage,
Resource Workbench, packages, and activation.

**Exit proof:** one evidence bundle proves all five invariants from exact
semantic revision through final `WorkspaceLock`, including data digests and
failure injection.

- [x] `[must]` `CBS5-01` Define the bounded `resource.records.manage` capability
  contract and one record state contract over the selected real CRUD resource.
- [x] `[must]` `CBS5-02` Preserve Core ownership of persistence, transactions,
  schema validation, optimistic revision conflict, access decisions, events,
  and tracing.
- [x] `[must]` `CBS5-03` Implement a deterministic simulation
  `BindingDefinition` with its own disposable state space and explicit
  non-production effects.
- [x] `[must]` `CBS5-04` Implement a local-production `BindingDefinition` and
  Binding A instance over the existing CRUD/storage machinery and allocate one
  stable production state space.
- [x] `[must]` `CBS5-05` Run the same contract scenarios against simulation and
  local production and emit immutable conformance claims for exact subjects.
- [x] `[must]` `CBS5-06` Prove materialization independence: activate simulation
  then production with an unchanged semantic Application revision digest while
  explicitly allowing and expecting different simulation and production
  `state_space_ref` values.
- [x] `[must]` `CBS5-07` Prove package-topology independence: move the production
  implementation to a different package/release topology without changing the
  requirement, capability-contract identity, binding-definition ref, or
  binding-definition digest.
- [x] `[must]` `CBS5-08` Prove state continuity in a separate production Binding
  A to production Binding B transition: retain the production
  `state_space_ref`, record count, canonical record digests, schema lineage, and
  access semantics through state-safe rebinding. Simulation state does not
  participate in this invariant.
- [x] `[must]` `CBS5-09` Prove Application independence by schema assertion and
  review that no semantic field contains selected package, binding, provider,
  storage, endpoint, or credential identities.
- [x] `[must]` `CBS5-10` Prove atomic rebinding with failure injection at every
  plan phase, stale generation conflicts, writer overlap attempts, process
  termination at the commit boundary, restart recovery, and health-check
  failure.
- [x] `[must]` `CBS5-11` Re-run current Resource Workbench, Application,
  package/activation, relational storage, access, and migration regression
  suites.
- [x] `[must]` `CBS5-12` Emit versioned benchmark-compatible telemetry from the
  first CRUD run: requirement totals/resolution, candidate and binding-selection
  cost, resolution/activation duration, context/tokens, residual
  implementation, package/contract reuse, manual interventions, and E2E
  result.
- [x] `[must]` `CBS5-13` Preserve an exact evidence bundle containing source and
  package digests, semantic revision, contracts, resolution, plan, before/after
  locks, local revision digests, state digests, claim/assessment records,
  telemetry, trace IDs, and test output.
- [x] `[should]` `CBS5-14` Repeat the proof with SQLite and PostgreSQL or another
  materially different provider profile.
- [x] `[should]` `CBS5-15` Exercise sandbox viability separately from simulation
  and local production.
- [x] `[could]` `CBS5-16` Add a compact UI view that animates requirement,
  resolution, plan, activation, and lock without making that view authoritative.
- [ ] `[deferred]` `CBS5-17` Generalize the proof to every Resource Workbench
  resource before the domain-capability result is known.

Validated-local checkpoint, 2026-09-23:

- portable contract trust admission, explicit compatibility edges, generated
  references/diffs, terminology lint, and installation identity mapping are
  covered by the contract suite;
- local revision history, state inspection, backup/capacity observations,
  legacy JSON and Prototype/SQLite projection, resolver ranking/Pareto reports,
  Builder gaps/evidence obligations, plan diff/replanning/cache, and sandbox
  viability have focused executable tests;
- the broad `CBS5-11` regression gate passes across Resource Workbench,
  Application runtime/access/data lifecycle, package storage/activation,
  relational storage, and migrations;
- a concurrent first-open SQLite/WAL initialization defect found by that gate
  was fixed, followed by twenty consecutive race reproductions and a clean
  complete regression run;
- the Applications package now exposes a compact, derived CBS lifecycle view
  over the exact Application record. Its requirement, resolution, plan,
  activation, and lock stages passed both wide and compact browser validation
  in Builder task `task.01M36WQ12JBWTK96Z1CG4MYAH5` without becoming an
  authority source;
- the same package was admitted as Candidate
  `applications-0-1-34-2a7d35b40618`, activated in Trial, and accepted through
  the public component-update endpoint. The resulting stable
  `RuntimeSelection` pins release
  `sha256:671797afb038e28b2453de707022d20ede8d7654ba0f7093d0c32a7d35b40618`
  and `WorkspaceLock`
  `sha256:4352e5abe81617367521c5c31c29b6f396322858a06b47994474ab950f9e1581`;
- all non-deferred tasks through `CBS5` are therefore closed at
  `validated-local`; no deferred item has been reclassified or implemented.

### CRUD acceptance matrix

| Invariant | Required disturbance | Required unchanged identity or state | Evidence |
| --- | --- | --- | --- |
| materialization independence | simulation state/binding -> production Binding A and separate production state | semantic Application revision only; simulation and production state refs may differ | resolutions, plans, locks, semantic digest, distinct state refs |
| package-topology independence | move implementation between packages | requirement, capability contract, and binding definition refs/digests | old/new package closure, delivery mappings, and shared conformance suite |
| state continuity | production Binding A -> production Binding B | production `state_space_ref` and canonical records | before/after revision, record, generation, and epoch evidence |
| Application independence | inspect every semantic field | absence of implementation topology | schema validation and explicit negative scan |
| atomic rebinding | phase faults, conflict, kill/restart | old authority before commit; one fenced writer after commit | journal, lock history, epochs, health and recovery traces |

### Benchmark-compatible telemetry

The scientific benchmark remains in `CBS9`, but data collection starts with
the first `CBS5` run. A versioned run record captures raw measurements and
provenance without claiming that one CRUD result demonstrates an evolutionary
trend.

Minimum fields:

```text
requirements_total
requirements_resolved
resolver_candidates_evaluated
binding_selection_duration_ms
binding_selection_evaluations
resolution_duration_ms
activation_duration_ms
context_input_tokens
context_output_tokens
residual_implementation_refs_and_diff_stats
packages_selected_existing
packages_created_for_run
contracts_selected_existing
contracts_created_for_run
manual_interventions_count
manual_intervention_reason_codes
e2e_result
```

Every record pins the metric-schema version, run ID, semantic revision,
contract, package, resolution, plan, environment, and resulting workspace-lock
digests. Missing or inapplicable values are explicit rather than coerced to
zero. Later benchmark methodology may derive normalized measures, but it must
retain these original observations as the historical baseline.

## Milestone CBS6: Evidence Freshness And External Change

**Outcome:** external drift changes current trust without rewriting history or
misclassifying an unchanged package.

**Exit proof:** a synthetic external version/fingerprint change turns a current
claim assessment stale, blocks or obligates production resolution by policy,
and a new verification claim produces either admissible or incompatible state.

- [x] `[must]` `CBS6-01` Implement derived `EvidenceAssessment` with
  `admissible`, `stale`, `superseded`, `incompatible`, and `revoked` states.
- [x] `[must]` `CBS6-02` Evaluate maximum age, subject digests, environment
  digest, dependency versions/fingerprints, trust domain, and explicit
  revocation without mutating the source claim.
- [x] `[must]` `CBS6-03` Add policy-specific handling so stale evidence may be
  visible for semantic/simulation use but cannot silently satisfy production.
- [x] `[should]` `CBS6-04` Add a bounded External Change Monitor adapter that
  writes immutable observations and triggers assessment rebuilds.
- [x] `[should]` `CBS6-05` Reverify one changing external API/provider and emit a
  new claim whose outcome is independently `verified` or `incompatible`.
- [x] `[should]` `CBS6-06` Add dependency/freshness impact queries for affected
  definitions, installed resolutions, plans, and Applications.
- [x] `[should]` `CBS6-07` Add operator and Builder explanations that distinguish
  "historically verified but stale" from "newly proven incompatible."
- [ ] `[could]` `CBS6-08` Prioritize revalidation queues by installed exposure,
  state risk, and production viability impact.
- [ ] `[deferred]` `CBS6-09` Continuously probe every external service or grant a
  universal freshness SLA independent of provider policy.

Validated-local checkpoint: immutable external observations, assessment
rebuild, bounded Jira-style drift/reverification, dependency impact, activation
recheck, and distinct Builder/operator explanations pass focused tests.

## Milestone CBS7: `booking.reserve` Domain Capability Proof

**Outcome:** `booking.reserve` proves that the abstractions survive behavior
more demanding than CRUD and exercise provider/state separation independently
of workflow implementation.

**Exit proof:** `booking.reserve` passes contract and end-to-end scenarios over
read-only `AvailabilityState`, writable `BookingState`, and append-only
`AuditState`, covering invariants, time intervals, concurrency, idempotency,
side effects, composition, provider failure, partial effects,
compensation/reconciliation, rebinding, and evidence drift.

- [x] `[should]` `CBS7-01` Freeze the `booking.reserve` fixture, external
  provider boundary, deterministic clock/concurrency harness, and acceptance
  stories before implementation.
- [x] `[should]` `CBS7-02` Define capability and state contracts without exposing
  workflow, package, class, or provider topology in the semantic Application;
  model `AvailabilityState` as read, `BookingState` as write, and `AuditState`
  as append.
- [x] `[should]` `CBS7-03` Define idempotency keys, temporal bounds, conflict
  semantics, effect receipts, and compensation or reconciliation outcomes.
- [x] `[should]` `CBS7-04` Compose at least one supporting capability and prove
  that composition edges remain explainable in resolution and evidence.
- [x] `[should]` `CBS7-05` Implement simulation and production-capable bindings
  sharing one contract scenario suite.
- [x] `[should]` `CBS7-06` Inject provider timeout, partial effect, duplicate
  request, stale read, concurrent decision, and recovery/reconciliation cases.
- [x] `[should]` `CBS7-07` Rebind or repackage the implementation while
  preserving every eligible semantic/state identity and explicitly changing
  every ineligible identity.
- [x] `[should]` `CBS7-08` Invalidate external evidence and prove resolver and
  planner behavior under stale, reverified, and incompatible assessments.
- [x] `[should]` `CBS7-09` Compare the domain result with CRUD and revise any
  abstraction that required domain behavior to leak into generic package or
  storage identity.
- [ ] `[could]` `CBS7-10` Add `order.approve` later as an independent workflow-
  heavy comparison if it tests materially different composition or
  compensation behavior.
- [ ] `[deferred]` `CBS7-11` Claim universal domain coverage from one or two
  proofs.

Validated-local checkpoint: the frozen booking fixture passes shared binding
scenarios plus concurrency, idempotency, timeout/partial-effect, compensation,
reconciliation, rebinding, state continuity, and evidence-gated planning.

## Milestone CBS8: Derived Graphs And Read-Only Evolver

**Outcome:** impact and reuse intelligence can be rebuilt from authoritative
records and can propose changes without gaining mutation authority.

**Exit proof:** deleting and rebuilding all derived indexes produces the same
canonical edges and impact results; no activation or identity record is lost.

- [x] `[should]` `CBS8-01` Define versioned derived-edge provenance with source
  refs/digests, build policy, `as_of`, and deterministic ordering.
- [x] `[should]` `CBS8-02` Materialize the Semantic Graph edges for requirements,
  contracts, definitions, packages, instances, state spaces, and evidence.
- [x] `[should]` `CBS8-03` Materialize an Impact Graph that starts from changed
  capability/state contracts, definitions, packages, dependencies, or evidence
  and reaches affected resolutions, plans, locks, and Applications.
- [x] `[should]` `CBS8-04` Prove complete deletion and deterministic rebuild from
  portable/local authorities.
- [x] `[should]` `CBS8-05` Add read-only Evolver observations for gaps, repeated
  schemas/operations, package co-occurrence, overlap, migration failures, and
  stale evidence.
- [x] `[should]` `CBS8-06` Add candidate proposal records for capability
  extraction, composition, deprecation, or migration; require normal review and
  publication paths. The proposal is not a package and grants no source,
  publication, data, or activation authority.
- [x] `[should]` `CBS8-07` Define and enforce capability maturity
  `application-local -> candidate -> reusable -> platform`. Advancement to
  `reusable` requires an extracted package, conformance evidence, and an
  independent consumer; execution of that curation path is coordinated by
  `ASC7` in the Semantic Application Composition roadmap.
- [ ] `[could]` `CBS8-08` Add graph and impact visualization to Builder,
  Applications component detail, or Desktop System diagnostics.
- [ ] `[could]` `CBS8-09` Use a graph database or incremental cache only after
  measurements show the rebuildable relational/file projection is insufficient.
- [ ] `[deferred]` `CBS8-10` Allow the Evolver to publish contracts, execute
  migrations, rewrite Applications, or activate bindings autonomously.

Validated-local checkpoint: graph provenance, semantic/impact/viability
projections, deterministic delete/rebuild, maturity gates, advisory proposals,
and all listed Evolver observations pass focused tests without mutation
authority.

## Milestone CBS9: Evolutionary Benchmark

**Outcome:** AdaOS can evaluate whether the capability inventory reduces the
marginal cost of new Applications without increasing regressions or hiding
work in oversized capabilities.

**Exit proof:** a frozen matched benchmark and held-out evaluation report
contain exact inputs, treatments, costs, identities, evidence, and regression
results.

- [x] `[should]` `CBS9-01` Freeze representative archetype, capability, and
  lifecycle cases from the existing E2E corpus before treatment-specific
  optimization.
- [x] `[should]` `CBS9-02` Define matched baseline and capability-inventory
  treatment with equivalent requirements, model/tool budgets, environment, and
  acceptance gates; consume the versioned raw telemetry accumulated since
  `CBS5` without rewriting historical observations.
- [x] `[should]` `CBS9-03` Keep held-out prompts, rubrics, target capability
  labels, and solution recipes unavailable to the authoring path.
- [x] `[should]` `CBS9-04` Measure accepted requirement coverage, independent
  reuse, composition complexity, residual implementation, semantic overlap,
  substitution cost, migrations, regressions, time, tokens, interventions, and
  nth-Application marginal cost.
- [x] `[should]` `CBS9-05` Exclude Application-local capabilities from reuse and
  report maturity distribution explicitly.
- [x] `[should]` `CBS9-06` Predefine the primary claim: marginal cost declines as
  verified reusable inventory grows without increasing semantic or E2E
  regression rate.
- [x] `[should]` `CBS9-07` Publish negative and null results, including contracts
  that increased resolution or composition cost.
- [ ] `[could]` `CBS9-08` Add ablations for evidence freshness, impact graph,
  multi-provider choice, and Evolver proposals.
- [ ] `[deferred]` `CBS9-09` Make ecosystem-wide or state-of-the-art claims from
  an unfrozen, non-independent, or recipe-leaking benchmark.

Validated-local checkpoint: two representative cases and one sealed held-out
case have matched legacy/CBS arms. The raw CBS5 observation is consumed without
rewriting but remains unmatched historical context. The predefined primary
claim is published as `not_supported`; see [CBS9 benchmark](cbs9-benchmark.md).

## Milestone CBS10: Shared Semantic Registry Publication

**Outcome:** portable CBS identities are discoverable across subnets through
the existing AdaOS registry without introducing another package manager or
copying local authority.

**Exit proof:** an exact Gmail provider Application is published from
Applications, a clean subnet imports its portable contract/binding/delivery
records, and another Gmail consumer resolves the shared delivery while creating
its own local account attachment and credential authority.

- [x] `[must]` `CBS10-01` Add fail-closed schemas for the shared semantic index
  and exact semantic Application release projection.
- [x] `[must]` `CBS10-02` Publish content-addressed `CapabilityContract`,
  `StateContract`, `BindingDefinition`, `BindingDelivery`, and explicitly
  portable `EvidenceClaim` records under the existing Git registry; deduplicate
  by stable identity, immutable revision, and digest.
- [x] `[must]` `CBS10-03` Bind publication to the exact admitted CBS compilation
  and ProjectRelease, and commit source plus semantic projection atomically.
- [x] `[must]` `CBS10-04` Reject credentials, local provider/account references,
  `BindingInstance`, `StateSpace`, operational assessments, and local evidence
  from shared publication.
- [x] `[must]` `CBS10-05` Materialize the compact semantic index in every sparse
  registry checkout and verify/import its portable records into the local
  catalog without installing the first publisher Application.
- [x] `[must]` `CBS10-06` Expose governed `Finalize` and `Publish` operations in
  Applications, including exact source-registry commit and semantic publication
  evidence.
- [~] `[must]` `CBS10-07` Prove publication and reuse with
  `gmail_cbs_cleanroom` and a distinct Gmail consumer on a clean subnet. Reuse
  the capability/provider credential attachment without copying a secret into
  either Application or the registry. Shared publication, clean-subnet import,
  exact provider delivery, consumer install, and subsequent auto-update are
  proven. The clean subnet still needs its own Gmail OAuth credential and local
  connected-account attachment before this item is complete; credentials are
  intentionally neither copied from the publisher nor published in Git.
- [ ] `[should]` `CBS10-08` Resolve a thin distribution online by semantic
  requirement and exact environment/policy/evidence constraints.
- [ ] `[should]` `CBS10-09` Export and admit a resolved portable distribution
  bundle with the exact immutable package and semantic closure for offline use.
- [ ] `[should]` `CBS10-10` Add registry query/explanation surfaces for eligible
  contracts, bindings, deliveries, publishers, and rejection reasons.
- [ ] `[could]` `CBS10-11` Add bounded registry-side search/ranking caches after
  correctness and cold-start costs are measured.
- [ ] `[deferred]` `CBS10-12` Define global cross-registry naming governance,
  federation conflict resolution, revocation propagation, and ecosystem-wide
  garbage collection.

## Cross-Cutting Acceptance Gates

### Contract gate

- every portable and local record has a versioned fail-closed schema;
- canonical serialization and digest round-trip are deterministic;
- stable refs and exact digests are never substituted for each other;
- unknown required semantics fail closed;
- permissions requested by a skill remain distinct from capabilities provided.

### State gate

- no persistent data identity uses the Web UI `state_ref` field;
- binding-instance and state-space refs are stable while material changes append
  immutable, predecessor-linked revisions;
- state ownership, lifecycle, custody, location, and write authority are
  independently observable;
- portability class is enforced during planning;
- stale writer epochs are rejected;
- destructive operations require explicit lifecycle authority.

### Resolution gate

- the resolver is read-only and deterministic for exact inputs;
- semantic candidates precede exact package resolution, and evidence/policy
  admission precedes creation of `ApplicationResolution`;
- every selection and rejection is explainable through source records;
- admitted resolutions are immutable;
- package resolution remains exact and compatible with current package locks;
- viability is derived for a named target and policy.

### Activation gate

- an exact immutable plan bridges resolution and activation;
- plan preconditions are rechecked at commit;
- pre-commit failure preserves old active authority;
- crash recovery distinguishes staged from committed work;
- at most one writer authority is valid for single-writer state;
- `WorkspaceLock` remains the sole active-workspace authority.

### Evidence gate

- claims identify exact subjects, environment, dependencies, time, and suite;
- claims are immutable;
- assessments are rebuildable and policy-specific;
- stale is not silently presented as incompatible or verified;
- portable evidence is secret-free and redacted.

### Architecture proof gate

- all five CRUD invariants pass in one exact evidence bundle;
- the semantic Application revision is byte-identical across substitutions;
- simulation and production use distinct state spaces while preserving one
  semantic Application revision;
- the production state record corpus and `state_space_ref` survive eligible
  production-to-production rebinding;
- package topology changes without capability or binding-definition identity
  change;
- phase fault injection and kill/restart recovery are included.
- benchmark-compatible telemetry and metric provenance exist from the first
  accepted CRUD run.

## Must / Should / Could / Deferred Summary

### Must

- vocabulary, compatibility inventory, schemas, stable refs, and digests;
- capability/state contracts, state ports, binding definitions/instances, and
  state spaces;
- immutable evidence claims and minimum assessment needed by resolution;
- pure semantic resolver and immutable Application resolution;
- immutable Resolution Plan with exact preconditions;
- additive integration with package resolution, activation journal, locks, and
  state migrations;
- fencing and atomic local authority switch;
- minimal CRUD-first implementations rather than generalized pre-proof
  frameworks;
- complete five-invariant CRUD proof, telemetry, and evidence bundle.
- shared semantic registry publication through the existing package/source
  registry, with secret-free import on a clean subnet.

### Should

- external dependency freshness monitor and policy-aware reassessment;
- one non-CRUD domain capability proof;
- multi-provider/sandbox repetitions;
- Builder gap and resolution reporting;
- operator inspectors and plan diffs;
- rebuildable Semantic/Impact Graph and read-only Evolver observations;
- matched evolutionary benchmark.
- thin online resolution and resolved offline distribution proof.

### Could

- alternative/Pareto resolution candidates;
- plan cache and richer visualization;
- broader legacy projections;
- revalidation prioritization;
- a graph database after measured need;
- a second domain proof and benchmark ablations.

### Deferred

- client plug-ins and arbitrary composite widget runtime;
- autonomous Evolver publication, production rewrites, or migrations;
- automatic migration of every legacy data store;
- arbitrary external distributed transactions and unattended destructive work;
- global multi-user semantic CRDT/merge authority;
- cross-registry federation and global naming governance;
- general-purpose global solver;
- package-manager rewrite or competing workspace lock;
- universal-domain or ecosystem-wide claims from bounded proofs.

## Evidence Bundle Layout

The CRUD and domain proof records should be content-addressed and include at
least:

```text
evidence/capability-binding-state/<run-id>/
  run.json
  environment-profile.json
  semantic-application.json
  capability-contracts/
  state-contracts/
  binding-definitions/
  package-locks/
  binding-instance-revisions.redacted.json
  state-space-revisions.redacted.json
  application-resolution.json
  resolution-plan.json
  workspace-lock.before.json
  workspace-lock.after.json
  activation-operation.json
  lock-history/
  evidence-claims/
  evidence-assessments/
  metrics.json
  state-record-digests.before.json
  state-record-digests.after.json
  traces/
  test-results/
  report.md
```

The exact repository location may reuse the established E2E or architecture
evidence conventions. Credentials, raw DSNs, and protected user records are
never copied into the bundle.

## Definition Of Program Success

The bounded architecture program is successful when:

1. the six identities are represented by versioned, tested contracts;
2. an unchanged semantic Application resolves to simulation and production
   while each may use a different state space;
3. a capability implementation moves across package topology without changing
   the requirement or capability identity;
4. eligible production state survives production-to-production rebinding with
   the same `state_space_ref` and records;
5. activation either commits the entire selected authority or preserves the
   previous active lock/generation, including crash recovery;
6. evidence can become stale due to external drift without mutating historical
   claims or falsely rewriting package truth;
7. one domain capability validates business invariants, effects, time, failure,
   compensation/reconciliation, and composition;
8. derived graphs can be deleted and rebuilt without losing identity or
   authority;
9. benchmark-compatible observations exist from the first CRUD run, so the
   later benchmark can test, rather than assume, declining marginal Application
   cost.

Production acceptance remains a separate maturity decision. Completing this
roadmap locally does not automatically authorize broad rollout, autonomous
migration, or public registry federation.

## Related Documents

- [Capability, Binding, and State Separation](capability-binding-state-separation.md)
- [Roadmap Inventory and Authority Map](roadmap-inventory.md)
- [Artifact Source, Package, and Activation Roadmap](artifact-source-package-activation-roadmap.md)
- [Application Lifecycle and Distribution Roadmap](application-lifecycle-and-distribution-roadmap.md)
- [Declarative Resource Workbench Roadmap](declarative-resource-workbench-roadmap.md)
- [Builder Intent-to-Prototype Roadmap](builder-intent-to-prototype-roadmap.md)
- [Semantic Application Composition And Evolution Roadmap](application-semantic-composition-roadmap.md)
- [Governed Evolution Roadmap](governed-evolution-roadmap.md)
- [Distributed Service and Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md)
