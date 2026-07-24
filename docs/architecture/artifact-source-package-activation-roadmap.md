# Artifact Source, Package, and Activation Roadmap

Status: implementation roadmap for
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).

Last reviewed: 2026-07-24.

## Outcome

AdaOS moves from path-copy publication and sparse Workspace installation to a
traceable pipeline:

```text
SourceRef
  -> immutable dependency-locked ProjectRelease
  -> candidate trial
  -> stable freshness gate
  -> channel promotion
  -> transactional Workspace activation
```

The active delivery goal is the single-user path. Collaborative extraction,
public feature proposals, editions, and multi-version runtime resolution are
recorded but explicitly deferred.

## Priority Vocabulary

- `[must]`: required for the single-user package pipeline acceptance proof.
- `[should]`: required before broad or unattended usage, but not required for
  the first bounded local proof.
- `[could]`: useful improvement that must not delay the proof gate.
- `[deferred]`: deliberately excluded from this refactoring and assigned to a
  later collaboration, distribution, or runtime milestone.

## Maturity Vocabulary

Tasks move through:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

The checklist reports delivery state, not only code presence. A task is not
`validated-local` without a reproducible test or operation record tied to an
exact revision.

## Guardrails

1. Do not edit installed Workspace content as development source.
2. Do not activate a package that is not identified by digest.
3. Do not resolve runtime dependencies through an implicit `latest` rule.
4. Do not partially activate an incompatible project release.
5. Do not move a stable channel before source, package, and validation records
   are durable.
6. Do not automatically repeat an unknown state-changing phase.
7. Do not report deferred collaboration or edition support as implemented.
8. Preserve registry v1 read compatibility until representative package-backed
   installs pass local and stand validation.

## Current Baseline

Current useful seams:

- Git skill and scenario repository adapters already isolate most sparse
  workspace access.
- Builder realization requests already carry `base_branch` and `sparse_paths`.
- Registry helpers already produce deterministic local catalog records.
- Durable operations already model accepted, running, failed, recoverable, and
  terminal work without blindly retrying unknown side effects.
- Scenario and skill publish flows already have DEV, validation, push, and
  Workspace projection concepts.

Validated-local implementation now provides:

- explicit source, package, release, candidate, subscription, and WorkspaceLock
  identities;
- deterministic, content-addressed scenario and skill packages;
- exact dependency locking for a scenario and companion skills;
- package-only trial, stable, rollback, and subscription activation;
- accepted-trial, exact-base freshness, public-source-tree, and stable-channel
  promotion gates;
- a bounded stale-candidate reapplication plan that requires new validation and
  trial;
- a durable Forge checkpoint intent/receipt journal that never automatically
  repeats an unknown state-changing write;
- content-addressed Builder task inputs with compare-and-switch DEV result
  activation, transactional backup, and rollback;
- explicit permission, migration, checkpoint, health-verify, and commit phases,
  with fail-closed permission admission and one-shot migration reconciliation.

The reproducible result and exact digests are recorded in
[Artifact Pipeline Local Evidence — 2026-07-24](artifact-pipeline-local-evidence-2026-07-24.md).

Remaining acceptance blockers:

- backend package and Forge-tree routes are committed locally but cannot be
  pushed or deployed until GitHub CLI authentication is available;
- a live stand/second-machine run is required before package-only activation is
  the default or legacy sparse Workspace compatibility is retired;
- delayed post-activation observation remains a `[should]` operational gate.

## Delivery Snapshot

This table records the highest maturity reached by the current implementation.
Detailed task checkboxes below remain open until their whole milestone exit gate
reaches the required environment; a local proof is not silently promoted to
stand or production acceptance.

| Milestone | Maturity | Validated task slices | Remaining broader gates |
| --- | --- | --- | --- |
| AP0 | validated-local | identities, schemas, canonical digests, SourceProvider, registry v2 compatibility | historical migration fixture breadth |
| AP1 | validated-local | deterministic package build/store/verify, authoring-state exclusion, corruption and zero-byte coverage | builder attestation identity and external signing |
| AP2 | validated-local | exact dependency ranges/digests, reverse consumers, conflict/cycle rejection | broader schema-component and migration-lock inputs |
| AP3 | validated-local | all 13 activation phases, atomic WorkspaceLock, permission admission, reversible migration/reconciliation, package projection, interruption recovery, runtime and health rollback | delayed health observation and stand validation |
| AP4 | validated-local | exact candidate identity, isolated package trial, data policy, acceptance evidence, immutable Builder task snapshot, concurrent-DEV compare-and-switch | stand validation before legacy task materialization retirement |
| AP5 | validated-local | freshness, stale/rebase plan, renewed trial, Forge tree gate, receipt recovery | deploy backend tree verification and exercise it live |
| AP6 | validated-local | stable subscription discovery, notify/pinned policy, package update, rollback, post-success observation | Builder/operator update-plan UI |
| AP7 | validated-local | representative LLM/Codex scenario+skill, 21 bounded resilience tests, and 161 focused regressions | live backend/stand run and cross-document synchronization |

## Milestone AP0: Contracts And Compatibility Boundary

**Outcome:** new identities and service boundaries exist without changing
current user-visible behavior.

**Exit proof:** legacy artifacts can be described by SourceRef, PackageRef,
ProjectRelease, and WorkspaceLock-compatible records while existing tests
remain green.

- [ ] `[must]` `AP0-01` Add versioned schemas and typed models for `ProjectRef`,
  `SourceRef`, `PackageRef`, `ProjectRelease`, `WorkspaceLock`, and stable
  `Subscription`.
- [ ] `[must]` `AP0-02` Define canonical digest serialization and reject one
  version identity mapping to different content.
- [ ] `[must]` `AP0-03` Make canonical manifest `id` the dependency identity;
  keep display names and registry aliases outside dependency locks.
- [ ] `[must]` `AP0-04` Add forge-independent `SourceProvider` and
  `RevisionSelector` ports.
- [ ] `[must]` `AP0-05` Add additive registry v2 fields for source, channels,
  release digest, and package references while preserving registry v1 reads.
- [ ] `[must]` `AP0-06` Add legacy adapters that synthesize the new identities
  for current path-backed artifacts.
- [ ] `[should]` `AP0-07` Add schema migration fixtures for historical registry
  entries and manifests with incomplete version metadata.
- [ ] `[could]` `AP0-08` Add a diagnostic command that explains resolved source,
  package, release, and activation identities.
- [ ] `[deferred]` `AP0-09` Add publisher namespaces and ownership-transfer
  records after the single-user identity contract is stable.

## Milestone AP1: Deterministic Immutable Packages

**Outcome:** scenario and skill content can be built once and addressed by
digest independently of Workspace and Git checkout layout.

**Admission gate:** AP0 contracts are implemented.

**Exit proof:** two builds from the same source produce the same digest, changed
content produces a different digest, and package extraction passes traversal,
symlink, size, and corruption tests.

- [ ] `[must]` `AP1-01` Implement `PackageBuilder` for scenario and skill
  component packages.
- [ ] `[must]` `AP1-02` Normalize package paths, timestamps, permissions, and
  manifest serialization for deterministic output.
- [ ] `[must]` `AP1-03` Implement a content-addressed local package store with
  atomic put, verify, get, and quarantine operations.
- [ ] `[must]` `AP1-04` Exclude DEV metadata, secrets, runtime data, caches, and
  unrelated sparse paths from package inputs.
- [ ] `[must]` `AP1-05` Validate archive path traversal, links, file count,
  decompressed size, and manifest digest before visibility.
- [ ] `[must]` `AP1-06` Persist source revision, builder identity, package
  manifest digest, and validation evidence references.
- [ ] `[should]` `AP1-07` Add signed attestations and external immutable release
  asset support behind a package-store adapter.
- [ ] `[could]` `AP1-08` Add package deduplication and bounded garbage collection
  diagnostics.
- [ ] `[deferred]` `AP1-09` Add commercial license and entitlement payloads to
  package admission policy.

## Milestone AP2: Dependency-Locked Project Releases

**Outcome:** activation uses one exact, compatible component set and never
selects a dependency implicitly.

**Admission gate:** AP1 component packages are reproducible.

**Exit proof:** a scenario and shared skill form a locked ProjectRelease;
compatible activation succeeds and an incompatible shared-skill update is
rejected without changing active state.

- [ ] `[must]` `AP2-01` Add declared dependency ranges to canonical scenario and
  skill manifest handling.
- [ ] `[must]` `AP2-02` Resolve declared dependencies into exact versions and
  package digests during ProjectRelease build.
- [ ] `[must]` `AP2-03` Store component, permission, schema, migration, and
  validation locks in ProjectRelease.
- [ ] `[must]` `AP2-04` Implement the MVP rule of one active package per canonical
  skill id in one node activation context.
- [ ] `[must]` `AP2-05` Compute reverse consumers before changing a shared skill
  binding.
- [ ] `[must]` `AP2-06` Reject missing, ambiguous, incompatible, or cyclic
  dependencies with an explainable plan and no partial mutation.
- [ ] `[must]` `AP2-07` Treat every dependency-lock change as a new release
  digest even when component source files are unchanged.
- [ ] `[should]` `AP2-08` Add lock explain and dependency graph diagnostics for
  Builder and operator UI.
- [ ] `[could]` `AP2-09` Cache compatible dependency plans by release digest.
- [ ] `[deferred]` `AP2-10` Add context-aware simultaneous active versions and a
  general-purpose dependency solver.

## Milestone AP3: Transactional Workspace Activation

**Outcome:** Workspace is materialized from packages through one durable lock
transition with health verification and rollback.

**Admission gate:** AP2 produces a complete ProjectRelease plan.

**Exit proof:** an empty Workspace installs from packages only, an update
switches the lock atomically, and injected failures before and after lock switch
leave either the old or the new complete release active.

- [ ] `[must]` `AP3-01` Implement durable WorkspaceLock storage with atomic
  revision and previous-lock linkage.
- [ ] `[must]` `AP3-02` Implement activation phases: resolve, fetch, verify,
  dependency plan, permission plan, migration plan, stage, checkpoint,
  switch-lock, reload, health verify, and commit.
- [ ] `[must]` `AP3-03` Make filesystem materialization and runtime bindings
  projections of WorkspaceLock.
- [ ] `[must]` `AP3-04` Add idempotency keys and durable phase evidence to the
  existing operation manager.
- [ ] `[must]` `AP3-05` Roll back lock, materialized content, and runtime binding
  after post-switch failure when the migration contract permits rollback.
- [ ] `[must]` `AP3-06` Never automatically replay an interrupted phase with an
  unknown side effect.
- [ ] `[must]` `AP3-07` Add transactional backup and restore coverage for current
  workspace update paths.
- [ ] `[should]` `AP3-08` Add delayed post-activation verification linked to the
  exact WorkspaceLock revision.
- [ ] `[should]` `AP3-09` Add orphan staging cleanup and package retention policy.
- [ ] `[could]` `AP3-10` Add operator diff output between active and proposed
  locks.
- [ ] `[deferred]` `AP3-11` Add unattended activation of irreversible migrations.

## Milestone AP4: Exact-Base DEV Candidate And Trial

**Outcome:** Builder develops from the installed stable identity, produces an
immutable candidate, and trials it without modifying primary stable activation.

**Admission gate:** AP3 can activate and roll back packages.

**Exit proof:** Builder creates a candidate from an exact stable release,
activates it in a trial slot, preserves primary stable activation, and records
acceptance or rollback evidence.

- [ ] `[must]` `AP4-01` Resolve installed stable release to exact SourceRef and
  ProjectRelease before DEV creation.
- [ ] `[must]` `AP4-02` Replace the shared mutable DEV branch context with a
  task/change-scoped worktree or isolated clone.
- [ ] `[must]` `AP4-03` Keep sparse paths as an optional task envelope inside the
  isolated DEV context.
- [ ] `[must]` `AP4-04` Build candidate version, source revision, base release,
  and package digest from the DEV result.
- [ ] `[must]` `AP4-05` Add a trial WorkspaceLock slot with explicit audience and
  data mode.
- [ ] `[must]` `AP4-06` Protect primary activation and real data from an
  incompatible candidate.
- [ ] `[must]` `AP4-07` Record deterministic validation, trial duration,
  acceptance, health, and rollback evidence against candidate digest.
- [ ] `[should]` `AP4-08` Add clear Builder and runtime labels for stable versus
  trial activation.
- [ ] `[could]` `AP4-09` Reuse unchanged low-risk evidence after a policy-proven
  no-op rebuild.
- [ ] `[deferred]` `AP4-10` Extract multiple-user WorkLogs into federated
  ChangeSets and public alpha proposals.

## Milestone AP5: Freshness Gate And Stable Promotion

**Outcome:** an accepted candidate becomes stable only when its exact base is
current; otherwise it returns through DEV migration and trial.

**Admission gate:** AP4 candidate trial is validated locally.

**Exit proof:** unchanged-base promotion advances stable to the accepted digest;
a moved-base candidate is rejected as stale, rebuilt on the new base, retrialed,
and then promoted.

- [ ] `[must]` `AP5-01` Compare candidate base version, source revision, and
  release digest with current stable.
- [ ] `[must]` `AP5-02` Mark moved-base candidates stale without changing source,
  package, or channel state.
- [ ] `[must]` `AP5-03` Recreate DEV on the new base and reapply the bounded
  project change inside its path and dependency envelope.
- [ ] `[must]` `AP5-04` Require a new digest, deterministic validation, and trial
  after any material rebase result.
- [ ] `[must]` `AP5-05` Persist public source identity and verify its tree before
  stable promotion.
- [ ] `[must]` `AP5-06` Persist source, package, release, and evidence before
  moving the stable channel pointer last.
- [ ] `[must]` `AP5-07` Make publication idempotent and recover partial completion
  without duplicate commits, releases, or registry entries.
- [ ] `[should]` `AP5-08` Add policy classes for documentation-only and
  deterministic metadata rebases.
- [ ] `[could]` `AP5-09` Add a merge-queue adapter after the single-writer flow is
  proven.
- [ ] `[deferred]` `AP5-10` Add zone-specific multi-user approvals and semantic
  merge arbitration.

## Milestone AP6: Stable Subscription And Package Update

**Outcome:** registry stable-version changes are discovered as subscriptions
and applied through package-backed transactional activation.

**Admission gate:** AP5 creates valid stable channel records.

**Exit proof:** an installed stable subscriber detects a new stable release,
shows the plan, updates from packages, verifies health, and rolls back after an
injected failure.

- [ ] `[must]` `AP6-01` Create a stable subscription record when a public project
  is installed.
- [ ] `[must]` `AP6-02` Support `notify` and `pinned` policies without enabling
  unattended stateful updates.
- [ ] `[must]` `AP6-03` Detect channel movement by release identity and digest,
  not version string alone.
- [ ] `[must]` `AP6-04` Route current scenario and skill update entrypoints
  through ProjectRelease planning and transactional activation.
- [ ] `[must]` `AP6-05` Preserve active release after fetch, dependency,
  permission, migration, reload, or health-check failure.
- [ ] `[must]` `AP6-06` Update subscription observation only after successful
  activation.
- [ ] `[must]` `AP6-07` Keep registry v1 source materialization as a bounded
  compatibility fallback during migration.
- [ ] `[should]` `AP6-08` Add explicit update-plan UI with dependencies,
  permissions, migrations, and rollback availability.
- [ ] `[could]` `AP6-09` Add `auto-compatible` for stateless, policy-approved
  releases after stand evidence.
- [ ] `[deferred]` `AP6-10` Add public candidate subscriptions and recommendation
  ranking.

## Milestone AP7: End-To-End Proof And Legacy Retirement Decision

**Outcome:** the representative single-user pipeline is reproducible on this
machine and produces durable proof for broader documentation updates.

**Admission gate:** AP0 through AP6 must items are integrated.

**Exit proof:** one representative scenario and shared skill pass the complete
source-to-trial-to-stable-to-subscriber update pipeline, including stale-base,
dependency-conflict, interruption, and rollback cases.

- [ ] `[must]` `AP7-01` Select and record the representative scenario, shared
  skill, initial stable release, and source revision.
- [ ] `[must]` `AP7-02` Install an empty Workspace from packages only.
- [ ] `[must]` `AP7-03` Create, validate, and trial a user-requested candidate
  through Builder and the built-in implementation model.
- [ ] `[must]` `AP7-04` Exercise unchanged-base stable promotion of the accepted
  digest.
- [ ] `[must]` `AP7-05` Exercise moved-base migration, rebuilt candidate, renewed
  trial, and promotion.
- [ ] `[must]` `AP7-06` Exercise compatible shared-skill update and incompatible
  dependency rejection.
- [ ] `[must]` `AP7-07` Exercise interruption at every activation phase and prove
  no unknown side effect is replayed.
- [ ] `[must]` `AP7-08` Exercise stable subscription discovery, update, health
  verification, and rollback.
- [ ] `[must]` `AP7-09` Record commands, commits, package digests, WorkspaceLock
  revisions, operation ids, validation results, and acceptance decision.
- [ ] `[must]` `AP7-10` Update Builder, registry, operations, runtime, and governed
  evolution documentation only from the recorded implementation evidence.
- [ ] `[should]` `AP7-11` Repeat the proof on a clean stand or second machine
  before defaulting new installs to package-only mode.
- [ ] `[could]` `AP7-12` Compare local package storage with an external immutable
  release backend.
- [ ] `[deferred]` `AP7-13` Claim production acceptance or marketplace readiness
  from the single-machine proof.

## Explicit Deferred Backlog

These items are architectural directions, not hidden requirements of the
active refactoring:

- [ ] `[deferred]` `APD-01` Multi-user WorkLog extraction and issue grouping.
- [ ] `[deferred]` `APD-02` Trusted development groups, delegated capability
  ownership, and proposal feeds.
- [ ] `[deferred]` `APD-03` Public alpha candidate discovery and cross-user trial
  evidence aggregation.
- [ ] `[deferred]` `APD-04` Multiple simultaneously active versions of one skill
  id.
- [ ] `[deferred]` `APD-05` Paid, free, organization, and personalized editions.
- [ ] `[deferred]` `APD-06` Commercial entitlement, licensing, and billing.
- [ ] `[deferred]` `APD-07` General dependency solving across independently
  released project graphs.
- [ ] `[deferred]` `APD-08` Automatic semantic merge and risk-weighted evidence
  transfer after rebase.
- [ ] `[deferred]` `APD-09` Repository-per-public-project migration; SourceRef
  must make it configuration-only when admitted.
- [ ] `[deferred]` `APD-10` Fully unattended stateful updates and irreversible
  migrations.

## Commit And Evidence Policy

- Commit each completed coherent milestone slice independently.
- A documentation-only architecture baseline is committed and tagged before
  implementation begins.
- Backend repositories may be published when a completed slice requires them.
- Client and main AdaOS publication occurs only after the local pipeline proof.
- Never include unrelated pre-existing worktree or submodule changes.
- Every roadmap checkbox changed to complete must link to a test, operation
  record, commit, or reproducible command.
- Failed experiments remain visible in evidence when they change an
  architectural decision.

## Documentation Synchronization Gate

Until AP7 evidence exists, this roadmap and its target architecture are the
only documents updated for the new pipeline. After the proof:

1. update Builder architecture and roadmap;
2. update registry/marketplace and operation roadmaps;
3. update runtime activation and dependency documentation;
4. update Governed Evolution milestone maturity;
5. update roadmap inventory and translated summaries;
6. record legacy sparse Workspace retirement or the remaining compatibility
   window.
